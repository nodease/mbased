from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from apps.gateway.adapters.db.agent_builder_repository import (
    AgentBuilderRepository,
    AgentBuilderRepositoryError,
)
from apps.gateway.application.agent_builder.graph_mutation_builder import (
    GraphMutationValidationError,
    GraphMutationBuilder,
    apply_graph_operations,
    deferred_parameter_projection,
    materialize_candidate_features,
    materialize_candidate_graph,
)
from apps.gateway.application.agent_builder.workflow_cas import (
    WorkflowDraftCASService,
    WorkflowMutationConflict,
)
from apps.gateway.services.workflow_service import WorkflowService
from apps.shared.schemas.agent_builder import (
    AgentBuilderParameterGroup,
    AgentBuilderParameterTask,
    GraphMutationSafeEnvelope,
)
from apps.shared.schemas.workflow import WorkflowDraftRequest


def _node(node_id, node_type):
    return {
        "id": node_id,
        "type": node_type,
        "position": {"x": 0, "y": 0},
        "data": {"title": node_id},
    }


def _draft_request(payload):
    data = dict(payload)
    context = data.get("mutation_context")
    if isinstance(context, dict):
        data.setdefault("expected_graph_hash", context["expected_base_graph_hash"])
        data.setdefault("expected_updated_at", context["expected_workflow_updated_at"])
    return WorkflowDraftRequest.model_validate(data)


def _issued_mutation(now):
    base = {"nodes": [], "edges": [], "viewport": {"x": 0, "y": 0, "zoom": 1}}
    operations = [
        {"op": "add_node", "node": _node("start", "startNode")},
        {"op": "add_node", "node": _node("answer", "answerNode")},
        {
            "op": "add_edge",
            "edge": {"id": "e1", "source": "start", "target": "answer"},
        },
    ]
    mutation = GraphMutationBuilder().build(
        operation_id=uuid4(),
        kind="initial_graph",
        generation_mode="configure_and_generate",
        workflow_id=uuid4(),
        base_graph=base,
        expected_workflow_updated_at=now,
        operations=operations,
    )
    return base, mutation


def _request(mutation, base):
    result = apply_graph_operations(base, mutation.operations)
    return _draft_request(
        {
            **result,
            "mutation_context": {
                "operation_id": str(mutation.operation_id),
                "action": "apply",
                "expected_base_graph_hash": mutation.base_graph_hash,
                "expected_workflow_updated_at": mutation.expected_workflow_updated_at,
                "catalog_version": 3,
            },
        }
    )


def _revert_request(mutation, result_graph, now):
    return _draft_request(
        {
            **result_graph,
            "nodes": [],
            "edges": [],
            "mutation_context": {
                "operation_id": str(mutation.operation_id),
                "action": "revert",
                "expected_base_graph_hash": mutation.expected_result_graph_hash,
                "expected_workflow_updated_at": now,
                "catalog_version": 3,
            },
        }
    )


def _redo_request(mutation, result_graph, base_graph_hash, now):
    return _draft_request(
        {
            **result_graph,
            "mutation_context": {
                "operation_id": str(mutation.operation_id),
                "action": "redo",
                "expected_base_graph_hash": base_graph_hash,
                "expected_workflow_updated_at": now,
                "catalog_version": 3,
            },
        }
    )


@pytest.fixture(autouse=True)
def _allow_workflow_write(monkeypatch):
    monkeypatch.setattr(
        "apps.gateway.services.workflow_service.has_workflow_permission",
        lambda *args, **kwargs: True,
    )


def test_draft_save_refreshes_identity_map_before_row_lock(monkeypatch):
    now = datetime.now(timezone.utc)
    workflow = SimpleNamespace(
        id=uuid4(),
        organization_id=uuid4(),
        graph={"nodes": [], "edges": [], "viewport": {"x": 0, "y": 0, "zoom": 1}},
        features={},
        env_variables=[],
        runtime_variables=[],
        updated_at=now,
    )
    request = WorkflowDraftRequest.model_validate(
        {
            "nodes": [],
            "edges": [],
            "viewport": {"x": 0, "y": 0, "zoom": 1},
            "expected_graph_hash": "0" * 64,
            "expected_updated_at": now,
        }
    )
    query = Mock()
    query.filter.return_value = query
    query.populate_existing.return_value = query
    query.with_for_update.return_value = query
    query.first.return_value = workflow
    db = Mock()
    db.query.return_value = query

    monkeypatch.setattr(WorkflowService, "validate_knowledge_references", Mock())
    monkeypatch.setattr(
        "apps.gateway.services.workflow_service.WorkflowDraftCASService.validate_expected_draft_state",
        Mock(),
    )
    monkeypatch.setattr(WorkflowService, "validate_mail_credential_references", Mock())
    db.refresh = Mock()

    result = WorkflowService.save_draft(
        db,
        str(workflow.id),
        request,
        user_id=str(uuid4()),
    )

    query.populate_existing.assert_called_once_with()
    query.with_for_update.assert_called_once_with()
    assert result["canonical_deferred_parameters"] == []


