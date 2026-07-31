from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from apps.shared.schemas.agent_builder import (
    AgentBuilderParameterGroup,
    AgentBuilderParameterGuidanceHint,
    AgentBuilderParameterTask,
)
from apps.shared.services.workflow_node_catalog import (
    apply_node_parameter_value,
    derive_node_configuration_state,
    node_parameter_is_configured,
    node_parameter_is_required,
    parameter_definition,
    node_parameter_value,
    node_parameter_definitions,
    required_any_configuration_groups,
    validate_node_parameter_value,
)
from apps.shared.services.tracing.policy import TracePolicyService
from apps.shared.services.tracing.redaction import TraceRedactionService
from apps.gateway.application.agent_builder.parameter_suggestions import (
    ParameterSuggestionError,
    ParameterSuggestionResolver,
)


class ParameterTaskConflict(ValueError):
    pass


class ParameterTaskSafetyError(ValueError):
    pass


_LLM_PROMPT_PARAMETER_KEYS = (
    "system_prompt",
    "user_prompt",
    "assistant_prompt",
)


@dataclass(frozen=True)
class ParameterTaskPlan:
    group_id: UUID
    graph: dict[str, Any]
    tasks: list[AgentBuilderParameterTask]


@dataclass(frozen=True)
class PreparedParameterDecision:
    operation_id: UUID
    task_id: UUID
    action: Literal["set", "clear", "confirm", "defer", "skip", "previous"]
    expected_task_version: int
    awaiting_persistence_ack: bool
    graph_data_patch: dict[str, Any] | None


def _is_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return bool(value)
    return True


def canonical_parameter_value_fingerprint(value: Any) -> str:
    try:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ParameterTaskConflict("parameter value is not canonical") from exc
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _canonical_node_parameter_value(
    node_type: str,
    parameter_key: str,
    node_data: dict[str, Any],
) -> tuple[bool, Any]:
    return node_parameter_value(node_type, parameter_key, node_data)


def _parameter_is_visible(
    node_type: str,
    parameter: dict[str, Any],
    node_data: dict[str, Any],
) -> bool:
    validation = parameter.get("validation")
    if not isinstance(validation, dict):
        return True
    visible_when = validation.get("visible_when")
    if not isinstance(visible_when, dict):
        return True
    controlling_key = visible_when.get("parameter_key")
    if not isinstance(controlling_key, str) or not controlling_key:
        return True
    found, current_value = _canonical_node_parameter_value(
        node_type,
        controlling_key,
        node_data,
    )
    return found and current_value == visible_when.get("equals")


def recommendation_matches_canonical_graph(
    task: AgentBuilderParameterTask,
    graph: dict[str, Any] | None,
) -> bool:
    if task.recommendation_fingerprint is None:
        return False
    node = next(
        (
            item
            for item in (graph or {}).get("nodes") or []
            if isinstance(item, dict)
            and str(item.get("id")) == task.node_id
            and str(item.get("type") or "") == task.node_type
        ),
        None,
    )
    data = node.get("data") if isinstance(node, dict) else None
    if not isinstance(data, dict):
        return False
    found, value = _canonical_node_parameter_value(
        task.node_type,
        task.parameter_key,
        data,
    )
    return found and (
        canonical_parameter_value_fingerprint(value)
        == task.recommendation_fingerprint
    )


def _deterministic_group_id(step_node_ids: dict[str, str]) -> UUID:
    identity = ";".join(f"{step}:{node}" for step, node in step_node_ids.items())
    return uuid5(NAMESPACE_URL, f"agent-builder-parameter-group:{identity}")


def _explicit_value_is_allowed(
    node_type: str,
    parameter: dict[str, Any],
    value: Any,
) -> bool:
    if parameter.get("sensitivity") != "safe":
        return False
    if parameter.get("input_type") in {"credential_ref", "resource_ref"}:
        return False
    redaction = TraceRedactionService.redact_payload(
        value,
        policy=TracePolicyService.fail_closed_redaction_policy(),
        payload_kind="agent_builder_explicit_parameter",
    )
    if redaction.failed or redaction.secret_detected:
        return False
    return not validate_node_parameter_value(
        node_type,
        str(parameter["key"]),
        value,
    )


def validate_direct_set_value(
    *,
    node_type: str,
    parameter_key: str,
    task_input_type: str,
    value: Any,
) -> list[str]:
    """Validate direct-edit values before they can become a graph patch."""
    parameter = parameter_definition(node_type, parameter_key)
    if parameter is None or parameter.get("input_type") != task_input_type:
        raise ParameterTaskSafetyError("catalog parameter mismatch")
    if (
        parameter.get("sensitivity") == "reference_only"
        and task_input_type not in {"credential_ref", "resource_ref"}
    ):
        raise ParameterTaskSafetyError("reference-only parameter mismatch")
    if (
        node_type == "llmNode"
        and parameter_key == "output_json_schema"
        and value is None
    ):
        return ["json_object_required"]

    if task_input_type == "secret":
        raise ParameterTaskSafetyError("secret_forbidden")

    redaction = TraceRedactionService.redact_payload(
        value,
        policy=TracePolicyService.fail_closed_redaction_policy(),
        payload_kind="agent_builder_direct_parameter",
    )
    if redaction.failed or redaction.secret_detected:
        raise ParameterTaskSafetyError("unsafe parameter value")

    return validate_node_parameter_value(node_type, parameter_key, value)


