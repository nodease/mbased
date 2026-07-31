from copy import deepcopy
from types import SimpleNamespace
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException

from apps.gateway.adapters.db.agent_builder_repository import (
    AgentBuilderRepository,
    AgentBuilderRepositoryError,
)
from apps.gateway.services.agent_builder.knowledge_selection_service import (
    KnowledgeSelectionService,
)
from apps.gateway.services.agent_builder import (
    knowledge_selection_service as selection_module,
)
from apps.gateway.services.agent_builder.parameter_candidates import (
    ParameterCandidateProvider,
)
from apps.gateway.services.agent_builder_service import AgentBuilderService
from apps.shared.schemas.agent_builder import (
    AgentBuilderKnowledgePlacement,
    AgentBuilderKnowledgeSelectionRequest,
    AgentBuilderParameterGroup,
    AgentBuilderParameterTask,
    AgentBuilderStructuredRequest,
    GraphMutation,
    GraphMutationSafeEnvelope,
)


def test_knowledge_target_uses_safe_step_node_mapping_without_graph_snapshot_state():
    workflow = SimpleNamespace(
        graph={
            "nodes": [
                {
                    "id": "llm-1",
                    "type": "llmNode",
                    "position": {"x": 0, "y": 0},
                    "data": {},
                }
            ],
            "edges": [],
        }
    )

    target = KnowledgeSelectionService._target_node_id(
        payload={"safe_step_node_ids": {"step_llm": "llm-1"}},
        request_row=SimpleNamespace(),
        workflow=workflow,
        target_step_id="step_llm",
    )

    assert target == "llm-1"


def test_knowledge_resolution_persists_only_safe_ids_and_acknowledges():
    repository = AgentBuilderRepository()
    request_row = SimpleNamespace(
        response_payload={
            "status": "clarification_required",
            "clarification_questions": ["Select a Knowledge Base."],
            "clarification_options": [
                {
                    "type": "knowledge_base",
                    "candidate_id": "rec-safe-1",
                    "resolution_id": "res-kb-1",
                    "requirement_id": "kr-1",
                }
            ],
        }
    )
    operation_id = uuid4()
    group_id = uuid4()
    task = AgentBuilderParameterTask(
        task_id=uuid4(),
        group_id=group_id,
        step_id="step_llm",
        node_id="llm-1",
        node_type="llmNode",
        parameter_key="knowledgeBases",
        label="Knowledge Base",
        input_type="knowledge_multi_select",
        required=False,
        status="completed",
        task_version=2,
        stable_order=0,
        resolution_source="user_request",
        reason="Knowledge Base binding is optional.",
        input_guidance="Select zero or more Knowledge Bases.",
    )
    repository.store_parameter_group(
        request_row,
        AgentBuilderParameterGroup(
            group_id=group_id,
            status="completed",
            tasks=[task],
        ),
    )

    stored = repository.store_knowledge_resolution(
        request_row,
        resolution_id="res-kb-1",
        operation_id=operation_id,
        timing="after_graph",
        selected_candidate_ids=["rec-safe-1", "rec-safe-1"],
        selected_collection_handles=["col-safe-1", "col-safe-1"],
        selected_kb_handles=["rec-safe-1", "rec-safe-1"],
    )
    acknowledged = repository.acknowledge_knowledge_resolution(
        request_row,
        resolution_id="res-kb-1",
        operation_id=operation_id,
    )

    assert stored["selected_candidate_ids"] == ["rec-safe-1"]
    assert stored["selected_collection_handles"] == ["col-safe-1"]
    assert stored["selected_kb_handles"] == ["rec-safe-1"]
    assert acknowledged["status"] == "completed"
    assert request_row.response_payload["clarification_options"] == []
    assert request_row.response_payload["clarification_questions"] == []
    assert "operations" not in request_row.response_payload
    assert "knowledge_base_id" not in str(request_row.response_payload)

    before_revert_attempt = request_row.response_payload
    with pytest.raises(
        AgentBuilderRepositoryError,
        match="operation is not history boundary",
    ):
        repository.revert_completion_state(
            request_row,
            {
                "kind": "knowledge_binding",
                "affected_node_ids": ["llm-1"],
                "completion_context": {"knowledge_resolution_id": "res-kb-1"},
            },
        )
    assert request_row.response_payload == before_revert_attempt


def test_knowledge_resolution_treats_reordered_handles_as_the_same_selection():
    repository = AgentBuilderRepository()
    request_row = SimpleNamespace(response_payload={})
    operation_id = uuid4()

    stored = repository.store_knowledge_resolution(
        request_row,
        resolution_id="res-kb-set",
        operation_id=operation_id,
        timing="after_graph",
        selected_candidate_ids=["rec-b", "col-b", "rec-a", "col-a"],
        selected_collection_handles=["col-b", "col-a", "col-b"],
        selected_kb_handles=["rec-b", "rec-a", "rec-b"],
    )
    retried = repository.store_knowledge_resolution(
        request_row,
        resolution_id="res-kb-set",
        operation_id=operation_id,
        timing="after_graph",
        selected_candidate_ids=["col-a", "rec-a", "col-b", "rec-b"],
        selected_collection_handles=["col-a", "col-b"],
        selected_kb_handles=["rec-a", "rec-b"],
    )

    assert stored == retried
    assert stored["selected_candidate_ids"] == ["col-a", "col-b", "rec-a", "rec-b"]
    assert stored["selected_collection_handles"] == ["col-a", "col-b"]
    assert stored["selected_kb_handles"] == ["rec-a", "rec-b"]


