"""Bounded application scheduler for blocking per-KB retrieval work."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Callable, Generic, Protocol, TypeVar, runtime_checkable


T = TypeVar("T")


class RAGRetrievalFanoutConfigurationError(ValueError):
    """Redaction-safe configuration failure for the retrieval scheduler."""

    code = "rag.retrieval_fanout_configuration_invalid"


class RAGRetrievalFanoutError(RuntimeError):
    """Redaction-safe terminal failure for fail-fast retrieval."""

    code = "rag.retrieval_fanout_failed"

    def __init__(self) -> None:
        super().__init__("RAG retrieval fan-out failed.")


@dataclass(frozen=True, slots=True)
class RAGRetrievalFanoutTask:
    ordinal: int
    resource_ref: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            isinstance(self.ordinal, bool)
            or not isinstance(self.ordinal, int)
            or self.ordinal < 0
            or not isinstance(self.resource_ref, str)
            or not self.resource_ref
        ):
            raise RAGRetrievalFanoutConfigurationError()


@runtime_checkable
class RAGRetrievalCancellation(Protocol):
    @property
    def cancelled(self) -> bool: ...

    def register(self, callback: Callable[[], None]) -> Callable[[], None]: ...

    def cancel(self) -> None: ...


class RAGRetrievalJob(Protocol, Generic[T]):
    def ready(self) -> bool: ...

    def result(self) -> T: ...


class RAGRetrievalExecutor(Protocol):
    def submit(self, callback: Callable[[], T]) -> RAGRetrievalJob[T]: ...

    def wait(
        self,
        jobs: tuple[RAGRetrievalJob[object], ...],
        *,
        timeout_seconds: float,
    ) -> None: ...

    def close(self) -> None: ...


RAGRetrievalExecutorFactory = Callable[[int], RAGRetrievalExecutor]
RAGRetrievalCancellationFactory = Callable[[], RAGRetrievalCancellation]
RAGRetrievalWorker = Callable[
    [RAGRetrievalFanoutTask, RAGRetrievalCancellation, int],
    T,
]


@dataclass(frozen=True, slots=True)
class RAGRetrievalFanoutResult(Generic[T]):
    results: tuple[tuple[RAGRetrievalFanoutTask, T], ...] = field(repr=False)
    failed_count: int
    timeout_count: int
    slowest_search_latency_ms: int | None


@dataclass(slots=True)
class _TaskState:
    task: RAGRetrievalFanoutTask
    cancellation: RAGRetrievalCancellation
    started_at: float | None = None
    finished_at: float | None = None
    budget_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class _TerminalOutcome(Generic[T]):
    task: RAGRetrievalFanoutTask
    value: T | None = field(default=None, repr=False)
    failed: bool = False
    timed_out: bool = False
    elapsed_ms: int | None = None


class _TaskStartDeadlineExceeded(TimeoutError):
    pass


class RAGRetrievalFanoutScheduler:
    """Coordinate authorized blocking searches through an injected executor port."""

    MAX_TASKS = 20
    _POLL_INTERVAL_SECONDS = 0.01

    def __init__(
        self,
        *,
        executor_factory: RAGRetrievalExecutorFactory,
        cancellation_factory: RAGRetrievalCancellationFactory,
        max_workers: int = 5,
        per_task_timeout_seconds: float = 10.0,
        aggregate_timeout_seconds: float = 30.0,
        cleanup_reserve_seconds: float = 1.0,
        minimum_start_budget_ms: int = 250,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if (
            not callable(executor_factory)
            or not callable(cancellation_factory)
            or isinstance(max_workers, bool)
            or not isinstance(max_workers, int)
            or not 1 <= max_workers <= self.MAX_TASKS
            or not self._valid_positive_number(per_task_timeout_seconds)
            or not self._valid_positive_number(aggregate_timeout_seconds)
            or not self._valid_positive_number(cleanup_reserve_seconds)
            or cleanup_reserve_seconds >= aggregate_timeout_seconds
            or isinstance(minimum_start_budget_ms, bool)
            or not isinstance(minimum_start_budget_ms, int)
            or minimum_start_budget_ms < 1
            or minimum_start_budget_ms
            >= (aggregate_timeout_seconds - cleanup_reserve_seconds) * 1000
            or not callable(clock)
        ):
            raise RAGRetrievalFanoutConfigurationError()
        self._executor_factory = executor_factory
        self._cancellation_factory = cancellation_factory
        self._max_workers = max_workers
        self._per_task_timeout_seconds = float(per_task_timeout_seconds)
        self._aggregate_timeout_seconds = float(aggregate_timeout_seconds)
        self._cleanup_reserve_seconds = float(cleanup_reserve_seconds)
        self._minimum_start_budget_ms = minimum_start_budget_ms
        self._clock = clock

    def execute(
        self,
        *,
        tasks: tuple[RAGRetrievalFanoutTask, ...],
        worker: RAGRetrievalWorker[T],
        fail_fast: bool = False,
    ) -> RAGRetrievalFanoutResult[T]:
        self._validate_request(tasks=tasks, worker=worker, fail_fast=fail_fast)
        if not tasks:
            return RAGRetrievalFanoutResult((), 0, 0, None)

        invocation_started_at = self._clock()
        hard_deadline = invocation_started_at + self._aggregate_timeout_seconds
        search_deadline = hard_deadline - self._cleanup_reserve_seconds
        try:
            stop_signal = self._cancellation_factory()
            queued = [
                _TaskState(
                    task=task,
                    cancellation=self._cancellation_factory(),
                )
                for task in tasks
            ]
        except Exception:
            raise RAGRetrievalFanoutError() from None
        if not isinstance(stop_signal, RAGRetrievalCancellation) or any(
            not isinstance(state.cancellation, RAGRetrievalCancellation)
            for state in queued
        ):
            raise RAGRetrievalFanoutError() from None
        running: list[tuple[RAGRetrievalJob[T], _TaskState]] = []
        terminal: dict[int, _TerminalOutcome[T]] = {}
        fail_fast_triggered = False

        try:
            executor = self._executor_factory(min(self._max_workers, len(tasks)))
        except Exception:
            raise RAGRetrievalFanoutError() from None

        def invoke(state: _TaskState) -> T:
            if stop_signal.cancelled:
                raise _TaskStartDeadlineExceeded()
            now = self._clock()
            remaining_seconds = search_deadline - now
            if remaining_seconds * 1000 <= self._minimum_start_budget_ms:
                raise _TaskStartDeadlineExceeded()
            budget_seconds = min(self._per_task_timeout_seconds, remaining_seconds)
            if stop_signal.cancelled:
                raise _TaskStartDeadlineExceeded()
            state.started_at = now
            state.budget_seconds = budget_seconds
            try:
                return worker(
                    state.task,
                    state.cancellation,
                    max(1, int(budget_seconds * 1000)),
                )
            finally:
                state.finished_at = self._clock()

        try:
            while (queued or running) and not fail_fast_triggered:
                fail_fast_triggered = self._consume_ready(
                    running=running,
                    terminal=terminal,
                    fail_fast=fail_fast,
                )
                if fail_fast_triggered:
                    break

                now = self._clock()
                for _job, state in tuple(running):
                    if state.task.ordinal in terminal:
                        continue
                    if (
                        state.started_at is not None
                        and state.budget_seconds is not None
                        and now >= state.started_at + state.budget_seconds
                    ):
                        state.cancellation.cancel()
                        terminal[state.task.ordinal] = _TerminalOutcome(
                            task=state.task,
                            failed=True,
                            timed_out=True,
                            elapsed_ms=self._elapsed_ms(state, now),
                        )
                        if fail_fast:
                            fail_fast_triggered = True
                            break
                if fail_fast_triggered:
                    break

                now = self._clock()
                if now >= search_deadline:
                    break
                while queued and len(running) < self._max_workers:
                    remaining_ms = int((search_deadline - self._clock()) * 1000)
                    if remaining_ms <= self._minimum_start_budget_ms:
                        break
                    state = queued.pop(0)
                    try:
                        job = executor.submit(lambda state=state: invoke(state))
                    except Exception:
                        terminal[state.task.ordinal] = _TerminalOutcome(
                            task=state.task,
                            failed=True,
                        )
                        if fail_fast:
                            fail_fast_triggered = True
                            break
                        continue
                    running.append((job, state))
                if fail_fast_triggered:
                    break
                if not running:
                    break
                executor.wait(
                    tuple(job for job, _state in running),
                    timeout_seconds=min(
                        self._POLL_INTERVAL_SECONDS,
                        max(0.0, search_deadline - self._clock()),
                    ),
                )

            if fail_fast_triggered:
                stop_signal.cancel()
                for _job, state in running:
                    if state.task.ordinal not in terminal:
                        state.cancellation.cancel()
            else:
                stop_signal.cancel()
                now = self._clock()
                for state in queued:
                    terminal[state.task.ordinal] = _TerminalOutcome(
                        task=state.task,
                        failed=True,
                        timed_out=True,
                    )
                queued.clear()
                for _job, state in running:
                    if state.task.ordinal in terminal:
                        continue
                    state.cancellation.cancel()
                    terminal[state.task.ordinal] = _TerminalOutcome(
                        task=state.task,
                        failed=True,
                        timed_out=True,
                        elapsed_ms=self._elapsed_ms(state, now),
                    )

            while running and self._clock() < hard_deadline:
                self._discard_ready(running)
                if not running:
                    break
                executor.wait(
                    tuple(job for job, _state in running),
                    timeout_seconds=min(
                        self._POLL_INTERVAL_SECONDS,
                        max(0.0, hard_deadline - self._clock()),
                    ),
                )
        except Exception:
            self._cancel_safely(stop_signal)
            for _job, state in running:
                self._cancel_safely(state.cancellation)
            raise RAGRetrievalFanoutError() from None
        finally:
            self._cancel_safely(stop_signal)
            for _job, state in running:
                self._cancel_safely(state.cancellation)
            try:
                executor.close()
            except Exception:
                pass

        if fail_fast_triggered:
            raise RAGRetrievalFanoutError()

        ordered_outcomes = tuple(terminal[index] for index in sorted(terminal))
        results = tuple(
            (outcome.task, outcome.value)
            for outcome in ordered_outcomes
            if not outcome.failed and not outcome.timed_out
        )
        elapsed_values = tuple(
            outcome.elapsed_ms
            for outcome in ordered_outcomes
            if outcome.elapsed_ms is not None
        )
        return RAGRetrievalFanoutResult(
            results=results,
            failed_count=sum(outcome.failed for outcome in ordered_outcomes),
            timeout_count=sum(outcome.timed_out for outcome in ordered_outcomes),
            slowest_search_latency_ms=max(elapsed_values, default=None),
        )

    def _consume_ready(
        self,
        *,
        running: list[tuple[RAGRetrievalJob[T], _TaskState]],
        terminal: dict[int, _TerminalOutcome[T]],
        fail_fast: bool,
    ) -> bool:
        for job, state in tuple(running):
            if not job.ready():
                continue
            running.remove((job, state))
            if state.task.ordinal in terminal:
                self._consume_ignored_job(job)
                continue
            elapsed_ms = self._elapsed_ms(state, self._clock())
            try:
                value = job.result()
            except (_TaskStartDeadlineExceeded, TimeoutError):
                terminal[state.task.ordinal] = _TerminalOutcome(
                    task=state.task,
                    failed=True,
                    timed_out=True,
                    elapsed_ms=elapsed_ms,
                )
                if fail_fast:
                    return True
            except Exception:
                terminal[state.task.ordinal] = _TerminalOutcome(
                    task=state.task,
                    failed=True,
                    elapsed_ms=elapsed_ms,
                )
                if fail_fast:
                    return True
            else:
                overrun = (
                    state.started_at is not None
                    and state.finished_at is not None
                    and state.budget_seconds is not None
                    and state.finished_at > state.started_at + state.budget_seconds
                )
                terminal[state.task.ordinal] = _TerminalOutcome(
                    task=state.task,
                    value=None if overrun else value,
                    failed=overrun,
                    timed_out=overrun,
                    elapsed_ms=elapsed_ms,
                )
                if overrun and fail_fast:
                    return True
        return False

    @classmethod
    def _discard_ready(
        cls,
        running: list[tuple[RAGRetrievalJob[T], _TaskState]],
    ) -> None:
        for job, state in tuple(running):
            if not job.ready():
                continue
            running.remove((job, state))
            cls._consume_ignored_job(job)

    @staticmethod
    def _consume_ignored_job(job: RAGRetrievalJob[object]) -> None:
        try:
            job.result()
        except Exception:
            return

    @staticmethod
    def _cancel_safely(cancellation: RAGRetrievalCancellation) -> None:
        try:
            cancellation.cancel()
        except Exception:
            return

    @staticmethod
    def _elapsed_ms(state: _TaskState, fallback_end: float) -> int | None:
        if state.started_at is None:
            return None
        end = state.finished_at if state.finished_at is not None else fallback_end
        return max(0, int((end - state.started_at) * 1000))

    def _validate_request(
        self,
        *,
        tasks: tuple[RAGRetrievalFanoutTask, ...],
        worker: RAGRetrievalWorker[T],
        fail_fast: bool,
    ) -> None:
        if (
            not isinstance(tasks, tuple)
            or len(tasks) > self.MAX_TASKS
            or any(not isinstance(task, RAGRetrievalFanoutTask) for task in tasks)
            or len({task.ordinal for task in tasks}) != len(tasks)
            or not callable(worker)
            or type(fail_fast) is not bool
        ):
            raise RAGRetrievalFanoutConfigurationError()

    @staticmethod
    def _valid_positive_number(value: object) -> bool:
        return (
            not isinstance(value, bool)
            and isinstance(value, (int, float))
            and math.isfinite(value)
            and value > 0
        )


__all__ = [
    "RAGRetrievalCancellation",
    "RAGRetrievalCancellationFactory",
    "RAGRetrievalExecutor",
    "RAGRetrievalExecutorFactory",
    "RAGRetrievalFanoutConfigurationError",
    "RAGRetrievalFanoutError",
    "RAGRetrievalFanoutResult",
    "RAGRetrievalFanoutScheduler",
    "RAGRetrievalFanoutTask",
    "RAGRetrievalJob",
]
