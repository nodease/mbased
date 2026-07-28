"""Public Chatbot client-held history와 authenticated internal Memory 경계 테스트.

docs/features/conversation-memory/test_cases.md:
- 공개 챗봇은 완료된 client history만 transient execution context로 전달한다.
- 공개 챗봇은 legacy memory_mode/conversation_id와 durable content logging을 사용하지 않는다.
- 인증형 내부 챗봇은 별도 subject-bound server Memory 계약을 유지한다.
"""

import asyncio
import logging
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.sql.operators import eq

from apps.shared.db.models.app import App
from apps.shared.db.models.workflow_deployment import DeploymentType, WorkflowDeployment
from apps.shared.domain.deployment_runtime_policy import (
    DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
)
from apps.shared.domain.app_auth_secret import (
    APP_AUTH_SECRET_VERIFIER_VERSION,
    app_auth_secret_verifier,
)
from apps.shared.services.workflow_task_publisher import (
    PUBLIC_CHAT_WORKFLOW_TASK_NAME,
)


# --- 실행 헬퍼 ---------------------------------------------------------------


_UNSET_HISTORY = object()


def _run_public(
    db,
    url_slug,
    user_inputs,
    monkeypatch,
    trigger_mode="app",
    conversation_history=_UNSET_HISTORY,
    async_result_cls=None,
    allow_stateless_public_chatbot_compatibility=False,
    expected_deployment_version=None,
):
    from apps.gateway.services import deployment_service as deployment_module

    if conversation_history is _UNSET_HISTORY:
        deployment_types = {
            row.type for row in db.rows if isinstance(row, WorkflowDeployment)
        }
        conversation_history = (
            () if DeploymentType.CHATBOT in deployment_types else None
        )

    celery = _CaptureCelery()
    celery.stored_history = []

    async def store_history(history, *, ttl_seconds):
        celery.stored_history.append(
            {"history": tuple(history), "ttl_seconds": ttl_seconds}
        )
        return "a" * 32

    monkeypatch.setattr(deployment_module, "store_public_chat_history", store_history)
    monkeypatch.setattr(deployment_module, "celery_app", celery)
    # run_deployment 내부의 `from celery.result import AsyncResult`가 가짜를 집도록 패치
    monkeypatch.setattr(
        "celery.result.AsyncResult",
        async_result_cls or _FakeAsyncResult,
    )

    result = asyncio.run(
        deployment_module.DeploymentService.run_deployment(
            db=db,
            url_slug=url_slug,
            user_inputs=user_inputs,
            trigger_mode=trigger_mode,
            runtime_policy=DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
            client_conversation_history=conversation_history,
            allow_stateless_public_chatbot_compatibility=(
                allow_stateless_public_chatbot_compatibility
            ),
            expected_deployment_version=expected_deployment_version,
            auth_token=None,
            require_auth=False,
        )
    )
    return celery, result


def _run_api_secret(db, url_slug, user_inputs, monkeypatch):
    from apps.gateway.services import deployment_service as deployment_module

    celery = _CaptureCelery()
    monkeypatch.setattr(deployment_module, "celery_app", celery)
    monkeypatch.setattr("celery.result.AsyncResult", _FakeAsyncResult)

    result = asyncio.run(
        deployment_module.DeploymentService.run_deployment(
            db=db,
            url_slug=url_slug,
            user_inputs=user_inputs,
            trigger_mode="api",
            runtime_policy=DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
            auth_token="deploy-secret",
            require_auth=True,
        )
    )
    return celery, result


def test_public_run_blocks_unresolved_external_configuration_before_publish(
    monkeypatch,
):
    from apps.gateway.services import deployment_service as deployment_module

    app_row, deployment_row = _deployed_app(DeploymentType.CHATBOT)
    deployment_row.graph_snapshot = {
        "nodes": [
            {
                "id": "answer-llm",
                "type": "llmNode",
                "data": {"model_id": "model-1", "user_prompt": "질문에 답변하세요."},
            },
            {
                "id": "slack-1",
                "type": "slackPostNode",
                "data": {"title": "Slack"},
            },
        ],
        "edges": [],
    }
    db = _Db(rows=[app_row, deployment_row])
    celery = _CaptureCelery()
    monkeypatch.setattr(deployment_module, "celery_app", celery)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            deployment_module.DeploymentService.run_deployment(
                db=db,
                url_slug=app_row.url_slug,
                user_inputs={},
                trigger_mode="app",
                runtime_policy=DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
                client_conversation_history=(),
                require_auth=False,
            )
        )

    assert exc_info.value.status_code == 409
    assert (
        exc_info.value.detail["error"]["code"]
        == "workflow.configuration_preflight.blocked"
    )
    assert celery.captured is None


