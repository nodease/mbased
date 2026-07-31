import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from apps.shared.domain.mail_processing import MailSourceReference
from apps.workflow_engine.services.mail_credential_service import (
    ResolvedMailCredential,
)
from apps.workflow_engine.workflow.nodes.mail.acknowledge_node import (
    ImapAcknowledgementAdapter,
    MailAcknowledgeNode,
)
from apps.workflow_engine.workflow.nodes.mail.entities import (
    GmailDraftNodeData,
    MailAcknowledgeNodeData,
)
from apps.workflow_engine.workflow.nodes.mail.gmail_draft_node import GmailDraftNode


def _context():
    return {
        "organization_id": str(uuid.uuid4()),
        "workflow_id": str(uuid.uuid4()),
        "workflow_run_id": str(uuid.uuid4()),
        "workflow_task_id": "task-safe-id",
        "execution_subject": {"type": "user", "id": str(uuid.uuid4())},
        "db": MagicMock(),
    }


def _oauth_credential(credential_id):
    return ResolvedMailCredential(
        credential_id=credential_id,
        email_address="mailbox@example.test",
        imap_host="imap.gmail.com",
        imap_port=993,
        resolved_ip="203.0.113.10",
        use_ssl=True,
        secret="protected-oauth-payload",
        provider="gmail",
        auth_type="oauth2",
    )


@patch(
    "apps.workflow_engine.workflow.nodes.mail.gmail_draft_node."
    "MailCredentialResolver.refresh_oauth_serialized"
)
@patch(
    "apps.workflow_engine.workflow.nodes.mail.gmail_draft_node."
    "MailCredentialResolver.resolve"
)
def test_gmail_draft_node_resolves_selectors_and_uses_injected_ports(
    resolve, refresh_oauth
):
    credential_id = uuid.uuid4()
    resolve.return_value = _oauth_credential(credential_id)
    processing_service = MagicMock()
    processing_service.create_gmail_reply_draft.return_value = {
        "status": "succeeded",
        "draft_ref": str(uuid.uuid4()),
    }
    token_service = MagicMock()
    refresh_oauth.return_value = SimpleNamespace(value="access-token")
    provider = MagicMock()
    provider_factory = MagicMock(return_value=provider)
    context = _context()
    context.update(
        {
            "mail_processing_service_factory": lambda _db: processing_service,
            "google_oauth_token_service": token_service,
            "gmail_draft_provider_factory": provider_factory,
        }
    )
    node = GmailDraftNode(
        "gmail-draft",
        GmailDraftNodeData(
            title="Draft",
            credential_id=credential_id,
            configuration_state="resolved",
            processing_ref_selector=["mail", "processing_ref"],
            reply_body_selector=["llm", "result"],
        ),
        execution_context=context,
    )

    result = node._run(
        {
            "mail": {"processing_ref": str(uuid.uuid4())},
            "llm": {"result": "답장 본문"},
        }
    )

    assert result["status"] == "succeeded"
    kwargs = processing_service.create_gmail_reply_draft.call_args.kwargs
    assert kwargs["node_id"] == "gmail-draft"
    assert kwargs["reply_body"] == "답장 본문"
    resolve.assert_not_called()
    assert kwargs["provider_factory"]() is provider
    assert refresh_oauth.call_args.kwargs["refresh"] is token_service.refresh
    provider_factory.assert_called_once_with("access-token", "mailbox@example.test")


@patch(
    "apps.workflow_engine.workflow.nodes.mail.gmail_draft_node."
    "MailCredentialResolver.resolve"
)
def test_gmail_draft_node_rejects_non_oauth_credential(resolve):
    credential_id = uuid.uuid4()
    credential = _oauth_credential(credential_id)
    resolve.return_value = ResolvedMailCredential(
        **{**credential.__dict__, "auth_type": "app_password"}
    )
    processing_service = MagicMock()
    processing_service.create_gmail_reply_draft.side_effect = (
        lambda **kwargs: kwargs["provider_factory"]()
    )
    context = _context()
    context["mail_processing_service_factory"] = lambda _db: processing_service
    node = GmailDraftNode(
        "gmail-draft",
        GmailDraftNodeData(
            title="Draft",
            credential_id=credential_id,
            processing_ref_selector=["mail", "processing_ref"],
            reply_body_selector=["llm", "result"],
        ),
        execution_context=context,
    )

    with pytest.raises(RuntimeError, match="^mail.gmail_oauth_credential_required$"):
        node._run(
            {
                "mail": {"processing_ref": str(uuid.uuid4())},
                "llm": {"result": "reply"},
            }
        )