def test_deferred_parameter_projection_uses_scoped_node_paths():
    graph = {
        "nodes": [
            {
                **_node("shared-1", "githubNode"),
                "data": {"_deferred_parameters": ["repo_owner"]},
            },
            {
                **_node("loop-1", "loopNode"),
                "data": {
                    "subGraph": {
                        "nodes": [
                            {
                                **_node("shared-1", "mailNode"),
                                "data": {
                                    "_deferred_parameters": ["credential_id"]
                                },
                            }
                        ],
                        "edges": [],
                    }
                },
            },
        ],
        "edges": [],
    }

    assert deferred_parameter_projection(graph) == [
        {"node_path": ["shared-1"], "parameter_keys": ["repo_owner"]},
        {"node_path": ["loop-1"], "parameter_keys": []},
        {
            "node_path": ["loop-1", "shared-1"],
            "parameter_keys": ["credential_id"],
        },
    ]


def test_draft_save_rechecks_write_permission_after_row_lock(monkeypatch):
    now = datetime.now(timezone.utc)
    workflow = SimpleNamespace(
        id=uuid4(),
        organization_id=uuid4(),
        graph={"nodes": [], "edges": [], "viewport": {"x": 0, "y": 0, "zoom": 1}},
        features={},
        env_variables=[],
        runtime_variables=[],
        updated_at=now,
    )
    request = WorkflowDraftRequest.model_validate(
        {
            "nodes": [],
            "edges": [],
            "viewport": {"x": 0, "y": 0, "zoom": 1},
            "expected_graph_hash": "0" * 64,
            "expected_updated_at": now,
        }
    )
    query = Mock()
    query.filter.return_value = query
    query.populate_existing.return_value = query
    query.with_for_update.return_value = query
    query.first.return_value = workflow
    db = Mock()
    db.query.return_value = query
    permission_check = Mock(return_value=False)
    monkeypatch.setattr(
        "apps.gateway.services.workflow_service.has_workflow_permission",
        permission_check,
    )
    knowledge_validation = Mock()
    monkeypatch.setattr(
        WorkflowService,
        "validate_knowledge_references",
        knowledge_validation,
    )

    with pytest.raises(HTTPException) as exc_info:
        WorkflowService.save_draft(
            db,
            str(workflow.id),
            request,
            user_id=str(uuid4()),
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Forbidden"
    permission_check.assert_called_once()
    knowledge_validation.assert_not_called()
    db.commit.assert_not_called()


def test_ordinary_draft_save_removes_presentation_fields_recursively(monkeypatch):
    now = datetime.now(timezone.utc)
    workflow = SimpleNamespace(
        id=uuid4(),
        organization_id=uuid4(),
        graph={"nodes": [], "edges": [], "viewport": {"x": 0, "y": 0, "zoom": 1}},
        features={},
        env_variables=[],
        runtime_variables=[],
        updated_at=now,
    )
    request = WorkflowDraftRequest.model_validate(
        {
            "nodes": [
                {
                    "id": "loop-1",
                    "type": "loopNode",
                    "position": {"x": 0, "y": 0},
                    "width": 320,
                    "height": 180,
                    "measured": {"width": 320, "height": 180},
                    "data": {
                        "displayNumber": 1,
                        "status": "success",
                        "observability": {"latency_ms": 10},
                        "subGraph": {
                            "nodes": [
                                {
                                    "id": "nested-1",
                                    "type": "codeNode",
                                    "position": {"x": 0, "y": 0},
                                    "width": 200,
                                    "height": 96,
                                    "measured": {"width": 200, "height": 96},
                                    "data": {
                                        "code": "return inputs",
                                        "displayNumber": 2,
                                        "status": "running",
                                        "observability": {"latency_ms": 5},
                                    },
                                }
                            ],
                            "edges": [
                                {
                                    "id": "nested-edge",
                                    "source": "nested-1",
                                    "target": "nested-1",
                                    "selected": True,
                                }
                            ],
                        },
                    },
                }
            ],
            "edges": [],
            "viewport": {"x": 0, "y": 0, "zoom": 1},
            "features": {
                "noteNodes": [
                    {
                        "id": "note-1",
                        "type": "note",
                        "position": {"x": 20, "y": 20},
                        "width": 240,
                        "height": 120,
                        "measured": {"width": 240, "height": 120},
                        "selected": True,
                        "data": {
                            "text": "server note",
                            "displayNumber": 9,
                            "status": "success",
                        },
                    }
                ]
            },
            "expected_graph_hash": "0" * 64,
            "expected_updated_at": now,
        }
    )
    query = Mock()
    query.filter.return_value = query
    query.populate_existing.return_value = query
    query.with_for_update.return_value = query
    query.first.return_value = workflow
    db = Mock()
    db.query.return_value = query

    monkeypatch.setattr(WorkflowService, "validate_knowledge_references", Mock())
    monkeypatch.setattr(
        "apps.gateway.services.workflow_service.WorkflowDraftCASService.validate_expected_draft_state",
        Mock(),
    )
    monkeypatch.setattr(WorkflowService, "validate_mail_credential_references", Mock())

    WorkflowService.save_draft(db, str(workflow.id), request, user_id=str(uuid4()))

    root_data = workflow.graph["nodes"][0]["data"]
    nested_data = root_data["subGraph"]["nodes"][0]["data"]
    for field in ("displayNumber", "status", "observability"):
        assert field not in root_data
        assert field not in nested_data
    for node in (
        workflow.graph["nodes"][0],
        root_data["subGraph"]["nodes"][0],
    ):
        assert "width" not in node
        assert "height" not in node
        assert "measured" not in node
    nested_edge = root_data["subGraph"]["edges"][0]
    assert nested_edge["id"] == "nested-edge"
    assert nested_edge["source"] == "nested-1"
    assert nested_edge["target"] == "nested-1"
    assert "selected" not in nested_edge
    note = workflow.features["noteNodes"][0]
    assert note["data"] == {"text": "server note"}
    for field in ("width", "height", "measured", "selected"):
        assert field not in note


@pytest.mark.parametrize(
    "features",
    [
        {"noteNodes": "not-a-list"},
        {
            "noteNodes": [
                {
                    "id": "not-a-note",
                    "type": "codeNode",
                    "position": {"x": 0, "y": 0},
                    "data": {"code": "return inputs"},
                }
            ]
        },
    ],
)
def test_feature_materializer_rejects_invalid_note_nodes(features):
    with pytest.raises(GraphMutationValidationError, match="workflow.features_invalid"):
        materialize_candidate_features(features)


def test_draft_save_rejects_invalid_note_features_with_safe_422(monkeypatch):
    now = datetime.now(timezone.utc)
    workflow = SimpleNamespace(
        id=uuid4(),
        organization_id=uuid4(),
        graph={"nodes": [], "edges": [], "viewport": {"x": 0, "y": 0, "zoom": 1}},
        features={},
        env_variables=[],
        runtime_variables=[],
        updated_at=now,
    )
    request = WorkflowDraftRequest.model_validate(
        {
            "nodes": [],
            "edges": [],
            "viewport": {"x": 0, "y": 0, "zoom": 1},
            "features": {"noteNodes": "not-a-list"},
            "expected_graph_hash": "0" * 64,
            "expected_updated_at": now,
        }
    )
    query = Mock()
    query.filter.return_value = query
    query.populate_existing.return_value = query
    query.with_for_update.return_value = query
    query.first.return_value = workflow
    db = Mock()
    db.query.return_value = query
    monkeypatch.setattr(WorkflowService, "validate_knowledge_references", Mock())
    monkeypatch.setattr(
        "apps.gateway.services.workflow_service.WorkflowDraftCASService.validate_expected_draft_state",
        Mock(),
    )

    with pytest.raises(HTTPException) as exc_info:
        WorkflowService.save_draft(
            db,
            str(workflow.id),
            request,
            user_id=str(uuid4()),
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "workflow.features_invalid"
    db.commit.assert_not_called()


@pytest.mark.parametrize(
    "subgraph",
    [
        {
            "nodes": [
                {
                    "id": "nested-1",
                    "type": "codeNode",
                    "data": {"code": "return inputs"},
                }
            ],
            "edges": [],
        },
        {
            "nodes": [
                {
                    "id": "nested-1",
                    "type": "codeNode",
                    "position": {"x": 0, "y": 0},
                    "data": {"code": "return inputs"},
                }
            ],
            "edges": [{"id": "nested-edge", "source": "nested-1"}],
        },
    ],
)
def test_draft_save_rejects_invalid_nested_graph_with_safe_422(
    monkeypatch,
    subgraph,
):
    now = datetime.now(timezone.utc)
    workflow = SimpleNamespace(
        id=uuid4(),
        organization_id=uuid4(),
        graph={"nodes": [], "edges": [], "viewport": {"x": 0, "y": 0, "zoom": 1}},
        features={},
        env_variables=[],
        runtime_variables=[],
        updated_at=now,
    )
    request = WorkflowDraftRequest.model_validate(
        {
            "nodes": [
                {
                    "id": "loop-1",
                    "type": "loopNode",
                    "position": {"x": 0, "y": 0},
                    "data": {"subGraph": subgraph},
                }
            ],
            "edges": [],
            "viewport": {"x": 0, "y": 0, "zoom": 1},
            "expected_graph_hash": "0" * 64,
            "expected_updated_at": now,
        }
    )
    query = Mock()
    query.filter.return_value = query
    query.populate_existing.return_value = query
    query.with_for_update.return_value = query
    query.first.return_value = workflow
    db = Mock()
    db.query.return_value = query
    monkeypatch.setattr(WorkflowService, "validate_knowledge_references", Mock())
    monkeypatch.setattr(
        "apps.gateway.services.workflow_service.WorkflowDraftCASService.validate_expected_draft_state",
        Mock(),
    )
    monkeypatch.setattr(WorkflowService, "validate_mail_credential_references", Mock())

    with pytest.raises(HTTPException) as exc_info:
        WorkflowService.save_draft(
            db,
            str(workflow.id),
            request,
            user_id=str(uuid4()),
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "workflow.graph_invalid"
    db.commit.assert_not_called()


def test_graph_materializer_wraps_nested_schema_errors():
    graph = {
        "nodes": [
            {
                "id": "loop-1",
                "type": "loopNode",
                "position": {"x": 0, "y": 0},
                "data": {
                    "subGraph": {
                        "nodes": [
                            {
                                "id": "nested-1",
                                "type": "codeNode",
                                "data": {},
                            }
                        ],
                        "edges": [],
                    }
                },
            }
        ],
        "edges": [],
    }

    with pytest.raises(GraphMutationValidationError, match="workflow.graph_invalid"):
        materialize_candidate_graph(graph)


def test_graph_materializer_clears_only_catalog_valid_deferred_parameters():
    graph = {
        "nodes": [
            {
                "id": "github-1",
                "type": "githubNode",
                "position": {"x": 0, "y": 0},
                "data": {
                    "title": "GitHub",
                    "action": "get_pr",
                    "repo_owner": "nodease",
                    "repo_name": "mbased",
                    "pr_number": 0,
                    "_deferred_parameters": ["repo_owner", "pr_number"],
                },
            }
        ],
        "edges": [],
    }

    materialized = materialize_candidate_graph(graph)

    assert materialized["nodes"][0]["data"]["_deferred_parameters"] == [
        "pr_number"
    ]


def test_agent_builder_draft_save_materializes_note_features(monkeypatch):
    now = datetime.now(timezone.utc)
    base, mutation = _issued_mutation(now)
    workflow = SimpleNamespace(
        id=mutation.workflow_id,
        organization_id=uuid4(),
        graph=base,
        features={},
        env_variables=[],
        runtime_variables=[],
        updated_at=now,
    )
    request = _request(mutation, base).model_copy(
        update={
            "features": {
                "noteNodes": [
                    {
                        "id": "note-1",
                        "type": "note",
                        "position": {"x": 10, "y": 10},
                        "selected": True,
                        "width": 200,
                        "data": {
                            "text": "safe note",
                            "status": "success",
                            "displayNumber": 7,
                        },
                    }
                ]
            }
        }
    )
    query = Mock()
    query.filter.return_value = query
    query.populate_existing.return_value = query
    query.with_for_update.return_value = query
    query.first.return_value = workflow
    db = Mock()
    db.query.return_value = query
    request_row = SimpleNamespace(response_payload={})
    repository = Mock()
    repository.load_request_for_operation.return_value = (
        request_row,
        GraphMutationSafeEnvelope.from_mutation(mutation).model_dump(mode="json"),
    )
    monkeypatch.setattr(
        "apps.gateway.services.workflow_service.AgentBuilderRepository",
        lambda: repository,
    )
    monkeypatch.setattr(WorkflowService, "validate_knowledge_references", Mock())
    monkeypatch.setattr(WorkflowService, "validate_mail_credential_references", Mock())
    monkeypatch.setattr(
        "apps.gateway.services.workflow_service.add_action_audit",
        Mock(),
    )

    WorkflowService.save_draft(
        db,
        str(workflow.id),
        request,
        user_id=str(uuid4()),
    )

    note = workflow.features["noteNodes"][0]
    assert note["data"] == {"text": "safe note"}
    assert "selected" not in note
    assert "width" not in note


def test_cas_validation_returns_canonical_graph_acknowledgement():
    now = datetime.now(timezone.utc)
    base, mutation = _issued_mutation(now)
    workflow = SimpleNamespace(id=mutation.workflow_id, graph=base, updated_at=now)

    result = WorkflowDraftCASService.validate_candidate(
        workflow=workflow,
        request=_request(mutation, base),
        envelope=GraphMutationSafeEnvelope.from_mutation(mutation),
    )

    assert result.graph_hash == mutation.expected_result_graph_hash
    assert result.operation_id == mutation.operation_id
    assert all(
        node["data"]["configuration_state"] in {"resolved", "unresolved"}
        for node in result.graph["nodes"]
    )


def test_cas_candidate_ignores_react_flow_runtime_fields():
    now = datetime.now(timezone.utc)
    base, mutation = _issued_mutation(now)
    workflow = SimpleNamespace(id=mutation.workflow_id, graph=base, updated_at=now)
    candidate = _request(mutation, base).model_dump(mode="python")
    candidate["nodes"][0].update(
        {
            "measured": {"width": 240, "height": 96},
            "selected": True,
            "dragging": False,
        }
    )
    candidate["nodes"][0]["data"]["displayNumber"] = 7
    candidate["edges"][0]["selected"] = True

    result = WorkflowDraftCASService.validate_candidate(
        workflow=workflow,
        request=WorkflowDraftRequest.model_validate(candidate),
        envelope=GraphMutationSafeEnvelope.from_mutation(mutation),
    )

    assert result.graph_hash == mutation.expected_result_graph_hash
    assert all("measured" not in node for node in result.graph["nodes"])
    assert all("selected" not in node for node in result.graph["nodes"])
    assert all("dragging" not in node for node in result.graph["nodes"])
    assert all("selected" not in edge for edge in result.graph["edges"])
    assert all("displayNumber" not in node["data"] for node in result.graph["nodes"])


def test_graph_edit_hash_includes_server_derived_state_for_existing_nodes():
    now = datetime.now(timezone.utc)
    workflow_id = uuid4()
    base = {
        "nodes": [
            _node("start", "startNode"),
            _node("llm", "llmNode"),
            _node("answer", "answerNode"),
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "llm"},
            {"id": "e2", "source": "llm", "target": "answer"},
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }
    mutation = GraphMutationBuilder().build(
        operation_id=uuid4(),
        kind="graph_edit",
        generation_mode="structure_only",
        workflow_id=workflow_id,
        base_graph=base,
        expected_workflow_updated_at=now,
        operations=[
            {"op": "remove_edge", "edge_id": "e2"},
            {"op": "add_node", "node": _node("template", "templateNode")},
            {
                "op": "add_edge",
                "edge": {"id": "e3", "source": "llm", "target": "template"},
            },
            {
                "op": "add_edge",
                "edge": {"id": "e4", "source": "template", "target": "answer"},
            },
        ],
    )
    workflow = SimpleNamespace(id=workflow_id, graph=base, updated_at=now)

    result = WorkflowDraftCASService.validate_candidate(
        workflow=workflow,
        request=_request(mutation, base),
        envelope=GraphMutationSafeEnvelope.from_mutation(mutation),
    )

    assert result.graph_hash == mutation.expected_result_graph_hash
    assert all(
        node["data"]["configuration_state"] in {"resolved", "unresolved"}
        for node in result.graph["nodes"]
    )


@pytest.mark.parametrize("stale_field", ["graph", "updated_at", "result"])
def test_cas_validation_rejects_stale_or_noncanonical_candidate(stale_field):
    now = datetime.now(timezone.utc)
    base, mutation = _issued_mutation(now)
    workflow = SimpleNamespace(id=mutation.workflow_id, graph=base, updated_at=now)
    request = _request(mutation, base)
    if stale_field == "graph":
        workflow.graph = {"nodes": [_node("other", "startNode")], "edges": []}
    elif stale_field == "updated_at":
        workflow.updated_at = now + timedelta(seconds=1)
    else:
        request.nodes[0].data["title"] = "changed"

    with pytest.raises(WorkflowMutationConflict):
        WorkflowDraftCASService.validate_candidate(
            workflow=workflow,
            request=request,
            envelope=GraphMutationSafeEnvelope.from_mutation(mutation),
        )


def test_repository_persists_only_safe_envelope_and_acknowledges_idempotently():
    now = datetime.now(timezone.utc)
    _, mutation = _issued_mutation(now)
    request_row = SimpleNamespace(response_payload={})
    repository = AgentBuilderRepository()

    repository.store_envelope(
        request_row,
        GraphMutationSafeEnvelope.from_mutation(mutation),
    )
    persisted = request_row.response_payload["operation_envelopes"][0]

    assert "operations" not in persisted
    saving = repository.mark_envelope_pending_save(
        request_row,
        mutation.operation_id,
    )
    assert saving["status"] == "pending_save"
    repository.mark_envelope_saved(
        request_row,
        operation_id=mutation.operation_id,
        result_graph_hash=mutation.expected_result_graph_hash,
        workflow_updated_at=now,
    )
    first = repository.acknowledge_envelope(
        request_row,
        operation_id=mutation.operation_id,
        result_graph_hash=mutation.expected_result_graph_hash,
        workflow_updated_at=now,
    )
    second = repository.acknowledge_envelope(
        request_row,
        operation_id=mutation.operation_id,
        result_graph_hash=mutation.expected_result_graph_hash,
        workflow_updated_at=now,
    )
    assert first == second
    assert second["status"] == "acknowledged"


@pytest.mark.parametrize("status", ["blocked", "acknowledged", "reverted"])
def test_repository_rejects_save_transition_from_terminal_status(status):
    now = datetime.now(timezone.utc)
    _, mutation = _issued_mutation(now)
    request_row = SimpleNamespace(response_payload={})
    repository = AgentBuilderRepository()
    repository.store_envelope(
        request_row,
        GraphMutationSafeEnvelope.from_mutation(mutation),
    )
    envelope = repository.find_envelope(request_row, mutation.operation_id)
    repository._replace_envelope(  # noqa: SLF001
        request_row,
        mutation.operation_id,
        {**envelope, "status": status},
    )

    with pytest.raises(AgentBuilderRepositoryError, match="operation cannot be saved"):
        repository.mark_envelope_pending_save(
            request_row,
            mutation.operation_id,
        )


def test_cas_validation_rejects_blocked_operation_even_when_hashes_match():
    now = datetime.now(timezone.utc)
    base, mutation = _issued_mutation(now)
    workflow = SimpleNamespace(id=mutation.workflow_id, graph=base, updated_at=now)
    envelope = GraphMutationSafeEnvelope.from_mutation(mutation).model_copy(
        update={"status": "blocked", "blocked_reason": "operation_payload_unavailable"}
    )

    with pytest.raises(WorkflowMutationConflict, match="operation_not_applicable"):
        WorkflowDraftCASService.validate_candidate(
            workflow=workflow,
            request=_request(mutation, base),
            envelope=envelope,
        )


def test_repository_activates_pending_parameter_group_after_initial_ack():
    group_id = uuid4()
    first_task = AgentBuilderParameterTask(
        task_id=uuid4(),
        group_id=group_id,
        step_id="step_llm",
        node_id="llm",
        node_type="llmNode",
        parameter_key="model_id",
        label="LLM model",
        input_type="resource_ref",
        required=True,
        defer_policy="allow_unresolved",
        status="pending",
        task_version=1,
        stable_order=0,
        reason="model required",
        input_guidance="select a model",
    )
    second_task = first_task.model_copy(
        update={
            "task_id": uuid4(),
            "parameter_key": "prompt",
            "label": "Prompt",
            "input_type": "textarea",
            "defer_policy": "forbidden",
            "stable_order": 1,
        }
    )
    request_row = SimpleNamespace(response_payload={})
    repository = AgentBuilderRepository()
    repository.store_parameter_group(
        request_row,
        AgentBuilderParameterGroup(
            group_id=group_id,
            status="pending_ack",
            tasks=[first_task, second_task],
        ),
    )

    group = repository.activate_latest_parameter_group(request_row)

    assert group is not None
    assert group.status == "active"
    assert [task.status for task in group.tasks] == ["active", "pending"]
    assert request_row.response_payload["parameter_groups"][0]["status"] == "active"


def test_revert_validation_accepts_only_acknowledged_current_result_to_base():
    now = datetime.now(timezone.utc)
    base, mutation = _issued_mutation(now)
    result_graph = apply_graph_operations(base, mutation.operations)
    workflow = SimpleNamespace(
        id=mutation.workflow_id,
        graph=result_graph,
        updated_at=now,
    )
    envelope = GraphMutationSafeEnvelope.from_mutation(mutation).model_copy(
        update={
            "status": "acknowledged",
            "result_graph_hash": mutation.expected_result_graph_hash,
            "saved_workflow_updated_at": now,
        }
    )

    result = WorkflowDraftCASService.validate_revert_candidate(
        workflow=workflow,
        request=_revert_request(mutation, result_graph, now),
        envelope=envelope,
    )

    assert result.graph_hash == mutation.base_graph_hash
    assert result.graph["nodes"] == []


def test_revert_validation_rejects_graph_saved_after_latest_acknowledgement():
    acknowledged_at = datetime.now(timezone.utc)
    current_updated_at = acknowledged_at + timedelta(seconds=1)
    base, mutation = _issued_mutation(acknowledged_at)
    result_graph = apply_graph_operations(base, mutation.operations)
    workflow = SimpleNamespace(
        id=mutation.workflow_id,
        graph=result_graph,
        updated_at=current_updated_at,
    )
    envelope = GraphMutationSafeEnvelope.from_mutation(mutation).model_copy(
        update={
            "status": "acknowledged",
            "result_graph_hash": mutation.expected_result_graph_hash,
            "saved_workflow_updated_at": acknowledged_at,
        }
    )

    with pytest.raises(
        WorkflowMutationConflict,
        match="stale_workflow_updated_at",
    ):
        WorkflowDraftCASService.validate_revert_candidate(
            workflow=workflow,
            request=_revert_request(mutation, result_graph, current_updated_at),
            envelope=envelope,
        )


def test_redo_validation_restores_only_latest_final_graph_from_reverted_boundary():
    final_updated_at = datetime.now(timezone.utc)
    reverted_updated_at = final_updated_at + timedelta(seconds=1)
    base, mutation = _issued_mutation(final_updated_at)
    result_graph = apply_graph_operations(base, mutation.operations)
    boundary = {
        "operation_id": str(mutation.operation_id),
        "workflow_id": str(mutation.workflow_id),
        "status": "reverted",
        "pre_run_snapshot": {
            "graph_hash": mutation.base_graph_hash,
            "workflow_updated_at": mutation.expected_workflow_updated_at.isoformat(),
        },
        "latest_final_graph": {
            "graph_hash": mutation.expected_result_graph_hash,
            "workflow_updated_at": final_updated_at.isoformat(),
            "operation_id": str(mutation.operation_id),
        },
    }

    result = WorkflowDraftCASService.validate_redo_candidate(
        workflow=SimpleNamespace(
            id=mutation.workflow_id,
            graph=base,
            updated_at=reverted_updated_at,
        ),
        request=_redo_request(
            mutation,
            result_graph,
            mutation.base_graph_hash,
            reverted_updated_at,
        ),
        boundary=boundary,
    )

    assert result.operation_id == mutation.operation_id
    assert result.graph_hash == mutation.expected_result_graph_hash


def test_revert_hash_uses_schema_normalized_existing_base_graph():
    now = datetime.now(timezone.utc)
    workflow_id = uuid4()
    base = {
        "nodes": [
            _node("start", "startNode"),
            _node("llm", "llmNode"),
            _node("answer", "answerNode"),
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "llm"},
            {"id": "e2", "source": "llm", "target": "answer"},
        ],
    }
    mutation = GraphMutationBuilder().build(
        operation_id=uuid4(),
        kind="graph_edit",
        generation_mode="structure_only",
        workflow_id=workflow_id,
        base_graph=base,
        expected_workflow_updated_at=now,
        operations=[
            {"op": "remove_edge", "edge_id": "e2"},
            {"op": "add_node", "node": _node("template", "templateNode")},
            {
                "op": "add_edge",
                "edge": {"id": "e3", "source": "llm", "target": "template"},
            },
            {
                "op": "add_edge",
                "edge": {"id": "e4", "source": "template", "target": "answer"},
            },
        ],
    )
    applied = WorkflowDraftCASService.validate_candidate(
        workflow=SimpleNamespace(id=workflow_id, graph=base, updated_at=now),
        request=_request(mutation, base),
        envelope=GraphMutationSafeEnvelope.from_mutation(mutation),
    )
    envelope = GraphMutationSafeEnvelope.from_mutation(mutation).model_copy(
        update={
            "status": "acknowledged",
            "result_graph_hash": applied.graph_hash,
            "saved_workflow_updated_at": now,
        }
    )
    request = _draft_request(
        {
            **base,
            "mutation_context": {
                "operation_id": str(mutation.operation_id),
                "action": "revert",
                "expected_base_graph_hash": applied.graph_hash,
                "expected_workflow_updated_at": now,
                "catalog_version": 3,
            },
        }
    )

    reverted = WorkflowDraftCASService.validate_revert_candidate(
        workflow=SimpleNamespace(
            id=workflow_id,
            graph=applied.graph,
            updated_at=now,
        ),
        request=request,
        envelope=envelope,
    )

    assert reverted.graph_hash == mutation.base_graph_hash


def test_repository_marks_acknowledged_envelope_reverted_idempotently():
    now = datetime.now(timezone.utc)
    _, mutation = _issued_mutation(now)
    request_row = SimpleNamespace(response_payload={})
    repository = AgentBuilderRepository()
    repository.store_envelope(
        request_row, GraphMutationSafeEnvelope.from_mutation(mutation)
    )
    repository.mark_envelope_pending_save(request_row, mutation.operation_id)
    repository.mark_envelope_saved(
        request_row,
        operation_id=mutation.operation_id,
        result_graph_hash=mutation.expected_result_graph_hash,
        workflow_updated_at=now,
    )
    repository.acknowledge_envelope(
        request_row,
        operation_id=mutation.operation_id,
        result_graph_hash=mutation.expected_result_graph_hash,
        workflow_updated_at=now,
    )

    first = repository.mark_envelope_reverted(request_row, mutation.operation_id)
    second = repository.mark_envelope_reverted(request_row, mutation.operation_id)

    assert first == second
    assert second["status"] == "reverted"


@pytest.mark.parametrize("kind", ["initial_graph", "replace_workflow"])
def test_structural_graph_revert_cancels_parameter_group(kind):
    group_id = uuid4()
    task = AgentBuilderParameterTask(
        task_id=uuid4(),
        group_id=group_id,
        step_id="step_llm",
        node_id="llm",
        node_type="llmNode",
        parameter_key="model_id",
        label="LLM model",
        input_type="resource_ref",
        required=True,
        defer_policy="allow_unresolved",
        status="active",
        task_version=1,
        stable_order=0,
        reason="model required",
        input_guidance="select a model",
    )
    request_row = SimpleNamespace(response_payload={})
    repository = AgentBuilderRepository()
    repository.store_parameter_group(
        request_row,
        AgentBuilderParameterGroup(
            group_id=group_id,
            status="active",
            tasks=[task],
        ),
    )

    group = repository.revert_completion_state(
        request_row,
        {"kind": kind, "completion_context": None},
    )

    assert group is not None
    assert group.status == "canceled"
    assert group.tasks[0].status == "canceled"
