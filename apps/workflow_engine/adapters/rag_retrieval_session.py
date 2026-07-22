"""SQLAlchemy session boundary for one blocking RAG retrieval task."""

from __future__ import annotations

from typing import Callable, TypeVar

from sqlalchemy import text

from apps.workflow_engine.application.rag_retrieval_fanout import (
    RAGRetrievalCancellation,
)


T = TypeVar("T")


def _noop() -> None:
    return None


class RAGRetrievalSessionError(RuntimeError):
    """Redaction-safe retrieval session failure."""

    code = "rag.retrieval_session_failed"

    def __init__(self) -> None:
        super().__init__("RAG retrieval session failed.")


class RAGRetrievalSessionTimeout(TimeoutError):
    """Redaction-safe timeout raised after PostgreSQL query cancellation."""

    code = "rag.retrieval_timed_out"

    def __init__(self) -> None:
        super().__init__("RAG retrieval timed out.")


class RAGRetrievalSessionRunner:
    MAX_STATEMENT_TIMEOUT_MS = 30_000

    def __init__(self, *, session_factory: Callable[[], object]) -> None:
        if not callable(session_factory):
            raise RAGRetrievalSessionError()
        self._session_factory = session_factory

    def run(
        self,
        *,
        timeout_ms: int,
        cancellation: RAGRetrievalCancellation,
        operation: Callable[[object], T],
    ) -> T:
        if (
            isinstance(timeout_ms, bool)
            or not isinstance(timeout_ms, int)
            or not 1 <= timeout_ms <= self.MAX_STATEMENT_TIMEOUT_MS
            or not isinstance(cancellation, RAGRetrievalCancellation)
            or not callable(operation)
        ):
            raise RAGRetrievalSessionError()

        try:
            session = self._session_factory()
        except Exception:
            raise RAGRetrievalSessionError() from None

        unregister: Callable[[], None] = _noop
        result: T | None = None
        failure: Exception | None = None
        cleanup_failed = False
        try:
            connection = session.connection()
            cancel = self._driver_cancel_callback(connection)
            if cancel is not None:
                unregister = cancellation.register(cancel)
            if cancellation.cancelled:
                raise RAGRetrievalSessionTimeout()
            session.execute(text("SET TRANSACTION READ ONLY"))
            session.execute(
                text(
                    "SELECT set_config('statement_timeout', :statement_timeout, true)"
                ),
                {"statement_timeout": f"{timeout_ms}ms"},
            )
            if cancellation.cancelled:
                raise RAGRetrievalSessionTimeout()
            result = operation(session)
            if cancellation.cancelled:
                raise RAGRetrievalSessionTimeout()
        except Exception as exc:
            failure = self._safe_failure(exc, cancellation=cancellation)
        finally:
            unregister()
            try:
                session.rollback()
            except Exception:
                cleanup_failed = True
            try:
                session.close()
            except Exception:
                cleanup_failed = True

        if failure is not None:
            raise failure from None
        if cleanup_failed:
            raise RAGRetrievalSessionError() from None
        return result  # type: ignore[return-value]

    @staticmethod
    def _driver_cancel_callback(connection) -> Callable[[], None] | None:
        proxy = getattr(connection, "connection", None)
        driver = getattr(proxy, "driver_connection", None)
        if driver is None:
            driver = getattr(proxy, "connection", None)
        cancel = getattr(driver, "cancel", None)
        return cancel if callable(cancel) else None

    @classmethod
    def _safe_failure(
        cls,
        exc: Exception,
        *,
        cancellation: RAGRetrievalCancellation,
    ) -> Exception:
        if (
            isinstance(exc, (RAGRetrievalSessionTimeout, TimeoutError))
            or cancellation.cancelled
            or cls._is_postgres_query_cancel(exc)
        ):
            return RAGRetrievalSessionTimeout()
        return RAGRetrievalSessionError()

    @staticmethod
    def _is_postgres_query_cancel(exc: Exception) -> bool:
        current: object = exc
        for _index in range(3):
            sqlstate = getattr(current, "sqlstate", None) or getattr(
                current, "pgcode", None
            )
            if sqlstate == "57014":
                return True
            nested = getattr(current, "orig", None)
            if nested is None or nested is current:
                break
            current = nested
        return False


__all__ = [
    "RAGRetrievalSessionError",
    "RAGRetrievalSessionRunner",
    "RAGRetrievalSessionTimeout",
]
