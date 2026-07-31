import hashlib
from unittest.mock import Mock
import pytest

from apps.shared.services.tracing.metadata import TraceMetadataSanitizer
from apps.workflow_engine.adapters.providers.slack import (
    SlackDeliveryMode,
    SlackEffectRequest,
)
from apps.workflow_engine.domain.external_effect import (
    PreparedEffectRequest,
    PreparedProviderCall,
    ProviderInvocationResult,
    provider_contract_registry,
)
from apps.workflow_engine.workflow.core.workflow_engine import WorkflowEngine
from apps.workflow_engine.workflow.errors import (
    NonRetryableWorkflowError,
    WorkflowNodeConfigurationError,
)
from apps.workflow_engine.workflow.nodes.slack import SlackPostNode, SlackPostNodeData


class CapturingAdapter:
    def __init__(self, mode: SlackDeliveryMode) -> None:
        self.mode = mode
        operation = (
            "slack.chat.post_message"
            if mode is SlackDeliveryMode.API
            else "slack.incoming_webhook.post"
        )
        self._profile = provider_contract_registry().active("slack", operation)
        self.request: SlackEffectRequest | None = None
        self.trace_metadata = {
            "slack": {
                "delivery_mode": mode.value,
                "delivery_status": "delivered",
                "status_code": 200,
            }
        }

    @property
    def profile(self):
        return self._profile

    def prepare_effect(self, payload, *, profile=None):
        self.request = payload
        return PreparedEffectRequest(
            request=payload,
            effect_input_digest=hashlib.sha256(b"test").hexdigest(),
            profile=profile or self.profile,
        )

    def finalize_provider_call(self, prepared, idempotency_key):
        assert idempotency_key is None
        return PreparedProviderCall(prepared.request, None, prepared.profile)

    def invoke_effect(self, call):
        output = {
            "status": 200,
            "delivery_status": "delivered",
            "delivery_mode": self.mode.value,
        }
        if self.mode is SlackDeliveryMode.API:
            output["message_ref"] = "1.2"
        return ProviderInvocationResult(output, provider_status_code=200)

    def replay_projection(self, output, *, profile):
        return output


def _node(
    *,
    mode: str = "api",
    referenced_variables=None,
    **overrides,
) -> tuple[SlackPostNode, CapturingAdapter]:
    selected_mode = SlackDeliveryMode(mode)
    adapter = CapturingAdapter(selected_mode)
    data = {
        "title": "Slack",
        "slackMode": mode,
        "channel": "C123",
        "message": "hello",
        "authConfig": {"token": "test-token"},
        "referenced_variables": referenced_variables or [],
    }
    if mode == "webhook":
        data["url"] = "https://hooks.slack.com/services/a/b/c"
        data["authConfig"] = {}
        data["authType"] = "none"
    data.update(overrides)
    node = SlackPostNode(
        "slack-1",
        SlackPostNodeData(**data),
        {"slack_effect_adapter_factory": lambda actual_mode: adapter},
    )
    return node, adapter


def test_node_uses_dedicated_output_and_resolves_only_referenced_template_values() -> (
    None
):
    node, adapter = _node(
        message="hello {{used}}",
        referenced_variables=[
            {"name": "used", "value_selector": ["llm", "text"]},
            {"name": "unused", "value_selector": ["missing", "value"]},
        ],
    )

    result = node.execute({"llm": {"text": "world"}})

    assert result == {
        "status": 200,
        "delivery_status": "delivered",
        "delivery_mode": "api",
        "message_ref": "1.2",
    }
    assert adapter.request is not None
    assert adapter.request.payload == {"text": "hello world", "channel": "C123"}
    assert "test-token" not in repr(adapter.request)


def test_node_resolves_opaque_secret_reference_only_at_runtime() -> None:
    node, adapter = _node(
        authConfig={
            "token": "workflow-node-secret://00000000-0000-4000-8000-000000000001"
        }
    )
    resolver = Mock(return_value="resolved-runtime-token")
    node.execution_context.update(
        {
            "workflow_id": "00000000-0000-4000-8000-000000000010",
            "organization_id": "00000000-0000-4000-8000-000000000020",
            "workflow_node_secret_resolver": resolver,
        }
    )

    node.execute({})

    resolver.assert_called_once_with(
        reference="workflow-node-secret://00000000-0000-4000-8000-000000000001",
        workflow_id="00000000-0000-4000-8000-000000000010",
        organization_id="00000000-0000-4000-8000-000000000020",
        node_id="slack-1",
        node_type="slackPostNode",
        parameter_key="bot_token",
    )
    assert adapter.request is not None
    assert adapter.request.secret.reveal_for_adapter() == "resolved-runtime-token"


def test_json_template_escapes_upstream_value_without_changing_structure() -> None:
    injected = 'text"}],"type":"divider"},{"text":"still data'
    node, adapter = _node(
        blocks=('[{"type":"section","text":{"type":"mrkdwn","text":"{{result}}"}}]'),
        referenced_variables=[{"name": "result", "value_selector": ["llm", "text"]}],
    )

    node.execute({"llm": {"text": injected}})

    assert adapter.request is not None
    assert len(adapter.request.payload["blocks"]) == 1
    assert adapter.request.payload["blocks"][0]["text"]["text"] == injected


def test_json_template_supports_scalar_as_an_entire_json_value() -> None:
    node, adapter = _node(
        blocks='[{"type":"section","expand":{{expanded}}}]',
        referenced_variables=[
            {"name": "expanded", "value_selector": ["input", "expanded"]}
        ],
    )

    node.execute({"input": {"expanded": True}})

    assert adapter.request is not None
    assert adapter.request.payload["blocks"][0]["expand"] is True


