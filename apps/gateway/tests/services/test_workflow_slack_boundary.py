import uuid
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from apps.gateway.services.app_service import AppService
from apps.gateway.services.workflow_service import WorkflowService


def _graph(data, *, selector=None):
    nodes = [
        {
            "id": "slack-1",
            "type": "slackPostNode",
            "data": {
                "title": "Slack",
                "slackMode": "api",
                "channel": "C123",
                "message": "message",
                "authConfig": {"token": "fixture-token"},
                "referenced_variables": [],
                **data,
            },
        }
    ]
    if selector:
        nodes.append(
            {
                "id": "consumer",
                "type": "templateNode",
                "data": {
                    "title": "Consumer",
                    "referenced_variables": [
                        {"name": "legacy", "value_selector": selector}
                    ],
                },
            }
        )
    return {"nodes": nodes, "edges": []}


def _validate(graph, *, require_resolved=False):
    return WorkflowService.validate_mail_credential_references(
        MagicMock(),
        graph,
        user_id=str(uuid.uuid4()),
        organization_id=uuid.uuid4(),
        require_resolved=require_resolved,
    )


def test_draft_allows_legacy_selector_warning_path_but_deployment_rejects_it():
    graph = _graph({}, selector=["slack-1", "headers"])

    _validate(graph)
    with pytest.raises(HTTPException) as exc:
        _validate(graph, require_resolved=True)

    assert exc.value.status_code == 422
    assert exc.value.detail == "slack.graph_configuration_invalid"


def test_draft_rejects_alternate_slack_request_source_without_echoing_value():
    raw_value = "synthetic-secret-value"

    with pytest.raises(HTTPException) as exc:
        _validate(_graph({"headers": [{"key": "Authorization", "value": raw_value}]}))

    assert exc.value.status_code == 422
    assert exc.value.detail == "slack.graph_configuration_invalid"
    assert raw_value not in str(exc.value)


def test_draft_preserves_incomplete_webhook_but_deployment_requires_canonical_url():
    graph = _graph(
        {
            "slackMode": "webhook",
            "channel": "",
            "authConfig": {},
            "url": "https://slack.com/api/chat.postMessage",
        }
    )

    _validate(graph)
    with pytest.raises(HTTPException) as exc:
        _validate(graph, require_resolved=True)

    assert exc.value.status_code == 422
    assert exc.value.detail == "slack.graph_configuration_invalid"


def test_deployment_rejects_webhook_message_ref_selector_but_draft_preserves_it():
    graph = _graph(
        {
            "slackMode": "webhook",
            "channel": "legacy-channel",
            "authConfig": {},
            "url": "https://hooks.slack.com/services/a/b/c",
        },
        selector=["slack-1", "message_ref"],
    )

    _validate(graph)
    with pytest.raises(HTTPException) as exc:
        _validate(graph, require_resolved=True)

    assert exc.value.status_code == 422
    assert exc.value.detail == "slack.graph_configuration_invalid"


def test_deployment_accepts_webhook_with_legacy_hidden_channel():
    graph = _graph(
        {
            "slackMode": "webhook",
            "channel": "legacy-channel",
            "authConfig": {},
            "url": "https://hooks.slack.com/services/a/b/c",
        }
    )

    _validate(graph, require_resolved=True)


def test_deployment_rejects_slack_without_message_blocks_or_attachments():
    graph = _graph({"message": "", "blocks": "[]", "attachments": []})

    _validate(graph)
    with pytest.raises(HTTPException) as exc:
        _validate(graph, require_resolved=True)

    assert exc.value.status_code == 422
    assert exc.value.detail == "slack.graph_configuration_invalid"


def test_public_graph_projection_removes_slack_secret_material():
    graph = _graph(
        {
            "url": "https://hooks.slack.com/services/a/b/c",
            "headers": [{"key": "Authorization", "value": "legacy-token"}],
            "body": '{"text":"legacy-message"}',
        }
    )
    graph["nodes"][0]["data"]["authConfig"] = {"token": "fixture-token"}

    cleaned = AppService._clean_graph_data(graph)

    assert "authConfig" not in cleaned["nodes"][0]["data"]
    assert "url" not in cleaned["nodes"][0]["data"]
    assert "headers" not in cleaned["nodes"][0]["data"]
    assert "body" not in cleaned["nodes"][0]["data"]


def test_public_graph_projection_removes_nested_slack_secret_material():
    graph = {
        "nodes": [
            {
                "id": "loop",
                "type": "loopNode",
                "data": {
                    "subGraph": {
                        "nodes": [
                            {
                                "id": "nested-slack",
                                "type": "slackPostNode",
                                "data": {
                                    "authConfig": {"token": "fixture-token"},
                                    "url": "https://hooks.slack.com/services/a/b/c",
                                    "headers": [
                                        {
                                            "key": "Authorization",
                                            "value": "legacy-token",
                                        }
                                    ],
                                    "body": '{"text":"legacy-message"}',
                                },
                            }
                        ]
                    }
                },
            }
        ]
    }

    cleaned = AppService._clean_graph_data(graph)
    nested = cleaned["nodes"][0]["data"]["subGraph"]["nodes"][0]["data"]

    assert "authConfig" not in nested
    assert "url" not in nested
    assert "headers" not in nested
    assert "body" not in nested
