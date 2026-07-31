import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from apps.gateway.services import agent_builder_service as service_module
from apps.gateway.services.agent_builder_service import AgentBuilderService
from apps.shared.schemas.agent_builder import (
    AgentBuilderDirectMessageResponse,
    AgentBuilderMessageResponse,
    AgentBuilderMessageRequest,
    AgentBuilderSessionCreateRequest,
    GraphMutation,
    AgentBuilderStructuredRequest,
    AgentBuilderValidationResult,
)


class _Query:
    def __init__(self, result=None, results=None):
        self.result = result
        self.results = list(results or ([] if result is None else [result]))

    def filter(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def with_for_update(self):
        return self

    def first(self):
        return self.result

    def all(self):
        return list(self.results)


class _Db:
    def __init__(self, query_result=None):
        self.query_result = query_result
        self.query_results = (
            list(query_result)
            if isinstance(query_result, list)
            else None
        )
        self.added = []

    def query(self, *_args, **_kwargs):
        if self.query_results is not None:
            return _Query(
                self.query_results[0] if self.query_results else None,
                self.query_results,
            )
        return _Query(self.query_result)

    def add(self, value):
        self.added.append(value)

    def flush(self):
        for value in self.added:
            if getattr(value, "id", None) is None:
                value.id = uuid.uuid4()

    def commit(self):
        return None

    def refresh(self, _value):
        return None


def _service(db):
    return AgentBuilderService(
        db,
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )


def test_message_request_defaults_to_configure_and_generate():
    request = AgentBuilderMessageRequest(message="workflow를 만들어줘")

    assert request.generation_mode == "configure_and_generate"


def test_agent_builder_summary_redacts_known_provider_credentials():
    secret = "github_pat_" + "a" * 40

    summary = service_module._safe_summary(f"use {secret} for the workflow")

    assert secret not in summary


def test_new_session_records_direct_edit_protocol(monkeypatch):
    db = _Db()
    service = _service(db)
    workflow_id = uuid.uuid4()
    workflow = SimpleNamespace(
        id=workflow_id,
        app_id=uuid.uuid4(),
        organization_id=service.organization_id,
    )
    monkeypatch.setattr(service, "_workflow_in_active_org", lambda _id: workflow)
    monkeypatch.setattr(
        service_module, "ensure_workflow_permission", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(service_module, "add_action_audit", lambda *_a, **_k: None)
    monkeypatch.setattr(
        service,
        "_session_response",
        lambda session: SimpleNamespace(protocol_version=session.protocol_version),
    )

    response = service.create_or_restore_session(
        AgentBuilderSessionCreateRequest(workflow_id=workflow_id)
    )

    assert response.protocol_version == "direct_edit_v1"
    assert db.added[0].protocol_version == "direct_edit_v1"


def test_direct_session_creation_requires_saved_workflow_shell():
    service = _service(_Db())

    with pytest.raises(HTTPException) as exc:
        service.create_or_restore_session(
            AgentBuilderSessionCreateRequest(app_id=uuid.uuid4())
        )

    assert exc.value.status_code == 422
    assert exc.value.detail == "workflow_context_required"


def test_null_protocol_session_recovers_as_stale_protocol(monkeypatch):
    service = _service(_Db())
    session = SimpleNamespace(
        id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        app_id=uuid.uuid4(),
        status="active",
        protocol_version=None,
    )
    monkeypatch.setattr(service, "_session_or_404", lambda _id: session)
    monkeypatch.setattr(service, "_session_scope_allowed", lambda _session: True)

    response = service.get_session(session.id)

    assert response.status == "stale_protocol"
    assert response.protocol_version is None
    assert response.active_graph_mutation is None
    assert response.parameter_group is None


def test_null_protocol_session_preserves_safe_history_without_legacy_graph_state(
    monkeypatch,
):
    request_id = uuid.uuid4()
    request_row = SimpleNamespace(
        id=request_id,
        status="draft_ready",
        message_summary="use [redacted] credential to build a workflow",
        response_payload={
            "request_id": str(request_id),
            "status": "draft_ready",
            "warnings": ["safe warning"],
            "clarification_questions": ["safe question"],
            "draft_preview": {"preview_graph": {"nodes": [{"secret": "no"}]}},
            "graph_mutation": {"operations": [{"op": "add_node"}]},
            "operation_envelopes": [{"operation_id": str(uuid.uuid4())}],
            "preview_prompt": "raw prompt",
            "apply_result": {"graph": {"nodes": []}},
        },
        created_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
    )
    service = _service(_Db(query_result=request_row))
    session = SimpleNamespace(
        id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        app_id=uuid.uuid4(),
        status="active",
        protocol_version=None,
    )
    monkeypatch.setattr(service, "_session_or_404", lambda _id: session)
    monkeypatch.setattr(service, "_session_scope_allowed", lambda _session: True)

    response = service.get_session(session.id)

    assert response.status == "stale_protocol"
    assert response.messages[0]["kind"] == "user"
    assert response.messages[0]["redacted"] is True
    assistant = response.messages[1]["response"]
    assert assistant == {
        "request_id": str(request_id),
        "status": "draft_ready",
        "warnings": ["safe warning"],
        "clarification_questions": ["safe question"],
    }


def test_recovery_returns_all_unexpired_safe_messages_in_chronological_order(
    monkeypatch,
):
    old_request_id = uuid.uuid4()
    latest_request_id = uuid.uuid4()
    expired_request_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    requests = [
        SimpleNamespace(
            id=latest_request_id,
            status="graph_mutation_ready",
            message_summary="second safe request",
            response_payload={
                "request_id": str(latest_request_id),
                "status": "graph_mutation_ready",
                "warnings": ["safe latest"],
                "draft_preview": {"preview_graph": {"nodes": [{"secret": "no"}]}},
                "operation_envelopes": [{"operation_id": str(uuid.uuid4())}],
                "apply_result": {"graph": {"nodes": []}},
            },
            created_at=now + timedelta(minutes=1),
            completed_at=now + timedelta(minutes=1),
            expires_at=now + timedelta(hours=1),
        ),
        SimpleNamespace(
            id=old_request_id,
            status="unsupported",
            message_summary="first safe request",
            response_payload={
                "request_id": str(old_request_id),
                "status": "unsupported",
                "warnings": ["safe old"],
                "draft_preview": {"preview_graph": {"nodes": [{"secret": "no"}]}},
                "operation_envelopes": [{"operation_id": str(uuid.uuid4())}],
                "apply_result": {"graph": {"nodes": []}},
            },
            created_at=now,
            completed_at=now,
            expires_at=now + timedelta(hours=1),
        ),
        SimpleNamespace(
            id=expired_request_id,
            status="failed",
            message_summary="expired request",
            response_payload={
                "request_id": str(expired_request_id),
                "status": "failed",
                "warnings": ["expired"],
            },
            created_at=now - timedelta(days=2),
            completed_at=now - timedelta(days=2),
            expires_at=now - timedelta(days=1),
        ),
    ]
    service = _service(_Db(query_result=requests))
    session = SimpleNamespace(
        id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        app_id=uuid.uuid4(),
        status="active",
        protocol_version=None,
    )
    monkeypatch.setattr(service, "_session_or_404", lambda _id: session)
    monkeypatch.setattr(service, "_session_scope_allowed", lambda _session: True)

    stale = service.get_session(session.id)

    assert [message["request_id"] for message in stale.messages] == [
        str(old_request_id),
        str(old_request_id),
        str(latest_request_id),
        str(latest_request_id),
    ]
    assert all("expired" not in str(message) for message in stale.messages)
    assert all("draft_preview" not in str(message) for message in stale.messages)
    assert all("operation_envelopes" not in str(message) for message in stale.messages)
    assert all("apply_result" not in str(message) for message in stale.messages)

    direct_service = _service(_Db(query_result=requests))
    direct_session = SimpleNamespace(
        id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        app_id=None,
        status="active",
        protocol_version="direct_edit_v1",
    )

    direct = direct_service._session_response(direct_session)  # noqa: SLF001

    assert [message["request_id"] for message in direct.messages] == [
        str(old_request_id),
        str(old_request_id),
        str(latest_request_id),
        str(latest_request_id),
    ]
    assert all("expired" not in str(message) for message in direct.messages)
    assert all("draft_preview" not in str(message) for message in direct.messages)
    assert all("operation_envelopes" not in str(message) for message in direct.messages)
    assert all("apply_result" not in str(message) for message in direct.messages)


def test_null_protocol_session_cannot_submit_direct_message(monkeypatch):
    service = _service(_Db())
    session = SimpleNamespace(
        id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        app_id=None,
        status="active",
        protocol_version=None,
    )
    monkeypatch.setattr(service, "_session_or_404", lambda _id: session)

    with pytest.raises(HTTPException) as exc:
        service.submit_message(
            session.id,
            AgentBuilderMessageRequest(message="workflow를 만들어줘"),
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == "stale_protocol"


def test_direct_session_rejects_legacy_selected_knowledge_before_request_row(
    monkeypatch,
):
    db = _Db()
    service = _service(db)
    session = SimpleNamespace(
        id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        app_id=None,
        status="active",
        protocol_version="direct_edit_v1",
    )
    monkeypatch.setattr(service, "_session_or_404", lambda _id: session)
    monkeypatch.setattr(
        service,
        "_lock_session_for_request",
        lambda _session: (_ for _ in ()).throw(AssertionError("should not lock")),
    )

    with pytest.raises(HTTPException) as exc:
        service.submit_message(
            session.id,
            AgentBuilderMessageRequest.model_validate(
                {
                    "message": "bind this knowledge base",
                    "selected_knowledge_candidate": {
                        "candidate_id": "rec-safe-1",
                        "resolution_id": "res-kb-1",
                        "requirement_id": "kr-1",
                    },
                }
            ),
        )

    assert exc.value.status_code == 422
    assert exc.value.detail == {
        "code": "invalid_request",
        "message": (
            "현재 Agent Builder에서는 대화 메시지로 Knowledge Base 선택을 "
            "제출할 수 없습니다. 표시된 Knowledge Base 선택 화면에서 선택해주세요."
        ),
    }
    assert db.added == []


def test_direct_response_persists_safe_envelope_without_typed_operations():
    service = _service(_Db())
    workflow_id = uuid.uuid4()
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
        request_id=uuid.uuid4(),
        status="graph_mutation_ready",
        graph_mutation=mutation,
    )

    payload = service._stored_response_payload(response)  # noqa: SLF001

    assert "graph_mutation" not in payload
    assert payload["generation_mode"] == "structure_only"
    assert payload["operation_envelopes"][0]["operation_id"] == str(
        mutation.operation_id
    )
    assert "operations" not in payload["operation_envelopes"][0]


def test_direct_response_persists_knowledge_resolution_for_recovery():
    service = _service(_Db())
    response = AgentBuilderMessageResponse(
        request_id=uuid.uuid4(),
        status="clarification_required",
        structured_request=AgentBuilderStructuredRequest.model_validate(
            {
                "request_type": "new_workflow",
                "draft_mode": "new_workflow",
                "intent_summary": "Knowledge workflow",
                "knowledge_requirements": [
                    {
                        "requirement_id": "kr-1",
                        "query_topics": ["policy"],
                        "target_step_ref": "step_llm",
                    }
                ],
                "pending_resolution": [
                    {
                        "resolution_id": "res-kb-1",
                        "slot_type": "knowledge_base",
                        "slot_key": "llm.knowledgeBases",
                        "target_step_ref": "step_llm",
                    }
                ],
            }
        ),
        clarification_options=[
            {
                "type": "knowledge_base",
                "candidate_id": "rec-safe-1",
                "resolution_id": "res-kb-1",
                "requirement_id": "kr-1",
            },
            {"type": "target_node", "node_id": "node-1"},
        ],
    )

    payload = service._stored_response_payload(  # noqa: SLF001
        response,
        direct_edit=True,
    )

    assert payload["knowledge_resolution"]["resolution_id"] == "res-kb-1"
    assert payload["knowledge_resolution"]["candidates"][0]["candidate_id"] == (
        "rec-safe-1"
    )
    assert payload["clarification_options"] == [
        {"type": "target_node", "node_id": "node-1"}
    ]
    assert isinstance(payload["knowledge_resolution"], dict)
    json.dumps(payload)


def test_direct_session_recovery_preserves_canonical_knowledge_candidates():
    service = _service(_Db())
    request_id = uuid.uuid4()
    response = AgentBuilderMessageResponse(
        request_id=request_id,
        status="clarification_required",
        structured_request=AgentBuilderStructuredRequest.model_validate(
            {
                "request_type": "new_workflow",
                "draft_mode": "new_workflow",
                "intent_summary": "Knowledge workflow",
                "knowledge_requirements": [
                    {
                        "requirement_id": "kr-1",
                        "query_topics": ["policy"],
                        "target_step_ref": "step_llm",
                    }
                ],
                "pending_resolution": [
                    {
                        "resolution_id": "res-kb-1",
                        "slot_type": "knowledge_base",
                        "slot_key": "llm.knowledgeBases",
                        "target_step_ref": "step_llm",
                    }
                ],
            }
        ),
        clarification_options=[
            {
                "type": "knowledge_base",
                "candidate_id": "rec-safe-1",
                "resolution_id": "res-kb-1",
                "requirement_id": "kr-1",
            }
        ],
    )
    issued_kb_id = uuid.uuid4()
    response._issued_knowledge_handle_bindings = {
        "knowledge_bases": {"rec-safe-1": str(issued_kb_id)},
        "collections": {},
    }
    payload = service._stored_response_payload(  # noqa: SLF001
        response,
        direct_edit=True,
    )
    assert payload["_issued_knowledge_handle_bindings"] == {
        "resolution_id": "res-kb-1",
        "knowledge_bases": {"rec-safe-1": str(issued_kb_id)},
        "collections": {},
    }
    request_row = SimpleNamespace(
        id=request_id,
        status="clarification_required",
        message_summary="Add safe Knowledge",
        response_payload=payload,
        created_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
    )
    service = _service(_Db(query_result=request_row))
    session = SimpleNamespace(
        id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        app_id=None,
        protocol_version="direct_edit_v1",
        status="active",
    )

    recovered = service._session_response(session)  # noqa: SLF001

    assert "_issued_knowledge_handle_bindings" not in str(
        recovered.model_dump(mode="json")
    )

    assistant = recovered.messages[-1]["response"]
    assert assistant["knowledge_resolution"]["resolution_id"] == "res-kb-1"
    assert assistant["knowledge_resolution"]["candidates"][0]["candidate_id"] == (
        "rec-safe-1"
    )


def test_direct_session_recovery_restores_completed_knowledge_selection():
    service = _service(_Db())
    request_id = uuid.uuid4()
    response = AgentBuilderMessageResponse(
        request_id=request_id,
        status="clarification_required",
        structured_request=AgentBuilderStructuredRequest.model_validate(
            {
                "request_type": "new_workflow",
                "draft_mode": "new_workflow",
                "intent_summary": "Knowledge workflow",
                "knowledge_requirements": [
                    {
                        "requirement_id": "kr-1",
                        "query_topics": ["policy"],
                        "target_step_ref": "step_llm",
                    }
                ],
                "pending_resolution": [
                    {
                        "resolution_id": "res-kb-1",
                        "slot_type": "knowledge_base",
                        "slot_key": "llm.knowledgeBases",
                        "target_step_ref": "step_llm",
                    }
                ],
            }
        ),
        clarification_options=[
            {
                "type": "knowledge_base",
                "candidate_id": "rec-safe-1",
                "safe_label": "휴가 정책",
                "resolution_id": "res-kb-1",
                "requirement_id": "kr-1",
            },
            {
                "type": "knowledge_base",
                "candidate_id": "rec-safe-2",
                "safe_label": "복지 정책",
                "resolution_id": "res-kb-1",
                "requirement_id": "kr-1",
            },
        ],
    )
    payload = service._stored_response_payload(  # noqa: SLF001
        response,
        direct_edit=True,
    )
    payload["status"] = "completed"
    payload["knowledge_resolutions"] = [
        {
            "resolution_id": "res-kb-1",
            "operation_id": str(uuid.uuid4()),
            "timing": "after_graph",
            "selected_candidate_ids": ["rec-safe-2"],
            "status": "completed",
        }
    ]
    request_row = SimpleNamespace(
        id=request_id,
        status="completed",
        message_summary="Add safe Knowledge",
        response_payload=payload,
        created_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
    )
    service = _service(_Db(query_result=request_row))
    session = SimpleNamespace(
        id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        app_id=None,
        protocol_version="direct_edit_v1",
        status="active",
    )

    recovered = service._session_response(session)  # noqa: SLF001

    assistant = recovered.messages[-1]["response"]
    assert assistant["knowledge_resolution"]["selection_status"] == "completed"
    assert assistant["knowledge_resolution"]["selected"] == [
        {
            "type": "knowledge_base",
            "candidate_id": "rec-safe-2",
            "safe_label": "복지 정책",
            "resolution_id": "res-kb-1",
            "requirement_id": "kr-1",
        }
    ]

    payload["knowledge_resolutions"][0]["status"] = "unapplied"
    recovered_retry = service._session_response(session)  # noqa: SLF001

    retry_assistant = recovered_retry.messages[-1]["response"]
    assert retry_assistant["knowledge_resolution"]["selection_status"] == "unapplied"
    assert retry_assistant["knowledge_resolution"]["selected"] == assistant[
        "knowledge_resolution"
    ]["selected"]


def test_direct_session_recovery_restores_pending_ack_knowledge_selection():
    service = _service(_Db())
    request_id = uuid.uuid4()
    response = AgentBuilderMessageResponse(
        request_id=request_id,
        status="clarification_required",
        structured_request=AgentBuilderStructuredRequest.model_validate(
            {
                "request_type": "new_workflow",
                "draft_mode": "new_workflow",
                "intent_summary": "Knowledge workflow",
                "knowledge_requirements": [
                    {
                        "requirement_id": "kr-1",
                        "query_topics": ["policy"],
                        "target_step_ref": "step_llm",
                    }
                ],
                "pending_resolution": [
                    {
                        "resolution_id": "res-kb-1",
                        "slot_type": "knowledge_base",
                        "slot_key": "llm.knowledgeBases",
                        "target_step_ref": "step_llm",
                    }
                ],
            }
        ),
        clarification_options=[
            {
                "type": "knowledge_base",
                "candidate_id": "rec-safe-1",
                "safe_label": "Policy A",
                "resolution_id": "res-kb-1",
                "requirement_id": "kr-1",
            },
            {
                "type": "knowledge_base",
                "candidate_id": "rec-safe-2",
                "safe_label": "Policy B",
                "resolution_id": "res-kb-1",
                "requirement_id": "kr-1",
            },
        ],
    )
    payload = service._stored_response_payload(  # noqa: SLF001
        response,
        direct_edit=True,
    )
    payload["knowledge_resolutions"] = [
        {
            "resolution_id": "res-kb-1",
            "operation_id": str(uuid.uuid4()),
            "timing": "after_graph",
            "selected_candidate_ids": ["rec-safe-2"],
            "status": "pending_ack",
        }
    ]
    request_row = SimpleNamespace(
        id=request_id,
        status="graph_mutation_ready",
        message_summary="Add safe Knowledge",
        response_payload=payload,
        created_at=datetime.now(timezone.utc),
        completed_at=None,
    )
    service = _service(_Db(query_result=request_row))
    session = SimpleNamespace(
        id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        app_id=None,
        protocol_version="direct_edit_v1",
        status="graph_mutation_ready",
    )

    recovered = service._session_response(session)  # noqa: SLF001

    assistant = recovered.messages[-1]["response"]
    assert assistant["knowledge_resolution"]["selection_status"] == "pending_ack"
    assert assistant["knowledge_resolution"]["selected"] == [
        {
            "type": "knowledge_base",
            "candidate_id": "rec-safe-2",
            "safe_label": "Policy B",
            "resolution_id": "res-kb-1",
            "requirement_id": "kr-1",
        }
    ]


def test_direct_session_message_returns_graph_mutation_without_preview(monkeypatch):
    db = _Db()
    service = _service(db)
    workflow = SimpleNamespace(
        id=uuid.uuid4(),
        app_id=None,
        graph={"nodes": [], "edges": [], "viewport": {"x": 0, "y": 0, "zoom": 1}},
        updated_at=datetime.now(timezone.utc),
    )
    session = SimpleNamespace(
        id=uuid.uuid4(),
        workflow_id=workflow.id,
        app_id=None,
        status="active",
        protocol_version="direct_edit_v1",
        updated_at=datetime.now(timezone.utc),
    )
    structured = AgentBuilderStructuredRequest.model_validate(
        {
            "request_type": "new_workflow",
            "draft_mode": "new_workflow",
            "intent_summary": "입력과 응답 생성",
            "planned_steps": [
                {
                    "step_id": "step_input",
                    "capability": "start_input",
                    "purpose": "입력",
                },
                {
                    "step_id": "step_answer",
                    "capability": "answer",
                    "purpose": "응답",
                    "depends_on": ["step_input"],
                },
            ],
            "required_capabilities": ["start_input", "answer"],
        }
    )
    candidate = {
        "nodes": [
            {
                "id": "start",
                "type": "startNode",
                "position": {"x": 0, "y": 0},
                "data": {"title": "입력"},
            },
            {
                "id": "answer",
                "type": "answerNode",
                "position": {"x": 320, "y": 0},
                "data": {
                    "title": "응답",
                    "outputs": [{"variable": "answer", "value_selector": ["start", "question"]}],
                },
            },
        ],
        "edges": [{"id": "e1", "source": "start", "target": "answer"}],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }
    monkeypatch.setattr(service, "_session_or_404", lambda _id: session)
    monkeypatch.setattr(service, "_lock_session_for_request", lambda value: value)
    monkeypatch.setattr(service, "_reject_if_pending", lambda _value: None)
    monkeypatch.setattr(service, "_workflow_in_active_org", lambda _id: workflow)
    monkeypatch.setattr(service_module, "ensure_workflow_permission", lambda *_a, **_k: None)
    monkeypatch.setattr(service_module, "add_action_audit", lambda *_a, **_k: None)
    monkeypatch.setattr(service, "_selected_knowledge_candidate_context", lambda *_a, **_k: None)
    monkeypatch.setattr(service, "_structure_request", lambda *_a, **_k: structured)
    monkeypatch.setattr(service, "_selected_edge_id_for_structured_request", lambda *_a, **_k: None)
    monkeypatch.setattr(service, "_resolve_edit_target", lambda *_a, **_k: {"status": "resolved"})
    monkeypatch.setattr(
        service,
        "_validate_structured_request",
        lambda *_a, **_k: AgentBuilderValidationResult(valid=True),
    )
    monkeypatch.setattr(
        service,
        "_resolve_knowledge_requirements",
        lambda *_a, **_k: {"status": "ready", "bindings": [], "warnings": []},
    )
    monkeypatch.setattr(service, "_structured_request_warnings", lambda *_a: [])
    monkeypatch.setattr(service, "_draft_safety_notices", lambda *_a: [])
    monkeypatch.setattr(service, "_build_preview_graph", lambda *_a, **_k: candidate)
    monkeypatch.setattr(
        service,
        "validate_preview_graph",
        lambda *_a, **_k: AgentBuilderValidationResult(valid=True),
    )

    response = service.submit_message(
        session.id,
        AgentBuilderMessageRequest(
            message="입력과 응답 노드를 만들어줘",
            workflow_id=workflow.id,
            generation_mode="structure_only",
        ),
    )

    assert response.status == "graph_mutation_ready"
    assert response.graph_mutation is not None
    assert response.parameter_group is None
    assert response.draft_preview is None
    assert not any(value.__class__.__name__ == "AgentBuilderDraft" for value in db.added)


def test_new_workflow_request_on_nonempty_editor_issues_replacement(monkeypatch):
    db = _Db()
    service = _service(db)
    workflow = SimpleNamespace(
        id=uuid.uuid4(),
        app_id=None,
        graph={
            "nodes": [
                {
                    "id": "existing",
                    "type": "startNode",
                    "position": {"x": 0, "y": 0},
                    "data": {"title": "Existing"},
                }
            ],
            "edges": [],
            "viewport": {"x": 0, "y": 0, "zoom": 1},
        },
        updated_at=datetime.now(timezone.utc),
    )
    session = SimpleNamespace(
        id=uuid.uuid4(),
        workflow_id=workflow.id,
        app_id=None,
        status="active",
        protocol_version="direct_edit_v1",
        updated_at=datetime.now(timezone.utc),
    )
    structured = AgentBuilderStructuredRequest.model_validate(
        {
            "request_type": "new_workflow",
            "draft_mode": "new_workflow",
            "intent_summary": "웹훅 사내 문서 챗봇 생성",
            "planned_steps": [
                {
                    "step_id": "step_input",
                    "capability": "webhook_trigger",
                    "purpose": "요청 수신",
                },
                {
                    "step_id": "step_llm",
                    "capability": "knowledge_backed_llm",
                    "purpose": "사내 문서 답변",
                    "depends_on": ["step_input"],
                },
                {
                    "step_id": "step_answer",
                    "capability": "answer",
                    "purpose": "응답",
                    "depends_on": ["step_llm"],
                },
            ],
            "required_capabilities": [
                "webhook_trigger",
                "llm",
                "knowledge_base",
                "answer",
            ],
        }
    )
    monkeypatch.setattr(service, "_session_or_404", lambda _id: session)
    monkeypatch.setattr(service, "_lock_session_for_request", lambda value: value)
    monkeypatch.setattr(service, "_reject_if_pending", lambda _value: None)
    monkeypatch.setattr(service, "_workflow_in_active_org", lambda _id: workflow)
    monkeypatch.setattr(service_module, "ensure_workflow_permission", lambda *_a, **_k: None)
    monkeypatch.setattr(service_module, "add_action_audit", lambda *_a, **_k: None)
    monkeypatch.setattr(
        service, "_selected_knowledge_candidate_context", lambda *_a, **_k: None
    )
    monkeypatch.setattr(service, "_structure_request", lambda *_a, **_k: structured)
    monkeypatch.setattr(
        service, "_selected_edge_id_for_structured_request", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        service, "_resolve_edit_target", lambda *_a, **_k: {"status": "resolved"}
    )
    monkeypatch.setattr(
        service,
        "_validate_structured_request",
        lambda *_a, **_k: AgentBuilderValidationResult(valid=True),
    )
    monkeypatch.setattr(
        service,
        "_resolve_knowledge_requirements",
        lambda *_a, **_k: {"status": "ready", "bindings": [], "warnings": []},
    )
    monkeypatch.setattr(service, "_structured_request_warnings", lambda *_a: [])
    monkeypatch.setattr(service, "_draft_safety_notices", lambda *_a: [])
    candidate = {
        "nodes": [
            {
                "id": "webhook",
                "type": "webhookTrigger",
                "position": {"x": 0, "y": 0},
                "data": {"title": "웹훅"},
            },
            {
                "id": "llm",
                "type": "llmNode",
                "position": {"x": 320, "y": 0},
                "data": {"title": "사내 문서 답변", "knowledgeBases": []},
            },
            {
                "id": "answer",
                "type": "answerNode",
                "position": {"x": 640, "y": 0},
                "data": {"title": "응답", "outputs": []},
            },
        ],
        "edges": [
            {"id": "e1", "source": "webhook", "target": "llm"},
            {"id": "e2", "source": "llm", "target": "answer"},
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }
    monkeypatch.setattr(service, "_build_preview_graph", lambda *_a, **_k: candidate)
    monkeypatch.setattr(
        service,
        "validate_preview_graph",
        lambda *_a, **_k: AgentBuilderValidationResult(valid=True),
    )

    response = service.submit_message(
        session.id,
        AgentBuilderMessageRequest(
            message="웹훅으로 받는 사내 문서 챗봇 워크플로우를 만들어줘",
            workflow_id=workflow.id,
            generation_mode="structure_only",
        ),
    )

    assert response.status == "graph_mutation_ready"
    assert response.graph_mutation is not None
    assert response.graph_mutation.kind == "replace_workflow"
    assert any(
        operation.op == "remove_node" and operation.node_id == "existing"
        for operation in response.graph_mutation.operations
    )


def test_direct_session_recovery_blocks_operations_lost_before_persistence():
    operation_id = uuid.uuid4()
    request_row = SimpleNamespace(
        id=uuid.uuid4(),
        status="graph_mutation_ready",
        message_summary="입력과 응답 생성",
        structured_request={},
        response_payload={
            "operation_envelopes": [
                {
                    "operation_id": str(operation_id),
                    "kind": "initial_graph",
                    "status": "pending_apply",
                    "generation_mode": "structure_only",
                    "workflow_id": str(uuid.uuid4()),
                    "base_graph_hash": "a" * 64,
                    "expected_result_graph_hash": "b" * 64,
                    "catalog_version": 3,
                    "affected_node_ids": ["start"],
                    "completion_context": None,
                }
            ],
            "parameter_groups": [],
        },
        created_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
    )
    db = _Db(query_result=request_row)
    service = _service(db)
    session = SimpleNamespace(
        id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        app_id=uuid.uuid4(),
        protocol_version="direct_edit_v1",
        status="active",
    )

    response = service._session_response(session)  # noqa: SLF001

    assert response.status == "operation_payload_unavailable"
    assert response.active_graph_mutation["operation_id"] == str(operation_id)
    assert response.active_graph_mutation["status"] == "blocked"
    assert response.active_graph_mutation["blocked_reason"] == "operation_payload_unavailable"
    assert "operations" not in response.active_graph_mutation
    assert response.parameter_group is None
    assert "draft_preview" not in response.model_dump()


def test_direct_session_recovery_uses_public_message_contract():
    request_id = uuid.uuid4()
    request_row = SimpleNamespace(
        id=request_id,
        status="unsupported",
        message_summary="기존 workflow 전체를 교체",
        structured_request={
            "request_type": "unsupported",
            "draft_mode": "modify_workflow",
            "intent_summary": "기존 workflow 전체 교체",
            "unsupported_requests": ["replace_workflow"],
        },
        response_payload={
            "request_id": str(request_id),
            "status": "unsupported",
            "structured_request": {
                "request_type": "unsupported",
                "draft_mode": "modify_workflow",
                "intent_summary": "기존 workflow 전체 교체",
                "unsupported_requests": ["replace_workflow"],
            },
            "clarification_questions": [],
            "clarification_options": [],
            "warnings": ["전체 교체는 지원하지 않습니다."],
        },
        created_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
    )
    service = _service(_Db(query_result=request_row))
    session = SimpleNamespace(
        id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        app_id=None,
        protocol_version="direct_edit_v1",
        status="active",
    )

    response = service._session_response(session)  # noqa: SLF001

    assistant = response.messages[-1]["response"]
    AgentBuilderDirectMessageResponse.model_validate(assistant)
    assert assistant["structured_plan"]["request_type"] == "unsupported"
    assert assistant["structured_plan"]["steps"] == []
    assert "planned_steps" not in assistant["structured_plan"]
    assert "draft_mode" not in assistant["structured_plan"]
    assert "structured_request" not in assistant
    assert "draft_preview" not in assistant
