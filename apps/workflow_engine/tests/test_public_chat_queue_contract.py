from pathlib import Path

from apps.shared.celery_app import celery_app
from apps.shared.services.workflow_task_publisher import (
    PUBLIC_CHAT_WORKFLOW_QUEUE,
    PUBLIC_CHAT_WORKFLOW_TASK_NAME,
)
from apps.workflow_engine import tasks


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def test_public_chat_task_uses_versioned_dedicated_queue() -> None:
    assert tasks.execute_public_chat_workflow.name == PUBLIC_CHAT_WORKFLOW_TASK_NAME
    assert celery_app.conf.task_routes[PUBLIC_CHAT_WORKFLOW_TASK_NAME] == {
        "queue": PUBLIC_CHAT_WORKFLOW_QUEUE
    }
    routed = celery_app.amqp.router.route({}, PUBLIC_CHAT_WORKFLOW_TASK_NAME)
    assert routed["queue"].name == PUBLIC_CHAT_WORKFLOW_QUEUE
    assert PUBLIC_CHAT_WORKFLOW_QUEUE != "workflow"


def test_workflow_workers_consume_public_chat_queue_during_rollout() -> None:
    entrypoint = (
        REPOSITORY_ROOT / "docker" / "workflow_engine" / "docker-entrypoint.sh"
    ).read_text(encoding="utf-8")
    development_script = (REPOSITORY_ROOT / "scripts" / "dev.sh").read_text(
        encoding="utf-8"
    )

    assert (
        f"--queues=workflow,{PUBLIC_CHAT_WORKFLOW_QUEUE}"
        in entrypoint
    )
    assert f"-Q workflow,{PUBLIC_CHAT_WORKFLOW_QUEUE}" in development_script