def _run_authenticated(
    db,
    deployment_id,
    user_id,
    user_inputs,
    monkeypatch,
    async_result_cls=None,
    client_conversation_id=None,
):
    from apps.gateway.services import deployment_service as deployment_module

    celery = _CaptureCelery()
    monkeypatch.setattr(deployment_module, "celery_app", celery)
    monkeypatch.setattr(
        "celery.result.AsyncResult",
        async_result_cls or _FakeAsyncResult,
    )

    result = asyncio.run(
        deployment_module.DeploymentService.run_authenticated_deployment(
            db=db,
            deployment_id=deployment_id,
            user_inputs=user_inputs,
            client_conversation_id=client_conversation_id,
            current_user_id=user_id,
            runtime_policy=DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
        )
    )
    return celery, result


def _captured_context(celery):
    # send_task(name, args=[graph, user_inputs, execution_context], kwargs=...)
    return celery.captured.args[2]


def _captured_inputs(celery):
    return celery.captured.args[1]


# --- 테스트 ------------------------------------------------------------------


def test_public_chatbot_threads_client_history_without_server_memory(monkeypatch):
    app_row, deployment_row = _deployed_app(DeploymentType.CHATBOT)
    db = _Db(rows=[app_row, deployment_row])
    history = (
        {"role": "user", "content": "이전 질문"},
        {"role": "assistant", "content": "이전 답변"},
    )

    celery, result = _run_public(
        db,
        app_row.url_slug,
        {"question": "안녕"},
        monkeypatch,
        conversation_history=history,
    )

    ctx = _captured_context(celery)
    assert celery.captured.name == PUBLIC_CHAT_WORKFLOW_TASK_NAME
    assert ctx["memory_mode"] is False
    assert ctx["conversation_id"] is None
    assert "public_chat_history" not in ctx
    assert ctx["public_chat_history_ref"] == "a" * 32
    assert "이전 질문" not in repr(celery.captured.args)
    assert celery.stored_history == [{"history": history, "ttl_seconds": 600}]
    assert ctx["suppress_content_persistence"] is True
    assert ctx["execution_actor"] == {"type": "public"}
    assert ctx["public_chat_history_consumer_ref"].startswith(
        "workflow-node-location:v1:"
    )
    assert ctx["public_request_deadline_at"]
    assert _captured_inputs(celery) == {"question": "안녕"}
    assert celery.captured.options["expires"] is not None
    assert result["status"] == "success"


def test_public_chatbot_forgets_consumed_celery_result(monkeypatch):
    app_row, deployment_row = _deployed_app(DeploymentType.CHATBOT)
    db = _Db(rows=[app_row, deployment_row])
    _TrackingForgetAsyncResult.forget_calls = 0

    _, result = _run_public(
        db,
        app_row.url_slug,
        {"question": "안녕"},
        monkeypatch,
        async_result_cls=_TrackingForgetAsyncResult,
    )

    assert result["status"] == "success"
    assert _TrackingForgetAsyncResult.forget_calls == 1


def test_public_chatbot_cleanup_failure_does_not_replace_success_or_log_detail(
    monkeypatch,
    caplog,
):
    app_row, deployment_row = _deployed_app(DeploymentType.CHATBOT)
    db = _Db(rows=[app_row, deployment_row])
    caplog.set_level(logging.WARNING, logger="apps.gateway.services.deployment_service")

    _, result = _run_public(
        db,
        app_row.url_slug,
        {"question": "안녕"},
        monkeypatch,
        async_result_cls=_ForgetFailureAsyncResult,
    )

    assert result["status"] == "success"
    assert "RuntimeError" in caplog.text
    assert "private-result-backend-detail" not in caplog.text


