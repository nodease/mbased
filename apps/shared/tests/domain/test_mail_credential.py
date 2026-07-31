import uuid

import pytest
from apps.shared.domain.mail_credential import (
    MailNodeCredentialBoundaryError,
    MailProcessingGraphBoundaryError,
    validate_mail_node_credential_boundary,
    validate_mail_processing_graph_contract,
    validate_mail_processing_node_boundary,
)


def test_durable_mail_node_rejects_immediate_mark_as_read():
    with pytest.raises(MailNodeCredentialBoundaryError):
        validate_mail_node_credential_boundary(
            {
                "title": "Mail",
                "credential_id": str(uuid.uuid4()),
                "processing_mode": "durable",
                "mark_as_read": True,
            }
        )


def test_gmail_draft_boundary_accepts_only_opaque_selectors():
    validate_mail_processing_node_boundary(
        "gmailDraftNode",
        {
            "title": "Draft",
            "credential_id": None,
            "configuration_state": "unresolved",
            "processing_ref_selector": ["mail", "processing_ref"],
            "reply_body_selector": ["llm", "result"],
        },
    )
    with pytest.raises(MailNodeCredentialBoundaryError):
        validate_mail_processing_node_boundary(
            "gmailDraftNode",
            {
                "title": "Draft",
                "processing_ref_selector": ["mail", "processing_ref"],
                "reply_body_selector": ["llm", "result"],
                "recipient": "attacker@example.com",
            },
        )


def test_mail_acknowledge_boundary_requires_effect_selectors():
    with pytest.raises(MailNodeCredentialBoundaryError):
        validate_mail_processing_node_boundary(
            "mailAcknowledgeNode",
            {
                "title": "Acknowledge",
                "processing_ref_selector": ["mail", "processing_ref"],
                "required_effect_ref_selectors": [],
            },
        )


def test_mail_acknowledge_boundary_accepts_server_configuration_state():
    validate_mail_processing_node_boundary(
        "mailAcknowledgeNode",
        {
            "title": "Acknowledge",
            "configuration_state": "unresolved",
            "processing_ref_selector": [],
            "required_effect_ref_selectors": [],
            "_deferred_parameters": [],
        },
        allow_unresolved=True,
    )
    with pytest.raises(MailNodeCredentialBoundaryError):
        validate_mail_processing_node_boundary(
            "mailAcknowledgeNode",
            {
                "title": "Acknowledge",
                "configuration_state": "unknown",
                "processing_ref_selector": [],
                "required_effect_ref_selectors": [],
            },
            allow_unresolved=True,
        )


def test_processing_node_allows_empty_selectors_only_for_unresolved_draft():
    data = {
        "title": "Draft",
        "credential_id": None,
        "configuration_state": "unresolved",
        "processing_ref_selector": [],
        "reply_body_selector": [],
    }

    validate_mail_processing_node_boundary(
        "gmailDraftNode",
        data,
        allow_unresolved=True,
    )
    with pytest.raises(MailNodeCredentialBoundaryError):
        validate_mail_processing_node_boundary("gmailDraftNode", data)


def test_mail_node_credential_boundary_accepts_only_reference_configuration():
    validate_mail_node_credential_boundary(
        {
            "title": "Mail",
            "credential_id": None,
            "configuration_state": "unresolved",
            "parameters": {},
        }
    )


def test_mail_boundaries_allow_only_catalog_deferred_parameter_metadata():
    validate_mail_node_credential_boundary(
        {
            "title": "Mail",
            "credential_id": None,
            "configuration_state": "unresolved",
            "_deferred_parameters": ["credential_id"],
        }
    )
    validate_mail_processing_node_boundary(
        "gmailDraftNode",
        {
            "title": "Draft",
            "credential_id": None,
            "configuration_state": "unresolved",
            "processing_ref_selector": [],
            "reply_body_selector": [],
            "_deferred_parameters": ["credential_id"],
        },
        allow_unresolved=True,
    )

    with pytest.raises(MailNodeCredentialBoundaryError):
        validate_mail_node_credential_boundary(
            {
                "title": "Mail",
                "credential_id": None,
                "configuration_state": "unresolved",
                "_deferred_parameters": ["api_token"],
            }
        )
    with pytest.raises(MailNodeCredentialBoundaryError):
        validate_mail_node_credential_boundary(
            {
                "title": "Mail",
                "credential_id": None,
                "configuration_state": "unresolved",
                "_deferred_parameters": [{"unexpected": True}],
            }
        )


