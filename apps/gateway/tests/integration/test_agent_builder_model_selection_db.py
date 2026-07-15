import copy
import json
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from apps.gateway.services.agent_builder_intent_service import (
    LLMAgentBuilderIntentExtractor,
)
from apps.gateway.services.agent_builder_service import (
    EXPECTED_APP_PRIMARY_WORKFLOW_ID,
    AgentBuilderService,
    calculate_graph_hash,
)
from apps.gateway.services.llm_service import (
    LLMCredentialNotAvailableError,
    LLMService,
)
from apps.gateway.services.workflow_service import WorkflowService
from apps.shared.audit.actions import AuditAction
from apps.shared.db.models.agent_builder import (
    AgentBuilderDraft,
    AgentBuilderRequest,
    AgentBuilderSession,
)
from apps.shared.db.models.app import App
from apps.shared.db.models.audit_log import AuditLog
from apps.shared.db.models.llm import (
    LLMCredential,
    LLMUsageLog,
    LLMModel,
    LLMProvider,
    LLMRelCredentialModel,
)
from apps.shared.db.models.organization import Organization
from apps.shared.db.models.organization_membership import OrganizationMembership
from apps.shared.db.models.team import UserLLMPermission, UserWorkflowPermission
from apps.shared.db.models.user import User
from apps.shared.db.models.workflow import Workflow
from apps.shared.db.models.workflow_run import WorkflowRun
from apps.shared.db.session import engine
from apps.shared.schemas.agent_builder import (
    AgentBuilderApplyRequest,
    AgentBuilderMessageRequest,
)
from apps.shared.schemas.workflow import WorkflowDraftRequest


@pytest.fixture
def db_session():
    connection = engine.connect()
    transaction = connection.begin()
    db = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield db
    finally:
        db.close()
        transaction.rollback()
        connection.close()


def _user(email_prefix: str) -> User:
    suffix = uuid.uuid4().hex
    return User(
        email=f"{email_prefix}-{suffix}@example.invalid",
        name=f"Agent Builder Integration {email_prefix}",
        social_provider="local",
    )


