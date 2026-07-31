import copy
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from apps.gateway.application.agent_builder.intent_usage import (
    AgentBuilderIntentUsageRecordingError,
)
from apps.gateway.services import agent_builder_service as service_module
from apps.gateway.services.agent_builder_service import (
    AgentBuilderService,
    calculate_graph_hash,
)
from apps.gateway.services.knowledge_rag_recommendation_service import (
    knowledge_base_recommendation_handle,
)
from apps.gateway.application.agent_builder.graph_mutation_builder import (
    materialize_candidate_graph,
)
from apps.gateway.services.workflow_service import WorkflowService
from apps.shared.schemas.agent_builder import (
    AgentBuilderApplyRequest,
    AgentBuilderApplyResponse,
    AgentBuilderEditOperation,
    AgentBuilderEditTargetReference,
    AgentBuilderMessageRequest,
    AgentBuilderMessageResponse,
    AgentBuilderParameterGroup,
    AgentBuilderParameterTask,
    AgentBuilderPlannedStep,
    AgentBuilderStructuredRequest,
    GraphMutation,
)
from apps.shared.schemas.knowledge import (
    KnowledgeSelection,
    KnowledgeSelectionCollection,
    KnowledgeRAGRecommendation,
    KnowledgeRAGRecommendationProvenance,
    KnowledgeRAGRecommendationResponse,
    KnowledgeRAGRecommendationSummary,
    KnowledgeRAGRecommendedOptions,
)
from apps.shared.schemas.workflow import NodeSchema
from apps.workflow_engine.workflow.core.workflow_node_factory import NodeFactory


class FakeDb:
    def __init__(self):
        self.added = []
        self.commits = 0
        self.rollbacks = 0
        self.flushed = False
        self.refreshed = []
        self.query_result = None

    def add(self, row):
        self.added.append(row)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def flush(self):
        self.flushed = True

    def refresh(self, row):
        self.refreshed.append(row)

    def query(self, _model):
        return self.query_result


class FakeQuery:
    def __init__(self, result=None):
        self.result = result

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        return self.result


class UsageRecordingFailingIntentExtractor:
    def __init__(self):
        self.calls = []

    def extract(self, **kwargs):
        self.calls.append(kwargs)
        raise AgentBuilderIntentUsageRecordingError(
            "intent_usage_recording_failed"
        )


class UnexpectedIntentExtractor:
    def __init__(self):
        self.calls = []

    def extract(self, **kwargs):
        self.calls.append(kwargs)
        raise AssertionError("provider must not be called for a non-primary workflow")


def test_agent_builder_llm_preview_uses_default_rag_options():
    service = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )

    node = service._build_capability_preview_node(
        "knowledge_backed_llm",
        "llm-1",
        source_id="start-1",
        source_output_key="question",
        entry_id="start-1",
        entry_capability="start_input",
        model_id="gpt-5.4-mini",
        kb_refs=[{"id": str(uuid.uuid4()), "name": "사내 규정"}],
    )

    assert node["data"]["scoreThreshold"] == 0.3
    assert node["data"]["topK"] == 5


def test_submit_message_returns_safe_usage_recording_failure(monkeypatch):
    db = FakeDb()
    session_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    app_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    user = SimpleNamespace(id=uuid.uuid4())
    session = SimpleNamespace(
        id=session_id,
        workflow_id=workflow_id,
        app_id=app_id,
        status="active",
        protocol_version="direct_edit_v1",
        updated_at=None,
    )
    workflow = SimpleNamespace(
        id=workflow_id,
        app_id=app_id,
        graph={"nodes": [], "edges": []},
    )
    extractor = UsageRecordingFailingIntentExtractor()
    service = AgentBuilderService(
        db,
        user=user,
        organization_id=organization_id,
        intent_extractor=extractor,
    )

    def flush_with_generated_ids():
        db.flushed = True
        for row in db.added:
            if getattr(row, "id", None) is None:
                row.id = uuid.uuid4()

    db.flush = flush_with_generated_ids
    monkeypatch.setattr(service, "_session_or_404", lambda _: session)
    monkeypatch.setattr(service, "_lock_session_for_request", lambda value: value)
    monkeypatch.setattr(service, "_reject_if_pending", lambda _: None)
    monkeypatch.setattr(service, "_workflow_in_active_org", lambda _: workflow)
    monkeypatch.setattr(
        service,
        "_app_in_active_org",
        lambda _: SimpleNamespace(id=app_id, workflow_id=workflow_id),
    )
    monkeypatch.setattr(
        service,
        "_selected_knowledge_candidate_context",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        service_module,
        "ensure_workflow_permission",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        service_module,
        "add_action_audit",
        lambda *_args, **_kwargs: None,
    )

    response = service.submit_message(
        session_id,
        AgentBuilderMessageRequest(
            message="입력을 요약하는 워크플로를 만들어줘",
            workflow_id=workflow_id,
        ),
    )

    assert response.status == "failed"
    assert response.validation_result is not None
    assert [issue.code for issue in response.validation_result.issues] == [
        "INTENT_USAGE_RECORDING_FAILED"
    ]
    assert len(extractor.calls) == 1
    usage_context = extractor.calls[0]["usage_context"]
    assert usage_context.user_id == user.id
    assert usage_context.organization_id == organization_id
    assert usage_context.workflow_id == workflow_id
    assert usage_context.session_id == session_id
    assert usage_context.request_id == response.request_id
    assert "입력을 요약" not in str(response.model_dump(mode="json"))


@pytest.mark.parametrize("scope_mismatch", ["request_session", "app_primary"])
def test_submit_message_rejects_non_primary_usage_scope_before_provider(
    monkeypatch,
    scope_mismatch,
):
    db = FakeDb()
    app_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    session_workflow_id = uuid.uuid4()
    requested_workflow_id = (
        uuid.uuid4()
        if scope_mismatch == "request_session"
        else session_workflow_id
    )
    primary_workflow_id = (
        session_workflow_id
        if scope_mismatch == "request_session"
        else uuid.uuid4()
    )
    session = SimpleNamespace(
        id=uuid.uuid4(),
        workflow_id=session_workflow_id,
        app_id=app_id,
        status="active",
        protocol_version="direct_edit_v1",
        updated_at=None,
    )
    workflow = SimpleNamespace(
        id=requested_workflow_id,
        app_id=app_id,
        graph={"nodes": [], "edges": []},
    )
    extractor = UnexpectedIntentExtractor()
    service = AgentBuilderService(
        db,
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=organization_id,
        intent_extractor=extractor,
    )

    def flush_with_generated_ids():
        db.flushed = True
        for row in db.added:
            if getattr(row, "id", None) is None:
                row.id = uuid.uuid4()

    db.flush = flush_with_generated_ids
    monkeypatch.setattr(service, "_session_or_404", lambda _: session)
    monkeypatch.setattr(service, "_lock_session_for_request", lambda value: value)
    monkeypatch.setattr(service, "_reject_if_pending", lambda _: None)
    monkeypatch.setattr(service, "_workflow_in_active_org", lambda _: workflow)
    monkeypatch.setattr(
        service,
        "_app_in_active_org",
        lambda _: SimpleNamespace(id=app_id, workflow_id=primary_workflow_id),
    )
    monkeypatch.setattr(
        service,
        "_selected_knowledge_candidate_context",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        service_module,
        "ensure_workflow_permission",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        service_module,
        "add_action_audit",
        lambda *_args, **_kwargs: None,
    )

    response = service.submit_message(
        session.id,
        AgentBuilderMessageRequest(
            message="입력과 응답 노드를 만들어줘",
            workflow_id=requested_workflow_id,
        ),
    )

    assert response.status == "failed"
    assert [issue.code for issue in response.validation_result.issues] == [
        "INTENT_USAGE_RECORDING_FAILED"
    ]
    assert extractor.calls == []


def _ready_modify_draft(preview_graph, *, workflow_id=None):
    workflow_id = workflow_id or uuid.uuid4()
    return SimpleNamespace(
        id=uuid.uuid4(),
        request_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        draft_mode="modify_workflow",
        base_graph_hash=calculate_graph_hash(preview_graph),
        base_workflow_updated_at=None,
        preview_graph=preview_graph,
        status="ready",
        workflow_id=workflow_id,
        app_id=None,
        draft_metadata={"workflow_id": str(workflow_id)},
        expires_at=None,
    )


def test_agent_builder_graph_hash_ignores_viewport_and_ui_only_state():
    graph_a = {
        "nodes": [
            {
                "id": "start",
                "type": "startNode",
                "position": {"x": 1, "y": 1},
                "data": {"title": "입력", "selected": True},
            }
        ],
        "edges": [],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }
    graph_b = {
        "nodes": [
            {
                "id": "start",
                "type": "startNode",
                "position": {"x": 999, "y": 999},
                "data": {"title": "입력", "selected": True},
            }
        ],
        "edges": [],
        "viewport": {"x": 50, "y": 50, "zoom": 2},
    }

    assert calculate_graph_hash(graph_a) == calculate_graph_hash(graph_b)


def test_agent_builder_graph_hash_ignores_ui_only_data_values():
    graph_a = {
        "nodes": [
            {
                "id": "start",
                "type": "startNode",
                "data": {"title": "Input", "selected": True, "status": "active"},
            }
        ],
        "edges": [],
    }
    graph_b = {
        "nodes": [
            {
                "id": "start",
                "type": "startNode",
                "data": {"title": "Input", "selected": False, "status": "idle"},
            }
        ],
        "edges": [],
    }

    assert calculate_graph_hash(graph_a) == calculate_graph_hash(graph_b)


def test_agent_builder_graph_hash_ignores_edge_id():
    graph_a = {
        "nodes": [{"id": "start", "type": "startNode", "data": {}}],
        "edges": [
            {
                "id": "edge-1",
                "source": "start",
                "target": "answer",
                "sourceHandle": "source",
                "targetHandle": "target",
            }
        ],
    }
    graph_b = {
        "nodes": [{"id": "start", "type": "startNode", "data": {}}],
        "edges": [
            {
                "id": "edge-2",
                "source": "start",
                "target": "answer",
                "sourceHandle": "source",
                "targetHandle": "target",
            }
        ],
    }

    assert calculate_graph_hash(graph_a) == calculate_graph_hash(graph_b)


def test_agent_builder_graph_hash_ignores_note_nodes_and_edges():
    graph_a = {
        "nodes": [
            {"id": "start", "type": "startNode", "data": {"title": "Input"}},
        ],
        "edges": [],
    }
    graph_b = {
        "nodes": [
            {"id": "start", "type": "startNode", "data": {"title": "Input"}},
            {"id": "note-1", "type": "note", "data": {"text": "memo"}},
        ],
        "edges": [
            {"id": "note-edge", "source": "note-1", "target": "start"},
        ],
    }

    assert calculate_graph_hash(graph_a) == calculate_graph_hash(graph_b)


def test_agent_builder_graph_hash_changes_for_semantic_data():
    graph_a = {
        "nodes": [{"id": "start", "type": "startNode", "data": {"title": "A"}}],
        "edges": [],
    }
    graph_b = {
        "nodes": [{"id": "start", "type": "startNode", "data": {"title": "B"}}],
        "edges": [],
    }

    assert calculate_graph_hash(graph_a) != calculate_graph_hash(graph_b)


@pytest.mark.parametrize(
    "raw_graph_key",
    [
        "client_graph_snapshot",
        "graph",
        "nodes",
        "edges",
        "preview_graph",
        "previewGraph",
        "workflow_graph",
        "workflowGraph",
        "raw_graph",
        "rawGraph",
        "clientGraphSnapshot",
    ],
)
def test_agent_builder_message_request_rejects_raw_graph_payload(raw_graph_key):
    with pytest.raises(ValidationError):
        AgentBuilderMessageRequest(
            message="create a workflow",
            **{raw_graph_key: {"nodes": [{"data": {"token": "secret"}}]}},
        )


def test_agent_builder_preview_redacts_raw_kb_and_source_identifiers():
    raw_kb_id = str(uuid.uuid4())
    preview = service_module._redact_graph_for_preview(
        {
            "nodes": [
                {
                    "id": "llm",
                    "type": "llmNode",
                    "data": {
                        "knowledgeBases": [{"id": raw_kb_id, "name": "Private KB"}],
                        "url": "https://secret.example.com/source",
                    },
                }
            ],
            "edges": [],
        }
    )

    node_data = preview["nodes"][0]["data"]
    assert raw_kb_id not in str(node_data)
    assert "secret.example.com" not in str(node_data)
    assert (
        node_data["knowledgeBases"][0]["reference_type"]
        == "existing_redacted_reference"
    )
    assert "url" not in node_data


def test_agent_builder_saved_response_requires_audit_recorded():
    with pytest.raises(ValueError):
        AgentBuilderApplyResponse(
            apply_id=uuid.uuid4(),
            outcome="saved",
            audit_recorded=False,
        )


def test_finish_request_does_not_overwrite_canceled_request():
    request_id = uuid.uuid4()
    request_row = SimpleNamespace(
        id=request_id,
        status="canceled",
        response_payload={},
        completed_at=None,
    )
    response = AgentBuilderMessageResponse(
        request_id=request_id,
        status="draft_ready",
        preview_prompt="도안 생성 미리보기",
        warnings=["ready"],
    )
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )

    svc._finish_request(request_row, response)  # noqa: SLF001

    assert response.status == "canceled"
    assert response.draft_preview is None
    assert response.preview_prompt is None
    assert request_row.response_payload["status"] == "canceled"
    assert request_row.completed_at is not None


def test_finish_request_does_not_overwrite_processing_timeout_terminal_payload():
    request_id = uuid.uuid4()
    completed_at = datetime.now(timezone.utc)
    timeout_payload = {
        "request_id": str(request_id),
        "status": "failed",
        "warnings": ["processing timeout"],
        "validation_result": {
            "valid": False,
            "issues": [
                {
                    "code": "REQUEST_PROCESSING_TIMEOUT",
                    "message": "request processing timed out",
                }
            ],
        },
    }
    request_row = SimpleNamespace(
        id=request_id,
        status="failed",
        response_payload=copy.deepcopy(timeout_payload),
        completed_at=completed_at,
    )
    late_response = AgentBuilderMessageResponse(
        request_id=request_id,
        status="graph_mutation_ready",
        warnings=["late result"],
    )
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )

    svc._finish_request(request_row, late_response, direct_edit=True)  # noqa: SLF001

    assert request_row.status == "failed"
    assert request_row.response_payload == timeout_payload
    assert request_row.completed_at == completed_at
    assert late_response.status == "failed"
    assert late_response.warnings == ["processing timeout"]
    assert late_response.validation_result is not None
    assert late_response.validation_result.issues[0].code == "REQUEST_PROCESSING_TIMEOUT"


def test_finish_request_cancellation_persists_only_safe_mutation_envelope():
    request_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    request_row = SimpleNamespace(
        id=request_id,
        status="canceled",
        response_payload={},
        completed_at=None,
    )
    mutation = GraphMutation.model_validate(
        {
            "operation_id": uuid.uuid4(),
            "kind": "initial_graph",
            "generation_mode": "structure_only",
            "workflow_id": workflow_id,
            "base_graph_hash": "a" * 64,
            "expected_workflow_updated_at": datetime.now(timezone.utc),
            "expected_result_graph_hash": "b" * 64,
            "catalog_version": 3,
            "operations": [
                {
                    "op": "add_node",
                    "node": {
                        "id": "start",
                        "type": "startNode",
                        "position": {"x": 0, "y": 0},
                        "data": {},
                    },
                }
            ],
        }
    )
    response = AgentBuilderMessageResponse(
        request_id=request_id,
        status="graph_mutation_ready",
        graph_mutation=mutation,
    )
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )

    svc._finish_request(request_row, response, direct_edit=True)  # noqa: SLF001

    assert response.status == "canceled"
    assert "graph_mutation" not in request_row.response_payload
    assert request_row.response_payload["operation_envelopes"][0][
        "operation_id"
    ] == str(mutation.operation_id)
    assert "operations" not in request_row.response_payload["operation_envelopes"][0]


def test_safe_summary_redacts_secret_values_urls_and_paths():
    summary = service_module._safe_summary(  # noqa: SLF001
        "password=hunter2 api_key: sk-test-secret Bearer abcdefghijk "
        "Authorization: Bearer super-secret-bearer Authorization: Basic super-secret-basic "
        "https://secret.example.com/doc C:\\secret\\policy.pdf /srv/private/file.txt "
        "password is hunter2 토큰 값은 korean-secret-token"
    )

    assert "hunter2" not in summary
    assert "sk-test-secret" not in summary
    assert "abcdefghijk" not in summary
    assert "super-secret-bearer" not in summary
    assert "super-secret-basic" not in summary
    assert "korean-secret-token" not in summary
    assert "secret.example.com" not in summary
    assert "C:\\secret" not in summary
    assert "/srv/private" not in summary


