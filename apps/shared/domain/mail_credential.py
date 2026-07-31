from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

MAIL_CREDENTIAL_REFERENCE_REQUIRED = "mail.credential_reference_required"
MAIL_PROCESSING_CONFIGURATION_INVALID = "mail.processing_configuration_invalid"
MAIL_NODE_DEFERRED_PARAMETER_KEYS = frozenset({"credential_id"})
GMAIL_DRAFT_NODE_DEFERRED_PARAMETER_KEYS = frozenset({"credential_id"})
MAIL_ACKNOWLEDGE_NODE_DEFERRED_PARAMETER_KEYS = frozenset()

MAIL_NODE_ALLOWED_DATA_FIELDS = frozenset(
    {
        "title",
        "description",
        "parameters",
        "credential_id",
        "configuration_state",
        "keyword",
        "sender",
        "subject",
        "start_date",
        "end_date",
        "folder",
        "max_results",
        "unread_only",
        "mark_as_read",
        "processing_mode",
        "referenced_variables",
        "displayNumber",
        "visibleProperties",
        "_deferred_parameters",
    }
)

MAIL_NODE_VISIBLE_PROPERTY_KEYS = frozenset({"credential_id", "folder", "filters"})
MAIL_NODE_UI_METADATA_FIELDS = frozenset({"displayNumber", "visibleProperties"})
MAIL_NODE_FOLDERS = frozenset({"INBOX", "SENT", "DRAFTS", "SPAM", "TRASH"})
MAIL_NODE_OPTIONAL_STRING_FIELDS = (
    "description",
    "keyword",
    "sender",
    "subject",
    "start_date",
    "end_date",
)

GMAIL_DRAFT_NODE_ALLOWED_DATA_FIELDS = frozenset(
    {
        "title",
        "description",
        "parameters",
        "credential_id",
        "configuration_state",
        "processing_ref_selector",
        "reply_body_selector",
        "displayNumber",
        "visibleProperties",
        "_deferred_parameters",
    }
)
MAIL_ACKNOWLEDGE_NODE_ALLOWED_DATA_FIELDS = frozenset(
    {
        "title",
        "description",
        "parameters",
        "processing_ref_selector",
        "required_effect_ref_selectors",
        "configuration_state",
        "displayNumber",
        "visibleProperties",
        "_deferred_parameters",
    }
)


class MailNodeCredentialBoundaryError(ValueError):
    def __init__(self) -> None:
        super().__init__(MAIL_CREDENTIAL_REFERENCE_REQUIRED)
        self.reason_code = MAIL_CREDENTIAL_REFERENCE_REQUIRED


class MailProcessingGraphBoundaryError(ValueError):
    def __init__(self, node_id: str | None = None) -> None:
        super().__init__(MAIL_PROCESSING_CONFIGURATION_INVALID)
        self.reason_code = MAIL_PROCESSING_CONFIGURATION_INVALID
        self.node_id = node_id


def validate_mail_node_credential_boundary(data: Any) -> None:
    """Reject Mail graph fields that can become an alternate credential store."""
    if not isinstance(data, Mapping):
        raise MailNodeCredentialBoundaryError()
    if set(data) - MAIL_NODE_ALLOWED_DATA_FIELDS:
        raise MailNodeCredentialBoundaryError()
    _validate_common_node_fields(data)
    _validate_credential_reference_state(data)
    _validate_deferred_parameters(data, MAIL_NODE_DEFERRED_PARAMETER_KEYS)

    for field_name in MAIL_NODE_OPTIONAL_STRING_FIELDS:
        _validate_optional_string(data, field_name)
    if data.get("folder", "INBOX") not in MAIL_NODE_FOLDERS:
        raise MailNodeCredentialBoundaryError()
    max_results = data.get("max_results")
    if max_results is not None and (
        not isinstance(max_results, int)
        or isinstance(max_results, bool)
        or not 1 <= max_results <= 100
    ):
        raise MailNodeCredentialBoundaryError()
    for field_name in ("unread_only", "mark_as_read"):
        if field_name in data and not isinstance(data[field_name], bool):
            raise MailNodeCredentialBoundaryError()
    _validate_referenced_variables(data.get("referenced_variables"))

    processing_mode = data.get("processing_mode", "search_only")
    if processing_mode not in {"search_only", "durable"}:
        raise MailNodeCredentialBoundaryError()
    if processing_mode == "durable" and data.get("mark_as_read") is True:
        raise MailNodeCredentialBoundaryError()


