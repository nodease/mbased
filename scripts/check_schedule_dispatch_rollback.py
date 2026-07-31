"""Fail-closed transition preflight for durable schedule dispatch."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import redis

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from apps.gateway.adapters.db.schedule_dispatch_repository import (  # noqa: E402
    SqlAlchemyScheduleDispatchRepository,
)
from apps.gateway.application.deployment.schedule_rollback_preflight import (  # noqa: E402
    ScheduleRollbackPreflightUseCase,
)
from apps.shared.celery_app import celery_app  # noqa: E402
from apps.shared.db.session import SessionLocal  # noqa: E402

_SCHEDULE_TASK = "workflow.execute_scheduled_deployment"
_LEGACY_DEPLOYMENT_TASK = "workflow.execute_by_deployment"
_WORKFLOW_QUEUE = b"workflow"
_REDIS_PRIORITY_SEPARATOR = b"\x06\x16"


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify that schedule dispatch can be rolled back without replay."
    )
    parser.add_argument("--inspect-timeout", type=float, default=5.0)
    parser.add_argument("--expected-worker-nodes", required=True)
    parser.add_argument("--stable-observations", type=int, default=2)
    parser.add_argument("--stability-seconds", type=float, default=2.0)
    parser.add_argument(
        "--purpose",
        choices=("activation", "rollback"),
        default="rollback",
    )
    args = parser.parse_args()
    if not 1.0 <= args.inspect_timeout <= 30.0:
        parser.error("--inspect-timeout must be between 1 and 30 seconds")
    if not 2 <= args.stable_observations <= 5:
        parser.error("--stable-observations must be between 2 and 5")
    if not 0.5 <= args.stability_seconds <= 30.0:
        parser.error("--stability-seconds must be between 0.5 and 30 seconds")
    args.expected_worker_nodes = frozenset(
        item.strip()
        for item in args.expected_worker_nodes.split(",")
        if item.strip()
    )
    if not args.expected_worker_nodes:
        parser.error("--expected-worker-nodes must not be empty")
    return args


def _worker_node_name(value: object) -> str:
    text = str(value or "")
    return text.split("@", 1)[-1]


def _active_schedule_task_count(
    *,
    timeout: float,
    include_legacy: bool = False,
    expected_workers: frozenset[str] | None = None,
) -> int:
    inspector = celery_app.control.inspect(timeout=timeout)
    task_names = {_SCHEDULE_TASK}
    if include_legacy:
        task_names.add(_LEGACY_DEPLOYMENT_TASK)

    # Inspect in both directions so a task moving scheduled -> reserved -> active
    # cannot disappear between the individual Celery snapshots.
    responses = (
        ("active", inspector.active()),
        ("reserved", inspector.reserved()),
        ("scheduled", inspector.scheduled()),
        ("scheduled", inspector.scheduled()),
        ("reserved", inspector.reserved()),
        ("active", inspector.active()),
    )
    state_counts: dict[str, list[int]] = {
        "active": [],
        "reserved": [],
        "scheduled": [],
    }
    observed_workers: frozenset[str] | None = None
    for state, response in responses:
        if response is None:
            raise RuntimeError("worker task inspection is unavailable")
        response_workers = frozenset(_worker_node_name(name) for name in response)
        if observed_workers is None:
            observed_workers = response_workers
        elif response_workers != observed_workers:
            raise RuntimeError("worker task inspection is incomplete")
        if expected_workers is not None and response_workers != expected_workers:
            raise RuntimeError("worker task inspection is incomplete")

        count = 0
        for tasks in response.values():
            for task in tasks or []:
                request = task.get("request") if isinstance(task, dict) else None
                name = (
                    request.get("name")
                    if isinstance(request, dict)
                    else task.get("name") if isinstance(task, dict) else None
                )
                if name in task_names:
                    count += 1
        state_counts[state].append(count)

    return sum(max(counts) for counts in state_counts.values())


def _workflow_queue_depth(*, timeout: float) -> int:
    client = redis.Redis.from_url(
        str(celery_app.conf.broker_url),
        socket_connect_timeout=timeout,
        socket_timeout=timeout,
    )
    try:
        depth = 0
        for key in client.scan_iter(match=b"workflow*"):
            if not (
                key == _WORKFLOW_QUEUE
                or key.startswith(_WORKFLOW_QUEUE + _REDIS_PRIORITY_SEPARATOR)
            ):
                continue
            if client.type(key) == b"list":
                depth += int(client.llen(key))
        return depth
    finally:
        client.close()


def _observe_stable_drain(
    *,
    timeout: float,
    include_legacy: bool,
    expected_workers: frozenset[str],
    stable_observations: int,
    stability_seconds: float,
) -> tuple[int, int]:
    active_tasks = 0
    queued_tasks = 0
    for observation in range(stable_observations):
        queued_before = _workflow_queue_depth(timeout=timeout)
        active_before = _active_schedule_task_count(
            timeout=timeout,
            include_legacy=include_legacy,
            expected_workers=expected_workers,
        )
        queued_after = _workflow_queue_depth(timeout=timeout)
        active_after = _active_schedule_task_count(
            timeout=timeout,
            include_legacy=include_legacy,
            expected_workers=expected_workers,
        )
        active_tasks = max(active_before, active_after)
        queued_tasks = max(queued_before, queued_after)
        if active_tasks or queued_tasks:
            break
        if observation + 1 < stable_observations:
            time.sleep(stability_seconds)
    return active_tasks, queued_tasks


def main() -> int:
    args = _arguments()
    db = None
    try:
        db = SessionLocal()
        blockers = ScheduleRollbackPreflightUseCase().evaluate(
            repository=SqlAlchemyScheduleDispatchRepository(db)
        )
        active_tasks, queued_tasks = _observe_stable_drain(
            timeout=args.inspect_timeout,
            include_legacy=args.purpose == "activation",
            expected_workers=args.expected_worker_nodes,
            stable_observations=args.stable_observations,
            stability_seconds=args.stability_seconds,
        )
    except Exception as exc:
        print(f"schedule transition preflight failed: error_type={type(exc).__name__}")
        return 1
    finally:
        if db is not None:
            db.close()

    if not blockers.ready or active_tasks or queued_tasks:
        print(
            "schedule dispatch transition blocked: "
            f"nonterminal_claims={blockers.nonterminal_claims} "
            "unreviewed_outcomes="
            f"{blockers.unreviewed_outcome_unknown_claims} "
            f"active_tasks={active_tasks} queued_tasks={queued_tasks}"
        )
        return 1

    print("schedule dispatch transition preflight passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
