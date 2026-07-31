"""Shared graph boundary for the dedicated Slack delivery node."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import urlsplit

from apps.shared.services.workflow_node_secret_service import (
    is_workflow_node_secret_reference,
)

SLACK_GRAPH_CONFIGURATION_INVALID = "slack.graph_configuration_invalid"
SLACK_LEGACY_SELECTOR_REQUIRES_MIGRATION = "slack.legacy_selector_requires_migration"
_SLACK_WEBHOOK_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_SLACK_TEMPLATE_TOKEN_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]{0,127})\s*\}\}")
_SLACK_UNSAFE_TEMPLATE_MARKERS = ("{{", "{%", "%}", "{#", "#}")
_SLACK_DEFERRED_PARAMETER_KEYS = frozenset({"channel"})
_SLACK_ALLOWED_FIELDS = frozenset(
    {
        "title",
        "description",
        "parameters",
        "slackMode",
        "channel",
        "message",
        "blocks",
        "attachments",
        "thread_ts",
        "username",
        "icon_emoji",
        "referenced_variables",
        # Legacy compatibility fields. They are not request sources.
        "url",
        "authConfig",
        "method",
        "headers",
        "body",
        "timeout",
        "authType",
        "configuration_state",
        "channel_resolution_state",
        "_deferred_parameters",
        "displayNumber",
        "visibleProperties",
    }
)


class SlackGraphBoundaryError(ValueError):
    def __init__(self, reason_code: str = SLACK_GRAPH_CONFIGURATION_INVALID) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def is_valid_commercial_slack_webhook_url(value: Any) -> bool:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or len(value.encode("utf-8")) > 2048
        or not value.startswith("https://hooks.slack.com/services/")
    ):
        return False
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return False
    path_parts = parts.path.split("/")
    return (
        parts.scheme == "https"
        and parts.hostname == "hooks.slack.com"
        and parts.netloc == "hooks.slack.com"
        and port is None
        and not parts.username
        and not parts.password
        and not parts.query
        and not parts.fragment
        and len(path_parts) == 5
        and path_parts[:2] == ["", "services"]
        and all(_SLACK_WEBHOOK_SEGMENT_RE.fullmatch(part) for part in path_parts[2:])
    )


def validate_slack_graph_boundary(
    nodes: Iterable[Any],
    *,
    require_resolved: bool = False,
    allow_legacy_selectors: bool = False,
) -> None:
    all_nodes = list(_iter_nodes(nodes))
    slack_node_modes: dict[str, str] = {}
    for node in all_nodes:
        node_type, node_id, data = _node_parts(node)
        if node_type != "slackPostNode":
            continue
        if not node_id or not isinstance(data, Mapping):
            raise SlackGraphBoundaryError()
        _validate_slack_data(data, require_resolved=require_resolved)
        slack_node_modes[node_id] = str(data.get("slackMode", "api"))

    if allow_legacy_selectors:
        return
    for node in all_nodes:
        _, _, data = _node_parts(node)
        if not isinstance(data, Mapping):
            continue
        for selector in _iter_selectors(data):
            if len(selector) < 2:
                continue
            slack_mode = slack_node_modes.get(selector[0])
            if slack_mode is None:
                continue
            output_key = selector[1]
            if output_key in {"data", "headers"} or (
                output_key == "message_ref" and slack_mode == "webhook"
            ):
                raise SlackGraphBoundaryError(SLACK_LEGACY_SELECTOR_REQUIRES_MIGRATION)


def _iter_nodes(nodes: Iterable[Any]) -> Iterable[Any]:
    pending = list(nodes)
    while pending:
        node = pending.pop()
        yield node
        _, _, data = _node_parts(node)
        if not isinstance(data, Mapping):
            continue
        subgraph = data.get("subGraph")
        nested = subgraph.get("nodes") if isinstance(subgraph, Mapping) else None
        if isinstance(nested, list):
            pending.extend(nested)


def _node_parts(node: Any) -> tuple[str, str, Any]:
    if isinstance(node, Mapping):
        return str(node.get("type") or ""), str(node.get("id") or ""), node.get("data")
    return (
        str(getattr(node, "type", "") or ""),
        str(getattr(node, "id", "") or ""),
        getattr(node, "data", None),
    )


def _validate_slack_data(data: Mapping[str, Any], *, require_resolved: bool) -> None:
    if set(data) - _SLACK_ALLOWED_FIELDS or data.get("parameters") not in (None, {}):
        raise SlackGraphBoundaryError()
    mode = data.get("slackMode", "api")
    if mode not in {"api", "webhook"}:
        raise SlackGraphBoundaryError()
    if data.get("configuration_state") not in (None, "resolved", "unresolved"):
        raise SlackGraphBoundaryError()
    if data.get("channel_resolution_state") not in (None, "resolved", "unresolved"):
        raise SlackGraphBoundaryError()
    deferred_parameters = data.get("_deferred_parameters", [])
    if not isinstance(deferred_parameters, list) or any(
        not isinstance(item, str) or item not in _SLACK_DEFERRED_PARAMETER_KEYS
        for item in deferred_parameters
    ):
        raise SlackGraphBoundaryError()
    if len(deferred_parameters) != len(set(deferred_parameters)):
        raise SlackGraphBoundaryError()
    display_number = data.get("displayNumber")
    if display_number is not None and (
        not isinstance(display_number, int)
        or isinstance(display_number, bool)
        or display_number < 1
    ):
        raise SlackGraphBoundaryError()
    visible_properties = data.get("visibleProperties")
    if visible_properties is not None and (
        not isinstance(visible_properties, list)
        or any(
            not isinstance(item, str) or not item.strip() or len(item) > 80
            for item in visible_properties
        )
    ):
        raise SlackGraphBoundaryError()
    for field in ("message", "channel", "thread_ts", "username", "icon_emoji", "url"):
        if data.get(field) is not None and not isinstance(data.get(field), str):
            raise SlackGraphBoundaryError()
    if any(
        not _is_valid_optional_json_array_template(data.get(field))
        for field in ("blocks", "attachments")
    ):
        raise SlackGraphBoundaryError()
    auth_config = data.get("authConfig", {})
    if not isinstance(auth_config, Mapping) or set(auth_config) - {"token"}:
        raise SlackGraphBoundaryError()
    token = auth_config.get("token")
    if token is not None and not isinstance(token, str):
        raise SlackGraphBoundaryError()
    if data.get("method") not in (None, "POST") or data.get("timeout") not in (
        None,
        5000,
    ):
        raise SlackGraphBoundaryError()
    if data.get("authType") not in (None, "", "bearer", "none"):
        raise SlackGraphBoundaryError()
    headers = data.get("headers", [])
    if not isinstance(headers, list) or any(
        not isinstance(item, Mapping)
        or str(item.get("key", "")).strip().lower() != "content-type"
        or str(item.get("value", "")).strip().lower() != "application/json"
        for item in headers
    ):
        raise SlackGraphBoundaryError()
    body = data.get("body")
    if body not in (None, ""):
        if not isinstance(body, str):
            raise SlackGraphBoundaryError()
        try:
            decoded = json.loads(body)
        except json.JSONDecodeError as exc:
            raise SlackGraphBoundaryError() from exc
        if not isinstance(decoded, dict) or set(decoded) - {
            "text",
            "channel",
            "blocks",
            "attachments",
            "thread_ts",
            "username",
            "icon_emoji",
        }:
            raise SlackGraphBoundaryError()
    references = data.get("referenced_variables", [])
    if not isinstance(references, list) or any(
        not _valid_reference(item) for item in references
    ):
        raise SlackGraphBoundaryError()
    if mode == "api":
        if (
            data.get("url") not in (None, "", "https://slack.com/api/chat.postMessage")
            or data.get("authType") == "none"
        ):
            raise SlackGraphBoundaryError()
        if require_resolved and (
            not _valid_secret_value(token, max_bytes=4096)
            or not _valid_channel_template(data.get("channel"))
        ):
            raise SlackGraphBoundaryError()
    else:
        if token not in (None, "") or data.get("authType") not in (None, "", "none"):
            raise SlackGraphBoundaryError()
        url = data.get("url")
        if require_resolved and (
            not url
            or not (
                is_workflow_node_secret_reference(url)
                or is_valid_commercial_slack_webhook_url(url)
            )
        ):
            raise SlackGraphBoundaryError()
    if require_resolved and not _has_delivery_payload(data):
        raise SlackGraphBoundaryError()


def _valid_reference(value: Any) -> bool:
    if not isinstance(value, Mapping) or not isinstance(value.get("name"), str):
        return False
    selector = value.get("value_selector")
    return (
        isinstance(selector, list)
        and len(selector) >= 2
        and all(isinstance(item, str) and item.strip() for item in selector)
    )


def _valid_secret_value(value: Any, *, max_bytes: int) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
        and len(value.encode("utf-8")) <= max_bytes
        and not any(char in value for char in "\r\n\x00")
    )


def _valid_channel_template(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and value == value.strip()
        and len(value.encode("utf-8")) <= 1024
        and not any(char in value for char in "\r\n\x00")
    )


def _has_delivery_payload(data: Mapping[str, Any]) -> bool:
    message = data.get("message")
    if isinstance(message, str) and bool(message.strip()):
        return True
    return any(
        _is_nonempty_json_array(data.get(field)) for field in ("blocks", "attachments")
    )


def _is_nonempty_json_array(value: Any) -> bool:
    decoded = _decode_json_array_template_shape(value)
    return bool(decoded) if decoded is not None else False


def _is_valid_optional_json_array_template(value: Any) -> bool:
    if value in (None, ""):
        return True
    return _decode_json_array_template_shape(value) is not None


def _decode_json_array_template_shape(value: Any) -> list[Any] | None:
    if isinstance(value, list):
        return value
    if not isinstance(value, str) or not value:
        return None
    shaped = _render_json_template_shape(value)
    if shaped is None:
        return None
    try:
        decoded = json.loads(
            shaped,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_json_constant,
        )
    except (ValueError, json.JSONDecodeError):
        return None
    return decoded if isinstance(decoded, list) else None


def _render_json_template_shape(value: str) -> str | None:
    without_tokens = _SLACK_TEMPLATE_TOKEN_RE.sub("", value)
    if any(marker in without_tokens for marker in _SLACK_UNSAFE_TEMPLATE_MARKERS):
        return None

    output: list[str] = []
    index = 0
    in_string = False
    escaped = False
    string_contains_token = False
    while index < len(value):
        match = _SLACK_TEMPLATE_TOKEN_RE.match(value, index)
        if match is not None:
            if escaped:
                return None
            string_contains_token = in_string
            output.append("x" if in_string else "null")
            index = match.end()
            continue

        char = value[index]
        output.append(char)
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
                if string_contains_token:
                    lookahead = index + 1
                    while lookahead < len(value) and value[lookahead].isspace():
                        lookahead += 1
                    if lookahead < len(value) and value[lookahead] == ":":
                        return None
                string_contains_token = False
        elif char == '"':
            in_string = True
            string_contains_token = False
        index += 1
    return "".join(output)


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, child in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = child
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _iter_selector_candidates(value: Any) -> Iterable[list[str]]:
    if (
        isinstance(value, list)
        and value
        and all(isinstance(item, str) for item in value)
    ):
        yield value
        return
    if isinstance(value, list):
        for child in value:
            yield from _iter_selector_candidates(child)


def _iter_selectors(value: Any) -> Iterable[list[str]]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if isinstance(key, str) and (
                key.endswith("_selector") or key.endswith("_selectors")
            ):
                yield from _iter_selector_candidates(child)
            yield from _iter_selectors(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_selectors(child)
