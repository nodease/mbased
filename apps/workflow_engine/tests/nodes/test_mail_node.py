"""Mail node credential reference tests."""

import imaplib
import socket
import ssl
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from apps.shared.domain.mail_processing import MailSourceReference
from apps.shared.services.outbound_proxy_policy import (
    OutboundProxyPolicy,
    OutboundTransportMode,
)
from apps.workflow_engine.services.mail_credential_service import (
    ResolvedMailCredential,
)
from apps.workflow_engine.workflow.core.workflow_node_factory import NodeFactory
from apps.workflow_engine.workflow.nodes.mail.entities import MailNodeData, MailVariable
from apps.workflow_engine.workflow.nodes.mail.mail_node import (
    MAIL_IMAP_TIMEOUT_SECONDS,
    MAX_IMAP_MESSAGE_BYTES,
    MailNode,
    _create_pinned_imap_socket,
)


@pytest.fixture
def mock_imap():
    with patch(
        "apps.workflow_engine.workflow.nodes.mail.mail_node._PinnedIMAP4SSL"
    ) as mock:
        yield mock


@pytest.fixture
def resolved_credential():
    return ResolvedMailCredential(
        credential_id=uuid.uuid4(),
        email_address="mailbox@example.test",
        imap_host="imap.example.test",
        imap_port=993,
        resolved_ip="203.0.113.10",
        use_ssl=True,
        secret="synthetic-mail-secret",
    )


@pytest.fixture
def credential_resolver(resolved_credential):
    with patch(
        "apps.workflow_engine.workflow.nodes.mail.mail_node."
        "MailCredentialResolver.resolve",
        return_value=resolved_credential,
    ) as resolver:
        yield resolver


def _node_data(credential_id: uuid.UUID, **overrides) -> MailNodeData:
    data = {
        "title": "Mail Search",
        "credential_id": credential_id,
        "keyword": "test",
        "folder": "INBOX",
        "max_results": 10,
        "referenced_variables": [],
    }
    data.update(overrides)
    return MailNodeData(**data)


def _node(data: MailNodeData) -> MailNode:
    subject_id = uuid.uuid4()
    return MailNode(
        id="mail-test",
        data=data,
        execution_context={
            "user_id": str(uuid.uuid4()),
            "execution_subject": {
                "type": "user",
                "id": str(subject_id),
            },
            "organization_id": str(uuid.uuid4()),
            "workflow_id": str(uuid.uuid4()),
            "deployment_id": str(uuid.uuid4()),
            "db": MagicMock(),
        },
    )


def test_mail_search_success(mock_imap, resolved_credential, credential_resolver):
    mock_mail = MagicMock()
    mock_mail.select.return_value = ("OK", [b"INBOX"])
    mock_mail.search.return_value = ("OK", [b"1 2 3"])
    mock_email_data = b"""From: sender@example.com
To: recipient@example.com
Subject: Test Email
Date: Mon, 05 Jan 2026 10:00:00 +0000

This is a test email body.
"""
    mock_mail.fetch.return_value = ("OK", [(b"1 (RFC822 {123})", mock_email_data)])
    mock_imap.return_value = mock_mail

    node = _node(_node_data(resolved_credential.credential_id))
    result = node._run(inputs={})

    assert result["total_count"] == 3
    assert result["folder"] == "INBOX"
    assert result["emails"][0]["subject"] == "Test Email"
    mock_mail.login.assert_called_once_with(
        "mailbox@example.test", "synthetic-mail-secret"
    )
    assert credential_resolver.call_count == 1
    _, kwargs = mock_imap.call_args
    tls_context = kwargs["ssl_context"]
    assert tls_context.verify_mode == ssl.CERT_REQUIRED
    assert tls_context.check_hostname is True
    assert kwargs["timeout"] == MAIL_IMAP_TIMEOUT_SECONDS


def test_imap_fetch_rejects_oversized_message_before_mime_parsing(
    mock_imap, resolved_credential, credential_resolver
):
    mail = mock_imap.return_value
    mail.select.return_value = ("OK", [b"INBOX"])
    mail.search.return_value = ("OK", [b"1"])
    mail.fetch.return_value = (
        "OK",
        [(b"1 (RFC822)", b"x" * (MAX_IMAP_MESSAGE_BYTES + 1))],
    )
    node = _node(_node_data(resolved_credential.credential_id))

    with pytest.raises(RuntimeError, match="^mail.message_too_large$"):
        node._run(inputs={})