def test_persisted_hierarchical_selection_restores_only_visible_safe_labels():
    hydrated = AgentBuilderService._hydrate_persisted_knowledge_selection(
        {
            "knowledge_resolution": {
                "resolution_id": "res-hydrate",
                "timing": "after_graph",
                "required": True,
                "candidates": [],
                "collections": [
                    {
                        "collection_handle": "collection-visible",
                        "safe_label": "사내 문서",
                        "children": [
                            {
                                "kb_handle": "kb-visible",
                                "selection_key": "kb-visible",
                                "safe_label": "인사 KB",
                            }
                        ],
                    }
                ],
                "ungrouped_kbs": [],
                "selected": [],
            },
            "knowledge_resolutions": [
                {
                    "resolution_id": "res-hydrate",
                    "operation_id": str(uuid4()),
                    "timing": "after_graph",
                    "selected_candidate_ids": [
                        "collection-visible",
                        "kb-visible",
                        "kb-hidden",
                    ],
                    "selected_collection_handles": ["collection-visible"],
                    "selected_kb_handles": ["kb-visible", "kb-hidden"],
                    "status": "completed",
                }
            ],
        }
    )

    assert hydrated is not None
    assert hydrated["selected_collection_handles"] == ["collection-visible"]
    assert hydrated["selected_kb_handles"] == ["kb-visible", "kb-hidden"]
    assert hydrated["selected"] == [
        {
            "selection_type": "collection",
            "collection_handle": "collection-visible",
            "safe_label": "사내 문서",
        },
        {
            "selection_type": "knowledge_base",
            "kb_handle": "kb-visible",
            "candidate_id": "kb-visible",
            "safe_label": "인사 KB",
        },
    ]


def test_knowledge_resolution_cannot_issue_a_second_operation_once_submitted():
    repository = AgentBuilderRepository()
    request_row = SimpleNamespace(response_payload={})
    first_operation_id = uuid4()

    first = repository.store_knowledge_resolution(
        request_row,
        resolution_id="res-kb-1",
        operation_id=first_operation_id,
        timing="after_graph",
        selected_candidate_ids=["rec-safe-1"],
    )

    with pytest.raises(
        AgentBuilderRepositoryError,
        match="already submitted",
    ):
        repository.store_knowledge_resolution(
            request_row,
            resolution_id="res-kb-1",
            operation_id=uuid4(),
            timing="after_graph",
            selected_candidate_ids=["rec-safe-1"],
        )

    assert request_row.response_payload["knowledge_resolutions"] == [first]


def test_unapplied_knowledge_resolution_reuses_card_with_a_new_operation():
    repository = AgentBuilderRepository()
    first_operation_id = uuid4()
    second_operation_id = uuid4()
    request_row = SimpleNamespace(
        response_payload={
            "knowledge_resolutions": [
                {
                    "resolution_id": "res-kb-retry",
                    "operation_id": str(first_operation_id),
                    "timing": "after_graph",
                    "selected_candidate_ids": ["rec-safe-1"],
                    "status": "pending_ack",
                }
            ]
        }
    )
    blocked_envelope = {
        "operation_id": str(first_operation_id),
        "kind": "knowledge_binding",
        "completion_context": {
            "knowledge_resolution_id": "res-kb-retry",
        },
    }

    repository.recover_blocked_completion_state(request_row, blocked_envelope)
    recovered = repository.find_knowledge_resolution(
        request_row, "res-kb-retry"
    )
    assert recovered["status"] == "unapplied"

    retried = repository.store_knowledge_resolution(
        request_row,
        resolution_id="res-kb-retry",
        operation_id=second_operation_id,
        timing="after_graph",
        selected_candidate_ids=["rec-safe-1"],
    )

    assert retried["operation_id"] == str(second_operation_id)
    assert retried["status"] == "pending_ack"
    assert len(request_row.response_payload["knowledge_resolutions"]) == 1


def test_before_graph_payload_loss_unapplies_resolution_and_allows_new_operation():
    repository = AgentBuilderRepository()
    workflow_id = uuid4()
    first_operation_id = uuid4()
    second_operation_id = uuid4()
    now = datetime.now(timezone.utc)
    request_row = SimpleNamespace(response_payload={})
    first_envelope = GraphMutationSafeEnvelope.model_validate(
        {
            "operation_id": first_operation_id,
            "kind": "initial_graph",
            "status": "pending_apply",
            "generation_mode": "configure_and_generate",
            "workflow_id": workflow_id,
            "base_graph_hash": "a" * 64,
            "expected_workflow_updated_at": now,
            "expected_result_graph_hash": "b" * 64,
            "catalog_version": 3,
            "affected_node_ids": ["llm-1"],
            "completion_context": {"knowledge_resolution_id": "res-before"},
        }
    )
    second_envelope = GraphMutationSafeEnvelope.model_validate(
        {
            "operation_id": second_operation_id,
            "kind": "initial_graph",
            "status": "pending_apply",
            "generation_mode": "configure_and_generate",
            "workflow_id": workflow_id,
            "base_graph_hash": "a" * 64,
            "expected_workflow_updated_at": now,
            "expected_result_graph_hash": "c" * 64,
            "catalog_version": 3,
            "affected_node_ids": ["llm-1"],
            "completion_context": {"knowledge_resolution_id": "res-before"},
        }
    )
    repository.store_envelope(request_row, first_envelope)
    repository.store_knowledge_resolution(
        request_row,
        resolution_id="res-before",
        operation_id=first_operation_id,
        timing="before_graph",
        selected_candidate_ids=[],
    )

    blocked = repository.mark_envelope_blocked(
        request_row,
        first_operation_id,
        reason="operation_payload_unavailable",
    )
    repository.recover_blocked_completion_state(request_row, blocked)
    recovered = repository.find_knowledge_resolution(request_row, "res-before")

    assert recovered["status"] == "unapplied"

    repository.store_envelope(request_row, second_envelope)
    retried = repository.store_knowledge_resolution(
        request_row,
        resolution_id="res-before",
        operation_id=second_operation_id,
        timing="before_graph",
        selected_candidate_ids=[],
    )

    assert retried["operation_id"] == str(second_operation_id)
    assert retried["status"] == "pending_ack"
    boundary = repository.load_history_boundary(request_row)
    assert boundary["operation_id"] == str(second_operation_id)
    assert str(first_operation_id) not in boundary["member_operation_ids"]