def test_agent_builder_record_preview_opened_audits_success(monkeypatch):
    db = FakeDb()
    draft = SimpleNamespace(
        id=uuid.uuid4(),
        request_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        draft_mode="new_workflow",
        preview_graph={"nodes": [], "edges": []},
        status="ready",
        expires_at=None,
        validation_result={"valid": True},
    )
    audit_calls = []
    svc = AgentBuilderService(
        db,
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(svc, "_draft_or_404", lambda _draft_id: draft)
    monkeypatch.setattr(
        svc,
        "_session_or_404",
        lambda _session_id: SimpleNamespace(workflow_id=None, app_id=uuid.uuid4()),
    )
    monkeypatch.setattr(svc, "_session_scope_allowed", lambda _session: True)
    monkeypatch.setattr(
        service_module,
        "add_action_audit",
        lambda *args, **kwargs: audit_calls.append((args, kwargs)),
    )

    svc.record_preview_opened(draft.id)

    assert (
        audit_calls[0][0][1] == service_module.AuditAction.AGENT_BUILDER_PREVIEW_OPENED
    )
    assert db.commits == 1


def test_agent_builder_record_preview_opened_blocks_invalid_draft(monkeypatch):
    db = FakeDb()
    draft = SimpleNamespace(
        id=uuid.uuid4(),
        request_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        draft_mode="new_workflow",
        preview_graph={"nodes": [], "edges": []},
        status="ready",
        expires_at=None,
        validation_result={"valid": False},
    )
    audit_calls = []
    svc = AgentBuilderService(
        db,
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(svc, "_draft_or_404", lambda _draft_id: draft)
    monkeypatch.setattr(
        service_module,
        "add_action_audit",
        lambda *args, **kwargs: audit_calls.append((args, kwargs)),
    )

    with pytest.raises(service_module.HTTPException) as exc:
        svc.record_preview_opened(draft.id)

    assert exc.value.detail == "DRAFT_VALIDATION_FAILED"
    assert (
        audit_calls[0][0][1] == service_module.AuditAction.AGENT_BUILDER_PREVIEW_BLOCKED
    )
    assert audit_calls[0][1]["metadata"]["block_reason"] == "DRAFT_VALIDATION_FAILED"
    assert db.commits == 1


def test_create_session_uses_workflow_app_id_over_client_app_id(monkeypatch):
    db = FakeDb()
    db.query_result = FakeQuery(None)
    workflow_id = uuid.uuid4()
    workflow_app_id = uuid.uuid4()
    client_app_id = uuid.uuid4()
    workflow = SimpleNamespace(
        id=workflow_id,
        app_id=workflow_app_id,
        organization_id=uuid.uuid4(),
    )
    svc = AgentBuilderService(
        db,
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=workflow.organization_id,
    )
    monkeypatch.setattr(svc, "_workflow_in_active_org", lambda _workflow_id: workflow)
    monkeypatch.setattr(
        service_module, "ensure_workflow_permission", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(svc, "_session_response", lambda _session: SimpleNamespace())

    svc.create_or_restore_session(
        service_module.AgentBuilderSessionCreateRequest(
            workflow_id=workflow_id,
            app_id=client_app_id,
        )
    )

    session = db.added[0]
    assert session.workflow_id == workflow_id
    assert session.app_id == workflow_app_id


def test_structured_request_respects_explicit_new_workflow_intent_with_workflow_scope():
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    workflow = SimpleNamespace(id=uuid.uuid4(), graph={"nodes": [], "edges": []})

    structured = svc._build_structured_request(  # noqa: SLF001
        AgentBuilderMessageRequest(
            message="새 워크플로우로 휴가 정책 답변 로직을 만들어줘"
        ),
        workflow=workflow,
    )

    assert structured.request_type == "new_workflow"
    assert structured.draft_mode == "new_workflow"


def test_structured_request_defaults_to_new_workflow_without_targeted_insert():
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    workflow = SimpleNamespace(id=uuid.uuid4(), graph={"nodes": [], "edges": []})

    structured = svc._build_structured_request(  # noqa: SLF001
        AgentBuilderMessageRequest(
            message="입력값을 분석해서 답변하는 로직을 만들어줘"
        ),
        workflow=workflow,
    )

    assert structured.request_type == "new_workflow"
    assert structured.draft_mode == "new_workflow"


def test_structured_request_modifies_existing_workflow_only_for_targeted_insert():
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    workflow = SimpleNamespace(id=uuid.uuid4(), graph={"nodes": [], "edges": []})

    structured = svc._build_structured_request(  # noqa: SLF001
        AgentBuilderMessageRequest(
            message="이 연결 사이에 입력값을 분석하는 노드를 넣어줘",
            selected_edge_id="edge-1",
        ),
        workflow=workflow,
    )

    assert structured.request_type == "modify_workflow"
    assert structured.draft_mode == "modify_workflow"


def test_structured_request_keeps_existing_target_out_of_new_capabilities():
    workflow = SimpleNamespace(
        graph={
            "nodes": [
                {"id": "start", "type": "startNode", "data": {"title": "Start"}},
                {
                    "id": "github-read",
                    "type": "githubNode",
                    "data": {"title": "GitHub PR 조회", "action": "get_pr"},
                },
                {"id": "answer", "type": "answerNode", "data": {"title": "응답"}},
            ],
            "edges": [
                {"id": "edge-start-github", "source": "start", "target": "github-read"},
                {
                    "id": "edge-github-answer",
                    "source": "github-read",
                    "target": "answer",
                },
            ],
        }
    )
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )

    structured = svc._build_structured_request(  # noqa: SLF001
        AgentBuilderMessageRequest(
            message="github 노드 뒤에 LLM 노드를 추가해줘",
        ),
        workflow=workflow,
    )

    assert structured.request_type == "modify_workflow"
    assert structured.required_capabilities == ["llm"]
    assert [step.capability for step in structured.planned_steps] == ["llm"]
    assert len(structured.edit_operations) == 1
    operation = structured.edit_operations[0]
    assert operation.operation == "insert"
    assert operation.placement == "after"
    assert operation.step_refs == ["step_llm"]
    assert operation.target.node_types == ["githubNode"]


def test_named_existing_node_target_is_resolved_and_only_requested_node_is_spliced(
    monkeypatch,
):
    workflow = SimpleNamespace(
        graph={
            "nodes": [
                {"id": "start", "type": "startNode", "data": {"title": "Start"}},
                {
                    "id": "github-read",
                    "type": "githubNode",
                    "data": {"title": "GitHub PR 조회", "action": "get_pr"},
                },
                {"id": "answer", "type": "answerNode", "data": {"title": "응답"}},
            ],
            "edges": [
                {"id": "edge-start-github", "source": "start", "target": "github-read"},
                {
                    "id": "edge-github-answer",
                    "source": "github-read",
                    "target": "answer",
                },
            ],
        }
    )
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(svc, "_recommended_draft_model_id", lambda: "model-1")
    structured = svc._build_structured_request(  # noqa: SLF001
        AgentBuilderMessageRequest(message="github 노드 뒤에 LLM 노드를 추가해줘"),
        workflow=workflow,
    )

    resolution = svc._resolve_edit_target(  # noqa: SLF001
        structured,
        workflow=workflow,
        selected_node_id=None,
        selected_edge_id=None,
    )
    preview = svc._build_preview_graph(  # noqa: SLF001
        structured,
        workflow=workflow,
        kb_bindings=[],
        target_resolution=resolution,
    )

    assert resolution["status"] == "resolved"
    assert resolution["node_id"] == "github-read"
    generated_nodes = [
        node for node in preview["nodes"] if str(node["id"]).startswith("agent-")
    ]
    assert [node["type"] for node in generated_nodes] == ["llmNode"]
    assert generated_nodes[0]["data"]["citationDisplayMode"] == "detailed"
    assert not any(node["type"] == "startNode" for node in generated_nodes)
    assert not any(node["type"] == "answerNode" for node in generated_nodes)
    generated_llm_id = generated_nodes[0]["id"]
    edge_pairs = {(edge["source"], edge["target"]) for edge in preview["edges"]}
    assert ("github-read", "answer") not in edge_pairs
    assert ("github-read", generated_llm_id) in edge_pairs
    assert (generated_llm_id, "answer") in edge_pairs


def test_named_target_with_multiple_matching_nodes_requires_clarification():
    workflow = SimpleNamespace(
        graph={
            "nodes": [
                {
                    "id": "github-read-1",
                    "type": "githubNode",
                    "data": {"title": "GitHub PR 조회 1", "action": "get_pr"},
                },
                {
                    "id": "github-read-2",
                    "type": "githubNode",
                    "data": {"title": "GitHub PR 조회 2", "action": "get_pr"},
                },
            ],
            "edges": [],
        }
    )
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    structured = svc._build_structured_request(  # noqa: SLF001
        AgentBuilderMessageRequest(message="github 노드 뒤에 LLM 노드를 추가해줘"),
        workflow=workflow,
    )

    unresolved = svc._resolve_edit_target(  # noqa: SLF001
        structured,
        workflow=workflow,
        selected_node_id=None,
        selected_edge_id=None,
    )
    resolved = svc._resolve_edit_target(  # noqa: SLF001
        structured,
        workflow=workflow,
        selected_node_id="github-read-2",
        selected_edge_id=None,
    )

    assert unresolved["status"] == "clarification_required"
    assert [option["node_id"] for option in unresolved["options"]] == [
        "github-read-1",
        "github-read-2",
    ]
    assert resolved["status"] == "resolved"
    assert resolved["node_id"] == "github-read-2"


@pytest.mark.parametrize(
    ("target_label", "target_type"),
    [
        ("GitHub", "githubNode"),
        ("Slack", "slackPostNode"),
        ("LLM", "llmNode"),
        ("HTTP", "httpRequestNode"),
    ],
)
def test_named_target_resolution_uses_catalog_node_type_for_multiple_node_kinds(
    target_label,
    target_type,
):
    workflow = SimpleNamespace(
        graph={
            "nodes": [
                {
                    "id": "target",
                    "type": target_type,
                    "data": {"title": f"{target_label} 작업"},
                },
                {"id": "answer", "type": "answerNode", "data": {"title": "응답"}},
            ],
            "edges": [
                {"id": "edge-target-answer", "source": "target", "target": "answer"}
            ],
        }
    )
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    structured = svc._build_structured_request(  # noqa: SLF001
        AgentBuilderMessageRequest(
            message=f"{target_label} 노드 뒤에 LLM 노드를 추가해줘"
        ),
        workflow=workflow,
    )

    resolution = svc._resolve_edit_target(  # noqa: SLF001
        structured,
        workflow=workflow,
        selected_node_id=None,
        selected_edge_id=None,
    )

    assert structured.edit_operations[0].target.node_types == [target_type]
    assert resolution["status"] == "resolved"
    assert resolution["node_id"] == "target"


def test_modify_request_does_not_inject_unrequested_llm_before_new_slack_node():
    workflow = SimpleNamespace(
        graph={
            "nodes": [
                {"id": "llm", "type": "llmNode", "data": {"title": "LLM"}},
                {"id": "answer", "type": "answerNode", "data": {"title": "응답"}},
            ],
            "edges": [{"id": "edge-llm-answer", "source": "llm", "target": "answer"}],
        }
    )
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )

    structured = svc._build_structured_request(  # noqa: SLF001
        AgentBuilderMessageRequest(message="LLM 노드 뒤에 Slack 노드를 추가해줘"),
        workflow=workflow,
    )

    assert structured.required_capabilities == ["slack_send"]
    assert [step.capability for step in structured.planned_steps] == ["slack_send"]
    assert structured.edit_operations[0].target.node_types == ["llmNode"]


def test_modify_preview_rejects_generated_component_detached_from_existing_graph():
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    graph = {
        "nodes": [
            {"id": "existing", "type": "startNode", "data": {}},
            {
                "id": "agent-llm",
                "type": "llmNode",
                "data": {"model_id": "model-1"},
            },
        ],
        "edges": [],
    }

    validation = svc.validate_preview_graph(
        graph,
        generated_node_ids={"agent-llm"},
    )

    assert validation.valid is False
    assert "DETACHED_GENERATED_COMPONENT" in {issue.code for issue in validation.issues}


@pytest.mark.parametrize(
    ("nodes", "edge", "expected_code"),
    [
        (
            [
                {"id": "llm", "type": "llmNode", "data": {}},
                {"id": "start", "type": "startNode", "data": {}},
            ],
            {"id": "bad", "source": "llm", "target": "start"},
            "START_NODE_HAS_INCOMING_EDGE",
        ),
        (
            [
                {"id": "llm", "type": "llmNode", "data": {}},
                {"id": "webhook", "type": "webhookTrigger", "data": {}},
            ],
            {"id": "bad", "source": "llm", "target": "webhook"},
            "TRIGGER_NODE_HAS_INCOMING_EDGE",
        ),
        (
            [
                {"id": "answer", "type": "answerNode", "data": {}},
                {"id": "llm", "type": "llmNode", "data": {}},
            ],
            {"id": "bad", "source": "answer", "target": "llm"},
            "TERMINAL_NODE_HAS_OUTGOING_EDGE",
        ),
        (
            [
                {
                    "id": "condition",
                    "type": "conditionNode",
                    "data": {"cases": [{"id": "case-1"}]},
                },
                {"id": "llm", "type": "llmNode", "data": {}},
            ],
            {
                "id": "bad",
                "source": "condition",
                "sourceHandle": "missing-case",
                "target": "llm",
            },
            "INVALID_CONDITION_SOURCE_HANDLE",
        ),
    ],
)
def test_agent_builder_backend_rejects_invalid_connection_policy(
    nodes, edge, expected_code
):
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )

    result = svc.validate_preview_graph({"nodes": nodes, "edges": [edge]})

    assert result.valid is False
    assert expected_code in {issue.code for issue in result.issues}


def test_modify_request_inserts_answer_node_after_existing_llm():
    workflow = SimpleNamespace(
        graph={
            "nodes": [
                {
                    "id": "llm-review",
                    "type": "llmNode",
                    "data": {"title": "LLM", "model_id": "model-1"},
                },
                {
                    "id": "github-comment",
                    "type": "githubNode",
                    "data": {
                        "title": "GitHub PR 댓글 등록",
                        "action": "comment_pr",
                    },
                },
            ],
            "edges": [
                {
                    "id": "edge-llm-comment",
                    "source": "llm-review",
                    "target": "github-comment",
                }
            ],
        }
    )
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )

    structured = svc._build_structured_request(  # noqa: SLF001
        AgentBuilderMessageRequest(message="llm 뒤에 응답 노드 하나 추가"),
        workflow=workflow,
    )
    resolution = svc._resolve_edit_target(  # noqa: SLF001
        structured,
        workflow=workflow,
        selected_node_id=None,
        selected_edge_id=None,
    )
    preview = svc._build_preview_graph(  # noqa: SLF001
        structured,
        workflow=workflow,
        kb_bindings=[],
        target_resolution=resolution,
    )

    assert structured.required_capabilities == ["answer"]
    assert [step.capability for step in structured.planned_steps] == ["answer"]
    assert resolution["status"] == "resolved"
    generated_nodes = [
        node for node in preview["nodes"] if str(node["id"]).startswith("agent-")
    ]
    assert [node["type"] for node in generated_nodes] == ["answerNode"]
    generated_answer_id = generated_nodes[0]["id"]
    edge_pairs = {(edge["source"], edge["target"]) for edge in preview["edges"]}
    assert ("llm-review", "github-comment") not in edge_pairs
    assert ("llm-review", generated_answer_id) in edge_pairs
    assert (generated_answer_id, "github-comment") in edge_pairs


def test_modify_request_with_unknown_new_node_reports_missing_capability_not_target():
    workflow = SimpleNamespace(
        graph={
            "nodes": [
                {
                    "id": "llm-review",
                    "type": "llmNode",
                    "data": {"title": "LLM", "model_id": "model-1"},
                }
            ],
            "edges": [],
        }
    )
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )

    structured = svc._build_structured_request(  # noqa: SLF001
        AgentBuilderMessageRequest(message="llm 뒤에 알 수 없는 노드 추가"),
        workflow=workflow,
    )
    resolution = svc._resolve_edit_target(  # noqa: SLF001
        structured,
        workflow=workflow,
        selected_node_id=None,
        selected_edge_id=None,
    )

    assert structured.missing_information == [
        "새로 추가할 node capability를 지정해주세요."
    ]
    assert resolution["status"] == "not_required"


def test_structured_request_rejects_non_workflow_message_with_hints():
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )

    structured = svc._build_structured_request(  # noqa: SLF001
        AgentBuilderMessageRequest(message="h"),
        workflow=None,
    )
    validation = svc._validate_structured_request(  # noqa: SLF001
        structured,
        app_id=uuid.uuid4(),
    )

    assert structured.request_type == "unsupported"
    assert structured.planned_steps == []
    assert structured.unsupported_requests
    assert validation.valid is False
    assert validation.issues[0].code == "UNSUPPORTED_REQUEST"


def test_structured_request_rejects_guardrail_node_in_mvp():
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )

    structured = svc._build_structured_request(  # noqa: SLF001
        AgentBuilderMessageRequest(
            message="입력값을 검사하는 Guardrail 노드를 추가해줘"
        ),
        workflow=None,
    )
    validation = svc._validate_structured_request(  # noqa: SLF001
        structured,
        app_id=uuid.uuid4(),
    )

    assert structured.request_type == "unsupported"
    assert structured.planned_steps == []
    assert "unsupported_capability" in structured.risk_flags
    assert validation.valid is False
    assert validation.issues[0].code == "UNSUPPORTED_REQUEST"


def test_structured_request_builds_durable_gmail_reply_draft_flow(monkeypatch):
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(svc, "_recommended_draft_model_id", lambda: "model-1")

    structured = svc._build_structured_request(  # noqa: SLF001
        AgentBuilderMessageRequest(
            message="읽지 않은 메일을 분석해서 Gmail 답장 초안을 만드는 워크플로우를 생성해줘"
        ),
        workflow=None,
    )
    preview = svc._build_preview_graph(  # noqa: SLF001
        structured,
        workflow=None,
        kb_bindings=[],
    )

    nodes_by_type = {node["type"]: node for node in preview["nodes"]}
    assert "answerNode" not in nodes_by_type
    assert nodes_by_type["mailNode"]["data"]["processing_mode"] == "durable"
    assert nodes_by_type["mailNode"]["data"]["max_results"] == 1
    assert nodes_by_type["gmailDraftNode"]["data"] == {
        "title": "Gmail 답장 초안",
        "credential_id": None,
        "configuration_state": "unresolved",
        "processing_ref_selector": [nodes_by_type["mailNode"]["id"], "processing_ref"],
        "reply_body_selector": [nodes_by_type["llmNode"]["id"], "text"],
    }
    assert nodes_by_type["mailAcknowledgeNode"]["data"] == {
        "title": "메일 처리 완료",
        "processing_ref_selector": [nodes_by_type["mailNode"]["id"], "processing_ref"],
        "required_effect_ref_selectors": [
            [nodes_by_type["gmailDraftNode"]["id"], "draft_ref"]
        ],
    }
    WorkflowService.validate_external_node_storage_boundaries(
        materialize_candidate_graph(preview),
        require_resolved=False,
    )


def test_gmail_acknowledgement_keeps_draft_effect_selector_after_other_steps(
    monkeypatch,
):
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(svc, "_recommended_draft_model_id", lambda: "model-1")
    structured = service_module.AgentBuilderStructuredRequest(
        request_type="new_workflow",
        draft_mode="new_workflow",
        intent_summary="메일 초안과 알림",
        required_capabilities=[
            "mail_search",
            "llm",
            "gmail_reply_draft_create",
            "slack_send",
            "mail_terminal_acknowledgement",
        ],
    )

    preview = svc._build_preview_graph(  # noqa: SLF001
        structured,
        workflow=None,
        kb_bindings=[],
    )

    nodes_by_type = {node["type"]: node for node in preview["nodes"]}
    assert nodes_by_type["mailAcknowledgeNode"]["data"][
        "required_effect_ref_selectors"
    ] == [[nodes_by_type["gmailDraftNode"]["id"], "draft_ref"]]


def test_intent_extraction_normalizes_gmail_draft_dependencies_and_terminal_order():
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    extraction = service_module.AgentBuilderIntentExtraction(
        request_type="new_workflow",
        draft_mode="new_workflow",
        intent_summary="메일 답장 초안 자동화",
        ordered_capabilities=[
            "start_input",
            "mail_terminal_acknowledgement",
            "gmail_reply_draft_create",
            "answer",
        ],
    )

    structured = svc._normalize_intent_extraction(  # noqa: SLF001
        extraction,
        request=AgentBuilderMessageRequest(
            message="메일을 분석해서 Gmail 답장 초안을 만드는 워크플로우를 생성해줘"
        ),
        workflow=None,
    )

    assert [step.capability for step in structured.planned_steps] == [
        "start_input",
        "mail_search",
        "llm",
        "gmail_reply_draft_create",
        "mail_terminal_acknowledgement",
    ]


def test_intent_extraction_rejects_acknowledgement_without_draft():
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    extraction = service_module.AgentBuilderIntentExtraction(
        request_type="new_workflow",
        draft_mode="new_workflow",
        intent_summary="메일 처리 완료",
        ordered_capabilities=[
            "start_input",
            "mail_terminal_acknowledgement",
            "answer",
        ],
    )

    structured = svc._normalize_intent_extraction(  # noqa: SLF001
        extraction,
        request=AgentBuilderMessageRequest(
            message="메일 처리 완료 노드를 만드는 워크플로우를 생성해줘"
        ),
        workflow=None,
    )

    assert structured.request_type == "unsupported"


def test_structured_request_rejects_mail_send_intent():
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )

    structured = svc._build_structured_request(  # noqa: SLF001
        AgentBuilderMessageRequest(
            message="고객에게 이메일을 보내는 워크플로우를 만들어줘"
        ),
        workflow=None,
    )

    assert structured.request_type == "unsupported"
    assert structured.risk_flags == ["unsupported_capability"]
    assert "메일 발송은 지원하지 않습니다" in structured.unsupported_requests[0]


def test_input_output_request_builds_without_llm_model_recommendation():
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )

    structured = svc._build_structured_request(  # noqa: SLF001
        AgentBuilderMessageRequest(message="입력 - 출력 노드를 만들어줘"),
        workflow=None,
    )
    preview_graph = svc._build_preview_graph(  # noqa: SLF001
        structured,
        workflow=None,
        kb_bindings=[],
    )

    assert structured.request_type == "new_workflow"
    assert "llm" not in structured.required_capabilities
    assert [node["type"] for node in preview_graph["nodes"]] == [
        "startNode",
        "answerNode",
    ]
    assert preview_graph["edges"][0]["source"].startswith("agent-input")
    assert preview_graph["edges"][0]["target"].startswith("agent-answer")


def test_webhook_request_builds_webhook_trigger_preview(monkeypatch):
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(svc, "_recommended_draft_model_id", lambda: "model-1")

    structured = svc._build_structured_request(  # noqa: SLF001
        AgentBuilderMessageRequest(
            message="웹훅으로 받는 사내 문서 챗봇 워크플로우를 만들어줘"
        ),
        workflow=None,
    )
    preview_graph = svc._build_preview_graph(  # noqa: SLF001
        structured,
        workflow=None,
        kb_bindings=[],
    )

    nodes_by_type = {node["type"]: node for node in preview_graph["nodes"]}
    assert structured.planned_steps[0].capability == "webhook_trigger"
    assert "webhook_trigger" in structured.required_capabilities
    assert "webhookTrigger" in nodes_by_type
    webhook_node = nodes_by_type["webhookTrigger"]
    assert webhook_node["data"]["variable_mappings"] == [
        {"variable_name": "payload", "json_path": "$"}
    ]
    llm_node = nodes_by_type["llmNode"]
    assert llm_node["data"]["user_prompt"] == "{{payload}}"
    assert llm_node["data"]["referenced_variables"] == [
        {"name": "payload", "value_selector": [webhook_node["id"], "payload"]}
    ]
    assert svc.validate_preview_graph(preview_graph).valid is True