def test_mail_variable_substitution(
    mock_imap, resolved_credential, credential_resolver
):
    mock_mail = MagicMock()
    mock_mail.select.return_value = ("OK", [b"INBOX"])
    mock_mail.search.return_value = ("OK", [b"1"])
    mock_email_data = b"""From: sender@example.com
To: recipient@example.com
Subject: PR #123
Date: Mon, 05 Jan 2026 10:00:00 +0000

Pull request merged.
"""
    mock_mail.fetch.return_value = ("OK", [(b"1 (RFC822 {123})", mock_email_data)])
    mock_imap.return_value = mock_mail
    node = _node(
        _node_data(
            resolved_credential.credential_id,
            keyword="{{pr_number}}",
            referenced_variables=[
                MailVariable(name="pr_number", value_selector=["start-123", "pr_id"])
            ],
        )
    )

    result = node._run(inputs={"start-123": {"pr_id": "PR #123"}})

    assert result["total_count"] == 1
    mock_mail.search.assert_called_once_with(None, mock_mail.search.call_args.args[1])
    assert 'TEXT "PR #123"' in mock_mail.search.call_args.args[1]


def test_mail_search_criteria_escape_quotes_and_backslashes():
    node = _node(_node_data(uuid.uuid4()))

    query = node._build_search_query('report "Q3" \\ final', "", "")

    assert query.startswith('TEXT "report \\"Q3\\" \\\\ final"')


@pytest.mark.parametrize("value", ["safe\r\nA1 NOOP", "safe\x00A1 NOOP"])
def test_mail_search_criteria_reject_protocol_control_characters(value):
    node = _node(_node_data(uuid.uuid4()))

    with pytest.raises(RuntimeError, match="^mail.search_criteria_invalid$"):
        node._build_search_query(value, "", "")


def test_mail_authentication_failure_is_redacted(
    mock_imap, resolved_credential, credential_resolver
):
    mock_imap.return_value.login.side_effect = imaplib.IMAP4.error(
        "provider detail must not escape"
    )
    node = _node(_node_data(resolved_credential.credential_id))

    with pytest.raises(RuntimeError, match="^mail.authentication_failed$") as exc:
        node._run(inputs={})

    assert "provider detail" not in str(exc.value)
    mock_imap.return_value.shutdown.assert_called_once_with()


def test_mail_post_login_provider_failure_is_redacted(
    mock_imap, resolved_credential, credential_resolver
):
    mock_mail = MagicMock()
    mock_mail.select.return_value = ("OK", [b"INBOX"])
    mock_mail.search.side_effect = imaplib.IMAP4.error(
        "provider mailbox detail must not escape"
    )
    mock_imap.return_value = mock_mail
    node = _node(_node_data(resolved_credential.credential_id))

    with pytest.raises(RuntimeError, match="^mail.operation_failed$") as exc:
        node._run(inputs={})

    assert "provider mailbox detail" not in str(exc.value)
    mock_mail.logout.assert_called_once()


def test_mail_logout_failure_does_not_override_successful_result(
    mock_imap, resolved_credential, credential_resolver
):
    mock_mail = MagicMock()
    mock_mail.select.return_value = ("OK", [b"INBOX"])
    mock_mail.search.return_value = ("OK", [b""])
    mock_mail.logout.side_effect = imaplib.IMAP4.abort(
        "provider cleanup detail must not escape"
    )
    mock_imap.return_value = mock_mail
    node = _node(_node_data(resolved_credential.credential_id))

    result = node._run(inputs={})

    assert result["emails"] == []


def test_search_only_marks_selected_messages_seen_in_one_terminal_command(
    mock_imap, resolved_credential, credential_resolver
):
    mock_mail = MagicMock()
    mock_mail.select.return_value = ("OK", [b"INBOX"])
    mock_mail.search.return_value = ("OK", [b"1 2"])
    mock_mail.fetch.return_value = (
        "OK",
        [(b"1 (RFC822 {10})", b"Message-ID: <id@example.com>\n\nbody")],
    )
    mock_mail.store.return_value = ("OK", [b"1 2"])
    mock_imap.return_value = mock_mail

    node = _node(_node_data(resolved_credential.credential_id, mark_as_read=True))
    result = node._run(inputs={})

    assert result["total_count"] == 2
    mock_mail.store.assert_called_once_with(b"1,2", "+FLAGS", "\\Seen")