def _ordered_step_node_items(
    graph: dict[str, Any],
    step_node_ids: dict[str, str],
) -> list[tuple[str, str]]:
    """Order task-bearing nodes by graph topology with stable graph-order ties."""
    step_by_node_id: dict[str, str] = {}
    for step_id, node_id in step_node_ids.items():
        step_by_node_id.setdefault(str(node_id), str(step_id))

    graph_node_ids = [
        str(node.get("id"))
        for node in graph.get("nodes") or []
        if isinstance(node, dict) and node.get("id") is not None
    ]
    graph_index = {node_id: index for index, node_id in enumerate(graph_node_ids)}
    selected = set(step_by_node_id)
    adjacency = {node_id: set() for node_id in selected}
    indegree = {node_id: 0 for node_id in selected}
    for edge in graph.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if (
            source not in selected
            or target not in selected
            or source == target
            or target in adjacency[source]
        ):
            continue
        adjacency[source].add(target)
        indegree[target] += 1

    fallback_index = len(graph_index)

    def order_key(node_id: str) -> tuple[int, str]:
        return (graph_index.get(node_id, fallback_index), node_id)

    ready = sorted(
        (node_id for node_id, degree in indegree.items() if degree == 0),
        key=order_key,
    )
    ordered_node_ids: list[str] = []
    while ready:
        node_id = ready.pop(0)
        ordered_node_ids.append(node_id)
        for target in sorted(adjacency[node_id], key=order_key):
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
                ready.sort(key=order_key)

    ordered_set = set(ordered_node_ids)
    ordered_node_ids.extend(
        sorted((selected - ordered_set), key=order_key)
    )
    return [(step_by_node_id[node_id], node_id) for node_id in ordered_node_ids]