def test_agent_builder_selected_model_rechecks_db_permission_and_relation(db_session):
    manager = _user("manager")
    actor = _user("actor")
    db_session.add_all([manager, actor])
    db_session.flush()

    organization = Organization(
        name=f"Agent Builder Integration {uuid.uuid4().hex}",
        created_by=manager.id,
        managed_by=manager.id,
    )
    db_session.add(organization)
    db_session.flush()
    db_session.add_all(
        [
            OrganizationMembership(
                organization_id=organization.id,
                user_id=manager.id,
                membership_state="active",
                organization_auth_state="manager",
            ),
            OrganizationMembership(
                organization_id=organization.id,
                user_id=actor.id,
                membership_state="active",
                organization_auth_state="member",
                invited_by=manager.id,
            ),
        ]
    )

    provider = LLMProvider(
        name="openai",
        description="Agent Builder integration provider",
        type="system",
        base_url="https://example.invalid/v1",
        auth_type="api_key",
        doc_url="https://example.invalid/docs",
    )
    db_session.add(provider)
    db_session.flush()
    model = LLMModel(
        provider_id=provider.id,
        model_id_for_api_call="gpt-5.5",
        name="GPT-5.5",
        type="chat",
        context_window=8192,
        is_active=True,
    )
    pro_model = LLMModel(
        provider_id=provider.id,
        model_id_for_api_call="gpt-5.5-pro",
        name="GPT-5.5 Pro",
        type="chat",
        context_window=8192,
        is_active=True,
    )
    credential = LLMCredential(
        provider_id=provider.id,
        user_id=manager.id,
        organization_id=organization.id,
        credential_name="Agent Builder Integration",
        encrypted_config=json.dumps({"apiKey": "integration-placeholder"}),
        config_preview="inte****",
        is_valid=True,
    )
    db_session.add_all([model, pro_model, credential])
    db_session.flush()
    relation = LLMRelCredentialModel(
        credential_id=credential.id,
        model_id=model.id,
        is_verified=True,
        priority=5,
    )
    pro_relation = LLMRelCredentialModel(
        credential_id=credential.id,
        model_id=pro_model.id,
        is_verified=True,
        priority=0,
    )
    permission = UserLLMPermission(
        grantee_organization_id=organization.id,
        user_id=actor.id,
        llm_credential_id=credential.id,
        auth_state="operator",
        assigned_by=manager.id,
    )
    db_session.add_all([relation, pro_relation, permission])
    db_session.flush()

    groups = LLMService.get_agent_builder_model_option_groups(
        db_session,
        actor.id,
        organization.id,
    )
    openai_options = next(
        group.options for group in groups if group.provider_name == "openai"
    )
    assert [option.credential.id for option in openai_options] == [
        credential.id,
        credential.id,
    ]
    assert [option.model.id for option in openai_options] == [model.id, pro_model.id]
    recommendation = LLMService.get_agent_builder_draft_model_recommendation(
        db_session,
        actor.id,
        organization.id,
    )
    assert recommendation is not None
    assert recommendation.model.id == model.id

    permission.auth_state = "none"
    db_session.flush()
    db_session.expire_all()

    groups_after_revoke = LLMService.get_agent_builder_model_option_groups(
        db_session,
        actor.id,
        organization.id,
    )
    openai_after_revoke = next(
        group.options
        for group in groups_after_revoke
        if group.provider_name == "openai"
    )
    assert openai_after_revoke == []
    assert (
        LLMService.get_agent_builder_draft_model_recommendation(
            db_session,
            actor.id,
            organization.id,
        )
        is None
    )

    with pytest.raises(LLMCredentialNotAvailableError) as exc:
        LLMService.get_wizard_client_for_selection(
            db_session,
            user_id=actor.id,
            credential_id=credential.id,
            model_id=model.id,
            organization_id=organization.id,
            runtime_surface="agent_builder_intent",
            audit_on_failure=False,
        )

    assert exc.value.reason == "credential_use_denied"


@pytest.mark.parametrize(
    "excluded_state",
    [
        "other_organization",
        "inactive_model",
        "invalid_credential",
        "non_chat_model",
        "unverified_relation",
        "provider_mismatch",
    ],
)
def test_agent_builder_default_excludes_unavailable_gpt_5_5(
    db_session,
    excluded_state,
):
    manager = _user(f"excluded-manager-{excluded_state}")
    actor = _user(f"excluded-actor-{excluded_state}")
    db_session.add_all([manager, actor])
    db_session.flush()

    organization = Organization(
        name=f"Agent Builder Exclusion {uuid.uuid4().hex}",
        created_by=manager.id,
        managed_by=manager.id,
    )
    other_organization = Organization(
        name=f"Agent Builder Other {uuid.uuid4().hex}",
        created_by=manager.id,
        managed_by=manager.id,
    )
    db_session.add_all([organization, other_organization])
    db_session.flush()
    db_session.add(
        OrganizationMembership(
            organization_id=organization.id,
            user_id=actor.id,
            membership_state="active",
            organization_auth_state="member",
            invited_by=manager.id,
        )
    )

    provider = LLMProvider(
        name="openai",
        description="Agent Builder exclusion provider",
        type="system",
        base_url="https://example.invalid/v1",
        auth_type="api_key",
        doc_url="https://example.invalid/docs",
    )
    db_session.add(provider)
    db_session.flush()
    credential_provider = provider
    if excluded_state == "provider_mismatch":
        credential_provider = LLMProvider(
            name="anthropic",
            description="Agent Builder mismatched credential provider",
            type="system",
            base_url="https://example.invalid/v1",
            auth_type="api_key",
            doc_url="https://example.invalid/docs",
        )
        db_session.add(credential_provider)
        db_session.flush()
    model = LLMModel(
        provider_id=provider.id,
        model_id_for_api_call="gpt-5.5",
        name="GPT-5.5",
        type="embedding" if excluded_state == "non_chat_model" else "chat",
        context_window=8192,
        is_active=excluded_state != "inactive_model",
    )
    credential = LLMCredential(
        provider_id=credential_provider.id,
        user_id=manager.id,
        organization_id=(
            other_organization.id
            if excluded_state == "other_organization"
            else organization.id
        ),
        credential_name="Agent Builder Excluded Credential",
        encrypted_config=json.dumps({}),
        config_preview=None,
        is_valid=excluded_state != "invalid_credential",
    )
    db_session.add_all([model, credential])
    db_session.flush()
    relation = LLMRelCredentialModel(
        credential_id=credential.id,
        model_id=model.id,
        is_verified=excluded_state != "unverified_relation",
        priority=0,
    )
    permission = UserLLMPermission(
        grantee_organization_id=(
            other_organization.id
            if excluded_state == "other_organization"
            else organization.id
        ),
        user_id=actor.id,
        llm_credential_id=credential.id,
        auth_state="operator",
        assigned_by=manager.id,
    )
    db_session.add_all([relation, permission])
    db_session.flush()

    groups = LLMService.get_agent_builder_model_option_groups(
        db_session,
        actor.id,
        organization.id,
    )
    openai_group = next(
        group for group in groups if group.provider_name == "openai"
    )

    assert openai_group.options == []
    assert openai_group.unavailable_reason == "no_authorized_model"
    assert (
        LLMService.get_agent_builder_draft_model_recommendation(
            db_session,
            actor.id,
            organization.id,
        )
        is None
    )


