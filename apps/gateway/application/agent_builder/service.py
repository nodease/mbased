from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from apps.gateway.application.agent_builder.graph_mutation_builder import (
    GraphMutationBuilder,
)
from apps.gateway.application.agent_builder.condition_branches import (
    add_condition_branch_tasks,
    normalize_condition_outgoing_edges,
)
from apps.gateway.application.agent_builder.parameter_suggestions import (
    ParameterSuggestionResolver,
)
from apps.gateway.application.agent_builder.parameter_tasks import ParameterTaskPlanner
from apps.shared.schemas.agent_builder import (
    AgentBuilderParameterGroup,
    AgentBuilderStructuredRequest,
    GraphMutation,
)
from apps.shared.services.workflow_node_catalog import (
    node_parameter_definitions,
    node_type_for_capability,
)


class DirectEditOrchestrationError(ValueError):
    pass


@dataclass(frozen=True)
class DirectEditIssueResult:
    mutation: GraphMutation
    parameter_group: AgentBuilderParameterGroup | None
    step_node_ids: dict[str, str]


def _pending_until_structural_ack(tasks):
    return [
        task.model_copy(update={"status": "pending"})
        if task.status == "active"
        else task
        for task in tasks
    ]


def _graph_operations(
    base_graph: dict[str, Any],
    candidate_graph: dict[str, Any],
    *,
    replace_existing: bool = False,
) -> list[dict[str, Any]]:
    base_nodes = {
        str(node.get("id")): node
        for node in base_graph.get("nodes") or []
        if isinstance(node, dict) and node.get("id")
    }
    candidate_nodes = {
        str(node.get("id")): node
        for node in candidate_graph.get("nodes") or []
        if isinstance(node, dict) and node.get("id")
    }
    base_edges = {
        str(edge.get("id")): edge
        for edge in base_graph.get("edges") or []
        if isinstance(edge, dict) and edge.get("id")
    }
    candidate_edges = {
        str(edge.get("id")): edge
        for edge in candidate_graph.get("edges") or []
        if isinstance(edge, dict) and edge.get("id")
    }
    if replace_existing:
        return [
            *(
                {"op": "remove_edge", "edge_id": edge_id}
                for edge_id in sorted(base_edges)
            ),
            *(
                {"op": "remove_node", "node_id": node_id}
                for node_id in sorted(base_nodes)
            ),
            *(
                {"op": "add_node", "node": copy.deepcopy(candidate_nodes[node_id])}
                for node_id in sorted(candidate_nodes)
            ),
            *(
                {"op": "add_edge", "edge": copy.deepcopy(candidate_edges[edge_id])}
                for edge_id in sorted(candidate_edges)
            ),
        ]
    removed_nodes = set(base_nodes) - set(candidate_nodes)
    changed_nodes = {
        node_id
        for node_id in set(base_nodes) & set(candidate_nodes)
        if base_nodes[node_id] != candidate_nodes[node_id]
    }
    if removed_nodes or changed_nodes:
        raise DirectEditOrchestrationError(
            "direct graph generation cannot replace existing nodes"
        )

    operations: list[dict[str, Any]] = []
    operations.extend(
        {"op": "remove_edge", "edge_id": edge_id}
        for edge_id in sorted(set(base_edges) - set(candidate_edges))
    )
    operations.extend(
        {"op": "add_node", "node": copy.deepcopy(candidate_nodes[node_id])}
        for node_id in sorted(set(candidate_nodes) - set(base_nodes))
    )
    for edge_id in sorted(set(candidate_edges) & set(base_edges)):
        if candidate_edges[edge_id] != base_edges[edge_id]:
            raise DirectEditOrchestrationError(
                "direct graph generation cannot replace existing edges"
            )
    operations.extend(
        {"op": "add_edge", "edge": copy.deepcopy(candidate_edges[edge_id])}
        for edge_id in sorted(set(candidate_edges) - set(base_edges))
    )
    return operations


