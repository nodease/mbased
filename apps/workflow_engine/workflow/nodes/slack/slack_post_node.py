"""Dedicated Slack node. Generic HTTP fields are compatibility checks only."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from typing import Any, NoReturn

from apps.shared.domain.slack_delivery import is_valid_commercial_slack_webhook_url
from apps.workflow_engine.adapters.providers.slack import (
    SlackDeliveryMode,
    SlackEffectRequest,
    SlackSecretMaterial,
)
from apps.workflow_engine.composition.slack import build_slack_effect_adapter
from apps.workflow_engine.workflow.errors import NonRetryableWorkflowError
from apps.workflow_engine.workflow.nodes.base.node import Node
from apps.shared.services.workflow_node_secret_service import (
    WorkflowNodeSecretError,
    resolve_runtime_workflow_node_secret,
)
from apps.workflow_engine.workflow.nodes.slack.entities import SlackPostNodeData


_API_URL = "https://slack.com/api/chat.postMessage"
_TOKEN_PATTERN = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]{0,127})\s*\}\}")
_UNSAFE_TEMPLATE_MARKERS = ("{{", "{%", "%}", "{#", "#}")


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _reject_nonfinite_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _validate_json_tree(value: Any, depth: int = 0) -> None:
    if depth > 20:
        raise ValueError("payload nesting too deep")
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
        raise ValueError("non-finite number")
    if isinstance(value, list):
        for child in value:
            _validate_json_tree(child, depth + 1)
        return
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError("invalid object key")
            _validate_json_tree(child, depth + 1)
        return
    raise ValueError("unsupported JSON value")


class SlackPostNode(Node[SlackPostNodeData]):
    node_type = "slackPostNode"

    def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        try:
            mode = SlackDeliveryMode(self.data.slackMode)
        except ValueError:
            self._fail("slack.configuration_invalid")
        self._validate_legacy_compatibility(mode)
        used_names = self._used_template_names(mode)
        context = self._template_context(inputs, used_names)
        request = SlackEffectRequest(
            mode=mode,
            payload=self._build_payload(mode, context),
            secret=self._resolve_secret(mode),
        )
        factory = self.execution_context.get("slack_effect_adapter_factory")
        adapter = (
            factory(mode) if callable(factory) else build_slack_effect_adapter(mode)
        )
        return self._run_external_effect(adapter, request)

    def _validate_legacy_compatibility(self, mode: SlackDeliveryMode) -> None:
        data = self.data
        method = (data.method or "POST").upper()
        if method != "POST" or data.timeout not in (None, 5000):
            self._fail("slack.legacy_configuration_invalid")
        for header in data.headers:
            name = str(header.get("key", "")).strip().lower()
            value = str(header.get("value", "")).strip().lower()
            if name and not (name == "content-type" and value == "application/json"):
                self._fail("slack.legacy_configuration_invalid")
        if data.body and data.body.strip():
            try:
                legacy_body = json.loads(data.body)
            except json.JSONDecodeError:
                self._fail("slack.legacy_configuration_invalid")
            if not isinstance(legacy_body, dict) or set(legacy_body) - {
                "text",
                "channel",
                "blocks",
                "attachments",
                "thread_ts",
                "username",
                "icon_emoji",
            }:
                self._fail("slack.legacy_configuration_invalid")
        allowed_auth_types = (
            (None, "", "bearer")
            if mode is SlackDeliveryMode.API
            else (None, "", "none")
        )
        if data.authType not in allowed_auth_types:
            self._fail("slack.legacy_configuration_invalid")
        if mode is SlackDeliveryMode.API and data.url not in (None, "", _API_URL):
            self._fail("slack.legacy_configuration_invalid")
        if mode is SlackDeliveryMode.WEBHOOK and data.authConfig.get("token"):
            self._fail("slack.configuration_invalid")

    def _used_template_names(self, mode: SlackDeliveryMode) -> set[str]:
        values: list[Any] = [
            self.data.message,
            self.data.blocks,
            self.data.attachments,
            self.data.thread_ts,
            self.data.username,
            self.data.icon_emoji,
        ]
        if mode is SlackDeliveryMode.API:
            values.append(self.data.channel)
        names: set[str] = set()
        for value in values:
            names.update(self._template_names_in_value(value))
        return names

    def _template_names_in_value(self, value: Any, depth: int = 0) -> set[str]:
        if depth > 20:
            self._fail("slack.payload_invalid")
        if value is None:
            return set()
        if isinstance(value, str):
            self._validate_template_syntax(value)
            return {match.group(1) for match in _TOKEN_PATTERN.finditer(value)}
        if isinstance(value, list):
            result: set[str] = set()
            for child in value:
                result.update(self._template_names_in_value(child, depth + 1))
            return result
        if isinstance(value, Mapping):
            result = set()
            for key, child in value.items():
                if not isinstance(key, str) or any(
                    marker in key for marker in _UNSAFE_TEMPLATE_MARKERS
                ):
                    self._fail("slack.template_render_failed")
                result.update(self._template_names_in_value(child, depth + 1))
            return result
        return set()

    def _template_context(
        self,
        inputs: dict[str, Any],
        used_names: set[str],
    ) -> dict[str, Any]:
        references: dict[str, list[str]] = {}
        for reference in self.data.referenced_variables:
            if reference.name not in used_names:
                continue
            if reference.name in references:
                self._fail("slack.template_value_invalid")
            references[reference.name] = reference.value_selector
        if set(references) != used_names:
            self._fail("slack.template_value_invalid")

        context: dict[str, Any] = {}
        for name, selector in references.items():
            value: Any = inputs
            for key in selector:
                if not isinstance(value, dict) or key not in value:
                    self._fail("slack.template_value_invalid")
                value = value[key]
            if isinstance(value, (dict, list, bytes)) or value is None:
                self._fail("slack.template_value_invalid")
            if isinstance(value, (bool, int, str)):
                context[name] = value
            elif isinstance(value, float) and math.isfinite(value):
                context[name] = value
            else:
                self._fail("slack.template_value_invalid")
        return context

    def _validate_template_syntax(self, value: str) -> None:
        template_without_tokens = _TOKEN_PATTERN.sub("", value)
        if any(
            marker in template_without_tokens for marker in _UNSAFE_TEMPLATE_MARKERS
        ):
            self._fail("slack.template_render_failed")

    def _render(self, value: str, context: dict[str, Any]) -> str:
        self._validate_template_syntax(value)

        def replace_token(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in context:
                self._fail("slack.template_value_invalid")
            return str(context[name])

        return _TOKEN_PATTERN.sub(replace_token, value)

    def _render_json_template(self, value: str, context: dict[str, Any]) -> str:
        self._validate_template_syntax(value)
        output: list[str] = []
        index = 0
        in_string = False
        escaped = False
        string_contains_token = False
        while index < len(value):
            match = _TOKEN_PATTERN.match(value, index)
            if match is not None:
                if escaped:
                    self._fail("slack.template_render_failed")
                name = match.group(1)
                if name not in context:
                    self._fail("slack.template_value_invalid")
                scalar = context[name]
                if in_string:
                    string_contains_token = True
                    encoded = json.dumps(
                        str(scalar), ensure_ascii=False, allow_nan=False
                    )
                    output.append(encoded[1:-1])
                else:
                    output.append(
                        json.dumps(scalar, ensure_ascii=False, allow_nan=False)
                    )
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
                            self._fail("slack.template_render_failed")
                    string_contains_token = False
            elif char == '"':
                in_string = True
                string_contains_token = False
            index += 1
        return "".join(output)

    def _render_json_tree(
        self,
        value: Any,
        context: dict[str, Any],
        depth: int = 0,
    ) -> Any:
        if depth > 20:
            self._fail("slack.payload_invalid")
        if isinstance(value, str):
            return self._render(value, context)
        if isinstance(value, list):
            return [
                self._render_json_tree(child, context, depth + 1) for child in value
            ]
        if isinstance(value, dict):
            if any(
                not isinstance(key, str)
                or any(marker in key for marker in _UNSAFE_TEMPLATE_MARKERS)
                for key in value
            ):
                self._fail("slack.template_render_failed")
            return {
                key: self._render_json_tree(child, context, depth + 1)
                for key, child in value.items()
            }
        return value

    def _load_json_array(
        self,
        value: Any,
        context: dict[str, Any],
        field: str,
    ) -> list[Any] | None:
        if value in (None, ""):
            return None
        try:
            loaded = (
                json.loads(
                    self._render_json_template(value, context),
                    object_pairs_hook=_reject_duplicate_json_keys,
                    parse_constant=_reject_nonfinite_json_constant,
                )
                if isinstance(value, str)
                else self._render_json_tree(value, context)
            )
            _validate_json_tree(loaded)
        except NonRetryableWorkflowError:
            raise
        except (TypeError, ValueError, json.JSONDecodeError):
            self._fail("slack.payload_invalid")
        if not isinstance(loaded, list):
            self._fail("slack.payload_invalid")
        if field == "blocks" and len(loaded) > 50:
            self._fail("slack.payload_too_large")
        if field == "attachments" and len(loaded) > 100:
            self._fail("slack.payload_too_large")
        return loaded

    def _build_payload(
        self,
        mode: SlackDeliveryMode,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        message = self._render(self.data.message, context)
        if len(message) > 40_000:
            self._fail("slack.payload_too_large")
        blocks = self._load_json_array(self.data.blocks, context, "blocks")
        attachments = self._load_json_array(
            self.data.attachments,
            context,
            "attachments",
        )
        if not message and not blocks and not attachments:
            self._fail("slack.message_required")
        payload: dict[str, Any] = {}
        if message:
            payload["text"] = message
        if blocks is not None:
            payload["blocks"] = blocks
        if attachments is not None:
            payload["attachments"] = attachments
        for key in ("thread_ts", "username", "icon_emoji"):
            value = getattr(self.data, key)
            if value:
                payload[key] = self._render(value, context)
        if mode is SlackDeliveryMode.API:
            channel = self._render(self.data.channel or "", context)
            if (
                not channel
                or channel != channel.strip()
                or len(channel) > 128
                or any(char in channel for char in "\r\n\x00")
            ):
                self._fail("slack.channel_invalid")
            payload["channel"] = channel
        try:
            serialized = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError):
            self._fail("slack.payload_invalid")
        if len(serialized.encode("utf-8")) > 256 * 1024:
            self._fail("slack.payload_too_large")
        return payload

    def _resolve_secret(self, mode: SlackDeliveryMode) -> SlackSecretMaterial:
        value = (
            self.data.authConfig.get("token")
            if mode is SlackDeliveryMode.API
            else self.data.url
        )
        try:
            value = resolve_runtime_workflow_node_secret(
                reference=value,
                execution_context=self.execution_context,
                node_id=self.id,
                node_type=self.node_type,
                parameter_key=(
                    "bot_token" if mode is SlackDeliveryMode.API else "url"
                ),
            )
        except WorkflowNodeSecretError:
            self._fail("slack.credential_invalid")
        limit = 4096 if mode is SlackDeliveryMode.API else 2048
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or len(value.encode("utf-8")) > limit
            or any(char in value for char in "\r\n\x00")
        ):
            self._fail("slack.credential_invalid")
        if mode is SlackDeliveryMode.WEBHOOK and not (
            is_valid_commercial_slack_webhook_url(value)
        ):
            self._fail("slack.credential_invalid")
        return SlackSecretMaterial(value)

    @staticmethod
    def _fail(reason_code: str) -> NoReturn:
        raise NonRetryableWorkflowError(reason_code)