class ParameterTaskPlanner:
    def plan(
        self,
        *,
        graph: dict[str, Any],
        step_node_ids: dict[str, str],
        explicit_values: dict[tuple[str, str], Any],
        upstream_candidates: dict[tuple[str, str], list[list[str]]],
        guidance_hints: list[AgentBuilderParameterGuidanceHint],
        step_purposes: dict[str, str] | None = None,
        group_id: UUID | None = None,
        externally_managed_parameters: set[tuple[str, str]] | None = None,
        base_node_ids: set[str] | None = None,
        affected_node_ids: set[str] | None = None,
    ) -> ParameterTaskPlan:
        materialized = copy.deepcopy(graph)
        node_by_id = {
            str(node.get("id")): node
            for node in materialized.get("nodes") or []
            if isinstance(node, dict) and node.get("id")
        }
        group_id = group_id or _deterministic_group_id(step_node_ids)
        hints = {
            (hint.step_id, hint.parameter_key): hint for hint in guidance_hints
        }
        step_purposes = step_purposes or {}
        externally_managed_parameters = externally_managed_parameters or set()
        if base_node_ids is None:
            base_node_ids = set(node_by_id)
        tasks: list[AgentBuilderParameterTask] = []
        actionable_indexes: list[int] = []

        for step_id, node_id in _ordered_step_node_items(
            materialized, step_node_ids
        ):
            node = node_by_id.get(node_id)
            if node is None:
                raise ParameterTaskConflict("step node is missing")
            node_type = str(node.get("type") or "")
            if (
                affected_node_ids is not None
                and node_type == "llmNode"
                and str(node_id) not in affected_node_ids
            ):
                continue
            data = node.setdefault("data", {})
            if not isinstance(data, dict):
                raise ParameterTaskConflict("node data is invalid")
            for parameter in node_parameter_definitions(node_type):
                if parameter.get("agent_builder_task") is False:
                    continue
                parameter_key = str(parameter["key"])
                identity = (step_id, parameter_key)
                if identity in externally_managed_parameters:
                    continue
                parameter_is_visible = _parameter_is_visible(
                    node_type,
                    parameter,
                    data,
                )
                source = None
                if identity in explicit_values and _explicit_value_is_allowed(
                    node_type,
                    parameter,
                    explicit_values[identity],
                ):
                    node["data"] = apply_node_parameter_value(
                        node_type,
                        parameter_key,
                        data,
                        explicit_values[identity],
                    )
                    data = node["data"]
                    source = "user_request"
                elif (
                    str(node_id) in base_node_ids
                    and node_parameter_is_configured(node_type, parameter_key, data)
                ):
                    source = "existing_graph"
                else:
                    candidates = upstream_candidates.get(identity) or []
                    if (
                        parameter.get("input_type") == "variable_selector_list"
                        and candidates
                        and node_parameter_is_configured(
                            node_type, parameter_key, data
                        )
                        and data.get(parameter_key) == candidates
                    ):
                        source = "upstream_selector"
                    elif (
                        parameter.get("input_type") == "variable_selector_list"
                        and candidates
                    ):
                        node["data"] = apply_node_parameter_value(
                            node_type,
                            parameter_key,
                            data,
                            candidates,
                        )
                        data = node["data"]
                        source = "upstream_selector"
                    elif (
                        len(candidates) == 1
                        and node_parameter_is_configured(
                            node_type, parameter_key, data
                        )
                        and data.get(parameter_key) == candidates[0]
                    ):
                        source = "upstream_selector"
                    elif len(candidates) == 1:
                        node["data"] = apply_node_parameter_value(
                            node_type,
                            parameter_key,
                            data,
                            candidates[0],
                        )
                        data = node["data"]
                        source = "upstream_selector"
                    elif (
                        str(node_id) not in base_node_ids
                        and node_parameter_is_configured(
                            node_type,
                            parameter_key,
                            data,
                        )
                    ):
                        # Generated templates are safe catalog-owned recommendations.
                        # Preserve their topology-specific value and require confirmation.
                        source = "catalog_default"
                    elif (
                        "default" in parameter
                        and (
                            str(node_id) not in base_node_ids
                            or parameter.get("apply_default_to_existing", True)
                        )
                    ):
                        node["data"] = apply_node_parameter_value(
                            node_type,
                            parameter_key,
                            data,
                            parameter["default"],
                        )
                        data = node["data"]
                        source = "catalog_default"
                hint = hints.get(identity)
                label = str(parameter["label"])
                status = "pending"
                recommendation_fingerprint = None
                requires_explicit_confirmation = False
                if source is not None:
                    found, recommendation_value = _canonical_node_parameter_value(
                        node_type,
                        parameter_key,
                        data,
                    )
                    if not found:
                        raise ParameterTaskConflict(
                            "automatic parameter value is missing"
                        )
                    if parameter.get("input_type") != "secret":
                        recommendation_fingerprint = (
                            canonical_parameter_value_fingerprint(recommendation_value)
                        )
                    requires_explicit_confirmation = (
                        node_type == "llmNode"
                        and parameter_key == "auto_model_routing"
                        and source == "catalog_default"
                        and recommendation_value is False
                    )
                    status = (
                        "pending" if requires_explicit_confirmation else "completed"
                    )
                if not parameter_is_visible:
                    status = "skipped"
                    source = None
                    recommendation_fingerprint = None
                    requires_explicit_confirmation = False
                task = AgentBuilderParameterTask(
                    task_id=uuid5(
                        NAMESPACE_URL,
                        f"{group_id}:{node_id}:{parameter_key}",
                    ),
                    group_id=group_id,
                    step_id=step_id,
                    node_id=node_id,
                    node_type=node_type,
                    parameter_key=parameter_key,
                    task_group=(
                        str(parameter["task_group"])
                        if parameter.get("task_group")
                        else None
                    ),
                    label=label,
                    input_type=str(parameter["input_type"]),
                    required=node_parameter_is_required(
                        node_type,
                        parameter_key,
                        data,
                    ),
                    confirmation_required=requires_explicit_confirmation,
                    defer_policy=str(parameter.get("defer_policy") or "forbidden"),
                    status=status,
                    task_version=1,
                    stable_order=len(tasks),
                    resolution_source=source,
                    recommendation_fingerprint=recommendation_fingerprint,
                    reason=(
                        str(parameter.get("reason"))
                        if parameter.get("reason")
                        else hint.reason
                        if hint is not None
                        else f"{label} 설정이 필요합니다."
                    ),
                    input_guidance=(
                        str(parameter.get("input_guidance"))
                        if parameter.get("input_guidance")
                        else hint.input_guidance
                        if hint is not None
                        else f"{label} 값을 입력하세요."
                    ),
                    node_label=str(data.get("title") or node_id),
                    node_purpose=str(step_purposes.get(step_id) or ""),
                    validation=copy.deepcopy(parameter.get("validation") or {}),
                    sensitivity=str(parameter.get("sensitivity") or "safe"),
                )
                tasks.append(task)
                if task.status == "pending":
                    actionable_indexes.append(len(tasks) - 1)

        for node_id, node in node_by_id.items():
            if str(node.get("type") or "") != "llmNode":
                continue
            data = node.get("data") if isinstance(node.get("data"), dict) else {}
            if any(
                node_parameter_is_configured("llmNode", parameter_key, data)
                for parameter_key in _LLM_PROMPT_PARAMETER_KEYS
            ):
                continue
            prompt_indexes = [
                index
                for index, task in enumerate(tasks)
                if task.node_id == node_id
                and task.parameter_key in _LLM_PROMPT_PARAMETER_KEYS
            ]
            if not prompt_indexes:
                continue
            for prompt_index in prompt_indexes:
                tasks[prompt_index] = tasks[prompt_index].model_copy(
                    update={
                        "status": "pending",
                        "resolution_source": None,
                        "recommendation_fingerprint": None,
                    }
                )
                if prompt_index not in actionable_indexes:
                    actionable_indexes.append(prompt_index)

        actionable_indexes.sort()
        if actionable_indexes:
            first = actionable_indexes[0]
            tasks[first] = tasks[first].model_copy(update={"status": "active"})
        configuration_by_node = {
            node_id: derive_node_configuration_state(
                str(node.get("type") or ""),
                node.get("data") if isinstance(node.get("data"), dict) else {},
            )
            for node_id, node in node_by_id.items()
        }
        tasks = [
            task.model_copy(
                update={
                    "configuration_state": configuration_by_node.get(
                        task.node_id,
                        "unresolved",
                    )
                }
            )
            for task in tasks
        ]
        return ParameterTaskPlan(group_id=group_id, graph=materialized, tasks=tasks)


