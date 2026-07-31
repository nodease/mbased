from types import SimpleNamespace
from uuid import uuid4

from apps.log_system.tasks import (
    _record_workflow_execute_audit,
    _schedule_audit_organization_id,
    _workflow_execute_audit_organization_id,
)
from apps.shared.audit.actions import AuditAction
from apps.shared.db.models.app import App
from apps.shared.db.models.schedule_dispatch import ScheduleDispatchClaim
from apps.shared.db.models.workflow import Workflow
from apps.shared.db.models.workflow_deployment import WorkflowDeployment
from apps.shared.db.models.workflow_run import RunTriggerMode


def test_record_workflow_execute_success_audit(monkeypatch):
    calls = []
    run = SimpleNamespace(
        id=uuid4(),
        user_id=uuid4(),
        workflow_id=uuid4(),
        trigger_mode=RunTriggerMode.MANUAL,
        request_id="req-1",
        correlation_id="corr-1",
        error_message=None,
    )
    monkeypatch.setattr(
        "apps.log_system.tasks.record_audit",
        lambda **kwargs: calls.append(kwargs),
    )

    _record_workflow_execute_audit(run, "success")

    assert len(calls) == 1
    event = calls[0]
    assert event["action"] == AuditAction.WORKFLOW_EXECUTE
    assert event["actor_id"] == run.user_id
    assert event["target_type"] == "workflow"
    assert event["target_id"] == run.workflow_id
    assert event["status"] == "success"
    assert event["metadata"]["policy_result"] == "allow"
    assert event["metadata"]["workflow_run_id"] == str(run.id)
    assert event["metadata"]["trigger_mode"] == "manual"


def test_record_workflow_execute_failure_audit_omits_raw_error(monkeypatch):
    calls = []
    run = SimpleNamespace(
        id=uuid4(),
        user_id=uuid4(),
        workflow_id=uuid4(),
        trigger_mode=RunTriggerMode.API,
        request_id=None,
        correlation_id=None,
        error_message="sensitive provider response",
    )
    monkeypatch.setattr(
        "apps.log_system.tasks.record_audit",
        lambda **kwargs: calls.append(kwargs),
    )

    _record_workflow_execute_audit(
        run,
        "failure",
        reason_code="workflow.execute_failed",
    )

    metadata = calls[0]["metadata"]
    assert calls[0]["status"] == "failure"
    assert metadata["reason_code"] == "workflow.execute_failed"
    assert metadata["error_present"] is True
    assert "sensitive provider response" not in str(metadata)


def test_system_schedule_execute_audit_has_no_user_actor(monkeypatch):
    calls = []
    run = SimpleNamespace(
        id=uuid4(),
        user_id=None,
        workflow_id=uuid4(),
        trigger_mode=RunTriggerMode.SCHEDULER,
        request_id=None,
        correlation_id=None,
        error_message=None,
    )
    monkeypatch.setattr(
        "apps.log_system.tasks.record_audit",
        lambda **kwargs: calls.append(kwargs),
    )

    _record_workflow_execute_audit(run, "success")

    assert calls[0]["actor_id"] is None
    assert calls[0]["actor_type"] == "system"


def test_system_schedule_execute_audit_includes_durable_organization(monkeypatch):
    calls = []
    organization_id = uuid4()
    run = SimpleNamespace(
        id=uuid4(),
        user_id=None,
        workflow_id=uuid4(),
        trigger_mode=RunTriggerMode.SCHEDULER,
        request_id=None,
        correlation_id=None,
        error_message=None,
    )
    monkeypatch.setattr(
        "apps.log_system.tasks.record_audit",
        lambda **kwargs: calls.append(kwargs),
    )

    _record_workflow_execute_audit(
        run,
        "success",
        organization_id=organization_id,
    )

    assert calls[0]["actor_id"] is None
    assert calls[0]["actor_type"] == "system"
    assert calls[0]["metadata"]["organization_id"] == str(organization_id)


class _Query:
    def __init__(self, row):
        self.row = row

    def filter(self, *args):
        return self

    def first(self):
        return self.row


class _Session:
    def __init__(self, rows):
        self.rows = rows

    def query(self, model):
        return _Query(self.rows.get(model))