def test_agent_builder_apply_save_persists_layout_models_and_audit_without_execution(
    db_session,
):
    actor = _user("apply-save")
    db_session.add(actor)
    db_session.flush()

    organization = Organization(
        name=f"Agent Builder Apply Integration {uuid.uuid4().hex}",
        created_by=actor.id,
        managed_by=actor.id,
    )
    db_session.add(organization)
    db_session.flush()
    db_session.add(
        OrganizationMembership(
            organization_id=organization.id,
            user_id=actor.id,
            membership_state="active",
            organization_auth_state="manager",
        )
    )

    app = App(
        organization_id=organization.id,
        name="Agent Builder Apply Integration",
        description="Transactional apply/save verification",
        url_slug=f"agent-builder-apply-{uuid.uuid4().hex}",
        auth_secret="integration-placeholder",
        created_by=actor.id,
    )
    db_session.add(app)
    db_session.flush()

    base_graph = {
        "nodes": [
            {
                "id": "start",
                "type": "startNode",
                "position": {"x": 0, "y": 0},
                "data": {},
            },
            {
                "id": "answer",
                "type": "answerNode",
                "position": {"x": 0, "y": 0},
                "data": {},
            },
            {
                "id": "existing-llm",
                "type": "llmNode",
                "position": {"x": 0, "y": 0},
                "data": {"model_id": "gpt-5.5-pro"},
            },
        ],
        "edges": [
            {
                "id": "edge-start-existing",
                "source": "start",
                "target": "existing-llm",
            },
            {
                "id": "edge-existing-answer",
                "source": "existing-llm",
                "target": "answer",
            },
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }
    workflow = Workflow(
        organization_id=organization.id,
        app_id=app.id,
        graph=base_graph,
        features={},
        env_variables=[],
        runtime_variables=[],
        created_by=actor.id,
        updated_by=actor.id,
    )
    db_session.add(workflow)
    db_session.flush()
    app.workflow_id = workflow.id
    db_session.add(
        UserWorkflowPermission(
            grantee_organization_id=organization.id,
            workflow_id=workflow.id,
            user_id=actor.id,
            auth_state="manager",
            assigned_by=actor.id,
        )
    )

    session = AgentBuilderSession(
        organization_id=organization.id,
        user_id=actor.id,
        workflow_id=workflow.id,
        app_id=app.id,
        status="active",
    )
    db_session.add(session)
    db_session.flush()
    request = AgentBuilderRequest(
        session_id=session.id,
        organization_id=organization.id,
        user_id=actor.id,
        status="draft_ready",
        message_summary="Add an LLM step",
        structured_request={},
        response_payload={},
    )
    db_session.add(request)
    db_session.flush()

    preview_graph = {
        "nodes": [
            *base_graph["nodes"],
            {
                "id": "agent-llm",
                "type": "llmNode",
                "position": {"x": 0, "y": 0},
                "data": {"model_id": "gpt-5.5"},
            },
        ],
        "edges": [
            {
                "id": "edge-start-agent-llm",
                "source": "start",
                "target": "agent-llm",
            },
            {
                "id": "edge-agent-llm-existing",
                "source": "agent-llm",
                "target": "existing-llm",
            },
            *base_graph["edges"][1:],
        ],
    }
    draft = AgentBuilderDraft(
        request_id=request.id,
        session_id=session.id,
        organization_id=organization.id,
        user_id=actor.id,
        draft_mode="modify_workflow",
        workflow_id=workflow.id,
        app_id=app.id,
        preview_graph=preview_graph,
        node_detail_previews=[],
        validation_result={"valid": True, "issues": []},
        draft_metadata={
            "workflow_id": str(workflow.id),
            "generated_node_ids": ["agent-llm"],
            "generated_edge_ids": [
                "edge-start-agent-llm",
                "edge-agent-llm-existing",
            ],
            "target_resolution": {"replaced_edge_ids": ["edge-start-existing"]},
        },
        base_graph_hash=calculate_graph_hash(base_graph),
        status="ready",
    )
    db_session.add(draft)
    db_session.flush()

    assert (
        db_session.query(WorkflowRun)
        .filter(WorkflowRun.workflow_id == workflow.id)
        .count()
        == 0
    )
    assert (
        db_session.query(LLMUsageLog)
        .filter(
            LLMUsageLog.user_id == actor.id,
            LLMUsageLog.organization_id == organization.id,
        )
        .count()
        == 0
    )

    service = AgentBuilderService(
        db_session,
        user=actor,
        organization_id=organization.id,
    )
    service.record_preview_opened(draft.id)
    response = service.apply_draft(
        draft.id,
        AgentBuilderApplyRequest(
            action="apply_and_save",
            client_preview_graph_hash=calculate_graph_hash(preview_graph),
            client_latest_graph_hash=calculate_graph_hash(base_graph),
        ),
    )

    assert response.outcome == "saved"
    assert response.saved_workflow_id == workflow.id
    assert response.layout_optimization_applied is True
    db_session.expire_all()

    saved_workflow = db_session.get(Workflow, workflow.id)
    positions = {
        node["id"]: node["position"] for node in saved_workflow.graph["nodes"]
    }
    assert positions == {
        "start": {"x": 0, "y": 0},
        "agent-llm": {"x": 580, "y": 0},
        "existing-llm": {"x": 1160, "y": 0},
        "answer": {"x": 1740, "y": 0},
    }
    saved_models = {
        node["id"]: node.get("data", {}).get("model_id")
        for node in saved_workflow.graph["nodes"]
        if node.get("type") == "llmNode"
    }
    assert saved_models == {
        "agent-llm": "gpt-5.5",
        "existing-llm": "gpt-5.5-pro",
    }

    actions = {
        row.action: row
        for row in db_session.query(AuditLog)
        .filter(
            AuditLog.actor_id == actor.id,
            AuditLog.action.in_(
                [
                    AuditAction.AGENT_BUILDER_PREVIEW_OPENED,
                    AuditAction.AGENT_BUILDER_APPLY_SAVE_REQUESTED,
                    AuditAction.AGENT_BUILDER_APPLY_SAVE_SUCCEEDED,
                ]
            ),
        )
        .all()
    }
    assert set(actions) == {
        AuditAction.AGENT_BUILDER_PREVIEW_OPENED,
        AuditAction.AGENT_BUILDER_APPLY_SAVE_REQUESTED,
        AuditAction.AGENT_BUILDER_APPLY_SAVE_SUCCEEDED,
    }
    success_metadata = actions[
        AuditAction.AGENT_BUILDER_APPLY_SAVE_SUCCEEDED
    ].audit_metadata
    assert success_metadata["layout_optimization_applied"] is True
    assert success_metadata["audit_durability"] == "same_transaction_audit_log"
    assert "integration-placeholder" not in json.dumps(success_metadata)

    assert (
        db_session.query(WorkflowRun)
        .filter(WorkflowRun.workflow_id == workflow.id)
        .count()
        == 0
    )
    assert (
        db_session.query(LLMUsageLog)
        .filter(
            LLMUsageLog.user_id == actor.id,
            LLMUsageLog.organization_id == organization.id,
        )
        .count()
        == 0
    )

    editor_graph = copy.deepcopy(saved_workflow.graph)
    generated_llm = next(
        node for node in editor_graph["nodes"] if node["id"] == "agent-llm"
    )
    generated_llm["data"]["model_id"] = "gpt-5.5-pro"
    WorkflowService.save_draft(
        db_session,
        str(workflow.id),
        WorkflowDraftRequest.model_validate(editor_graph),
        user_id=str(actor.id),
    )
    db_session.expire_all()

    editor_saved_workflow = db_session.get(Workflow, workflow.id)
    editor_saved_models = {
        node["id"]: node.get("data", {}).get("model_id")
        for node in editor_saved_workflow.graph["nodes"]
        if node.get("type") == "llmNode"
    }
    assert editor_saved_models == {
        "agent-llm": "gpt-5.5-pro",
        "existing-llm": "gpt-5.5-pro",
    }


def test_header_selection_stays_in_planner_while_new_agent_persists_recommendation(
    db_session,
):
    actor = _user("new-agent-default-model")
    db_session.add(actor)
    db_session.flush()

    organization = Organization(
        name=f"Agent Builder Default Model {uuid.uuid4().hex}",
        created_by=actor.id,
        managed_by=actor.id,
    )
    db_session.add(organization)
    db_session.flush()
    db_session.add(
        OrganizationMembership(
            organization_id=organization.id,
            user_id=actor.id,
            membership_state="active",
            organization_auth_state="manager",
        )
    )

    provider = LLMProvider(
        name="openai",
        description="Agent Builder default model provider",
        type="system",
        base_url="https://example.invalid/v1",
        auth_type="api_key",
        doc_url="https://example.invalid/docs",
    )
    db_session.add(provider)
    db_session.flush()
    preferred_model = LLMModel(
        provider_id=provider.id,
        model_id_for_api_call="gpt-5.5",
        name="GPT-5.5",
        type="chat",
        context_window=8192,
        is_active=True,
    )
    pro_model = LLMModel(
        provider_id=provider.id,
        model_id_for_api_call="gpt-5.5-pro",
        name="GPT-5.5 Pro",
        type="chat",
        context_window=8192,
        is_active=True,
    )
    credential = LLMCredential(
        provider_id=provider.id,
        user_id=actor.id,
        organization_id=organization.id,
        credential_name="Agent Builder Default Model Credential",
        encrypted_config=json.dumps({}),
        config_preview=None,
        is_valid=True,
    )
    db_session.add_all([preferred_model, pro_model, credential])
    db_session.flush()
    db_session.add_all(
        [
            LLMRelCredentialModel(
                credential_id=credential.id,
                model_id=preferred_model.id,
                is_verified=True,
                priority=5,
            ),
            LLMRelCredentialModel(
                credential_id=credential.id,
                model_id=pro_model.id,
                is_verified=True,
                priority=0,
            ),
            UserLLMPermission(
                grantee_organization_id=organization.id,
                user_id=actor.id,
                llm_credential_id=credential.id,
                auth_state="operator",
                assigned_by=actor.id,
            ),
        ]
    )

    app = App(
        organization_id=organization.id,
        name="Agent Builder Default Model",
        url_slug=f"agent-builder-default-model-{uuid.uuid4().hex}",
        auth_secret="integration-placeholder",
        created_by=actor.id,
    )
    db_session.add(app)
    db_session.flush()
    original_workflow = Workflow(
        organization_id=organization.id,
        app_id=app.id,
        graph={"nodes": [], "edges": []},
        created_by=actor.id,
        updated_by=actor.id,
    )
    db_session.add(original_workflow)
    db_session.flush()
    app.workflow_id = original_workflow.id

    session = AgentBuilderSession(
        organization_id=organization.id,
        user_id=actor.id,
        workflow_id=original_workflow.id,
        app_id=app.id,
        status="active",
    )
    db_session.add(session)
    db_session.flush()

    runtime_calls = []

    class IntentClient:
        def invoke_sync(self, *args, **kwargs):
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "request_type": "new_workflow",
                                    "draft_mode": "new_workflow",
                                    "intent_summary": "입력을 LLM으로 요약",
                                    "ordered_capabilities": [
                                        "start_input",
                                        "llm",
                                        "answer",
                                    ],
                                }
                            )
                        }
                    }
                ]
            }

    def runtime_loader(**kwargs):
        runtime_calls.append(kwargs)
        return SimpleNamespace(client=IntentClient())

    intent_extractor = LLMAgentBuilderIntentExtractor(
        db=db_session,
        user_id=actor.id,
        organization_id=organization.id,
        credential_id=credential.id,
        model_id=pro_model.id,
        runtime_loader=runtime_loader,
        knowledge_context_loader=lambda **kwargs: [],
    )
    service = AgentBuilderService(
        db_session,
        user=actor,
        organization_id=organization.id,
        intent_extractor=intent_extractor,
    )
    structured = service._structure_request(  # noqa: SLF001
        AgentBuilderMessageRequest(message="새 워크플로우로 입력을 LLM으로 요약해줘"),
        workflow=None,
    )
    preview_graph = service._build_preview_graph(  # noqa: SLF001
        structured,
        workflow=None,
        kb_bindings=[],
    )
    generated_llm = next(
        node for node in preview_graph["nodes"] if node["type"] == "llmNode"
    )
    assert len(runtime_calls) == 1
    assert runtime_calls[0]["credential_id"] == credential.id
    assert runtime_calls[0]["model_id"] == pro_model.id
    assert generated_llm["data"]["model_id"] == "gpt-5.5"
    persisted_input = json.dumps(
        {
            "structured_request": structured.model_dump(mode="json"),
            "preview_graph": preview_graph,
        }
    )
    assert str(credential.id) not in persisted_input
    assert str(pro_model.id) not in persisted_input

    request = AgentBuilderRequest(
        session_id=session.id,
        organization_id=organization.id,
        user_id=actor.id,
        status="draft_ready",
        message_summary="Create a new LLM workflow",
        structured_request=structured.model_dump(mode="json"),
        response_payload={},
    )
    db_session.add(request)
    db_session.flush()
    draft = AgentBuilderDraft(
        request_id=request.id,
        session_id=session.id,
        organization_id=organization.id,
        user_id=actor.id,
        draft_mode="new_workflow",
        workflow_id=None,
        app_id=app.id,
        preview_graph=preview_graph,
        node_detail_previews=[],
        validation_result={"valid": True, "issues": []},
        draft_metadata={
            EXPECTED_APP_PRIMARY_WORKFLOW_ID: str(original_workflow.id),
            "workflow_scope": "new_workflow",
            "generated_node_ids": [node["id"] for node in preview_graph["nodes"]],
            "generated_edge_ids": [edge["id"] for edge in preview_graph["edges"]],
        },
        base_graph_hash=None,
        status="ready",
    )
    db_session.add(draft)
    db_session.flush()

    response = service.apply_draft(
        draft.id,
        AgentBuilderApplyRequest(
            action="apply_and_save",
            client_preview_graph_hash=calculate_graph_hash(preview_graph),
        ),
    )

    assert response.outcome == "saved"
    db_session.expire_all()
    saved_workflow = db_session.get(Workflow, response.saved_workflow_id)
    saved_llm = next(
        node for node in saved_workflow.graph["nodes"] if node["type"] == "llmNode"
    )
    assert saved_llm["data"]["model_id"] == "gpt-5.5"


