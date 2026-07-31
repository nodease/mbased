from __future__ import annotations

import logging
from datetime import datetime
from time import monotonic
from uuid import UUID

from sqlalchemy import and_, event, func, or_, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from apps.shared.db.models.connection import Connection
from apps.shared.db.models.knowledge import Document

logger = logging.getLogger(__name__)

CONNECTION_REFERENCE_LOCK_TIMEOUT_MS = 2_000
_CONNECTION_REFERENCE_HOLD_STARTED_AT = (
    "nodease.connection_reference_lock_hold_started_at"
)
_TRANSIENT_CONTENTION_SQLSTATES = frozenset(
    {
        "40001",  # serialization_failure
        "40P01",  # deadlock_detected
        "55P03",  # lock_not_available / lock timeout
        "57014",  # query_canceled while waiting for a lock
    }
)


class ConnectionLifecycleError(Exception):
    """Base error for owner-scoped connection lifecycle mutations."""


class ConnectionLifecycleHidden(ConnectionLifecycleError):
    pass


class ConnectionLifecycleInUse(ConnectionLifecycleError):
    pass


class ConnectionLifecycleConflict(ConnectionLifecycleError):
    code = "connection.reference_conflict"
    retryable = False

    def __init__(self) -> None:
        super().__init__("The Connection reference changed concurrently.")


class ConnectionLifecycleBusy(ConnectionLifecycleError):
    code = "connection.reference_busy"
    retryable = True

    def __init__(self) -> None:
        super().__init__("Connection reference is temporarily busy.")


class ConnectionLifecycleUnavailable(ConnectionLifecycleError):
    code = "connection.reference_unavailable"
    retryable = True

    def __init__(self) -> None:
        super().__init__("Connection lifecycle is temporarily unavailable.")


