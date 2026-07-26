"""Native-thread executor adapter for a gevent-monkey-patched Workflow worker."""

from __future__ import annotations

from typing import Callable, Generic, TypeVar

import gevent
from gevent.monkey import get_original
from gevent.threadpool import ThreadPool

from apps.workflow_engine.application.rag_retrieval_fanout import (
    DEFAULT_RAG_FANOUT_MAX_WORKERS,
    RAGRetrievalFanoutConfigurationError,
    RAGRetrievalFanoutError,
    RAGRetrievalJob,
)


T = TypeVar("T")
_allocate_native_lock = get_original("_thread", "allocate_lock")
_native_get_ident = get_original("_thread", "get_ident")

PROCESS_RAG_RETRIEVAL_MAX_WORKERS = DEFAULT_RAG_FANOUT_MAX_WORKERS
PROCESS_RAG_RETRIEVAL_CONTROL_MAX_WORKERS = 2
_IDLE_TASK_TIMEOUT_SECONDS = 0.1

_process_pool_lock = _allocate_native_lock()
_process_data_admission_lock = _allocate_native_lock()
_process_data_pool: ThreadPool | None = None
_process_control_pool: ThreadPool | None = None
_process_admitted_data_jobs = 0


def _reserve_process_data_job() -> bool:
    global _process_admitted_data_jobs
    with _process_data_admission_lock:
        if _process_admitted_data_jobs >= PROCESS_RAG_RETRIEVAL_MAX_WORKERS:
            return False
        _process_admitted_data_jobs += 1
        return True


def _release_process_data_job() -> None:
    global _process_admitted_data_jobs
    with _process_data_admission_lock:
        _process_admitted_data_jobs -= 1


def _get_process_data_pool() -> ThreadPool:
    global _process_data_pool
    with _process_pool_lock:
        if _process_data_pool is None:
            _process_data_pool = ThreadPool(
                PROCESS_RAG_RETRIEVAL_MAX_WORKERS,
                idle_task_timeout=_IDLE_TASK_TIMEOUT_SECONDS,
            )
        return _process_data_pool


def _get_process_control_pool() -> ThreadPool:
    global _process_control_pool
    with _process_pool_lock:
        if _process_control_pool is None:
            _process_control_pool = ThreadPool(
                PROCESS_RAG_RETRIEVAL_CONTROL_MAX_WORKERS,
                idle_task_timeout=_IDLE_TASK_TIMEOUT_SECONDS,
            )
        return _process_control_pool


class _RegisteredCancellationCallback:
    """Keep an asynchronously dispatched callback inside its resource lifetime."""

    def __init__(self, callback: Callable[[], None]) -> None:
        self._lock = _allocate_native_lock()
        self._completion = _allocate_native_lock()
        self._completion.acquire()
        self._callback: Callable[[], None] | None = callback
        self._active = True
        self._running = False

    def invoke(self) -> None:
        with self._lock:
            callback = self._callback
            if not self._active or callback is None:
                return
            self._active = False
            self._running = True
            self._callback = None
        try:
            callback()
        finally:
            with self._lock:
                self._running = False
            self._completion.release()

    def deactivate(self) -> None:
        with self._lock:
            self._active = False
            self._callback = None
            running = self._running
        if running:
            self._completion.acquire()
            self._completion.release()


class NativeThreadRAGRetrievalCancellation:
    """Cancellation registry safe between the gevent hub and native workers."""

    def __init__(self) -> None:
        self._lock = _allocate_native_lock()
        self._cancelled = False
        self._next_token = 0
        self._callbacks: dict[int, _RegisteredCancellationCallback] = {}
        self._owner_thread_id = _native_get_ident()
        self._control_pool = _get_process_control_pool()

    @property
    def cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def register(self, callback: Callable[[], None]) -> Callable[[], None]:
        if not callable(callback):
            raise RAGRetrievalFanoutConfigurationError()
        registered = _RegisteredCancellationCallback(callback)
        with self._lock:
            if self._cancelled:
                token = None
            else:
                token = self._next_token
                self._next_token += 1
                self._callbacks[token] = registered
        if token is None:
            self._invoke_or_dispatch(registered.invoke)

        def unregister() -> None:
            if token is not None:
                with self._lock:
                    self._callbacks.pop(token, None)
            registered.deactivate()

        return unregister

    def cancel(self) -> None:
        with self._lock:
            if self._cancelled:
                return
            self._cancelled = True
            callbacks = tuple(self._callbacks.values())
            self._callbacks.clear()
        for callback in callbacks:
            self._invoke_or_dispatch(callback.invoke)

    def _invoke_or_dispatch(self, callback: Callable[[], None]) -> None:
        if _native_get_ident() != self._owner_thread_id:
            self._invoke_safely(callback)
            return
        try:
            self._control_pool.apply_async(
                self._invoke_safely,
                args=(callback,),
            )
        except Exception:
            return

    @staticmethod
    def _invoke_safely(callback: Callable[[], None]) -> None:
        try:
            callback()
        except Exception:
            return


class GeventNativeThreadJob(Generic[T]):
    def __init__(self, result) -> None:
        self._result = result

    def ready(self) -> bool:
        return self._result.ready()

    def result(self) -> T:
        return self._result.get()


class GeventNativeThreadRAGRetrievalExecutor:
    """Coordinate one invocation through the process-wide native data pool."""

    def __init__(self, max_workers: int) -> None:
        if (
            isinstance(max_workers, bool)
            or not isinstance(max_workers, int)
            or not 1 <= max_workers <= 20
        ):
            raise RAGRetrievalFanoutConfigurationError()
        self._pool = _get_process_data_pool()
        self._max_workers = max_workers
        self._state_lock = _allocate_native_lock()
        self._active_jobs = 0
        self._closed = False

    def submit(self, callback: Callable[[], T]) -> RAGRetrievalJob[T]:
        if not callable(callback):
            raise RAGRetrievalFanoutConfigurationError()
        self._reserve_invocation_job()
        if not _reserve_process_data_job():
            self._release_invocation_job()
            raise RAGRetrievalFanoutError()
        try:
            return GeventNativeThreadJob(gevent.spawn(self._run_admitted, callback))
        except BaseException:
            _release_process_data_job()
            self._release_invocation_job()
            raise

    def _run_admitted(self, callback: Callable[[], T]) -> T:
        try:
            return self._pool.apply(callback)
        finally:
            _release_process_data_job()
            self._release_invocation_job()

    def _reserve_invocation_job(self) -> None:
        with self._state_lock:
            if self._closed:
                raise RAGRetrievalFanoutConfigurationError()
            if self._active_jobs >= self._max_workers:
                raise RAGRetrievalFanoutError()
            self._active_jobs += 1

    def _release_invocation_job(self) -> None:
        with self._state_lock:
            self._active_jobs -= 1

    def wait(
        self,
        jobs: tuple[RAGRetrievalJob[object], ...],
        *,
        timeout_seconds: float,
    ) -> None:
        if self._closed:
            return
        raw_results = tuple(
            job._result for job in jobs if isinstance(job, GeventNativeThreadJob)
        )
        if raw_results:
            gevent.wait(raw_results, timeout=max(0.0, timeout_seconds), count=1)

    def close(self) -> None:
        with self._state_lock:
            self._closed = True


__all__ = [
    "PROCESS_RAG_RETRIEVAL_CONTROL_MAX_WORKERS",
    "PROCESS_RAG_RETRIEVAL_MAX_WORKERS",
    "GeventNativeThreadJob",
    "GeventNativeThreadRAGRetrievalExecutor",
    "NativeThreadRAGRetrievalCancellation",
]
