from __future__ import annotations

import uuid

import pytest

from apps.shared.domain.schedule_dispatch import ScheduleDispatchSettings
from apps.workflow_engine import tasks
from apps.workflow_engine.application import schedule_dispatch as application
from apps.workflow_engine.domain.external_effect import ExternalEffectError
from apps.workflow_engine.workflow.errors import NonRetryableWorkflowError


class _Session:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _Engine:
    calls = []
    error = None
    cleanup_error = None

    def __init__(self, **kwargs):
        self.__class__.calls.append(kwargs)

    def execute(self):
        if self.error:
            raise self.error
        return {"ok": True}

    def cleanup(self):
        if self.cleanup_error:
            raise self.cleanup_error


def _plan(claim_id, task_id):
    return application.ScheduledExecutionPlan(
        claim_id=claim_id,
        workflow_run_id=uuid.uuid4(),
        admission_owner="owner",
        graph_snapshot={"nodes": []},
        user_input={"schedule_id": str(uuid.uuid4())},
        execution_context={
            "user_id": None,
            "workflow_id": str(uuid.uuid4()),
            "organization_id": str(uuid.uuid4()),
            "workflow_task_id": task_id,
        },
    )


def _assert_logs_redact(caplog, *sensitive_values) -> None:
    messages = "\n".join(caplog.messages)
    for value in sensitive_values:
        assert str(value) not in messages


def test_scheduled_task_runs_engine_only_after_admission_and_finalizes(monkeypatch):
    claim_id = uuid.uuid4()
    task_id = f"schedule:{uuid.uuid4()}"
    sessions = [_Session(), _Session(), _Session(), _Session()]
    calls = []

    class _UseCase:
        def __init__(self, **kwargs):
            pass

        def admit(self, **kwargs):
            calls.append("admit")
            return application.ScheduleAdmissionResult(
                "admitted", plan=_plan(claim_id, task_id)
            )

        def finalize(self, **kwargs):
            calls.append(("finalize", kwargs["succeeded"]))
            return True

    monkeypatch.setattr(tasks, "SessionLocal", lambda: sessions.pop(0))
    monkeypatch.setattr(application, "ScheduledDeploymentExecutionUseCase", _UseCase)
    monkeypatch.setattr(
        "apps.workflow_engine.workflow.core.workflow_engine.WorkflowEngine",
        _Engine,
    )
    monkeypatch.setattr(
        tasks,
        "_sync_knowledge_bases_for_execution_subject",
        lambda *a, **k: {"skipped": True},
    )
    _Engine.calls = []

    result = tasks._execute_scheduled_deployment_claim(
        str(claim_id),
        task_id=task_id,
    )

    assert result["status"] == "success"
    assert set(result) == {"status", "claim_id", "finalized"}
    assert calls == ["admit", ("finalize", True)]
    assert len(_Engine.calls) == 1


def test_scheduled_task_cleanup_failure_keeps_successful_claim_finalization(
    monkeypatch,
    caplog,
):
    claim_id = uuid.uuid4()
    task_id = f"schedule:{uuid.uuid4()}"
    finalized = []

    class _UseCase:
        def __init__(self, **kwargs):
            pass

        def admit(self, **kwargs):
            return application.ScheduleAdmissionResult(
                "admitted", plan=_plan(claim_id, task_id)
            )

        def finalize(self, **kwargs):
            finalized.append(kwargs["succeeded"])
            return True

    monkeypatch.setattr(tasks, "SessionLocal", _Session)
    monkeypatch.setattr(application, "ScheduledDeploymentExecutionUseCase", _UseCase)
    monkeypatch.setattr(
        "apps.workflow_engine.workflow.core.workflow_engine.WorkflowEngine",
        _Engine,
    )
    monkeypatch.setattr(
        tasks,
        "_sync_knowledge_bases_for_execution_subject",
        lambda *a, **k: {"skipped": True},
    )
    _Engine.cleanup_error = RuntimeError("cleanup detail must not escape")
    try:
        result = tasks._execute_scheduled_deployment_claim(
            str(claim_id),
            task_id=task_id,
        )
    finally:
        _Engine.cleanup_error = None

    assert result["status"] == "success"
    assert finalized == [True]
    _assert_logs_redact(caplog, claim_id, "cleanup detail must not escape")


