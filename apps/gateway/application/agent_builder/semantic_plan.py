from __future__ import annotations

from collections.abc import Iterable

from apps.shared.schemas.agent_builder import (
    AgentBuilderExplicitParameterValue,
    AgentBuilderParameterGuidanceHint,
)
from apps.shared.services.tracing.policy import TracePolicyService
from apps.shared.services.tracing.redaction import TraceRedactionService
from apps.shared.services.workflow_node_catalog import (
    node_parameter_definitions,
    node_type_for_capability,
    validate_node_parameter_value,
)


CAPABILITY_STEP_IDS = {
    "start_input": "step_input",
    "webhook_trigger": "step_input",
    "schedule_trigger": "step_input",
    "file_extraction": "step_file_extraction",
    "variable_extraction": "step_variable_extraction",
    "github_pr_read": "step_github_read",
    "mail_search": "step_mail",
    "gmail_reply_draft_create": "step_gmail_draft",
    "mail_terminal_acknowledgement": "step_mail_acknowledge",
    "http_request": "step_http",
    "workflow_call": "step_workflow",
    "code_execution": "step_code",
    "template_render": "step_template",
    "condition": "step_condition",
    "loop": "step_loop",
    "llm": "step_llm",
    "knowledge_backed_llm": "step_llm",
    "github_pr_comment": "step_github_comment",
    "slack_send": "step_slack",
    "answer": "step_answer",
}


def planned_step_ids(capabilities: Iterable[str]) -> list[tuple[str, str]]:
    counts: dict[str, int] = {}
    result: list[tuple[str, str]] = []
    for capability in capabilities:
        base_step_id = CAPABILITY_STEP_IDS.get(capability)
        if base_step_id is None:
            continue
        counts[base_step_id] = counts.get(base_step_id, 0) + 1
        count = counts[base_step_id]
        step_id = base_step_id if count == 1 else f"{base_step_id}_{count}"
        result.append((step_id, capability))
    return result


def catalog_parameter_guide(
    capabilities: Iterable[str],
) -> dict[str, list[dict[str, str]]]:
    guide: dict[str, list[dict[str, str]]] = {}
    for step_id, capability in planned_step_ids(capabilities):
        node_type = node_type_for_capability(capability)
        if node_type is None:
            continue
        guide[step_id] = [
            {
                "parameter_key": str(parameter["key"]),
                "label": str(parameter["label"]),
                "input_type": str(parameter["input_type"]),
            }
            for parameter in node_parameter_definitions(node_type)
        ]
    return guide


def _contains_secret_like_text(value: str) -> bool:
    result = TraceRedactionService.redact_payload(
        value,
        policy=TracePolicyService.fail_closed_redaction_policy(),
        payload_kind="agent_builder_parameter_guidance",
    )
    return result.failed or result.secret_detected


def normalize_parameter_guidance_hints(
    hints: Iterable[AgentBuilderParameterGuidanceHint],
    steps: Iterable[tuple[str, str]],
) -> list[AgentBuilderParameterGuidanceHint]:
    step_capabilities = dict(steps)
    result: list[AgentBuilderParameterGuidanceHint] = []
    seen: set[tuple[str, str]] = set()
    for hint in hints:
        capability = step_capabilities.get(hint.step_id)
        if capability is None:
            continue
        node_type = node_type_for_capability(capability)
        if node_type is None:
            continue
        allowed_keys = {
            str(parameter["key"])
            for parameter in node_parameter_definitions(node_type)
        }
        key = (hint.step_id, hint.parameter_key)
        if hint.parameter_key not in allowed_keys or key in seen:
            continue
        if _contains_secret_like_text(hint.reason) or _contains_secret_like_text(
            hint.input_guidance
        ):
            continue
        seen.add(key)
        result.append(hint)
    return result


def normalize_explicit_parameter_values(
    values: Iterable[AgentBuilderExplicitParameterValue],
    steps: Iterable[tuple[str, str]],
) -> list[AgentBuilderExplicitParameterValue]:
    step_capabilities = dict(steps)
    accepted: dict[tuple[str, str], AgentBuilderExplicitParameterValue] = {}
    ambiguous: set[tuple[str, str]] = set()
    for item in values:
        capability = step_capabilities.get(item.step_id)
        node_type = node_type_for_capability(capability) if capability else None
        if node_type is None:
            continue
        parameter = next(
            (
                definition
                for definition in node_parameter_definitions(node_type)
                if str(definition["key"]) == item.parameter_key
            ),
            None,
        )
        if parameter is None or parameter.get("sensitivity") != "safe":
            continue
        if parameter.get("input_type") in {"credential_ref", "resource_ref"}:
            continue
        redaction = TraceRedactionService.redact_payload(
            item.value,
            policy=TracePolicyService.fail_closed_redaction_policy(),
            payload_kind="agent_builder_explicit_parameter",
        )
        if redaction.failed or redaction.secret_detected:
            continue
        if validate_node_parameter_value(node_type, item.parameter_key, item.value):
            continue
        identity = (item.step_id, item.parameter_key)
        previous = accepted.get(identity)
        if previous is not None and previous.value != item.value:
            ambiguous.add(identity)
            continue
        accepted[identity] = item
    return [
        item
        for identity, item in accepted.items()
        if identity not in ambiguous
    ]
