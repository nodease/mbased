import pytest
from apps.shared.domain.slack_delivery import (
    SLACK_GRAPH_CONFIGURATION_INVALID,
    SLACK_LEGACY_SELECTOR_REQUIRES_MIGRATION,
    SlackGraphBoundaryError,
    is_valid_commercial_slack_webhook_url,
    validate_slack_graph_boundary,
)


def _slack_node(data=None):
    return {
        "id": "slack",
        "type": "slackPostNode",
        "data": {
            "title": "Slack",
            "slackMode": "api",
            "channel": "C123",
            "message": "hello",
            "authConfig": {"token": "static-token"},
            "referenced_variables": [],
            **(data or {}),
        },
    }


def test_slack_graph_allows_agent_builder_deferred_parameter_metadata():
    validate_slack_graph_boundary(
        [
            _slack_node(
                {
                    "channel": "C123",
                    "body": '{"channel":"C123","text":"hello"}',
                    "_deferred_parameters": [],
                    "configuration_state": "unresolved",
                }
            )
        ],
        allow_legacy_selectors=True,
    )


def test_slack_graph_rejects_invalid_agent_builder_deferred_parameter_metadata():
    with pytest.raises(SlackGraphBoundaryError):
        validate_slack_graph_boundary(
            [
                _slack_node(
                    {
                        "_deferred_parameters": ["channel", 123],
                    }
                )
            ],
            allow_legacy_selectors=True,
        )


def test_slack_graph_rejects_unhashable_agent_builder_deferred_parameter_metadata():
    with pytest.raises(SlackGraphBoundaryError):
        validate_slack_graph_boundary(
            [
                _slack_node(
                    {
                        "_deferred_parameters": ["channel", {"unexpected": True}],
                    }
                )
            ],
            allow_legacy_selectors=True,
        )


def test_slack_graph_boundary_allows_dedicated_api_configuration():
    validate_slack_graph_boundary([_slack_node()], require_resolved=True)


def test_slack_graph_boundary_allows_editor_display_metadata():
    validate_slack_graph_boundary(
        [_slack_node({"displayNumber": 1, "visibleProperties": ["status"]})]
    )


@pytest.mark.parametrize(
    "data",
    [
        {"headers": [{"key": "Authorization", "value": "alternate"}]},
        {"method": "PUT"},
        {"authConfig": {"token": "x", "extra": "y"}},
        {"parameters": {"alternate": "storage"}},
    ],
)
def test_slack_graph_boundary_rejects_alternate_request_sources(data):
    with pytest.raises(SlackGraphBoundaryError):
        validate_slack_graph_boundary([_slack_node(data)])


def test_slack_graph_boundary_rejects_removed_raw_output_selector():
    consumer = {
        "id": "consumer",
        "type": "templateNode",
        "data": {
            "title": "Consumer",
            "referenced_variables": [
                {"name": "raw", "value_selector": ["slack", "data"]}
            ],
        },
    }

    with pytest.raises(SlackGraphBoundaryError) as error:
        validate_slack_graph_boundary([_slack_node(), consumer])
    assert error.value.reason_code == SLACK_LEGACY_SELECTOR_REQUIRES_MIGRATION

    validate_slack_graph_boundary(
        [_slack_node(), consumer], allow_legacy_selectors=True
    )


@pytest.mark.parametrize(
    ("selector_field", "selector_value"),
    [
        ("variable_selector", ["slack", "data"]),
        ("source_selector", ["slack", "headers"]),
        ("required_effect_ref_selectors", [["slack", "data"]]),
    ],
)
def test_slack_graph_boundary_checks_all_execution_selector_fields(
    selector_field, selector_value
):
    consumer = {
        "id": "consumer",
        "type": "conditionNode",
        "data": {selector_field: selector_value},
    }

    with pytest.raises(SlackGraphBoundaryError) as error:
        validate_slack_graph_boundary([_slack_node(), consumer])

    assert error.value.reason_code == SLACK_LEGACY_SELECTOR_REQUIRES_MIGRATION


def test_slack_graph_boundary_rejects_webhook_message_ref_selector_only():
    webhook = _slack_node(
        {
            "slackMode": "webhook",
            "channel": "legacy-channel",
            "authConfig": {},
            "url": "https://hooks.slack.com/services/a/b/c",
        }
    )
    consumer = {
        "id": "consumer",
        "type": "templateNode",
        "data": {"value_selector": ["slack", "message_ref"]},
    }

    with pytest.raises(SlackGraphBoundaryError) as error:
        validate_slack_graph_boundary([webhook, consumer])

    assert error.value.reason_code == SLACK_LEGACY_SELECTOR_REQUIRES_MIGRATION
    validate_slack_graph_boundary([webhook, consumer], allow_legacy_selectors=True)
    validate_slack_graph_boundary([_slack_node(), consumer])