def refresh_parameter_group_configuration(
    group: AgentBuilderParameterGroup | None,
    graph: dict[str, Any] | None,
) -> AgentBuilderParameterGroup | None:
    if group is None:
        return None
    node_by_id = {
        str(node.get("id")): node
        for node in (graph or {}).get("nodes") or []
        if isinstance(node, dict) and node.get("id")
    }
    tasks = []
    for task in group.tasks:
        node = node_by_id.get(task.node_id)
        state = "unresolved"
        if node is not None:
            state = derive_node_configuration_state(
                str(node.get("type") or ""),
                node.get("data") if isinstance(node.get("data"), dict) else {},
            )
        tasks.append(task.model_copy(update={"configuration_state": state}))
    return group.model_copy(update={"tasks": tasks})


def reconcile_parameter_group_catalog_tasks(
    group: AgentBuilderParameterGroup,
    catalog_tasks: list[AgentBuilderParameterTask],
) -> AgentBuilderParameterGroup:
    """Merge current Catalog tasks without changing persisted task order."""
    if group.status in {"pending_save", "pending_ack", "blocked", "canceled"}:
        return group

    existing_by_identity = {
        (task.node_id, task.parameter_key): task for task in group.tasks
    }
    catalog_tasks_by_identity = {
        (task.node_id, task.parameter_key): task for task in catalog_tasks
    }
    planned_identities = set(catalog_tasks_by_identity)
    existing_identities_in_order: list[tuple[str, str]] = []
    seen_existing_identities: set[tuple[str, str]] = set()
    for task in sorted(group.tasks, key=lambda task: task.stable_order):
        identity = (task.node_id, task.parameter_key)
        if (
            identity not in catalog_tasks_by_identity
            or identity in seen_existing_identities
        ):
            continue
        seen_existing_identities.add(identity)
        existing_identities_in_order.append(identity)
    ordered_catalog_tasks = [
        catalog_tasks_by_identity[identity]
        for identity in existing_identities_in_order
    ]
    ordered_catalog_tasks.extend(
        task
        for task in sorted(catalog_tasks, key=lambda task: task.stable_order)
        if (task.node_id, task.parameter_key) not in existing_by_identity
    )
    merged: list[AgentBuilderParameterTask] = []
    priority_reopen_identity: tuple[str, str] | None = None
    next_stable_order = max(
        (task.stable_order for task in group.tasks),
        default=-1,
    ) + 1

    for planned in ordered_catalog_tasks:
        identity = (planned.node_id, planned.parameter_key)
        existing = existing_by_identity.get(identity)
        if existing is None:
            merged.append(
                planned.model_copy(
                    update={
                        "group_id": group.group_id,
                        "stable_order": next_stable_order,
                    }
                )
            )
            next_stable_order += 1
            continue
        status = existing.status
        mode_visibility_changed = (
            planned.status == "skipped" and existing.status != "skipped"
        ) or (
            planned.status in {"pending", "active"}
            and existing.status == "skipped"
            and isinstance(planned.validation.get("visible_when"), dict)
        )
        required_task_reopened = (
            planned.required
            and planned.status in {"pending", "active"}
            and existing.status == "skipped"
        )
        legacy_unconfirmed_disabled_routing = (
            existing.node_type == "llmNode"
            and existing.parameter_key == "auto_model_routing"
            and existing.status == "completed"
            and existing.task_version == 1
            and existing.resolution_source == "catalog_default"
            and existing.recommendation_fingerprint
            == canonical_parameter_value_fingerprint(False)
        )
        matching_recommendation = (
            planned.status == "completed"
            and existing.status in {"pending", "active"}
            and planned.resolution_source is not None
            and existing.resolution_source == planned.resolution_source
            and existing.recommendation_fingerprint
            == planned.recommendation_fingerprint
        )
        secret_configuration_completed = (
            planned.input_type == "secret"
            and planned.status == "completed"
            and existing.status in {"pending", "active", "invalid", "skipped"}
        )
        secret_configuration_removed = (
            planned.input_type == "secret"
            and planned.status != "completed"
            and existing.status == "completed"
        )
        if required_task_reopened:
            status = planned.status
            if priority_reopen_identity is None:
                priority_reopen_identity = identity
        elif mode_visibility_changed:
            status = planned.status
        elif legacy_unconfirmed_disabled_routing:
            status = "pending"
            if priority_reopen_identity is None:
                priority_reopen_identity = identity
        elif secret_configuration_completed:
            status = "completed"
        elif secret_configuration_removed:
            status = "pending"
            if priority_reopen_identity is None:
                priority_reopen_identity = identity
        elif matching_recommendation:
            status = "completed"
        resolution_source = (
            planned.resolution_source
            if secret_configuration_completed or secret_configuration_removed
            else existing.resolution_source
        )
        recommendation_fingerprint = (
            None
            if planned.input_type == "secret"
            else existing.recommendation_fingerprint
        )
        merged.append(
            planned.model_copy(
                update={
                    "task_id": existing.task_id,
                    "group_id": group.group_id,
                    "status": status,
                    "task_version": existing.task_version,
                    "stable_order": existing.stable_order,
                    "resolution_source": resolution_source,
                    "recommendation_fingerprint": recommendation_fingerprint,
                    "suggestions": planned.suggestions or existing.suggestions,
                    "candidates": existing.candidates,
                }
            )
        )

    for existing in sorted(group.tasks, key=lambda task: task.stable_order):
        if (existing.node_id, existing.parameter_key) in planned_identities:
            continue
        definition = parameter_definition(
            existing.node_type,
            existing.parameter_key,
        )
        if definition is not None and definition.get("agent_builder_task") is False:
            continue
        merged.append(existing)

    merged.sort(key=lambda task: task.stable_order)

    if not merged:
        return group.model_copy(update={"status": "completed", "tasks": []})
    if priority_reopen_identity is not None:
        merged = [
            task.model_copy(update={"status": "active"})
            if (task.node_id, task.parameter_key) == priority_reopen_identity
            else task.model_copy(update={"status": "pending"})
            if task.status == "active"
            else task
            for task in merged
        ]
    active_indexes = [
        index for index, task in enumerate(merged) if task.status == "active"
    ]
    if len(active_indexes) > 1:
        existing_active_identities = {
            (task.node_id, task.parameter_key)
            for task in group.tasks
            if task.status == "active"
        }
        preserved_active = next(
            (
                index
                for index in active_indexes
                if (
                    merged[index].node_id,
                    merged[index].parameter_key,
                )
                in existing_active_identities
            ),
            active_indexes[0],
        )
        merged = [
            task.model_copy(update={"status": "pending"})
            if index in active_indexes and index != preserved_active
            else task
            for index, task in enumerate(merged)
        ]
    if not any(task.status in {"active", "invalid"} for task in merged):
        next_pending = next(
            (index for index, task in enumerate(merged) if task.status == "pending"),
            None,
        )
        if next_pending is not None:
            merged[next_pending] = merged[next_pending].model_copy(
                update={"status": "active"}
            )

    terminal_statuses = {"completed", "skipped", "deferred"}
    status = (
        "completed"
        if all(task.status in terminal_statuses for task in merged)
        else "active"
    )
    return group.model_copy(update={"status": status, "tasks": merged})