def test_search_only_fetch_failure_never_marks_messages_seen(
    mock_imap, resolved_credential, credential_resolver
):
    mock_mail = MagicMock()
    mock_mail.select.return_value = ("OK", [b"INBOX"])
    mock_mail.search.return_value = ("OK", [b"1"])
    mock_mail.fetch.return_value = ("NO", [])
    mock_imap.return_value = mock_mail
    node = _node(_node_data(resolved_credential.credential_id, mark_as_read=True))

    with pytest.raises(RuntimeError, match="^mail.fetch_failed$"):
        node._run(inputs={})

    mock_mail.store.assert_not_called()


def test_durable_mode_registers_uid_identity_and_hides_provider_id(
    mock_imap, resolved_credential, credential_resolver
):
    mock_mail = MagicMock()
    mock_mail.select.return_value = ("OK", [b"INBOX"])
    mock_mail.uid.side_effect = [
        ("OK", [b"42"]),
        (
            "OK",
            [
                (
                    b"42 (UID 42 RFC822 {30})",
                    b"Message-ID: <durable@example.com>\nSubject: Durable\n\nbody",
                )
            ],
        ),
    ]
    mock_mail.response.return_value = ("UIDVALIDITY", [b"99"])
    mock_imap.return_value = mock_mail
    processing_service = MagicMock()
    processing_service.register_message.return_value = "opaque-processing-ref"

    node = _node(
        _node_data(
            resolved_credential.credential_id,
            processing_mode="durable",
        )
    )
    node.execution_context["mail_processing_service_factory"] = lambda _db: (
        processing_service
    )
    result = node._run(inputs={})

    assert result["emails"][0]["processing_ref"] == "opaque-processing-ref"
    assert result["processing_ref"] == "opaque-processing-ref"
    assert "id" not in result["emails"][0]
    registration = processing_service.register_message.call_args.args[0]
    assert registration.source.uid_validity == 99
    assert registration.source.uid == 42
    assert registration.source.message_id == "<durable@example.com>"
    assert registration.source.folder == "INBOX"
    mock_mail.store.assert_not_called()


def test_durable_mode_rejects_immediate_mark_as_read():
    with pytest.raises(ValidationError) as exc_info:
        _node_data(uuid.uuid4(), processing_mode="durable", mark_as_read=True)
    assert "mail.processing_configuration_invalid" in str(exc_info.value)


@patch("apps.workflow_engine.workflow.nodes.mail.mail_node._PinnedIMAP4")
def test_port_143_negotiates_starttls_before_login(plain_imap, resolved_credential):
    starttls_credential = ResolvedMailCredential(
        credential_id=resolved_credential.credential_id,
        email_address=resolved_credential.email_address,
        imap_host=resolved_credential.imap_host,
        imap_port=143,
        resolved_ip=resolved_credential.resolved_ip,
        use_ssl=False,
        secret=resolved_credential.secret,
    )
    mail = plain_imap.return_value
    node = _node(_node_data(starttls_credential.credential_id))

    node._connect_imap(starttls_credential)

    _, kwargs = plain_imap.call_args
    assert kwargs["timeout"] == MAIL_IMAP_TIMEOUT_SECONDS
    assert mail.method_calls[0][0] == "starttls"
    starttls_context = mail.starttls.call_args.kwargs["ssl_context"]
    assert starttls_context.verify_mode == ssl.CERT_REQUIRED
    assert starttls_context.check_hostname is True
    assert mail.method_calls[1] == (
        "login",
        ("mailbox@example.test", "synthetic-mail-secret"),
        {},
    )


class _ProxyHandshakeSocket:
    def __init__(self, response: bytes) -> None:
        self._response = bytearray(response)
        self.sent = b""
        self.closed = False

    def sendall(self, payload: bytes) -> None:
        self.sent += payload

    def recv(self, size: int) -> bytes:
        if not self._response:
            return b""
        chunk = bytes(self._response[:size])
        del self._response[:size]
        return chunk

    def close(self) -> None:
        self.closed = True


def _proxy_policy() -> OutboundProxyPolicy:
    return OutboundProxyPolicy(
        mode=OutboundTransportMode.PROXY_GUARDED_EXTERNAL,
        proxy_url="http://proxy:3129",
        allowed_proxy_hosts=("proxy",),
        policy_revision="proxy-v1",
    )