def test_agent_builder_new_workflow_apply_rebinds_session_scope(db_session):
    actor = _user("new-workflow-session")
    db_session.add(actor)
    db_session.flush()
    organization = Organization(
        name=f"Agent Builder New Workflow {uuid.uuid4().hex}",
        created_by=actor.id,
        managed_by=actor.id,
    )
    db_session.add(organization)
    db_session.flush()
    db_session.add(
        OrganizationMembership(
            organization_id=organization.id,
            user_id=actor.id,
            membership_state="active",
            organization_auth_state="manager",
        )
    )
    app = App(
        organization_id=organization.id,
        name="Agent Builder New Workflow",
        url_slug=f"agent-builder-new-{uuid.uuid4().hex}",
        auth_secret="integration-placeholder",
        created_by=actor.id,
    )
    db_session.add(app)
    db_session.flush()
    original_workflow = Workflow(
        organization_id=organization.id,
        app_id=app.id,
        graph={"nodes": [], "edges": []},
        created_by=actor.id,
        updated_by=actor.id,
    )
    db_session.add(original_workflow)
    db_session.flush()
    app.workflow_id = original_workflow.id

    session = AgentBuilderSession(
        organization_id=organization.id,
        user_id=actor.id,
        workflow_id=original_workflow.id,
        app_id=app.id,
        status="active",
    )
    db_session.add(session)
    db_session.flush()
    request = AgentBuilderRequest(
        session_id=session.id,
        organization_id=organization.id,
        user_id=actor.id,
        status="draft_ready",
        message_summary="Create a new workflow",
        structured_request={},
        response_payload={},
    )
    db_session.add(request)
    db_session.flush()
    preview_graph = {
        "nodes": [
            {
                "id": "new-start",
                "type": "startNode",
                "position": {"x": 0, "y": 0},
                "data": {},
            },
            {
                "id": "new-answer",
                "type": "answerNode",
                "position": {"x": 0, "y": 0},
                "data": {},
            },
        ],
        "edges": [
            {
                "id": "edge-new-start-answer",
                "source": "new-start",
                "target": "new-answer",
            }
        ],
    }
    draft = AgentBuilderDraft(
        request_id=request.id,
        session_id=session.id,
        organization_id=organization.id,
        user_id=actor.id,
        draft_mode="new_workflow",
        workflow_id=None,
        app_id=app.id,
        preview_graph=preview_graph,
        node_detail_previews=[],
        validation_result={"valid": True, "issues": []},
        draft_metadata={
            EXPECTED_APP_PRIMARY_WORKFLOW_ID: str(original_workflow.id),
            "workflow_scope": "new_workflow",
            "generated_node_ids": ["new-start", "new-answer"],
            "generated_edge_ids": ["edge-new-start-answer"],
        },
        base_graph_hash=None,
        status="ready",
    )
    db_session.add(draft)
    db_session.flush()

    response = AgentBuilderService(
        db_session,
        user=actor,
        organization_id=organization.id,
    ).apply_draft(
        draft.id,
        AgentBuilderApplyRequest(
            action="apply_and_save",
            client_preview_graph_hash=calculate_graph_hash(preview_graph),
        ),
    )

    assert response.outcome == "saved"
    assert response.saved_workflow_id != original_workflow.id
    db_session.expire_all()
    rebound_session = db_session.get(AgentBuilderSession, session.id)
    assert rebound_session.workflow_id == response.saved_workflow_id
    assert rebound_session.app_id == app.id
