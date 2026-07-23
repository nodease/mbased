"""Bounded native-thread connection acquisition for RAG retrieval sessions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol

from gevent.monkey import get_original

from apps.workflow_engine.adapters.rag_retrieval_executor import (
    PROCESS_RAG_RETRIEVAL_MAX_WORKERS,
)
from apps.workflow_engine.application.rag_retrieval_fanout import (
    RAGRetrievalCancellation,
)


_allocate_native_lock = get_original("_thread", "allocate_lock")
_start_native_thread = get_original("_thread", "start_new_thread")
_NANOSECONDS_PER_SECOND = 1_000_000_000
_CANCELLATION_POLL_SECONDS = 0.01


class RAGRetrievalConnectionAcquisitionError(RuntimeError):
    """Redaction-safe connection acquisition failure."""

    code = "rag.retrieval_connection_acquisition_failed"

    def __init__(self) -> None:
        super().__init__("RAG retrieval connection acquisition failed.")


class RAGRetrievalConnectionAcquisitionTimeout(TimeoutError):
    """Redaction-safe connection acquisition timeout."""

    code = "rag.retrieval_timed_out"

    def __init__(self) -> None:
        super().__init__("RAG retrieval timed out.")


@dataclass(frozen=True, slots=True)
class AcquiredRAGRetrievalConnection:
    session: object = field(repr=False)
    connection: object = field(repr=False)


class RAGRetrievalConnectionAcquirer(Protocol):
    def acquire(
        self,
        *,
        session_factory: Callable[[], object],
        cancellation: RAGRetrievalCancellation,
        deadline_ns: int,
        monotonic_ns: Callable[[], int],
    ) -> AcquiredRAGRetrievalConnection: ...


class _ConnectionAcquisitionState:
    def __init__(self) -> None:
        self._lock = _allocate_native_lock()
        self._signal = _allocate_native_lock()
        self._signal.acquire()
        self._status = "pending"
        self._acquired: AcquiredRAGRetrievalConnection | None = None

    @property
    def status(self) -> str:
        with self._lock:
            return self._status

    def abandon(self) -> bool:
        with self._lock:
            if self._status != "pending":
                return False
            self._status = "abandoned"
            self._signal.release()
            return True

    def publish_success(
        self,
        acquired: AcquiredRAGRetrievalConnection,
    ) -> bool:
        with self._lock:
            if self._status != "pending":
                return False
            self._status = "succeeded"
            self._acquired = acquired
            self._signal.release()
            return True

    def publish_failure(self) -> bool:
        with self._lock:
            if self._status != "pending":
                return False
            self._status = "failed"
            self._signal.release()
            return True

    def wait(self, timeout_seconds: float) -> bool:
        return self._signal.acquire(timeout=max(0.0, timeout_seconds))

    def result(self) -> AcquiredRAGRetrievalConnection:
        with self._lock:
            if self._status == "succeeded" and self._acquired is not None:
                return self._acquired
            if self._status == "failed":
                raise RAGRetrievalConnectionAcquisitionError()
            raise RAGRetrievalConnectionAcquisitionTimeout()


class NativeThreadRAGRetrievalConnectionAcquirer:
    """Bound checkout stalls without changing the shared SQLAlchemy pool."""

    def __init__(self, *, max_workers: int) -> None:
        if (
            isinstance(max_workers, bool)
            or not isinstance(max_workers, int)
            or not 1 <= max_workers <= 20
        ):
            raise RAGRetrievalConnectionAcquisitionError()
        self._max_workers = max_workers
        self._lock = _allocate_native_lock()
        self._active_workers = 0

    def acquire(
        self,
        *,
        session_factory: Callable[[], object],
        cancellation: RAGRetrievalCancellation,
        deadline_ns: int,
        monotonic_ns: Callable[[], int],
    ) -> AcquiredRAGRetrievalConnection:
        if (
            not callable(session_factory)
            or not isinstance(cancellation, RAGRetrievalCancellation)
            or isinstance(deadline_ns, bool)
            or not isinstance(deadline_ns, int)
            or not callable(monotonic_ns)
        ):
            raise RAGRetrievalConnectionAcquisitionError()
        if cancellation.cancelled or deadline_ns <= monotonic_ns():
            raise RAGRetrievalConnectionAcquisitionTimeout()
        if not self._reserve_worker():
            raise RAGRetrievalConnectionAcquisitionTimeout()

        state = _ConnectionAcquisitionState()
        try:
            _start_native_thread(
                self._acquire_owned,
                (state, session_factory),
            )
        except BaseException:
            self._release_worker()
            raise RAGRetrievalConnectionAcquisitionError() from None

        while state.status == "pending":
            if cancellation.cancelled:
                if state.abandon():
                    raise RAGRetrievalConnectionAcquisitionTimeout()
                break
            remaining_ns = deadline_ns - monotonic_ns()
            if remaining_ns <= 0:
                if state.abandon():
                    raise RAGRetrievalConnectionAcquisitionTimeout()
                break
            state.wait(
                min(
                    remaining_ns / _NANOSECONDS_PER_SECOND,
                    _CANCELLATION_POLL_SECONDS,
                )
            )

        return state.result()

    def _reserve_worker(self) -> bool:
        with self._lock:
            if self._active_workers >= self._max_workers:
                return False
            self._active_workers += 1
            return True

    def _release_worker(self) -> None:
        with self._lock:
            self._active_workers -= 1

    def _acquire_owned(
        self,
        state: _ConnectionAcquisitionState,
        session_factory: Callable[[], object],
    ) -> None:
        session = None
        try:
            if state.status == "abandoned":
                return
            session = session_factory()
            if state.status == "abandoned":
                return
            connection = session.connection()
            if state.publish_success(
                AcquiredRAGRetrievalConnection(
                    session=session,
                    connection=connection,
                )
            ):
                session = None
        except BaseException:
            state.publish_failure()
        finally:
            try:
                if session is not None:
                    self._cleanup_session(session)
            finally:
                self._release_worker()

    @staticmethod
    def _cleanup_session(session: object) -> None:
        try:
            session.rollback()
        except BaseException:
            pass
        try:
            session.close()
        except BaseException:
            pass


_process_connection_acquirer = NativeThreadRAGRetrievalConnectionAcquirer(
    max_workers=PROCESS_RAG_RETRIEVAL_MAX_WORKERS
)


def get_process_rag_retrieval_connection_acquirer(
) -> NativeThreadRAGRetrievalConnectionAcquirer:
    return _process_connection_acquirer


__all__ = [
    "AcquiredRAGRetrievalConnection",
    "NativeThreadRAGRetrievalConnectionAcquirer",
    "RAGRetrievalConnectionAcquirer",
    "RAGRetrievalConnectionAcquisitionError",
    "RAGRetrievalConnectionAcquisitionTimeout",
    "get_process_rag_retrieval_connection_acquirer",
]
