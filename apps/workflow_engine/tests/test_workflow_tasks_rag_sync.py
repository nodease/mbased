import uuid
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from apps.workflow_engine import tasks
from apps.shared.db.models.workflow_deployment import DeploymentType
from apps.shared.domain.deployment_runtime_policy import (
    DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
    SURFACE_WEBHOOK_RUN,
)
from apps.shared.domain.public_chat_history import (
    remaining_public_chat_history_tokens,
)
from apps.shared.domain.workflow_node_location import (
    CanonicalWorkflowNodeLocation,
)
from apps.shared.services.public_chat_history_transient_store import (
    PublicChatHistoryTransientStoreError,
)
from apps.workflow_engine.workflow.errors import NonRetryableWorkflowError


class FakeSession:
    deployment = None
    app = None
    workflow = None

    def close(self):
        return None

    def query(self, model):
        return FakeQuery(model)


class FakeQuery:
    def __init__(self, model):
        self.model = model

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        if self.model is FakeWorkflowDeployment and FakeSession.deployment is not None:
            return FakeSession.deployment
        if self.model is FakeApp and FakeSession.app is not None:
            return FakeSession.app
        if self.model is FakeWorkflow and FakeSession.workflow is not None:
            return FakeSession.workflow
        return SimpleNamespace(
            id=uuid.uuid4(),
            app_id=uuid.uuid4(),
            workflow_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            active_deployment_id=uuid.uuid4(),
            is_active=True,
            type=DeploymentType.WEBHOOK,
            graph_data={"nodes": []},
            graph_snapshot={"nodes": []},
            version=1,
        )


class FakeColumn:
    def __eq__(self, _other):
        return True

    def is_(self, _other):
        return True


class FakeApp:
    id = FakeColumn()
    workflow_id = FakeColumn()


class FakeWorkflow:
    id = FakeColumn()
    app_id = FakeColumn()


class FakeWorkflowDeployment:
    id = FakeColumn()
    app_id = FakeColumn()
    is_active = FakeColumn()


def _active_deployment_pair(
    *,
    deployment_id=None,
    deployment_type=DeploymentType.WEBHOOK,
    trigger_mode="webhook",
    graph_snapshot=None,
):
    deployment_id = deployment_id or uuid.uuid4()
    app_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    created_by = uuid.uuid4()
    FakeSession.deployment = SimpleNamespace(
        id=deployment_id,
        app_id=app_id,
        version=7,
        type=deployment_type,
        is_active=True,
        created_by=created_by,
        graph_snapshot=graph_snapshot or {"nodes": []},
        config={},
    )
    FakeSession.app = SimpleNamespace(
        id=app_id,
        workflow_id=workflow_id,
        organization_id=organization_id,
        active_deployment_id=deployment_id,
        created_by=created_by,
    )
    return FakeSession.deployment, FakeSession.app, trigger_mode


class FakeWorkflowEngine:
    calls = []
    execute_error = None

    def __init__(self, *args, **kwargs):
        self.__class__.calls.append({"args": args, "kwargs": kwargs})
        self.execution_context = kwargs.get("execution_context", {})

    def execute(self):
        if self.execute_error is not None:
            raise self.execute_error
        return {"ok": True}

    def execute_stream(self):
        yield {"type": "workflow_finish", "data": {"ok": True}}

    def cleanup(self):
        return None


class FakeSyncService:
    calls = []

    def __init__(self, db, user_id, organization_id=None):
        self.db = db
        self.user_id = user_id
        self.organization_id = organization_id

    def sync_knowledge_bases(self, graph):
        self.__class__.calls.append(
            {
                "user_id": self.user_id,
                "organization_id": self.organization_id,
                "graph": graph,
            }
        )
        return {"synced_count": 1, "failed": []}