def test_scheduled_task_knowledge_sync_failure_does_not_skip_engine(
    monkeypatch,
    caplog,
):
    claim_id = uuid.uuid4()
    task_id = f"schedule:{uuid.uuid4()}"
    finalized = []

    class _UseCase:
        def __init__(self, **kwargs):
            pass

        def admit(self, **kwargs):
            return application.ScheduleAdmissionResult(
                "admitted", plan=_plan(claim_id, task_id)
            )

        def finalize(self, **kwargs):
            finalized.append(kwargs["succeeded"])
            return True

    monkeypatch.setattr(tasks, "SessionLocal", _Session)
    monkeypatch.setattr(application, "ScheduledDeploymentExecutionUseCase", _UseCase)
    monkeypatch.setattr(
        "apps.workflow_engine.workflow.core.workflow_engine.WorkflowEngine",
        _Engine,
    )
    monkeypatch.setattr(
        tasks,
        "_sync_knowledge_bases_for_execution_subject",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("connector detail")),
    )
    _Engine.calls = []

    result = tasks._execute_scheduled_deployment_claim(
        str(claim_id),
        task_id=task_id,
    )

    assert result["status"] == "success"
    assert set(result) == {"status", "claim_id", "finalized"}
    assert len(_Engine.calls) == 1
    assert finalized == [True]
    _assert_logs_redact(caplog, claim_id, "connector detail")


def test_scheduled_task_retries_only_finalization_with_fresh_sessions(
    monkeypatch,
    caplog,
):
    claim_id = uuid.uuid4()
    task_id = f"schedule:{uuid.uuid4()}"
    finalization_attempts = []
    sessions = []

    class _UseCase:
        def __init__(self, **kwargs):
            pass

        def admit(self, **kwargs):
            return application.ScheduleAdmissionResult(
                "admitted", plan=_plan(claim_id, task_id)
            )

        def finalize(self, **kwargs):
            finalization_attempts.append(kwargs["succeeded"])
            if len(finalization_attempts) == 1:
                raise RuntimeError("database endpoint must not escape")
            return True

    def session_factory():
        session = _Session()
        sessions.append(session)
        return session

    monkeypatch.setattr(tasks, "SessionLocal", session_factory)
    monkeypatch.setattr(application, "ScheduledDeploymentExecutionUseCase", _UseCase)
    monkeypatch.setattr(
        "apps.workflow_engine.workflow.core.workflow_engine.WorkflowEngine",
        _Engine,
    )
    monkeypatch.setattr(
        tasks,
        "_sync_knowledge_bases_for_execution_subject",
        lambda *a, **k: {"skipped": True},
    )
    _Engine.calls = []

    result = tasks._execute_scheduled_deployment_claim(
        str(claim_id),
        task_id=task_id,
    )

    assert result["status"] == "success"
    assert len(_Engine.calls) == 1
    assert finalization_attempts == [True, True]
    assert len(sessions) == 5
    assert all(session.closed for session in sessions)
    _assert_logs_redact(caplog, claim_id, "database endpoint must not escape")


def test_scheduled_task_duplicate_does_not_construct_engine(monkeypatch):
    claim_id = uuid.uuid4()
    task_id = f"schedule:{uuid.uuid4()}"

    class _UseCase:
        def __init__(self, **kwargs):
            pass

        def admit(self, **kwargs):
            return application.ScheduleAdmissionResult("duplicate", "running")

    monkeypatch.setattr(tasks, "SessionLocal", _Session)
    monkeypatch.setattr(application, "ScheduledDeploymentExecutionUseCase", _UseCase)
    _Engine.calls = []

    result = tasks._execute_scheduled_deployment_claim(
        str(claim_id),
        task_id=task_id,
    )

    assert result["status"] == "duplicate"
    assert _Engine.calls == []