def test_legacy_selector_compatibility_does_not_skip_slack_configuration_validation():
    with pytest.raises(SlackGraphBoundaryError):
        validate_slack_graph_boundary(
            [_slack_node({"headers": [{"key": "Authorization", "value": "x"}]})],
            allow_legacy_selectors=True,
        )


def test_slack_graph_boundary_accepts_legacy_generated_payload_shape():
    validate_slack_graph_boundary(
        [
            _slack_node(
                {
                    "body": (
                        '{"text":"old","channel":"C123","blocks":[{"type":"section"}]}'
                    )
                }
            )
        ]
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://slack.com/api/chat.postMessage",
        "https://hooks.slack.com/services/a/b/c?query=1",
        "https://hooks.slack.com/services/a/b",
        " https://hooks.slack.com/services/a/b/c",
        "https://HOOKS.SLACK.COM/services/a/b/c",
    ],
)
def test_slack_graph_boundary_rejects_noncanonical_webhook_before_deployment(url):
    node = _slack_node(
        {
            "slackMode": "webhook",
            "channel": "",
            "authConfig": {},
            "url": url,
        }
    )

    with pytest.raises(SlackGraphBoundaryError) as error:
        validate_slack_graph_boundary([node], require_resolved=True)

    assert error.value.reason_code == SLACK_GRAPH_CONFIGURATION_INVALID


def test_slack_graph_boundary_accepts_opaque_webhook_reference_before_deployment():
    node = _slack_node(
        {
            "slackMode": "webhook",
            "url": "workflow-node-secret://00000000-0000-4000-8000-000000000001",
            "authConfig": {},
            "authType": "none",
            "message": "hello",
        }
    )

    validate_slack_graph_boundary([node], require_resolved=True)


def test_commercial_slack_webhook_validator_accepts_exact_shape():
    assert is_valid_commercial_slack_webhook_url(
        "https://hooks.slack.com/services/T_1/B-2/secret_3"
    )


def test_slack_graph_boundary_ignores_legacy_channel_in_webhook_mode():
    validate_slack_graph_boundary(
        [
            _slack_node(
                {
                    "slackMode": "webhook",
                    "channel": "legacy-channel",
                    "authConfig": {},
                    "url": "https://hooks.slack.com/services/a/b/c",
                }
            )
        ],
        require_resolved=True,
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("channel", "   "),
        ("channel", " C123 "),
        ("channel", "C123\nother"),
        ("authConfig", {"token": " token-with-space "}),
    ],
)
def test_resolved_api_rejects_noncanonical_token_or_channel(field, value):
    with pytest.raises(SlackGraphBoundaryError):
        validate_slack_graph_boundary(
            [_slack_node({field: value})],
            require_resolved=True,
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"message": "", "blocks": None, "attachments": None},
        {"message": "   ", "blocks": None, "attachments": None},
        {"message": "", "blocks": [], "attachments": []},
        {"message": "", "blocks": "[]", "attachments": "[]"},
    ],
)
def test_slack_graph_boundary_requires_nonempty_payload_before_deployment(payload):
    node = _slack_node(payload)

    validate_slack_graph_boundary([node])
    with pytest.raises(SlackGraphBoundaryError) as error:
        validate_slack_graph_boundary([node], require_resolved=True)

    assert error.value.reason_code == SLACK_GRAPH_CONFIGURATION_INVALID


@pytest.mark.parametrize(
    "payload",
    [
        {"message": "hello", "blocks": None, "attachments": None},
        {"message": "", "blocks": '[{"type":"section"}]'},
        {"message": "", "attachments": [{"fallback": "notice"}]},
    ],
)
def test_slack_graph_boundary_accepts_each_supported_payload_form(payload):
    validate_slack_graph_boundary(
        [_slack_node(payload)],
        require_resolved=True,
    )


def test_slack_graph_boundary_accepts_scalar_template_inside_blocks_array():
    validate_slack_graph_boundary(
        [
            _slack_node(
                {
                    "message": "",
                    "blocks": '[{"type":"section","expand":{{expanded}}}]',
                    "referenced_variables": [
                        {
                            "name": "expanded",
                            "value_selector": ["input", "expanded"],
                        }
                    ],
                }
            )
        ],
        require_resolved=True,
    )


@pytest.mark.parametrize(
    "blocks",
    [
        {"type": "section"},
        '{"type":"section"}',
        '[{"{{dynamic_key}}":"value"}]',
        '[{"type":"section","type":"divider"}]',
    ],
)
def test_slack_graph_boundary_rejects_invalid_blocks_shape(blocks):
    with pytest.raises(SlackGraphBoundaryError):
        validate_slack_graph_boundary(
            [_slack_node({"blocks": blocks})],
            require_resolved=True,
        )