@pytest.mark.parametrize("legacy_key", ["memory_mode", "conversation_id"])
def test_public_chatbot_rejects_legacy_conversation_controls(
    monkeypatch,
    legacy_key,
):
    app_row, deployment_row = _deployed_app(DeploymentType.CHATBOT)
    db = _Db(rows=[app_row, deployment_row])
    side_effects = []
    from apps.gateway.services import deployment_service as deployment_module

    monkeypatch.setattr(
        deployment_module.WorkflowBudgetService,
        "ensure_workflow_budget_allows_execution",
        lambda *_args, **_kwargs: side_effects.append("budget"),
    )
    monkeypatch.setattr(
        deployment_module.DeploymentService,
        "migrate_legacy_node_secrets",
        lambda *_args, **_kwargs: side_effects.append("migration"),
    )

    with pytest.raises(HTTPException) as exc_info:
        _run_public(
            db,
            app_row.url_slug,
            {"question": "x", legacy_key: "legacy-value"},
            monkeypatch,
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["code"] == "conversation.legacy_control_forbidden"
    assert side_effects == []


def test_legacy_public_chatbot_route_runs_stateless_without_persistence(monkeypatch):
    app_row, deployment_row = _deployed_app(DeploymentType.CHATBOT)
    deployment_row.config = {}
    db = _Db(rows=[app_row, deployment_row])

    celery, result = _run_public(
        db,
        app_row.url_slug,
        {
            "question": "x",
            "memory_mode": True,
            "conversation_id": "legacy-browser-session",
        },
        monkeypatch,
        conversation_history=None,
        allow_stateless_public_chatbot_compatibility=True,
    )

    ctx = _captured_context(celery)
    assert celery.captured.name == PUBLIC_CHAT_WORKFLOW_TASK_NAME
    assert result["status"] == "success"
    assert _captured_inputs(celery) == {"question": "x"}
    assert ctx["memory_mode"] is False
    assert ctx["conversation_id"] is None
    assert ctx["suppress_content_persistence"] is True
    assert ctx["execution_actor"] == {"type": "public"}
    assert ctx["public_chat_stateless_compatibility"] is True


def test_non_chatbot_does_not_force_memory_but_threads_conversation_id(monkeypatch):
    app_row, deployment_row = _deployed_app(DeploymentType.WEBAPP)
    db = _Db(rows=[app_row, deployment_row])

    celery, _ = _run_public(
        db,
        app_row.url_slug,
        {"question": "x", "conversation_id": "conv-C"},
        monkeypatch,
    )

    ctx = _captured_context(celery)
    assert celery.captured.name == "workflow.execute"
    # webapp 등 비챗봇 배포는 기억모드를 강제하지 않는다 (기본 False).
    assert ctx["memory_mode"] is False
    # conversation_id는 배포 타입과 무관하게 그대로 전달된다.
    assert ctx["conversation_id"] == "conv-C"


def test_public_chatbot_requires_client_history_envelope(monkeypatch):
    app_row, deployment_row = _deployed_app(DeploymentType.CHATBOT)
    db = _Db(rows=[app_row, deployment_row])

    with pytest.raises(HTTPException) as exc_info:
        _run_public(
            db,
            app_row.url_slug,
            {"question": "x"},
            monkeypatch,
            conversation_history=None,
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["code"] == "conversation.history_required"


def test_public_chatbot_rejects_stale_deployment_version_before_side_effects(
    monkeypatch,
):
    from apps.gateway.services import deployment_service as deployment_module

    app_row, deployment_row = _deployed_app(DeploymentType.CHATBOT)
    deployment_row.version = 2
    db = _Db(rows=[app_row, deployment_row])
    side_effects = []
    celery = _CaptureCelery()
    monkeypatch.setattr(deployment_module, "celery_app", celery)
    monkeypatch.setattr(
        deployment_module,
        "store_public_chat_history",
        lambda *_args, **_kwargs: side_effects.append("history_store"),
    )
    monkeypatch.setattr(
        deployment_module.WorkflowBudgetService,
        "ensure_workflow_budget_allows_execution",
        lambda *_args, **_kwargs: side_effects.append("budget"),
    )
    monkeypatch.setattr(
        deployment_module.DeploymentService,
        "migrate_legacy_node_secrets",
        lambda *_args, **_kwargs: side_effects.append("migration"),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            deployment_module.DeploymentService.run_deployment(
                db=db,
                url_slug=app_row.url_slug,
                user_inputs={"question": "current"},
                trigger_mode="app",
                runtime_policy=DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
                client_conversation_history=(),
                expected_deployment_version=1,
                require_auth=False,
            )
        )

    assert exc_info.value.status_code == 409
    assert (
        exc_info.value.detail["code"]
        == "conversation.deployment_version_changed"
    )
    assert side_effects == []
    assert celery.captured is None


def test_public_run_does_not_fallback_to_owner_execution_subject(monkeypatch):
    app_row, deployment_row = _deployed_app(DeploymentType.CHATBOT)
    db = _Db(rows=[app_row, deployment_row])

    celery, _ = _run_public(db, app_row.url_slug, {"question": "x"}, monkeypatch)

    ctx = _captured_context(celery)
    assert ctx["user_id"] == str(app_row.created_by)
    assert "execution_subject" not in ctx


def test_public_run_rejects_workflow_node_deployment(monkeypatch):
    app_row, deployment_row = _deployed_app(DeploymentType.WORKFLOW_NODE)
    db = _Db(rows=[app_row, deployment_row])

    with pytest.raises(HTTPException) as exc_info:
        _run_public(
            db,
            app_row.url_slug,
            {"question": "x"},
            monkeypatch,
            expected_deployment_version=999,
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Deployment not found."


def test_public_slug_run_rejects_cross_app_active_deployment_pointer(monkeypatch):
    app_row, deployment_row = _deployed_app(DeploymentType.CHATBOT)
    deployment_row.app_id = uuid4()
    db = _Db(rows=[app_row, deployment_row])

    with pytest.raises(HTTPException) as exc_info:
        _run_public(db, app_row.url_slug, {"question": "x"}, monkeypatch)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Deployment data not found."


@pytest.mark.parametrize(
    "deployment_type",
    [
        DeploymentType.API,
        DeploymentType.MCP,
        DeploymentType.SCHEDULE,
        DeploymentType.WEBHOOK,
        DeploymentType.WORKFLOW_NODE,
    ],
)
def test_public_slug_run_rejects_non_public_app_deployment_types(
    monkeypatch,
    deployment_type,
):
    app_row, deployment_row = _deployed_app(deployment_type)
    db = _Db(rows=[app_row, deployment_row])

    with pytest.raises(HTTPException) as exc_info:
        _run_public(db, app_row.url_slug, {"question": "x"}, monkeypatch)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Deployment not found."


def test_api_slug_run_allows_api_deployment(monkeypatch):
    app_row, deployment_row = _deployed_app(DeploymentType.API)
    db = _Db(rows=[app_row, deployment_row])

    celery, result = _run_api_secret(
        db,
        app_row.url_slug,
        {"question": "x"},
        monkeypatch,
    )

    assert _captured_context(celery)["trigger_mode"] == "api"
    assert result["status"] == "success"


def test_api_slug_run_rejects_cross_app_active_deployment_pointer(monkeypatch):
    app_row, deployment_row = _deployed_app(DeploymentType.API)
    deployment_row.app_id = uuid4()
    db = _Db(rows=[app_row, deployment_row])

    with pytest.raises(HTTPException) as exc_info:
        _run_api_secret(db, app_row.url_slug, {"question": "x"}, monkeypatch)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Deployment data not found."


def test_workflow_node_listing_join_requires_active_deployment_owner():
    from apps.gateway.services.deployment_service import DeploymentService

    query = _JoinCaptureQuery()
    db = _JoinCaptureDb(query)

    assert DeploymentService.list_workflow_node_deployments(db, uuid4()) == []

    clauses = list(query.join_clause.clauses)
    column_pairs = {
        (expression.left.key, expression.right.key) for expression in clauses
    }
    assert column_pairs == {
        ("active_deployment_id", "id"),
        ("id", "app_id"),
    }


@pytest.mark.parametrize(
    "deployment_type",
    [
        DeploymentType.CHATBOT,
        DeploymentType.MCP,
        DeploymentType.SCHEDULE,
        DeploymentType.WEBAPP,
        DeploymentType.WEBHOOK,
        DeploymentType.WIDGET,
        DeploymentType.WORKFLOW_NODE,
    ],
)
def test_api_slug_run_rejects_non_api_deployment_types(monkeypatch, deployment_type):
    app_row, deployment_row = _deployed_app(deployment_type)
    db = _Db(rows=[app_row, deployment_row])

    with pytest.raises(HTTPException) as exc_info:
        _run_api_secret(db, app_row.url_slug, {"question": "x"}, monkeypatch)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Deployment not found."


@pytest.mark.parametrize(
    "deployment_type",
    [DeploymentType.CHATBOT, DeploymentType.INTERNAL_CHATBOT],
)
def test_authenticated_run_uses_current_user_execution_subject(
    monkeypatch,
    deployment_type,
):
    from apps.gateway.services import deployment_service as deployment_module

    app_row, deployment_row = _deployed_app(deployment_type)
    current_user_id = uuid4()
    client_conversation_id = str(uuid4())
    forged_user_id = str(uuid4())
    forged_organization_id = str(uuid4())
    db = _Db(rows=[app_row, deployment_row])
    budget_calls = []
    evaluated_surfaces = []
    evaluate_surface = deployment_module.is_deployment_type_allowed_for_surface

    def capture_budget_call(db, **kwargs):
        budget_calls.append(kwargs)

    monkeypatch.setattr(
        deployment_module.WorkflowBudgetService,
        "ensure_workflow_budget_allows_execution",
        capture_budget_call,
    )
    monkeypatch.setattr(
        deployment_module,
        "is_deployment_type_allowed_for_surface",
        lambda deployment_type, surface, *, policy: (
            evaluated_surfaces.append((deployment_type, surface))
            or evaluate_surface(deployment_type, surface, policy=policy)
        ),
    )

    celery, result = _run_authenticated(
        db,
        deployment_row.id,
        current_user_id,
        {
            "question": "안녕",
            "user_id": forged_user_id,
            "organization_id": forged_organization_id,
            "execution_subject": {"type": "user", "id": forged_user_id},
        },
        monkeypatch,
        client_conversation_id=client_conversation_id,
    )

    ctx = _captured_context(celery)
    sent_inputs = _captured_inputs(celery)

    assert ctx["user_id"] == str(current_user_id)
    assert ctx["execution_subject"] == {
        "type": "user",
        "id": str(current_user_id),
    }
    assert ctx["memory_mode"] is True
    assert ctx["conversation_id"].startswith("auth:v1:")
    assert client_conversation_id not in ctx["conversation_id"]
    assert sent_inputs == {
        "question": "안녕",
        "user_id": forged_user_id,
        "organization_id": forged_organization_id,
        "execution_subject": {"type": "user", "id": forged_user_id},
    }
    assert ctx["organization_id"] == str(app_row.organization_id)
    assert budget_calls == [
        {
            "workflow_id": app_row.workflow_id,
            "trigger_mode": "app",
            "actor_id": current_user_id,
        }
    ]
    assert evaluated_surfaces == [
        (deployment_row.type, deployment_module.SURFACE_AUTHENTICATED_RUN)
    ]
    assert result["status"] == "success"


def test_authenticated_conversation_control_preserves_declared_reserved_inputs(
    monkeypatch,
):
    app_row, deployment_row = _deployed_app(DeploymentType.INTERNAL_CHATBOT)
    deployment_row.input_schema = {
        "variables": [
            {"name": "conversation_id"},
            {"name": "memory_mode"},
        ]
    }
    db = _Db(rows=[app_row, deployment_row])
    client_conversation_id = str(uuid4())

    celery, _ = _run_authenticated(
        db,
        deployment_row.id,
        uuid4(),
        {
            "conversation_id": "business-conversation-value",
            "memory_mode": "business-memory-value",
        },
        monkeypatch,
        client_conversation_id=client_conversation_id,
    )

    assert _captured_inputs(celery) == {
        "conversation_id": "business-conversation-value",
        "memory_mode": "business-memory-value",
    }
    context = _captured_context(celery)
    assert context["memory_mode"] is True
    assert context["conversation_id"].startswith("auth:v1:")
    assert client_conversation_id not in context["conversation_id"]


def test_authenticated_run_rejects_conflicting_conversation_controls(monkeypatch):
    app_row, deployment_row = _deployed_app(DeploymentType.INTERNAL_CHATBOT)
    db = _Db(rows=[app_row, deployment_row])

    with pytest.raises(HTTPException) as exc_info:
        _run_authenticated(
            db,
            deployment_row.id,
            uuid4(),
            {"question": "x", "conversation_id": "legacy-value"},
            monkeypatch,
            client_conversation_id=str(uuid4()),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Conflicting conversation controls"


def test_authenticated_run_rejects_typed_conversation_for_non_chatbot(monkeypatch):
    app_row, deployment_row = _deployed_app(DeploymentType.WEBAPP)
    db = _Db(rows=[app_row, deployment_row])

    with pytest.raises(HTTPException) as exc_info:
        _run_authenticated(
            db,
            deployment_row.id,
            uuid4(),
            {"question": "x"},
            monkeypatch,
            client_conversation_id=str(uuid4()),
        )

    assert exc_info.value.status_code == 400
    assert (
        exc_info.value.detail
        == "Conversation control is only supported for chatbot deployments"
    )


@pytest.mark.parametrize(
    "invalid_value",
    [123, "", " " * 4, "x" * 256, "valid-prefix\ninvalid-suffix"],
)
def test_authenticated_legacy_conversation_control_is_bounded(
    monkeypatch,
    invalid_value,
):
    app_row, deployment_row = _deployed_app(DeploymentType.INTERNAL_CHATBOT)
    db = _Db(rows=[app_row, deployment_row])

    with pytest.raises(HTTPException) as exc_info:
        _run_authenticated(
            db,
            deployment_row.id,
            uuid4(),
            {"question": "x", "conversation_id": invalid_value},
            monkeypatch,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Invalid conversation control"


def test_authenticated_declared_legacy_conversation_input_is_not_swallowed(
    monkeypatch,
):
    app_row, deployment_row = _deployed_app(DeploymentType.INTERNAL_CHATBOT)
    deployment_row.input_schema = {"variables": [{"name": "conversation_id"}]}
    db = _Db(rows=[app_row, deployment_row])

    celery, _ = _run_authenticated(
        db,
        deployment_row.id,
        uuid4(),
        {"conversation_id": "business-value"},
        monkeypatch,
    )

    assert _captured_inputs(celery) == {"conversation_id": "business-value"}
    assert _captured_context(celery)["conversation_id"] is None


def test_authenticated_conversation_namespace_is_stable_and_isolated():
    from apps.gateway.services.deployment_service import DeploymentService

    deployment_id = uuid4()
    subject_id = uuid4()
    client_id = str(uuid4())

    first = DeploymentService._authenticated_conversation_id(
        deployment_id=deployment_id,
        subject_id=subject_id,
        client_conversation_id=client_id,
    )

    assert first == DeploymentService._authenticated_conversation_id(
        deployment_id=deployment_id,
        subject_id=subject_id,
        client_conversation_id=client_id,
    )
    assert first != DeploymentService._authenticated_conversation_id(
        deployment_id=uuid4(),
        subject_id=subject_id,
        client_conversation_id=client_id,
    )
    assert first != DeploymentService._authenticated_conversation_id(
        deployment_id=deployment_id,
        subject_id=uuid4(),
        client_conversation_id=client_id,
    )
    assert first.startswith("auth:v1:")
    assert client_id not in first


def test_authenticated_run_rejects_inactive_deployment(monkeypatch):
    app_row, deployment_row = _deployed_app(DeploymentType.WEBAPP)
    deployment_row.is_active = False
    db = _Db(rows=[app_row, deployment_row])

    with pytest.raises(HTTPException) as exc_info:
        _run_authenticated(
            db,
            deployment_row.id,
            uuid4(),
            {"question": "x"},
            monkeypatch,
        )

    assert exc_info.value.status_code == 404


def test_authenticated_run_rejects_stale_non_active_deployment(monkeypatch):
    app_row, deployment_row = _deployed_app(DeploymentType.WEBAPP)
    app_row.active_deployment_id = uuid4()
    db = _Db(rows=[app_row, deployment_row])

    with pytest.raises(HTTPException) as exc_info:
        _run_authenticated(
            db,
            deployment_row.id,
            uuid4(),
            {"question": "x"},
            monkeypatch,
        )

    assert exc_info.value.status_code == 404


def test_authenticated_run_rejects_workflow_node_deployment(monkeypatch):
    app_row, deployment_row = _deployed_app(DeploymentType.WORKFLOW_NODE)
    db = _Db(rows=[app_row, deployment_row])

    with pytest.raises(HTTPException) as exc_info:
        _run_authenticated(
            db,
            deployment_row.id,
            uuid4(),
            {"question": "x"},
            monkeypatch,
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Deployment not found"


def test_deployment_run_info_excludes_secret_and_graph_snapshot():
    from apps.gateway.services import deployment_service as deployment_module

    app_row, deployment_row = _deployed_app(DeploymentType.CHATBOT)
    deployment_row.input_schema = {"variables": [{"name": "question"}]}
    deployment_row.output_schema = {"outputs": [{"variable": "answer"}]}
    db = _Db(rows=[app_row, deployment_row])

    result = deployment_module.DeploymentService.get_deployment_run_info(
        db,
        deployment_row.id,
        runtime_policy=DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
    )

    assert result["deployment_id"] == deployment_row.id
    assert result["workflow_id"] == app_row.workflow_id
    assert result["name"] == app_row.name
    assert result["input_schema"] == deployment_row.input_schema
    assert "auth_secret" not in result
    assert "graph_snapshot" not in result


def test_deployment_run_info_uses_dedicated_runtime_surface(monkeypatch):
    from apps.gateway.services import deployment_service as deployment_module

    app_row, deployment_row = _deployed_app(DeploymentType.CHATBOT)
    db = _Db(rows=[app_row, deployment_row])
    evaluated = []

    def capture_surface(deployment_type, surface, *, policy):
        evaluated.append((deployment_type, surface, policy))
        return True

    monkeypatch.setattr(
        deployment_module,
        "is_deployment_type_allowed_for_surface",
        capture_surface,
    )

    deployment_module.DeploymentService.get_deployment_run_info(
        db,
        deployment_row.id,
        runtime_policy=DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
    )

    assert evaluated == [
        (
            deployment_row.type,
            deployment_module.SURFACE_AUTHENTICATED_RUN_INFO,
            DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
        )
    ]


def test_deployment_run_info_does_not_acquire_lifecycle_write_lock(monkeypatch):
    from apps.gateway.services import deployment_service as deployment_module

    app_row, deployment_row = _deployed_app(DeploymentType.CHATBOT)
    db = _Db(rows=[app_row, deployment_row])
    monkeypatch.setattr(
        deployment_module,
        "lock_app_for_lifecycle",
        lambda *args, **kwargs: pytest.fail(
            "read-only runtime lookup must not acquire an App write lock"
        ),
    )

    result = deployment_module.DeploymentService.get_deployment_run_info(
        db,
        deployment_row.id,
        runtime_policy=DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
    )

    assert result["deployment_id"] == deployment_row.id


def test_deployment_run_info_rejects_workflow_node_deployment():
    from apps.gateway.services import deployment_service as deployment_module

    app_row, deployment_row = _deployed_app(DeploymentType.WORKFLOW_NODE)
    db = _Db(rows=[app_row, deployment_row])

    with pytest.raises(HTTPException) as exc_info:
        deployment_module.DeploymentService.get_deployment_run_info(
            db,
            deployment_row.id,
            runtime_policy=DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Deployment not found"


def test_engine_failure_detail_does_not_expose_secret_like_exception(
    monkeypatch, caplog
):
    app_row, deployment_row = _deployed_app(DeploymentType.CHATBOT)
    db = _Db(rows=[app_row, deployment_row])
    caplog.set_level(logging.ERROR, logger="apps.gateway.services.deployment_service")

    with pytest.raises(HTTPException) as exc_info:
        _run_authenticated(
            db,
            deployment_row.id,
            uuid4(),
            {"question": "x"},
            monkeypatch,
            async_result_cls=_SecretFailureAsyncResult,
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Workflow execution failed"
    assert "sk-test-secret" not in str(exc_info.value.detail)
    assert "RuntimeError" in caplog.text
    assert "sk-test-secret" not in caplog.text


# --- fakes -------------------------------------------------------------------


def _deployed_app(deployment_type):
    workflow_id = uuid4()
    organization_id = uuid4()
    deployment_id = uuid4()
    graph_snapshot = {"nodes": [], "edges": []}
    config = {}
    if deployment_type is DeploymentType.CHATBOT:
        graph_snapshot = {
            "nodes": [
                {
                    "id": "answer-llm",
                    "type": "llmNode",
                    "data": {"model_id": "model-1", "user_prompt": "질문에 답변하세요."},
                }
            ],
            "edges": [],
        }
        config = {
            "public_conversation": {
                "contract_version": "public_chat_conversation.v1",
                "history_consumer": {"node_id": "answer-llm", "container_path": []},
            }
        }
    app_row = App(
        id=uuid4(),
        name="챗봇 앱",
        url_slug=f"chatbot-{uuid4().hex[:8]}",
        auth_secret=None,
        auth_secret_verifier=app_auth_secret_verifier("deploy-secret"),
        auth_secret_verifier_version=APP_AUTH_SECRET_VERIFIER_VERSION,
        auth_secret_generation=1,
        workflow_id=workflow_id,
        organization_id=organization_id,
        active_deployment_id=deployment_id,
        created_by=uuid4(),
    )
    deployment_row = WorkflowDeployment(
        id=deployment_id,
        app_id=app_row.id,
        version=1,
        type=deployment_type,
        graph_snapshot=graph_snapshot,
        config=config,
        is_active=True,
        created_by=uuid4(),
    )
    return app_row, deployment_row


class _CaptureCelery:
    """send_task 인자를 캡처하고 즉시 완료되는 가짜 task를 반환한다."""

    def __init__(self):
        self.captured = None

    def send_task(self, name, args=None, kwargs=None, **options):
        self.captured = SimpleNamespace(
            name=name,
            args=args,
            kwargs=kwargs,
            options=options,
        )
        return SimpleNamespace(id="fake-task-id")


class _FakeAsyncResult:
    def __init__(self, task_id, app=None):
        self._task_id = task_id

    def ready(self):
        return True

    def failed(self):
        return False

    @property
    def result(self):
        return {
            "status": "success",
            "result": {"answer": "ok"},
            "run_id": "00000000-0000-0000-0000-000000000777",
        }


class _TrackingForgetAsyncResult(_FakeAsyncResult):
    forget_calls = 0

    def forget(self):
        type(self).forget_calls += 1


class _ForgetFailureAsyncResult(_FakeAsyncResult):
    def forget(self):
        raise RuntimeError("private-result-backend-detail")


class _SecretFailureAsyncResult:
    def __init__(self, task_id, app=None):
        self._task_id = task_id

    def ready(self):
        return True

    def failed(self):
        return True

    @property
    def result(self):
        return RuntimeError("provider failed with api_key=sk-test-secret")


class _Query:
    def __init__(self, items):
        self.items = list(items)
        self.filters = []

    def filter(self, *expressions):
        self.filters.extend(expressions)
        return self

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        return next(
            (
                item
                for item in self.items
                if all(_matches(item, e) for e in self.filters)
            ),
            None,
        )


def _matches(item, expression):
    if not hasattr(expression, "left"):
        return True
    column = str(expression.left).split(".")[-1]
    if not hasattr(item, column):
        return True
    if expression.operator is not eq:
        return True
    right = expression.right
    right_value = right.value if hasattr(right, "value") else right
    return getattr(item, column) == right_value


class _Db:
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    def query(self, model, *rest):
        return _Query([row for row in self.rows if isinstance(row, model)])

    def add(self, obj):
        self.rows.append(obj)

    def flush(self):
        pass

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class _JoinCaptureQuery:
    def __init__(self):
        self.join_clause = None

    def join(self, _model, on_clause):
        self.join_clause = on_clause
        return self

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return []


class _JoinCaptureDb:
    def __init__(self, query):
        self.query_result = query

    def query(self, *args, **kwargs):
        return self.query_result
