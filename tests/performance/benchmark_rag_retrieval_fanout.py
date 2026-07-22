"""Synthetic benchmark for bounded blocking RAG retrieval fan-out."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import threading
import time

from apps.workflow_engine.application.rag_retrieval_fanout import (
    RAGRetrievalFanoutScheduler,
    RAGRetrievalFanoutTask,
)
from apps.workflow_engine.adapters.rag_retrieval_executor import (
    GeventNativeThreadRAGRetrievalExecutor,
    NativeThreadRAGRetrievalCancellation,
)


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _summarize(values: list[float]) -> dict[str, float]:
    return {
        "p50_ms": round(statistics.median(values), 3),
        "p95_ms": round(_percentile(values, 0.95), 3),
    }


def _sequential_elapsed_ms(candidate_count: int, delay_seconds: float) -> float:
    started = time.perf_counter()
    for _index in range(candidate_count):
        time.sleep(delay_seconds)
    return (time.perf_counter() - started) * 1000


def _fanout_elapsed_ms(
    candidate_count: int,
    delay_seconds: float,
) -> tuple[float, int, int]:
    state_lock = threading.Lock()
    active = 0
    max_active = 0
    search_calls = 0

    def search(_task, _cancellation, _timeout_ms):
        nonlocal active, max_active, search_calls
        with state_lock:
            active += 1
            search_calls += 1
            max_active = max(max_active, active)
        try:
            time.sleep(delay_seconds)
            return None
        finally:
            with state_lock:
                active -= 1

    tasks = tuple(
        RAGRetrievalFanoutTask(index, f"benchmark-{index}")
        for index in range(candidate_count)
    )
    started = time.perf_counter()
    result = RAGRetrievalFanoutScheduler(
        executor_factory=GeventNativeThreadRAGRetrievalExecutor,
        cancellation_factory=NativeThreadRAGRetrievalCancellation,
        aggregate_timeout_seconds=5,
        cleanup_reserve_seconds=0.1,
        per_task_timeout_seconds=2,
        minimum_start_budget_ms=1,
    ).execute(tasks=tasks, worker=search)
    elapsed_ms = (time.perf_counter() - started) * 1000
    if result.failed_count or len(result.results) != candidate_count:
        raise RuntimeError("synthetic RAG fan-out benchmark did not complete")
    return elapsed_ms, max_active, search_calls


def run(*, candidates: tuple[int, ...], delay_ms: int, iterations: int) -> dict:
    delay_seconds = delay_ms / 1000
    results = []
    for candidate_count in candidates:
        _sequential_elapsed_ms(candidate_count, delay_seconds)
        _fanout_elapsed_ms(candidate_count, delay_seconds)
        sequential_timings = []
        fanout_timings = []
        max_active = 0
        search_calls = 0
        for _index in range(iterations):
            sequential_timings.append(
                _sequential_elapsed_ms(candidate_count, delay_seconds)
            )
            elapsed_ms, observed_active, observed_calls = _fanout_elapsed_ms(
                candidate_count,
                delay_seconds,
            )
            fanout_timings.append(elapsed_ms)
            max_active = max(max_active, observed_active)
            search_calls += observed_calls
        results.append(
            {
                "candidate_count": candidate_count,
                "sequential": _summarize(sequential_timings),
                "bounded_fanout": _summarize(fanout_timings),
                "max_active_worker": max_active,
                "search_calls": search_calls,
                "simulated_query_embedding_provider_calls": iterations,
            }
        )
    return {
        "delay_ms": delay_ms,
        "iterations": iterations,
        "query_embedding_precomputed": True,
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", default="1,2,4,10,20")
    parser.add_argument("--delay-ms", type=int, default=20)
    parser.add_argument("--iterations", type=int, default=20)
    args = parser.parse_args()
    candidates = tuple(int(value) for value in args.candidates.split(","))
    if (
        not candidates
        or any(value < 1 or value > 20 for value in candidates)
        or args.delay_ms < 1
        or args.iterations < 1
    ):
        raise SystemExit("benchmark arguments are outside the safe bounded range")
    print(
        json.dumps(
            run(
                candidates=candidates,
                delay_ms=args.delay_ms,
                iterations=args.iterations,
            ),
            ensure_ascii=True,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
