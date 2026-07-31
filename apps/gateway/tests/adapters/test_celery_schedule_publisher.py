import uuid

from apps.gateway.adapters.queue.celery_schedule_publisher import (
    CeleryScheduleTaskPublisher,
)
from apps.gateway.application.deployment.schedule_models import (
    SchedulePublishRequest,
)


class _Celery:
    def __init__(self):
        self.calls = []

    def send_task(self, *args, **kwargs):
        self.calls.append((args, kwargs))


def test_schedule_publisher_sends_only_claim_locator_with_deterministic_task_id():
    celery = _Celery()
    request = SchedulePublishRequest(
        claim_id=uuid.uuid4(),
        task_id=f"schedule:{uuid.uuid4()}",
        lease_owner="must-not-be-published",
    )

    CeleryScheduleTaskPublisher(celery).publish(request)

    assert celery.calls == [
        (
            ("workflow.execute_scheduled_deployment",),
                {
                    "args": [str(request.claim_id)],
                    "kwargs": {},
                    "task_id": request.task_id,
                    "retry": False,
                    "ignore_result": True,
                    "argsrepr": "[workflow arguments redacted]",
                    "kwargsrepr": "{workflow arguments redacted}",
                },
        )
    ]
    assert request.lease_owner not in str(celery.calls)
