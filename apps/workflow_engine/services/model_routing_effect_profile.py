"""LLM 라우팅 학습에 사용할 후속 workflow 동작의 안전한 구조 요약."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from apps.shared.services.workflow_node_catalog import node_side_effect_mapping


_CUSTOMER_OUTPUT_NODE_TYPES = frozenset(
    {"answerNode", "gmailDraftNode", "slackPostNode"}
)


def build_model_routing_effect_profiles(
    node_schemas: Mapping[str, Any],
    adjacency: Mapping[str, list[str]],
) -> dict[str, dict[str, Any]]:
    """각 LLM 노드 뒤에서 발생할 수 있는 동작만 원문 없이 요약한다."""

    side_effects = node_side_effect_mapping()
    profiles: dict[str, dict[str, Any]] = {}
    for node_id, schema in node_schemas.items():
        if _schema_value(schema, "type") != "llmNode":
            continue
        descendants = _descendants(node_id, adjacency)
        external_write_count = 0
        external_read_reachable = False
        local_execution_reachable = False
        control_gate_present = False
        customer_output_reachable = False
        for descendant_id in descendants:
            descendant = node_schemas.get(descendant_id)
            if descendant is None:
                continue
            node_type = str(_schema_value(descendant, "type") or "")
            data = _schema_data(descendant)
            effect = _effective_side_effect(
                node_type,
                data,
                side_effects.get(node_type, "external_write"),
            )
            external_write_count += int(effect == "external_write")
            external_read_reachable = external_read_reachable or effect == "external_read"
            local_execution_reachable = (
                local_execution_reachable or effect == "local_execution"
            )
            control_gate_present = control_gate_present or node_type == "conditionNode"
            customer_output_reachable = (
                customer_output_reachable or node_type in _CUSTOMER_OUTPUT_NODE_TYPES
            )

        source_context = _schema_data(schema).get("model_routing_context")
        source_context = source_context if isinstance(source_context, Mapping) else {}
        external_write_reachable = external_write_count > 0
        explicitly_reversible = bool(source_context.get("effects_reversible"))
        profiles[str(node_id)] = {
            "control_gate_present": control_gate_present,
            "customer_output_reachable": bool(
                customer_output_reachable or source_context.get("customer_facing")
            ),
            "downstream_contract_required": bool(descendants),
            "external_read_reachable": external_read_reachable,
            "external_write_reachable": external_write_reachable,
            "human_approval_required": bool(
                source_context.get("human_approval_required")
            ),
            # Catalog에 가역성 계약이 없는 external write는 안전하게 비가역 가능으로 본다.
            "irreversible_effect_possible": bool(
                external_write_reachable and not explicitly_reversible
            ),
            "local_execution_reachable": local_execution_reachable,
            "reachable_effect_count": external_write_count,
        }
    return profiles


def _descendants(
    source_node_id: str,
    adjacency: Mapping[str, list[str]],
) -> set[str]:
    pending = list(adjacency.get(source_node_id, ()))
    result: set[str] = set()
    while pending:
        node_id = pending.pop()
        if node_id in result or node_id == source_node_id:
            continue
        result.add(node_id)
        pending.extend(adjacency.get(node_id, ()))
    return result


def _effective_side_effect(
    node_type: str,
    data: Mapping[str, Any],
    catalog_effect: str,
) -> str:
    if node_type == "httpRequestNode" and str(data.get("method", "GET")).upper() == "GET":
        return "external_read"
    if node_type == "githubNode" and data.get("action", "get_pr") == "get_pr":
        return "external_read"
    return catalog_effect


def _schema_value(schema: Any, key: str) -> Any:
    if isinstance(schema, Mapping):
        return schema.get(key)
    return getattr(schema, key, None)


def _schema_data(schema: Any) -> Mapping[str, Any]:
    value = _schema_value(schema, "data")
    return value if isinstance(value, Mapping) else {}