def test_json_template_rejects_dynamic_object_key() -> None:
    node, adapter = _node(
        blocks='[{"{{dynamic_key}}":"value"}]',
        referenced_variables=[
            {"name": "dynamic_key", "value_selector": ["input", "key"]}
        ],
    )

    with pytest.raises(NonRetryableWorkflowError, match="template_render_failed"):
        node.execute({"input": {"key": "type"}})

    assert adapter.request is None


@pytest.mark.parametrize(
    "message",
    [
        "{{ cycler.__init__.__globals__.os.popen('id').read() }}",
        "{% for item in range(100000) %}x{% endfor %}",
        "{{value|upper}}",
    ],
)
def test_template_rejects_expression_filter_and_control_flow(message: str) -> None:
    node, adapter = _node(message=message)

    with pytest.raises(NonRetryableWorkflowError, match="template_render_failed"):
        node.execute({})

    assert adapter.request is None


def test_upstream_template_syntax_is_inert_after_single_substitution() -> None:
    node, adapter = _node(
        message="hello {{result}}",
        referenced_variables=[
            {"name": "result", "value_selector": ["upstream", "text"]}
        ],
    )

    node.execute({"upstream": {"text": "{{not_evaluated}}"}})

    assert adapter.request is not None
    assert adapter.request.payload["text"] == "hello {{not_evaluated}}"


def test_custom_legacy_http_configuration_fails_before_adapter_call() -> None:
    node, adapter = _node(headers=[{"key": "X-Unapproved", "value": "value"}])

    with pytest.raises(NonRetryableWorkflowError, match="legacy_configuration_invalid"):
        node.execute({})

    assert adapter.request is None


def test_legacy_body_is_validated_but_never_used_as_request_source() -> None:
    node, adapter = _node(
        message="canonical-message",
        method="POST",
        headers=[{"key": "Content-Type", "value": "application/json"}],
        body='{"text":"legacy-message"}',
        timeout=5000,
        authType="bearer",
    )

    node.execute({})

    assert adapter.request is not None
    assert adapter.request.payload["text"] == "canonical-message"


def test_webhook_ignores_hidden_legacy_channel_and_has_no_message_reference() -> None:
    node, adapter = _node(mode="webhook", channel="legacy-channel")

    result = node.execute({})

    assert result == {
        "status": 200,
        "delivery_status": "delivered",
        "delivery_mode": "webhook",
    }
    assert adapter.request is not None
    assert "channel" not in adapter.request.payload


def test_api_channel_rejects_surrounding_whitespace() -> None:
    node, adapter = _node(channel=" C123 ")

    with pytest.raises(NonRetryableWorkflowError, match="channel_invalid"):
        node.execute({})

    assert adapter.request is None


@pytest.mark.parametrize(
    "blocks",
    [
        '[{"type":"section","type":"duplicate"}]',
        "[NaN]",
        "[" * 22 + "]" * 22,
    ],
)
def test_unsafe_or_excessively_nested_json_is_rejected(blocks: str) -> None:
    node, adapter = _node(blocks=blocks)

    with pytest.raises(NonRetryableWorkflowError, match="payload_invalid"):
        node.execute({})

    assert adapter.request is None


def test_slack_trace_allowlist_excludes_content_and_message_reference() -> None:
    metadata = TraceMetadataSanitizer.sanitize_span_metadata(
        "slackPostNode",
        {
            "slack": {
                "delivery_mode": "api",
                "delivery_status": "delivered",
                "status_code": 200,
                "has_message_ref": True,
                "message_ref": "1.2",
                "message": "must-not-survive",
            }
        },
    )

    assert metadata["slack"] == {
        "delivery_mode": "api",
        "delivery_status": "delivered",
        "status_code": 200,
        "has_message_ref": True,
    }


def test_runtime_fails_fast_for_removed_output_selector() -> None:
    graph = {
        "nodes": [
            {
                "id": "slack",
                "type": "slackPostNode",
                "position": {"x": 0, "y": 0},
                "data": {
                    "title": "Slack",
                    "slackMode": "api",
                    "channel": "C123",
                    "message": "message",
                    "authConfig": {"token": "test-token"},
                    "referenced_variables": [],
                },
            },
            {
                "id": "consumer",
                "type": "templateNode",
                "position": {"x": 100, "y": 0},
                "data": {
                    "title": "Consumer",
                    "template": "{{raw}}",
                    "variables": [{"name": "raw", "value_selector": ["slack", "data"]}],
                },
            },
        ],
        "edges": [],
    }

    with pytest.raises(
        WorkflowNodeConfigurationError,
        match="slack.legacy_selector_requires_migration",
    ):
        WorkflowEngine(graph=graph)


def test_runtime_reports_invalid_slack_configuration_without_provider_call() -> None:
    graph = {
        "nodes": [
            {
                "id": "slack",
                "type": "slackPostNode",
                "position": {"x": 0, "y": 0},
                "data": {
                    "title": "Slack",
                    "slackMode": "api",
                    "channel": "C123",
                    "message": "message",
                    "authConfig": {"token": "test-token"},
                    "method": "PUT",
                    "referenced_variables": [],
                },
            }
        ],
        "edges": [],
    }

    with pytest.raises(
        WorkflowNodeConfigurationError,
        match="slack.graph_configuration_invalid",
    ):
        WorkflowEngine(graph=graph)
