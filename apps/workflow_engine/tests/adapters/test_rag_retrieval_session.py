import threading
from types import SimpleNamespace

import gevent
import pytest
from sqlalchemy import create_engine, text

from apps.workflow_engine.adapters.rag_retrieval_session import (
    RAGRetrievalSessionError,
    RAGRetrievalSessionRunner,
    RAGRetrievalSessionTimeout,
    _StatementDeadlineGuard,
)
from apps.workflow_engine.adapters.rag_retrieval_executor import (
    NativeThreadRAGRetrievalCancellation,
)


class _DriverConnection:
    def __init__(self) -> None:
        self.cancel_count = 0
        self.cancelled = threading.Event()

    def cancel(self) -> None:
        self.cancel_count += 1
        self.cancelled.set()


class _FakeSession:
    def __init__(self, *, rollback_error: bool = False, close_error: bool = False):
        self.events: list[object] = []
        self.driver = _DriverConnection()
        self._connection = SimpleNamespace(
            connection=SimpleNamespace(driver_connection=self.driver)
        )
        self.rollback_error = rollback_error
        self.close_error = close_error

    def connection(self):
        self.events.append("connection")
        return self._connection

    def execute(self, statement, parameters=None):
        self.events.append((str(statement), parameters))
        return SimpleNamespace()

    def rollback(self):
        self.events.append("rollback")
        if self.rollback_error:
            raise RuntimeError("private-rollback-detail")

    def close(self):
        self.events.append("close")
        if self.close_error:
            raise RuntimeError("private-close-detail")