@pytest.mark.parametrize("terminal_status", ["pending_ack", "completed"])
def test_terminal_knowledge_selection_rejects_stale_handles_without_refresh(
    monkeypatch,
    terminal_status,
):
    user_id = uuid4()
    organization_id = uuid4()
    workflow_id = uuid4()
    session = SimpleNamespace(
        id=uuid4(),
        user_id=user_id,
        organization_id=organization_id,
        workflow_id=workflow_id,
        protocol_version="direct_edit_v1",
    )
    workflow = SimpleNamespace(
        id=workflow_id,
        organization_id=organization_id,
        graph={
            "nodes": [
                {
                    "id": "llm-1",
                    "type": "llmNode",
                    "position": {"x": 0, "y": 0},
                    "data": {},
                }
            ],
            "edges": [],
        },
        updated_at=datetime.now(timezone.utc),
    )
    structured = AgentBuilderStructuredRequest.model_validate(
        {
            "request_type": "new_workflow",
            "draft_mode": "new_workflow",
            "intent_summary": "safe summary",
            "planned_steps": [
                {
                    "step_id": "step_llm",
                    "capability": "llm",
                    "purpose": "safe purpose",
                }
            ],
            "knowledge_placements": [
                {
                    "requirement_id": "kr-1",
                    "timing": "after_graph",
                    "effect_kind": "binding_only",
                    "target_step_id": "step_llm",
                }
            ],
        }
    )
    request_row = SimpleNamespace(
        structured_request=structured.model_dump(mode="json"),
        response_payload={
                "safe_step_node_ids": {"step_llm": "llm-1"},
                "knowledge_resolution": {
                    "resolution_id": "res-kb-1",
                    "timing": "after_graph",
                    "required": True,
                    "candidates": [
                        {
                            "type": "knowledge_base",
                            "candidate_id": "rec-safe-1",
                            "resolution_id": "res-kb-1",
                            "requirement_id": "kr-1",
                        }
                    ],
                    "selected": [],
                },
                "clarification_options": [
                    {
                        "type": "knowledge_base",
                    "candidate_id": "rec-safe-1",
                    "resolution_id": "res-kb-1",
                    "requirement_id": "kr-1",
                }
            ],
            "operation_envelopes": [
                {
                    "kind": "initial_graph",
                    "status": "acknowledged",
                }
            ],
            "knowledge_resolutions": [
                {
                    "resolution_id": "res-kb-1",
                    "operation_id": str(uuid4()),
                    "timing": "after_graph",
                    "selected_candidate_ids": ["rec-safe-1"],
                    "selected_collection_handles": [],
                    "selected_kb_handles": ["rec-safe-1"],
                    "status": terminal_status,
                }
            ],
        },
    )

    class _Query:
        def __init__(self, *, first=None, rows=None):
            self._first = first
            self._rows = list(rows or [])

        def filter(self, *_args):
            return self

        def order_by(self, *_args):
            return self

        def with_for_update(self):
            return self

        def first(self):
            return self._first

        def all(self):
            return list(self._rows)

    class _Db:
        def __init__(self):
            self.commits = 0

        def query(self, model):
            if model is selection_module.AgentBuilderSession:
                return _Query(first=session)
            if model is selection_module.Workflow:
                return _Query(first=workflow)
            if model is selection_module.AgentBuilderRequest:
                return _Query(rows=[request_row])
            raise AssertionError(f"unexpected model: {model}")

        def commit(self):
            self.commits += 1

    resolver_calls = []
    refresh_calls = []
    monkeypatch.setattr(
        selection_module,
        "has_workflow_permission",
        lambda *_args, **_kwargs: True,
    )
    db = _Db()
    service = KnowledgeSelectionService(
        db,
        user_id=user_id,
        organization_id=organization_id,
        binding_materializer=lambda *_args, **_kwargs: resolver_calls.append(
            (_args, _kwargs)
        ),
        knowledge_selection_refresher=lambda structured_request: (
            refresh_calls.append(structured_request)
            or {
                "knowledge_selection": {
                    "collections": [],
                    "ungrouped_kbs": [],
                }
            }
        ),
        no_knowledge_candidate_id="no-kb",
    )
    selection = AgentBuilderKnowledgeSelectionRequest.model_validate(
        {
            "resolution_id": "res-kb-1",
            "selected_kb_handles": ["rec-stale"],
        }
    )
    original_payload = deepcopy(request_row.response_payload)

    with pytest.raises(HTTPException) as exc:
        service.select(session.id, selection)

    assert exc.value.status_code == 409
    assert exc.value.detail == "knowledge_resolution_already_submitted"
    assert resolver_calls == []
    assert refresh_calls == []
    assert db.commits == 0
    assert request_row.response_payload == original_payload