def test_scheduled_task_configuration_block_does_not_start_knowledge_or_engine(
    monkeypatch,
):
    claim_id = uuid.uuid4()
    task_id = f"schedule:{uuid.uuid4()}"
    admissions = []

    class _UseCase:
        def __init__(self, **kwargs):
            pass

        def admit(self, **kwargs):
            admissions.append(kwargs)
            return application.ScheduleAdmissionResult(
                "rejected",
                "configuration_preflight_blocked",
            )

    monkeypatch.setattr(tasks, "SessionLocal", _Session)
    monkeypatch.setattr(application, "ScheduledDeploymentExecutionUseCase", _UseCase)
    monkeypatch.setattr(
        "apps.workflow_engine.composition.schedule_dispatch.build_scheduled_workflow_engine",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("configuration-blocked claim must not construct engine")
        ),
    )
    monkeypatch.setattr(
        tasks,
        "_sync_knowledge_bases_for_execution_subject",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("configuration-blocked claim must not sync knowledge")
        ),
    )
    result = tasks._execute_scheduled_deployment_claim(
        str(claim_id),
        task_id=task_id,
    )

    assert result == {
        "status": "rejected",
        "reason": "configuration_preflight_blocked",
        "claim_id": str(claim_id),
    }
    assert len(admissions) == 1
    assert hasattr(admissions[0]["configuration_preflight"], "is_ready")


def test_scheduled_task_engine_failure_is_finalized_without_celery_retry(
    monkeypatch,
    caplog,
):
    claim_id = uuid.uuid4()
    task_id = f"schedule:{uuid.uuid4()}"
    finalized = []
    signals = []

    class _UseCase:
        def __init__(self, **kwargs):
            pass

        def admit(self, **kwargs):
            return application.ScheduleAdmissionResult(
                "admitted", plan=_plan(claim_id, task_id)
            )

        def finalize(self, **kwargs):
            finalized.append(kwargs["succeeded"])
            return True

    monkeypatch.setattr(tasks, "SessionLocal", _Session)
    monkeypatch.setattr(
        tasks,
        "get_schedule_dispatch_settings",
        lambda: ScheduleDispatchSettings(mode="claim"),
    )
    monkeypatch.setattr(application, "ScheduledDeploymentExecutionUseCase", _UseCase)
    monkeypatch.setattr(
        "apps.workflow_engine.workflow.core.workflow_engine.WorkflowEngine",
        _Engine,
    )
    monkeypatch.setattr(
        tasks,
        "_sync_knowledge_bases_for_execution_subject",
        lambda *a, **k: {"skipped": True},
    )
    monkeypatch.setattr(
        tasks,
        "emit_schedule_dispatch_signal",
        lambda _logger, event, **kwargs: signals.append((event, kwargs)),
    )
    _Engine.error = RuntimeError("provider raw detail")
    try:
        with pytest.raises(NonRetryableWorkflowError):
            tasks._execute_scheduled_deployment_claim(
                str(claim_id),
                task_id=task_id,
            )
    finally:
        _Engine.error = None

    assert finalized == [False]
    assert signals == [
        (
            "schedule_claim_dead_letter_total",
            {
                "status": "dead_lettered",
                "reason": "execution_failed_after_admission",
                "mode": "claim",
            },
        )
    ]
    _assert_logs_redact(caplog, claim_id, "provider raw detail")


def test_scheduled_external_effect_outcome_unknown_uses_existing_claim_reason(
    monkeypatch,
):
    claim_id = uuid.uuid4()
    task_id = f"schedule:{uuid.uuid4()}"
    finalized = []
    signals = []

    class _UseCase:
        def __init__(self, **kwargs):
            pass

        def admit(self, **kwargs):
            return application.ScheduleAdmissionResult(
                "admitted", plan=_plan(claim_id, task_id)
            )

        def finalize(self, **kwargs):
            finalized.append(kwargs["failure_reason"])
            return True

    monkeypatch.setattr(tasks, "SessionLocal", _Session)
    monkeypatch.setattr(
        tasks,
        "get_schedule_dispatch_settings",
        lambda: ScheduleDispatchSettings(mode="claim"),
    )
    monkeypatch.setattr(application, "ScheduledDeploymentExecutionUseCase", _UseCase)
    monkeypatch.setattr(
        "apps.workflow_engine.workflow.core.workflow_engine.WorkflowEngine",
        _Engine,
    )
    monkeypatch.setattr(
        tasks,
        "_sync_knowledge_bases_for_execution_subject",
        lambda *a, **k: {"skipped": True},
    )
    monkeypatch.setattr(
        tasks,
        "emit_schedule_dispatch_signal",
        lambda _logger, event, **kwargs: signals.append((event, kwargs)),
    )
    _Engine.error = ExternalEffectError(
        "external_effect.outcome_unknown",
        retryable=False,
        node_id="http-1",
    )
    try:
        with pytest.raises(
            NonRetryableWorkflowError,
            match="external_effect.outcome_unknown",
        ):
            tasks._execute_scheduled_deployment_claim(
                str(claim_id),
                task_id=task_id,
            )
    finally:
        _Engine.error = None

    assert finalized == ["execution_outcome_unknown"]
    assert signals[0][1]["reason"] == "execution_outcome_unknown"