def remove_direct_edit_knowledge_parameter_tasks(
    group: AgentBuilderParameterGroup | None,
) -> AgentBuilderParameterGroup | None:
    """Remove persisted generic KB tasks superseded by direct knowledge resolution."""
    if group is None:
        return None
    if group.status in {"pending_save", "pending_ack"}:
        return group
    tasks = [
        task
        for task in group.tasks
        if not (task.node_type == "llmNode" and task.parameter_key == "knowledgeBases")
    ]
    if len(tasks) == len(group.tasks):
        return group

    normalized = group.model_copy(update={"tasks": tasks})
    if group.status != "active" or any(task.status == "active" for task in tasks):
        return normalized

    terminal_statuses = {"completed", "skipped", "deferred"}
    if all(task.status in terminal_statuses for task in tasks):
        return normalized.model_copy(update={"status": "completed"})

    next_pending_index = next(
        (
            index
            for index, task in sorted(
                enumerate(tasks), key=lambda item: item[1].stable_order
            )
            if task.status == "pending"
        ),
        None,
    )
    if next_pending_index is None:
        return normalized
    tasks[next_pending_index] = tasks[next_pending_index].model_copy(
        update={"status": "active"}
    )
    return normalized.model_copy(update={"tasks": tasks})


def remove_direct_edit_external_credential_tasks(
    group: AgentBuilderParameterGroup | None,
) -> AgentBuilderParameterGroup | None:
    """Drop legacy Slack/GitHub credential tasks from direct-edit sessions."""
    if group is None or group.status in {"pending_save", "pending_ack"}:
        return group
    tasks = [
        task
        for task in group.tasks
        if not (
            task.input_type == "credential_ref"
            and task.node_type in {"slackPostNode", "githubNode"}
        )
    ]
    if len(tasks) == len(group.tasks):
        return group

    normalized = group.model_copy(update={"tasks": tasks})
    if group.status != "active" or any(task.status == "active" for task in tasks):
        return normalized

    terminal_statuses = {"completed", "skipped", "deferred"}
    if all(task.status in terminal_statuses for task in tasks):
        return normalized.model_copy(update={"status": "completed"})

    next_pending_index = next(
        (
            index
            for index, task in sorted(
                enumerate(tasks), key=lambda item: item[1].stable_order
            )
            if task.status == "pending"
        ),
        None,
    )
    if next_pending_index is None:
        return normalized
    tasks[next_pending_index] = tasks[next_pending_index].model_copy(
        update={"status": "active"}
    )
    return normalized.model_copy(update={"tasks": tasks})


