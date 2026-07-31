from __future__ import annotations

import copy
import re
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from apps.shared.schemas.agent_builder import (
    AgentBuilderParameterGroup,
    AgentBuilderParameterTask,
)
from apps.shared.services.workflow_node_catalog import (
    connection_policy_for_node_type,
    derive_node_configuration_state,
)


CONDITION_NODE_TYPE = "conditionNode"
CONDITION_BRANCH_PARAMETER_PREFIX = "condition_branch:"
CONDITION_BRANCH_NO_CONNECTION = "__agent_builder_no_connection__"
CONDITION_BRANCH_TARGETS_KEY = "_agent_builder_condition_branch_targets"
CONDITION_BRANCH_CONFIRMED_KEY = "_agent_builder_condition_branch_confirmed"
CONDITION_BRANCH_DEFERRED_PARAMETER = "cases"


def condition_branch_handle_from_key(parameter_key: str) -> str | None:
    if not parameter_key.startswith(CONDITION_BRANCH_PARAMETER_PREFIX):
        return None
    handle = parameter_key[len(CONDITION_BRANCH_PARAMETER_PREFIX) :]
    return handle or None


def is_condition_branch_task(task: Any) -> bool:
    return (
        getattr(task, "node_type", None) == CONDITION_NODE_TYPE
        and condition_branch_handle_from_key(str(getattr(task, "parameter_key", "")))
        is not None
    )


def add_condition_branch_tasks(
    *,
    graph: dict[str, Any],
    tasks: list[AgentBuilderParameterTask],
    group_id: UUID,
    step_node_ids: dict[str, str],
) -> tuple[dict[str, Any], list[AgentBuilderParameterTask]]:
    updated_graph = copy.deepcopy(graph)
    updated_tasks = list(tasks)
    existing_keys = {
        (str(task.node_id), str(task.parameter_key)) for task in updated_tasks
    }
    next_order = (
        max((int(task.stable_order) for task in updated_tasks), default=-1) + 1
    )
    step_id_by_node_id = {str(node_id): step_id for step_id, node_id in step_node_ids.items()}
    included_node_ids = set(step_id_by_node_id)

    for node in updated_graph.get("nodes") or []:
        if not isinstance(node, dict) or node.get("type") != CONDITION_NODE_TYPE:
            continue
        node_id = str(node.get("id") or "")
        if not node_id or node_id not in included_node_ids:
            continue
        data = dict(node.get("data") or {})
        handles = condition_branch_handles(data)
        targets = _branch_target_map(data, handles)
        targets.update(
            {
                handle: target
                for handle, target in _edge_target_map(
                    updated_graph,
                    node_id,
                    handles,
                ).items()
                if handle not in targets
            }
        )
        confirmed = _confirmed_branch_handles(data, handles)
        data[CONDITION_BRANCH_TARGETS_KEY] = targets
        data[CONDITION_BRANCH_CONFIRMED_KEY] = confirmed
        data["_deferred_parameters"] = _with_deferred_cases(
            data.get("_deferred_parameters", []),
            unresolved=not all(handle in confirmed for handle in handles),
        )
        data["configuration_state"] = derive_node_configuration_state(
            CONDITION_NODE_TYPE,
            data,
        )
        node["data"] = data

        target_options, labels = _target_options(updated_graph, node_id)
        for handle in handles:
            parameter_key = f"{CONDITION_BRANCH_PARAMETER_PREFIX}{handle}"
            if (node_id, parameter_key) in existing_keys:
                continue
            updated_tasks.append(
                AgentBuilderParameterTask(
                    task_id=_task_id(group_id, node_id, handle),
                    group_id=group_id,
                    step_id=step_id_by_node_id.get(node_id, f"step_{node_id}"),
                    node_id=node_id,
                    node_type=CONDITION_NODE_TYPE,
                    parameter_key=parameter_key,
                    label=_branch_label(data, handle),
                    input_type="select",
                    required=True,
                    status="pending",
                    task_version=1,
                    stable_order=next_order,
                    reason="조건 분기가 실행될 다음 노드를 확인합니다.",
                    input_guidance="기존 노드를 선택하거나 연결 안 함을 명시하세요.",
                    node_label=str(data.get("title") or node_id),
                    node_purpose="조건 결과에 따라 다음 단계를 선택합니다.",
                    configuration_state="unresolved",
                    validation={
                        "type": "condition_branch_target",
                        "condition_branch_handle": handle,
                        "options": [*target_options, CONDITION_BRANCH_NO_CONNECTION],
                        "option_labels": {
                            **labels,
                            CONDITION_BRANCH_NO_CONNECTION: "연결 안 함",
                        },
                    },
                    sensitivity="safe",
                )
            )
            next_order += 1

    return updated_graph, updated_tasks