def test_file_extraction_downstream_uses_catalog_declared_dynamic_output(monkeypatch):
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(svc, "_recommended_draft_model_id", lambda: "model-1")
    structured = service_module.AgentBuilderStructuredRequest.model_validate(
        {
            "request_type": "new_workflow",
            "draft_mode": "new_workflow",
            "intent_summary": "파일을 추출하고 요약",
            "planned_steps": [
                {"step_id": "input", "capability": "start_input", "purpose": "입력"},
                {
                    "step_id": "extract",
                    "capability": "file_extraction",
                    "purpose": "파일 추출",
                    "depends_on": ["input"],
                },
                {
                    "step_id": "llm",
                    "capability": "llm",
                    "purpose": "요약",
                    "depends_on": ["extract"],
                },
            ],
            "required_capabilities": ["start_input", "file_extraction", "llm"],
        }
    )

    graph = svc._build_preview_graph(structured, workflow=None, kb_bindings=[])

    file_node = next(node for node in graph["nodes"] if node["type"] == "fileExtractionNode")
    llm_node = next(node for node in graph["nodes"] if node["type"] == "llmNode")
    assert file_node["data"]["referenced_variables"][0]["name"] == "file"
    assert llm_node["data"]["referenced_variables"] == [
        {"name": "file", "value_selector": [file_node["id"], "file"]}
    ]
    assert "text" not in str(llm_node["data"]["referenced_variables"])


def test_unconfigured_dynamic_output_is_not_guessed_for_downstream_node(monkeypatch):
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(svc, "_recommended_draft_model_id", lambda: "model-1")
    structured = service_module.AgentBuilderStructuredRequest.model_validate(
        {
            "request_type": "new_workflow",
            "draft_mode": "new_workflow",
            "intent_summary": "변수를 추출하고 요약",
            "planned_steps": [
                {"step_id": "input", "capability": "start_input", "purpose": "입력"},
                {
                    "step_id": "extract",
                    "capability": "variable_extraction",
                    "purpose": "변수 추출",
                    "depends_on": ["input"],
                },
                {
                    "step_id": "llm",
                    "capability": "llm",
                    "purpose": "요약",
                    "depends_on": ["extract"],
                },
            ],
            "required_capabilities": ["start_input", "variable_extraction", "llm"],
        }
    )

    graph = svc._build_preview_graph(structured, workflow=None, kb_bindings=[])

    llm_node = next(node for node in graph["nodes"] if node["type"] == "llmNode")
    assert llm_node["data"]["referenced_variables"] == []
    assert "result" not in llm_node["data"]["user_prompt"]


def test_new_workflow_edges_follow_structured_step_dependencies(monkeypatch):
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(svc, "_recommended_draft_model_id", lambda: "model-1")
    structured = service_module.AgentBuilderStructuredRequest.model_validate(
        {
            "request_type": "new_workflow",
            "draft_mode": "new_workflow",
            "intent_summary": "두 경로를 합쳐 전송",
            "planned_steps": [
                {"step_id": "input", "capability": "start_input", "purpose": "입력"},
                {
                    "step_id": "llm",
                    "capability": "llm",
                    "purpose": "분석",
                    "depends_on": ["input"],
                },
                {
                    "step_id": "template",
                    "capability": "template_render",
                    "purpose": "서식",
                    "depends_on": ["input"],
                },
                {
                    "step_id": "slack",
                    "capability": "slack_send",
                    "purpose": "전송",
                    "depends_on": ["llm", "template"],
                },
            ],
            "required_capabilities": ["start_input", "llm", "template_render", "slack_send"],
        }
    )

    graph = svc._build_preview_graph(structured, workflow=None, kb_bindings=[])

    by_type = {node["type"]: node["id"] for node in graph["nodes"]}
    pairs = {(edge["source"], edge["target"]) for edge in graph["edges"]}
    assert (by_type["startNode"], by_type["llmNode"]) in pairs
    assert (by_type["startNode"], by_type["templateNode"]) in pairs
    assert (by_type["llmNode"], by_type["slackPostNode"]) in pairs
    assert (by_type["templateNode"], by_type["slackPostNode"]) in pairs
    assert (by_type["llmNode"], by_type["templateNode"]) not in pairs


def test_github_pr_review_request_builds_read_review_and_comment_nodes(monkeypatch):
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(svc, "_recommended_draft_model_id", lambda: "model-1")

    structured = svc._build_structured_request(  # noqa: SLF001
        AgentBuilderMessageRequest(
            message="웹훅 노드로 받아서 PR 리뷰를 깃허브에 올려주는 워크플로우를 만들어줘"
        ),
        workflow=None,
    )
    preview_graph = svc._build_preview_graph(  # noqa: SLF001
        structured,
        workflow=None,
        kb_bindings=[],
    )

    assert "github_pr_read" in structured.required_capabilities
    assert "github_pr_comment" in structured.required_capabilities
    node_types = [node["type"] for node in preview_graph["nodes"]]
    assert node_types == [
        "webhookTrigger",
        "githubNode",
        "llmNode",
        "githubNode",
        "answerNode",
    ]
    github_nodes = [
        node for node in preview_graph["nodes"] if node["type"] == "githubNode"
    ]
    assert [node["data"]["action"] for node in github_nodes] == [
        "get_pr",
        "comment_pr",
    ]
    assert all(node["data"]["api_token"] == "" for node in github_nodes)
    assert all(
        node["data"]["configuration_state"] == "unresolved" for node in github_nodes
    )
    configuration_issues = svc._node_configuration_issues(  # noqa: SLF001
        preview_graph
    )
    assert [issue.node_label for issue in configuration_issues] == [
        "GitHub PR 조회",
        "GitHub PR 댓글 등록",
    ]
    assert [issue.capability for issue in configuration_issues] == [
        "github_pr_read",
        "github_pr_comment",
    ]
    assert [
        [parameter.label for parameter in issue.missing_parameters]
        for issue in configuration_issues
        ] == [
            ["GitHub API Token", "PR 번호"],
            ["GitHub API Token", "PR 번호"],
    ]
    assert svc.validate_preview_graph(preview_graph).valid is True


def test_explicit_new_workflow_supports_github_comment_registration_phrase(
    monkeypatch,
):
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(svc, "_recommended_draft_model_id", lambda: "model-1")

    structured = svc._build_structured_request(  # noqa: SLF001
        AgentBuilderMessageRequest(
            message=(
                "새 워크플로우로 웹훅에서 요청을 받고 GitHub PR을 조회한 뒤 "
                "LLM으로 리뷰해서 GitHub PR에 댓글을 등록해줘"
            )
        ),
        workflow=None,
    )
    preview_graph = svc._build_preview_graph(  # noqa: SLF001
        structured,
        workflow=None,
        kb_bindings=[],
    )

    assert structured.request_type == "new_workflow"
    assert structured.required_capabilities == [
        "webhook_trigger",
        "github_pr_read",
        "llm",
        "github_pr_comment",
        "answer",
    ]
    assert [node["type"] for node in preview_graph["nodes"]] == [
        "webhookTrigger",
        "githubNode",
        "llmNode",
        "githubNode",
        "answerNode",
    ]
    assert [
        node["data"]["action"]
        for node in preview_graph["nodes"]
        if node["type"] == "githubNode"
    ] == ["get_pr", "comment_pr"]


def test_github_llm_review_without_comment_intent_does_not_add_comment_node(
    monkeypatch,
):
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(svc, "_recommended_draft_model_id", lambda: "model-1")

    structured = svc._build_structured_request(  # noqa: SLF001
        AgentBuilderMessageRequest(
            message="새 워크플로우로 GitHub PR을 조회한 뒤 LLM으로 리뷰해줘"
        ),
        workflow=None,
    )
    preview_graph = svc._build_preview_graph(  # noqa: SLF001
        structured,
        workflow=None,
        kb_bindings=[],
    )

    assert structured.request_type == "new_workflow"
    assert "github_pr_read" in structured.required_capabilities
    assert "llm" in structured.required_capabilities
    assert "github_pr_comment" not in structured.required_capabilities
    github_nodes = [
        node for node in preview_graph["nodes"] if node["type"] == "githubNode"
    ]
    assert [node["data"]["action"] for node in github_nodes] == ["get_pr"]


def test_spaced_korean_webhook_and_explicit_github_comment_builds_two_github_nodes(
    monkeypatch,
):
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(svc, "_recommended_draft_model_id", lambda: "model-1")

    structured = svc._build_structured_request(  # noqa: SLF001
        AgentBuilderMessageRequest(
            message="웹 훅으로 받고 깃허브에서 PR을 받고 분석해서 깃허브 PR에 리뷰 댓글을 올리는 노드를 생성해줘"
        ),
        workflow=None,
    )
    preview_graph = svc._build_preview_graph(  # noqa: SLF001
        structured,
        workflow=None,
        kb_bindings=[],
    )

    assert structured.required_capabilities == [
        "webhook_trigger",
        "github_pr_read",
        "llm",
        "github_pr_comment",
        "answer",
    ]
    assert [node["type"] for node in preview_graph["nodes"]] == [
        "webhookTrigger",
        "githubNode",
        "llmNode",
        "githubNode",
        "answerNode",
    ]