def validate_mail_processing_node_boundary(
    node_type: str, data: Any, *, allow_unresolved: bool = False
) -> None:
    if not isinstance(data, Mapping):
        raise MailNodeCredentialBoundaryError()
    if node_type == "gmailDraftNode":
        allowed = GMAIL_DRAFT_NODE_ALLOWED_DATA_FIELDS
        selector_fields = ("processing_ref_selector", "reply_body_selector")
        deferred_parameter_keys = GMAIL_DRAFT_NODE_DEFERRED_PARAMETER_KEYS
    elif node_type == "mailAcknowledgeNode":
        allowed = MAIL_ACKNOWLEDGE_NODE_ALLOWED_DATA_FIELDS
        selector_fields = ("processing_ref_selector",)
        deferred_parameter_keys = MAIL_ACKNOWLEDGE_NODE_DEFERRED_PARAMETER_KEYS
    else:
        raise MailNodeCredentialBoundaryError()
    if set(data) - allowed:
        raise MailNodeCredentialBoundaryError()
    _validate_common_node_fields(data)
    _validate_deferred_parameters(data, deferred_parameter_keys)
    if node_type == "gmailDraftNode":
        _validate_credential_reference_state(data)
    else:
        _validate_configuration_state(data)
    for field_name in selector_fields:
        selector = data.get(field_name)
        if not _valid_selector(selector) and not (
            allow_unresolved and selector in (None, [])
        ):
            raise MailNodeCredentialBoundaryError()
    if node_type == "mailAcknowledgeNode":
        effect_selectors = data.get("required_effect_ref_selectors")
        if (
            not isinstance(effect_selectors, list)
            or (not effect_selectors and not allow_unresolved)
            or any(not _valid_selector(selector) for selector in effect_selectors)
        ):
            raise MailNodeCredentialBoundaryError()


def validate_mail_processing_graph_contract(
    graph: Any,
    *,
    require_resolved: bool,
) -> None:
    """Validate durable Mail processing selectors and graph reachability."""
    if not isinstance(graph, Mapping):
        raise MailProcessingGraphBoundaryError()

    for current_graph in _iter_graphs(graph):
        nodes = current_graph.get("nodes")
        edges = current_graph.get("edges")
        if not isinstance(nodes, list):
            continue
        node_by_id = {
            str(node.get("id")): node
            for node in nodes
            if isinstance(node, Mapping) and node.get("id")
        }
        edge_pairs = {
            (str(edge.get("source")), str(edge.get("target")))
            for edge in (edges if isinstance(edges, list) else [])
            if isinstance(edge, Mapping) and edge.get("source") and edge.get("target")
        }

        for node in node_by_id.values():
            node_type = str(node.get("type") or "")
            if node_type not in {"gmailDraftNode", "mailAcknowledgeNode"}:
                continue
            node_id = str(node.get("id"))
            data = node.get("data")
            if not isinstance(data, Mapping):
                continue
            processing_selector = data.get("processing_ref_selector")
            if processing_selector in (None, []) and not require_resolved:
                continue
            source = _mail_processing_source(node_by_id, processing_selector)
            if source is None or not _has_graph_path(
                edge_pairs,
                str(source.get("id")),
                node_id,
            ):
                raise MailProcessingGraphBoundaryError(node_id)

            if node_type == "gmailDraftNode":
                reply_selector = data.get("reply_body_selector")
                reply_source_id = (
                    str(reply_selector[0])
                    if isinstance(reply_selector, list) and len(reply_selector) >= 2
                    else ""
                )
                reply_source = node_by_id.get(reply_source_id)
                if (
                    reply_source is None
                    or not _has_graph_path(edge_pairs, reply_source_id, node_id)
                    or source.get("data", {}).get("credential_id")
                    != data.get("credential_id")
                ):
                    raise MailProcessingGraphBoundaryError(node_id)
                continue

            effect_selectors = data.get("required_effect_ref_selectors")
            if effect_selectors in (None, []) and not require_resolved:
                continue
            for selector in effect_selectors or []:
                if not isinstance(selector, list) or len(selector) != 2:
                    raise MailProcessingGraphBoundaryError(node_id)
                effect_id = str(selector[0])
                effect_node = node_by_id.get(effect_id)
                effect_data = (
                    effect_node.get("data")
                    if isinstance(effect_node, Mapping)
                    else None
                )
                if (
                    not isinstance(effect_node, Mapping)
                    or effect_node.get("type") != "gmailDraftNode"
                    or selector[1] != "draft_ref"
                    or not isinstance(effect_data, Mapping)
                    or effect_data.get("processing_ref_selector") != processing_selector
                    or not _has_graph_path(edge_pairs, effect_id, node_id)
                ):
                    raise MailProcessingGraphBoundaryError(node_id)


def _iter_graphs(graph: Mapping[str, Any]):
    pending = [graph]
    while pending:
        current = pending.pop()
        yield current
        nodes = current.get("nodes")
        if not isinstance(nodes, list):
            continue
        for node in nodes:
            data = node.get("data") if isinstance(node, Mapping) else None
            subgraph = data.get("subGraph") if isinstance(data, Mapping) else None
            if isinstance(subgraph, Mapping):
                pending.append(subgraph)


