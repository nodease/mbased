import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from apps.gateway.application.agent_builder.graph_mutation_builder import (
    canonical_graph_hash,
)
from apps.gateway.services.workflow_service import WorkflowService
from apps.shared.schemas.workflow import NodeSchema, Position, WorkflowDraftRequest


def _request(data: dict) -> WorkflowDraftRequest:
    nodes = [
        NodeSchema(
            id="mail-1",
            type="mailNode",
            position=Position(x=0, y=0),
            data=data,
        )
    ]
    return WorkflowDraftRequest(
        nodes=nodes,
        expected_graph_hash=canonical_graph_hash(
            {"nodes": [node.model_dump(mode="python") for node in nodes], "edges": []}
        ),
        expected_updated_at=datetime(2026, 7, 13, tzinfo=timezone.utc),
    )


def test_draft_rejects_legacy_mail_secret_without_echoing_value():
    secret = "synthetic-legacy-secret"

    with pytest.raises(HTTPException) as exc:
        WorkflowService.validate_mail_credential_references(
            MagicMock(),
            _request(
                {
                    "title": "Legacy Mail",
                    "email": "mailbox@example.test",
                    "password": secret,
                }
            ),
            user_id=str(uuid.uuid4()),
            organization_id=uuid.uuid4(),
        )

    assert exc.value.status_code == 422
    assert exc.value.detail == "mail.credential_reference_required"
    assert secret not in str(exc.value)