def refresh_parameter_group_suggestions(
    group: AgentBuilderParameterGroup | None,
    graph: dict[str, Any] | None,
) -> AgentBuilderParameterGroup | None:
    if group is None:
        return None
    canonical_graph = graph or {"nodes": [], "edges": []}
    node_by_id = {
        str(node.get("id")): node
        for node in canonical_graph.get("nodes") or []
        if isinstance(node, dict) and node.get("id")
    }
    resolver = ParameterSuggestionResolver()
    tasks = [task.model_copy(deep=True) for task in group.tasks]
    invalid_indexes: list[int] = []
    for index, task in enumerate(tasks):
        if task.input_type not in {"variable_selector", "variable_selector_list"}:
            continue
        try:
            suggestions = resolver.resolve(
                graph=canonical_graph,
                target_node_id=task.node_id,
                parameter_key=task.parameter_key,
            )
        except ParameterSuggestionError:
            suggestions = []
        updated = task.model_copy(update={"suggestions": suggestions})
        node = node_by_id.get(task.node_id)
        data = node.get("data") if isinstance(node, dict) else {}
        found, selected = node_parameter_value(
            task.node_type,
            task.parameter_key,
            data if isinstance(data, dict) else {},
        )
        if not found:
            selected = None
        valid_selectors = [item.value_selector for item in suggestions]
        selection_is_valid = (
            selected in valid_selectors
            if task.input_type == "variable_selector"
            else isinstance(selected, list)
            and bool(selected)
            and all(selector in valid_selectors for selector in selected)
            and len({tuple(selector) for selector in selected}) == len(selected)
        )
        if (
            task.status == "completed"
            and task.resolution_source == "upstream_selector"
            and not selection_is_valid
        ):
            updated = updated.model_copy(
                update={
                    "status": "invalid",
                    "task_version": task.task_version + 1,
                }
            )
            invalid_indexes.append(index)
        tasks[index] = updated

    if invalid_indexes:
        first_invalid = invalid_indexes[0]
        tasks = [
            task.model_copy(update={"status": "pending"})
            if task.status == "active" and index != first_invalid
            else task
            for index, task in enumerate(tasks)
        ]
        tasks[first_invalid] = tasks[first_invalid].model_copy(
            update={"status": "active"}
        )
        return group.model_copy(update={"status": "active", "tasks": tasks})
    return group.model_copy(update={"tasks": tasks})


def _task_index(tasks: list[AgentBuilderParameterTask], task_id: UUID) -> int:
    for index, task in enumerate(tasks):
        if task.task_id == task_id:
            return index
    raise ParameterTaskConflict("task is missing")


def _ensure_required_any_configuration_remains(
    tasks: list[AgentBuilderParameterTask],
    task: AgentBuilderParameterTask,
) -> None:
    for group_key, parameter_keys in required_any_configuration_groups(
        task.node_type
    ):
        if task.parameter_key not in parameter_keys:
            continue
        siblings = [
            item
            for item in tasks
            if item.node_id == task.node_id
            and item.task_id != task.task_id
            and item.parameter_key in parameter_keys
        ]
        has_configured = any(item.status == "completed" for item in siblings)
        has_remaining = any(
            item.status in {"active", "pending", "invalid"} for item in siblings
        )
        if not has_configured and not has_remaining:
            raise ParameterTaskConflict(f"one {group_key} is required")


