"""Native-thread executor adapter for a gevent-monkey-patched Workflow worker."""

from __future__ import annotations

from typing import Callable, Generic, TypeVar

import gevent
from gevent.event import AsyncResult
from gevent.monkey import get_original
from gevent.threadpool import ThreadPool

from apps.workflow_engine.application.rag_retrieval_fanout import (
    RAGRetrievalFanoutConfigurationError,
    RAGRetrievalJob,
)


T = TypeVar("T")
_allocate_native_lock = get_original("_thread", "allocate_lock")


class NativeThreadRAGRetrievalCancellation:
    """Cancellation registry safe between the gevent hub and native workers."""

    def __init__(self) -> None:
        self._lock = _allocate_native_lock()
        self._cancelled = False
        self._next_token = 0
        self._callbacks: dict[int, Callable[[], None]] = {}

    @property
    def cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def register(self, callback: Callable[[], None]) -> Callable[[], None]:
        if not callable(callback):
            raise RAGRetrievalFanoutConfigurationError()
        with self._lock:
            if self._cancelled:
                token = None
            else:
                token = self._next_token
                self._next_token += 1
                self._callbacks[token] = callback
        if token is None:
            self._invoke_safely(callback)

        def unregister() -> None:
            if token is None:
                return
            with self._lock:
                self._callbacks.pop(token, None)

        return unregister

    def cancel(self) -> None:
        with self._lock:
            if self._cancelled:
                return
            self._cancelled = True
            callbacks = tuple(self._callbacks.values())
            self._callbacks.clear()
        for callback in callbacks:
            self._invoke_safely(callback)

    @staticmethod
    def _invoke_safely(callback: Callable[[], None]) -> None:
        try:
            callback()
        except Exception:
            return


class GeventNativeThreadJob(Generic[T]):
    def __init__(self, result: AsyncResult) -> None:
        self._result = result

    def ready(self) -> bool:
        return self._result.ready()

    def result(self) -> T:
        return self._result.get()


class GeventNativeThreadRAGRetrievalExecutor:
    """Use gevent's dedicated native thread pool without blocking its hub."""

    _IDLE_TASK_TIMEOUT_SECONDS = 0.1

    def __init__(self, max_workers: int) -> None:
        if (
            isinstance(max_workers, bool)
            or not isinstance(max_workers, int)
            or not 1 <= max_workers <= 20
        ):
            raise RAGRetrievalFanoutConfigurationError()
        self._pool = ThreadPool(
            max_workers,
            idle_task_timeout=self._IDLE_TASK_TIMEOUT_SECONDS,
        )
        self._closed = False

    def submit(self, callback: Callable[[], T]) -> RAGRetrievalJob[T]:
        if self._closed or not callable(callback):
            raise RAGRetrievalFanoutConfigurationError()
        return GeventNativeThreadJob(self._pool.spawn(callback))

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
        if self._closed:
            return
        self._closed = True
        pool = self._pool
        try:
            # ThreadPool.kill() waits when called from an ordinary greenlet.
            # Scheduling it on the hub makes close non-blocking; an active native
            # worker then exits after its task or the short idle timeout.
            pool.hub.loop.run_callback(pool.kill)
        except Exception:
            try:
                pool.fork_watcher.close()
            except Exception:
                pass


__all__ = [
    "GeventNativeThreadJob",
    "GeventNativeThreadRAGRetrievalExecutor",
    "NativeThreadRAGRetrievalCancellation",
]
