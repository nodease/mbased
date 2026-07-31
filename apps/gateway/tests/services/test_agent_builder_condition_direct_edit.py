from copy import deepcopy
from datetime import datetime, timezone
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from apps.gateway.adapters.db.agent_builder_repository import AgentBuilderRepository
from apps.gateway.services.agent_builder import parameter_task_service as task_service_module
from apps.gateway.services.agent_builder import mutation_lifecycle as lifecycle_module
from apps.gateway.application.agent_builder.graph_mutation_builder import (
    apply_graph_operations,
)
from apps.gateway.application.agent_builder.condition_branches import (
    condition_branch_decision_issues,
    reconcile_condition_branch_tasks,
)
from apps.gateway.services.agent_builder.parameter_task_service import (
    ParameterTaskService,
)
from apps.gateway.services.agent_builder.mutation_lifecycle import (
    GraphMutationLifecycleService,
)
from apps.gateway.application.agent_builder.service import DirectEditOrchestrator
from apps.gateway.services.agent_builder_service import AgentBuilderService
from apps.shared.schemas.agent_builder import (
    AgentBuilderParameterGroup,
    AgentBuilderParameterTask,
    AgentBuilderParameterTaskDecisionRequest,
    AgentBuilderStructuredRequest,
    GraphMutationAcknowledgementRequest,
)
from apps.shared.services.workflow_node_catalog import (
    derive_node_configuration_state,
    validate_workflow_graph_connections,
)


CONDITION_BRANCH_PREFIX = "condition_branch:"
NO_CONNECTION = "__agent_builder_no_connection__"


def _node(node_id: str, node_type: str, data: dict | None = None) -> dict:
    return {
        "id": node_id,
        "type": node_type,
        "position": {"x": 0, "y": 0},
        "data": {"title": node_id, **(data or {})},
    }


def _edge(
    edge_id: str,
    source: str,
    target: str,
    *,
    source_handle: str | None = None,
) -> dict:
    edge = {"id": edge_id, "source": source, "target": target}
    if source_handle is not None:
        edge["sourceHandle"] = source_handle
    return edge


def _structured_condition_request() -> AgentBuilderStructuredRequest:
    return AgentBuilderStructuredRequest.model_validate(
        {
            "request_type": "new_workflow",
            "draft_mode": "new_workflow",
            "intent_summary": "Route requests by condition",
            "planned_steps": [
                {
                    "step_id": "step-input",
                    "capability": "start_input",
                    "purpose": "Receive input",
                },
                {
                    "step_id": "step-condition",
                    "capability": "condition",
                    "purpose": "Choose the branch",
                    "depends_on": ["step-input"],
                },
            ],
            "required_capabilities": ["start_input", "condition"],
        }
    )


def _branch_task(
    *,
    group_id,
    handle: str,
    status: str = "active",
    task_version: int = 1,
    stable_order: int = 0,
) -> AgentBuilderParameterTask:
    label = "Default branch" if handle == "default" else f"Case {handle}"
    return AgentBuilderParameterTask(
        task_id=uuid4(),
        group_id=group_id,
        step_id="step-condition",
        node_id="condition",
        node_type="conditionNode",
        parameter_key=f"{CONDITION_BRANCH_PREFIX}{handle}",
        label=label,
        input_type="select",
        required=True,
        status=status,
        task_version=task_version,
        stable_order=stable_order,
        reason="Select the branch target.",
        input_guidance="Select an existing node or no connection.",
        validation={
            "condition_branch_handle": handle,
            "options": ["target", NO_CONNECTION],
        },
    )


