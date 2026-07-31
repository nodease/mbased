import inspect
import uuid
from datetime import datetime, timedelta, timezone

from apps.shared.db.models.schedule_dispatch import ScheduleDispatchClaim
from apps.shared.domain.schedule_dispatch import (
    REASON_BUDGET_EVALUATION_FAILED,
    STATUS_PENDING,
)
from apps.workflow_engine.adapters.schedule_dispatch_repository import (
    SqlAlchemyScheduleAdmissionRepository,
)


def test_admission_repository_declares_canonical_lock_order():
    source = inspect.getsource(
        SqlAlchemyScheduleAdmissionRepository.lock_canonical_bundle
    )

    app_lock = source.index("select(App)")
    deployment_lock = source.index("select(WorkflowDeployment)", app_lock)
    schedule_lock = source.index("select(Schedule)", deployment_lock)
    claim_lock = source.index("select(ScheduleDispatchClaim)", schedule_lock)

    assert app_lock < deployment_lock < schedule_lock < claim_lock


def test_admission_repository_uses_wall_clock_after_lock_acquisition():
    source = inspect.getsource(SqlAlchemyScheduleAdmissionRepository.database_now)

    assert "clock_timestamp" in source


def test_admission_repository_uses_deployment_creator_as_credential_principal():
    source = inspect.getsource(
        SqlAlchemyScheduleAdmissionRepository.lock_canonical_bundle
    )

    assert "credential_principal_user_id" in source
    assert "deployment.created_by" in source


def test_worker_budget_deferred_reuses_dispatcher_attempt_count():
    now = datetime.now(timezone.utc)
    claim = ScheduleDispatchClaim(
        id=uuid.uuid4(),
        schedule_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        deployment_id=uuid.uuid4(),
        scheduled_for=now,
        idempotency_key=f"schedule:{uuid.uuid4()}",
        status=STATUS_PENDING,
        attempt_count=2,
        claimed_at=now,
    )
    repository = object.__new__(SqlAlchemyScheduleAdmissionRepository)
    repository._claim = claim

    repository.mark_budget_deferred(
        now=now,
        next_attempt_at=now + timedelta(seconds=5),
        exhausted=False,
    )

    assert claim.attempt_count == 2
    assert claim.status == STATUS_PENDING
    assert claim.safe_reason_code == REASON_BUDGET_EVALUATION_FAILED