@patch(
    "apps.workflow_engine.workflow.nodes.mail.acknowledge_node."
    "MailCredentialResolver.resolve"
)
def test_acknowledge_node_derives_credential_and_requires_effect_refs(resolve):
    credential_id = uuid.uuid4()
    resolve.return_value = _oauth_credential(credential_id)
    processing_service = MagicMock()
    processing_service.acknowledge_message.return_value = {
        "status": "succeeded",
        "processing_ref": str(uuid.uuid4()),
    }
    acknowledgement = MagicMock()
    context = _context()
    context.update(
        {
            "mail_processing_service_factory": lambda _db: processing_service,
            "mail_acknowledgement_factory": lambda _credential: acknowledgement,
        }
    )
    node = MailAcknowledgeNode(
        "mail-ack",
        MailAcknowledgeNodeData(
            title="Acknowledge",
            processing_ref_selector=["mail", "processing_ref"],
            required_effect_ref_selectors=[["draft", "draft_ref"]],
        ),
        execution_context=context,
    )
    processing_ref = str(uuid.uuid4())
    draft_ref = str(uuid.uuid4())

    result = node._run(
        {
            "mail": {"processing_ref": processing_ref},
            "draft": {"draft_ref": draft_ref},
        }
    )

    assert result["status"] == "succeeded"
    kwargs = processing_service.acknowledge_message.call_args.kwargs
    assert kwargs["effect_refs"] == [draft_ref]
    assert kwargs["deployment_id"] is None
    assert len(kwargs["lease_owner_hash"]) == 64
    resolve.assert_not_called()
    assert kwargs["acknowledgement_factory"](credential_id) is acknowledgement


@patch(
    "apps.workflow_engine.workflow.nodes.mail.acknowledge_node."
    "MailCredentialResolver.refresh_oauth_serialized"
)
def test_acknowledge_node_uses_gmail_rest_provider_for_oauth_credential(
    refresh_oauth,
):
    credential_id = uuid.uuid4()
    token_service = MagicMock()
    refresh_oauth.return_value = SimpleNamespace(value="access-token")
    provider = MagicMock()
    provider_factory = MagicMock(return_value=provider)
    context = _context()
    context.update(
        {
            "google_oauth_token_service": token_service,
            "gmail_mailbox_provider_factory": provider_factory,
        }
    )
    node = MailAcknowledgeNode(
        "mail-ack",
        MailAcknowledgeNodeData(
            title="Acknowledge",
            processing_ref_selector=["mail", "processing_ref"],
            required_effect_ref_selectors=[["draft", "draft_ref"]],
        ),
        execution_context=context,
    )
    credential = _oauth_credential(credential_id)

    acknowledgement = node._default_acknowledgement(
        credential,
        db=context["db"],
        user_id=uuid.UUID(context["execution_subject"]["id"]),
        organization_id=uuid.UUID(context["organization_id"]),
    )

    assert acknowledgement is provider
    assert refresh_oauth.call_args.kwargs["refresh"] is token_service.refresh
    provider_factory.assert_called_once_with("access-token")


@patch(
    "apps.workflow_engine.workflow.nodes.mail.acknowledge_node._connect_imap_credential"
)
def test_imap_acknowledgement_fails_closed_on_uidvalidity_change(connect):
    mail = connect.return_value
    mail.select.return_value = ("OK", [b"INBOX"])
    mail.response.return_value = ("UIDVALIDITY", [b"100"])
    adapter = ImapAcknowledgementAdapter(_oauth_credential(uuid.uuid4()))

    with pytest.raises(RuntimeError, match="^mail.message_identity_stale$"):
        adapter.acknowledge(
            source_reference=MailSourceReference(
                uid_validity=99,
                uid=42,
                message_id="<id@example.com>",
                folder="INBOX",
            )
        )

    mail.uid.assert_not_called()
    mail.close.assert_called_once()
    mail.logout.assert_called_once()


@patch(
    "apps.workflow_engine.workflow.nodes.mail.acknowledge_node._connect_imap_credential"
)
def test_imap_acknowledgement_uses_uid_store_only_after_identity_check(connect):
    mail = connect.return_value
    mail.select.return_value = ("OK", [b"INBOX"])
    mail.response.return_value = ("UIDVALIDITY", [b"99"])
    mail.uid.return_value = ("OK", [b"42"])
    adapter = ImapAcknowledgementAdapter(_oauth_credential(uuid.uuid4()))

    adapter.acknowledge(
        source_reference=MailSourceReference(
            uid_validity=99,
            uid=42,
            message_id="<id@example.com>",
            folder="INBOX",
        )
    )

    mail.uid.assert_called_once_with("store", "42", "+FLAGS", "\\Seen")