def _service_env(monkeypatch, graph: dict, group: AgentBuilderParameterGroup):
    repository = AgentBuilderRepository()
    request_row = SimpleNamespace(response_payload={})
    repository.store_parameter_group(request_row, group)
    user_id = uuid4()
    organization_id = uuid4()
    workflow_id = uuid4()
    session = SimpleNamespace(
        id=uuid4(),
        user_id=user_id,
        organization_id=organization_id,
        workflow_id=workflow_id,
    )
    workflow = SimpleNamespace(
        id=workflow_id,
        organization_id=organization_id,
        graph=deepcopy(graph),
        updated_at=datetime.now(timezone.utc),
    )

    class _Query:
        def __init__(self, model, *, first=None, all_rows=None):
            self._model = model
            self._first = first
            self._all = list(all_rows or [])

        def filter(self, *_args):
            return self

        def order_by(self, *_args):
            return self

        def with_for_update(self):
            return self

        def first(self):
            return self._first

        def all(self):
            return list(self._all)

    class _Db:
        def __init__(self):
            self.commits = 0

        def query(self, model):
            if model is task_service_module.AgentBuilderSession:
                return _Query(model, first=session)
            if model is task_service_module.Workflow:
                return _Query(model, first=workflow)
            if model is task_service_module.AgentBuilderRequest:
                return _Query(model, all_rows=[request_row])
            raise AssertionError(f"unexpected model: {model}")

        def commit(self):
            self.commits += 1

    db = _Db()
    audit_calls = []
    monkeypatch.setattr(
        task_service_module,
        "has_workflow_permission",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        task_service_module,
        "add_action_audit",
        lambda *_args, **_kwargs: audit_calls.append((_args, _kwargs)),
    )
    service = ParameterTaskService(
        db,
        user_id=user_id,
        organization_id=organization_id,
        repository=repository,
    )
    return service, session, workflow, request_row, db, audit_calls


def _condition_node(graph: dict) -> dict:
    return next(node for node in graph["nodes"] if node["id"] == "condition")


def test_direct_edit_creates_condition_branch_tasks_and_keeps_condition_unresolved():
    workflow = SimpleNamespace(
        id=uuid4(),
        graph={"nodes": [], "edges": []},
        updated_at=datetime.now(timezone.utc),
    )
    candidate_graph = {
        "nodes": [
            _node("start", "startNode"),
            _node(
                "condition",
                "conditionNode",
                {
                    "cases": [
                        {"id": "approved", "label": "Approved"},
                        {"id": "rejected", "label": "Rejected"},
                    ],
                },
            ),
        ],
        "edges": [_edge("edge-start-condition", "start", "condition")],
    }

    result = DirectEditOrchestrator().issue(
        workflow=workflow,
        structured_request=_structured_condition_request(),
        candidate_graph=candidate_graph,
        generation_mode="configure_and_generate",
    )

    assert result.parameter_group is not None
    branch_tasks = [
        task
        for task in result.parameter_group.tasks
        if task.parameter_key.startswith(CONDITION_BRANCH_PREFIX)
    ]
    assert [task.parameter_key for task in branch_tasks] == [
        "condition_branch:approved",
        "condition_branch:rejected",
        "condition_branch:default",
    ]
    assert {task.input_type for task in branch_tasks} == {"select"}
    assert all(task.status == "pending" for task in branch_tasks)
    assert all(NO_CONNECTION in task.validation["options"] for task in branch_tasks)

    serialized_tasks = json.dumps(
        [task.model_dump(mode="json") for task in branch_tasks],
        sort_keys=True,
    )
    assert '"nodes"' not in serialized_tasks
    assert '"edges"' not in serialized_tasks

    graph_after_mutation = apply_graph_operations(
        workflow.graph,
        result.mutation.operations,
    )
    condition_data = _condition_node(graph_after_mutation)["data"]
    assert derive_node_configuration_state("conditionNode", condition_data) == (
        "unresolved"
    )
    assert condition_data["configuration_state"] == "unresolved"
    assert condition_data["_deferred_parameters"] == ["cases"]
    assert not [
        edge
        for edge in graph_after_mutation["edges"]
        if edge.get("source") == "condition"
    ]