def test_scheduled_task_emits_terminal_budget_admission_signal(monkeypatch):
    signals = []

    class _UseCase:
        def __init__(self, **kwargs):
            pass

        def admit(self, **kwargs):
            return application.ScheduleAdmissionResult(
                "deferred",
                "budget_evaluation_failed",
                dead_lettered_reason="budget_evaluation_failed",
            )

    monkeypatch.setattr(tasks, "SessionLocal", _Session)
    monkeypatch.setattr(application, "ScheduledDeploymentExecutionUseCase", _UseCase)
    monkeypatch.setattr(
        tasks,
        "emit_schedule_dispatch_signal",
        lambda _logger, event, **kwargs: signals.append((event, kwargs)),
    )

    result = tasks._execute_scheduled_deployment_claim(
        str(uuid.uuid4()),
        task_id=f"schedule:{uuid.uuid4()}",
    )

    assert result["status"] == "deferred"
    assert signals[0][0] == "schedule_claim_dead_letter_total"
    assert signals[0][1]["reason"] == "budget_evaluation_failed"


@pytest.mark.parametrize(
    ("claim_id", "task_id"),
    [("not-a-uuid", "schedule:value"), (str(uuid.uuid4()), "wrong-task")],
)
def test_scheduled_task_rejects_invalid_locator_or_task_identity(claim_id, task_id):
    with pytest.raises(tasks.PermanentDeploymentExecutionError):
        tasks._execute_scheduled_deployment_claim(claim_id, task_id=task_id)


def test_claim_task_registration_has_no_automatic_retry():
    assert tasks.execute_scheduled_deployment.max_retries == 0
    assert tasks.execute_scheduled_deployment.ignore_result is True
    assert tasks.celery_app.conf.task_store_errors_even_if_ignored is False


def test_missing_claim_schema_is_safe_permanent_rejection(monkeypatch, caplog):
    claim_id = uuid.uuid4()

    class _UnavailableUseCase:
        def __init__(self, **kwargs):
            pass

        def admit(self, **kwargs):
            raise RuntimeError("relation details must not escape")

    monkeypatch.setattr(tasks, "SessionLocal", _Session)
    monkeypatch.setattr(
        application,
        "ScheduledDeploymentExecutionUseCase",
        _UnavailableUseCase,
    )

    with pytest.raises(tasks.PermanentDeploymentExecutionError) as exc_info:
        tasks._execute_scheduled_deployment_claim(
            str(claim_id),
            task_id=f"schedule:{uuid.uuid4()}",
        )

    assert str(exc_info.value) == "schedule admission is unavailable"
    _assert_logs_redact(caplog, claim_id, "relation details must not escape")


def test_invalid_schedule_dispatch_settings_are_safe_permanent_rejection(
    monkeypatch,
):
    monkeypatch.setattr(
        tasks,
        "get_schedule_dispatch_settings",
        lambda: (_ for _ in ()).throw(ValueError("invalid environment detail")),
    )

    with pytest.raises(tasks.PermanentDeploymentExecutionError) as exc_info:
        tasks._execute_scheduled_deployment_claim(
            str(uuid.uuid4()),
            task_id=f"schedule:{uuid.uuid4()}",
        )

    assert str(exc_info.value) == "schedule dispatch configuration is invalid"