def _step_node_ids(
    structured_request: AgentBuilderStructuredRequest,
    graph: dict[str, Any],
    *,
    included_node_ids: set[str],
) -> dict[str, str]:
    nodes = [
        node
        for node in graph.get("nodes") or []
        if isinstance(node, dict) and str(node.get("id")) in included_node_ids
    ]
    available: dict[str, list[str]] = {}
    for node in nodes:
        available.setdefault(str(node.get("type") or ""), []).append(str(node["id"]))
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for step in structured_request.planned_steps:
        node_type = node_type_for_capability(step.capability)
        choices = available.get(str(node_type or ""), [])
        node_id = next((candidate for candidate in choices if candidate not in used), None)
        if node_id is None:
            continue
        mapping[step.step_id] = node_id
        used.add(node_id)
    for node in nodes:
        node_id = str(node["id"])
        if node_id not in used:
            mapping[f"step_{node_id}"] = node_id
    return mapping


def step_node_ids_for_graph(
    structured_request: AgentBuilderStructuredRequest,
    graph: dict[str, Any],
) -> dict[str, str]:
    included_node_ids = {
        str(node.get("id"))
        for node in graph.get("nodes") or []
        if isinstance(node, dict) and node.get("id")
    }
    return _step_node_ids(
        structured_request,
        graph,
        included_node_ids=included_node_ids,
    )


def _upstream_candidates(
    graph: dict[str, Any], step_node_ids: dict[str, str]
) -> dict[tuple[str, str], list[list[str]]]:
    resolver = ParameterSuggestionResolver()
    result: dict[tuple[str, str], list[list[str]]] = {}
    node_by_id = {
        str(node.get("id")): node
        for node in graph.get("nodes") or []
        if isinstance(node, dict) and node.get("id")
    }
    for step_id, node_id in step_node_ids.items():
        node = node_by_id[node_id]
        for parameter in node_parameter_definitions(str(node.get("type") or "")):
            if parameter.get("input_type") not in {
                "variable_selector",
                "variable_selector_list",
            }:
                continue
            suggestions = resolver.resolve(
                graph=graph,
                target_node_id=node_id,
                parameter_key=str(parameter["key"]),
            )
            result[(step_id, str(parameter["key"]))] = [
                suggestion.value_selector for suggestion in suggestions
            ]
    return result


def _direct_edit_externally_managed_parameters(
    graph: dict[str, Any],
    step_node_ids: dict[str, str],
) -> set[tuple[str, str]]:
    node_types = {
        str(node.get("id")): str(node.get("type") or "")
        for node in graph.get("nodes") or []
        if isinstance(node, dict) and node.get("id") is not None
    }
    return {
        (step_id, "knowledgeBases")
        for step_id, node_id in step_node_ids.items()
        if node_types.get(node_id) == "llmNode"
    }


def plan_parameter_tasks_for_existing_graph(
    *,
    graph: dict[str, Any],
    step_node_ids: dict[str, str],
    group_id,
    affected_node_ids: set[str] | None = None,
):
    """Rebuild current Catalog task definitions without rerunning the planner LLM."""
    existing_node_ids = {
        str(node.get("id"))
        for node in graph.get("nodes") or []
        if isinstance(node, dict) and node.get("id")
    }
    current_step_node_ids = {
        str(step_id): str(node_id)
        for step_id, node_id in step_node_ids.items()
        if str(node_id) in existing_node_ids
        and (affected_node_ids is None or str(node_id) in affected_node_ids)
    }
    if not current_step_node_ids:
        return []
    task_plan = ParameterTaskPlanner().plan(
        graph=graph,
        step_node_ids=current_step_node_ids,
        explicit_values={},
        upstream_candidates=_upstream_candidates(graph, current_step_node_ids),
        guidance_hints=[],
        group_id=group_id,
        externally_managed_parameters=_direct_edit_externally_managed_parameters(
            graph,
            current_step_node_ids,
        ),
        base_node_ids=existing_node_ids,
        affected_node_ids=affected_node_ids,
    )
    candidate_graph, tasks = add_condition_branch_tasks(
        graph=task_plan.graph,
        tasks=task_plan.tasks,
        group_id=task_plan.group_id,
        step_node_ids=current_step_node_ids,
    )
    suggestion_resolver = ParameterSuggestionResolver()
    return [
        task.model_copy(
            update={
                "suggestions": suggestion_resolver.resolve(
                    graph=candidate_graph,
                    target_node_id=task.node_id,
                    parameter_key=task.parameter_key,
                )
                if task.input_type in {"variable_selector", "variable_selector_list"}
                else []
            }
        )
        for task in tasks
    ]