@pytest.fixture(autouse=True)
def patch_task_dependencies(monkeypatch):
    FakeSyncService.calls = []
    FakeWorkflowEngine.calls = []
    FakeWorkflowEngine.execute_error = None
    FakeSession.deployment = None
    FakeSession.app = None
    app_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    deployment_id = uuid.uuid4()
    FakeSession.workflow = SimpleNamespace(
        id=workflow_id,
        app_id=app_id,
        organization_id=organization_id,
    )
    FakeSession.app = SimpleNamespace(
        id=app_id,
        workflow_id=workflow_id,
        organization_id=organization_id,
        active_deployment_id=deployment_id,
        created_by=uuid.uuid4(),
    )
    FakeSession.deployment = SimpleNamespace(
        id=deployment_id,
        app_id=app_id,
        version=1,
        type=DeploymentType.WEBHOOK,
        is_active=True,
        created_by=FakeSession.app.created_by,
        graph_snapshot={"nodes": []},
        config={},
    )
    monkeypatch.setattr(
        tasks, "consume_public_chat_history", lambda _ref, **_kwargs: ()
    )
    monkeypatch.setattr(tasks, "SessionLocal", lambda: FakeSession())
    monkeypatch.setitem(
        sys.modules,
        "apps.workflow_engine.workflow.core.workflow_engine",
        SimpleNamespace(WorkflowEngine=FakeWorkflowEngine),
    )
    monkeypatch.setitem(
        sys.modules,
        "apps.workflow_engine.services.sync_service",
        SimpleNamespace(SyncService=FakeSyncService),
    )
    monkeypatch.setitem(
        sys.modules,
        "apps.shared.db.models.app",
        SimpleNamespace(App=FakeApp),
    )
    monkeypatch.setitem(
        sys.modules,
        "apps.shared.db.models.workflow_deployment",
        SimpleNamespace(
            DeploymentType=DeploymentType,
            WorkflowDeployment=FakeWorkflowDeployment,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "apps.shared.db.models.workflow",
        SimpleNamespace(Workflow=FakeWorkflow),
    )
    monkeypatch.setitem(
        sys.modules,
        "apps.shared.pubsub",
        SimpleNamespace(publish_workflow_event=lambda *_args, **_kwargs: None),
    )


def test_execute_workflow_skips_sync_without_execution_subject():
    owner_id = uuid.uuid4()
    result = tasks.execute_workflow.run(
        {"nodes": []},
        {},
        {
            "user_id": str(owner_id),
            "workflow_id": str(FakeSession.workflow.id),
            "organization_id": str(uuid.uuid4()),
            "execution_id": str(uuid.uuid4()),
        },
        False,
    )

    assert result["status"] == "success"
    assert result["sync_status"] == {
        "synced_count": 0,
        "failed": [],
        "skipped": True,
        "reason": "anonymous_public_only",
    }
    assert FakeSyncService.calls == []


def test_deployed_graph_execution_revalidates_exact_snapshot():
    graph = {"nodes": [], "edges": []}
    FakeSession.deployment.graph_snapshot = graph

    result = tasks.execute_workflow.run(
        graph,
        {},
        {
            "workflow_id": str(FakeSession.workflow.id),
            "execution_id": str(uuid.uuid4()),
            "deployment_id": str(FakeSession.deployment.id),
            "workflow_version": FakeSession.deployment.version,
        },
        True,
    )

    context = FakeWorkflowEngine.calls[0]["kwargs"]["execution_context"]
    assert result["status"] == "success"
    assert context["deployment_id"] == str(FakeSession.deployment.id)
    assert context["workflow_version"] == FakeSession.deployment.version


def test_deployed_public_chatbot_rebuilds_consumer_mapping_from_snapshot(
    monkeypatch,
):
    lifecycle_events = []
    graph = {
        "nodes": [
            {
                "id": "classifier",
                "type": "llmNode",
                "data": {"model_id": "model-1", "user_prompt": "분류하세요."},
            },
            {
                "id": "answer",
                "type": "llmNode",
                "data": {"model_id": "model-1", "user_prompt": "질문에 답변하세요."},
            },
        ],
        "edges": [],
    }
    FakeSession.deployment.type = DeploymentType.CHATBOT
    FakeSession.deployment.graph_snapshot = graph
    FakeSession.deployment.config = {
        "public_conversation": {
            "contract_version": "public_chat_conversation.v1",
            "history_consumer": {
                "node_id": "answer",
                "container_path": [],
            },
        }
    }
    forged_ref = CanonicalWorkflowNodeLocation((), "classifier").safe_reference
    consume_timeouts = []
    monkeypatch.setattr(
        tasks,
        "_sync_knowledge_bases_for_execution_subject",
        lambda *_args, **_kwargs: (
            lifecycle_events.append("validated"),
            {"synced_count": 0, "failed": []},
        )[1],
    )
    monkeypatch.setattr(
        tasks,
        "consume_public_chat_history",
        lambda _ref, *, timeout_seconds: (
            lifecycle_events.append("consumed"),
            consume_timeouts.append(timeout_seconds),
            (),
        )[2],
    )

    result = tasks.execute_public_chat_workflow.run(
        graph,
        {"question": "current"},
        {
            "workflow_id": str(FakeSession.workflow.id),
            "execution_id": str(uuid.uuid4()),
            "deployment_id": str(FakeSession.deployment.id),
            "workflow_version": FakeSession.deployment.version,
            "trigger_mode": "app",
            "public_chat_history_ref": "b" * 32,
            "public_chat_history_consumer_ref": forged_ref,
            "execution_actor": {"type": "public"},
            "suppress_content_persistence": True,
            "public_chat_history_token_budget": 4096,
            "memory_mode": False,
            "conversation_id": None,
            "public_request_deadline_at": (
                datetime.now(timezone.utc) + timedelta(minutes=1)
            ).isoformat(),
        },
        True,
    )

    context = FakeWorkflowEngine.calls[0]["kwargs"]["execution_context"]
    assert result["status"] == "success"
    assert context["public_chat_history_consumer_ref"] == (
        CanonicalWorkflowNodeLocation((), "answer").safe_reference
    )
    assert context["execution_actor"] == {"type": "public"}
    assert "public_chat_history_ref" not in context
    assert context["public_chat_history_token_budget"] == (
        remaining_public_chat_history_tokens({"question": "current"})
    )
    assert "memory_mode" not in context
    assert "conversation_id" not in context
    assert lifecycle_events == ["validated", "consumed"]
    assert 0 < consume_timeouts[0] <= 2.0


def test_deployed_graph_execution_rejects_snapshot_drift():
    FakeSession.deployment.graph_snapshot = {"nodes": [], "edges": []}

    with pytest.raises(
        tasks.PermanentDeploymentExecutionError,
        match="identity is not frozen",
    ):
        tasks.execute_workflow.run(
            {"nodes": [{"id": "changed"}], "edges": []},
            {},
            {
                "workflow_id": str(FakeSession.workflow.id),
                "execution_id": str(uuid.uuid4()),
                "deployment_id": str(FakeSession.deployment.id),
                "workflow_version": FakeSession.deployment.version,
            },
            True,
        )

    assert FakeWorkflowEngine.calls == []


def test_public_chatbot_execution_rejects_expired_queue_payload_before_db_access(
    monkeypatch,
):
    monkeypatch.setattr(
        tasks,
        "SessionLocal",
        lambda: pytest.fail("expired public payload must not access the database"),
    )

    with pytest.raises(
        NonRetryableWorkflowError,
        match="conversation.request_expired",
    ):
        tasks.execute_public_chat_workflow.run(
            {"nodes": []},
            {},
            {
                "public_chat_history_ref": "c" * 32,
                "trigger_mode": "app",
                "execution_actor": {"type": "public"},
                "suppress_content_persistence": True,
                "public_request_deadline_at": (
                    datetime.now(timezone.utc) - timedelta(seconds=1)
                ).isoformat(),
            },
            True,
        )

    assert FakeWorkflowEngine.calls == []
    assert FakeSyncService.calls == []


@pytest.mark.parametrize(
    "queued_history",
    [
        {"public_chat_history": [{"role": "user", "content": "raw"}]},
        {
            "public_chat_history_ref": "c" * 32,
            "public_chat_history": [{"role": "user", "content": "raw"}],
        },
    ],
)
def test_public_chatbot_execution_rejects_raw_history_before_db_access(
    monkeypatch,
    queued_history,
):
    monkeypatch.setattr(
        tasks,
        "SessionLocal",
        lambda: pytest.fail("raw public history must be rejected before database access"),
    )

    with pytest.raises(
        NonRetryableWorkflowError,
        match="conversation.history_payload_forbidden",
    ):
        tasks.execute_public_chat_workflow.run(
            {"nodes": []},
            {},
            {
                **queued_history,
                "trigger_mode": "app",
                "execution_actor": {"type": "public"},
                "suppress_content_persistence": True,
                "public_chat_stateless_compatibility": (
                    "public_chat_history_ref" not in queued_history
                ),
                "public_request_deadline_at": (
                    datetime.now(timezone.utc) + timedelta(minutes=1)
                ).isoformat(),
            },
            True,
        )

    assert FakeWorkflowEngine.calls == []
    assert FakeSyncService.calls == []


@pytest.mark.parametrize(
    "legacy_controls",
    [
        {"memory_mode": True, "conversation_id": None},
        {"memory_mode": False, "conversation_id": "forged-public-session"},
    ],
)
def test_public_chatbot_task_rejects_unsafe_legacy_memory_controls_before_db(
    monkeypatch,
    legacy_controls,
):
    monkeypatch.setattr(
        tasks,
        "SessionLocal",
        lambda: pytest.fail(
            "unsafe public memory controls must be rejected before database access"
        ),
    )

    with pytest.raises(
        NonRetryableWorkflowError,
        match="conversation.task_contract_mismatch",
    ):
        tasks.execute_public_chat_workflow.run(
            {"nodes": []},
            {},
            {
                "public_chat_stateless_compatibility": True,
                "trigger_mode": "app",
                "execution_actor": {"type": "public"},
                "suppress_content_persistence": True,
                "public_request_deadline_at": (
                    datetime.now(timezone.utc) + timedelta(minutes=1)
                ).isoformat(),
                **legacy_controls,
            },
            True,
        )

    assert FakeWorkflowEngine.calls == []


def test_public_history_consume_timeout_is_capped_by_remaining_task_deadline(
    monkeypatch,
):
    monkeypatch.setattr(tasks.time, "monotonic", lambda: 50.0)

    assert tasks._public_history_consume_timeout_seconds(55.0) == 2.0
    assert tasks._public_history_consume_timeout_seconds(50.125) == pytest.approx(
        0.125
    )

    with pytest.raises(
        NonRetryableWorkflowError,
        match="conversation.request_expired",
    ):
        tasks._public_history_consume_timeout_seconds(50.0)


def test_public_task_deadline_never_exceeds_absolute_request_deadline():
    current = datetime(2026, 1, 1, tzinfo=timezone.utc)

    deadline = tasks._workflow_task_deadline(
        current + timedelta(seconds=1),
        now=current,
        monotonic_now=50.0,
    )

    assert deadline == pytest.approx(51.0)


def test_public_chatbot_does_not_retry_after_one_time_history_is_consumed(
    monkeypatch,
):
    graph = {
        "nodes": [
            {
                "id": "answer",
                "type": "llmNode",
                "data": {"model_id": "model-1", "user_prompt": "질문에 답변하세요."},
            }
        ],
        "edges": [],
    }
    FakeSession.deployment.type = DeploymentType.CHATBOT
    FakeSession.deployment.graph_snapshot = graph
    FakeSession.deployment.config = {
        "public_conversation": {
            "contract_version": "public_chat_conversation.v1",
            "history_consumer": {
                "node_id": "answer",
                "container_path": [],
            },
        }
    }
    consumed = []
    monkeypatch.setattr(
        tasks,
        "consume_public_chat_history",
        lambda reference, **_kwargs: (
            consumed.append(reference),
            (
                {"role": "user", "content": "이전 질문"},
                {"role": "assistant", "content": "이전 답변"},
            ),
        )[1],
    )
    monkeypatch.setattr(
        tasks,
        "_safe_retry",
        lambda *_args, **_kwargs: pytest.fail(
            "consumed one-time history must not schedule a Celery retry"
        ),
    )
    FakeWorkflowEngine.execute_error = RuntimeError("provider unavailable")

    with pytest.raises(
        NonRetryableWorkflowError,
        match="conversation.history_replay_required",
    ):
        tasks.execute_public_chat_workflow.run(
            graph,
            {},
            {
                "workflow_id": str(FakeSession.workflow.id),
                "execution_id": str(uuid.uuid4()),
                "deployment_id": str(FakeSession.deployment.id),
                "workflow_version": FakeSession.deployment.version,
                "trigger_mode": "app",
                "public_chat_history_ref": "d" * 32,
                "execution_actor": {"type": "public"},
                "suppress_content_persistence": True,
                "public_request_deadline_at": (
                    datetime.now(timezone.utc) + timedelta(minutes=1)
                ).isoformat(),
            },
            True,
        )

    assert consumed == ["d" * 32]


def test_public_chatbot_retries_when_redis_is_unavailable_before_consume(
    monkeypatch,
):
    graph = {
        "nodes": [
            {
                "id": "answer",
                "type": "llmNode",
                "data": {"model_id": "model-1", "user_prompt": "answer"},
            }
        ],
        "edges": [],
    }
    FakeSession.deployment.type = DeploymentType.CHATBOT
    FakeSession.deployment.graph_snapshot = graph
    FakeSession.deployment.config = {
        "public_conversation": {
            "contract_version": "public_chat_conversation.v1",
            "history_consumer": {
                "node_id": "answer",
                "container_path": [],
            },
        }
    }
    retry_errors = []

    class _RetryScheduled(Exception):
        pass

    def unavailable(_reference, **_kwargs):
        raise PublicChatHistoryTransientStoreError(
            "conversation.history_store_unavailable"
        )

    def schedule_retry(_task, error):
        retry_errors.append(error)
        raise _RetryScheduled()

    monkeypatch.setattr(tasks, "consume_public_chat_history", unavailable)
    monkeypatch.setattr(tasks, "_safe_retry", schedule_retry)

    with pytest.raises(_RetryScheduled):
        tasks.execute_public_chat_workflow.run(
            graph,
            {},
            {
                "workflow_id": str(FakeSession.workflow.id),
                "execution_id": str(uuid.uuid4()),
                "deployment_id": str(FakeSession.deployment.id),
                "workflow_version": FakeSession.deployment.version,
                "trigger_mode": "app",
                "public_chat_history_ref": "e" * 32,
                "execution_actor": {"type": "public"},
                "suppress_content_persistence": True,
                "public_request_deadline_at": (
                    datetime.now(timezone.utc) + timedelta(minutes=1)
                ).isoformat(),
            },
            True,
        )

    assert [error.code for error in retry_errors] == [
        "conversation.history_store_unavailable"
    ]
    assert FakeWorkflowEngine.calls == []


def test_generic_workflow_task_rejects_public_context_before_database_access(
    monkeypatch,
):
    monkeypatch.setattr(
        tasks,
        "SessionLocal",
        lambda: pytest.fail("misrouted public task must not access the database"),
    )

    with pytest.raises(
        NonRetryableWorkflowError,
        match="conversation.task_contract_mismatch",
    ):
        tasks.execute_workflow.run(
            {"nodes": []},
            {},
            {
                "public_chat_history_ref": "f" * 32,
                "trigger_mode": "app",
                "execution_actor": {"type": "public"},
                "suppress_content_persistence": True,
                "public_request_deadline_at": (
                    datetime.now(timezone.utc) + timedelta(minutes=1)
                ).isoformat(),
            },
            True,
        )

    assert FakeWorkflowEngine.calls == []


def test_execute_workflow_rejects_invalid_execution_identity_without_retry():
    with pytest.raises(
        tasks.PermanentDeploymentExecutionError,
        match="identity is invalid",
    ):
        tasks.execute_workflow.run(
            {"nodes": []},
            {},
            {
                "workflow_id": str(FakeSession.workflow.id),
                "execution_id": "not-a-uuid",
            },
            False,
        )

    assert FakeWorkflowEngine.calls == []


@pytest.mark.parametrize("task_name", ["execute_workflow", "stream_workflow"])
def test_draft_execution_rejects_unresolved_external_action_before_engine(task_name):
    graph = {
        "nodes": [
            {
                "id": "slack-1",
                "type": "slackPostNode",
                "data": {"configuration_state": "resolved"},
            }
        ],
        "edges": [],
    }
    execution_context = {
        "workflow_id": str(FakeSession.workflow.id),
        "execution_id": str(uuid.uuid4()),
    }

    with pytest.raises(
        NonRetryableWorkflowError, match="workflow_configuration_unresolved"
    ):
        if task_name == "execute_workflow":
            tasks.execute_workflow.run(graph, {}, execution_context, False)
        else:
            tasks.stream_workflow.run(graph, {}, execution_context, "run-1")

    assert FakeWorkflowEngine.calls == []


def test_execute_workflow_syncs_with_execution_subject_not_actor_user():
    actor_id = uuid.uuid4()
    subject_id = uuid.uuid4()
    organization_id = uuid.uuid4()

    result = tasks.execute_workflow.run(
        {"nodes": []},
        {},
        {
            "user_id": str(actor_id),
            "workflow_id": str(FakeSession.workflow.id),
            "organization_id": str(organization_id),
            "execution_id": str(uuid.uuid4()),
            "execution_subject": {
                "type": "user",
                "id": str(subject_id),
            },
        },
        False,
    )

    assert result["sync_status"] == {"synced_count": 1, "failed": []}
    assert FakeSyncService.calls == [
        {
            "user_id": subject_id,
            "organization_id": str(FakeSession.workflow.organization_id),
            "graph": {"nodes": []},
        }
    ]


def test_execute_workflow_skips_sync_for_invalid_subject():
    result = tasks.execute_workflow.run(
        {"nodes": []},
        {},
        {
            "user_id": str(uuid.uuid4()),
            "workflow_id": str(FakeSession.workflow.id),
            "organization_id": str(uuid.uuid4()),
            "execution_id": str(uuid.uuid4()),
            "execution_subject": {
                "type": "service_account",
                "id": str(uuid.uuid4()),
            },
        },
        False,
    )

    assert result["status"] == "success"
    assert result["sync_status"]["skipped"] is True
    assert result["sync_status"]["reason"] == "anonymous_public_only"
    assert FakeSyncService.calls == []


def test_stream_workflow_uses_execution_subject_for_sync():
    subject_id = uuid.uuid4()
    organization_id = uuid.uuid4()

    result = tasks.stream_workflow.run(
        {"nodes": []},
        {},
        {
            "user_id": str(uuid.uuid4()),
            "workflow_id": str(FakeSession.workflow.id),
            "organization_id": str(organization_id),
            "execution_id": str(uuid.uuid4()),
            "execution_subject": {
                "subject_type": "user",
                "subject_id": str(subject_id),
            },
        },
        str(uuid.uuid4()),
    )

    assert result["status"] == "success"
    assert FakeSyncService.calls == [
        {
            "user_id": subject_id,
            "organization_id": str(FakeSession.workflow.organization_id),
            "graph": {"nodes": []},
        }
    ]


def test_stream_workflow_does_not_retry_permanent_identity_error(monkeypatch):
    def reject_context(*_args, **_kwargs):
        raise tasks.PermanentDeploymentExecutionError("invalid workflow identity")

    monkeypatch.setattr(tasks, "_canonical_workflow_execution_context", reject_context)
    monkeypatch.setitem(
        sys.modules,
        "apps.shared.pubsub",
        SimpleNamespace(publish_workflow_event=lambda *_args, **_kwargs: None),
    )

    with pytest.raises(tasks.PermanentDeploymentExecutionError):
        tasks.stream_workflow.run(
            {"nodes": []},
            {},
            {"workflow_id": str(uuid.uuid4())},
            str(uuid.uuid4()),
        )


def test_stream_workflow_publishes_error_when_external_effect_retry_is_exhausted(
    monkeypatch,
):
    external_run_id = str(uuid.uuid4())
    published = []

    def retrying_stream(_self):
        if False:
            yield None
        raise tasks.ExternalEffectRetrySignal(
            "external_effect.retry_allowed",
            node_id="http-1",
            terminal_code="external_effect.connection_failed",
        )

    def exhausted_retry(_task, _error):
        raise RuntimeError("retry exhausted")

    monkeypatch.setattr(FakeWorkflowEngine, "execute_stream", retrying_stream)
    monkeypatch.setattr(tasks, "_safe_retry", exhausted_retry)
    monkeypatch.setitem(
        sys.modules,
        "apps.shared.pubsub",
        SimpleNamespace(
            publish_workflow_event=lambda *args: published.append(args),
        ),
    )

    with pytest.raises(RuntimeError, match="retry exhausted"):
        tasks.stream_workflow.run(
            {"nodes": []},
            {},
            {
                "user_id": str(uuid.uuid4()),
                "workflow_id": str(FakeSession.workflow.id),
                "organization_id": str(uuid.uuid4()),
                "execution_id": str(uuid.uuid4()),
            },
            external_run_id,
        )

    assert published == [
        (
            external_run_id,
            "error",
            {
                "code": "external_effect.connection_failed",
                "message": "external_effect.connection_failed",
                "retryable": False,
                "node_id": "http-1",
            },
        )
    ]


def test_stream_workflow_does_not_publish_terminal_error_while_retry_is_scheduled(
    monkeypatch,
):
    published = []

    def retrying_stream(_self):
        if False:
            yield None
        raise tasks.ExternalEffectRetrySignal("external_effect.claim_wait")

    def scheduled_retry(_task, _error):
        raise tasks.Retry()

    monkeypatch.setattr(FakeWorkflowEngine, "execute_stream", retrying_stream)
    monkeypatch.setattr(tasks, "_safe_retry", scheduled_retry)
    monkeypatch.setitem(
        sys.modules,
        "apps.shared.pubsub",
        SimpleNamespace(
            publish_workflow_event=lambda *args: published.append(args),
        ),
    )

    with pytest.raises(tasks.Retry):
        tasks.stream_workflow.run(
            {"nodes": []},
            {},
            {
                "user_id": str(uuid.uuid4()),
                "workflow_id": str(FakeSession.workflow.id),
                "organization_id": str(uuid.uuid4()),
                "execution_id": str(uuid.uuid4()),
            },
            str(uuid.uuid4()),
        )

    assert published == []


def test_execute_deployed_workflow_skips_sync_without_execution_subject():
    result = tasks.execute_deployed_workflow.run(
        str(uuid.uuid4()),
        {},
        {
            "user_id": str(uuid.uuid4()),
            "organization_id": str(uuid.uuid4()),
        },
    )

    assert result["status"] == "success"
    assert result["sync_status"] == {
        "synced_count": 0,
        "failed": [],
        "skipped": True,
        "reason": "anonymous_public_only",
    }
    assert FakeSyncService.calls == []


def test_legacy_deployed_effect_requires_frozen_deployment_envelope():
    deployment, app, _trigger_mode = _active_deployment_pair(
        graph_snapshot={
            "nodes": [
                {
                    "id": "http-1",
                    "type": "httpRequestNode",
                    "data": {
                        "method": "POST",
                        "url": "https://example.test/hook",
                    },
                }
            ],
            "edges": [],
        }
    )

    with pytest.raises(
        tasks.PermanentDeploymentExecutionError,
        match="identity is not frozen",
    ):
        tasks.execute_deployed_workflow.run(
            str(app.workflow_id),
            {},
            {
                "workflow_id": str(app.workflow_id),
                "execution_id": str(uuid.uuid4()),
            },
        )

    assert FakeWorkflowEngine.calls == []


def test_legacy_deployed_effect_accepts_exact_snapshot_envelope():
    from apps.shared.domain.workflow_node_binding import canonical_snapshot_sha256

    deployment, app, _trigger_mode = _active_deployment_pair(
        graph_snapshot={
            "nodes": [
                {
                    "id": "http-1",
                    "type": "httpRequestNode",
                    "data": {
                        "method": "POST",
                        "url": "https://example.test/hook",
                    },
                }
            ],
            "edges": [],
        }
    )
    snapshot_sha256 = canonical_snapshot_sha256(deployment.graph_snapshot)

    result = tasks.execute_deployed_workflow.run(
        str(app.workflow_id),
        {},
        {
            "workflow_id": str(app.workflow_id),
            "execution_id": str(uuid.uuid4()),
            "deployment_id": str(deployment.id),
            "deployment_version": deployment.version,
            "snapshot_sha256": snapshot_sha256,
        },
    )

    context = FakeWorkflowEngine.calls[0]["kwargs"]["execution_context"]
    assert result["status"] == "success"
    assert context["deployment_version"] == deployment.version
    assert context["snapshot_sha256"] == snapshot_sha256
    assert context["workflow_version"] == deployment.version


def test_legacy_deployed_redelivery_uses_frozen_deployment_after_new_activation(
    monkeypatch,
):
    from apps.shared.domain.workflow_node_binding import canonical_snapshot_sha256

    class _NamedColumn:
        def __init__(self, name):
            self.name = name

        def __eq__(self, value):
            return ("eq", self.name, value)

        def is_(self, value):
            return ("is", self.name, value)

    monkeypatch.setattr(FakeApp, "workflow_id", _NamedColumn("workflow_id"))
    monkeypatch.setattr(FakeWorkflowDeployment, "id", _NamedColumn("id"))
    monkeypatch.setattr(FakeWorkflowDeployment, "app_id", _NamedColumn("app_id"))
    monkeypatch.setattr(
        FakeWorkflowDeployment,
        "is_active",
        _NamedColumn("is_active"),
    )
    app_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    d1_graph = {
        "nodes": [
            {
                "id": "http-1",
                "type": "httpRequestNode",
                "data": {
                    "method": "POST",
                    "url": "https://example.test/hook",
                },
            }
        ],
        "edges": [],
    }
    d1 = SimpleNamespace(
        id=uuid.uuid4(),
        app_id=app_id,
        version=1,
        is_active=False,
        created_by=uuid.uuid4(),
        graph_snapshot=d1_graph,
    )
    d2 = SimpleNamespace(
        id=uuid.uuid4(),
        app_id=app_id,
        version=2,
        is_active=True,
        created_by=d1.created_by,
        graph_snapshot={"nodes": [], "edges": []},
    )
    app = SimpleNamespace(
        id=app_id,
        workflow_id=workflow_id,
        organization_id=uuid.uuid4(),
        active_deployment_id=d2.id,
        created_by=d1.created_by,
    )

    class _Query:
        def __init__(self, model):
            self.model = model
            self.criteria = ()

        def filter(self, *criteria):
            self.criteria = criteria
            return self

        def first(self):
            if self.model is FakeApp:
                return app
            if self.model is FakeWorkflowDeployment:
                requested_id = next(
                    (value for _operator, name, value in self.criteria if name == "id"),
                    None,
                )
                return {d1.id: d1, d2.id: d2}.get(requested_id)
            return None

    class _Session:
        def query(self, model):
            return _Query(model)

        def close(self):
            pass

    monkeypatch.setattr(tasks, "SessionLocal", _Session)

    result = tasks.execute_deployed_workflow.run(
        str(workflow_id),
        {},
        {
            "workflow_id": str(workflow_id),
            "execution_id": str(uuid.uuid4()),
            "deployment_id": str(d1.id),
            "deployment_version": d1.version,
            "snapshot_sha256": canonical_snapshot_sha256(d1_graph),
        },
    )

    context = FakeWorkflowEngine.calls[0]["kwargs"]["execution_context"]
    assert result["status"] == "success"
    assert context["deployment_id"] == str(d1.id)
    assert context["deployment_version"] == d1.version


def test_execute_by_deployment_skips_sync_without_execution_subject():
    deployment, _app, trigger_mode = _active_deployment_pair()

    result = tasks.execute_by_deployment.run(
        str(deployment.id),
        {},
        {
            "user_id": str(uuid.uuid4()),
            "organization_id": str(uuid.uuid4()),
            "trigger_mode": trigger_mode,
            "execution_id": str(uuid.uuid4()),
        },
    )

    assert result["status"] == "success"
    assert result["sync_status"] == {
        "synced_count": 0,
        "failed": [],
        "skipped": True,
        "reason": "anonymous_public_only",
    }
    assert FakeSyncService.calls == []


def test_deployed_execution_rejects_unresolved_external_action_before_engine():
    graph_snapshot = {
        "nodes": [
            {
                "id": "slack-1",
                "type": "slackPostNode",
                "data": {"configuration_state": "resolved"},
            }
        ],
        "edges": [],
    }
    deployment, _app, trigger_mode = _active_deployment_pair(
        graph_snapshot=graph_snapshot
    )

    with pytest.raises(
        NonRetryableWorkflowError, match="workflow_configuration_unresolved"
    ):
        tasks.execute_by_deployment.run(
            str(deployment.id),
            {},
            {
                "trigger_mode": trigger_mode,
                "execution_id": str(uuid.uuid4()),
            },
        )

    assert FakeWorkflowEngine.calls == []


def test_execute_by_deployment_uses_snapshot_rag_selection():
    deployment_id = uuid.uuid4()
    knowledge_base_id = str(uuid.uuid4())
    graph_snapshot = {
        "nodes": [
            {
                "id": "llm-1",
                "type": "llmNode",
                "data": {
                    "title": "LLM",
                    "provider": "openai",
                    "model_id": "gpt-4o",
                    "user_prompt": "query",
                    "knowledgeBases": [{"id": knowledge_base_id, "name": "제품 정책"}],
                    "topK": 4,
                },
            }
        ],
        "edges": [],
    }
    deployment, app, trigger_mode = _active_deployment_pair(
        deployment_id=deployment_id,
        graph_snapshot=graph_snapshot,
    )

    result = tasks.execute_by_deployment.run(
        str(deployment_id),
        {"message": "hello"},
        {"trigger_mode": trigger_mode, "execution_id": str(uuid.uuid4())},
    )

    engine_kwargs = FakeWorkflowEngine.calls[0]["kwargs"]
    assert result["status"] == "success"
    assert engine_kwargs["graph"]["nodes"][0]["data"]["knowledgeBases"] == [
        {"id": knowledge_base_id, "name": "제품 정책"}
    ]
    assert engine_kwargs["graph"]["nodes"][0]["data"]["topK"] == 4
    assert engine_kwargs["execution_context"]["workflow_id"] == str(app.workflow_id)
    assert engine_kwargs["execution_context"]["organization_id"] == str(
        app.organization_id
    )
    assert engine_kwargs["execution_context"]["deployment_id"] == str(deployment_id)


def test_execute_by_deployment_rebuilds_tenant_context_from_database():
    deployment, app, trigger_mode = _active_deployment_pair()
    attacker_workflow_id = str(uuid.uuid4())
    attacker_organization_id = str(uuid.uuid4())
    attacker_app_id = str(uuid.uuid4())
    attacker_user_id = str(uuid.uuid4())

    result = tasks.execute_by_deployment.run(
        str(deployment.id),
        {},
        {
            "trigger_mode": trigger_mode,
            "execution_id": str(uuid.uuid4()),
            "workflow_id": attacker_workflow_id,
            "organization_id": attacker_organization_id,
            "app_id": attacker_app_id,
            "user_id": attacker_user_id,
            "deployment_id": str(uuid.uuid4()),
            "workflow_version": 999,
            "execution_subject": {
                "subject_type": "user",
                "subject_id": str(uuid.uuid4()),
            },
            "request_id": "request-1",
        },
    )

    context = FakeWorkflowEngine.calls[0]["kwargs"]["execution_context"]
    assert result["status"] == "success"
    assert context["workflow_id"] == str(app.workflow_id)
    assert context["organization_id"] == str(app.organization_id)
    assert context["app_id"] == str(deployment.app_id)
    assert context["deployment_id"] == str(deployment.id)
    assert context["workflow_version"] == deployment.version
    assert context["user_id"] == str(deployment.created_by)
    assert context["request_id"] == "request-1"
    assert "execution_subject" not in context
    assert attacker_workflow_id not in context.values()
    assert attacker_organization_id not in context.values()
    assert attacker_app_id not in context.values()
    assert attacker_user_id not in context.values()


def test_execute_by_deployment_rejects_inactive_deployment():
    deployment, _app, trigger_mode = _active_deployment_pair()
    deployment.is_active = False

    with pytest.raises(tasks.PermanentDeploymentExecutionError):
        tasks.execute_by_deployment.run(
            str(deployment.id),
            {},
            {"trigger_mode": trigger_mode, "execution_id": str(uuid.uuid4())},
        )

    assert FakeWorkflowEngine.calls == []


def test_execute_by_deployment_rejects_deleted_deployment_without_retry():
    FakeSession.deployment = False

    with pytest.raises(tasks.PermanentDeploymentExecutionError):
        tasks.execute_by_deployment.run(
            str(uuid.uuid4()),
            {},
            {"trigger_mode": "schedule"},
        )

    assert FakeWorkflowEngine.calls == []


def test_execute_by_deployment_rejects_missing_graph_without_retry():
    deployment, _app, trigger_mode = _active_deployment_pair(
        deployment_type=DeploymentType.SCHEDULE,
        trigger_mode="schedule",
    )
    deployment.graph_snapshot = None

    with pytest.raises(tasks.PermanentDeploymentExecutionError):
        tasks.execute_by_deployment.run(
            str(deployment.id),
            {},
            {"trigger_mode": trigger_mode, "execution_id": str(uuid.uuid4())},
        )

    assert FakeWorkflowEngine.calls == []


def test_execute_by_deployment_rejects_stale_active_pointer():
    deployment, app, trigger_mode = _active_deployment_pair()
    app.active_deployment_id = uuid.uuid4()

    with pytest.raises(tasks.PermanentDeploymentExecutionError):
        tasks.execute_by_deployment.run(
            str(deployment.id),
            {},
            {"trigger_mode": trigger_mode},
        )

    assert FakeWorkflowEngine.calls == []


def test_execute_by_deployment_rejects_trigger_type_mismatch():
    deployment, _app, _trigger_mode = _active_deployment_pair(
        deployment_type=DeploymentType.WEBHOOK,
    )

    with pytest.raises(tasks.PermanentDeploymentExecutionError):
        tasks.execute_by_deployment.run(
            str(deployment.id),
            {},
            {"trigger_mode": "schedule"},
        )

    assert FakeWorkflowEngine.calls == []


def test_claim_mode_rejects_legacy_generic_schedule_task(monkeypatch):
    deployment, _app, _trigger_mode = _active_deployment_pair(
        deployment_type=DeploymentType.SCHEDULE,
        trigger_mode="schedule",
    )
    monkeypatch.setattr(
        tasks,
        "get_schedule_dispatch_settings",
        lambda: SimpleNamespace(mode="claim"),
    )

    with pytest.raises(tasks.PermanentDeploymentExecutionError):
        tasks.execute_by_deployment.run(
            str(deployment.id),
            {},
            {"trigger_mode": "schedule"},
        )

    assert FakeWorkflowEngine.calls == []


def test_execute_by_deployment_uses_worker_runtime_policy_provider(monkeypatch):
    deployment, _app, trigger_mode = _active_deployment_pair()
    injected_policy = DEFAULT_DEPLOYMENT_RUNTIME_POLICY.with_surface_allowed_types(
        SURFACE_WEBHOOK_RUN,
        set(),
    )
    monkeypatch.setattr(
        tasks,
        "get_deployment_runtime_policy",
        lambda: injected_policy,
    )

    with pytest.raises(tasks.PermanentDeploymentExecutionError):
        tasks.execute_by_deployment.run(
            str(deployment.id),
            {},
            {"trigger_mode": trigger_mode},
        )

    assert FakeWorkflowEngine.calls == []


def test_execute_by_deployment_rejects_workflow_node_deployment():
    deployment, _app, trigger_mode = _active_deployment_pair(
        deployment_type=DeploymentType.WORKFLOW_NODE,
    )

    with pytest.raises(tasks.PermanentDeploymentExecutionError):
        tasks.execute_by_deployment.run(
            str(deployment.id),
            {},
            {"trigger_mode": trigger_mode},
        )

    assert FakeWorkflowEngine.calls == []


def test_execute_by_deployment_does_not_retry_non_retryable_runtime_error():
    deployment, _app, trigger_mode = _active_deployment_pair()
    FakeWorkflowEngine.execute_error = NonRetryableWorkflowError(
        "Recursive workflow-node reference detected"
    )

    with pytest.raises(NonRetryableWorkflowError):
        tasks.execute_by_deployment.run(
            str(deployment.id),
            {},
            {"trigger_mode": trigger_mode, "execution_id": str(uuid.uuid4())},
        )

    assert len(FakeWorkflowEngine.calls) == 1