class _SQLAlchemyBackedSession:
    def __init__(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        self.sql_connection = self.engine.connect()
        self.events: list[object] = []

    def connection(self):
        self.events.append("connection")
        return self.sql_connection

    def execute(self, statement, parameters=None):
        rendered = str(statement)
        self.events.append((rendered, parameters))
        if rendered.startswith("SET TRANSACTION") or "set_config" in rendered:
            return SimpleNamespace()
        return self.sql_connection.execute(statement, parameters or {})

    def rollback(self):
        self.events.append("rollback")
        self.sql_connection.rollback()

    def close(self):
        self.events.append("close")

    def dispose(self) -> None:
        self.sql_connection.close()
        self.engine.dispose()


def test_session_runner_applies_read_only_timeout_and_always_rolls_back() -> None:
    session = _FakeSession()
    cancellation = NativeThreadRAGRetrievalCancellation()

    result = RAGRetrievalSessionRunner(
        session_factory=lambda: session,
        monotonic_ns=lambda: 0,
    ).run(
        timeout_ms=125,
        cancellation=cancellation,
        operation=lambda current: current is session,
    )

    assert result is True
    assert session.events[0] == "connection"
    assert "SET TRANSACTION READ ONLY" in session.events[1][0]
    assert "set_config" in session.events[2][0]
    assert session.events[2][1] == {"statement_timeout": "125ms"}
    assert session.events[-2:] == ["rollback", "close"]
    cancellation.cancel()
    assert session.driver.cancel_count == 0


def test_session_runner_opens_and_closes_one_session_per_invocation() -> None:
    sessions = [_FakeSession(), _FakeSession()]
    opened = []

    def session_factory():
        session = sessions[len(opened)]
        opened.append(session)
        return session

    runner = RAGRetrievalSessionRunner(session_factory=session_factory)
    observed = [
        runner.run(
            timeout_ms=125,
            cancellation=NativeThreadRAGRetrievalCancellation(),
            operation=id,
        )
        for _index in range(2)
    ]

    assert observed == [id(session) for session in sessions]
    assert opened == sessions
    assert all(session.events[-2:] == ["rollback", "close"] for session in sessions)


def test_session_runner_cancellation_calls_driver_and_returns_safe_timeout() -> None:
    session = _FakeSession()
    cancellation = NativeThreadRAGRetrievalCancellation()
    operation_started = threading.Event()
    failures = []

    def operation(_session):
        operation_started.set()
        session.driver.cancelled.wait(timeout=1)
        return "late-result"

    def run():
        try:
            RAGRetrievalSessionRunner(session_factory=lambda: session).run(
                timeout_ms=125,
                cancellation=cancellation,
                operation=operation,
            )
        except Exception as exc:  # pragma: no cover - asserted below
            failures.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    assert operation_started.wait(timeout=1)

    cancellation.cancel()
    gevent.sleep(0)
    worker.join(timeout=1)

    assert worker.is_alive() is False
    assert session.driver.cancel_count == 1
    assert session.events[-2:] == ["rollback", "close"]
    assert len(failures) == 1
    assert isinstance(failures[0], RAGRetrievalSessionTimeout)
    assert "late-result" not in str(failures[0])


def test_session_runner_redacts_operation_and_cleanup_failures() -> None:
    session = _FakeSession(rollback_error=True, close_error=True)

    with pytest.raises(RAGRetrievalSessionError) as captured:
        RAGRetrievalSessionRunner(session_factory=lambda: session).run(
            timeout_ms=125,
            cancellation=NativeThreadRAGRetrievalCancellation(),
            operation=lambda _session: (_ for _ in ()).throw(
                RuntimeError("private-query-detail")
            ),
        )

    assert session.events[-2:] == ["rollback", "close"]
    assert "private-query-detail" not in str(captured.value)
    assert "private-rollback-detail" not in str(captured.value)
    assert "private-close-detail" not in str(captured.value)


def test_session_runner_blocks_next_statement_after_cancellation() -> None:
    session = _SQLAlchemyBackedSession()
    cancellation = NativeThreadRAGRetrievalCancellation()
    second_statement_reached = False

    def operation(current):
        nonlocal second_statement_reached
        assert current.execute(text("SELECT 1")).scalar_one() == 1
        cancellation.cancel()
        current.execute(text("SELECT 2"))
        second_statement_reached = True

    try:
        with pytest.raises(RAGRetrievalSessionTimeout):
            RAGRetrievalSessionRunner(session_factory=lambda: session).run(
                timeout_ms=125,
                cancellation=cancellation,
                operation=operation,
            )

        assert second_statement_reached is False
        assert session.events[-2:] == ["rollback", "close"]
    finally:
        session.dispose()


def test_session_runner_blocks_next_statement_after_absolute_deadline() -> None:
    session = _SQLAlchemyBackedSession()
    now_ns = 0

    def monotonic_ns() -> int:
        return now_ns

    def operation(current):
        nonlocal now_ns
        assert current.execute(text("SELECT 1")).scalar_one() == 1
        now_ns = 126_000_000
        current.execute(text("SELECT 2"))

    try:
        with pytest.raises(RAGRetrievalSessionTimeout):
            RAGRetrievalSessionRunner(
                session_factory=lambda: session,
                monotonic_ns=monotonic_ns,
            ).run(
                timeout_ms=125,
                cancellation=NativeThreadRAGRetrievalCancellation(),
                operation=operation,
            )

        assert session.events[-2:] == ["rollback", "close"]
    finally:
        session.dispose()


def test_postgres_statement_guard_uses_remaining_absolute_budget() -> None:
    now_ns = 40_000_000
    cancellation = NativeThreadRAGRetrievalCancellation()
    cursor_calls = []
    cursor = SimpleNamespace(
        execute=lambda statement, parameters: cursor_calls.append(
            (statement, parameters)
        )
    )
    connection = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    guard = _StatementDeadlineGuard(
        cancellation=cancellation,
        deadline_ns=125_000_000,
        monotonic_ns=lambda: now_ns,
    )

    guard(connection, cursor, "SELECT 1", (), None, False)

    assert cursor_calls == [
        (
            "SELECT set_config('statement_timeout', %s, true)",
            ("85ms",),
        )
    ]

    now_ns = 126_000_000
    with pytest.raises(RAGRetrievalSessionTimeout):
        guard(connection, cursor, "SELECT 2", (), None, False)

    assert len(cursor_calls) == 1


def test_session_runner_removes_statement_guard_before_connection_reuse() -> None:
    session = _SQLAlchemyBackedSession()
    cancellation = NativeThreadRAGRetrievalCancellation()

    try:
        result = RAGRetrievalSessionRunner(session_factory=lambda: session).run(
            timeout_ms=125,
            cancellation=cancellation,
            operation=lambda current: current.execute(text("SELECT 1")).scalar_one(),
        )
        cancellation.cancel()

        assert result == 1
        assert session.sql_connection.execute(text("SELECT 2")).scalar_one() == 2
    finally:
        session.dispose()


@pytest.mark.parametrize("timeout_ms", [0, -1, True, 30001])
def test_session_runner_rejects_invalid_timeout_before_opening_session(
    timeout_ms,
) -> None:
    opened = []

    with pytest.raises(RAGRetrievalSessionError):
        RAGRetrievalSessionRunner(session_factory=lambda: opened.append(True)).run(
            timeout_ms=timeout_ms,
            cancellation=NativeThreadRAGRetrievalCancellation(),
            operation=lambda _session: None,
        )

    assert opened == []