def test_preview_graph_preserves_condition_downstream_with_explicit_default_handle():
    service = AgentBuilderService(
        SimpleNamespace(),
        user=SimpleNamespace(id=uuid4()),
        organization_id=uuid4(),
    )
    structured = AgentBuilderStructuredRequest.model_validate(
        {
            "request_type": "new_workflow",
            "draft_mode": "new_workflow",
            "intent_summary": "Route requests by condition and answer",
            "planned_steps": [
                {
                    "step_id": "step-input",
                    "capability": "start_input",
                    "purpose": "Receive input",
                },
                {
                    "step_id": "step-condition",
                    "capability": "condition",
                    "purpose": "Choose branch",
                    "depends_on": ["step-input"],
                },
                {
                    "step_id": "step-answer",
                    "capability": "answer",
                    "purpose": "Return answer",
                    "depends_on": ["step-condition"],
                },
            ],
            "required_capabilities": ["start_input", "condition", "answer"],
        }
    )

    preview_graph = service._build_preview_graph(
        structured,
        workflow=None,
        kb_bindings=[],
    )
    condition_node = next(
        node for node in preview_graph["nodes"] if node.get("type") == "conditionNode"
    )
    answer_node = next(
        node for node in preview_graph["nodes"] if node.get("type") == "answerNode"
    )
    assert any(
        edge.get("source") == condition_node["id"]
        and edge.get("target") == answer_node["id"]
        and edge.get("sourceHandle") == "default"
        for edge in preview_graph["edges"]
    )
    workflow = SimpleNamespace(
        id=uuid4(),
        graph={"nodes": [], "edges": []},
        updated_at=datetime.now(timezone.utc),
    )

    issued = DirectEditOrchestrator().issue(
        workflow=workflow,
        structured_request=structured,
        candidate_graph=preview_graph,
        generation_mode="configure_and_generate",
    )

    result_graph = apply_graph_operations(workflow.graph, issued.mutation.operations)
    condition_node_ids = {
        str(node["id"])
        for node in result_graph["nodes"]
        if node.get("type") == "conditionNode"
    }
    assert condition_node_ids
    assert not [
        edge
        for edge in result_graph["edges"]
        if str(edge.get("source")) in condition_node_ids
        and not edge.get("sourceHandle")
    ]
    assert any(node.get("id") == answer_node["id"] for node in result_graph["nodes"])
    default_task = next(
        task
        for task in issued.parameter_group.tasks
        if task.parameter_key == "condition_branch:default"
    )
    assert answer_node["id"] in default_task.validation["options"]
    condition_data = next(
        node["data"]
        for node in result_graph["nodes"]
        if node.get("id") == condition_node["id"]
    )
    assert condition_data["_agent_builder_condition_branch_targets"]["default"] == answer_node["id"]
    assert condition_data["configuration_state"] == "unresolved"


@pytest.mark.parametrize("handle", ["approved", "default"])
def test_condition_branch_select_decision_issues_runtime_compatible_edge(
    monkeypatch, handle
):
    group_id = uuid4()
    task = _branch_task(group_id=group_id, handle=handle)
    group = AgentBuilderParameterGroup(
        group_id=group_id,
        status="active",
        tasks=[task],
    )
    graph = {
        "nodes": [
            _node("start", "startNode"),
            _node(
                "condition",
                "conditionNode",
                {
                    "cases": [{"id": "approved", "label": "Approved"}],
                    "_deferred_parameters": ["cases"],
                    "_agent_builder_condition_branch_targets": {},
                },
            ),
            _node("target", "answerNode", {"outputs": []}),
        ],
        "edges": [
            _edge("edge-start-condition", "start", "condition"),
            _edge("edge-start-target", "start", "target"),
        ],
    }
    service, session, workflow, request_row, db, audit_calls = _service_env(
        monkeypatch, graph, group
    )
    operation_id = uuid4()
    payload = AgentBuilderParameterTaskDecisionRequest.model_validate(
        {
            "operation_id": operation_id,
            "expected_task_version": task.task_version,
            "action": "set",
            "value": {"kind": "select", "value": "target"},
        }
    )

    first = service.decide(session.id, task.task_id, payload)
    retry = service.decide(session.id, task.task_id, payload)

    assert first.graph_mutation is not None
    assert first.graph_mutation.kind == "parameter_update"
    assert first.graph_mutation.completion_context.parameter_task_id == task.task_id
    assert retry.graph_mutation is None
    assert retry.awaiting_persistence_ack is True
    assert db.commits == 1
    assert len(audit_calls) == 1
    assert len(request_row.response_payload["operation_envelopes"]) == 1

    result_graph = apply_graph_operations(workflow.graph, first.graph_mutation.operations)
    branch_edges = [
        edge
        for edge in result_graph["edges"]
        if edge.get("source") == "condition" and edge.get("sourceHandle") == handle
    ]
    assert len(branch_edges) == 1
    assert branch_edges[0]["target"] == "target"
    assert all(edge.get("sourceHandle") for edge in result_graph["edges"] if edge.get("source") == "condition")
    assert validate_workflow_graph_connections(result_graph) == []

    condition_data = _condition_node(result_graph)["data"]
    assert condition_data["_agent_builder_condition_branch_targets"][handle] == "target"
    assert condition_data["configuration_state"] == "unresolved"
    assert derive_node_configuration_state("conditionNode", condition_data) == (
        "unresolved"
    )