def test_imap_uses_validated_ip_through_proxy_without_direct_fallback(
    monkeypatch,
) -> None:
    tunnel = _ProxyHandshakeSocket(b"HTTP/1.1 200 Connection established\r\n\r\n")
    connect = MagicMock(return_value=tunnel)
    monkeypatch.setattr(socket, "create_connection", connect)
    monkeypatch.setattr(
        "apps.workflow_engine.workflow.nodes.mail.mail_node."
        "outbound_proxy_policy_from_environment",
        _proxy_policy,
    )

    result = _create_pinned_imap_socket("203.0.113.10", 993, timeout=10.0)

    assert result is tunnel
    connect.assert_called_once_with(("proxy", 3129), 10.0)
    assert tunnel.sent == (
        b"CONNECT 203.0.113.10:993 HTTP/1.1\r\n"
        b"Host: 203.0.113.10:993\r\n\r\n"
    )


def test_imap_proxy_rejection_is_fail_closed(monkeypatch) -> None:
    tunnel = _ProxyHandshakeSocket(b"HTTP/1.1 403 Forbidden\r\n\r\n")
    connect = MagicMock(return_value=tunnel)
    monkeypatch.setattr(socket, "create_connection", connect)
    monkeypatch.setattr(
        "apps.workflow_engine.workflow.nodes.mail.mail_node."
        "outbound_proxy_policy_from_environment",
        _proxy_policy,
    )

    with pytest.raises(OSError, match="mail.imap_proxy_tunnel_failed"):
        _create_pinned_imap_socket("203.0.113.10", 993, timeout=10.0)

    connect.assert_called_once_with(("proxy", 3129), 10.0)
    assert tunnel.closed is True


def test_imap_local_direct_mode_keeps_address_pinning(monkeypatch) -> None:
    direct_socket = MagicMock()
    connect = MagicMock(return_value=direct_socket)
    monkeypatch.setattr(socket, "create_connection", connect)
    monkeypatch.setattr(
        "apps.workflow_engine.workflow.nodes.mail.mail_node."
        "outbound_proxy_policy_from_environment",
        lambda: OutboundProxyPolicy(
            mode=OutboundTransportMode.DIRECT_PINNED_INTERNAL_OR_DEDICATED,
            proxy_url=None,
            allowed_proxy_hosts=(),
            policy_revision="direct-v1",
        ),
    )

    result = _create_pinned_imap_socket("203.0.113.10", 993, timeout=10.0)

    assert result is direct_socket
    connect.assert_called_once_with(("203.0.113.10", 993), 10.0)


def test_gmail_oauth_is_rejected_before_imap_connection(mock_imap, resolved_credential):
    oauth_credential = ResolvedMailCredential(
        credential_id=resolved_credential.credential_id,
        email_address=resolved_credential.email_address,
        imap_host=resolved_credential.imap_host,
        imap_port=resolved_credential.imap_port,
        resolved_ip=resolved_credential.resolved_ip,
        use_ssl=True,
        secret="protected-oauth-payload",
        provider="gmail",
        auth_type="oauth2",
    )
    node = _node(_node_data(oauth_credential.credential_id))

    with pytest.raises(RuntimeError, match="^mail.oauth_imap_not_supported$"):
        node._connect_imap(oauth_credential)

    mock_imap.assert_not_called()


def test_non_gmail_oauth_is_rejected_before_imap_connection(
    mock_imap, resolved_credential
):
    oauth_credential = ResolvedMailCredential(
        credential_id=resolved_credential.credential_id,
        email_address=resolved_credential.email_address,
        imap_host=resolved_credential.imap_host,
        imap_port=resolved_credential.imap_port,
        resolved_ip=resolved_credential.resolved_ip,
        use_ssl=True,
        secret="protected-oauth-payload",
        provider="custom",
        auth_type="oauth2",
    )
    node = _node(_node_data(oauth_credential.credential_id))

    with pytest.raises(RuntimeError, match="^mail.oauth_imap_not_supported$"):
        node._connect_imap(oauth_credential)

    mock_imap.assert_not_called()


