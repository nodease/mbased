import base64
import inspect
from email import message_from_bytes
from types import SimpleNamespace

import httpx
import pytest

from apps.shared.domain.mail_processing import MailSourceReference
from apps.shared.services.outbound_operation_http import OperationHttpRequester
from apps.workflow_engine.adapters import gmail_draft_provider as provider_module
from apps.workflow_engine.adapters.gmail_draft_provider import (
    GmailDraftProvider,
    build_reply_mime,
)
from apps.workflow_engine.application.mail_processing import (
    GmailDraftOutcomeUnknown,
    GmailDraftRejectedBeforeEffect,
    GmailDraftRequest,
    GmailSourceMessage,
)


def _source():
    return GmailSourceMessage(
        message_id="gmail-message",
        thread_id="gmail-thread",
        rfc_message_id="<message@example.com>",
        references=("<parent@example.com>",),
        reply_to="Sender <sender@example.com>",
        subject="Question",
    )


def _requester(handler) -> OperationHttpRequester:
    def client_factory(**kwargs):
        kwargs.pop("transport")
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    return OperationHttpRequester(client_factory=client_factory)


def test_mime_builder_preserves_reply_thread_headers_and_unicode():
    raw = build_reply_mime(
        mailbox_email="mailbox@example.com",
        source=_source(),
        reply_body="안녕하세요. 답변입니다.",
    )
    message = message_from_bytes(base64.urlsafe_b64decode(raw))

    assert message["To"] == "sender@example.com"
    assert message["Subject"] == "Re: Question"
    assert message["In-Reply-To"] == "<message@example.com>"
    assert "<parent@example.com>" in message["References"]
    assert "<message@example.com>" in message["References"]


@pytest.mark.parametrize(
    "reply_to,subject",
    [
        ("sender@example.com\r\nBcc: victim@example.com", "Question"),
        ("one@example.com,two@example.com", "Question"),
        ("sender@example.com", "Question\nBcc: victim@example.com"),
    ],
)
def test_mime_builder_rejects_header_injection_and_multiple_recipients(
    reply_to, subject
):
    source = GmailSourceMessage(
        **{**_source().__dict__, "reply_to": reply_to, "subject": subject}
    )
    with pytest.raises(GmailDraftRejectedBeforeEffect):
        build_reply_mime(
            mailbox_email="mailbox@example.com",
            source=source,
            reply_body="reply",
        )


def test_provider_resolves_source_and_creates_draft_without_send_surface():
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.method == "GET" and request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "gmail-message"}]})
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "id": "gmail-message",
                    "threadId": "gmail-thread",
                    "payload": {
                        "headers": [
                            {"name": "From", "value": "sender@example.com"},
                            {"name": "Subject", "value": "Question"},
                            {
                                "name": "Message-ID",
                                "value": "<message@example.com>",
                            },
                        ]
                    },
                },
            )
        assert request.url.path.endswith("/drafts")
        return httpx.Response(200, json={"id": "provider-draft"})

    provider = GmailDraftProvider(
        access_token="synthetic-access-token",
        mailbox_email="mailbox@example.com",
        requester=_requester(handler),
    )
    source = provider.resolve_source_message(
        source_reference=MailSourceReference(
            uid_validity=1, uid=2, message_id="<message@example.com>"
        )
    )
    created = provider.create_reply_draft(
        GmailDraftRequest(source=source, reply_body="reply")
    )

    assert created.provider_draft_id == "provider-draft"
    assert calls[-1] == ("POST", "/gmail/v1/users/me/drafts")
    production_source = inspect.getsource(provider_module)
    assert "drafts.send" not in production_source
    assert "messages.send" not in production_source


def test_provider_rejects_source_lookup_with_mismatched_rfc_message_id():
    def handler(request):
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "gmail-message"}]})
        return httpx.Response(
            200,
            json={
                "id": "gmail-message",
                "threadId": "gmail-thread",
                "payload": {
                    "headers": [
                        {"name": "From", "value": "sender@example.com"},
                        {"name": "Message-ID", "value": "<other@example.com>"},
                    ]
                },
            },
        )

    provider = GmailDraftProvider(
        access_token="synthetic-access-token",
        mailbox_email="mailbox@example.com",
        requester=_requester(handler),
    )

    with pytest.raises(GmailDraftRejectedBeforeEffect) as exc_info:
        provider.resolve_source_message(
            source_reference=MailSourceReference(
                uid_validity=1,
                uid=2,
                message_id="<message@example.com>",
            )
        )
    assert exc_info.value.reason_code == "mail.gmail_source_message_invalid"