def prepare_task_decision(
    *,
    tasks: list[AgentBuilderParameterTask],
    task_id: UUID,
    operation_id: UUID,
    expected_task_version: int,
    action: Literal["set", "clear", "confirm", "defer", "skip", "previous"],
    value: Any,
) -> PreparedParameterDecision:
    index = _task_index(tasks, task_id)
    task = tasks[index]
    if task.task_version != expected_task_version:
        raise ParameterTaskConflict("task version conflict")
    if action == "set":
        if task.status not in {"active", "completed", "skipped", "deferred", "invalid"}:
            raise ParameterTaskConflict("task is not settable")
        if value is None:
            raise ParameterTaskConflict("set value is required")
        return PreparedParameterDecision(
            operation_id=operation_id,
            task_id=task_id,
            action=action,
            expected_task_version=expected_task_version,
            awaiting_persistence_ack=True,
            graph_data_patch={task.parameter_key: copy.deepcopy(value)},
        )
    if action == "clear":
        if task.status not in {
            "active",
            "completed",
            "skipped",
            "deferred",
            "invalid",
        }:
            raise ParameterTaskConflict("task is not clearable")
        if task.required:
            raise ParameterTaskConflict("required task cannot be cleared")
        if (
            task.node_type == "llmNode"
            and task.parameter_key in _LLM_PROMPT_PARAMETER_KEYS
        ):
            other_prompts = [
                item
                for item in tasks
                if item.node_id == task.node_id
                and item.task_id != task.task_id
                and item.parameter_key in _LLM_PROMPT_PARAMETER_KEYS
            ]
            empty_fingerprint = canonical_parameter_value_fingerprint("")
            has_configured_prompt = any(
                item.status == "completed"
                and (
                    item.resolution_source == "user_request"
                    or item.recommendation_fingerprint
                    not in {None, empty_fingerprint}
                )
                for item in other_prompts
            )
            has_remaining_prompt = any(
                item.status in {"active", "pending"} for item in other_prompts
            )
            if not has_configured_prompt and not has_remaining_prompt:
                raise ParameterTaskConflict("one prompt is required")
        _ensure_required_any_configuration_remains(tasks, task)
        return PreparedParameterDecision(
            operation_id=operation_id,
            task_id=task_id,
            action=action,
            expected_task_version=expected_task_version,
            awaiting_persistence_ack=True,
            graph_data_patch={"_clear_parameter": task.parameter_key},
        )
    if action == "previous":
        if task.status not in {"active", "completed", "skipped", "deferred"}:
            raise ParameterTaskConflict("task is not reopenable")
        previous_reopenable_task_id(tasks, task_id)
        return PreparedParameterDecision(
            operation_id=operation_id,
            task_id=task_id,
            action=action,
            expected_task_version=expected_task_version,
            awaiting_persistence_ack=False,
            graph_data_patch=None,
        )
    if task.status != "active":
        raise ParameterTaskConflict("task is not active")
    if action == "confirm":
        if task.resolution_source is None:
            raise ParameterTaskConflict("task has no automatic resolution")
        return PreparedParameterDecision(
            operation_id=operation_id,
            task_id=task_id,
            action=action,
            expected_task_version=expected_task_version,
            awaiting_persistence_ack=False,
            graph_data_patch=None,
        )
    if action == "defer":
        if task.defer_policy != "allow_unresolved":
            raise ParameterTaskConflict("defer is forbidden")
        return PreparedParameterDecision(
            operation_id=operation_id,
            task_id=task_id,
            action=action,
            expected_task_version=expected_task_version,
            awaiting_persistence_ack=True,
            graph_data_patch={"_deferred_parameter": task.parameter_key},
        )
    if action == "skip":
        if task.confirmation_required:
            raise ParameterTaskConflict("confirmation is required")
        if task.required:
            raise ParameterTaskConflict("required task cannot be skipped")
        if (
            task.node_type == "llmNode"
            and task.parameter_key in _LLM_PROMPT_PARAMETER_KEYS
        ):
            other_prompts = [
                item
                for item in tasks
                if item.node_id == task.node_id
                and item.task_id != task.task_id
                and item.parameter_key in _LLM_PROMPT_PARAMETER_KEYS
            ]
            empty_fingerprint = canonical_parameter_value_fingerprint("")
            has_configured_prompt = any(
                item.status == "completed"
                and (
                    item.resolution_source == "user_request"
                    or item.recommendation_fingerprint not in {None, empty_fingerprint}
                )
                for item in other_prompts
            )
            has_remaining_prompt = any(
                item.status in {"active", "pending"} for item in other_prompts
            )
            if not has_configured_prompt and not has_remaining_prompt:
                raise ParameterTaskConflict("one prompt is required")
        _ensure_required_any_configuration_remains(tasks, task)
        return PreparedParameterDecision(
            operation_id=operation_id,
            task_id=task_id,
            action=action,
            expected_task_version=expected_task_version,
            awaiting_persistence_ack=False,
            graph_data_patch=None,
        )
    raise ParameterTaskConflict("invalid decision")


def recovery_affected_node_ids(
    *,
    graph: dict[str, Any],
    envelopes: list[dict[str, Any]],
    parameter_task_node_ids: list[str],
) -> set[str]:
    """Collect recoverable task nodes without widening beyond the canonical graph."""
    canonical_node_ids = {
        str(node.get("id"))
        for node in graph.get("nodes") or []
        if isinstance(node, dict) and node.get("id")
    }
    affected_node_ids = {
        str(node_id)
        for envelope in envelopes
        if isinstance(envelope, dict)
        and envelope.get("catalog_version") == 3
        and envelope.get("status") != "reverted"
        for node_id in envelope.get("affected_node_ids") or []
        if node_id
    }
    affected_node_ids.update(
        str(node_id) for node_id in parameter_task_node_ids if node_id
    )
    return affected_node_ids & canonical_node_ids


