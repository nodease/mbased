from __future__ import annotations

import hashlib
from collections import deque
from typing import Any

from apps.shared.schemas.agent_builder import AgentBuilderParameterSuggestion
from apps.shared.services.workflow_node_catalog import node_definition


class ParameterSuggestionError(ValueError):
    pass


def _upstream_distances(
    graph: dict[str, Any], target_node_id: str
) -> dict[str, int]:
    incoming: dict[str, list[str]] = {}
    for edge in graph.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        incoming.setdefault(str(edge.get("target")), []).append(
            str(edge.get("source"))
        )
    distances: dict[str, int] = {}
    queue = deque([(target_node_id, 0)])
    while queue:
        current, distance = queue.popleft()
        for source in sorted(incoming.get(current, [])):
            candidate_distance = distance + 1
            if source in distances and distances[source] <= candidate_distance:
                continue
            distances[source] = candidate_distance
            queue.append((source, candidate_distance))
    distances.pop(target_node_id, None)
    return distances


def _output_items(node: dict[str, Any]) -> list[tuple[str, str, str]]:
    definition = node_definition(str(node.get("type") or ""))
    if definition is None:
        return []
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    result: list[tuple[str, str, str]] = []
    for contract in definition.get("outputs") or []:
        value_type = str(contract.get("value_type") or "unknown")
        if contract.get("mode") == "static":
            result.extend(
                (str(key), value_type, "$") for key in contract.get("keys") or []
            )
            continue
        parameter_values = data.get(str(contract.get("parameter_key")))
        if not isinstance(parameter_values, list):
            continue
        name_key = str(contract.get("name_key") or "")
        for item in parameter_values:
            if not isinstance(item, dict) or not item.get(name_key):
                continue
            json_path = str(item.get("json_path") or "$")
            result.append((str(item[name_key]), value_type, json_path))
    return list(dict.fromkeys(result))


def _suggestion_id(
    target_node_id: str,
    parameter_key: str,
    source_node_id: str,
    output_key: str,
) -> str:
    raw = f"{target_node_id}:{parameter_key}:{source_node_id}:{output_key}"
    return "sel-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


class ParameterSuggestionResolver:
    def resolve(
        self,
        *,
        graph: dict[str, Any],
        target_node_id: str,
        parameter_key: str,
    ) -> list[AgentBuilderParameterSuggestion]:
        nodes = {
            str(node.get("id")): node
            for node in graph.get("nodes") or []
            if isinstance(node, dict) and node.get("id")
        }
        target = nodes.get(target_node_id)
        if target is None:
            raise ParameterSuggestionError("target node not found")
        target_definition = node_definition(str(target.get("type") or ""))
        allowed_parameters = {
            str(item.get("key"))
            for item in (target_definition or {}).get("parameters") or []
        }
        if parameter_key not in allowed_parameters:
            raise ParameterSuggestionError("target parameter not found")
        distances = _upstream_distances(graph, target_node_id)
        suggestions: list[tuple[int, AgentBuilderParameterSuggestion]] = []
        for source_node_id, distance in distances.items():
            source = nodes.get(source_node_id)
            if source is None:
                continue
            for output_key, value_type, json_path in _output_items(source):
                title = str((source.get("data") or {}).get("title") or source_node_id)
                suggestion = AgentBuilderParameterSuggestion(
                    suggestion_id=_suggestion_id(
                        target_node_id,
                        parameter_key,
                        source_node_id,
                        output_key,
                    ),
                    label=f"{title} · {output_key}",
                    description=f"{title} node의 {output_key} 출력을 사용합니다.",
                    source_node_id=source_node_id,
                    output_key=output_key,
                    value_type=value_type,
                    value_selector=[source_node_id, output_key],
                    json_path=json_path if json_path.startswith("$") else f"$.{json_path}",
                )
                suggestions.append((distance, suggestion))
        suggestions.sort(
            key=lambda item: (
                item[0],
                item[1].source_node_id,
                item[1].output_key,
            )
        )
        return [suggestion for _, suggestion in suggestions]

    def validate_selection(
        self,
        *,
        graph: dict[str, Any],
        target_node_id: str,
        parameter_key: str,
        suggestion_id: str,
        value_selector: list[str],
    ) -> AgentBuilderParameterSuggestion:
        suggestions = self.resolve(
            graph=graph,
            target_node_id=target_node_id,
            parameter_key=parameter_key,
        )
        suggestion = next(
            (item for item in suggestions if item.suggestion_id == suggestion_id),
            None,
        )
        if suggestion is None or suggestion.value_selector != value_selector:
            raise ParameterSuggestionError("suggestion selection mismatch")
        return suggestion
