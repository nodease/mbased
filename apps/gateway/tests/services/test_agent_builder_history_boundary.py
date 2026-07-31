from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from apps.gateway.adapters.db.agent_builder_repository import AgentBuilderRepository
from apps.gateway.services.agent_builder import mutation_lifecycle as lifecycle_module
from apps.gateway.services.agent_builder.mutation_lifecycle import (
    GraphMutationLifecycleService,
)
from apps.gateway.application.agent_builder.graph_mutation_builder import (
    GraphMutationBuilder,
    apply_graph_operations,
    canonical_graph_hash,
)
from apps.gateway.application.agent_builder.workflow_cas import (
    WorkflowDraftCASService,
    WorkflowMutationConflict,
)
from apps.shared.schemas.agent_builder import (
    AgentBuilderParameterGroup,
    AgentBuilderParameterTask,
    GraphMutationCompletionContext,
    GraphMutationSafeEnvelope,
)
from apps.shared.schemas.workflow import WorkflowDraftRequest
from apps.shared.db.models.agent_builder import AgentBuilderRequest, AgentBuilderSession
from apps.shared.db.models.workflow import Workflow


class _Query:
    def __init__(self, *, first=None, all_rows=None):
        self._first = first
        self._all = list(all_rows or [])

    def filter(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def with_for_update(self):
        return self

    def first(self):
        return self._first

    def all(self):
        return list(self._all)


class _Db:
    def __init__(self, rows):
        self.rows = rows

    def query(self, model):
        value = self.rows[model]
        return _Query(
            first=value if not isinstance(value, list) else None,
            all_rows=value if isinstance(value, list) else None,
        )


def _node(node_id: str, node_type: str, data: dict | None = None) -> dict:
    return {
        "id": node_id,
        "type": node_type,
        "position": {"x": 0, "y": 0},
        "data": {"title": node_id, **(data or {})},
    }


def _draft_request(payload):
    data = dict(payload)
    context = data.get("mutation_context")
    if isinstance(context, dict):
        data.setdefault("expected_graph_hash", context["expected_base_graph_hash"])
        data.setdefault("expected_updated_at", context["expected_workflow_updated_at"])
    return WorkflowDraftRequest.model_validate(data)


def _root_mutation(now: datetime):
    workflow_id = uuid4()
    base = {
        "nodes": [_node("existing", "startNode")],
        "edges": [],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }
    mutation = GraphMutationBuilder().build(
        operation_id=uuid4(),
        kind="replace_workflow",
        generation_mode="configure_and_generate",
        workflow_id=workflow_id,
        base_graph=base,
        expected_workflow_updated_at=now,
        operations=[
            {"op": "remove_node", "node_id": "existing"},
            {"op": "add_node", "node": _node("start", "startNode")},
            {
                "op": "add_node",
                "node": _node("answer", "answerNode", {"answer": "initial"}),
            },
            {
                "op": "add_edge",
                "edge": {"id": "e1", "source": "start", "target": "answer"},
            },
        ],
    )
    return base, mutation


def _persist_and_acknowledge(
    repository: AgentBuilderRepository,
    request_row,
    mutation,
    saved_at: datetime,
) -> dict:
    repository.store_envelope(
        request_row,
        GraphMutationSafeEnvelope.from_mutation(mutation),
    )
    repository.mark_envelope_pending_save(request_row, mutation.operation_id)
    repository.mark_envelope_saved(
        request_row,
        operation_id=mutation.operation_id,
        result_graph_hash=mutation.expected_result_graph_hash,
        workflow_updated_at=saved_at,
    )
    return repository.acknowledge_envelope(
        request_row,
        operation_id=mutation.operation_id,
        result_graph_hash=mutation.expected_result_graph_hash,
        workflow_updated_at=saved_at,
    )


def _task(
    *,
    group_id,
    stable_order: int,
    status: str,
    parameter_key: str,
) -> AgentBuilderParameterTask:
    return AgentBuilderParameterTask(
        task_id=uuid4(),
        group_id=group_id,
        step_id=f"step-{stable_order}",
        node_id=f"node-{stable_order}",
        node_type="answerNode",
        parameter_key=parameter_key,
        label=parameter_key,
        input_type="text",
        required=True,
        status=status,
        task_version=stable_order + 1,
        stable_order=stable_order,
        reason="safe reason",
        input_guidance="safe guidance",
    )


def test_history_boundary_persists_safe_pre_run_metadata_and_tracks_latest_final_ack():
    now = datetime(2026, 7, 13, tzinfo=timezone.utc)
    parameter_saved_at = now + timedelta(seconds=2)
    base, root = _root_mutation(now)
    root_result = apply_graph_operations(base, root.operations)
    request_row = SimpleNamespace(response_payload={})
    repository = AgentBuilderRepository()

    _persist_and_acknowledge(repository, request_row, root, now + timedelta(seconds=1))
    parameter = GraphMutationBuilder().build(
        operation_id=uuid4(),
        kind="parameter_update",
        generation_mode="configure_and_generate",
        workflow_id=root.workflow_id,
        base_graph=root_result,
        expected_workflow_updated_at=now + timedelta(seconds=1),
        operations=[
            {
                "op": "replace_node_data",
                "node_id": "answer",
                "data": {"title": "answer", "answer": "changed"},
            }
        ],
        completion_context=GraphMutationCompletionContext(
            parameter_task_id=uuid4()
        ),
    )
    _persist_and_acknowledge(
        repository,
        request_row,
        parameter,
        parameter_saved_at,
    )
    repository.acknowledge_envelope(
        request_row,
        operation_id=root.operation_id,
        result_graph_hash=root.expected_result_graph_hash,
        workflow_updated_at=now + timedelta(seconds=1),
    )

    boundary = repository.load_history_boundary(request_row)
    effective_root = repository.history_boundary_envelope(
        request_row,
        root.operation_id,
    )

    assert boundary is not None
    assert boundary["operation_id"] == str(root.operation_id)
    assert boundary["pre_run_snapshot"] == {
        "graph_hash": canonical_graph_hash(base),
        "workflow_updated_at": now.isoformat(),
    }
    assert boundary["latest_final_graph"] == {
        "graph_hash": parameter.expected_result_graph_hash,
        "workflow_updated_at": parameter_saved_at.isoformat(),
        "operation_id": str(parameter.operation_id),
    }
    assert boundary["member_operation_ids"] == [
        str(root.operation_id),
        str(parameter.operation_id),
    ]
    assert effective_root["operation_id"] == str(root.operation_id)
    assert effective_root["result_graph_hash"] == parameter.expected_result_graph_hash
    assert effective_root["saved_workflow_updated_at"] == parameter_saved_at.isoformat()
    assert "operations" not in boundary
    assert "nodes" not in boundary
    assert "edges" not in boundary
    assert "parameter_value" not in boundary
    assert "credential" not in boundary
    assert "secret" not in boundary


def test_duplicate_acknowledgement_repairs_without_advancing_boundary_twice():
    now = datetime(2026, 7, 13, tzinfo=timezone.utc)
    base, root = _root_mutation(now)
    request_row = SimpleNamespace(response_payload={})
    repository = AgentBuilderRepository()
    first = _persist_and_acknowledge(repository, request_row, root, now)
    first_boundary = repository.load_history_boundary(request_row)

    second = repository.acknowledge_envelope(
        request_row,
        operation_id=root.operation_id,
        result_graph_hash=root.expected_result_graph_hash,
        workflow_updated_at=now,
    )

    assert second == first
    assert repository.load_history_boundary(request_row) == first_boundary
    assert canonical_graph_hash(base) == first_boundary["pre_run_snapshot"]["graph_hash"]


def test_completion_gate_waits_for_required_knowledge_and_its_canonical_ack():
    now = datetime(2026, 7, 13, tzinfo=timezone.utc)
    root_saved_at = now + timedelta(seconds=1)
    knowledge_saved_at = now + timedelta(seconds=2)
    base, root = _root_mutation(now)
    root_graph = apply_graph_operations(base, root.operations)
    request_row = SimpleNamespace(
        response_payload={},
        status="graph_mutation_ready",
    )
    repository = AgentBuilderRepository()
    _persist_and_acknowledge(repository, request_row, root, root_saved_at)

    payload = repository._payload(request_row)  # noqa: SLF001
    payload["clarification_options"] = [
        {
            "type": "knowledge_base",
            "candidate_id": "safe-candidate",
            "resolution_id": "kb-resolution",
            "requirement_id": "kr-1",
        }
    ]
    request_row.response_payload = payload
    repository.synchronize_history_boundary_state(request_row)

    assert repository.load_history_boundary(request_row)["status"] == "active"
    assert request_row.status != "completed"

    knowledge = GraphMutationBuilder().build(
        operation_id=uuid4(),
        kind="knowledge_binding",
        generation_mode="configure_and_generate",
        workflow_id=root.workflow_id,
        base_graph=root_graph,
        expected_workflow_updated_at=root_saved_at,
        operations=[
            {
                "op": "replace_node_data",
                "node_id": "answer",
                "data": {
                    "title": "answer",
                    "answer": "initial",
                    "knowledgeBases": [{"id": "safe-kb-ref"}],
                },
            }
        ],
        completion_context=GraphMutationCompletionContext(
            knowledge_resolution_id="kb-resolution"
        ),
    )
    repository.store_envelope(
        request_row,
        GraphMutationSafeEnvelope.from_mutation(knowledge),
    )
    repository.store_knowledge_resolution(
        request_row,
        resolution_id="kb-resolution",
        operation_id=knowledge.operation_id,
        timing="after_graph",
        selected_candidate_ids=["safe-candidate"],
    )
    repository.mark_envelope_pending_save(request_row, knowledge.operation_id)
    repository.mark_envelope_saved(
        request_row,
        operation_id=knowledge.operation_id,
        result_graph_hash=knowledge.expected_result_graph_hash,
        workflow_updated_at=knowledge_saved_at,
    )

    assert repository.load_history_boundary(request_row)["status"] == "pending_ack"

    repository.acknowledge_envelope(
        request_row,
        operation_id=knowledge.operation_id,
        result_graph_hash=knowledge.expected_result_graph_hash,
        workflow_updated_at=knowledge_saved_at,
    )
    assert repository.load_history_boundary(request_row)["status"] == "pending_ack"

    repository.acknowledge_knowledge_resolution(
        request_row,
        resolution_id="kb-resolution",
        operation_id=knowledge.operation_id,
    )

    boundary = repository.load_history_boundary(request_row)
    assert boundary["status"] == "completed"
    assert boundary["latest_final_graph"] == {
        "graph_hash": knowledge.expected_result_graph_hash,
        "workflow_updated_at": knowledge_saved_at.isoformat(),
        "operation_id": str(knowledge.operation_id),
    }
    assert request_row.status == "completed"


def test_blocked_graph_operation_never_exposes_completed_boundary():
    now = datetime(2026, 7, 13, tzinfo=timezone.utc)
    _base, root = _root_mutation(now)
    request_row = SimpleNamespace(
        response_payload={},
        status="graph_mutation_ready",
    )
    repository = AgentBuilderRepository()
    repository.store_envelope(
        request_row,
        GraphMutationSafeEnvelope.from_mutation(root),
    )

    repository.mark_envelope_blocked(
        request_row,
        root.operation_id,
        reason="operation_payload_unavailable",
    )

    assert repository.load_history_boundary(request_row)["status"] == "blocked"
    assert request_row.status != "completed"


def test_direct_knowledge_candidates_block_completion_until_resolution_ack():
    now = datetime(2026, 7, 14, tzinfo=timezone.utc)
    base, root = _root_mutation(now)
    request_row = SimpleNamespace(
        response_payload={
            "knowledge_resolution": {
                "timing": "after_graph",
                "required": True,
                "candidates": [
                    {
                        "candidate_id": "safe-candidate",
                        "resolution_id": "direct-kb-resolution",
                        "requirement_id": "kr-direct",
                    }
                ],
                "selected": [],
            },
            "clarification_options": [],
        },
        status="graph_mutation_ready",
    )
    repository = AgentBuilderRepository()
    _persist_and_acknowledge(repository, request_row, root, now)

    repository.synchronize_history_boundary_state(request_row)

    assert repository.load_history_boundary(request_row)["status"] == "active"
    assert request_row.status != "completed"


def test_direct_knowledge_without_candidates_uses_top_level_resolution_as_pending():
    now = datetime(2026, 7, 14, tzinfo=timezone.utc)
    _base, root = _root_mutation(now)
    request_row = SimpleNamespace(
        response_payload={
            "knowledge_resolution": {
                "resolution_id": "direct-kb-empty-resolution",
                "timing": "before_graph",
                "required": True,
                "candidates": [],
                "selected": [],
            },
            "clarification_options": [],
        },
        status="graph_mutation_ready",
    )
    repository = AgentBuilderRepository()
    _persist_and_acknowledge(repository, request_row, root, now)

    repository.synchronize_history_boundary_state(request_row)

    assert repository.load_history_boundary(request_row)["status"] == "active"
    assert request_row.status != "completed"


def test_last_task_reopen_is_read_only_and_preserves_completed_state():
    group_id = uuid4()
    group = AgentBuilderParameterGroup(
        group_id=group_id,
        status="completed",
        tasks=[
            _task(
                group_id=group_id,
                stable_order=0,
                status="completed",
                parameter_key="first",
            ),
            _task(
                group_id=group_id,
                stable_order=1,
                status="completed",
                parameter_key="last",
            ),
        ],
    )
    request_row = SimpleNamespace(response_payload={})
    repository = AgentBuilderRepository()
    repository.store_parameter_group(request_row, group)
    before = request_row.response_payload

    reopened = repository.last_reopenable_parameter_task(request_row)

    assert reopened is not None
    assert reopened.parameter_key == "last"
    assert reopened.status == "completed"
    assert request_row.response_payload == before


def test_last_task_reopen_uses_stable_order_without_sensitivity_or_node_filter():
    group_id = uuid4()
    group = AgentBuilderParameterGroup(
        group_id=group_id,
        status="completed",
        tasks=[
            _task(
                group_id=group_id,
                stable_order=0,
                status="completed",
                parameter_key="safe_text",
            ),
            _task(
                group_id=group_id,
                stable_order=2,
                status="completed",
                parameter_key="credential_id",
            ).model_copy(
                update={
                    "node_type": "mailNode",
                    "input_type": "credential_ref",
                    "sensitivity": "secret_forbidden",
                }
            ),
            _task(
                group_id=group_id,
                stable_order=1,
                status="deferred",
                parameter_key="middle",
            ),
        ],
    )
    request_row = SimpleNamespace(response_payload={})
    repository = AgentBuilderRepository()
    repository.store_parameter_group(request_row, group)

    reopened = repository.last_reopenable_parameter_task(request_row)

    assert reopened is not None
    assert reopened.parameter_key == "credential_id"
    assert reopened.stable_order == 2


def test_lifecycle_prepares_read_only_reopen_and_whole_restore_plan(monkeypatch):
    now = datetime(2026, 7, 13, tzinfo=timezone.utc)
    saved_at = now + timedelta(seconds=1)
    base, root = _root_mutation(now)
    final_graph = apply_graph_operations(base, root.operations)
    repository = AgentBuilderRepository()
    request_row = SimpleNamespace(response_payload={})
    _persist_and_acknowledge(repository, request_row, root, saved_at)
    group_id = uuid4()
    repository.store_parameter_group(
        request_row,
        AgentBuilderParameterGroup(
            group_id=group_id,
            status="completed",
            tasks=[
                _task(
                    group_id=group_id,
                    stable_order=0,
                    status="completed",
                    parameter_key="answer",
                )
            ],
        ),
    )
    user_id = uuid4()
    organization_id = uuid4()
    session = SimpleNamespace(
        id=uuid4(),
        user_id=user_id,
        organization_id=organization_id,
        workflow_id=root.workflow_id,
    )
    workflow = SimpleNamespace(
        id=root.workflow_id,
        organization_id=organization_id,
        graph=final_graph,
        updated_at=saved_at,
    )
    service = GraphMutationLifecycleService(
        _Db(
            {
                AgentBuilderSession: session,
                Workflow: workflow,
                AgentBuilderRequest: [request_row],
            }
        ),
        user_id=user_id,
        organization_id=organization_id,
        repository=repository,
    )
    monkeypatch.setattr(
        lifecycle_module,
        "has_workflow_permission",
        lambda *_args, **_kwargs: True,
    )
    before = request_row.response_payload

    reopened = service.reopen_last_parameter_task(session.id)
    restore = service.prepare_history_boundary_restore(session.id)

    assert reopened is not None
    assert reopened.status == "completed"
    assert request_row.response_payload == before
    assert restore.operation_id == root.operation_id
    assert restore.pre_run_graph_hash == canonical_graph_hash(base)
    assert restore.final_graph_hash == root.expected_result_graph_hash
    assert restore.expected_workflow_updated_at == saved_at


def test_lifecycle_restore_plan_rejects_unacknowledged_boundary(monkeypatch):
    now = datetime(2026, 7, 13, tzinfo=timezone.utc)
    base, root = _root_mutation(now)
    repository = AgentBuilderRepository()
    request_row = SimpleNamespace(response_payload={})
    repository.store_envelope(
        request_row,
        GraphMutationSafeEnvelope.from_mutation(root),
    )
    user_id = uuid4()
    organization_id = uuid4()
    session = SimpleNamespace(
        id=uuid4(),
        user_id=user_id,
        organization_id=organization_id,
        workflow_id=root.workflow_id,
    )
    workflow = SimpleNamespace(
        id=root.workflow_id,
        organization_id=organization_id,
        graph=base,
        updated_at=now,
    )
    service = GraphMutationLifecycleService(
        _Db(
            {
                AgentBuilderSession: session,
                Workflow: workflow,
                AgentBuilderRequest: [request_row],
            }
        ),
        user_id=user_id,
        organization_id=organization_id,
        repository=repository,
    )
    monkeypatch.setattr(
        lifecycle_module,
        "has_workflow_permission",
        lambda *_args, **_kwargs: True,
    )

    with pytest.raises(HTTPException) as exc:
        service.prepare_history_boundary_restore(session.id)

    assert exc.value.status_code == 409
    assert exc.value.detail == "acknowledgement_required"


def test_lifecycle_restore_plan_rejects_acknowledged_but_incomplete_setup(
    monkeypatch,
):
    now = datetime(2026, 7, 13, tzinfo=timezone.utc)
    saved_at = now + timedelta(seconds=1)
    base, root = _root_mutation(now)
    final_graph = apply_graph_operations(base, root.operations)
    repository = AgentBuilderRepository()
    request_row = SimpleNamespace(
        response_payload={},
        status="graph_mutation_ready",
    )
    _persist_and_acknowledge(repository, request_row, root, saved_at)
    group_id = uuid4()
    repository.store_parameter_group(
        request_row,
        AgentBuilderParameterGroup(
            group_id=group_id,
            status="active",
            tasks=[
                _task(
                    group_id=group_id,
                    stable_order=0,
                    status="active",
                    parameter_key="answer",
                )
            ],
        ),
    )
    user_id = uuid4()
    organization_id = uuid4()
    session = SimpleNamespace(
        id=uuid4(),
        user_id=user_id,
        organization_id=organization_id,
        workflow_id=root.workflow_id,
    )
    workflow = SimpleNamespace(
        id=root.workflow_id,
        organization_id=organization_id,
        graph=final_graph,
        updated_at=saved_at,
    )
    service = GraphMutationLifecycleService(
        _Db(
            {
                AgentBuilderSession: session,
                Workflow: workflow,
                AgentBuilderRequest: [request_row],
            }
        ),
        user_id=user_id,
        organization_id=organization_id,
        repository=repository,
    )
    monkeypatch.setattr(
        lifecycle_module,
        "has_workflow_permission",
        lambda *_args, **_kwargs: True,
    )

    with pytest.raises(HTTPException) as exc:
        service.prepare_history_boundary_restore(session.id)

    assert repository.load_history_boundary(request_row)["status"] == "active"
    assert exc.value.status_code == 409
    assert exc.value.detail == "history_boundary_incomplete"


def test_whole_operation_revert_cancels_all_parameter_and_knowledge_flows():
    now = datetime(2026, 7, 13, tzinfo=timezone.utc)
    _, root = _root_mutation(now)
    request_row = SimpleNamespace(response_payload={})
    repository = AgentBuilderRepository()
    acknowledged_root = _persist_and_acknowledge(
        repository,
        request_row,
        root,
        now + timedelta(seconds=1),
    )
    group_ids = [uuid4(), uuid4()]
    for index, group_id in enumerate(group_ids):
        repository.store_parameter_group(
            request_row,
            AgentBuilderParameterGroup(
                group_id=group_id,
                status="active" if index else "completed",
                tasks=[
                    _task(
                        group_id=group_id,
                        stable_order=index,
                        status="active" if index else "completed",
                        parameter_key=f"parameter-{index}",
                    )
                ],
            ),
        )
    payload = repository._payload(request_row)  # noqa: SLF001
    payload.update(
        {
            "knowledge_resolutions": [
                {"resolution_id": "kb-1", "status": "completed"},
                {"resolution_id": "kb-2", "status": "pending_ack"},
            ],
            "clarification_options": [{"resolution_id": "kb-2"}],
            "clarification_questions": ["safe question"],
            "pending_task_decisions": [
                {"operation_id": str(uuid4()), "task_id": str(uuid4())}
            ],
        }
    )
    request_row.response_payload = payload

    repository.mark_envelope_reverted(request_row, root.operation_id)
    returned_group = repository.revert_completion_state(
        request_row,
        acknowledged_root,
    )

    groups = request_row.response_payload["parameter_groups"]
    assert returned_group is not None
    assert all(group["status"] == "canceled" for group in groups)
    assert all(
        task["status"] == "canceled"
        for group in groups
        for task in group["tasks"]
    )
    assert all(
        resolution["status"] == "canceled"
        for resolution in request_row.response_payload["knowledge_resolutions"]
    )
    assert request_row.response_payload["clarification_options"] == []
    assert request_row.response_payload["clarification_questions"] == []
    assert request_row.response_payload["pending_task_decisions"] == []
    assert repository.load_history_boundary(request_row)["status"] == "reverted"


def test_parameter_and_knowledge_operations_cannot_be_individually_reverted():
    now = datetime(2026, 7, 13, tzinfo=timezone.utc)
    base = {
        "nodes": [_node("answer", "answerNode", {"answer": "before"})],
        "edges": [],
    }
    mutation = GraphMutationBuilder().build(
        operation_id=uuid4(),
        kind="parameter_update",
        generation_mode="configure_and_generate",
        workflow_id=uuid4(),
        base_graph=base,
        expected_workflow_updated_at=now,
        operations=[
            {
                "op": "replace_node_data",
                "node_id": "answer",
                "data": {"title": "answer", "answer": "after"},
            }
        ],
    )
    result_graph = apply_graph_operations(base, mutation.operations)
    envelope = GraphMutationSafeEnvelope.from_mutation(mutation).model_copy(
        update={
            "status": "acknowledged",
            "result_graph_hash": mutation.expected_result_graph_hash,
            "saved_workflow_updated_at": now,
        }
    )
    request = _draft_request(
        {
            **base,
            "mutation_context": {
                "operation_id": mutation.operation_id,
                "action": "revert",
                "expected_base_graph_hash": mutation.expected_result_graph_hash,
                "expected_workflow_updated_at": now,
                "catalog_version": 3,
            },
        }
    )

    with pytest.raises(
        WorkflowMutationConflict,
        match="operation_not_history_boundary",
    ):
        WorkflowDraftCASService.validate_revert_candidate(
            workflow=SimpleNamespace(
                id=mutation.workflow_id,
                graph=result_graph,
                updated_at=now,
            ),
            request=request,
            envelope=envelope,
        )


@pytest.mark.parametrize("kind", ["parameter_update", "knowledge_binding"])
def test_repository_disables_operation_scoped_completion_revert(kind):
    repository = AgentBuilderRepository()
    request_row = SimpleNamespace(response_payload={})

    with pytest.raises(
        ValueError,
        match="operation is not history boundary",
    ):
        repository.revert_completion_state(
            request_row,
            {
                "operation_id": str(uuid4()),
                "kind": kind,
                "affected_node_ids": [],
                "completion_context": {},
            },
        )


def test_redo_validation_restores_only_latest_final_graph_from_pre_run_state():
    now = datetime(2026, 7, 13, tzinfo=timezone.utc)
    reverted_at = now + timedelta(seconds=3)
    base, root = _root_mutation(now)
    final_graph = apply_graph_operations(base, root.operations)
    request_row = SimpleNamespace(response_payload={})
    repository = AgentBuilderRepository()
    acknowledged = _persist_and_acknowledge(
        repository,
        request_row,
        root,
        now + timedelta(seconds=1),
    )
    repository.mark_envelope_reverted(request_row, root.operation_id)
    repository.revert_completion_state(request_row, acknowledged)
    boundary = repository.load_history_boundary(request_row)
    request = _draft_request(
        {
            **final_graph,
            "mutation_context": {
                "operation_id": root.operation_id,
                "action": "apply",
                "expected_base_graph_hash": canonical_graph_hash(base),
                "expected_workflow_updated_at": reverted_at,
                "catalog_version": 3,
            },
        }
    )
    request.mutation_context = request.mutation_context.model_copy(
        update={"action": "redo"}
    )

    validated = WorkflowDraftCASService.validate_redo_candidate(
        workflow=SimpleNamespace(
            id=root.workflow_id,
            graph=base,
            updated_at=reverted_at,
        ),
        request=request,
        boundary=boundary,
    )

    assert validated.operation_id == root.operation_id
    assert validated.graph_hash == root.expected_result_graph_hash
    assert repository.load_history_boundary(request_row)["status"] == "reverted"