class DirectEditOrchestrator:
    def issue(
        self,
        *,
        workflow: Any,
        structured_request: AgentBuilderStructuredRequest,
        candidate_graph: dict[str, Any],
        generation_mode: str,
    ) -> DirectEditIssueResult:
        base_graph = copy.deepcopy(workflow.graph or {"nodes": [], "edges": []})
        candidate = normalize_condition_outgoing_edges(candidate_graph)
        replace_existing = bool(
            (base_graph.get("nodes") or base_graph.get("edges"))
            and (
                structured_request.request_type == "new_workflow"
                or structured_request.draft_mode == "replace_workflow"
            )
        )
        initial_operations = _graph_operations(
            base_graph,
            candidate,
            replace_existing=replace_existing,
        )
        generated_node_ids = {
            str(operation["node"]["id"])
            for operation in initial_operations
            if operation["op"] == "add_node"
        }
        step_node_ids = _step_node_ids(
            structured_request,
            candidate,
            included_node_ids=generated_node_ids,
        )
        parameter_group = None
        if generation_mode == "configure_and_generate":
            task_plan = ParameterTaskPlanner().plan(
                graph=candidate,
                step_node_ids=step_node_ids,
                explicit_values={
                    (item.step_id, item.parameter_key): item.value
                    for item in structured_request.explicit_parameter_values
                },
                upstream_candidates=_upstream_candidates(candidate, step_node_ids),
                guidance_hints=structured_request.parameter_guidance_hints,
                step_purposes={
                    step.step_id: step.purpose
                    for step in structured_request.planned_steps
                },
                externally_managed_parameters=_direct_edit_externally_managed_parameters(
                    candidate,
                    step_node_ids,
                ),
                base_node_ids={
                    str(node.get("id"))
                    for node in base_graph.get("nodes") or []
                    if isinstance(node, dict) and node.get("id")
                },
            )
            candidate = task_plan.graph
            candidate, planned_tasks = add_condition_branch_tasks(
                graph=candidate,
                tasks=task_plan.tasks,
                group_id=task_plan.group_id,
                step_node_ids=step_node_ids,
            )
            suggestion_resolver = ParameterSuggestionResolver()
            tasks = [
                task.model_copy(
                    update={
                        "suggestions": suggestion_resolver.resolve(
                            graph=candidate,
                            target_node_id=task.node_id,
                            parameter_key=task.parameter_key,
                        )
                        if task.input_type in {
                            "variable_selector",
                            "variable_selector_list",
                        }
                        else []
                    }
                )
                for task in planned_tasks
            ]
            parameter_group = AgentBuilderParameterGroup(
                group_id=task_plan.group_id,
                status="pending_save",
                tasks=_pending_until_structural_ack(tasks),
            )

        operations = _graph_operations(
            base_graph,
            candidate,
            replace_existing=replace_existing,
        )
        kind = (
            "replace_workflow"
            if replace_existing
            else "initial_graph"
            if not (base_graph.get("nodes") or base_graph.get("edges"))
            else "graph_edit"
        )
        mutation = GraphMutationBuilder().build(
            operation_id=uuid4(),
            kind=kind,
            generation_mode=generation_mode,
            workflow_id=workflow.id,
            base_graph=base_graph,
            expected_workflow_updated_at=workflow.updated_at,
            operations=operations,
        )
        return DirectEditIssueResult(
            mutation=mutation,
            parameter_group=parameter_group,
            step_node_ids=step_node_ids,
        )