def test_condition_branch_no_connection_removes_handle_edge_and_completes_branch_set(
    monkeypatch,
):
    group_id = uuid4()
    task = _branch_task(group_id=group_id, handle="approved")
    group = AgentBuilderParameterGroup(
        group_id=group_id,
        status="active",
        tasks=[task],
    )
    graph = {
        "nodes": [
            _node("start", "startNode"),
            _node(
                "condition",
                "conditionNode",
                {
                    "cases": [{"id": "approved", "label": "Approved"}],
                    "_deferred_parameters": ["cases"],
                    "_agent_builder_condition_branch_targets": {
                        "default": "default-target"
                    },
                    "_agent_builder_condition_branch_confirmed": ["default"],
                },
            ),
            _node("old-target", "answerNode", {"outputs": []}),
            _node("default-target", "answerNode", {"outputs": []}),
        ],
        "edges": [
            _edge("edge-start-condition", "start", "condition"),
            _edge("edge-start-old", "start", "old-target"),
            _edge(
                "edge-condition-approved-old",
                "condition",
                "old-target",
                source_handle="approved",
            ),
            _edge(
                "edge-condition-default",
                "condition",
                "default-target",
                source_handle="default",
            ),
        ],
    }
    service, session, workflow, *_ = _service_env(monkeypatch, graph, group)
    payload = AgentBuilderParameterTaskDecisionRequest.model_validate(
        {
            "operation_id": uuid4(),
            "expected_task_version": task.task_version,
            "action": "set",
            "value": {"kind": "select", "value": NO_CONNECTION},
        }
    )

    response = service.decide(session.id, task.task_id, payload)

    assert response.graph_mutation is not None
    operations = response.graph_mutation.operations
    assert any(
        operation.op == "remove_edge"
        and operation.edge_id == "edge-condition-approved-old"
        for operation in operations
    )
    assert not [
        operation
        for operation in operations
        if operation.op == "add_edge"
        and operation.edge.source == "condition"
        and operation.edge.sourceHandle == "approved"
    ]

    result_graph = apply_graph_operations(workflow.graph, operations)
    assert not [
        edge
        for edge in result_graph["edges"]
        if edge.get("source") == "condition"
        and edge.get("sourceHandle") == "approved"
    ]
    condition_data = _condition_node(result_graph)["data"]
    assert condition_data["_agent_builder_condition_branch_targets"]["approved"] is None
    assert "cases" not in condition_data.get("_deferred_parameters", [])
    assert condition_data["configuration_state"] == "resolved"
    assert derive_node_configuration_state("conditionNode", condition_data) == (
        "resolved"
    )
    assert validate_workflow_graph_connections(result_graph) == []


def test_condition_cases_reconcile_adds_and_cancels_dynamic_branch_tasks():
    group_id = uuid4()
    cases_task = AgentBuilderParameterTask(
        task_id=uuid4(),
        group_id=group_id,
        step_id="step-condition",
        node_id="condition",
        node_type="conditionNode",
        parameter_key="cases",
        label="Cases",
        input_type="json",
        required=True,
        status="completed",
        task_version=2,
        stable_order=0,
        reason="Define the condition cases.",
        input_guidance="Enter the condition cases.",
    )
    removed_task = _branch_task(
        group_id=group_id,
        handle="removed",
        status="completed",
        stable_order=1,
    )
    default_task = _branch_task(
        group_id=group_id,
        handle="default",
        status="completed",
        stable_order=2,
    )
    group = AgentBuilderParameterGroup(
        group_id=group_id,
        status="completed",
        tasks=[cases_task, removed_task, default_task],
    )
    graph = {
        "nodes": [
            _node(
                "condition",
                "conditionNode",
                {
                    "cases": [
                        {"id": "approved", "label": "Approved"},
                        {"id": "rejected", "label": "Rejected"},
                    ],
                    "_agent_builder_condition_branch_targets": {
                        "default": "target"
                    },
                    "_agent_builder_condition_branch_confirmed": ["default"],
                },
            ),
            _node("target", "answerNode", {"outputs": []}),
        ],
        "edges": [
            _edge(
                "edge-condition-default",
                "condition",
                "target",
                source_handle="default",
            )
        ],
    }

    reconciled = reconcile_condition_branch_tasks(group, graph)

    tasks_by_key = {task.parameter_key: task for task in reconciled.tasks}
    assert tasks_by_key["condition_branch:removed"].status == "canceled"
    assert tasks_by_key["condition_branch:default"].status == "completed"
    assert tasks_by_key["condition_branch:approved"].status == "active"
    assert tasks_by_key["condition_branch:rejected"].status == "pending"
    assert reconciled.status == "active"