@patch(
    "apps.workflow_engine.workflow.nodes.mail.mail_node."
    "MailCredentialResolver.refresh_oauth_serialized"
)
@patch(
    "apps.workflow_engine.workflow.nodes.mail.mail_node.MailCredentialResolver.resolve"
)
def test_gmail_oauth_search_uses_rest_provider_without_imap(
    resolve, refresh_oauth, mock_imap
):
    credential_id = uuid.uuid4()
    resolve.return_value = ResolvedMailCredential(
        credential_id=credential_id,
        email_address="mailbox@example.test",
        imap_host="imap.gmail.com",
        imap_port=993,
        resolved_ip="",
        use_ssl=True,
        secret="protected-oauth-payload",
        provider="gmail",
        auth_type="oauth2",
    )
    token_service = MagicMock()
    refresh_oauth.return_value = SimpleNamespace(value="synthetic-access-token")
    provider = MagicMock()
    provider.search.return_value = [
        SimpleNamespace(
            output={"subject": "Question", "body_text": "Body"},
            source_reference=SimpleNamespace(provider_message_id="gmail-message"),
        )
    ]
    provider_factory = MagicMock(return_value=provider)
    node = _node(_node_data(credential_id, mark_as_read=True))
    node.execution_context.update(
        {
            "google_oauth_token_service": token_service,
            "gmail_mailbox_provider_factory": provider_factory,
            "mail_authorization_guard": lambda *_args, **_kwargs: None,
        }
    )

    result = node._run(inputs={})

    assert result["emails"] == [{"subject": "Question", "body_text": "Body"}]
    assert refresh_oauth.call_args.kwargs["refresh"] is token_service.refresh
    provider_factory.assert_called_once_with("synthetic-access-token")
    provider.search.assert_called_once()
    provider.mark_read.assert_called_once()
    mock_imap.assert_not_called()


@patch(
    "apps.workflow_engine.workflow.nodes.mail.mail_node."
    "MailCredentialResolver.refresh_oauth_serialized"
)
@patch(
    "apps.workflow_engine.workflow.nodes.mail.mail_node.MailCredentialResolver.resolve"
)
def test_gmail_oauth_durable_search_registers_protected_provider_reference(
    resolve, refresh_oauth, mock_imap
):
    credential_id = uuid.uuid4()
    credential = ResolvedMailCredential(
        credential_id=credential_id,
        email_address="mailbox@example.test",
        imap_host="imap.gmail.com",
        imap_port=993,
        resolved_ip="",
        use_ssl=True,
        secret="protected-oauth-payload",
        provider="gmail",
        auth_type="oauth2",
    )
    resolve.return_value = credential
    token_service = MagicMock()
    refresh_oauth.return_value = SimpleNamespace(value="synthetic-access-token")
    source = MailSourceReference(
        provider_message_id="gmail-message",
        message_id="<message@example.com>",
    )
    provider = MagicMock()
    provider.search.return_value = [
        SimpleNamespace(output={"subject": "Question"}, source_reference=source)
    ]
    processing_service = MagicMock()
    processing_service.register_message.return_value = "opaque-processing-ref"
    node = _node(_node_data(credential_id, processing_mode="durable", max_results=1))
    node.execution_context.update(
        {
            "google_oauth_token_service": token_service,
            "gmail_mailbox_provider_factory": lambda _token: provider,
            "mail_processing_service_factory": lambda _db: processing_service,
            "mail_authorization_guard": lambda *_args, **_kwargs: None,
        }
    )

    result = node._run(inputs={})

    assert result["processing_ref"] == "opaque-processing-ref"
    registration = processing_service.register_message.call_args.args[0]
    assert registration.source == source
    assert registration.provider == "gmail"
    provider.mark_read.assert_not_called()
    mock_imap.assert_not_called()


def test_mail_empty_results(mock_imap, resolved_credential, credential_resolver):
    mock_mail = MagicMock()
    mock_mail.select.return_value = ("OK", [b"INBOX"])
    mock_mail.search.return_value = ("OK", [b""])
    mock_imap.return_value = mock_mail
    node = _node(_node_data(resolved_credential.credential_id))

    result = node._run(inputs={})

    assert result["total_count"] == 0
    assert result["emails"] == []


def test_node_factory_rejects_legacy_inline_secret_without_echoing_value():
    legacy_value = "synthetic-legacy-secret"
    schema = SimpleNamespace(
        id="mail-test",
        type="mailNode",
        data={
            "title": "Legacy Mail",
            "email": "mailbox@example.test",
            "password": legacy_value,
        },
    )

    with pytest.raises(ValueError, match="^mail.credential_reference_required$") as exc:
        NodeFactory.create(schema)

    assert legacy_value not in str(exc.value)


def test_mail_node_does_not_fallback_to_workflow_or_app_owner_identity():
    node = MailNode(
        id="mail-test",
        data=_node_data(uuid.uuid4()),
        execution_context={
            "user_id": str(uuid.uuid4()),
            "organization_id": str(uuid.uuid4()),
            "db": MagicMock(),
        },
    )

    with (
        patch(
            "apps.workflow_engine.workflow.nodes.mail.mail_node."
            "MailCredentialResolver.resolve"
        ) as resolver,
        pytest.raises(RuntimeError, match="^mail.execution_subject_required$"),
    ):
        node._run(inputs={})

    resolver.assert_not_called()
