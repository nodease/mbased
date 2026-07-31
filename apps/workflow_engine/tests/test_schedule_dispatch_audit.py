from __future__ import annotations

import uuid

import pytest

from apps.shared.db.models.audit_log import ActorType, AuditStatus
from apps.workflow_engine.adapters.schedule_dispatch_audit import (
    SqlAlchemyScheduleAdmissionAuditRecorder,
)


class _Session:
    def __init__(self) -> None:
        self.rows = []

    def add(self, row) -> None:
        self.rows.append(row)


def test_worker_budget_block_uses_system_policy_audit():
    session = _Session()
    organization_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    claim_id = uuid.uuid4()

    SqlAlchemyScheduleAdmissionAuditRecorder(session).record_budget_block(
        organization_id=organization_id,
        workflow_id=workflow_id,
        claim_id=claim_id,
    )

    row = session.rows[0]
    assert row.action == "policy.block"
    assert row.actor_id is None
    assert row.actor_type == ActorType.SYSTEM
    assert row.target_type == "workflow"
    assert row.target_id == str(workflow_id)
    assert row.status == AuditStatus.FAILURE
    assert row.audit_metadata["reason"] == "budget.exceeded"
    assert row.audit_metadata["policy_reason"] == "budget.exceeded"
    assert row.audit_metadata["trigger_mode"] == "scheduler"


def test_worker_terminal_admission_failure_is_failure_audit():
    session = _Session()

    SqlAlchemyScheduleAdmissionAuditRecorder(session).record_claim_result(
        organization_id=uuid.uuid4(),
        claim_id=uuid.uuid4(),
        action="schedule_dispatch.failed",
        reason="budget_evaluation_failed",
    )

    row = session.rows[0]
    assert row.action == "schedule_dispatch.failed"
    assert row.status == AuditStatus.FAILURE


def test_schedule_admission_audit_rejects_unbounded_reason():
    recorder = SqlAlchemyScheduleAdmissionAuditRecorder(_Session())

    with pytest.raises(ValueError, match="reason"):
        recorder.record_claim_result(
            organization_id=uuid.uuid4(),
            claim_id=uuid.uuid4(),
            action="schedule_dispatch.failed",
            reason="raw-provider-exception",
        )
