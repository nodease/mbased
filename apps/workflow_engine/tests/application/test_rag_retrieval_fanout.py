import threading
import time

import pytest

from apps.workflow_engine.application.rag_retrieval_fanout import (
    RAGRetrievalFanoutConfigurationError,
    RAGRetrievalFanoutError,
    RAGRetrievalFanoutScheduler,
    RAGRetrievalFanoutTask,
)
from apps.workflow_engine.adapters.rag_retrieval_executor import (
    GeventNativeThreadRAGRetrievalExecutor,
    NativeThreadRAGRetrievalCancellation,
)


def _tasks(count: int) -> tuple[RAGRetrievalFanoutTask, ...]:
    return tuple(
        RAGRetrievalFanoutTask(ordinal=index, resource_ref=f"resource-{index}")
        for index in range(count)
    )


def test_scheduler_overlaps_native_workers_caps_concurrency_and_orders_results() -> (
    None
):
    barrier = threading.Barrier(2)
    state_lock = threading.Lock()
    active = 0
    max_active = 0
    thread_ids: set[int] = set()

    def search(task, _cancellation, _timeout_ms):
        nonlocal active, max_active
        with state_lock:
            active += 1
            max_active = max(max_active, active)
            thread_ids.add(threading.get_ident())
        try:
            barrier.wait(timeout=1)
            time.sleep(0.01 * (4 - task.ordinal))
            return f"result-{task.ordinal}"
        finally:
            with state_lock:
                active -= 1

    result = RAGRetrievalFanoutScheduler(
        executor_factory=GeventNativeThreadRAGRetrievalExecutor,
        cancellation_factory=NativeThreadRAGRetrievalCancellation,
        max_workers=2,
        per_task_timeout_seconds=1,
        aggregate_timeout_seconds=2,
        cleanup_reserve_seconds=0.1,
        minimum_start_budget_ms=1,
    ).execute(tasks=_tasks(4), worker=search)

    assert [task.ordinal for task, _value in result.results] == [0, 1, 2, 3]
    assert [value for _task, value in result.results] == [
        "result-0",
        "result-1",
        "result-2",
        "result-3",
    ]
    assert max_active == 2
    assert len(thread_ids) == 2
    assert result.failed_count == 0
    assert result.timeout_count == 0
    assert isinstance(result.slowest_search_latency_ms, int)


def test_scheduler_timeout_cancels_active_task_and_ignores_late_result() -> None:
    cancellation_observed = threading.Event()

    def search(_task, cancellation, _timeout_ms):
        unregister = cancellation.register(cancellation_observed.set)
        try:
            cancellation_observed.wait(timeout=1)
            return "late-result"
        finally:
            unregister()

    started = time.monotonic()
    result = RAGRetrievalFanoutScheduler(
        executor_factory=GeventNativeThreadRAGRetrievalExecutor,
        cancellation_factory=NativeThreadRAGRetrievalCancellation,
        max_workers=1,
        per_task_timeout_seconds=0.03,
        aggregate_timeout_seconds=0.2,
        cleanup_reserve_seconds=0.05,
        minimum_start_budget_ms=1,
    ).execute(tasks=_tasks(1), worker=search)

    assert time.monotonic() - started < 0.5
    assert cancellation_observed.is_set()
    assert result.results == ()
    assert result.failed_count == 1
    assert result.timeout_count == 1


def test_scheduler_hard_deadline_does_not_wait_for_uncooperative_worker() -> None:
    worker_finished = threading.Event()

    def search(_task, _cancellation, _timeout_ms):
        try:
            time.sleep(0.3)
            return "late-result"
        finally:
            worker_finished.set()

    started = time.monotonic()
    result = RAGRetrievalFanoutScheduler(
        executor_factory=GeventNativeThreadRAGRetrievalExecutor,
        cancellation_factory=NativeThreadRAGRetrievalCancellation,
        max_workers=1,
        per_task_timeout_seconds=0.03,
        aggregate_timeout_seconds=0.12,
        cleanup_reserve_seconds=0.05,
        minimum_start_budget_ms=1,
    ).execute(tasks=_tasks(1), worker=search)
    scheduler_elapsed = time.monotonic() - started

    assert scheduler_elapsed < 0.25
    assert result.results == ()
    assert result.failed_count == 1
    assert result.timeout_count == 1
    assert worker_finished.wait(timeout=1)


def test_scheduler_fail_fast_cancels_other_work_and_uses_safe_error() -> None:
    barrier = threading.Barrier(2)
    cancellation_observed = threading.Event()

    def search(task, cancellation, _timeout_ms):
        barrier.wait(timeout=1)
        if task.ordinal == 0:
            raise RuntimeError("private-database-detail")
        unregister = cancellation.register(cancellation_observed.set)
        try:
            cancellation_observed.wait(timeout=1)
            return "late-result"
        finally:
            unregister()

    scheduler = RAGRetrievalFanoutScheduler(
        executor_factory=GeventNativeThreadRAGRetrievalExecutor,
        cancellation_factory=NativeThreadRAGRetrievalCancellation,
        max_workers=2,
        per_task_timeout_seconds=1,
        aggregate_timeout_seconds=1,
        cleanup_reserve_seconds=0.1,
        minimum_start_budget_ms=1,
    )

    with pytest.raises(RAGRetrievalFanoutError) as captured:
        scheduler.execute(tasks=_tasks(2), worker=search, fail_fast=True)

    assert cancellation_observed.is_set()
    assert "private-database-detail" not in str(captured.value)


def test_scheduler_redacts_executor_coordination_failure() -> None:
    class NeverReadyJob:
        def ready(self):
            return False

        def result(self):
            raise AssertionError("unfinished job must not be consumed")

    class FailingExecutor:
        def __init__(self):
            self.closed = False

        def submit(self, _callback):
            return NeverReadyJob()

        def wait(self, _jobs, *, timeout_seconds):
            assert timeout_seconds > 0
            raise RuntimeError("private-executor-detail")

        def close(self):
            self.closed = True

    executor = FailingExecutor()
    scheduler = RAGRetrievalFanoutScheduler(
        executor_factory=lambda _max_workers: executor,
        cancellation_factory=NativeThreadRAGRetrievalCancellation,
        aggregate_timeout_seconds=1,
        cleanup_reserve_seconds=0.1,
        minimum_start_budget_ms=1,
    )

    with pytest.raises(RAGRetrievalFanoutError) as captured:
        scheduler.execute(tasks=_tasks(1), worker=lambda *_args: None)

    assert executor.closed is True
    assert "private-executor-detail" not in str(captured.value)


def test_scheduler_rejects_more_than_runtime_candidate_cap() -> None:
    scheduler = RAGRetrievalFanoutScheduler(
        executor_factory=GeventNativeThreadRAGRetrievalExecutor,
        cancellation_factory=NativeThreadRAGRetrievalCancellation,
    )

    with pytest.raises(RAGRetrievalFanoutConfigurationError):
        scheduler.execute(tasks=_tasks(21), worker=lambda *_args: None)