def test_direct_selection_rejects_legacy_only_knowledge_candidate(
    monkeypatch,
):
    user_id = uuid4()
    organization_id = uuid4()
    workflow_id = uuid4()
    session = SimpleNamespace(
        id=uuid4(),
        user_id=user_id,
        organization_id=organization_id,
        workflow_id=workflow_id,
        protocol_version="direct_edit_v1",
    )
    workflow = SimpleNamespace(
        id=workflow_id,
        organization_id=organization_id,
        graph={"nodes": [], "edges": []},
        updated_at=datetime.now(timezone.utc),
    )
    structured = AgentBuilderStructuredRequest.model_validate(
        {
            "request_type": "new_workflow",
            "draft_mode": "new_workflow",
            "intent_summary": "safe summary",
            "planned_steps": [
                {
                    "step_id": "step_llm",
                    "capability": "llm",
                    "purpose": "safe purpose",
                }
            ],
            "knowledge_placements": [
                {
                    "requirement_id": "kr-1",
                    "timing": "after_graph",
                    "effect_kind": "binding_only",
                    "target_step_id": "step_llm",
                }
            ],
        }
    )
    request_row = SimpleNamespace(
        structured_request=structured.model_dump(mode="json"),
        response_payload={
            "safe_step_node_ids": {"step_llm": "llm-1"},
            "clarification_options": [
                {
                    "type": "knowledge_base",
                    "candidate_id": "legacy-rec-1",
                    "resolution_id": "res-kb-legacy",
                    "requirement_id": "kr-1",
                }
            ],
            "operation_envelopes": [
                {
                    "kind": "initial_graph",
                    "status": "acknowledged",
                }
            ],
        },
    )

    class _Query:
        def __init__(self, *, first=None, rows=None):
            self._first = first
            self._rows = list(rows or [])

        def filter(self, *_args):
            return self

        def order_by(self, *_args):
            return self

        def with_for_update(self):
            return self

        def first(self):
            return self._first

        def all(self):
            return list(self._rows)

    class _Db:
        def query(self, model):
            if model is selection_module.AgentBuilderSession:
                return _Query(first=session)
            if model is selection_module.Workflow:
                return _Query(first=workflow)
            if model is selection_module.AgentBuilderRequest:
                return _Query(rows=[request_row])
            raise AssertionError(f"unexpected model: {model}")

    resolver_calls = []
    monkeypatch.setattr(
        selection_module,
        "has_workflow_permission",
        lambda *_args, **_kwargs: True,
    )
    service = KnowledgeSelectionService(
        _Db(),
        user_id=user_id,
        organization_id=organization_id,
        binding_materializer=lambda *_args, **_kwargs: resolver_calls.append(
            (_args, _kwargs)
        ),
        no_knowledge_candidate_id="no-kb",
    )
    selection = AgentBuilderKnowledgeSelectionRequest.model_validate(
        {
            "resolution_id": "res-kb-legacy",
            "selected_candidates": [
                {
                    "candidate_id": "legacy-rec-1",
                    "resolution_id": "res-kb-legacy",
                    "requirement_id": "kr-1",
                }
            ],
        }
    )

    with pytest.raises(HTTPException) as exc:
        service.select(session.id, selection)

    assert exc.value.status_code == 404
    assert exc.value.detail == "resource_not_found"
    assert resolver_calls == []


def test_knowledge_selection_reads_direct_resolution_candidates_without_legacy_options(
    monkeypatch,
):
    user_id = uuid4()
    organization_id = uuid4()
    workflow_id = uuid4()
    session = SimpleNamespace(
        id=uuid4(),
        user_id=user_id,
        organization_id=organization_id,
        workflow_id=workflow_id,
        protocol_version="direct_edit_v1",
    )
    workflow = SimpleNamespace(
        id=workflow_id,
        organization_id=organization_id,
        graph={
            "nodes": [
                {
                    "id": "llm-1",
                    "type": "llmNode",
                    "position": {"x": 0, "y": 0},
                    "data": {},
                }
            ],
            "edges": [],
        },
        updated_at=datetime.now(timezone.utc),
    )
    structured = AgentBuilderStructuredRequest.model_validate(
        {
            "request_type": "new_workflow",
            "draft_mode": "new_workflow",
            "intent_summary": "safe summary",
            "planned_steps": [
                {
                    "step_id": "step_llm",
                    "capability": "llm",
                    "purpose": "safe purpose",
                }
            ],
            "knowledge_placements": [
                {
                    "requirement_id": "kr-1",
                    "timing": "after_graph",
                    "effect_kind": "binding_only",
                    "target_step_id": "step_llm",
                }
            ],
        }
    )
    knowledge_base_id = uuid4()
    request_row = SimpleNamespace(
        structured_request=structured.model_dump(mode="json"),
        response_payload={
            "safe_step_node_ids": {"step_llm": "llm-1"},
            "knowledge_resolution": {
                "timing": "after_graph",
                "required": True,
                "candidates": [
                    {
                        "type": "knowledge_base",
                        "candidate_id": "rec-safe-1",
                        "resolution_id": "res-kb-1",
                        "requirement_id": "kr-1",
                    }
                ],
                "selected": [],
            },
            "clarification_options": [],
            "_issued_knowledge_handle_bindings": {
                "resolution_id": "res-kb-1",
                "knowledge_bases": {
                    "rec-safe-1": str(knowledge_base_id),
                },
                "collections": {},
            },
            "operation_envelopes": [
                {
                    "kind": "initial_graph",
                    "status": "acknowledged",
                }
            ],
        },
    )

    class _Query:
        def __init__(self, *, first=None, rows=None):
            self._first = first
            self._rows = list(rows or [])

        def filter(self, *_args):
            return self

        def order_by(self, *_args):
            return self

        def with_for_update(self):
            return self

        def first(self):
            return self._first

        def all(self):
            return list(self._rows)

    class _Db:
        def __init__(self):
            self.commits = 0

        def query(self, model):
            if model is selection_module.AgentBuilderSession:
                return _Query(first=session)
            if model is selection_module.Workflow:
                return _Query(first=workflow)
            if model is selection_module.AgentBuilderRequest:
                return _Query(rows=[request_row])
            raise AssertionError(f"unexpected model: {model}")

        def commit(self):
            self.commits += 1

    materializer_calls = []

    def _materializer(*args, **kwargs):
        materializer_calls.append((args, kwargs))
        return {
            "status": "ready",
            "bindings": [
                {
                    "safe_handle": "rec-safe-1",
                    "knowledge_base_id": str(knowledge_base_id),
                    "name": "휴가 정책",
                }
            ],
            "warnings": [],
        }

    monkeypatch.setattr(
        selection_module,
        "has_workflow_permission",
        lambda *_args, **_kwargs: True,
    )
    audit_calls = []
    monkeypatch.setattr(
        selection_module,
        "add_action_audit",
        lambda *_args, **_kwargs: audit_calls.append((_args, _kwargs)),
    )
    db = _Db()
    service = KnowledgeSelectionService(
        db,
        user_id=user_id,
        organization_id=organization_id,
        binding_materializer=_materializer,
        no_knowledge_candidate_id="no-kb",
    )
    selection = AgentBuilderKnowledgeSelectionRequest.model_validate(
        {
            "resolution_id": "res-kb-1",
            "selected_candidates": [
                {
                    "candidate_id": "rec-safe-1",
                    "resolution_id": "res-kb-1",
                    "requirement_id": "kr-1",
                }
            ],
        }
    )

    response = service.select(session.id, selection)

    assert response.resolution_id == "res-kb-1"
    assert response.graph_mutation.completion_context.knowledge_resolution_id == (
        "res-kb-1"
    )
    assert materializer_calls[0][1]["selected_candidate_handles"] == {
        "rec-safe-1"
    }
    assert materializer_calls[0][1]["issued_handle_bindings"] == {
        "knowledge_bases": {"rec-safe-1": str(knowledge_base_id)},
        "collections": {},
    }
    assert request_row.response_payload["knowledge_resolutions"][0][
        "selected_candidate_ids"
    ] == ["rec-safe-1"]
    assert len(request_row.response_payload["operation_envelopes"]) == 2
    assert len(audit_calls) == 1
    assert db.commits == 1