def test_mail_node_boundary_accepts_legacy_null_reference_without_state():
    validate_mail_node_credential_boundary(
        {
            "title": "Mail",
            "credential_id": None,
        }
    )


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("title", None),
        ("folder", "ARCHIVE"),
        ("max_results", 0),
        ("max_results", 101),
        ("max_results", True),
        ("max_results", "10"),
        ("unread_only", "false"),
        ("mark_as_read", 0),
        ("keyword", 1),
        ("referenced_variables", "invalid"),
    ],
)
def test_mail_node_boundary_matches_worker_field_contract(
    field_name,
    invalid_value,
):
    data = {
        "title": "Mail",
        "credential_id": str(uuid.uuid4()),
        "configuration_state": "resolved",
    }
    data[field_name] = invalid_value

    with pytest.raises(MailNodeCredentialBoundaryError):
        validate_mail_node_credential_boundary(data)


def test_mail_node_boundary_requires_unresolved_state_for_null_reference():
    with pytest.raises(MailNodeCredentialBoundaryError):
        validate_mail_node_credential_boundary(
            {
                "title": "Mail",
                "credential_id": None,
                "configuration_state": "resolved",
            }
        )


def test_mail_node_boundary_rejects_unresolved_state_for_non_null_reference():
    with pytest.raises(MailNodeCredentialBoundaryError):
        validate_mail_node_credential_boundary(
            {
                "title": "Mail",
                "credential_id": str(uuid.uuid4()),
                "configuration_state": "unresolved",
            }
        )


def test_mail_node_boundary_does_not_treat_empty_reference_as_null():
    with pytest.raises(MailNodeCredentialBoundaryError):
        validate_mail_node_credential_boundary(
            {
                "title": "Mail",
                "credential_id": "",
                "configuration_state": "unresolved",
            }
        )


@pytest.mark.parametrize(
    "data",
    [
        "synthetic-only",
        {"title": "Mail", "app_password": "synthetic-only"},
        {"title": "Mail", "parameters": {"secret": "synthetic-only"}},
        {"title": "Mail", "credential": "synthetic-only"},
    ],
)
def test_mail_node_credential_boundary_rejects_alternate_secret_fields(data):
    with pytest.raises(
        MailNodeCredentialBoundaryError,
        match="^mail.credential_reference_required$",
    ):
        validate_mail_node_credential_boundary(data)


def test_mail_processing_graph_contract_accepts_reachable_durable_chain():
    graph = _processing_graph()

    validate_mail_processing_graph_contract(graph, require_resolved=True)


def test_mail_processing_graph_contract_rejects_unreachable_effect():
    graph = _processing_graph()
    graph["edges"] = [edge for edge in graph["edges"] if edge["target"] != "ack"]

    with pytest.raises(MailProcessingGraphBoundaryError) as exc_info:
        validate_mail_processing_graph_contract(graph, require_resolved=True)

    assert exc_info.value.node_id == "ack"


def test_mail_processing_graph_contract_normalizes_malformed_effect_selector():
    graph = _processing_graph()
    graph["nodes"][-1]["data"]["required_effect_ref_selectors"] = [[]]

    with pytest.raises(MailProcessingGraphBoundaryError) as exc_info:
        validate_mail_processing_graph_contract(graph, require_resolved=True)

    assert exc_info.value.node_id == "ack"


def _processing_graph():
    credential_id = str(uuid.uuid4())
    processing_selector = ["mail", "processing_ref"]
    return {
        "nodes": [
            {
                "id": "mail",
                "type": "mailNode",
                "data": {
                    "credential_id": credential_id,
                    "processing_mode": "durable",
                    "max_results": 1,
                    "mark_as_read": False,
                },
            },
            {
                "id": "llm",
                "type": "llmNode",
                "data": {},
            },
            {
                "id": "draft",
                "type": "gmailDraftNode",
                "data": {
                    "credential_id": credential_id,
                    "processing_ref_selector": processing_selector,
                    "reply_body_selector": ["llm", "result"],
                },
            },
            {
                "id": "ack",
                "type": "mailAcknowledgeNode",
                "data": {
                    "processing_ref_selector": processing_selector,
                    "required_effect_ref_selectors": [["draft", "draft_ref"]],
                },
            },
        ],
        "edges": [
            {"source": "mail", "target": "llm"},
            {"source": "llm", "target": "draft"},
            {"source": "draft", "target": "ack"},
        ],
    }