def test_condition_branch_target_rejects_cycle_and_unissued_option():
    group_id = uuid4()
    task = _branch_task(group_id=group_id, handle="default")
    cycle_task = task.model_copy(
        update={
            "validation": {
                **task.validation,
                "options": ["upstream", "target", NO_CONNECTION],
            }
        }
    )
    graph = {
        "nodes": [
            _node("upstream", "llmNode"),
            _node("condition", "conditionNode", {"cases": []}),
            _node("target", "answerNode", {"outputs": []}),
        ],
        "edges": [_edge("edge-upstream-condition", "upstream", "condition")],
    }

    assert condition_branch_decision_issues(
        graph=graph,
        task=cycle_task,
        value="upstream",
    ) == ["invalid_target_node"]
    assert condition_branch_decision_issues(
        graph=graph,
        task=task,
        value="not-issued",
    ) == ["invalid_selection"]


def test_condition_cases_ack_reconciles_branch_tasks_once(monkeypatch):
    group_id = uuid4()
    cases_task = AgentBuilderParameterTask(
        task_id=uuid4(),
        group_id=group_id,
        step_id="step-condition",
        node_id="condition",
        node_type="conditionNode",
        parameter_key="cases",
        label="Cases",
        input_type="json",
        required=True,
        status="active",
        task_version=1,
        stable_order=0,
        reason="Define the condition cases.",
        input_guidance="Enter the condition cases.",
    )
    default_task = _branch_task(
        group_id=group_id,
        handle="default",
        status="pending",
        stable_order=1,
    )
    group = AgentBuilderParameterGroup(
        group_id=group_id,
        status="active",
        tasks=[cases_task, default_task],
    )
    graph = {
        "nodes": [
            _node(
                "condition",
                "conditionNode",
                {
                    "cases": [],
                    "_deferred_parameters": ["cases"],
                    "_agent_builder_condition_branch_targets": {
                        "default": "target"
                    },
                    "_agent_builder_condition_branch_confirmed": [],
                },
            ),
            _node("target", "answerNode", {"outputs": []}),
        ],
        "edges": [
            _edge(
                "edge-condition-default",
                "condition",
                "target",
                source_handle="default",
            )
        ],
    }
    service, session, workflow, request_row, db, _ = _service_env(
        monkeypatch, graph, group
    )
    operation_id = uuid4()
    decision = service.decide(
        session.id,
        cases_task.task_id,
        AgentBuilderParameterTaskDecisionRequest.model_validate(
            {
                "operation_id": operation_id,
                "expected_task_version": cases_task.task_version,
                "action": "set",
                "value": {
                    "kind": "json",
                    "value": [
                        {"id": "approved", "label": "Approved"},
                        {"id": "rejected", "label": "Rejected"},
                    ],
                },
            }
        ),
    )
    mutation = decision.graph_mutation
    assert mutation is not None
    workflow.graph = apply_graph_operations(workflow.graph, mutation.operations)
    workflow.updated_at = datetime.now(timezone.utc)
    service.repository.mark_envelope_pending_save(request_row, operation_id)
    service.repository.mark_envelope_saved(
        request_row,
        operation_id=operation_id,
        result_graph_hash=mutation.expected_result_graph_hash,
        workflow_updated_at=workflow.updated_at,
    )
    monkeypatch.setattr(
        lifecycle_module,
        "has_workflow_permission",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        lifecycle_module,
        "add_action_audit",
        lambda *_args, **_kwargs: None,
    )
    lifecycle = GraphMutationLifecycleService(
        db,
        user_id=service.user_id,
        organization_id=service.organization_id,
        repository=service.repository,
    )
    acknowledgement = GraphMutationAcknowledgementRequest(
        workflow_id=workflow.id,
        graph_hash=mutation.expected_result_graph_hash,
        updated_at=workflow.updated_at,
    )

    first = lifecycle.acknowledge(session.id, operation_id, acknowledgement)
    second = lifecycle.acknowledge(session.id, operation_id, acknowledgement)

    assert first.parameter_group is not None
    keys = [task.parameter_key for task in first.parameter_group.tasks]
    assert keys.count("condition_branch:approved") == 1
    assert keys.count("condition_branch:rejected") == 1
    assert keys.count("condition_branch:default") == 1
    assert second.parameter_group is not None
    assert [task.parameter_key for task in second.parameter_group.tasks] == keys
    condition_data = _condition_node(workflow.graph)["data"]
    assert condition_data["configuration_state"] == "unresolved"
    assert "cases" in condition_data["_deferred_parameters"]