def test_unresolved_mail_reference_can_be_saved_for_preview():
    db = MagicMock()

    WorkflowService.validate_mail_credential_references(
        db,
        _request(
            {
                "title": "Mail",
                "credential_id": None,
                "configuration_state": "unresolved",
            }
        ),
        user_id=str(uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )

    db.query.assert_not_called()


def test_legacy_unresolved_mail_reference_without_state_remains_draft_compatible():
    db = MagicMock()
    request = _request(
        {
            "title": "Mail",
            "credential_id": None,
        }
    )

    WorkflowService.validate_mail_credential_references(
        db,
        request,
        user_id=str(uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )

    db.query.assert_not_called()
    with pytest.raises(HTTPException) as exc:
        WorkflowService.validate_mail_credential_references(
            db,
            request,
            user_id=str(uuid.uuid4()),
            organization_id=uuid.uuid4(),
            require_resolved=True,
        )
    assert exc.value.detail == "mail.credential_reference_required"


def test_deployment_validation_rejects_unresolved_mail_reference():
    with pytest.raises(HTTPException) as exc:
        WorkflowService.validate_mail_credential_references(
            MagicMock(),
            _request(
                {
                    "title": "Mail",
                    "credential_id": None,
                    "configuration_state": "unresolved",
                }
            ),
            user_id=str(uuid.uuid4()),
            organization_id=uuid.uuid4(),
            require_resolved=True,
        )

    assert exc.value.status_code == 422
    assert exc.value.detail == "mail.credential_reference_required"


def test_draft_allows_valid_mail_ui_metadata():
    db = MagicMock()

    WorkflowService.validate_mail_credential_references(
        db,
        _request(
            {
                "title": "Mail",
                "credential_id": None,
                "configuration_state": "unresolved",
                "displayNumber": 2,
                "visibleProperties": ["credential_id", "folder", "filters"],
            }
        ),
        user_id=str(uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )

    db.query.assert_not_called()


@pytest.mark.parametrize(
    "ui_metadata",
    [
        {"displayNumber": "synthetic-secret"},
        {"visibleProperties": ["synthetic-secret"]},
    ],
)
def test_draft_rejects_unsafe_mail_ui_metadata(ui_metadata):
    with pytest.raises(HTTPException) as exc:
        WorkflowService.validate_mail_credential_references(
            MagicMock(),
            _request(
                {
                    "title": "Mail",
                    "credential_id": None,
                    **ui_metadata,
                }
            ),
            user_id=str(uuid.uuid4()),
            organization_id=uuid.uuid4(),
        )

    assert exc.value.status_code == 422
    assert exc.value.detail == "mail.credential_reference_required"


def test_nested_subgraph_mail_node_rejects_inline_secret():
    secret = "synthetic-nested-secret"
    graph = {
        "nodes": [
            {
                "id": "loop-1",
                "type": "loopNode",
                "data": {
                    "subGraph": {
                        "nodes": [
                            {
                                "id": "mail-nested",
                                "type": "mailNode",
                                "data": {
                                    "credential_id": None,
                                    "app_password": secret,
                                },
                            }
                        ],
                        "edges": [],
                    }
                },
            }
        ],
        "edges": [],
    }

    with pytest.raises(HTTPException) as exc:
        WorkflowService.validate_mail_credential_references(
            MagicMock(),
            graph,
            user_id=str(uuid.uuid4()),
            organization_id=uuid.uuid4(),
        )

    assert exc.value.status_code == 422
    assert exc.value.detail == "mail.credential_reference_required"
    assert secret not in str(exc.value)


@pytest.mark.parametrize(
    "data",
    [
        {"title": "Mail", "credential_id": None, "app_password": "synthetic-only"},
        {
            "title": "Mail",
            "credential_id": None,
            "parameters": {"secret": "synthetic-only"},
        },
    ],
)
def test_draft_rejects_alternate_mail_secret_storage(data):
    with pytest.raises(HTTPException) as exc:
        WorkflowService.validate_mail_credential_references(
            MagicMock(),
            _request(data),
            user_id=str(uuid.uuid4()),
            organization_id=uuid.uuid4(),
        )

    assert exc.value.status_code == 422
    assert exc.value.detail == "mail.credential_reference_required"
    assert "synthetic-only" not in str(exc.value)


@patch("apps.gateway.services.workflow_service.record_resource_permission_denied")
@patch(
    "apps.gateway.services.workflow_service.get_effective_mail_credential_auth_state",
    return_value="none",
)
@patch(
    "apps.gateway.services.workflow_service.has_mail_credential_permission",
    return_value=False,
)
def test_draft_rechecks_mail_credential_use_permission(
    _permission, _auth_state, record_denial
):
    organization_id = uuid.uuid4()
    credential_id = uuid.uuid4()
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(
        id=credential_id
    )

    with pytest.raises(HTTPException) as exc:
        WorkflowService.validate_mail_credential_references(
            db,
            _request({"title": "Mail", "credential_id": str(credential_id)}),
            user_id=str(uuid.uuid4()),
            organization_id=organization_id,
        )

    assert exc.value.status_code == 403
    assert exc.value.detail == "mail.credential_permission_denied"
    record_denial.assert_called_once()


def test_draft_hides_cross_organization_or_missing_credential():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None

    with pytest.raises(HTTPException) as exc:
        WorkflowService.validate_mail_credential_references(
            db,
            _request({"title": "Mail", "credential_id": str(uuid.uuid4())}),
            user_id=str(uuid.uuid4()),
            organization_id=uuid.uuid4(),
        )

    assert exc.value.status_code == 404
    assert exc.value.detail == "resource.not_found"


def test_raw_agent_builder_graph_rejects_legacy_inline_secret():
    secret = "synthetic-agent-builder-legacy-secret"

    with pytest.raises(HTTPException) as exc:
        WorkflowService.validate_mail_credential_references(
            MagicMock(),
            {
                "nodes": [
                    {
                        "id": "mail-1",
                        "type": "mailNode",
                        "data": {"credential_id": None, "password": secret},
                    }
                ],
                "edges": [],
            },
            user_id=str(uuid.uuid4()),
            organization_id=uuid.uuid4(),
        )

    assert exc.value.status_code == 422
    assert exc.value.detail == "mail.credential_reference_required"
    assert secret not in str(exc.value)


def test_raw_deployment_graph_rejects_non_object_mail_data():
    with pytest.raises(HTTPException) as exc:
        WorkflowService.validate_mail_credential_references(
            MagicMock(),
            {
                "nodes": [
                    {
                        "id": "mail-1",
                        "type": "mailNode",
                        "data": "synthetic-only",
                    }
                ],
                "edges": [],
            },
            user_id=str(uuid.uuid4()),
            organization_id=uuid.uuid4(),
        )

    assert exc.value.status_code == 422
    assert exc.value.detail == "mail.credential_reference_required"
    assert "synthetic-only" not in str(exc.value)


def _durable_draft_graph(credential_id: uuid.UUID) -> dict:
    return {
        "nodes": [
            {
                "id": "mail-source",
                "type": "mailNode",
                "data": {
                    "title": "Mail",
                    "credential_id": str(credential_id),
                    "folder": "INBOX",
                    "max_results": 1,
                    "unread_only": True,
                    "mark_as_read": False,
                    "processing_mode": "durable",
                    "referenced_variables": [],
                },
            },
            {
                "id": "llm-reply",
                "type": "llmNode",
                "data": {"title": "LLM"},
            },
            {
                "id": "draft-effect",
                "type": "gmailDraftNode",
                "data": {
                    "title": "Draft",
                    "credential_id": str(credential_id),
                    "configuration_state": "resolved",
                    "processing_ref_selector": [
                        "mail-source",
                        "processing_ref",
                    ],
                    "reply_body_selector": ["llm-reply", "text"],
                },
            },
            {
                "id": "mail-ack",
                "type": "mailAcknowledgeNode",
                "data": {
                    "title": "Ack",
                    "processing_ref_selector": [
                        "mail-source",
                        "processing_ref",
                    ],
                    "required_effect_ref_selectors": [["draft-effect", "draft_ref"]],
                },
            },
        ],
        "edges": [
            {"id": "e1", "source": "mail-source", "target": "llm-reply"},
            {"id": "e2", "source": "llm-reply", "target": "draft-effect"},
            {"id": "e3", "source": "draft-effect", "target": "mail-ack"},
        ],
    }


def test_unresolved_processing_nodes_can_be_saved_but_not_deployed():
    graph = {
        "nodes": [
            {
                "id": "draft-effect",
                "type": "gmailDraftNode",
                "data": {
                    "title": "Draft",
                    "credential_id": None,
                    "configuration_state": "unresolved",
                    "processing_ref_selector": [],
                    "reply_body_selector": [],
                },
            },
            {
                "id": "mail-ack",
                "type": "mailAcknowledgeNode",
                "data": {
                    "title": "Ack",
                    "processing_ref_selector": [],
                    "required_effect_ref_selectors": [],
                },
            },
        ],
        "edges": [],
    }

    WorkflowService.validate_mail_credential_references(
        MagicMock(),
        graph,
        user_id=str(uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )
    with pytest.raises(HTTPException) as exc:
        WorkflowService.validate_mail_credential_references(
            MagicMock(),
            graph,
            user_id=str(uuid.uuid4()),
            organization_id=uuid.uuid4(),
            require_resolved=True,
        )
    assert exc.value.detail == "mail.processing_configuration_invalid"


@patch(
    "apps.gateway.services.workflow_service.has_mail_credential_permission",
    return_value=True,
)
def test_deployment_accepts_consistent_durable_gmail_draft_graph(_permission):
    credential_id = uuid.uuid4()
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(
        id=credential_id,
        provider="gmail",
        auth_type="oauth2",
    )

    WorkflowService.validate_mail_credential_references(
        db,
        _durable_draft_graph(credential_id),
        user_id=str(uuid.uuid4()),
        organization_id=uuid.uuid4(),
        require_resolved=True,
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda graph: graph["nodes"][0]["data"].update(
            {"processing_mode": "search_only"}
        ),
        lambda graph: graph["nodes"][0]["data"].update({"max_results": 2}),
        lambda graph: graph["nodes"][2]["data"].update(
            {"processing_ref_selector": ["llm-reply", "processing_ref"]}
        ),
        lambda graph: graph["nodes"][3]["data"].update(
            {"required_effect_ref_selectors": [["llm-reply", "text"]]}
        ),
        lambda graph: graph["edges"].pop(),
    ],
)
def test_deployment_rejects_inconsistent_mail_processing_graph(mutate):
    graph = _durable_draft_graph(uuid.uuid4())
    mutate(graph)

    with pytest.raises(HTTPException) as exc:
        WorkflowService.validate_mail_credential_references(
            MagicMock(),
            graph,
            user_id=str(uuid.uuid4()),
            organization_id=uuid.uuid4(),
            require_resolved=True,
        )
    assert exc.value.detail == "mail.processing_configuration_invalid"


@patch(
    "apps.gateway.services.workflow_service.has_mail_credential_permission",
    return_value=True,
)
def test_deployment_rejects_non_oauth_credential_for_gmail_draft(_permission):
    credential_id = uuid.uuid4()
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(
        id=credential_id,
        provider="gmail",
        auth_type="app_password",
    )

    with pytest.raises(HTTPException) as exc:
        WorkflowService.validate_mail_credential_references(
            db,
            _durable_draft_graph(credential_id),
            user_id=str(uuid.uuid4()),
            organization_id=uuid.uuid4(),
            require_resolved=True,
        )
    assert exc.value.detail == "mail.gmail_oauth_credential_required"


@patch("apps.gateway.services.workflow_service.record_resource_permission_denied")
@patch(
    "apps.gateway.services.workflow_service.get_effective_mail_credential_auth_state",
    return_value="none",
)
@patch(
    "apps.gateway.services.workflow_service.has_mail_credential_permission",
    return_value=False,
)
def test_gmail_credential_capability_is_not_disclosed_before_permission(
    _permission,
    _auth_state,
    _record_denial,
):
    credential_id = uuid.uuid4()
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(
        id=credential_id,
        provider="gmail",
        auth_type="app_password",
    )

    with pytest.raises(HTTPException) as exc:
        WorkflowService.validate_mail_credential_references(
            db,
            _durable_draft_graph(credential_id),
            user_id=str(uuid.uuid4()),
            organization_id=uuid.uuid4(),
            require_resolved=True,
        )
    assert exc.value.status_code == 403
    assert exc.value.detail == "mail.credential_permission_denied"