def condition_branch_decision_issues(
    *,
    graph: dict[str, Any],
    task: AgentBuilderParameterTask,
    value: Any,
) -> list[str]:
    handle = condition_branch_handle_from_key(task.parameter_key)
    if handle is None:
        return ["unknown_parameter"]
    condition_node = _node_by_id(graph, task.node_id)
    if condition_node is None or condition_node.get("type") != CONDITION_NODE_TYPE:
        return ["stale_condition_node"]
    data = condition_node.get("data") if isinstance(condition_node.get("data"), dict) else {}
    if handle not in condition_branch_handles(data):
        return ["invalid_condition_handle"]
    if not isinstance(value, str) or not value:
        return ["invalid_selection"]
    issued_options = task.validation.get("options")
    if not isinstance(issued_options, list) or value not in issued_options:
        return ["invalid_selection"]
    if value == CONDITION_BRANCH_NO_CONNECTION:
        return []
    target_node = _node_by_id(graph, value)
    if target_node is None:
        return ["unknown_target_node"]
    if str(target_node.get("id")) == str(task.node_id):
        return ["invalid_target_node"]
    policy = connection_policy_for_node_type(str(target_node.get("type") or ""))
    if policy and policy.get("incoming") == "forbidden":
        return ["invalid_target_node"]
    if _would_create_cycle(graph, str(task.node_id), value):
        return ["invalid_target_node"]
    return []


def build_condition_branch_operations(
    *,
    graph: dict[str, Any],
    task: AgentBuilderParameterTask,
    value: str,
) -> list[dict[str, Any]]:
    handle = condition_branch_handle_from_key(task.parameter_key)
    if handle is None:
        raise ValueError("not a condition branch task")
    condition_node = _node_by_id(graph, task.node_id)
    if condition_node is None:
        raise ValueError("condition node is missing")
    data = dict(condition_node.get("data") or {})
    handles = condition_branch_handles(data)
    desired_target = None if value == CONDITION_BRANCH_NO_CONNECTION else value
    targets = _branch_target_map(data, handles)
    targets[handle] = desired_target
    confirmed = _confirmed_branch_handles(data, handles)
    if handle not in confirmed:
        confirmed.append(handle)
    data[CONDITION_BRANCH_TARGETS_KEY] = targets
    data[CONDITION_BRANCH_CONFIRMED_KEY] = confirmed
    data["_deferred_parameters"] = _with_deferred_cases(
        data.get("_deferred_parameters", []),
        unresolved=not all(handle in confirmed for handle in handles),
    )
    data["configuration_state"] = derive_node_configuration_state(
        CONDITION_NODE_TYPE,
        data,
    )

    operations: list[dict[str, Any]] = [
        {"op": "remove_edge", "edge_id": str(edge["id"])}
        for edge in graph.get("edges") or []
        if isinstance(edge, dict)
        and str(edge.get("source") or "") == str(task.node_id)
        and edge.get("sourceHandle") == handle
        and edge.get("id") is not None
    ]
    operations.append(
        {
            "op": "replace_node_data",
            "node_id": task.node_id,
            "data": data,
        }
    )
    if desired_target is not None:
        operations.append(
            {
                "op": "add_edge",
                "edge": {
                    "id": _edge_id(str(task.node_id), handle, desired_target),
                    "source": task.node_id,
                    "target": desired_target,
                    "sourceHandle": handle,
                },
            }
        )
    return operations


def normalize_condition_outgoing_edges(graph: dict[str, Any]) -> dict[str, Any]:
    """Preserve generated downstream nodes while making one linear edge explicit."""
    normalized = copy.deepcopy(graph)
    nodes = normalized.get("nodes") or []
    condition_ids = {
        str(node["id"])
        for node in nodes
        if isinstance(node, dict)
        and node.get("id")
        if str(node.get("type") or "") == CONDITION_NODE_TYPE
    }
    if not condition_ids:
        return normalized

    edges = [copy.deepcopy(edge) for edge in normalized.get("edges") or []]
    for condition_id in condition_ids:
        outgoing = [
            edge
            for edge in edges
            if isinstance(edge, dict)
            and str(edge.get("source") or "") == condition_id
        ]
        handleless = [edge for edge in outgoing if not edge.get("sourceHandle")]
        has_default = any(edge.get("sourceHandle") == "default" for edge in outgoing)
        if len(handleless) == 1 and not has_default:
            handleless[0]["sourceHandle"] = "default"
    normalized["edges"] = edges
    return normalized