def test_provider_uses_protected_gmail_message_id_without_search_round_trip():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "id": "gmail-message",
                "threadId": "gmail-thread",
                "payload": {
                    "headers": [
                        {"name": "From", "value": "sender@example.com"},
                        {"name": "Message-ID", "value": "<message@example.com>"},
                    ]
                },
            },
        )

    provider = GmailDraftProvider(
        access_token="synthetic-access-token",
        mailbox_email="mailbox@example.com",
        requester=_requester(handler),
    )

    source = provider.resolve_source_message(
        source_reference=MailSourceReference(
            provider_message_id="gmail-message",
            message_id="<message@example.com>",
        )
    )

    assert source.message_id == "gmail-message"
    assert calls == ["/gmail/v1/users/me/messages/gmail-message"]


def test_read_timeout_after_draft_request_is_outcome_unknown():
    def handler(request):
        raise httpx.ReadTimeout("timeout after request", request=request)

    provider = GmailDraftProvider(
        access_token="synthetic-access-token",
        mailbox_email="mailbox@example.com",
        requester=_requester(handler),
    )
    with pytest.raises(GmailDraftOutcomeUnknown) as exc_info:
        provider.create_reply_draft(
            GmailDraftRequest(source=_source(), reply_body="reply")
        )
    assert exc_info.value.reason_code == "mail.draft_outcome_unknown"


def test_connect_timeout_before_draft_request_is_retryable_before_effect():
    def handler(request):
        raise httpx.ConnectTimeout("connect timeout", request=request)

    provider = GmailDraftProvider(
        access_token="synthetic-access-token",
        mailbox_email="mailbox@example.com",
        requester=_requester(handler),
    )
    with pytest.raises(GmailDraftRejectedBeforeEffect) as exc_info:
        provider.create_reply_draft(
            GmailDraftRequest(source=_source(), reply_body="reply")
        )
    assert exc_info.value.reason_code == "mail.draft_provider_unavailable"


def test_malformed_success_response_is_outcome_unknown():
    provider = GmailDraftProvider(
        access_token="synthetic-access-token",
        mailbox_email="mailbox@example.com",
        requester=_requester(lambda _request: httpx.Response(200, content=b"not-json")),
    )
    with pytest.raises(GmailDraftOutcomeUnknown) as exc_info:
        provider.create_reply_draft(
            GmailDraftRequest(source=_source(), reply_body="reply")
        )
    assert exc_info.value.reason_code == "mail.draft_response_invalid"


def test_server_error_after_draft_request_is_outcome_unknown():
    provider = GmailDraftProvider(
        access_token="synthetic-access-token",
        mailbox_email="mailbox@example.com",
        requester=_requester(
            lambda _request: httpx.Response(503, json={"error": "raw detail"})
        ),
    )

    with pytest.raises(GmailDraftOutcomeUnknown) as exc_info:
        provider.create_reply_draft(
            GmailDraftRequest(source=_source(), reply_body="reply")
        )

    assert exc_info.value.reason_code == "mail.draft_outcome_unknown"
    assert "raw detail" not in str(exc_info.value)


def test_oversized_success_response_after_draft_request_is_outcome_unknown(
    monkeypatch,
):
    monkeypatch.setattr(provider_module, "MAX_RESPONSE_BYTES", 32)
    provider = GmailDraftProvider(
        access_token="synthetic-access-token",
        mailbox_email="mailbox@example.com",
        requester=_requester(
            lambda _request: httpx.Response(
                200,
                json={"id": "provider-draft-" + "x" * 128},
            )
        ),
    )

    with pytest.raises(GmailDraftOutcomeUnknown) as exc_info:
        provider.create_reply_draft(
            GmailDraftRequest(source=_source(), reply_body="reply")
        )

    assert exc_info.value.reason_code == "mail.draft_response_invalid"


@pytest.mark.parametrize(
    "provider_message_id",
    ["../drafts", "id/modify", "id\\modify", "id?format=raw", "id#fragment"],
)
def test_source_lookup_rejects_message_id_that_can_change_the_fixed_path(
    provider_message_id,
):
    calls = []
    provider = GmailDraftProvider(
        access_token="synthetic-access-token",
        mailbox_email="mailbox@example.com",
        requester=_requester(lambda request: calls.append(request)),
    )

    with pytest.raises(GmailDraftRejectedBeforeEffect) as captured:
        provider.resolve_source_message(
            source_reference=SimpleNamespace(
                provider_message_id=provider_message_id,
                message_id="<message@example.com>",
            )
        )

    assert captured.value.reason_code == "mail.gmail_source_message_invalid"
    assert calls == []
