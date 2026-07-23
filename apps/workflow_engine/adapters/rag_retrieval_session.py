"""SQLAlchemy session boundary for one blocking RAG retrieval task."""

from __future__ import annotations

import time
from typing import Callable, TypeVar

from sqlalchemy import event, text
from sqlalchemy.engine import Connection

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


class _StatementDeadlineGuard:
    _NANOSECONDS_PER_MILLISECOND = 1_000_000

    def __init__(
        self,
        *,
        cancellation: RAGRetrievalCancellation,
        deadline_ns: int,
        monotonic_ns: Callable[[], int],
    ) -> None:
        self._cancellation = cancellation
        self._deadline_ns = deadline_ns
        self._monotonic_ns = monotonic_ns

    def remaining_timeout_ms(self) -> int:
        if self._cancellation.cancelled:
            raise RAGRetrievalSessionTimeout()
        remaining_ns = self._deadline_ns - self._monotonic_ns()
        remaining_ms = remaining_ns // self._NANOSECONDS_PER_MILLISECOND
        if remaining_ms < 1:
            raise RAGRetrievalSessionTimeout()
        return remaining_ms

    def __call__(
        self,
        connection,
        cursor,
        _statement,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        remaining_timeout_ms = self.remaining_timeout_ms()
        dialect = getattr(getattr(connection, "dialect", None), "name", None)
        if dialect == "postgresql":
            cursor.execute(
                "SELECT set_config('statement_timeout', %s, true)",
                (f"{remaining_timeout_ms}ms",),
            )
            self.remaining_timeout_ms()


class RAGRetrievalSessionRunner:
    MAX_STATEMENT_TIMEOUT_MS = 30_000

    def __init__(
        self,
        *,
        session_factory: Callable[[], object],
        monotonic_ns: Callable[[], int] | None = None,
    ) -> None:
        if not callable(session_factory):
            raise RAGRetrievalSessionError()
        self._session_factory = session_factory
        self._monotonic_ns = monotonic_ns or time.monotonic_ns

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

        deadline_guard = _StatementDeadlineGuard(
            cancellation=cancellation,
            deadline_ns=(
                self._monotonic_ns()
                + timeout_ms * _StatementDeadlineGuard._NANOSECONDS_PER_MILLISECOND
            ),
            monotonic_ns=self._monotonic_ns,
        )
        try:
            session = self._session_factory()
        except Exception:
            raise RAGRetrievalSessionError() from None

        unregister: Callable[[], None] = _noop
        unregister_statement_guard: Callable[[], None] = _noop
        result: T | None = None
        failure: Exception | None = None
        cleanup_failed = False
        try:
            connection = session.connection()
            cancel = self._driver_cancel_callback(connection)
            if cancel is not None:
                unregister = cancellation.register(cancel)
            deadline_guard.remaining_timeout_ms()
            session.execute(text("SET TRANSACTION READ ONLY"))
            remaining_timeout_ms = deadline_guard.remaining_timeout_ms()
            session.execute(
                text(
                    "SELECT set_config('statement_timeout', :statement_timeout, true)"
                ),
                {"statement_timeout": f"{remaining_timeout_ms}ms"},
            )
            unregister_statement_guard = self._install_statement_guard(
                connection,
                deadline_guard,
            )
            deadline_guard.remaining_timeout_ms()
            result = operation(session)
            deadline_guard.remaining_timeout_ms()
        except Exception as exc:
            failure = self._safe_failure(exc, cancellation=cancellation)
        finally:
            try:
                unregister()
            except Exception:
                cleanup_failed = True
            try:
                unregister_statement_guard()
            except Exception:
                cleanup_failed = True
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
    def _install_statement_guard(
        connection,
        guard: _StatementDeadlineGuard,
    ) -> Callable[[], None]:
        if not isinstance(connection, Connection):
            return _noop
        event.listen(connection, "before_cursor_execute", guard)

        def unregister() -> None:
            event.remove(connection, "before_cursor_execute", guard)

        return unregister

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
