import threading
import time

import pytest

from apps.workflow_engine.adapters.rag_retrieval_connection_acquirer import (
    NativeThreadRAGRetrievalConnectionAcquirer,
    RAGRetrievalConnectionAcquisitionTimeout,
)
from apps.workflow_engine.adapters.rag_retrieval_executor import (
    NativeThreadRAGRetrievalCancellation,
)


class _Session:
    def __init__(self) -> None:
        self.connection_value = object()
        self.rollback_count = 0
        self.close_count = 0
        self.closed = threading.Event()

    def connection(self):
        return self.connection_value

    def rollback(self) -> None:
        self.rollback_count += 1

    def close(self) -> None:
        self.close_count += 1
        self.closed.set()


def test_acquirer_returns_session_and_connection_within_deadline() -> None:
    session = _Session()
    acquirer = NativeThreadRAGRetrievalConnectionAcquirer(max_workers=1)

    acquired = acquirer.acquire(
        session_factory=lambda: session,
        cancellation=NativeThreadRAGRetrievalCancellation(),
        deadline_ns=time.monotonic_ns() + 1_000_000_000,
        monotonic_ns=time.monotonic_ns,
    )

    assert acquired.session is session
    assert acquired.connection is session.connection_value
    assert session.rollback_count == 0
    assert session.close_count == 0


def test_acquirer_returns_at_deadline_and_late_session_is_cleaned_by_owner() -> None:
    factory_started = threading.Event()
    release_factory = threading.Event()
    deadline_expired = threading.Event()
    session = _Session()
    acquirer = NativeThreadRAGRetrievalConnectionAcquirer(max_workers=1)

    def delayed_factory():
        factory_started.set()
        release_factory.wait(timeout=1)
        return session

    def expire_deadline():
        assert factory_started.wait(timeout=1)
        deadline_expired.set()

    expiry = threading.Thread(target=expire_deadline)
    expiry.start()
    with pytest.raises(RAGRetrievalConnectionAcquisitionTimeout):
        acquirer.acquire(
            session_factory=delayed_factory,
            cancellation=NativeThreadRAGRetrievalCancellation(),
            deadline_ns=1_000_000_000,
            monotonic_ns=lambda: (
                2_000_000_000 if deadline_expired.is_set() else 0
            ),
        )
    expiry.join(timeout=1)

    assert factory_started.is_set()
    assert session.close_count == 0

    release_factory.set()
    assert session.closed.wait(timeout=1)
    assert session.rollback_count == 1
    assert session.close_count == 1


def test_acquirer_rejects_new_checkout_when_process_slots_are_occupied() -> None:
    factory_started = threading.Event()
    release_factory = threading.Event()
    deadline_expired = threading.Event()
    first_session = _Session()
    second_factory_calls = 0
    acquirer = NativeThreadRAGRetrievalConnectionAcquirer(max_workers=1)

    def delayed_factory():
        factory_started.set()
        release_factory.wait(timeout=1)
        return first_session

    def expire_deadline():
        assert factory_started.wait(timeout=1)
        deadline_expired.set()

    expiry = threading.Thread(target=expire_deadline)
    expiry.start()
    with pytest.raises(RAGRetrievalConnectionAcquisitionTimeout):
        acquirer.acquire(
            session_factory=delayed_factory,
            cancellation=NativeThreadRAGRetrievalCancellation(),
            deadline_ns=1_000_000_000,
            monotonic_ns=lambda: (
                2_000_000_000 if deadline_expired.is_set() else 0
            ),
        )
    expiry.join(timeout=1)

    assert factory_started.is_set()

    def second_factory():
        nonlocal second_factory_calls
        second_factory_calls += 1
        return _Session()

    with pytest.raises(RAGRetrievalConnectionAcquisitionTimeout):
        acquirer.acquire(
            session_factory=second_factory,
            cancellation=NativeThreadRAGRetrievalCancellation(),
            deadline_ns=time.monotonic_ns() + 1_000_000_000,
            monotonic_ns=time.monotonic_ns,
        )

    assert second_factory_calls == 0

    release_factory.set()
    assert first_session.closed.wait(timeout=1)


def test_acquirer_rejects_cancelled_request_before_factory_call() -> None:
    factory_calls = 0
    cancellation = NativeThreadRAGRetrievalCancellation()
    cancellation.cancel()
    acquirer = NativeThreadRAGRetrievalConnectionAcquirer(max_workers=1)

    def session_factory():
        nonlocal factory_calls
        factory_calls += 1
        return _Session()

    with pytest.raises(RAGRetrievalConnectionAcquisitionTimeout):
        acquirer.acquire(
            session_factory=session_factory,
            cancellation=cancellation,
            deadline_ns=time.monotonic_ns() + 1_000_000_000,
            monotonic_ns=time.monotonic_ns,
        )

    assert factory_calls == 0


def test_acquirer_cancellation_releases_caller_and_late_session_is_cleaned() -> None:
    factory_started = threading.Event()
    release_factory = threading.Event()
    acquisition_finished = threading.Event()
    session = _Session()
    cancellation = NativeThreadRAGRetrievalCancellation()
    acquirer = NativeThreadRAGRetrievalConnectionAcquirer(max_workers=1)
    failures = []

    def delayed_factory():
        factory_started.set()
        release_factory.wait(timeout=1)
        return session

    def acquire():
        try:
            acquirer.acquire(
                session_factory=delayed_factory,
                cancellation=cancellation,
                deadline_ns=time.monotonic_ns() + 1_000_000_000,
                monotonic_ns=time.monotonic_ns,
            )
        except Exception as exc:  # pragma: no cover - asserted below
            failures.append(exc)
        finally:
            acquisition_finished.set()

    caller = threading.Thread(target=acquire)
    caller.start()
    assert factory_started.wait(timeout=1)

    cancellation.cancel()

    assert acquisition_finished.wait(timeout=0.5)
    caller.join(timeout=0.5)
    assert caller.is_alive() is False
    assert len(failures) == 1
    assert isinstance(failures[0], RAGRetrievalConnectionAcquisitionTimeout)
    assert session.close_count == 0

    release_factory.set()
    assert session.closed.wait(timeout=1)
    assert session.rollback_count == 1
    assert session.close_count == 1