def _activate_next(
    tasks: list[AgentBuilderParameterTask], completed_index: int
) -> list[AgentBuilderParameterTask]:
    if any(task.status == "active" for task in tasks):
        return tasks
    for index in range(completed_index + 1, len(tasks)):
        if tasks[index].status == "pending":
            tasks[index] = tasks[index].model_copy(update={"status": "active"})
            break
    return tasks


def previous_reopenable_task_id(
    tasks: list[AgentBuilderParameterTask],
    current_task_id: UUID,
) -> UUID:
    """Resolve UI navigation without changing canonical task completion state."""
    index = _task_index(tasks, current_task_id)
    stable_indexes = sorted(
        range(len(tasks)),
        key=lambda task_index: tasks[task_index].stable_order,
    )
    stable_position = stable_indexes.index(index)
    for previous_index in reversed(stable_indexes[:stable_position]):
        previous = tasks[previous_index]
        if previous.status in {"completed", "skipped", "deferred"}:
            return previous.task_id
    raise ParameterTaskConflict("previous task is missing")


def acknowledge_parameter_binding(
    tasks: list[AgentBuilderParameterTask],
    *,
    affected_node_ids: set[str],
    parameter_key: str,
) -> list[AgentBuilderParameterTask]:
    updated = [task.model_copy(deep=True) for task in tasks]
    changed = False
    for index, task in enumerate(updated):
        if (
            task.node_id not in affected_node_ids
            or task.parameter_key != parameter_key
            or task.status in {"completed", "canceled"}
        ):
            continue
        updated[index] = task.model_copy(
            update={
                "status": "completed",
                "task_version": task.task_version + 1,
                "resolution_source": "user_request",
            }
        )
        changed = True
    if not changed or any(task.status == "active" for task in updated):
        return updated
    next_index = next(
        (
            index
            for index, task in sorted(
                enumerate(updated), key=lambda item: item[1].stable_order
            )
            if task.status == "pending"
        ),
        None,
    )
    if next_index is not None:
        updated[next_index] = updated[next_index].model_copy(
            update={"status": "active"}
        )
    return updated


def apply_local_task_decision(
    tasks: list[AgentBuilderParameterTask],
    decision: PreparedParameterDecision,
) -> list[AgentBuilderParameterTask]:
    if decision.awaiting_persistence_ack:
        raise ParameterTaskConflict("persistence acknowledgement is required")
    updated = [task.model_copy(deep=True) for task in tasks]
    index = _task_index(updated, decision.task_id)
    task = updated[index]
    if decision.action == "skip":
        updated[index] = task.model_copy(
            update={"status": "skipped", "task_version": task.task_version + 1}
        )
        return _activate_next(updated, index)
    if decision.action == "confirm":
        if task.resolution_source is None:
            raise ParameterTaskConflict("task has no automatic resolution")
        updated[index] = task.model_copy(
            update={"status": "completed", "task_version": task.task_version + 1}
        )
        return _activate_next(updated, index)
    if decision.action == "previous":
        previous_reopenable_task_id(updated, decision.task_id)
        return updated
    raise ParameterTaskConflict("decision is not local")


def acknowledge_task_decision(
    tasks: list[AgentBuilderParameterTask],
    decision: PreparedParameterDecision,
) -> list[AgentBuilderParameterTask]:
    if not decision.awaiting_persistence_ack:
        raise ParameterTaskConflict("decision does not require acknowledgement")
    updated = [task.model_copy(deep=True) for task in tasks]
    index = _task_index(updated, decision.task_id)
    task = updated[index]
    if task.task_version != decision.expected_task_version:
        raise ParameterTaskConflict("task version conflict")
    status = (
        "deferred"
        if decision.action == "defer"
        else "skipped"
        if decision.action == "clear"
        else "completed"
    )
    resolution_source = task.resolution_source
    if decision.action == "set":
        resolution_source = "user_request"
    elif decision.action == "clear":
        resolution_source = None
    updated[index] = task.model_copy(
        update={
            "status": status,
            "task_version": task.task_version + 1,
            "resolution_source": resolution_source,
            "recommendation_fingerprint": (
                None
                if decision.action in {"set", "clear"}
                else task.recommendation_fingerprint
            ),
        }
    )
    return _activate_next(updated, index)


def cancel_parameter_group(
    group: AgentBuilderParameterGroup,
    *,
    expected_task_id: UUID,
    expected_task_version: int,
) -> AgentBuilderParameterGroup:
    active = next((task for task in group.tasks if task.status == "active"), None)
    if active is None or active.task_id != expected_task_id:
        raise ParameterTaskConflict("active task conflict")
    if active.task_version != expected_task_version:
        raise ParameterTaskConflict("task version conflict")
    tasks = [
        task
        if task.status in {"completed", "skipped", "deferred", "canceled"}
        else task.model_copy(
            update={"status": "canceled", "task_version": task.task_version + 1}
        )
        for task in group.tasks
    ]
    return group.model_copy(update={"status": "canceled", "tasks": tasks})