def _mail_processing_source(
    node_by_id: Mapping[str, Mapping[str, Any]],
    selector: Any,
) -> Mapping[str, Any] | None:
    if (
        not isinstance(selector, list)
        or len(selector) != 2
        or selector[1] != "processing_ref"
    ):
        return None
    source = node_by_id.get(str(selector[0]))
    data = source.get("data") if isinstance(source, Mapping) else None
    if (
        not isinstance(source, Mapping)
        or source.get("type") != "mailNode"
        or not isinstance(data, Mapping)
        or data.get("processing_mode") != "durable"
        or data.get("mark_as_read") is True
        or data.get("max_results") != 1
    ):
        return None
    return source


def _has_graph_path(
    edge_pairs: set[tuple[str, str]],
    source_id: str,
    target_id: str,
) -> bool:
    if not source_id or not target_id or source_id == target_id:
        return False
    pending = [source_id]
    visited: set[str] = set()
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        for edge_source, edge_target in edge_pairs:
            if edge_source != current:
                continue
            if edge_target == target_id:
                return True
            pending.append(edge_target)
    return False


def _valid_selector(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) >= 2
        and all(isinstance(item, str) and item.strip() for item in value)
    )


def _validate_common_node_fields(data: Mapping[str, Any]) -> None:
    if not isinstance(data.get("title"), str):
        raise MailNodeCredentialBoundaryError()
    _validate_optional_string(data, "description")

    # BaseNodeData.parameters is intentionally flexible for other nodes. Mail
    # credentials have a dedicated resource, so this alternate storage bag must
    # stay empty.
    if data.get("parameters") not in (None, {}):
        raise MailNodeCredentialBoundaryError()

    display_number = data.get("displayNumber")
    if display_number is not None and (
        not isinstance(display_number, int)
        or isinstance(display_number, bool)
        or display_number < 1
    ):
        raise MailNodeCredentialBoundaryError()

    visible_properties = data.get("visibleProperties")
    if visible_properties is not None and (
        not isinstance(visible_properties, list)
        or any(
            not isinstance(item, str) or item not in MAIL_NODE_VISIBLE_PROPERTY_KEYS
            for item in visible_properties
        )
    ):
        raise MailNodeCredentialBoundaryError()


def _validate_credential_reference_state(data: Mapping[str, Any]) -> None:
    credential_id = data.get("credential_id")
    configuration_state = _validate_configuration_state(data)
    if credential_id is None:
        # Mail nodes created before configuration_state was introduced omitted
        # the field. Preserve that narrow draft compatibility as unresolved.
        if "configuration_state" in data and configuration_state != "unresolved":
            raise MailNodeCredentialBoundaryError()
        return
    if configuration_state == "unresolved":
        raise MailNodeCredentialBoundaryError()
    try:
        UUID(str(credential_id))
    except (TypeError, ValueError):
        raise MailNodeCredentialBoundaryError() from None


def _validate_configuration_state(data: Mapping[str, Any]) -> Any:
    configuration_state = data.get("configuration_state")
    if configuration_state not in (None, "resolved", "unresolved"):
        raise MailNodeCredentialBoundaryError()
    return configuration_state


def _validate_deferred_parameters(
    data: Mapping[str, Any],
    allowed_keys: frozenset[str],
) -> None:
    deferred_parameters = data.get("_deferred_parameters", [])
    if not isinstance(deferred_parameters, list):
        raise MailNodeCredentialBoundaryError()
    normalized: list[str] = []
    for item in deferred_parameters:
        if not isinstance(item, str) or item not in allowed_keys:
            raise MailNodeCredentialBoundaryError()
        normalized.append(item)
    if len(normalized) != len(set(normalized)):
        raise MailNodeCredentialBoundaryError()


def _validate_optional_string(data: Mapping[str, Any], field_name: str) -> None:
    value = data.get(field_name)
    if value is not None and not isinstance(value, str):
        raise MailNodeCredentialBoundaryError()


def _validate_referenced_variables(value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, list):
        raise MailNodeCredentialBoundaryError()
    for variable in value:
        if not isinstance(variable, Mapping):
            raise MailNodeCredentialBoundaryError()
        if set(variable) - {"name", "value_selector"}:
            raise MailNodeCredentialBoundaryError()
        if "name" in variable and not isinstance(variable["name"], str):
            raise MailNodeCredentialBoundaryError()
        selector = variable.get("value_selector", [])
        if not isinstance(selector, list) or any(
            not isinstance(item, str) for item in selector
        ):
            raise MailNodeCredentialBoundaryError()