@pytest.mark.parametrize("selection_source", ["agent_builder", "node_editor"])
def test_hierarchical_knowledge_selection_rejects_stale_handles_and_materializes_separately(
    monkeypatch,
    selection_source,
):
    user_id = uuid4()
    organization_id = uuid4()
    workflow_id = uuid4()
    session = SimpleNamespace(
        id=uuid4(),
        user_id=user_id,
        organization_id=organization_id,
        workflow_id=workflow_id,
        protocol_version="direct_edit_v1",
    )
    workflow = SimpleNamespace(
        id=workflow_id,
        organization_id=organization_id,
        graph={
            "nodes": [
                {
                    "id": "llm-1",
                    "type": "llmNode",
                    "position": {"x": 0, "y": 0},
                    "data": {"knowledgeBases": [], "knowledgeCollections": []},
                }
            ],
            "edges": [],
        },
        updated_at=datetime.now(timezone.utc),
    )
    structured = AgentBuilderStructuredRequest.model_validate(
        {
            "request_type": "new_workflow",
            "draft_mode": "new_workflow",
            "intent_summary": "safe summary",
            "planned_steps": [
                {
                    "step_id": "step_llm",
                    "capability": "llm",
                    "purpose": "safe purpose",
                }
            ],
            "knowledge_placements": [
                {
                    "requirement_id": "kr-1",
                    "timing": "after_graph",
                    "effect_kind": "binding_only",
                    "target_step_id": "step_llm",
                }
            ],
        }
    )
    request_row = SimpleNamespace(
        structured_request=structured.model_dump(mode="json"),
        response_payload={
            "safe_step_node_ids": {"step_llm": "llm-1"},
            "knowledge_resolution": {
                "resolution_id": "res-hierarchy-1",
                "timing": "after_graph",
                "required": True,
                "candidates": [],
                "collections": [
                    {
                        "collection_handle": "col-safe-1",
                        "safe_label": "사내 문서",
                        "score": 0.8,
                        "children": [
                            {
                                "kb_handle": "rec-safe-1",
                                "selection_key": "kbsel-safe-1",
                                "safe_label": "휴가 정책",
                                "score": 0.9,
                                "shared_collection_count": 1,
                            }
                        ],
                    }
                ],
                "ungrouped_kbs": [],
                "selected": [],
            },
            "operation_envelopes": [
                {"kind": "initial_graph", "status": "acknowledged"}
            ],
            "knowledge_resolutions": [
                {
                    "resolution_id": "res-hierarchy-1",
                    "operation_id": str(uuid4()),
                    "timing": "after_graph",
                    "selected_candidate_ids": ["col-stale", "rec-stale"],
                    "selected_collection_handles": ["col-stale"],
                    "selected_kb_handles": ["rec-stale"],
                    "status": "unapplied",
                }
            ],
        },
    )

    class _Query:
        def __init__(self, *, first=None, rows=None):
            self._first = first
            self._rows = list(rows or [])

        def filter(self, *_args):
            return self

        def order_by(self, *_args):
            return self

        def with_for_update(self):
            return self

        def first(self):
            return self._first

        def all(self):
            return list(self._rows)

    class _Db:
        def query(self, model):
            if model is selection_module.AgentBuilderSession:
                return _Query(first=session)
            if model is selection_module.Workflow:
                return _Query(first=workflow)
            if model is selection_module.AgentBuilderRequest:
                return _Query(rows=[request_row])
            raise AssertionError(f"unexpected model: {model}")

        def commit(self):
            return None

    materializer_calls = []
    refresh_calls = []
    knowledge_base_id = uuid4()
    collection_id = uuid4()
    editor_kb_handle = "rec-editor-outside-top-k"
    editor_collection_handle = "col-editor-outside-top-k"

    def _materializer(*args, **kwargs):
        materializer_calls.append((args, kwargs))
        requested_kb_handles = kwargs.get("selected_kb_handles") or {
            "rec-safe-1"
        }
        requested_collection_handles = kwargs.get(
            "selected_collection_handles"
        ) or {"col-safe-1"}
        selected_kb_handle = sorted(requested_kb_handles)[0]
        selected_collection_handle = sorted(requested_collection_handles)[0]
        return {
            "status": "ready",
            "bindings": [
                {
                    "safe_handle": selected_kb_handle,
                    "knowledge_base_id": str(knowledge_base_id),
                    "name": "휴가 정책",
                }
            ],
            "collections": [
                {
                    "safe_handle": selected_collection_handle,
                    "knowledge_collection_id": str(collection_id),
                    "name": "사내 문서",
                }
            ],
            "warnings": [],
        }

    def _refresh_hierarchy(structured_request):
        refresh_calls.append(structured_request)
        return {
            "status": "clarification_required",
            "options": [],
            "knowledge_selection": {
                "collections": [
                    {
                        "collection_handle": "col-safe-1",
                        "safe_label": "사내 문서 최신",
                        "score": 0.8,
                        "children": [
                            {
                                "kb_handle": "rec-safe-1",
                                "selection_key": "kbsel-safe-1",
                                "safe_label": "휴가 정책 최신",
                                "score": 0.9,
                                "shared_collection_count": 1,
                            }
                        ],
                    }
                ],
                "ungrouped_kbs": [],
            },
        }

    monkeypatch.setattr(
        selection_module,
        "has_workflow_permission",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(selection_module, "add_action_audit", lambda *_args, **_kwargs: None)
    service = KnowledgeSelectionService(
        _Db(),
        user_id=user_id,
        organization_id=organization_id,
        binding_materializer=_materializer,
        knowledge_selection_refresher=_refresh_hierarchy,
        no_knowledge_candidate_id="no-kb",
        knowledge_base_handle_resolver=lambda resource_id: (
            editor_kb_handle if resource_id == knowledge_base_id else "rec-stale"
        ),
        knowledge_collection_handle_resolver=lambda resource_id: (
            editor_collection_handle
            if resource_id == collection_id
            else "col-stale"
        ),
    )

    with pytest.raises(HTTPException) as stale:
        service.select(
            session.id,
            AgentBuilderKnowledgeSelectionRequest(
                resolution_id="res-hierarchy-1",
                selected_collection_handles=["col-stale"],
            ),
        )
    assert stale.value.status_code == 409
    assert stale.value.detail == {"code": "knowledge_selection_stale"}
    assert materializer_calls == []
    assert refresh_calls == [structured]
    assert request_row.response_payload["knowledge_resolution"]["collections"][0][
        "safe_label"
    ] == "사내 문서 최신"
    assert request_row.response_payload["knowledge_resolutions"][0][
        "selected_candidate_ids"
    ] == []

    request = (
        AgentBuilderKnowledgeSelectionRequest(
            resolution_id="res-hierarchy-1",
            editor_target_node_id="llm-1",
            selected_knowledge_collection_ids=[collection_id],
            selected_knowledge_base_ids=[knowledge_base_id],
        )
        if selection_source == "node_editor"
        else AgentBuilderKnowledgeSelectionRequest(
            resolution_id="res-hierarchy-1",
            selected_collection_handles=["col-safe-1", "col-safe-1"],
            selected_kb_handles=["rec-safe-1", "rec-safe-1"],
        )
    )
    response = service.select(session.id, request)

    expected_collection_handle = (
        editor_collection_handle if selection_source == "node_editor" else "col-safe-1"
    )
    expected_kb_handle = (
        editor_kb_handle if selection_source == "node_editor" else "rec-safe-1"
    )
    assert response.selected_collection_handles == [expected_collection_handle]
    assert response.selected_kb_handles == [expected_kb_handle]
    expected_issued_bindings = (
        {
            "knowledge_bases": {
                editor_kb_handle: str(knowledge_base_id),
            },
            "collections": {
                editor_collection_handle: str(collection_id),
            },
        }
        if selection_source == "node_editor"
        else {}
    )
    assert materializer_calls[0][1] == {
        "selected_kb_handles": {expected_kb_handle},
        "selected_collection_handles": {expected_collection_handle},
        "issued_handle_bindings": expected_issued_bindings,
    }
    operation = response.graph_mutation.operations[0]
    assert operation.data["knowledgeBases"] == [
        {"id": str(knowledge_base_id), "name": "휴가 정책"}
    ]
    assert operation.data["knowledgeCollections"] == [
        {"id": str(collection_id), "safeLabel": "사내 문서"}
    ]
    assert request_row.response_payload["knowledge_resolutions"][0][
        "selected_candidate_ids"
    ] == sorted([expected_collection_handle, expected_kb_handle])
    assert request_row.response_payload["knowledge_resolutions"][0][
        "selected_collection_handles"
    ] == [expected_collection_handle]
    assert request_row.response_payload["knowledge_resolutions"][0][
        "selected_kb_handles"
    ] == [expected_kb_handle]


def test_before_graph_empty_direct_resolution_uses_dedicated_empty_selection(
    monkeypatch,
):
    user_id = uuid4()
    organization_id = uuid4()
    workflow_id = uuid4()
    session = SimpleNamespace(
        id=uuid4(),
        user_id=user_id,
        organization_id=organization_id,
        workflow_id=workflow_id,
        protocol_version="direct_edit_v1",
    )
    workflow = SimpleNamespace(
        id=workflow_id,
        organization_id=organization_id,
        graph={"nodes": [], "edges": []},
        updated_at=datetime.now(timezone.utc),
    )
    structured = AgentBuilderStructuredRequest.model_validate(
        {
            "request_type": "new_workflow",
            "draft_mode": "new_workflow",
            "intent_summary": "safe summary",
            "planned_steps": [
                {
                    "step_id": "step_input",
                    "capability": "webhook_trigger",
                    "purpose": "input",
                },
                {
                    "step_id": "step_llm",
                    "capability": "knowledge_backed_llm",
                    "purpose": "answer with knowledge",
                    "depends_on": ["step_input"],
                },
                {
                    "step_id": "step_answer",
                    "capability": "answer",
                    "purpose": "answer",
                    "depends_on": ["step_llm"],
                },
            ],
            "knowledge_requirements": [
                {
                    "requirement_id": "kr-empty",
                    "query_topics": ["policy"],
                    "target_step_ref": "step_llm",
                }
            ],
            "pending_resolution": [
                {
                    "resolution_id": "res-empty",
                    "slot_type": "knowledge_base",
                    "slot_key": "llm.knowledgeBases",
                    "target_step_ref": "step_llm",
                }
            ],
            "knowledge_placements": [
                {
                    "requirement_id": "kr-empty",
                    "timing": "before_graph",
                    "effect_kind": "insert_step",
                    "target_step_id": "step_llm",
                    "knowledge_step_id": "step_llm",
                    "upstream_step_id": "step_input",
                    "downstream_step_id": "step_answer",
                    "empty_selection_bridge": "connect_upstream_to_downstream",
                }
            ],
        }
    )
    mutation = {
        "operation_id": uuid4(),
        "kind": "initial_graph",
        "status": "pending_apply",
        "generation_mode": "configure_and_generate",
        "workflow_id": workflow_id,
        "base_graph_hash": "a" * 64,
        "expected_workflow_updated_at": workflow.updated_at,
        "expected_result_graph_hash": "b" * 64,
        "catalog_version": 3,
        "operations": [],
        "affected_node_ids": [],
        "completion_context": {"knowledge_resolution_id": "res-empty"},
    }
    request_row = SimpleNamespace(
        structured_request=structured.model_dump(mode="json"),
        response_payload={
            "knowledge_resolution": {
                "resolution_id": "res-empty",
                "timing": "before_graph",
                "required": True,
                "candidates": [],
                "selected": [],
            },
            "clarification_options": [],
            "operation_envelopes": [],
        },
    )

    class _Query:
        def __init__(self, *, first=None, rows=None):
            self._first = first
            self._rows = list(rows or [])

        def filter(self, *_args):
            return self

        def order_by(self, *_args):
            return self

        def with_for_update(self):
            return self

        def first(self):
            return self._first

        def all(self):
            return list(self._rows)

    class _Db:
        def __init__(self):
            self.commits = 0

        def query(self, model):
            if model is selection_module.AgentBuilderSession:
                return _Query(first=session)
            if model is selection_module.Workflow:
                return _Query(first=workflow)
            if model is selection_module.AgentBuilderRequest:
                return _Query(rows=[request_row])
            raise AssertionError(f"unexpected model: {model}")

        def commit(self):
            self.commits += 1

    builder_calls = []

    def _builder(**kwargs):
        builder_calls.append(kwargs)
        return {"mutation": GraphMutation.model_validate(mutation)}

    monkeypatch.setattr(
        selection_module,
        "has_workflow_permission",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(selection_module, "add_action_audit", lambda *_a, **_k: None)
    db = _Db()
    service = KnowledgeSelectionService(
        db,
        user_id=user_id,
        organization_id=organization_id,
        binding_materializer=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("empty selection must not materialize Knowledge Base handles")
        ),
        no_knowledge_candidate_id="no-kb",
        before_graph_builder=_builder,
    )
    selection = AgentBuilderKnowledgeSelectionRequest.model_validate(
        {
            "resolution_id": "res-empty",
            "selected_candidates": [],
        }
    )

    response = service.select(session.id, selection)

    assert response.resolution_id == "res-empty"
    assert builder_calls[0]["bindings"] == []
    assert request_row.response_payload["knowledge_resolutions"][0][
        "selected_candidate_ids"
    ] == []
    assert db.commits == 1


def test_before_graph_bindings_update_only_the_placement_target_llm():
    structured = AgentBuilderStructuredRequest.model_validate(
        {
            "request_type": "new_workflow",
            "draft_mode": "new_workflow",
            "intent_summary": "two llm workflow",
            "planned_steps": [
                {
                    "step_id": "step_input",
                    "capability": "start_input",
                    "purpose": "receive request",
                },
                {
                    "step_id": "step_other_llm",
                    "capability": "llm",
                    "purpose": "request preprocessing",
                    "depends_on": ["step_input"],
                },
                {
                    "step_id": "step_knowledge_llm",
                    "capability": "knowledge_backed_llm",
                    "purpose": "answer with knowledge",
                    "depends_on": ["step_other_llm"],
                },
                {
                    "step_id": "step_answer",
                    "capability": "answer",
                    "purpose": "return answer",
                    "depends_on": ["step_knowledge_llm"],
                },
            ],
        }
    )
    placement = AgentBuilderKnowledgePlacement(
        requirement_id="kr-target",
        timing="before_graph",
        effect_kind="insert_step",
        target_step_id="step_knowledge_llm",
        knowledge_step_id="step_knowledge_llm",
        upstream_step_id="step_other_llm",
        downstream_step_id="step_answer",
        empty_selection_bridge="connect_upstream_to_downstream",
    )
    graph = {
        "nodes": [
            {
                "id": "llm-other",
                "type": "llmNode",
                "data": {"knowledgeBases": [], "knowledgeCollections": []},
            },
            {
                "id": "llm-target",
                "type": "llmNode",
                "data": {"knowledgeBases": [], "knowledgeCollections": []},
            },
        ],
        "edges": [],
    }
    service = AgentBuilderService(
        SimpleNamespace(),
        user=SimpleNamespace(id=uuid4()),
        organization_id=uuid4(),
    )

    service._apply_before_graph_knowledge_bindings(  # noqa: SLF001
        candidate_graph=graph,
        structured=structured,
        placement=placement,
        bindings=[
            {
                "knowledge_base_id": "kb-internal-1",
                "name": "Safe KB",
            }
        ],
        collection_bindings=[
            {
                "knowledge_collection_id": "collection-internal-1",
                "name": "Safe Collection",
            }
        ],
    )

    assert graph["nodes"][0]["data"]["knowledgeBases"] == []
    assert graph["nodes"][0]["data"]["knowledgeCollections"] == []
    assert graph["nodes"][1]["data"]["knowledgeBases"] == [
        {"id": "kb-internal-1", "name": "Safe KB"}
    ]
    assert graph["nodes"][1]["data"]["knowledgeCollections"] == [
        {"id": "collection-internal-1", "safeLabel": "Safe Collection"}
    ]


def test_before_graph_selection_builds_selected_and_empty_topology_without_planner_rerun(
    monkeypatch,
):
    structured = AgentBuilderStructuredRequest.model_validate(
        {
            "request_type": "new_workflow",
            "draft_mode": "new_workflow",
            "intent_summary": "사내 문서 답변",
            "planned_steps": [
                {
                    "step_id": "step_input",
                    "capability": "webhook_trigger",
                    "purpose": "요청 수신",
                },
                {
                    "step_id": "step_other_llm",
                    "capability": "llm",
                    "purpose": "요청 전처리",
                    "depends_on": ["step_input"],
                },
                {
                    "step_id": "step_knowledge_llm",
                    "capability": "knowledge_backed_llm",
                    "purpose": "사내 문서 답변",
                    "depends_on": ["step_other_llm"],
                },
                {
                    "step_id": "step_answer",
                    "capability": "answer",
                    "purpose": "응답",
                    "depends_on": ["step_knowledge_llm"],
                },
            ],
            "required_capabilities": [
                "webhook_trigger",
                "llm",
                "knowledge_backed_llm",
                "knowledge_base",
                "answer",
            ],
        }
    )
    placement = AgentBuilderKnowledgePlacement(
        requirement_id="kr-1",
        timing="before_graph",
        effect_kind="insert_step",
        target_step_id="step_knowledge_llm",
        knowledge_step_id="step_knowledge_llm",
        upstream_step_id="step_other_llm",
        downstream_step_id="step_answer",
        empty_selection_bridge="connect_upstream_to_downstream",
    )
    workflow = SimpleNamespace(
        id=uuid4(),
        graph={"nodes": [], "edges": [], "viewport": {"x": 0, "y": 0, "zoom": 1}},
        updated_at=datetime.now(timezone.utc),
    )
    service = AgentBuilderService(
        SimpleNamespace(),
        user=SimpleNamespace(id=uuid4()),
        organization_id=uuid4(),
    )
    monkeypatch.setattr(
        ParameterCandidateProvider,
        "enrich_group",
        lambda _self, group: group,
    )

    selected = service.build_before_graph_knowledge_selection(
        structured=structured,
        workflow=workflow,
        placement=placement,
        bindings=[
            {
                "safe_handle": "rec-safe-1",
                "name": "사내 문서",
                "knowledge_base_id": str(uuid4()),
            }
        ],
        resolution_id="res-kb-1",
    )
    empty = service.build_before_graph_knowledge_selection(
        structured=structured,
        workflow=workflow,
        placement=placement,
        bindings=[],
        resolution_id="res-kb-2",
    )

    selected_nodes = [
        operation.node
        for operation in selected["mutation"].operations
        if operation.op == "add_node"
    ]
    target_node_id = selected["step_node_ids"]["step_knowledge_llm"]
    other_node_id = selected["step_node_ids"]["step_other_llm"]
    target_llm = next(node for node in selected_nodes if node.id == target_node_id)
    other_llm = next(node for node in selected_nodes if node.id == other_node_id)
    assert target_llm.data["knowledgeBases"][0]["id"] != "rec-safe-1"
    assert other_llm.data["knowledgeBases"] == []
    assert selected["mutation"].completion_context.knowledge_resolution_id == "res-kb-1"
    assert "step_knowledge_llm" not in empty["step_node_ids"]
    empty_nodes = [
        operation.node
        for operation in empty["mutation"].operations
        if operation.op == "add_node"
    ]
    empty_other_llm = next(
        node
        for node in empty_nodes
        if node.id == empty["step_node_ids"]["step_other_llm"]
    )
    assert empty_other_llm.type == "llmNode"
    assert empty_other_llm.data["knowledgeBases"] == []