def prepare_condition_cases_data(data: dict[str, Any]) -> dict[str, Any]:
    """Keep retained decisions and force every newly introduced handle to be confirmed."""
    updated = copy.deepcopy(data)
    handles = condition_branch_handles(updated)
    updated[CONDITION_BRANCH_TARGETS_KEY] = _branch_target_map(updated, handles)
    confirmed = _confirmed_branch_handles(updated, handles)
    updated[CONDITION_BRANCH_CONFIRMED_KEY] = confirmed
    updated["_deferred_parameters"] = _with_deferred_cases(
        updated.get("_deferred_parameters", []),
        unresolved=not all(handle in confirmed for handle in handles),
    )
    updated["configuration_state"] = derive_node_configuration_state(
        CONDITION_NODE_TYPE,
        updated,
    )
    return updated


def reconcile_condition_branch_tasks(
    group: AgentBuilderParameterGroup | None,
    graph: dict[str, Any] | None,
) -> AgentBuilderParameterGroup | None:
    if group is None or group.status == "canceled":
        return group
    canonical_graph = graph or {"nodes": [], "edges": []}
    condition_nodes = {
        str(node.get("id")): node
        for node in canonical_graph.get("nodes") or []
        if isinstance(node, dict)
        and node.get("id")
        and str(node.get("type") or "") == CONDITION_NODE_TYPE
    }
    step_node_ids: dict[str, str] = {}
    for task in group.tasks:
        if task.node_type == CONDITION_NODE_TYPE and task.node_id in condition_nodes:
            step_node_ids.setdefault(task.step_id, task.node_id)

    _, tasks = add_condition_branch_tasks(
        graph=canonical_graph,
        tasks=[task.model_copy(deep=True) for task in group.tasks],
        group_id=group.group_id,
        step_node_ids=step_node_ids,
    )
    for index, task in enumerate(tasks):
        if not is_condition_branch_task(task):
            continue
        node = condition_nodes.get(task.node_id)
        data = node.get("data") if isinstance(node, dict) else {}
        handles = condition_branch_handles(data if isinstance(data, dict) else {})
        handle = condition_branch_handle_from_key(task.parameter_key)
        if handle not in handles:
            if task.status != "canceled":
                tasks[index] = task.model_copy(
                    update={"status": "canceled", "task_version": task.task_version + 1}
                )
            continue
        options, labels = _target_options(canonical_graph, task.node_id)
        validation = {
            "type": "condition_branch_target",
            "condition_branch_handle": handle,
            "options": [*options, CONDITION_BRANCH_NO_CONNECTION],
            "option_labels": {
                **labels,
                CONDITION_BRANCH_NO_CONNECTION: "연결 안 함",
            },
        }
        update: dict[str, Any] = {
            "label": _branch_label(data if isinstance(data, dict) else {}, handle),
            "validation": validation,
        }
        if task.status == "canceled":
            update.update({"status": "pending", "task_version": task.task_version + 1})
        tasks[index] = task.model_copy(update=update)

    if group.status != "pending_save" and not any(
        task.status in {"active", "invalid"} for task in tasks
    ):
        next_index = next(
            (
                index
                for index, task in sorted(
                    enumerate(tasks), key=lambda item: item[1].stable_order
                )
                if task.status == "pending"
            ),
            None,
        )
        if next_index is not None:
            tasks[next_index] = tasks[next_index].model_copy(update={"status": "active"})

    terminal = {"completed", "skipped", "deferred", "canceled"}
    status = group.status
    if status != "pending_save":
        status = "completed" if all(task.status in terminal for task in tasks) else "active"
    return group.model_copy(update={"status": status, "tasks": tasks})


def condition_branch_handles(node_data: dict[str, Any] | None) -> list[str]:
    data = node_data if isinstance(node_data, dict) else {}
    cases = data.get("cases") if isinstance(data.get("cases"), list) else []
    handles = [
        str(case.get("id"))
        for case in cases
        if isinstance(case, dict) and case.get("id")
    ]
    return [*dict.fromkeys(handles), "default"]