def test_submit_message_preserves_explicit_github_comment_capabilities(
    monkeypatch,
):
    db = FakeDb()
    session_id = uuid.uuid4()
    app_id = uuid.uuid4()
    session = SimpleNamespace(
        id=session_id,
        workflow_id=None,
        app_id=app_id,
        status="active",
        updated_at=None,
    )
    svc = AgentBuilderService(
        db,
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(svc, "_structure_request", svc._build_structured_request)

    def flush_with_generated_ids():
        db.flushed = True
        for row in db.added:
            if getattr(row, "id", None) is None:
                row.id = uuid.uuid4()

    db.flush = flush_with_generated_ids
    monkeypatch.setattr(svc, "_session_or_404", lambda _: session)
    monkeypatch.setattr(svc, "_lock_session_for_request", lambda value: value)
    monkeypatch.setattr(svc, "_reject_if_pending", lambda _: None)
    monkeypatch.setattr(
        svc,
        "_app_in_active_org",
        lambda _: SimpleNamespace(id=app_id),
    )
    monkeypatch.setattr(svc, "_recommended_draft_model_id", lambda: "model-1")
    monkeypatch.setattr(
        service_module.AppService,
        "access_denial_status",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        service_module, "add_action_audit", lambda *args, **kwargs: None
    )

    response = svc.submit_message(
        session_id,
        AgentBuilderMessageRequest(
            message="웹 훅으로 받고 깃허브에서 PR을 받고 분석해서 깃허브 PR에 리뷰 댓글을 올리는 노드를 생성해줘"
        ),
    )

    assert response.status == "draft_ready"
    assert response.structured_request is not None
    assert response.structured_request.required_capabilities == [
        "webhook_trigger",
        "github_pr_read",
        "llm",
        "github_pr_comment",
        "answer",
    ]
    assert response.draft_preview is not None
    preview_nodes = response.draft_preview.preview_graph["nodes"]
    assert [node["type"] for node in preview_nodes] == [
        "webhookTrigger",
        "githubNode",
        "llmNode",
        "githubNode",
        "answerNode",
    ]
    assert [
        node["data"]["action"] for node in preview_nodes if node["type"] == "githubNode"
    ] == ["get_pr", "comment_pr"]


def test_submit_message_app_scope_denial_marks_permission_audit_recorded(
    monkeypatch,
):
    db = FakeDb()
    session_id = uuid.uuid4()
    app_id = uuid.uuid4()
    user = SimpleNamespace(id=uuid.uuid4())
    organization_id = uuid.uuid4()
    session = SimpleNamespace(
        id=session_id,
        workflow_id=None,
        app_id=app_id,
        status="active",
        updated_at=None,
    )
    svc = AgentBuilderService(
        db,
        user=user,
        organization_id=organization_id,
    )
    audits = []
    monkeypatch.setattr(svc, "_session_or_404", lambda _: session)
    monkeypatch.setattr(svc, "_lock_session_for_request", lambda value: value)
    monkeypatch.setattr(svc, "_reject_if_pending", lambda _: None)
    monkeypatch.setattr(
        svc,
        "_app_in_active_org",
        lambda _: SimpleNamespace(id=app_id),
    )
    monkeypatch.setattr(
        service_module.AppService,
        "access_denial_status",
        lambda *args, **kwargs: 403,
    )
    monkeypatch.setattr(
        service_module,
        "record_resource_permission_denied",
        lambda **event: audits.append(event),
    )

    with pytest.raises(HTTPException) as denied:
        svc.submit_message(
            session_id,
            AgentBuilderMessageRequest(message="새 workflow를 만들어줘"),
        )

    assert denied.value.status_code == 403
    assert getattr(denied.value, "audit_recorded", False) is True
    assert len(audits) == 1
    assert audits[0]["user_id"] == user.id
    assert audits[0]["resource_type"] == "app"
    assert audits[0]["resource_id"] == app_id
    assert audits[0]["organization_id"] == organization_id


@pytest.mark.parametrize(
    ("message", "expected_node_types"),
    [
        (
            "입력 출력 노드 생성해줘",
            ["startNode", "answerNode"],
        ),
        (
            "새 워크플로우로 schedule trigger에서 시작해서 REST API를 호출하고 "
            "LLM으로 결과를 요약한 뒤 Slack으로 보내줘",
            [
                "scheduleTrigger",
                "httpRequestNode",
                "llmNode",
                "slackPostNode",
                "answerNode",
            ],
        ),
    ],
)
def test_submit_message_allows_new_workflow_draft_from_existing_workflow_context(
    monkeypatch,
    message,
    expected_node_types,
):
    db = FakeDb()
    session_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    app_id = uuid.uuid4()
    workflow = SimpleNamespace(
        id=workflow_id,
        app_id=app_id,
        updated_at=None,
        graph={
            "nodes": [
                {
                    "id": "existing-start",
                    "type": "startNode",
                    "data": {"title": "Existing Start"},
                },
                {
                    "id": "existing-answer",
                    "type": "answerNode",
                    "data": {"title": "Existing Answer"},
                },
            ],
            "edges": [
                {
                    "id": "existing-edge",
                    "source": "existing-start",
                    "target": "existing-answer",
                }
            ],
        },
    )
    session = SimpleNamespace(
        id=session_id,
        workflow_id=workflow_id,
        app_id=app_id,
        status="active",
        updated_at=None,
    )
    svc = AgentBuilderService(
        db,
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(svc, "_structure_request", svc._build_structured_request)

    def flush_with_generated_ids():
        db.flushed = True
        for row in db.added:
            if getattr(row, "id", None) is None:
                row.id = uuid.uuid4()

    db.flush = flush_with_generated_ids
    monkeypatch.setattr(svc, "_session_or_404", lambda _: session)
    monkeypatch.setattr(svc, "_lock_session_for_request", lambda value: value)
    monkeypatch.setattr(svc, "_reject_if_pending", lambda _: None)
    monkeypatch.setattr(svc, "_workflow_in_active_org", lambda _: workflow)
    monkeypatch.setattr(
        svc,
        "_app_in_active_org",
        lambda _: SimpleNamespace(id=app_id, workflow_id=workflow_id),
    )
    monkeypatch.setattr(svc, "_recommended_draft_model_id", lambda: "model-1")
    monkeypatch.setattr(
        service_module, "ensure_workflow_permission", lambda *args: None
    )
    monkeypatch.setattr(
        service_module, "add_action_audit", lambda *args, **kwargs: None
    )

    response = svc.submit_message(
        session_id,
        AgentBuilderMessageRequest(message=message, workflow_id=workflow_id),
    )

    assert response.status == "draft_ready"
    assert response.structured_request is not None
    assert response.structured_request.draft_mode == "new_workflow"
    assert response.draft_preview is not None
    assert [
        node["type"] for node in response.draft_preview.preview_graph["nodes"]
    ] == expected_node_types
    draft = next(row for row in db.added if hasattr(row, "draft_metadata"))
    assert draft.workflow_id is None
    assert draft.base_workflow_updated_at is None
    assert draft.draft_metadata["workflow_id"] is None
    assert draft.draft_metadata["workflow_scope"] == "new_workflow"
    assert draft.draft_metadata[service_module.EXPECTED_APP_PRIMARY_WORKFLOW_ID] == str(
        workflow_id
    )


def test_submit_message_splices_named_existing_target_and_apply_removes_old_edge(
    monkeypatch,
):
    db = FakeDb()
    session_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    app_id = uuid.uuid4()
    workflow = SimpleNamespace(
        id=workflow_id,
        app_id=app_id,
        updated_at=None,
        graph={
            "nodes": [
                {"id": "start", "type": "startNode", "data": {"title": "Start"}},
                {
                    "id": "github-read",
                    "type": "githubNode",
                    "data": {"title": "GitHub PR 조회", "action": "get_pr"},
                },
                {"id": "answer", "type": "answerNode", "data": {"title": "응답"}},
            ],
            "edges": [
                {"id": "edge-start-github", "source": "start", "target": "github-read"},
                {
                    "id": "edge-github-answer",
                    "source": "github-read",
                    "target": "answer",
                },
            ],
        },
    )
    session = SimpleNamespace(
        id=session_id,
        workflow_id=workflow_id,
        app_id=app_id,
        status="active",
        updated_at=None,
    )
    svc = AgentBuilderService(
        db,
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(svc, "_structure_request", svc._build_structured_request)

    def flush_with_generated_ids():
        db.flushed = True
        for row in db.added:
            if getattr(row, "id", None) is None:
                row.id = uuid.uuid4()

    db.flush = flush_with_generated_ids
    monkeypatch.setattr(svc, "_session_or_404", lambda _: session)
    monkeypatch.setattr(svc, "_lock_session_for_request", lambda value: value)
    monkeypatch.setattr(svc, "_reject_if_pending", lambda _: None)
    monkeypatch.setattr(svc, "_workflow_in_active_org", lambda _: workflow)
    monkeypatch.setattr(
        svc,
        "_app_in_active_org",
        lambda _: SimpleNamespace(id=app_id),
    )
    monkeypatch.setattr(svc, "_recommended_draft_model_id", lambda: "model-1")
    monkeypatch.setattr(
        service_module, "ensure_workflow_permission", lambda *args: None
    )
    monkeypatch.setattr(
        service_module, "add_action_audit", lambda *args, **kwargs: None
    )

    response = svc.submit_message(
        session_id,
        AgentBuilderMessageRequest(
            message="github 노드 뒤에 LLM 노드를 추가해줘",
            workflow_id=workflow_id,
        ),
    )

    assert response.status == "draft_ready"
    assert response.draft_preview is not None
    generated_nodes = [
        node
        for node in response.draft_preview.preview_graph["nodes"]
        if str(node["id"]).startswith("agent-")
    ]
    assert [node["type"] for node in generated_nodes] == ["llmNode"]
    draft = next(row for row in db.added if hasattr(row, "draft_metadata"))
    assert draft.draft_metadata["target_resolution"]["replaced_edge_ids"] == [
        "edge-github-answer"
    ]

    saved_graph = svc._graph_for_apply(  # noqa: SLF001
        draft,
        workflow,
        runtime_kb_bindings=[],
    )
    saved_edge_pairs = {
        (edge["source"], edge["target"]) for edge in saved_graph["edges"]
    }
    generated_llm_id = generated_nodes[0]["id"]
    assert ("github-read", "answer") not in saved_edge_pairs
    assert ("github-read", generated_llm_id) in saved_edge_pairs
    assert (generated_llm_id, "answer") in saved_edge_pairs


@pytest.mark.parametrize(
    ("capability", "expected_node_type"),
    [
        ("start_input", "startNode"),
        ("webhook_trigger", "webhookTrigger"),
        ("schedule_trigger", "scheduleTrigger"),
        ("llm", "llmNode"),
        ("workflow_call", "workflowNode"),
        ("code_execution", "codeNode"),
        ("condition", "conditionNode"),
        ("file_extraction", "fileExtractionNode"),
        ("variable_extraction", "variableExtractionNode"),
        ("answer", "answerNode"),
        ("http_request", "httpRequestNode"),
        ("slack_send", "slackPostNode"),
        ("template_render", "templateNode"),
        ("github_pr_read", "githubNode"),
        ("github_pr_comment", "githubNode"),
        ("mail_search", "mailNode"),
        ("gmail_reply_draft_create", "gmailDraftNode"),
        ("mail_terminal_acknowledgement", "mailAcknowledgeNode"),
    ],
)
def test_agent_builder_has_draft_template_for_every_supported_capability(
    monkeypatch,
    capability,
    expected_node_type,
):
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(svc, "_recommended_draft_model_id", lambda: "model-1")
    dependency_capabilities = {
        "gmail_reply_draft_create": [
            "mail_search",
            "llm",
            "gmail_reply_draft_create",
        ],
        "mail_terminal_acknowledgement": [
            "mail_search",
            "llm",
            "gmail_reply_draft_create",
            "mail_terminal_acknowledgement",
        ],
    }
    structured = service_module.AgentBuilderStructuredRequest(
        request_type="new_workflow",
        draft_mode="new_workflow",
        intent_summary="catalog capability",
        required_capabilities=dependency_capabilities.get(capability, [capability]),
    )

    preview = svc._build_preview_graph(  # noqa: SLF001
        structured,
        workflow=None,
        kb_bindings=[],
    )

    matching_nodes = [
        node for node in preview["nodes"] if node["type"] == expected_node_type
    ]
    assert matching_nodes
    NodeFactory.create(NodeSchema.model_validate(matching_nodes[0]))


def test_structured_request_keeps_unresolved_slack_channel_as_nonblocking_warning(
    monkeypatch,
):
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )

    structured = svc._build_structured_request(  # noqa: SLF001
        AgentBuilderMessageRequest(message="Analyze the input and send it to Slack"),
        workflow=None,
    )
    monkeypatch.setattr(svc, "_recommended_draft_model_id", lambda: "model-1")
    preview_graph = svc._build_preview_graph(  # noqa: SLF001
        structured,
        workflow=None,
        kb_bindings=[],
    )

    assert structured.missing_information == []
    assert "slack_send" in structured.required_capabilities
    assert "slack_channel_unresolved" in structured.risk_flags
    configuration_issues = svc._node_configuration_issues(  # noqa: SLF001
        preview_graph
    )
    assert len(configuration_issues) == 1
    assert configuration_issues[0].node_label == "Slack 전송"
    assert [
        parameter.label for parameter in configuration_issues[0].missing_parameters
    ] == ["Bot Token", "Slack channel"]
    slack_resolution = next(
        item
        for item in structured.pending_resolution
        if item.slot_key == "slack.channel"
    )
    assert slack_resolution.slot_type == "other"
    assert slack_resolution.blocking is False
    assert slack_resolution.target_step_ref == "step_slack"


def test_session_message_payload_rehydrates_ready_draft_preview():
    graph = {
        "nodes": [
            {
                "id": "slack-1",
                "type": "slackPostNode",
                "position": {"x": 0, "y": 0},
                "data": {
                    "title": "Slack 전송",
                    "configuration_state": "unresolved",
                },
            }
        ],
        "edges": [],
    }
    request_id = uuid.uuid4()
    draft = SimpleNamespace(
        id=uuid.uuid4(),
        request_id=request_id,
        status="ready",
        preview_graph=graph,
        base_graph_hash=calculate_graph_hash(graph),
        base_workflow_updated_at=None,
        draft_mode="new_workflow",
        node_detail_previews=[],
        validation_result={"valid": True, "issues": []},
        draft_metadata={},
        expires_at=None,
    )
    request_row = SimpleNamespace(
        id=request_id,
        response_payload={"request_id": str(request_id), "status": "draft_ready"},
    )
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )

    payload = svc._message_payload_with_latest_preview(request_row, draft)  # noqa: SLF001

    assert payload["draft_preview"]["draft_id"] == str(draft.id)
    assert payload["draft_preview"]["preview_graph"] == graph
    assert payload["draft_preview"]["safety_notices"] == [
        service_module.SAFE_SIDE_EFFECT_NOTICE
    ]
    assert payload["draft_preview"]["configuration_issues"] == [
        {
            "node_id": "slack-1",
            "node_type": "slackPostNode",
            "node_label": "Slack 전송",
                "capability": "slack_send",
                "missing_parameters": [
                    {"key": "payload", "label": "Slack message payload"},
                    {"key": "bot_token", "label": "Bot Token"},
                    {"key": "channel", "label": "Slack channel"},
            ],
        }
    ]


@pytest.mark.parametrize(
    "latest_status", ["failed", "unsupported", "validation_failed"]
)
def test_session_response_hides_draft_from_an_older_request(latest_status):
    latest_request_id = uuid.uuid4()
    graph = {
        "nodes": [
            {
                "id": "http-old",
                "type": "httpRequestNode",
                "position": {"x": 0, "y": 0},
                "data": {"title": "이전 HTTP 요청"},
            }
        ],
        "edges": [],
    }
    latest_request = SimpleNamespace(
        id=latest_request_id,
        status=latest_status,
        message_summary="기존 LLM 노드 뒤에 GitHub PR 생성 노드를 삽입",
        response_payload={
            "request_id": str(latest_request_id),
            "status": latest_status,
            "warnings": [],
        },
        created_at=None,
    )
    older_draft = SimpleNamespace(
        id=uuid.uuid4(),
        request_id=uuid.uuid4(),
        status="ready",
        preview_graph=graph,
        base_graph_hash=calculate_graph_hash(graph),
        base_workflow_updated_at=None,
        draft_mode="modify_workflow",
        node_detail_previews=[],
        validation_result={"valid": True, "issues": []},
        draft_metadata={},
        expires_at=None,
    )

    class SessionDb(FakeDb):
        def query(self, model):
            if model is service_module.AgentBuilderRequest:
                return FakeQuery(latest_request)
            if model is service_module.AgentBuilderDraft:
                return FakeQuery(older_draft)
            raise AssertionError(f"unexpected model: {model}")

    svc = AgentBuilderService(
        SessionDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    response = svc._session_response(  # noqa: SLF001
        SimpleNamespace(
            id=uuid.uuid4(),
            workflow_id=uuid.uuid4(),
            app_id=None,
            status="active",
        )
    )

    assert response.draft_preview is None
    assert response.messages[-1]["response"]["status"] == latest_status
    assert response.messages[-1]["response"].get("draft_preview") is None


def test_session_response_restores_draft_for_the_latest_request():
    request_id = uuid.uuid4()
    latest_request = SimpleNamespace(
        id=request_id,
        status="completed",
        message_summary="입력과 응답 노드를 만들어줘",
        response_payload={
            "request_id": str(request_id),
            "status": "draft_ready",
        },
        created_at=None,
    )
    latest_draft = SimpleNamespace(
        id=uuid.uuid4(),
        request_id=request_id,
        status="ready",
        preview_graph={"nodes": [], "edges": []},
        base_graph_hash="latest-graph",
        base_workflow_updated_at=None,
        draft_mode="new_workflow",
        node_detail_previews=[],
        validation_result={"valid": True, "issues": []},
        draft_metadata={},
        expires_at=None,
    )

    class SessionDb(FakeDb):
        def query(self, model):
            if model is service_module.AgentBuilderRequest:
                return FakeQuery(latest_request)
            if model is service_module.AgentBuilderDraft:
                return FakeQuery(latest_draft)
            raise AssertionError(f"unexpected model: {model}")

    svc = AgentBuilderService(
        SessionDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    response = svc._session_response(  # noqa: SLF001
        SimpleNamespace(
            id=uuid.uuid4(),
            workflow_id=None,
            app_id=uuid.uuid4(),
            status="active",
        )
    )

    assert response.draft_preview is not None
    assert response.draft_preview["draft_id"] == str(latest_draft.id)


def test_agent_builder_validator_rejects_unsupported_node_type():
    svc = AgentBuilderService(
        object(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )

    result = svc.validate_preview_graph(
        {
            "nodes": [{"id": "custom", "type": "customNode", "data": {}}],
            "edges": [],
        }
    )

    assert result.valid is False
    assert result.issues[0].code == "UNSUPPORTED_NODE_TYPE"


def test_agent_builder_kb_recommendation_uses_safe_summary_and_high_confidence(
    monkeypatch,
):
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    kb_id = uuid.uuid4()
    captured = {}

    class FakeRecommendationService:
        def __init__(self, db, *, user_id, organization_id):
            captured["user_id"] = user_id
            captured["organization_id"] = organization_id

        def recommend_for_builder(self, request, **_kwargs):
            captured["workflow_intent"] = request.workflow_intent
            captured["node_purpose"] = request.node_purpose
            captured["max_recommendations"] = request.max_recommendations
            return KnowledgeRAGRecommendationResponse(
                recommendations=[
                    KnowledgeRAGRecommendation(
                        recommendation_id="safe-rec-1",
                        recommendation_mode="auto_collection",
                        candidate_id="safe-rec-1",
                        candidate_handle="safe-rec-1",
                        safe_label="휴가 정책",
                        confidence="high",
                        score=0.82,
                        threshold_result="high_confidence",
                        safe_reason_code="topic_keyword_match",
                        recommended_options=KnowledgeRAGRecommendedOptions(),
                        materialized_knowledge_bases=[
                            {"id": kb_id, "name": "휴가 정책"}
                        ],
                        provenance=KnowledgeRAGRecommendationProvenance(
                            safe_reason_code="topic_keyword_match",
                        ),
                        runtime_availability="available",
                    )
                ],
                summary=KnowledgeRAGRecommendationSummary(
                    candidate_count_bucket="1",
                    recommendation_count_bucket="1",
                ),
            )

    monkeypatch.setattr(
        service_module,
        "KnowledgeRAGRecommendationService",
        FakeRecommendationService,
    )
    svc = AgentBuilderService(
        object(),
        user=SimpleNamespace(id=user_id),
        organization_id=organization_id,
    )
    structured = svc._build_structured_request(  # noqa: SLF001
        AgentBuilderMessageRequest(
            message="휴가 정책 문서를 찾아 답변 workflow를 만들어줘"
        ),
        workflow=None,
    )

    result = svc._resolve_knowledge_requirements(structured)  # noqa: SLF001

    assert result["status"] == "clarification_required"
    assert result["bindings"] == []
    assert result["options"][0]["candidate_id"] == "safe-rec-1"
    assert result["options"][0]["confidence"] == "high"
    assert result["options"][0]["score"] == 0.82
    assert len(result["options"]) == 1
    assert captured["user_id"] == user_id
    assert captured["organization_id"] == organization_id
    assert (
        captured["max_recommendations"]
        == service_module.AGENT_BUILDER_KB_RECOMMENDATION_LIMIT
    )
    assert "sk-" not in captured["workflow_intent"]


def test_agent_builder_structures_kb_query_topics_without_workflow_noise():
    svc = AgentBuilderService(
        object(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )

    structured = svc._build_structured_request(  # noqa: SLF001
        AgentBuilderMessageRequest(
            message="웹훅으로 받는 사내 문서 챗봇 워크플로우를 만들어줘"
        ),
        workflow=None,
    )

    topics = structured.knowledge_requirements[0].query_topics
    assert topics[:3] == ["사내 문서", "내부 문서", "문서 질의"]
    assert "웹훅" not in topics
    assert "워크플로우" not in topics


def test_agent_builder_kb_recommendation_close_score_requires_clarification(
    monkeypatch,
):
    kb_id_a = uuid.uuid4()
    kb_id_b = uuid.uuid4()

    class FakeRecommendationService:
        def __init__(self, db, *, user_id, organization_id):
            pass

        def recommend_for_builder(self, request, **_kwargs):
            return KnowledgeRAGRecommendationResponse(
                recommendations=[
                    KnowledgeRAGRecommendation(
                        recommendation_id="safe-rec-1",
                        recommendation_mode="auto_collection",
                        candidate_id="safe-rec-1",
                        candidate_handle="safe-rec-1",
                        safe_label="휴가 정책",
                        confidence="high",
                        score=0.7,
                        threshold_result="high_confidence",
                        safe_reason_code="topic_keyword_match",
                        recommended_options=KnowledgeRAGRecommendedOptions(),
                        materialized_knowledge_bases=[
                            {"id": kb_id_a, "name": "휴가 정책"}
                        ],
                        provenance=KnowledgeRAGRecommendationProvenance(
                            safe_reason_code="topic_keyword_match",
                        ),
                        runtime_availability="available",
                    ),
                    KnowledgeRAGRecommendation(
                        recommendation_id="safe-rec-2",
                        recommendation_mode="auto_collection",
                        candidate_id="safe-rec-2",
                        candidate_handle="safe-rec-2",
                        safe_label="인사 정책",
                        confidence="high",
                        score=0.66,
                        threshold_result="high_confidence",
                        safe_reason_code="metadata_match",
                        recommended_options=KnowledgeRAGRecommendedOptions(),
                        materialized_knowledge_bases=[
                            {"id": kb_id_b, "name": "인사 정책"}
                        ],
                        provenance=KnowledgeRAGRecommendationProvenance(
                            safe_reason_code="metadata_match",
                        ),
                        runtime_availability="available",
                    ),
                ],
                summary=KnowledgeRAGRecommendationSummary(
                    candidate_count_bucket="2",
                    recommendation_count_bucket="2",
                ),
            )

    monkeypatch.setattr(
        service_module,
        "KnowledgeRAGRecommendationService",
        FakeRecommendationService,
    )
    svc = AgentBuilderService(
        object(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    structured = svc._build_structured_request(  # noqa: SLF001
        AgentBuilderMessageRequest(
            message="휴가 정책 문서를 찾아 답변 workflow를 만들어줘"
        ),
        workflow=None,
    )

    result = svc._resolve_knowledge_requirements(structured)  # noqa: SLF001

    assert result["status"] == "clarification_required"
    assert result["questions"]
    assert result["options"] == [
        {
            "type": "knowledge_base",
            "candidate_id": "safe-rec-1",
            "label": "휴가 정책",
            "confidence": "high",
            "score": 0.7,
            "reason_category": "topic_keyword_match",
            "threshold_result": "high_confidence",
            "runtime_availability": "available",
            "requirement_id": "kr_1",
            "resolution_id": "res_kb_1",
        },
        {
            "type": "knowledge_base",
            "candidate_id": "safe-rec-2",
            "label": "인사 정책",
            "confidence": "high",
            "score": 0.66,
            "reason_category": "metadata_match",
            "threshold_result": "high_confidence",
            "runtime_availability": "available",
            "requirement_id": "kr_1",
            "resolution_id": "res_kb_1",
        },
    ]


def test_agent_builder_multiple_kb_candidates_require_clarification_even_when_top_is_clear(
    monkeypatch,
):
    class FakeRecommendationService:
        def __init__(self, db, *, user_id, organization_id):
            pass

        def recommend_for_builder(self, request, **_kwargs):
            return KnowledgeRAGRecommendationResponse(
                recommendations=[
                    KnowledgeRAGRecommendation(
                        recommendation_id="safe-rec-1",
                        recommendation_mode="auto_collection",
                        candidate_id="safe-rec-1",
                        candidate_handle="safe-rec-1",
                        safe_label="Policy KB",
                        confidence="high",
                        score=0.92,
                        threshold_result="high_confidence",
                        safe_reason_code="structured_intent_matches_safe_metadata",
                        recommended_options=KnowledgeRAGRecommendedOptions(),
                        provenance=KnowledgeRAGRecommendationProvenance(
                            safe_reason_code="structured_intent_matches_safe_metadata",
                        ),
                        runtime_availability="available",
                    ),
                    KnowledgeRAGRecommendation(
                        recommendation_id="safe-rec-2",
                        recommendation_mode="auto_collection",
                        candidate_id="safe-rec-2",
                        candidate_handle="safe-rec-2",
                        safe_label="Benefits KB",
                        confidence="medium",
                        score=0.45,
                        threshold_result="close_score",
                        safe_reason_code="structured_intent_matches_safe_metadata",
                        recommended_options=KnowledgeRAGRecommendedOptions(),
                        provenance=KnowledgeRAGRecommendationProvenance(
                            safe_reason_code="structured_intent_matches_safe_metadata",
                        ),
                        runtime_availability="available",
                    ),
                ],
                summary=KnowledgeRAGRecommendationSummary(
                    candidate_count_bucket="2",
                    recommendation_count_bucket="2",
                ),
            )

    monkeypatch.setattr(
        service_module,
        "KnowledgeRAGRecommendationService",
        FakeRecommendationService,
    )
    svc = AgentBuilderService(
        object(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    structured = service_module.AgentBuilderStructuredRequest(
        request_type="new_workflow",
        draft_mode="new_workflow",
        intent_summary="Create a workflow from internal policy knowledge",
        knowledge_requirements=[
            service_module.AgentBuilderKnowledgeRequirement(
                requirement_id="kr_1",
                query_topics=["policy"],
                target_step_ref="step_llm",
            )
        ],
        pending_resolution=[
            service_module.AgentBuilderPendingResolution(
                resolution_id="res_kb_1",
                slot_type="knowledge_base",
                slot_key="llm.knowledgeBases",
                target_step_ref="step_llm",
            )
        ],
    )

    result = svc._resolve_knowledge_requirements(structured)  # noqa: SLF001

    assert result["status"] == "clarification_required"
    assert result["options"][0]["candidate_id"] == "safe-rec-1"
    assert result["options"][1]["candidate_id"] == "safe-rec-2"
    assert len(result["options"]) == 2
    assert result["bindings"] == []


def test_agent_builder_single_close_score_kb_candidate_requires_clarification(
    monkeypatch,
):
    class FakeRecommendationService:
        def __init__(self, db, *, user_id, organization_id):
            pass

        def recommend_for_builder(self, request, **_kwargs):
            return KnowledgeRAGRecommendationResponse(
                recommendations=[
                    KnowledgeRAGRecommendation(
                        recommendation_id="safe-rec-1",
                        recommendation_mode="auto_collection",
                        candidate_id="safe-rec-1",
                        candidate_handle="safe-rec-1",
                        safe_label="사내 문서",
                        confidence="medium",
                        score=0.5,
                        threshold_result="close_score",
                        safe_reason_code="intent_matches_safe_metadata",
                        recommended_options=KnowledgeRAGRecommendedOptions(),
                        materialized_knowledge_bases=[],
                        provenance=KnowledgeRAGRecommendationProvenance(
                            safe_reason_code="intent_matches_safe_metadata",
                        ),
                        runtime_availability="available",
                    )
                ],
                summary=KnowledgeRAGRecommendationSummary(
                    candidate_count_bucket="1",
                    recommendation_count_bucket="1",
                ),
            )

    monkeypatch.setattr(
        service_module,
        "KnowledgeRAGRecommendationService",
        FakeRecommendationService,
    )
    svc = AgentBuilderService(
        object(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    structured = svc._build_structured_request(  # noqa: SLF001
        AgentBuilderMessageRequest(message="사내 문서를 찾아 답변 workflow를 만들어줘"),
        workflow=None,
    )

    result = svc._resolve_knowledge_requirements(structured)  # noqa: SLF001

    assert result["status"] == "clarification_required"
    assert result["bindings"] == []
    assert result["options"][0]["candidate_id"] == "safe-rec-1"
    assert result["options"][0]["confidence"] == "medium"
    assert result["options"][0]["score"] == 0.5
    assert result["options"][0]["threshold_result"] == "close_score"
    assert len(result["options"]) == 1


def test_agent_builder_selected_kb_candidates_use_selected_safe_handles(monkeypatch):
    kb_id_a = uuid.uuid4()
    kb_id_b = uuid.uuid4()

    class FakeRecommendationService:
        def __init__(self, db, *, user_id, organization_id):
            pass

        def recommend_for_builder(self, request, **kwargs):
            include_materialized_refs = kwargs.get("include_materialized_refs", False)
            materialized_a = (
                [{"id": kb_id_a, "name": "휴가 정책"}]
                if include_materialized_refs
                else []
            )
            materialized_b = (
                [{"id": kb_id_b, "name": "인사 정책"}]
                if include_materialized_refs
                else []
            )
            return KnowledgeRAGRecommendationResponse(
                recommendations=[
                    KnowledgeRAGRecommendation(
                        recommendation_id="safe-rec-1",
                        recommendation_mode="auto_collection",
                        candidate_id="safe-rec-1",
                        candidate_handle="safe-rec-1",
                        safe_label="휴가 정책",
                        confidence="high",
                        score=0.7,
                        threshold_result="high_confidence",
                        safe_reason_code="topic_keyword_match",
                        recommended_options=KnowledgeRAGRecommendedOptions(),
                        materialized_knowledge_bases=materialized_a,
                        provenance=KnowledgeRAGRecommendationProvenance(
                            safe_reason_code="topic_keyword_match",
                        ),
                        runtime_availability="available",
                    ),
                    KnowledgeRAGRecommendation(
                        recommendation_id="safe-rec-2",
                        recommendation_mode="auto_collection",
                        candidate_id="safe-rec-2",
                        candidate_handle="safe-rec-2",
                        safe_label="인사 정책",
                        confidence="high",
                        score=0.66,
                        threshold_result="high_confidence",
                        safe_reason_code="metadata_match",
                        recommended_options=KnowledgeRAGRecommendedOptions(),
                        materialized_knowledge_bases=materialized_b,
                        provenance=KnowledgeRAGRecommendationProvenance(
                            safe_reason_code="metadata_match",
                        ),
                        runtime_availability="available",
                    ),
                ],
                summary=KnowledgeRAGRecommendationSummary(
                    candidate_count_bucket="2",
                    recommendation_count_bucket="2",
                ),
            )

    monkeypatch.setattr(
        service_module,
        "KnowledgeRAGRecommendationService",
        FakeRecommendationService,
    )
    svc = AgentBuilderService(
        object(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    structured = svc._build_structured_request(  # noqa: SLF001
        AgentBuilderMessageRequest(
            message="휴가 정책 문서를 찾아 답변 workflow를 만들어줘"
        ),
        workflow=None,
    )

    result = svc._resolve_knowledge_requirements(  # noqa: SLF001
        structured,
        selected_candidate_handles={"safe-rec-1", "safe-rec-2"},
        include_materialized_refs=True,
    )

    assert result["status"] == "recommended"
    assert result["bindings"] == [
        {
            "safe_handle": "safe-rec-1",
            "name": "휴가 정책",
            "confidence": "high",
            "score": 0.7,
            "reason_category": "topic_keyword_match",
            "threshold_result": "high_confidence",
            "knowledge_base_id": str(kb_id_a),
        },
        {
            "safe_handle": "safe-rec-2",
            "name": "인사 정책",
            "confidence": "high",
            "score": 0.66,
            "reason_category": "metadata_match",
            "threshold_result": "high_confidence",
            "knowledge_base_id": str(kb_id_b),
        },
    ]


def test_agent_builder_selected_low_score_kb_candidate_does_not_repeat_clarification(
    monkeypatch,
):
    class FakeRecommendationService:
        def __init__(self, db, *, user_id, organization_id):
            pass

        def recommend_for_builder(self, request, **_kwargs):
            return KnowledgeRAGRecommendationResponse(
                recommendations=[
                    KnowledgeRAGRecommendation(
                        recommendation_id="safe-rec-1",
                        recommendation_mode="auto_collection",
                        candidate_id="safe-rec-1",
                        candidate_handle="safe-rec-1",
                        safe_label="Knowledge Base",
                        confidence="low",
                        score=0.33,
                        threshold_result="below_threshold",
                        safe_reason_code="intent_matches_safe_metadata",
                        recommended_options=KnowledgeRAGRecommendedOptions(),
                        materialized_knowledge_bases=[],
                        provenance=KnowledgeRAGRecommendationProvenance(
                            safe_reason_code="intent_matches_safe_metadata",
                        ),
                        runtime_availability="available",
                    )
                ],
                summary=KnowledgeRAGRecommendationSummary(
                    candidate_count_bucket="1",
                    recommendation_count_bucket="1",
                ),
            )

    monkeypatch.setattr(
        service_module,
        "KnowledgeRAGRecommendationService",
        FakeRecommendationService,
    )
    svc = AgentBuilderService(
        object(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    structured = svc._build_structured_request(  # noqa: SLF001
        AgentBuilderMessageRequest(message="Knowledge Base로 답변 workflow를 만들어줘"),
        workflow=None,
    )

    result = svc._resolve_knowledge_requirements(  # noqa: SLF001
        structured,
        selected_candidate_handles={"safe-rec-1"},
    )

    assert result["status"] == "recommended"
    assert result["bindings"] == [
        {
            "safe_handle": "safe-rec-1",
            "name": "Knowledge Base",
            "confidence": "low",
            "score": 0.33,
            "reason_category": "intent_matches_safe_metadata",
            "threshold_result": "below_threshold",
        }
    ]
    assert "사용자가 선택한 Knowledge Base 후보" in result["warnings"][0]


def test_agent_builder_selected_no_kb_option_skips_kb_binding(monkeypatch):
    service_called = False

    class FakeRecommendationService:
        def __init__(self, db, *, user_id, organization_id):
            pass

        def recommend_for_builder(self, request, **_kwargs):
            nonlocal service_called
            service_called = True
            return KnowledgeRAGRecommendationResponse(
                recommendations=[
                    KnowledgeRAGRecommendation(
                        recommendation_id="safe-rec-1",
                        recommendation_mode="auto_collection",
                        candidate_id="safe-rec-1",
                        candidate_handle="safe-rec-1",
                        safe_label="Knowledge Base",
                        confidence="low",
                        score=0.33,
                        threshold_result="below_threshold",
                        safe_reason_code="intent_matches_safe_metadata",
                        recommended_options=KnowledgeRAGRecommendedOptions(),
                        materialized_knowledge_bases=[],
                        provenance=KnowledgeRAGRecommendationProvenance(
                            safe_reason_code="intent_matches_safe_metadata",
                        ),
                        runtime_availability="available",
                    )
                ],
                summary=KnowledgeRAGRecommendationSummary(
                    candidate_count_bucket="1",
                    recommendation_count_bucket="1",
                ),
            )

    monkeypatch.setattr(
        service_module,
        "KnowledgeRAGRecommendationService",
        FakeRecommendationService,
    )
    svc = AgentBuilderService(
        object(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    structured = service_module.AgentBuilderStructuredRequest(
        request_type="new_workflow",
        draft_mode="new_workflow",
        intent_summary="Use a Knowledge policy document in this workflow",
        knowledge_requirements=[
            service_module.AgentBuilderKnowledgeRequirement(
                requirement_id="kr_1",
                query_topics=["policy"],
                target_step_ref="step_llm",
            )
        ],
        pending_resolution=[
            service_module.AgentBuilderPendingResolution(
                resolution_id="res_kb_1",
                slot_type="knowledge_base",
                slot_key="llm.knowledgeBases",
                target_step_ref="step_llm",
            )
        ],
    )

    result = svc._resolve_knowledge_requirements(  # noqa: SLF001
        structured,
        selected_candidate_handles={service_module.NO_KB_CANDIDATE_ID},
    )

    assert result["status"] == "recommended"
    assert result["bindings"] == []
    assert "Knowledge Base 없이" in result["warnings"][0]
    assert service_called is False


def test_agent_builder_kb_recommendation_adapter_unavailable_with_options_requires_clarification(
    monkeypatch,
):
    class FakeRecommendationService:
        def __init__(self, db, *, user_id, organization_id):
            pass

        def recommend_for_builder(self, request, **_kwargs):
            return KnowledgeRAGRecommendationResponse(
                status="clarification_required",
                recommendations=[],
                clarification_options=[
                    {
                        "candidate_id": "safe-rec-1",
                        "safe_label": "휴가 정책",
                        "reason_category": "adapter_unavailable",
                    }
                ],
                fallback_reason="adapter_unavailable",
                user_safe_warning="Knowledge Base 추천을 사용할 수 없어 사용자 확인이 필요합니다.",
            )

    monkeypatch.setattr(
        service_module,
        "KnowledgeRAGRecommendationService",
        FakeRecommendationService,
    )
    svc = AgentBuilderService(
        object(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    structured = svc._build_structured_request(  # noqa: SLF001
        AgentBuilderMessageRequest(
            message="휴가 정책 문서를 찾아 답변 workflow를 만들어줘"
        ),
        workflow=None,
    )

    result = svc._resolve_knowledge_requirements(structured)  # noqa: SLF001

    assert result["status"] == "clarification_required"
    assert result["options"][0]["candidate_id"] == "safe-rec-1"
    assert result["options"][0]["requirement_id"] == "kr_1"
    assert result["options"][0]["resolution_id"] == "res_kb_1"


def test_agent_builder_selected_kb_candidate_from_unavailable_fallback_is_used(
    monkeypatch,
):
    class FakeRecommendationService:
        def __init__(self, db, *, user_id, organization_id):
            pass

        def recommend_for_builder(self, request, **_kwargs):
            return KnowledgeRAGRecommendationResponse(
                status="unavailable",
                recommendations=[],
                clarification_options=[
                    {
                        "candidate_id": "safe-rec-1",
                        "safe_label": "HR Policy",
                        "confidence": "low",
                        "score": 0.0,
                        "reason_category": "adapter_unavailable",
                        "threshold_result": "adapter_unavailable",
                    }
                ],
                fallback_reason="adapter_unavailable",
                user_safe_warning="Knowledge Base recommendation is unavailable.",
            )

    monkeypatch.setattr(
        service_module,
        "KnowledgeRAGRecommendationService",
        FakeRecommendationService,
    )
    svc = AgentBuilderService(
        object(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    structured = service_module.AgentBuilderStructuredRequest(
        request_type="new_workflow",
        draft_mode="new_workflow",
        intent_summary="Use a Knowledge policy document in this workflow",
        knowledge_requirements=[
            service_module.AgentBuilderKnowledgeRequirement(
                requirement_id="kr_1",
                query_topics=["policy"],
                target_step_ref="step_llm",
            )
        ],
        pending_resolution=[
            service_module.AgentBuilderPendingResolution(
                resolution_id="res_kb_1",
                slot_type="knowledge_base",
                slot_key="llm.knowledgeBases",
                target_step_ref="step_llm",
            )
        ],
    )

    result = svc._resolve_knowledge_requirements(  # noqa: SLF001
        structured,
        selected_candidate_handles={"safe-rec-1"},
    )

    assert result["status"] == "recommended"
    assert result["bindings"] == [
        {
            "safe_handle": "safe-rec-1",
            "name": "HR Policy",
            "confidence": "low",
            "score": 0.0,
            "reason_category": "adapter_unavailable",
            "threshold_result": "adapter_unavailable",
        }
    ]


def test_agent_builder_kb_recommendation_unavailable_blocks_required_kb(monkeypatch):
    class FakeRecommendationService:
        def __init__(self, db, *, user_id, organization_id):
            pass

        def recommend_for_builder(self, request, **_kwargs):
            return KnowledgeRAGRecommendationResponse(
                status="unavailable",
                recommendations=[],
                fallback_reason="adapter_unavailable",
                user_safe_warning="Knowledge Base 추천을 사용할 수 없습니다.",
            )

    monkeypatch.setattr(
        service_module,
        "KnowledgeRAGRecommendationService",
        FakeRecommendationService,
    )
    svc = AgentBuilderService(
        object(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    structured = svc._build_structured_request(  # noqa: SLF001
        AgentBuilderMessageRequest(
            message="휴가 정책 문서를 찾아 답변 workflow를 만들어줘"
        ),
        workflow=None,
    )

    result = svc._resolve_knowledge_requirements(structured)  # noqa: SLF001

    assert result["status"] == "validation_failed"
    assert "Knowledge Base" in result["warnings"][0]


def test_agent_builder_kb_recommendation_no_candidate_requires_explicit_empty_selection(
    monkeypatch,
):
    class FakeRecommendationService:
        def __init__(self, db, *, user_id, organization_id):
            pass

        def recommend_for_builder(self, request, **_kwargs):
            return KnowledgeRAGRecommendationResponse(
                recommendations=[],
                summary=KnowledgeRAGRecommendationSummary(
                    candidate_count_bucket="0",
                    recommendation_count_bucket="0",
                ),
            )

    monkeypatch.setattr(
        service_module,
        "KnowledgeRAGRecommendationService",
        FakeRecommendationService,
    )
    svc = AgentBuilderService(
        object(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    structured = svc._build_structured_request(  # noqa: SLF001
        AgentBuilderMessageRequest(
            message="휴가 정책 문서를 찾아 Slack으로 보내는 workflow를 만들어줘"
        ),
        workflow=None,
    )

    result = svc._resolve_knowledge_requirements(structured)  # noqa: SLF001

    assert result["status"] == "clarification_required"
    assert result["bindings"] == []
    assert result["options"] == []
    assert "확인" in result["questions"][0]
    assert "후보" in result["warnings"][0]


def test_agent_builder_collection_only_recommendation_remains_selectable(monkeypatch):
    class FakeRecommendationService:
        def __init__(self, db, *, user_id, organization_id):
            pass

        def recommend_for_builder(self, request, **_kwargs):
            return KnowledgeRAGRecommendationResponse(
                status="recommended",
                recommendations=[],
                knowledge_selection=KnowledgeSelection(
                    collections=[
                        KnowledgeSelectionCollection(
                            collection_handle="col-safe-1",
                            safe_label="제한 문서",
                            score=0.8,
                            children=[],
                        )
                    ]
                ),
                summary=KnowledgeRAGRecommendationSummary(
                    candidate_count_bucket="0",
                    recommendation_count_bucket="0",
                ),
            )

    monkeypatch.setattr(
        service_module,
        "KnowledgeRAGRecommendationService",
        FakeRecommendationService,
    )
    svc = AgentBuilderService(
        object(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    structured = svc._build_structured_request(  # noqa: SLF001
        AgentBuilderMessageRequest(
            message="제한 문서를 참고해 답변하는 workflow를 만들어줘"
        ),
        workflow=None,
    )

    result = svc._resolve_knowledge_requirements(structured)  # noqa: SLF001

    assert result["status"] == "clarification_required"
    assert result["knowledge_selection"]["collections"][0][
        "collection_handle"
    ] == "col-safe-1"
    assert "후보가 없습니다" not in result["questions"][0]


def test_direct_builder_rejects_flat_only_knowledge_recommendation(monkeypatch):
    class FakeRecommendationService:
        def __init__(self, db, *, user_id, organization_id):
            pass

        def recommend_for_builder(self, request, **_kwargs):
            return KnowledgeRAGRecommendationResponse(
                status="recommended",
                recommendations=[
                    KnowledgeRAGRecommendation(
                        recommendation_id="rec-flat-1",
                        recommendation_mode="auto_collection",
                        candidate_id="rec-flat-1",
                        candidate_handle="rec-flat-1",
                        safe_label="Flat KB",
                        confidence="low",
                        score=0.1,
                        reason_category="safe_candidate_available",
                        threshold_result="below_threshold",
                        safe_reason_code="intent_matches_safe_metadata",
                        recommended_options=KnowledgeRAGRecommendedOptions(),
                        materialized_knowledge_bases=[],
                        provenance=KnowledgeRAGRecommendationProvenance(
                            safe_reason_code="intent_matches_safe_metadata",
                        ),
                        runtime_availability="available",
                    )
                ],
                clarification_options=[
                    {
                        "type": "knowledge_base",
                        "candidate_id": "rec-flat-1",
                        "label": "Flat KB",
                    }
                ],
                knowledge_selection=None,
                summary=KnowledgeRAGRecommendationSummary(
                    candidate_count_bucket="1",
                    recommendation_count_bucket="1",
                ),
            )

    monkeypatch.setattr(
        service_module,
        "KnowledgeRAGRecommendationService",
        FakeRecommendationService,
    )
    svc = AgentBuilderService(
        object(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    structured = svc._build_structured_request(  # noqa: SLF001
        AgentBuilderMessageRequest(message="사내 문서로 답변하는 workflow를 만들어줘"),
        workflow=None,
    )

    result = svc._resolve_knowledge_requirements(  # noqa: SLF001
        structured,
        require_hierarchical_selection=True,
    )

    assert result["status"] == "validation_failed"
    assert result["bindings"] == []
    assert result["options"] == []
    assert result["knowledge_selection"] == {
        "collections": [],
        "ungrouped_kbs": [],
    }
    assert "계층형 Knowledge 후보" in result["warnings"][0]


def test_agent_builder_session_messages_restore_redacted_user_turn_and_assistant_turn():
    request_id = uuid.uuid4()
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    request_row = SimpleNamespace(
        id=request_id,
        message_summary="휴가 정책을 찾아서 요약해줘",
        response_payload={
            "request_id": str(request_id),
            "status": "clarification_required",
            "clarification_questions": ["사용할 Knowledge Base를 선택해주세요."],
            "clarification_options": [],
            "warnings": [],
        },
    )

    messages = svc._session_messages(request_row, None)  # noqa: SLF001

    assert messages[0] == {
        "kind": "user",
        "request_id": str(request_id),
        "content": "휴가 정책을 찾아서 요약해줘",
        "redacted": True,
    }
    assert messages[1]["kind"] == "assistant"
    assert messages[1]["response"]["status"] == "clarification_required"


def test_direct_session_messages_expose_processing_request_as_planning():
    request_id = uuid.uuid4()
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    request_row = SimpleNamespace(
        id=request_id,
        status="processing",
        created_at=None,
        message_summary="workflow request",
        response_payload={},
    )

    messages = svc._session_messages(  # noqa: SLF001
        request_row,
        None,
        direct_edit=True,
    )

    assert messages[1]["response"]["request_id"] == str(request_id)
    assert messages[1]["response"]["status"] == "planning"
    assert svc._request_summary(request_row)["status"] == "planning"  # noqa: SLF001


def test_processing_request_past_deadline_becomes_terminal_failed():
    now = datetime.now(timezone.utc)
    request_row = SimpleNamespace(
        id=uuid.uuid4(),
        status="processing",
        created_at=now - timedelta(minutes=5),
        completed_at=None,
        response_payload={},
        structured_request={},
    )
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )

    expired = svc._reconcile_processing_deadline(  # noqa: SLF001
        request_row,
        now=now,
    )

    assert expired is True
    assert request_row.status == "failed"
    assert request_row.response_payload["status"] == "failed"
    assert request_row.response_payload["validation_result"]["issues"][0][
        "code"
    ] == "REQUEST_PROCESSING_TIMEOUT"
    assert request_row.completed_at == now


def test_processing_request_inside_deadline_remains_planning():
    now = datetime.now(timezone.utc)
    request_row = SimpleNamespace(
        id=uuid.uuid4(),
        status="processing",
        created_at=now - timedelta(minutes=1),
        completed_at=None,
        response_payload={},
        structured_request={},
    )
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )

    expired = svc._reconcile_processing_deadline(  # noqa: SLF001
        request_row,
        now=now,
    )

    assert expired is False
    assert request_row.status == "processing"


def test_processing_request_past_deadline_releases_next_request_admission(monkeypatch):
    now = datetime.now(timezone.utc)
    pending = SimpleNamespace(
        id=uuid.uuid4(),
        status="processing",
        created_at=now - timedelta(minutes=5),
        completed_at=None,
        response_payload={},
        structured_request={},
    )
    db = FakeDb()
    db.query_result = FakeQuery(pending)
    audit_calls = []
    monkeypatch.setattr(
        service_module,
        "add_action_audit",
        lambda *_args, **kwargs: audit_calls.append(kwargs),
    )
    svc = AgentBuilderService(
        db,
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )

    svc._reject_if_pending(SimpleNamespace(id=uuid.uuid4()))  # noqa: SLF001

    assert pending.status == "failed"
    assert pending.response_payload["validation_result"]["issues"][0][
        "code"
    ] == "REQUEST_PROCESSING_TIMEOUT"
    assert audit_calls[0]["metadata"]["reason"] == "processing_timeout"


def test_direct_session_recovery_removes_legacy_generic_knowledge_task(
    monkeypatch,
):
    group_id = uuid.uuid4()
    model_task = AgentBuilderParameterTask(
        task_id=uuid.uuid4(),
        group_id=group_id,
        step_id="step_llm",
        node_id="llm",
        node_type="llmNode",
        parameter_key="model_id",
        label="Model",
        input_type="resource_ref",
        required=True,
        defer_policy="forbidden",
        status="completed",
        task_version=1,
        stable_order=0,
        reason="Model is configured.",
        input_guidance="Select a model.",
        node_label="LLM",
    )
    knowledge_task = AgentBuilderParameterTask(
        task_id=uuid.uuid4(),
        group_id=group_id,
        step_id="step_llm",
        node_id="llm",
        node_type="llmNode",
        parameter_key="knowledgeBases",
        label="Knowledge Bases",
        input_type="resource_ref",
        required=False,
        defer_policy="forbidden",
        status="active",
        task_version=1,
        stable_order=1,
        reason="Knowledge Base setting is required.",
        input_guidance="Select Knowledge Bases.",
        node_label="LLM",
    )
    group = AgentBuilderParameterGroup(
        group_id=group_id,
        status="active",
        tasks=[model_task, knowledge_task],
    )
    request_id = uuid.uuid4()
    request_row = SimpleNamespace(
        id=request_id,
        status="completed",
        created_at=None,
        expires_at=None,
        message_summary="LLM workflow",
        response_payload={
            "request_id": str(request_id),
            "status": "graph_mutation_ready",
            "parameter_groups": [group.model_dump(mode="json")],
        },
    )

    class SessionQuery(FakeQuery):
        def with_for_update(self):
            return self

        def all(self):
            return [self.result] if self.result is not None else []

    class SessionDb(FakeDb):
        def query(self, model):
            if model is service_module.AgentBuilderRequest:
                return SessionQuery(request_row)
            raise AssertionError(f"unexpected model: {model}")

    monkeypatch.setattr(
        service_module.ParameterCandidateProvider,
        "enrich_group",
        lambda _self, parameter_group, **_kwargs: parameter_group,
    )
    db = SessionDb()
    svc = AgentBuilderService(
        db,
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )

    response = svc._session_response(  # noqa: SLF001
        SimpleNamespace(
            id=uuid.uuid4(),
            workflow_id=None,
            app_id=None,
            status="active",
            protocol_version="direct_edit_v1",
        )
    )

    assert response.parameter_group is not None
    assert [task.parameter_key for task in response.parameter_group.tasks] == [
        "model_id"
    ]
    assert response.parameter_group.status == "completed"
    assert [
        task["parameter_key"]
        for task in request_row.response_payload["parameter_groups"][0]["tasks"]
    ] == ["model_id"]
    assert db.commits == 1


@pytest.mark.parametrize("include_safe_step_node_ids", [True, False])
def test_direct_session_recovery_adds_catalog_tasks_missing_from_legacy_group(
    monkeypatch,
    include_safe_step_node_ids,
):
    group_id = uuid.uuid4()
    channel_task = AgentBuilderParameterTask(
        task_id=uuid.uuid4(),
        group_id=group_id,
        step_id="step_slack",
        node_id="slack",
        node_type="slackPostNode",
        parameter_key="channel",
        label="Slack channel",
        input_type="text",
        required=False,
        defer_policy="allow_unresolved",
        status="active",
        task_version=3,
        stable_order=0,
        reason="Select the Slack channel.",
        input_guidance="Enter a channel name or ID.",
        node_label="Slack",
    )
    group = AgentBuilderParameterGroup(
        group_id=group_id,
        status="active",
        tasks=[channel_task],
    )
    request_id = uuid.uuid4()
    response_payload = {
        "request_id": str(request_id),
        "status": "graph_mutation_ready",
        "parameter_groups": [group.model_dump(mode="json")],
    }
    if include_safe_step_node_ids:
        response_payload["safe_step_node_ids"] = {"step_slack": "slack"}
    request_row = SimpleNamespace(
        id=request_id,
        status="completed",
        created_at=None,
        expires_at=None,
        message_summary="Send a Slack message",
        response_payload=response_payload,
    )
    workflow_id = uuid.uuid4()
    workflow = SimpleNamespace(
        id=workflow_id,
        graph={
            "nodes": [
                {
                    "id": "slack",
                    "type": "slackPostNode",
                    "data": {"title": "Slack", "slackMode": "api"},
                }
            ],
            "edges": [],
        },
    )

    class SessionQuery(FakeQuery):
        def with_for_update(self):
            return self

        def all(self):
            return [self.result] if self.result is not None else []

    class SessionDb(FakeDb):
        def query(self, model):
            if model is service_module.AgentBuilderRequest:
                return SessionQuery(request_row)
            if model is service_module.Workflow:
                return SessionQuery(workflow)
            raise AssertionError(f"unexpected model: {model}")

    monkeypatch.setattr(
        service_module.ParameterCandidateProvider,
        "enrich_group",
        lambda _self, parameter_group, **_kwargs: parameter_group,
    )
    db = SessionDb()
    svc = AgentBuilderService(
        db,
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )

    response = svc._session_response(  # noqa: SLF001
        SimpleNamespace(
            id=uuid.uuid4(),
            workflow_id=workflow_id,
            app_id=None,
            status="active",
            protocol_version="direct_edit_v1",
        )
    )

    expected_keys = [
        "channel",
        "slackMode",
        "bot_token",
        "url",
        "message",
        "blocks",
        "attachments",
        "thread_ts",
        "username",
        "icon_emoji",
    ]
    assert response.parameter_group is not None
    assert [task.parameter_key for task in response.parameter_group.tasks] == expected_keys
    recovered_channel = response.parameter_group.tasks[0]
    assert recovered_channel.task_id == channel_task.task_id
    assert recovered_channel.task_version == channel_task.task_version
    assert recovered_channel.stable_order == channel_task.stable_order
    stored_tasks = request_row.response_payload["parameter_groups"][0]["tasks"]
    assert [task["parameter_key"] for task in stored_tasks] == expected_keys
    stored_by_key = {task["parameter_key"]: task for task in stored_tasks}
    assert stored_by_key["bot_token"]["required"] is True
    assert stored_by_key["bot_token"]["defer_policy"] == "allow_unresolved"
    assert stored_by_key["url"]["required"] is False
    assert stored_by_key["url"]["status"] == "skipped"
    recovered_channel = next(
        task for task in response.parameter_group.tasks if task.parameter_key == "channel"
    )
    assert recovered_channel.task_id == channel_task.task_id
    assert recovered_channel.task_version == 3
    assert recovered_channel.status == "active"


def test_new_workflow_preview_graph_is_deterministic_for_the_same_plan():
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    structured = service_module.AgentBuilderStructuredRequest.model_validate(
        {
            "request_type": "new_workflow",
            "draft_mode": "new_workflow",
            "intent_summary": "receive a webhook and call an API",
            "planned_steps": [
                {
                    "step_id": "step_webhook",
                    "capability": "webhook_trigger",
                    "purpose": "receive input",
                },
                {
                    "step_id": "step_http",
                    "capability": "http_request",
                    "purpose": "call an API",
                    "depends_on": ["step_webhook"],
                },
                {
                    "step_id": "step_answer",
                    "capability": "answer",
                    "purpose": "return the result",
                    "depends_on": ["step_http"],
                },
            ],
            "required_capabilities": [
                "webhook_trigger",
                "http_request",
                "answer",
            ],
        }
    )

    first = svc._build_preview_graph(structured, workflow=None, kb_bindings=[])  # noqa: SLF001
    second = svc._build_preview_graph(structured, workflow=None, kb_bindings=[])  # noqa: SLF001

    assert first == second


def test_modify_workflow_preview_graph_is_deterministic_for_the_same_target(
    monkeypatch,
):
    workflow = SimpleNamespace(
        graph={
            "nodes": [
                {"id": "start", "type": "startNode", "data": {}},
                {"id": "answer", "type": "answerNode", "data": {}},
            ],
            "edges": [
                {"id": "edge-start-answer", "source": "start", "target": "answer"}
            ],
            "viewport": {"x": 0, "y": 0, "zoom": 1},
        }
    )
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(svc, "_recommended_draft_model_id", lambda: "model-1")
    structured = service_module.AgentBuilderStructuredRequest.model_validate(
        {
            "request_type": "modify_workflow",
            "draft_mode": "modify_workflow",
            "intent_summary": "insert an LLM",
            "planned_steps": [
                {
                    "step_id": "step_llm",
                    "capability": "llm",
                    "purpose": "analyze input",
                }
            ],
            "required_capabilities": ["llm"],
        }
    )
    resolution = {
        "status": "resolved",
        "source_node_id": "start",
        "destination_node_id": "answer",
        "replaced_edge_ids": ["edge-start-answer"],
    }

    first = svc._build_preview_graph(  # noqa: SLF001
        structured,
        workflow=workflow,
        kb_bindings=[],
        target_resolution=resolution,
    )
    second = svc._build_preview_graph(  # noqa: SLF001
        structured,
        workflow=workflow,
        kb_bindings=[],
        target_resolution=resolution,
    )

    assert first == second


def test_agent_builder_selected_kb_candidate_validates_prior_clarification_context():
    structured = service_module.AgentBuilderStructuredRequest(
        request_type="new_workflow",
        draft_mode="new_workflow",
        intent_summary="휴가 정책",
        knowledge_requirements=[
            service_module.AgentBuilderKnowledgeRequirement(
                requirement_id="kr_1",
                query_topics=["휴가 정책"],
                target_step_ref="step_llm",
            )
        ],
        pending_resolution=[
            service_module.AgentBuilderPendingResolution(
                resolution_id="res_kb_1",
                slot_type="knowledge_base",
                slot_key="llm.knowledgeBases",
                target_step_ref="step_llm",
            )
        ],
    )
    previous_request = SimpleNamespace(
        status="clarification_required",
        response_payload={
            "clarification_options": [
                {
                    "candidate_id": "safe-rec-1",
                    "resolution_id": "res_kb_1",
                    "requirement_id": "kr_1",
                }
            ],
            "structured_request": structured.model_dump(mode="json"),
        },
        structured_request=structured.model_dump(mode="json"),
    )
    db = FakeDb()
    db.query_result = FakeQuery(previous_request)
    svc = AgentBuilderService(
        db,
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    session = SimpleNamespace(id=uuid.uuid4())

    context = svc._selected_knowledge_candidate_context(  # noqa: SLF001
        session,
        AgentBuilderMessageRequest(
            message="선택한 Knowledge Base로 도안을 생성해줘",
            selected_knowledge_candidate={
                "candidate_id": "safe-rec-1",
                "resolution_id": "res_kb_1",
                "requirement_id": "kr_1",
            },
        ),
    )

    assert context["candidate_handles"] == {"safe-rec-1"}
    assert (
        context["structured_request"].knowledge_requirements[0].requirement_id == "kr_1"
    )


def test_agent_builder_selected_kb_candidates_accepts_multiple_safe_handles():
    structured = service_module.AgentBuilderStructuredRequest(
        request_type="new_workflow",
        draft_mode="new_workflow",
        intent_summary="휴가 정책",
        knowledge_requirements=[
            service_module.AgentBuilderKnowledgeRequirement(
                requirement_id="kr_1",
                query_topics=["휴가 정책"],
                target_step_ref="step_llm",
            )
        ],
        pending_resolution=[
            service_module.AgentBuilderPendingResolution(
                resolution_id="res_kb_1",
                slot_type="knowledge_base",
                slot_key="llm.knowledgeBases",
                target_step_ref="step_llm",
            )
        ],
    )
    previous_request = SimpleNamespace(
        status="clarification_required",
        response_payload={
            "clarification_options": [
                {
                    "candidate_id": "safe-rec-1",
                    "resolution_id": "res_kb_1",
                    "requirement_id": "kr_1",
                },
                {
                    "candidate_id": "safe-rec-2",
                    "resolution_id": "res_kb_1",
                    "requirement_id": "kr_1",
                },
            ],
            "structured_request": structured.model_dump(mode="json"),
        },
        structured_request=structured.model_dump(mode="json"),
    )
    db = FakeDb()
    db.query_result = FakeQuery(previous_request)
    svc = AgentBuilderService(
        db,
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )

    context = svc._selected_knowledge_candidate_context(  # noqa: SLF001
        SimpleNamespace(id=uuid.uuid4()),
        AgentBuilderMessageRequest(
            message="선택한 Knowledge Base로 도안을 생성해줘",
            selected_knowledge_candidates=[
                {
                    "candidate_id": "safe-rec-1",
                    "resolution_id": "res_kb_1",
                    "requirement_id": "kr_1",
                },
                {
                    "candidate_id": "safe-rec-2",
                    "resolution_id": "res_kb_1",
                    "requirement_id": "kr_1",
                },
            ],
        ),
    )

    assert context["candidate_handles"] == {"safe-rec-1", "safe-rec-2"}


def test_agent_builder_empty_kb_selection_uses_no_kb_internal_handle():
    structured = service_module.AgentBuilderStructuredRequest(
        request_type="new_workflow",
        draft_mode="new_workflow",
        intent_summary="휴가 정책",
        knowledge_requirements=[
            service_module.AgentBuilderKnowledgeRequirement(
                requirement_id="kr_1",
                query_topics=["휴가 정책"],
                target_step_ref="step_llm",
            )
        ],
        pending_resolution=[
            service_module.AgentBuilderPendingResolution(
                resolution_id="res_kb_1",
                slot_type="knowledge_base",
                slot_key="llm.knowledgeBases",
                target_step_ref="step_llm",
            )
        ],
    )
    previous_request = SimpleNamespace(
        status="clarification_required",
        response_payload={
            "clarification_options": [
                {
                    "candidate_id": "safe-rec-1",
                    "resolution_id": "res_kb_1",
                    "requirement_id": "kr_1",
                }
            ],
            "structured_request": structured.model_dump(mode="json"),
        },
        structured_request=structured.model_dump(mode="json"),
    )
    db = FakeDb()
    db.query_result = FakeQuery(previous_request)
    svc = AgentBuilderService(
        db,
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )

    context = svc._selected_knowledge_candidate_context(  # noqa: SLF001
        SimpleNamespace(id=uuid.uuid4()),
        AgentBuilderMessageRequest(
            message="Knowledge Base 선택 없이 도안을 생성해줘",
            selected_knowledge_candidates=[],
        ),
    )

    assert context["candidate_handles"] == {service_module.NO_KB_CANDIDATE_ID}


def test_agent_builder_selected_kb_candidate_rejects_mismatched_context():
    structured = service_module.AgentBuilderStructuredRequest(
        request_type="new_workflow",
        draft_mode="new_workflow",
        intent_summary="휴가 정책",
        knowledge_requirements=[],
        pending_resolution=[],
    )
    previous_request = SimpleNamespace(
        status="clarification_required",
        response_payload={
            "clarification_options": [
                {
                    "candidate_id": "safe-rec-1",
                    "resolution_id": "res_kb_1",
                    "requirement_id": "kr_1",
                }
            ],
            "structured_request": structured.model_dump(mode="json"),
        },
        structured_request=structured.model_dump(mode="json"),
    )
    db = FakeDb()
    db.query_result = FakeQuery(previous_request)
    svc = AgentBuilderService(
        db,
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )

    context = svc._selected_knowledge_candidate_context(  # noqa: SLF001
        SimpleNamespace(id=uuid.uuid4()),
        AgentBuilderMessageRequest(
            message="선택한 Knowledge Base로 도안을 생성해줘",
            selected_knowledge_candidate={
                "candidate_id": "safe-rec-1",
                "resolution_id": "other-resolution",
                "requirement_id": "kr_1",
            },
        ),
    )

    assert context == {"error": "candidate_not_in_prior_options"}


def test_agent_builder_selected_kb_candidate_rejects_resolved_clarification():
    structured = service_module.AgentBuilderStructuredRequest(
        request_type="new_workflow",
        draft_mode="new_workflow",
        intent_summary="휴가 정책",
        knowledge_requirements=[
            service_module.AgentBuilderKnowledgeRequirement(
                requirement_id="kr_1",
                query_topics=["휴가 정책"],
                target_step_ref="step_llm",
            )
        ],
        pending_resolution=[
            service_module.AgentBuilderPendingResolution(
                resolution_id="res_kb_1",
                slot_type="knowledge_base",
                slot_key="llm.knowledgeBases",
                target_step_ref="step_llm",
            )
        ],
    )
    db = FakeDb()
    db.query_result = FakeQuery(
        SimpleNamespace(
            status="draft_ready",
            response_payload={
                "clarification_options": [
                    {
                        "candidate_id": "safe-rec-1",
                        "resolution_id": "res_kb_1",
                        "requirement_id": "kr_1",
                    }
                ],
                "structured_request": structured.model_dump(mode="json"),
            },
            structured_request=structured.model_dump(mode="json"),
        )
    )
    svc = AgentBuilderService(
        db,
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )

    context = svc._selected_knowledge_candidate_context(  # noqa: SLF001
        SimpleNamespace(id=uuid.uuid4()),
        AgentBuilderMessageRequest(
            message="Knowledge Base 선택 없이 도안을 생성해줘",
            selected_knowledge_candidates=[],
        ),
    )

    assert context == {"error": "prior_clarification_not_pending"}


def test_agent_builder_apply_blocks_missing_client_preview_hash(monkeypatch):
    class FakeDb:
        def __init__(self):
            self.added = []
            self.committed = False

        def add(self, row):
            self.added.append(row)

        def commit(self):
            self.committed = True

    db = FakeDb()
    draft_id = uuid.uuid4()
    draft = SimpleNamespace(
        id=draft_id,
        request_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        draft_mode="modify_workflow",
        base_graph_hash="base",
        preview_graph={"nodes": [], "edges": []},
        status="ready",
        workflow_id=uuid.uuid4(),
        expires_at=None,
    )
    svc = AgentBuilderService(
        db,
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    lock_calls = []
    monkeypatch.setattr(svc, "_draft_or_404", lambda _draft_id: draft)
    monkeypatch.setattr(
        svc,
        "_lock_draft_for_apply",
        lambda locked_draft: lock_calls.append(locked_draft.id) or locked_draft,
    )

    response = svc.apply_draft(
        draft_id,
        AgentBuilderApplyRequest(action="apply_and_save"),
    )

    assert response.outcome == "blocked"
    assert response.block_reason == "DRAFT_STALE"
    assert response.stale_state == "preview_hash_missing"
    assert response.audit_recorded is True
    assert db.committed is True
    assert draft.status == "ready"
    assert lock_calls == [draft_id, draft_id]


def test_agent_builder_apply_blocks_preview_hash_mismatch(monkeypatch):
    db = FakeDb()
    graph = {"nodes": [], "edges": []}
    draft = _ready_modify_draft(graph)
    svc = AgentBuilderService(
        db,
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(svc, "_draft_or_404", lambda _draft_id: draft)

    response = svc.apply_draft(
        draft.id,
        AgentBuilderApplyRequest(
            action="apply_and_save",
            client_preview_graph_hash="wrong-preview-hash",
        ),
    )

    assert response.outcome == "blocked"
    assert response.block_reason == "DRAFT_STALE"
    assert response.stale_state == "preview_hash_mismatch"
    assert draft.status == "ready"


def test_agent_builder_apply_blocks_canceled_draft_without_overwriting_status(
    monkeypatch,
):
    class FakeDb:
        def __init__(self):
            self.commits = 0

        def add(self, row):
            pass

        def commit(self):
            self.commits += 1

    preview_graph = {"nodes": [], "edges": []}
    draft_id = uuid.uuid4()
    draft = SimpleNamespace(
        id=draft_id,
        request_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        draft_mode="modify_workflow",
        base_graph_hash="base",
        preview_graph=preview_graph,
        status="canceled",
        workflow_id=uuid.uuid4(),
        app_id=None,
        draft_metadata={"workflow_id": str(uuid.uuid4())},
        expires_at=None,
    )
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(svc, "_draft_or_404", lambda _draft_id: draft)

    response = svc.apply_draft(
        draft_id,
        AgentBuilderApplyRequest(
            action="apply_and_save",
            client_preview_graph_hash=calculate_graph_hash(preview_graph),
        ),
    )

    assert response.outcome == "blocked"
    assert response.block_reason == "DRAFT_NOT_APPLICABLE"
    assert draft.status == "canceled"


def test_agent_builder_cancel_does_not_overwrite_applied_draft(monkeypatch):
    preview_graph = {"nodes": [], "edges": []}
    draft = SimpleNamespace(
        id=uuid.uuid4(),
        request_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        draft_mode="modify_workflow",
        base_graph_hash=calculate_graph_hash(preview_graph),
        preview_graph=preview_graph,
        status="applied",
        workflow_id=uuid.uuid4(),
        app_id=None,
        draft_metadata={"workflow_id": str(uuid.uuid4())},
        expires_at=None,
    )
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(svc, "_draft_or_404", lambda _draft_id: draft)

    response = svc.apply_draft(
        draft.id,
        AgentBuilderApplyRequest(action="cancel"),
    )

    assert response.outcome == "blocked"
    assert response.block_reason == "DRAFT_NOT_APPLICABLE"
    assert draft.status == "applied"


def test_agent_builder_cancel_records_audit_without_terminally_canceling_ready_draft(
    monkeypatch,
):
    preview_graph = {"nodes": [], "edges": []}
    draft = SimpleNamespace(
        id=uuid.uuid4(),
        request_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        draft_mode="modify_workflow",
        base_graph_hash=calculate_graph_hash(preview_graph),
        preview_graph=preview_graph,
        status="ready",
        workflow_id=uuid.uuid4(),
        app_id=None,
        draft_metadata={"workflow_id": str(uuid.uuid4())},
        expires_at=None,
    )
    db = FakeDb()
    svc = AgentBuilderService(
        db,
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(svc, "_draft_or_404", lambda _draft_id: draft)

    response = svc.apply_draft(
        draft.id,
        AgentBuilderApplyRequest(action="cancel"),
    )

    assert response.outcome == "canceled"
    assert response.audit_recorded is True
    assert draft.status == "ready"
    assert db.commits == 2


def test_agent_builder_apply_requires_workflow_write_permission(monkeypatch):
    db = FakeDb()
    workflow_id = uuid.uuid4()
    graph = {"nodes": [], "edges": []}
    workflow = SimpleNamespace(
        id=workflow_id,
        graph=graph,
        updated_at=None,
        app_id=uuid.uuid4(),
    )
    draft = _ready_modify_draft(graph, workflow_id=workflow_id)
    svc = AgentBuilderService(
        db,
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(svc, "_draft_or_404", lambda _draft_id: draft)
    monkeypatch.setattr(svc, "_workflow_in_active_org", lambda _workflow_id: workflow)
    monkeypatch.setattr(
        service_module, "has_workflow_permission", lambda *args, **kwargs: False
    )

    response = svc.apply_draft(
        draft.id,
        AgentBuilderApplyRequest(
            action="apply_and_save",
            client_preview_graph_hash=calculate_graph_hash(graph),
        ),
    )

    assert response.outcome == "blocked"
    assert response.block_reason == "WORKFLOW_PERMISSION_REQUIRED"
    assert response.permission_recheck_outcome == "denied"
    assert response.audit_recorded is True
    assert draft.status == "ready"


def test_agent_builder_apply_blocks_missing_latest_graph_hash(monkeypatch):
    db = FakeDb()
    workflow_id = uuid.uuid4()
    graph = {"nodes": [], "edges": []}
    workflow = SimpleNamespace(
        id=workflow_id,
        graph=graph,
        updated_at=None,
        app_id=uuid.uuid4(),
    )
    draft = _ready_modify_draft(graph, workflow_id=workflow_id)
    svc = AgentBuilderService(
        db,
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(svc, "_draft_or_404", lambda _draft_id: draft)
    monkeypatch.setattr(svc, "_workflow_in_active_org", lambda _workflow_id: workflow)
    monkeypatch.setattr(
        service_module, "has_workflow_permission", lambda *args, **kwargs: True
    )

    response = svc.apply_draft(
        draft.id,
        AgentBuilderApplyRequest(
            action="apply_and_save",
            client_preview_graph_hash=calculate_graph_hash(graph),
        ),
    )

    assert response.outcome == "blocked"
    assert response.block_reason == "UNSAVED_EDITOR_CHANGES"
    assert response.stale_state == "client_graph_hash_missing"


def test_agent_builder_apply_blocks_latest_graph_hash_mismatch(monkeypatch):
    db = FakeDb()
    workflow_id = uuid.uuid4()
    graph = {"nodes": [], "edges": []}
    workflow = SimpleNamespace(
        id=workflow_id,
        graph=graph,
        updated_at=None,
        app_id=uuid.uuid4(),
    )
    draft = _ready_modify_draft(graph, workflow_id=workflow_id)
    svc = AgentBuilderService(
        db,
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(svc, "_draft_or_404", lambda _draft_id: draft)
    monkeypatch.setattr(svc, "_workflow_in_active_org", lambda _workflow_id: workflow)
    monkeypatch.setattr(
        service_module, "has_workflow_permission", lambda *args, **kwargs: True
    )

    response = svc.apply_draft(
        draft.id,
        AgentBuilderApplyRequest(
            action="apply_and_save",
            client_preview_graph_hash=calculate_graph_hash(graph),
            client_latest_graph_hash="wrong-latest-hash",
        ),
    )

    assert response.outcome == "blocked"
    assert response.block_reason == "UNSAVED_EDITOR_CHANGES"
    assert response.stale_state == "client_graph_mismatch"


def test_agent_builder_apply_blocks_stale_base_graph_hash(monkeypatch):
    db = FakeDb()
    workflow_id = uuid.uuid4()
    graph = {"nodes": [], "edges": []}
    changed_graph = {
        "nodes": [{"id": "n1", "type": "startNode", "data": {}}],
        "edges": [],
    }
    workflow = SimpleNamespace(
        id=workflow_id,
        graph=changed_graph,
        updated_at=None,
        app_id=uuid.uuid4(),
    )
    draft = _ready_modify_draft(graph, workflow_id=workflow_id)
    svc = AgentBuilderService(
        db,
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(svc, "_draft_or_404", lambda _draft_id: draft)
    monkeypatch.setattr(svc, "_workflow_in_active_org", lambda _workflow_id: workflow)
    monkeypatch.setattr(
        service_module, "has_workflow_permission", lambda *args, **kwargs: True
    )

    response = svc.apply_draft(
        draft.id,
        AgentBuilderApplyRequest(
            action="apply_and_save",
            client_preview_graph_hash=calculate_graph_hash(graph),
            client_latest_graph_hash=calculate_graph_hash(changed_graph),
        ),
    )

    assert response.outcome == "blocked"
    assert response.block_reason == "DRAFT_STALE"
    assert response.stale_state == "stale"


def test_agent_builder_apply_blocks_stale_workflow_updated_at(monkeypatch):
    db = FakeDb()
    workflow_id = uuid.uuid4()
    graph = {"nodes": [], "edges": []}
    base_updated_at = service_module.datetime(
        2026, 1, 1, tzinfo=service_module.timezone.utc
    )
    workflow = SimpleNamespace(
        id=workflow_id,
        graph=graph,
        updated_at=service_module.datetime(
            2026, 1, 2, tzinfo=service_module.timezone.utc
        ),
        app_id=uuid.uuid4(),
    )
    draft = _ready_modify_draft(graph, workflow_id=workflow_id)
    draft.base_workflow_updated_at = base_updated_at
    svc = AgentBuilderService(
        db,
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(svc, "_draft_or_404", lambda _draft_id: draft)
    monkeypatch.setattr(svc, "_workflow_in_active_org", lambda _workflow_id: workflow)
    monkeypatch.setattr(
        service_module, "has_workflow_permission", lambda *args, **kwargs: True
    )

    response = svc.apply_draft(
        draft.id,
        AgentBuilderApplyRequest(
            action="apply_and_save",
            client_preview_graph_hash=calculate_graph_hash(graph),
            client_latest_graph_hash=calculate_graph_hash(graph),
        ),
    )

    assert response.outcome == "blocked"
    assert response.block_reason == "DRAFT_STALE"
    assert response.stale_state == "stale"


def test_agent_builder_apply_commit_success_refresh_failure_still_returns_saved(
    monkeypatch,
):
    class RefreshFailingDb(FakeDb):
        def refresh(self, row):
            raise RuntimeError("refresh failed")

    db = RefreshFailingDb()
    workflow_id = uuid.uuid4()
    graph = {"nodes": [], "edges": []}
    workflow = SimpleNamespace(
        id=workflow_id,
        graph=graph,
        updated_at=None,
        app_id=uuid.uuid4(),
        updated_by=None,
    )
    draft = _ready_modify_draft(graph, workflow_id=workflow_id)
    svc = AgentBuilderService(
        db,
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(svc, "_draft_or_404", lambda _draft_id: draft)
    monkeypatch.setattr(svc, "_workflow_in_active_org", lambda _workflow_id: workflow)
    monkeypatch.setattr(
        service_module, "has_workflow_permission", lambda *args, **kwargs: True
    )
    monkeypatch.setattr(svc, "_runtime_kb_bindings_for_apply", lambda _draft: [])

    response = svc.apply_draft(
        draft.id,
        AgentBuilderApplyRequest(
            action="apply_and_save",
            client_preview_graph_hash=calculate_graph_hash(graph),
            client_latest_graph_hash=calculate_graph_hash(graph),
        ),
    )

    assert response.outcome == "saved"
    assert response.saved_workflow_id == workflow_id
    assert response.audit_recorded is True
    assert response.layout_optimization_applied is True
    assert db.rollbacks == 0


def test_agent_builder_apply_requires_app_create_scope(monkeypatch):
    db = FakeDb()
    app_id = uuid.uuid4()
    graph = {"nodes": [], "edges": []}
    draft = SimpleNamespace(
        id=uuid.uuid4(),
        request_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        draft_mode="new_workflow",
        base_graph_hash=calculate_graph_hash(graph),
        base_workflow_updated_at=None,
        preview_graph=graph,
        status="ready",
        workflow_id=None,
        app_id=app_id,
        draft_metadata={},
        expires_at=None,
    )
    svc = AgentBuilderService(
        db,
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(svc, "_draft_or_404", lambda _draft_id: draft)
    monkeypatch.setattr(
        svc, "_app_in_active_org", lambda _app_id: SimpleNamespace(id=app_id)
    )
    monkeypatch.setattr(
        service_module.AppService,
        "access_denial_status",
        lambda *args, **kwargs: 403,
    )

    response = svc.apply_draft(
        draft.id,
        AgentBuilderApplyRequest(
            action="apply_and_save",
            client_preview_graph_hash=calculate_graph_hash(graph),
        ),
    )

    assert response.outcome == "blocked"
    assert response.block_reason == "APP_CREATE_PERMISSION_REQUIRED"
    assert response.permission_recheck_outcome == "denied"
    assert response.audit_recorded is True
    assert draft.status == "ready"


def test_agent_builder_new_workflow_promotes_app_primary(monkeypatch):
    db = FakeDb()
    app_id = uuid.uuid4()
    old_workflow_id = uuid.uuid4()
    graph = {
        "nodes": [
            {"id": "start", "type": "startNode", "data": {}},
            {"id": "answer", "type": "answerNode", "data": {}},
        ],
        "edges": [{"id": "start-answer", "source": "start", "target": "answer"}],
    }
    app = SimpleNamespace(
        id=app_id,
        workflow_id=old_workflow_id,
        active_deployment_id=None,
    )
    draft = SimpleNamespace(
        id=uuid.uuid4(),
        request_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        draft_mode="new_workflow",
        base_graph_hash=calculate_graph_hash(graph),
        base_workflow_updated_at=None,
        preview_graph=graph,
        status="ready",
        workflow_id=None,
        app_id=app_id,
        draft_metadata={
            service_module.EXPECTED_APP_PRIMARY_WORKFLOW_ID: str(old_workflow_id)
        },
        expires_at=None,
    )
    svc = AgentBuilderService(
        db,
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )

    def flush_with_workflow_id():
        db.flushed = True
        for row in db.added:
            if isinstance(row, service_module.Workflow) and row.id is None:
                row.id = uuid.uuid4()

    db.flush = flush_with_workflow_id
    monkeypatch.setattr(svc, "_draft_or_404", lambda _draft_id: draft)
    monkeypatch.setattr(svc, "_app_in_active_org", lambda _app_id: app)
    monkeypatch.setattr(
        service_module.AppService,
        "access_denial_status",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        service_module.AppService,
        "_inherit_primary_workflow_permissions",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        service_module.WorkflowBudgetService,
        "has_active_budget",
        lambda *args, **kwargs: False,
    )
    monkeypatch.setattr(svc, "_runtime_kb_bindings_for_apply", lambda _draft: [])
    monkeypatch.setattr(
        service_module.WorkflowService,
        "validate_mail_credential_references",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        svc,
        "_rebind_session_after_new_workflow_apply",
        lambda _draft, _workflow: None,
    )
    monkeypatch.setattr(
        service_module,
        "add_action_audit",
        lambda *args, **kwargs: None,
    )

    response = svc.apply_draft(
        draft.id,
        AgentBuilderApplyRequest(
            action="apply_and_save",
            client_preview_graph_hash=calculate_graph_hash(graph),
        ),
    )

    assert response.outcome == "saved"
    assert app.workflow_id == response.saved_workflow_id
    assert app.workflow_id != old_workflow_id


@pytest.mark.parametrize(
    ("draft_metadata", "expected_stale_state"),
    [
        ({}, "app_primary_expected_missing"),
        (
            {service_module.EXPECTED_APP_PRIMARY_WORKFLOW_ID: "not-a-uuid"},
            "app_primary_expected_invalid",
        ),
    ],
)
def test_agent_builder_new_workflow_blocks_missing_or_invalid_primary_metadata(
    monkeypatch,
    draft_metadata,
    expected_stale_state,
):
    db = FakeDb()
    app_id = uuid.uuid4()
    old_workflow_id = uuid.uuid4()
    graph = {"nodes": [], "edges": []}
    app = SimpleNamespace(
        id=app_id,
        workflow_id=old_workflow_id,
        active_deployment_id=None,
    )
    draft = SimpleNamespace(
        id=uuid.uuid4(),
        request_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        draft_mode="new_workflow",
        base_graph_hash=calculate_graph_hash(graph),
        base_workflow_updated_at=None,
        preview_graph=graph,
        status="ready",
        workflow_id=None,
        app_id=app_id,
        draft_metadata=draft_metadata,
        expires_at=None,
    )
    svc = AgentBuilderService(
        db,
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(svc, "_draft_or_404", lambda _draft_id: draft)
    monkeypatch.setattr(svc, "_app_in_active_org", lambda _app_id: app)
    monkeypatch.setattr(
        service_module.AppService,
        "access_denial_status",
        lambda *args, **kwargs: None,
    )

    response = svc.apply_draft(
        draft.id,
        AgentBuilderApplyRequest(
            action="apply_and_save",
            client_preview_graph_hash=calculate_graph_hash(graph),
        ),
    )

    assert response.outcome == "blocked"
    assert response.block_reason == "DRAFT_METADATA_NOT_FOUND"
    assert response.stale_state == expected_stale_state
    assert app.workflow_id == old_workflow_id
    assert not any(isinstance(row, service_module.Workflow) for row in db.added)


def test_agent_builder_new_workflow_blocks_stale_app_primary(monkeypatch):
    db = FakeDb()
    app_id = uuid.uuid4()
    expected_workflow_id = uuid.uuid4()
    current_workflow_id = uuid.uuid4()
    graph = {"nodes": [], "edges": []}
    app = SimpleNamespace(
        id=app_id,
        workflow_id=current_workflow_id,
        active_deployment_id=None,
    )
    draft = SimpleNamespace(
        id=uuid.uuid4(),
        request_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        draft_mode="new_workflow",
        base_graph_hash=calculate_graph_hash(graph),
        base_workflow_updated_at=None,
        preview_graph=graph,
        status="ready",
        workflow_id=None,
        app_id=app_id,
        draft_metadata={
            service_module.EXPECTED_APP_PRIMARY_WORKFLOW_ID: str(expected_workflow_id)
        },
        expires_at=None,
    )
    svc = AgentBuilderService(
        db,
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(svc, "_draft_or_404", lambda _draft_id: draft)
    monkeypatch.setattr(svc, "_app_in_active_org", lambda _app_id: app)
    monkeypatch.setattr(
        service_module.AppService,
        "access_denial_status",
        lambda *args, **kwargs: None,
    )

    response = svc.apply_draft(
        draft.id,
        AgentBuilderApplyRequest(
            action="apply_and_save",
            client_preview_graph_hash=calculate_graph_hash(graph),
        ),
    )

    assert response.outcome == "blocked"
    assert response.block_reason == "DRAFT_STALE"
    assert response.stale_state == "app_primary_changed"
    assert app.workflow_id == current_workflow_id
    assert not any(isinstance(row, service_module.Workflow) for row in db.added)


def test_agent_builder_new_workflow_blocks_active_deployment(monkeypatch):
    db = FakeDb()
    app_id = uuid.uuid4()
    old_workflow_id = uuid.uuid4()
    graph = {"nodes": [], "edges": []}
    app = SimpleNamespace(
        id=app_id,
        workflow_id=old_workflow_id,
        active_deployment_id=uuid.uuid4(),
    )
    draft = SimpleNamespace(
        id=uuid.uuid4(),
        request_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        draft_mode="new_workflow",
        base_graph_hash=calculate_graph_hash(graph),
        base_workflow_updated_at=None,
        preview_graph=graph,
        status="ready",
        workflow_id=None,
        app_id=app_id,
        draft_metadata={
            service_module.EXPECTED_APP_PRIMARY_WORKFLOW_ID: str(old_workflow_id)
        },
        expires_at=None,
    )
    svc = AgentBuilderService(
        db,
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(svc, "_draft_or_404", lambda _draft_id: draft)
    monkeypatch.setattr(svc, "_app_in_active_org", lambda _app_id: app)
    monkeypatch.setattr(
        service_module.AppService,
        "access_denial_status",
        lambda *args, **kwargs: None,
    )

    response = svc.apply_draft(
        draft.id,
        AgentBuilderApplyRequest(
            action="apply_and_save",
            client_preview_graph_hash=calculate_graph_hash(graph),
        ),
    )

    assert response.outcome == "blocked"
    assert response.block_reason == "APP_ACTIVE_DEPLOYMENT_CONFLICT"
    assert response.stale_state == "active_deployment_present"
    assert app.workflow_id == old_workflow_id
    assert not any(isinstance(row, service_module.Workflow) for row in db.added)


def test_agent_builder_new_workflow_blocks_active_primary_budget(monkeypatch):
    db = FakeDb()
    app_id = uuid.uuid4()
    old_workflow_id = uuid.uuid4()
    graph = {"nodes": [], "edges": []}
    app = SimpleNamespace(
        id=app_id,
        workflow_id=old_workflow_id,
        active_deployment_id=None,
    )
    draft = SimpleNamespace(
        id=uuid.uuid4(),
        request_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        draft_mode="new_workflow",
        base_graph_hash=calculate_graph_hash(graph),
        base_workflow_updated_at=None,
        preview_graph=graph,
        status="ready",
        workflow_id=None,
        app_id=app_id,
        draft_metadata={
            service_module.EXPECTED_APP_PRIMARY_WORKFLOW_ID: str(old_workflow_id)
        },
        expires_at=None,
    )
    svc = AgentBuilderService(
        db,
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    budget_checks = []
    monkeypatch.setattr(svc, "_draft_or_404", lambda _draft_id: draft)
    monkeypatch.setattr(svc, "_app_in_active_org", lambda _app_id: app)
    monkeypatch.setattr(
        service_module.AppService,
        "access_denial_status",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        service_module.WorkflowBudgetService,
        "has_active_budget",
        lambda *args, **kwargs: budget_checks.append(kwargs) or True,
    )

    response = svc.apply_draft(
        draft.id,
        AgentBuilderApplyRequest(
            action="apply_and_save",
            client_preview_graph_hash=calculate_graph_hash(graph),
        ),
    )

    assert response.outcome == "blocked"
    assert response.block_reason == "APP_WORKFLOW_BUDGET_CONFLICT"
    assert response.stale_state == "active_workflow_budget_present"
    assert budget_checks == [
        {
            "workflow_id": old_workflow_id,
            "organization_id": svc.organization_id,
        }
    ]
    assert app.workflow_id == old_workflow_id
    assert not any(isinstance(row, service_module.Workflow) for row in db.added)


def test_agent_builder_new_workflow_rolls_back_permission_inheritance_failure(
    monkeypatch,
):
    db = FakeDb()
    app_id = uuid.uuid4()
    old_workflow_id = uuid.uuid4()
    graph = {"nodes": [], "edges": []}
    app = SimpleNamespace(
        id=app_id,
        workflow_id=old_workflow_id,
        active_deployment_id=None,
    )
    draft = SimpleNamespace(
        id=uuid.uuid4(),
        request_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        draft_mode="new_workflow",
        base_graph_hash=calculate_graph_hash(graph),
        base_workflow_updated_at=None,
        preview_graph=graph,
        status="ready",
        workflow_id=None,
        app_id=app_id,
        draft_metadata={
            service_module.EXPECTED_APP_PRIMARY_WORKFLOW_ID: str(old_workflow_id)
        },
        expires_at=None,
    )
    svc = AgentBuilderService(
        db,
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )

    def flush_with_workflow_id():
        for row in db.added:
            if isinstance(row, service_module.Workflow) and row.id is None:
                row.id = uuid.uuid4()

    db.flush = flush_with_workflow_id
    monkeypatch.setattr(svc, "_draft_or_404", lambda _draft_id: draft)
    monkeypatch.setattr(svc, "_app_in_active_org", lambda _app_id: app)
    monkeypatch.setattr(
        service_module.AppService,
        "access_denial_status",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        service_module.AppService,
        "_inherit_primary_workflow_permissions",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("db failure")),
    )
    monkeypatch.setattr(
        service_module.WorkflowBudgetService,
        "has_active_budget",
        lambda *args, **kwargs: False,
    )
    monkeypatch.setattr(svc, "_runtime_kb_bindings_for_apply", lambda _draft: [])
    monkeypatch.setattr(
        service_module.WorkflowService,
        "validate_mail_credential_references",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        service_module,
        "add_action_audit",
        lambda *args, **kwargs: None,
    )

    response = svc.apply_draft(
        draft.id,
        AgentBuilderApplyRequest(
            action="apply_and_save",
            client_preview_graph_hash=calculate_graph_hash(graph),
        ),
    )

    assert response.outcome == "failed"
    assert response.failure_reason == "SAVE_FAILED"
    assert db.rollbacks == 1
    assert app.workflow_id == old_workflow_id


def test_agent_builder_preview_splices_generated_chain_into_selected_edge(monkeypatch):
    workflow = SimpleNamespace(
        graph={
            "nodes": [
                {"id": "start", "type": "startNode", "data": {}},
                {"id": "answer", "type": "answerNode", "data": {}},
            ],
            "edges": [
                {"id": "edge-start-answer", "source": "start", "target": "answer"}
            ],
        }
    )
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(svc, "_recommended_draft_model_id", lambda: "model-1")
    structured = svc._build_structured_request(  # noqa: SLF001
        AgentBuilderMessageRequest(
            message="이 연결 사이에 LLM 노드를 추가해줘",
            selected_edge_id="edge-start-answer",
        ),
        workflow=workflow,
    )
    resolution = svc._resolve_edit_target(  # noqa: SLF001
        structured,
        workflow=workflow,
        selected_node_id=None,
        selected_edge_id="edge-start-answer",
    )

    preview = svc._build_preview_graph(  # noqa: SLF001
        structured,
        workflow=workflow,
        kb_bindings=[],
        selected_edge_id="edge-start-answer",
        target_resolution=resolution,
    )

    edge_ids = {edge.get("id") for edge in preview["edges"]}
    assert "edge-start-answer" not in edge_ids
    generated_nodes = [
        node for node in preview["nodes"] if str(node["id"]).startswith("agent-")
    ]
    assert [node["type"] for node in generated_nodes] == ["llmNode"]
    generated_id = generated_nodes[0]["id"]
    assert {("start", generated_id), (generated_id, "answer")} <= {
        (edge.get("source"), edge.get("target")) for edge in preview["edges"]
    }


def test_agent_builder_preview_auto_layouts_new_workflow_chain(monkeypatch):
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(svc, "_recommended_draft_model_id", lambda: "model-1")
    structured = service_module.AgentBuilderStructuredRequest(
        request_type="new_workflow",
        draft_mode="new_workflow",
        intent_summary="create",
        required_capabilities=["start_input", "llm", "answer"],
    )

    preview = svc._build_preview_graph(  # noqa: SLF001
        structured,
        workflow=None,
        kb_bindings=[],
    )

    nodes_by_type = {node["type"]: node for node in preview["nodes"]}
    start_position = nodes_by_type["startNode"]["position"]
    llm_position = nodes_by_type["llmNode"]["position"]
    answer_position = nodes_by_type["answerNode"]["position"]

    assert start_position["x"] < llm_position["x"] < answer_position["x"]
    assert start_position["y"] == llm_position["y"] == answer_position["y"]


def test_agent_builder_preview_generates_valid_slack_node_when_requested(monkeypatch):
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(svc, "_recommended_draft_model_id", lambda: "model-1")
    structured = service_module.AgentBuilderStructuredRequest(
        request_type="new_workflow",
        draft_mode="new_workflow",
        intent_summary="send to Slack",
        required_capabilities=["start_input", "llm", "slack_send", "answer"],
    )

    preview = svc._build_preview_graph(  # noqa: SLF001
        structured,
        workflow=None,
        kb_bindings=[],
    )

    nodes_by_type = {node["type"]: node for node in preview["nodes"]}
    assert "slackPostNode" in nodes_by_type
    slack_node = nodes_by_type["slackPostNode"]
    assert slack_node["data"]["channel"] == ""
    assert slack_node["data"]["authConfig"] == {}
    assert "body" not in slack_node["data"]
    assert "headers" not in slack_node["data"]
    assert "timeout" not in slack_node["data"]
    assert "delivery_status" == service_module.CAPABILITY_OUTPUT_KEYS["slack_send"]
    assert any(
        edge.get("source") == nodes_by_type["llmNode"]["id"]
        and edge.get("target") == slack_node["id"]
        for edge in preview["edges"]
    )
    assert svc.validate_preview_graph(preview).valid is True


def test_agent_builder_preview_auto_layout_avoids_existing_node_overlap(monkeypatch):
    workflow = SimpleNamespace(
        graph={
            "nodes": [
                {
                    "id": "selected",
                    "type": "startNode",
                    "position": {"x": 0, "y": 0},
                    "data": {},
                },
                {
                    "id": "occupied",
                    "type": "answerNode",
                    "position": {"x": 360, "y": 0},
                    "data": {},
                },
            ],
            "edges": [],
        }
    )
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(svc, "_recommended_draft_model_id", lambda: "model-1")
    structured = svc._build_structured_request(  # noqa: SLF001
        AgentBuilderMessageRequest(
            message="이 노드 뒤에 LLM 노드를 추가해줘",
            selected_node_id="selected",
        ),
        workflow=workflow,
    )
    resolution = svc._resolve_edit_target(  # noqa: SLF001
        structured,
        workflow=workflow,
        selected_node_id="selected",
        selected_edge_id=None,
    )

    preview = svc._build_preview_graph(  # noqa: SLF001
        structured,
        workflow=workflow,
        kb_bindings=[],
        selected_node_id="selected",
        target_resolution=resolution,
    )

    generated_nodes = [
        node for node in preview["nodes"] if str(node["id"]).startswith("agent-")
    ]
    generated_positions = [node["position"] for node in generated_nodes]

    assert generated_positions == [{"x": 360.0, "y": 220.0}]


def test_agent_builder_selected_edge_uses_structured_target_without_message_regex():
    workflow = SimpleNamespace(
        graph={
            "nodes": [],
            "edges": [
                {"id": "edge-start-answer", "source": "start", "target": "answer"}
            ],
        }
    )
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )

    structured = AgentBuilderStructuredRequest(
        request_type="modify_workflow",
        draft_mode="modify_workflow",
        intent_summary="선택된 연결에 LLM을 추가합니다.",
        planned_steps=[
            AgentBuilderPlannedStep(step_id="step_llm", capability="llm", purpose="LLM")
        ],
        required_capabilities=["llm"],
        edit_operations=[
            AgentBuilderEditOperation(
                operation_id="edit_1",
                operation="insert",
                placement="between",
                step_refs=["step_llm"],
                target=AgentBuilderEditTargetReference(reference_type="selected_edge"),
            )
        ],
    )

    assert (
        svc._selected_edge_id_for_structured_request(  # noqa: SLF001
            workflow,
            "edge-start-answer",
            structured,
        )
        == "edge-start-answer"
    )


def test_agent_builder_save_graph_removes_selected_edge_when_spliced():
    base_graph = {
        "nodes": [
            {"id": "start", "type": "startNode", "data": {}},
            {"id": "answer", "type": "answerNode", "data": {}},
        ],
        "edges": [{"id": "edge-start-answer", "source": "start", "target": "answer"}],
    }
    preview_graph = {
        "nodes": [
            *base_graph["nodes"],
            {"id": "agent-input", "type": "startNode", "data": {}},
            {"id": "agent-llm", "type": "llmNode", "data": {"model_id": "model-1"}},
            {"id": "agent-answer", "type": "answerNode", "data": {}},
        ],
        "edges": [
            {
                "id": "edge-start-agent-input",
                "source": "start",
                "target": "agent-input",
            },
            {
                "id": "edge-agent-input-agent-llm",
                "source": "agent-input",
                "target": "agent-llm",
            },
            {
                "id": "edge-agent-llm-agent-answer",
                "source": "agent-llm",
                "target": "agent-answer",
            },
            {
                "id": "edge-agent-answer-answer",
                "source": "agent-answer",
                "target": "answer",
            },
        ],
    }
    draft = SimpleNamespace(
        preview_graph=preview_graph,
        draft_mode="modify_workflow",
        draft_metadata={
            "generated_node_ids": ["agent-input", "agent-llm", "agent-answer"],
            "generated_edge_ids": [
                "edge-start-agent-input",
                "edge-agent-input-agent-llm",
                "edge-agent-llm-agent-answer",
                "edge-agent-answer-answer",
            ],
            "target_resolution": {"selected_edge_id": "edge-start-answer"},
        },
    )
    workflow = SimpleNamespace(graph=base_graph)
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )

    save_graph = svc._graph_for_apply(  # noqa: SLF001
        draft,
        workflow,
        runtime_kb_bindings=[],
    )

    edge_ids = {edge["id"] for edge in save_graph["edges"]}
    assert "edge-start-answer" not in edge_ids
    assert "edge-start-agent-input" in edge_ids
    assert "edge-agent-answer-answer" in edge_ids


def test_agent_builder_graph_for_apply_optimizes_layout_before_save():
    preview_graph = {
        "nodes": [
            {
                "id": "agent-input",
                "type": "startNode",
                "position": {"x": 0, "y": 0},
                "data": {},
            },
            {
                "id": "agent-llm",
                "type": "llmNode",
                "position": {"x": 0, "y": 0},
                "data": {},
            },
            {
                "id": "agent-answer",
                "type": "answerNode",
                "position": {"x": 0, "y": 0},
                "data": {},
            },
        ],
        "edges": [
            {
                "id": "edge-agent-input-agent-llm",
                "source": "agent-input",
                "target": "agent-llm",
            },
            {
                "id": "edge-agent-llm-agent-answer",
                "source": "agent-llm",
                "target": "agent-answer",
            },
        ],
    }
    draft = SimpleNamespace(
        preview_graph=preview_graph,
        draft_mode="new_workflow",
        draft_metadata={
            "generated_node_ids": ["agent-input", "agent-llm", "agent-answer"]
        },
    )
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )

    save_graph = svc._graph_for_apply(  # noqa: SLF001
        draft,
        None,
        runtime_kb_bindings=[],
    )

    positions = {node["id"]: node["position"] for node in save_graph["nodes"]}
    assert positions["agent-input"]["x"] < positions["agent-llm"]["x"]
    assert positions["agent-llm"]["x"] < positions["agent-answer"]["x"]
    assert {
        positions["agent-input"]["y"],
        positions["agent-llm"]["y"],
        positions["agent-answer"]["y"],
    } == {0}


def test_agent_builder_apply_persists_optimized_layout(monkeypatch):
    db = FakeDb()
    workflow_id = uuid.uuid4()
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
        ],
        "edges": [{"id": "edge-start-answer", "source": "start", "target": "answer"}],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }
    preview_graph = {
        "nodes": [
            *copy.deepcopy(base_graph["nodes"]),
            {
                "id": "agent-llm",
                "type": "llmNode",
                "position": {"x": 0, "y": 0},
                "data": {"model_id": "model-1"},
            },
        ],
        "edges": [
            {"id": "edge-start-agent-llm", "source": "start", "target": "agent-llm"},
            {"id": "edge-agent-llm-answer", "source": "agent-llm", "target": "answer"},
        ],
        "_nodease_runtime": {
            "workflow_node_bindings": {"version": "forged", "entries": []}
        },
    }
    workflow = SimpleNamespace(
        id=workflow_id,
        graph=copy.deepcopy(base_graph),
        updated_at=None,
        app_id=uuid.uuid4(),
        updated_by=None,
    )
    draft = SimpleNamespace(
        id=uuid.uuid4(),
        request_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        draft_mode="modify_workflow",
        base_graph_hash=calculate_graph_hash(base_graph),
        base_workflow_updated_at=None,
        preview_graph=preview_graph,
        status="ready",
        workflow_id=workflow_id,
        app_id=None,
        draft_metadata={
            "workflow_id": str(workflow_id),
            "generated_node_ids": ["agent-llm"],
            "generated_edge_ids": [
                "edge-start-agent-llm",
                "edge-agent-llm-answer",
            ],
            "target_resolution": {
                "replaced_edge_ids": ["edge-start-answer"],
            },
        },
        expires_at=None,
    )
    svc = AgentBuilderService(
        db,
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(svc, "_draft_or_404", lambda _draft_id: draft)
    monkeypatch.setattr(svc, "_workflow_in_active_org", lambda _workflow_id: workflow)
    monkeypatch.setattr(
        service_module, "has_workflow_permission", lambda *args, **kwargs: True
    )
    monkeypatch.setattr(svc, "_runtime_kb_bindings_for_apply", lambda _draft: [])

    response = svc.apply_draft(
        draft.id,
        AgentBuilderApplyRequest(
            action="apply_and_save",
            client_preview_graph_hash=calculate_graph_hash(preview_graph),
            client_latest_graph_hash=calculate_graph_hash(base_graph),
        ),
    )

    assert response.outcome == "saved"
    assert response.layout_optimization_applied is True
    positions = {node["id"]: node["position"] for node in workflow.graph["nodes"]}
    assert positions["start"]["x"] < positions["agent-llm"]["x"]
    assert positions["agent-llm"]["x"] < positions["answer"]["x"]
    assert {position["y"] for position in positions.values()} == {0}
    assert "_nodease_runtime" not in workflow.graph


def test_agent_builder_apply_rejects_invalid_connection_policy(monkeypatch):
    db = FakeDb()
    workflow_id = uuid.uuid4()
    base_graph = {
        "nodes": [
            {"id": "start", "type": "startNode", "data": {}},
            {"id": "answer", "type": "answerNode", "data": {}},
        ],
        "edges": [{"id": "edge-start-answer", "source": "start", "target": "answer"}],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }
    preview_graph = {
        "nodes": [
            *copy.deepcopy(base_graph["nodes"]),
            {
                "id": "agent-llm",
                "type": "llmNode",
                "data": {"model_id": "model-1"},
            },
        ],
        "edges": [
            *copy.deepcopy(base_graph["edges"]),
            {"id": "edge-answer-llm", "source": "answer", "target": "agent-llm"},
        ],
    }
    workflow = SimpleNamespace(
        id=workflow_id,
        graph=copy.deepcopy(base_graph),
        updated_at=None,
        app_id=uuid.uuid4(),
        updated_by=None,
    )
    draft = SimpleNamespace(
        id=uuid.uuid4(),
        request_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        draft_mode="modify_workflow",
        base_graph_hash=calculate_graph_hash(base_graph),
        base_workflow_updated_at=None,
        preview_graph=preview_graph,
        status="ready",
        workflow_id=workflow_id,
        app_id=None,
        draft_metadata={
            "workflow_id": str(workflow_id),
            "generated_node_ids": ["agent-llm"],
            "generated_edge_ids": ["edge-answer-llm"],
        },
        expires_at=None,
    )
    svc = AgentBuilderService(
        db,
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(svc, "_draft_or_404", lambda _draft_id: draft)
    monkeypatch.setattr(svc, "_workflow_in_active_org", lambda _workflow_id: workflow)
    monkeypatch.setattr(
        service_module, "has_workflow_permission", lambda *args, **kwargs: True
    )
    monkeypatch.setattr(svc, "_runtime_kb_bindings_for_apply", lambda _draft: [])

    response = svc.apply_draft(
        draft.id,
        AgentBuilderApplyRequest(
            action="apply_and_save",
            client_preview_graph_hash=calculate_graph_hash(preview_graph),
            client_latest_graph_hash=calculate_graph_hash(base_graph),
        ),
    )

    assert response.outcome == "blocked"
    assert response.block_reason == "DRAFT_VALIDATION_FAILED"
    assert workflow.graph == base_graph


def test_agent_builder_draft_without_recommended_model_stays_unresolved():
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    structured = svc._build_structured_request(  # noqa: SLF001
        AgentBuilderMessageRequest(
            message="LLM으로 입력을 요약하는 워크플로우를 만들어줘"
        ),
        workflow=None,
    )
    preview_graph = svc._build_preview_graph(  # noqa: SLF001
        structured,
        workflow=None,
        kb_bindings=[],
    )
    llm_node = next(
        node for node in preview_graph["nodes"] if node["type"] == "llmNode"
    )
    validation = svc.validate_preview_graph(preview_graph)
    configuration_issues = svc._node_configuration_issues(  # noqa: SLF001
        preview_graph
    )

    assert llm_node["data"]["model_id"] is None
    assert llm_node["data"]["configuration_state"] == "unresolved"
    assert validation.valid is True
    assert any(
        issue.node_id == llm_node["id"]
        and "model_id" in {parameter.key for parameter in issue.missing_parameters}
        for issue in configuration_issues
    )


def test_agent_builder_draft_uses_permission_aware_model_recommendation(monkeypatch):
    db = service_module.Session()
    svc = AgentBuilderService(
        db,
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(
        service_module.LLMService,
        "get_agent_builder_draft_model_recommendation",
        lambda *args, **kwargs: SimpleNamespace(
            model=SimpleNamespace(model_id_for_api_call="gpt-5.5")
        ),
    )

    assert svc._recommended_draft_model_id() == "gpt-5.5"  # noqa: SLF001
    db.close()


def test_agent_builder_apply_save_failure_returns_safe_failure(monkeypatch):
    class SaveFailingDb(FakeDb):
        def commit(self):
            self.commits += 1
            if self.commits == 2:
                raise RuntimeError("commit failed")

    db = SaveFailingDb()
    workflow_id = uuid.uuid4()
    graph = {"nodes": [], "edges": []}
    workflow = SimpleNamespace(
        id=workflow_id,
        graph=graph,
        updated_at=None,
        app_id=uuid.uuid4(),
        updated_by=None,
    )
    draft = _ready_modify_draft(graph, workflow_id=workflow_id)
    svc = AgentBuilderService(
        db,
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(svc, "_draft_or_404", lambda _draft_id: draft)
    monkeypatch.setattr(svc, "_workflow_in_active_org", lambda _workflow_id: workflow)
    monkeypatch.setattr(
        service_module, "has_workflow_permission", lambda *args, **kwargs: True
    )
    monkeypatch.setattr(svc, "_runtime_kb_bindings_for_apply", lambda _draft: [])

    response = svc.apply_draft(
        draft.id,
        AgentBuilderApplyRequest(
            action="apply_and_save",
            client_preview_graph_hash=calculate_graph_hash(graph),
            client_latest_graph_hash=calculate_graph_hash(graph),
        ),
    )

    assert response.outcome == "failed"
    assert response.failure_reason == "SAVE_FAILED"
    assert response.audit_recorded is True
    assert db.rollbacks == 1
    assert db.commits == 3


def test_agent_builder_apply_materializes_kb_at_apply_time(monkeypatch):
    draft = SimpleNamespace(
        preview_graph={
            "nodes": [
                {
                    "data": {
                        "knowledgeBases": [
                            {
                                "id": "safe-rec-1",
                                "reference_type": "safe_candidate_handle",
                            }
                        ]
                    }
                }
            ]
        },
        draft_metadata={
            "structured_request": service_module.AgentBuilderStructuredRequest(
                request_type="modify_workflow",
                draft_mode="modify_workflow",
                intent_summary="휴가 정책",
                knowledge_requirements=[
                    service_module.AgentBuilderKnowledgeRequirement(
                        requirement_id="kr_1",
                        query_topics=["휴가 정책"],
                        target_step_ref="step_llm",
                    )
                ],
                pending_resolution=[
                    service_module.AgentBuilderPendingResolution(
                        resolution_id="res_kb_1",
                        slot_type="knowledge_base",
                        slot_key="llm.knowledgeBases",
                        target_step_ref="step_llm",
                    )
                ],
            ).model_dump(mode="json")
        },
    )
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )

    class FakeRecommendationService:
        def __init__(self, db, *, user_id, organization_id):
            pass

        def materialize_legacy_candidate_handles_for_builder(
            self,
            request,
            candidate_handles,
        ):
            assert candidate_handles == {"safe-rec-1"}
            return [
                {
                    "safe_handle": "safe-rec-1",
                    "knowledge_base_id": str(uuid.uuid4()),
                    "name": "휴가 규정",
                }
            ]

    monkeypatch.setattr(
        service_module,
        "KnowledgeRAGRecommendationService",
        FakeRecommendationService,
    )

    bindings = svc._runtime_kb_bindings_for_apply(draft)  # noqa: SLF001

    assert isinstance(bindings, list)
    assert bindings[0]["safe_handle"] == "safe-rec-1"
    assert "knowledge_base_id" in bindings[0]


def test_agent_builder_materializes_selected_kb_without_reranking(monkeypatch):
    structured = service_module.AgentBuilderStructuredRequest(
        request_type="new_workflow",
        draft_mode="new_workflow",
        intent_summary="safe policy workflow",
        planned_steps=[
            service_module.AgentBuilderPlannedStep(
                step_id="step_llm",
                capability="knowledge_backed_llm",
                purpose="answer policy questions",
            )
        ],
        knowledge_requirements=[
            service_module.AgentBuilderKnowledgeRequirement(
                requirement_id="kr_1",
                query_topics=["policy"],
                target_step_ref="step_llm",
            )
        ],
        pending_resolution=[
            service_module.AgentBuilderPendingResolution(
                resolution_id="res_kb_1",
                slot_type="knowledge_base",
                slot_key="llm.knowledgeBases",
                target_step_ref="step_llm",
            )
        ],
    )
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    knowledge_base_id = uuid.uuid4()
    safe_handle = knowledge_base_recommendation_handle(
        svc.organization_id,
        knowledge_base_id,
    )

    class FakeRecommendationService:
        def __init__(self, db, *, user_id, organization_id):
            pass

        def recommend_for_builder(self, *_args, **_kwargs):
            raise AssertionError("selection must not rerun KB ranking")

        def materialize_candidate_handles_for_builder(
            self,
            request,
            candidate_handles,
            *,
            issued_resource_ids,
        ):
            assert request.pending_resolution_ref == "res_kb_1"
            assert candidate_handles == {safe_handle}
            assert issued_resource_ids == {safe_handle: knowledge_base_id}
            return [
                {
                    "safe_handle": safe_handle,
                    "knowledge_base_id": str(knowledge_base_id),
                    "name": "Policy knowledge",
                }
            ]

    monkeypatch.setattr(
        service_module,
        "KnowledgeRAGRecommendationService",
        FakeRecommendationService,
    )

    response = svc.materialize_knowledge_selection(
        structured,
        selected_candidate_handles={safe_handle},
        issued_handle_bindings={
            "knowledge_bases": {safe_handle: str(knowledge_base_id)},
            "collections": {},
        },
    )

    assert response["status"] == "ready"
    assert response["bindings"][0]["safe_handle"] == safe_handle


def test_agent_builder_session_restore_hides_cached_payload_when_scope_denied(
    monkeypatch,
):
    session = SimpleNamespace(
        id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        app_id=None,
        status="active",
    )
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(svc, "_session_or_404", lambda _session_id: session)
    monkeypatch.setattr(svc, "_session_scope_allowed", lambda _session: False)

    response = svc.get_session(session.id)

    assert response.messages == []
    assert response.pending_request is None
    assert "draft_preview" not in response.model_dump()