class ConnectionLifecycleService:
    """Serialize owner connection references with reference-aware deletion."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def lock_owned_connection_for_reference(
        self,
        *,
        connection_id: UUID,
        owner_id: UUID,
    ) -> Connection:
        """Serialize a new document reference with owner-scoped deletion."""

        started_at = monotonic()
        try:
            self._set_bounded_lock_timeout()
            connection = (
                self.db.query(Connection)
                .filter(
                    Connection.id == connection_id,
                    Connection.user_id == owner_id,
                )
                .with_for_update()
                .first()
            )
        except SQLAlchemyError as exc:
            self.db.rollback()
            error = self._translate_store_error(exc)
            self._record_lock_outcome(
                "busy" if isinstance(error, ConnectionLifecycleBusy) else "unavailable",
                started_at,
            )
            raise error from None
        except BaseException:
            self.db.rollback()
            self._record_lock_outcome("cancelled", started_at)
            raise
        if connection is None:
            self.db.rollback()
            self._record_lock_outcome("hidden", started_at)
            raise ConnectionLifecycleHidden()
        self._record_lock_outcome("acquired", started_at)
        self._register_lock_hold_observer(started_at)
        return connection

    def lock_owned_connection_and_document_for_reference(
        self,
        *,
        connection_id: UUID,
        owner_id: UUID,
        document_id: UUID,
        expected_document_updated_at: datetime | None,
    ) -> Document:
        """Lock a Connection before its Document and reject a stale writer."""

        self.lock_owned_connection_for_reference(
            connection_id=connection_id,
            owner_id=owner_id,
        )
        try:
            document = (
                self.db.query(Document)
                .filter(Document.id == document_id)
                .populate_existing()
                .with_for_update()
                .first()
            )
        except SQLAlchemyError as exc:
            self.db.rollback()
            raise self._translate_store_error(exc) from None
        except BaseException:
            self.db.rollback()
            raise

        if document is None:
            self.db.rollback()
            raise ConnectionLifecycleHidden()
        if document.updated_at != expected_document_updated_at:
            self.db.rollback()
            raise ConnectionLifecycleConflict()
        return document

    def commit_reference_mutation(self) -> None:
        """Commit a locked reference mutation and map safe retry semantics."""

        try:
            self.db.commit()
        except SQLAlchemyError as exc:
            self.db.rollback()
            raise self._translate_store_error(exc) from None
        except BaseException:
            self.db.rollback()
            raise

    def flush(self) -> None:
        """Flush a reference mutation with the same safe error contract."""

        try:
            self.db.flush()
        except SQLAlchemyError as exc:
            self.db.rollback()
            raise self._translate_store_error(exc) from None
        except BaseException:
            self.db.rollback()
            raise

    def commit(self) -> None:
        self.commit_reference_mutation()

    def rollback(self) -> None:
        self.db.rollback()

    def delete_unreferenced_connection(
        self,
        *,
        connection_id: UUID,
        owner_id: UUID,
    ) -> None:
        try:
            connection = self.lock_owned_connection_for_reference(
                connection_id=connection_id,
                owner_id=owner_id,
            )

            referenced_document = (
                self.db.query(Document.id)
                .filter(
                    or_(
                        Document.meta_info.contains(
                            {"connection_id": str(connection_id)}
                        ),
                        Document.meta_info.contains(
                            {
                                "db_config": {
                                    "connection_id": str(connection_id)
                                }
                            }
                        ),
                        and_(
                            func.jsonb_typeof(Document.meta_info["db_config"])
                            == "string",
                            Document.meta_info["db_config"].astext.contains(
                                str(connection_id)
                            ),
                        ),
                    )
                )
                .first()
            )
            if referenced_document is not None:
                raise ConnectionLifecycleInUse()

            self.db.delete(connection)
            self.db.commit()
        except ConnectionLifecycleError:
            self.db.rollback()
            raise
        except SQLAlchemyError as exc:
            self.db.rollback()
            raise self._translate_store_error(exc) from None
        except BaseException:
            self.db.rollback()
            raise

    def _set_bounded_lock_timeout(self) -> None:
        try:
            bind = self.db.get_bind()
        except (AttributeError, TypeError):
            return
        if getattr(getattr(bind, "dialect", None), "name", None) != "postgresql":
            return
        self.db.execute(
            text(
                "SET LOCAL lock_timeout = "
                f"'{CONNECTION_REFERENCE_LOCK_TIMEOUT_MS}ms'"
            )
        )

    @staticmethod
    def _translate_store_error(exc: SQLAlchemyError) -> ConnectionLifecycleError:
        original = getattr(exc, "orig", None)
        sqlstate = getattr(original, "sqlstate", None) or getattr(
            original, "pgcode", None
        )
        if sqlstate in _TRANSIENT_CONTENTION_SQLSTATES:
            return ConnectionLifecycleBusy()
        return ConnectionLifecycleUnavailable()

    @staticmethod
    def _record_lock_outcome(outcome: str, started_at: float) -> None:
        elapsed_ms = max(0.0, (monotonic() - started_at) * 1_000)
        if elapsed_ms < 50:
            wait_bucket = "lt_50ms"
        elif elapsed_ms < 250:
            wait_bucket = "lt_250ms"
        elif elapsed_ms < 1_000:
            wait_bucket = "lt_1s"
        else:
            wait_bucket = "gte_1s"
        logger.info(
            "Connection reference lock outcome=%s wait_bucket=%s",
            outcome,
            wait_bucket,
        )

    def _register_lock_hold_observer(self, acquired_at: float) -> None:
        if not isinstance(self.db, Session):
            return
        self.db.info.setdefault(_CONNECTION_REFERENCE_HOLD_STARTED_AT, acquired_at)

    @staticmethod
    def _record_lock_hold(acquired_at: float) -> None:
        elapsed_ms = max(0.0, (monotonic() - acquired_at) * 1_000)
        if elapsed_ms < 100:
            hold_bucket = "lt_100ms"
        elif elapsed_ms < 500:
            hold_bucket = "lt_500ms"
        elif elapsed_ms < 2_000:
            hold_bucket = "lt_2s"
        else:
            hold_bucket = "gte_2s"
        logger.info("Connection reference lock hold_bucket=%s", hold_bucket)


@event.listens_for(Session, "after_transaction_end")
def _record_connection_reference_lock_hold(session, transaction) -> None:
    if getattr(transaction, "parent", None) is not None:
        return
    acquired_at = session.info.pop(_CONNECTION_REFERENCE_HOLD_STARTED_AT, None)
    if acquired_at is not None:
        ConnectionLifecycleService._record_lock_hold(acquired_at)