def test_interactive_workflow_execute_audit_uses_workflow_organization():
    organization_id = uuid4()
    workflow_id = uuid4()
    run = SimpleNamespace(
        id=uuid4(),
        user_id=uuid4(),
        workflow_id=workflow_id,
        workflow_task_id=None,
        trigger_mode=RunTriggerMode.MANUAL,
    )

    result = _workflow_execute_audit_organization_id(
        _Session(
            {
                Workflow: SimpleNamespace(
                    id=workflow_id,
                    organization_id=organization_id,
                )
            }
        ),
        run,
    )

    assert result == organization_id


def test_schedule_audit_organization_requires_claim_run_deployment_and_workflow_match():
    run_id = uuid4()
    deployment_id = uuid4()
    workflow_id = uuid4()
    organization_id = uuid4()
    app_id = uuid4()
    task_id = f"schedule:{uuid4()}"
    run = SimpleNamespace(
        id=run_id,
        workflow_run_id=run_id,
        workflow_id=workflow_id,
        deployment_id=deployment_id,
        workflow_task_id=task_id,
        trigger_mode=RunTriggerMode.SCHEDULER,
    )
    claim = SimpleNamespace(
        workflow_run_id=run_id,
        deployment_id=deployment_id,
        organization_id=organization_id,
        idempotency_key=task_id,
    )
    deployment = SimpleNamespace(id=deployment_id, app_id=app_id)
    app = SimpleNamespace(
        id=app_id,
        workflow_id=workflow_id,
        organization_id=organization_id,
    )

    result = _schedule_audit_organization_id(
        _Session(
            {
                ScheduleDispatchClaim: claim,
                WorkflowDeployment: deployment,
                App: app,
            }
        ),
        run,
    )

    assert result == organization_id


def test_schedule_audit_organization_fails_closed_on_current_app_mismatch():
    run_id = uuid4()
    deployment_id = uuid4()
    organization_id = uuid4()
    run = SimpleNamespace(
        id=run_id,
        workflow_id=uuid4(),
        deployment_id=deployment_id,
        workflow_task_id=f"schedule:{uuid4()}",
        trigger_mode=RunTriggerMode.SCHEDULER,
    )
    claim = SimpleNamespace(
        workflow_run_id=run_id,
        deployment_id=deployment_id,
        organization_id=organization_id,
    )

    assert (
        _schedule_audit_organization_id(
            _Session(
                {
                    ScheduleDispatchClaim: claim,
                    WorkflowDeployment: SimpleNamespace(
                        id=deployment_id,
                        app_id=uuid4(),
                    ),
                    App: SimpleNamespace(
                        workflow_id=uuid4(),
                        organization_id=uuid4(),
                    ),
                }
            ),
            run,
        )
        is None
    )


def test_schedule_audit_uses_durable_claim_after_deployment_is_deleted():
    run_id = uuid4()
    organization_id = uuid4()
    task_id = f"schedule:{uuid4()}"
    run = SimpleNamespace(
        id=run_id,
        workflow_id=uuid4(),
        deployment_id=None,
        workflow_task_id=task_id,
        trigger_mode=RunTriggerMode.SCHEDULER,
    )
    claim = SimpleNamespace(
        workflow_run_id=run_id,
        deployment_id=uuid4(),
        organization_id=organization_id,
        idempotency_key=task_id,
    )

    result = _schedule_audit_organization_id(
        _Session(
            {
                ScheduleDispatchClaim: claim,
                WorkflowDeployment: None,
                App: None,
            }
        ),
        run,
    )

    assert result == organization_id


def test_schedule_audit_fails_closed_when_live_run_and_claim_deployments_differ():
    run_id = uuid4()
    run = SimpleNamespace(
        id=run_id,
        workflow_id=uuid4(),
        deployment_id=uuid4(),
        workflow_task_id=f"schedule:{uuid4()}",
        trigger_mode=RunTriggerMode.SCHEDULER,
    )
    claim = SimpleNamespace(
        workflow_run_id=run_id,
        deployment_id=uuid4(),
        organization_id=uuid4(),
    )

    result = _schedule_audit_organization_id(
        _Session({ScheduleDispatchClaim: claim}),
        run,
    )

    assert result is None