def _branch_target_map(data: dict[str, Any], handles: list[str]) -> dict[str, str | None]:
    raw = data.get(CONDITION_BRANCH_TARGETS_KEY)
    if not isinstance(raw, dict):
        return {}
    allowed = set(handles)
    return {
        str(handle): (None if target is None else str(target))
        for handle, target in raw.items()
        if str(handle) in allowed and (target is None or isinstance(target, str))
    }


def _confirmed_branch_handles(data: dict[str, Any], handles: list[str]) -> list[str]:
    raw = data.get(CONDITION_BRANCH_CONFIRMED_KEY)
    if not isinstance(raw, list):
        return []
    allowed = set(handles)
    return list(
        dict.fromkeys(
            str(handle)
            for handle in raw
            if isinstance(handle, str) and str(handle) in allowed
        )
    )


def _edge_target_map(
    graph: dict[str, Any],
    source_node_id: str,
    handles: list[str],
) -> dict[str, str]:
    targets_by_handle: dict[str, list[str]] = {}
    allowed = set(handles)
    for edge in graph.get("edges") or []:
        if not isinstance(edge, dict) or str(edge.get("source") or "") != source_node_id:
            continue
        handle = edge.get("sourceHandle")
        target = edge.get("target")
        if isinstance(handle, str) and handle in allowed and target is not None:
            targets_by_handle.setdefault(handle, []).append(str(target))
    return {
        handle: targets[0]
        for handle, targets in targets_by_handle.items()
        if len(set(targets)) == 1
    }


def _with_deferred_cases(value: Any, *, unresolved: bool) -> list[str]:
    existing = [str(item) for item in value if isinstance(item, str)] if isinstance(value, list) else []
    if unresolved:
        return list(dict.fromkeys([*existing, CONDITION_BRANCH_DEFERRED_PARAMETER]))
    return [
        item
        for item in existing
        if item != CONDITION_BRANCH_DEFERRED_PARAMETER
    ]


def _node_by_id(graph: dict[str, Any], node_id: str) -> dict[str, Any] | None:
    return next(
        (
            node
            for node in graph.get("nodes") or []
            if isinstance(node, dict) and str(node.get("id") or "") == str(node_id)
        ),
        None,
    )


def _target_options(
    graph: dict[str, Any],
    source_node_id: str,
) -> tuple[list[str], dict[str, str]]:
    options: list[str] = []
    labels: dict[str, str] = {}
    for node in graph.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or "")
        if not node_id or node_id == source_node_id:
            continue
        policy = connection_policy_for_node_type(str(node.get("type") or ""))
        if policy and policy.get("incoming") == "forbidden":
            continue
        if _would_create_cycle(graph, source_node_id, node_id):
            continue
        options.append(node_id)
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        labels[node_id] = str(data.get("title") or node_id)
    return options, labels


def _would_create_cycle(
    graph: dict[str, Any], source_node_id: str, target_node_id: str
) -> bool:
    if source_node_id == target_node_id:
        return True
    reachable = {target_node_id}
    changed = True
    while changed:
        changed = False
        for edge in graph.get("edges") or []:
            if not isinstance(edge, dict):
                continue
            source = str(edge.get("source") or "")
            target = str(edge.get("target") or "")
            if source in reachable and target and target not in reachable:
                reachable.add(target)
                changed = True
    return source_node_id in reachable


def _branch_label(data: dict[str, Any], handle: str) -> str:
    if handle == "default":
        return "기본 분기 연결"
    cases = data.get("cases") if isinstance(data.get("cases"), list) else []
    case = next(
        (
            item
            for item in cases
            if isinstance(item, dict) and str(item.get("id") or "") == handle
        ),
        None,
    )
    if case is None:
        return f"{handle} 분기 연결"
    label = case.get("label") or case.get("title") or case.get("name")
    return f"{label or handle} 분기 연결"


def _task_id(group_id: UUID, node_id: str, handle: str) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        f"agent-builder:condition-branch:{group_id}:{node_id}:{handle}",
    )


def _edge_id(source: str, handle: str, target: str) -> str:
    return "edge-condition-{source}-{handle}-{target}".format(
        source=_slug(source),
        handle=_slug(handle),
        target=_slug(target),
    )


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-")
    return slug or "branch"
