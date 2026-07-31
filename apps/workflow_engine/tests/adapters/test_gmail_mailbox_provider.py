import base64
import inspect
import json
from types import SimpleNamespace

import httpx
import pytest

from apps.shared.domain.mail_processing import MailSourceReference
from apps.shared.services.outbound_operation_http import OperationHttpRequester
from apps.workflow_engine.adapters import gmail_mailbox_provider as provider_module
from apps.workflow_engine.adapters.gmail_mailbox_provider import (
    GmailMailboxError,
    GmailMailboxProvider,
    GmailSearchCriteria,
    build_gmail_search_query,
)


def _encoded(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def _requester(handler, *, observed: dict | None = None) -> OperationHttpRequester:
    def client_factory(**kwargs):
        if observed is not None:
            observed["client_count"] = observed.get("client_count", 0) + 1
        kwargs.pop("transport")
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    return OperationHttpRequester(client_factory=client_factory)


def _message_detail(message_id: str = "gmail-message") -> dict:
    return {
        "id": message_id,
        "snippet": "preview",
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [
                {"name": "From", "value": "sender@example.com"},
                {"name": "To", "value": "mailbox@example.com"},
                {"name": "Subject", "value": "Question"},
                {"name": "Date", "value": "Mon, 05 Jan 2026 10:00:00 +0000"},
                {"name": "Message-ID", "value": "<message@example.com>"},
            ],
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": _encoded("hello from gmail"), "size": 16},
                },
                {
                    "mimeType": "application/pdf",
                    "filename": "policy.pdf",
                    "body": {"attachmentId": "attachment", "size": 128},
                },
            ],
        },
    }


def test_search_returns_safe_output_and_protected_source_identity():
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "gmail-message"}]})
        return httpx.Response(200, json=_message_detail())

    provider = GmailMailboxProvider(
        access_token="synthetic-access-token",
        requester=_requester(handler),
    )
    messages = provider.search(
        GmailSearchCriteria(
            keyword="onboarding",
            unread_only=True,
            folder="INBOX",
            max_results=1,
            start_date="2026-01-01",
        )
    )

    assert len(messages) == 1
    message = messages[0]
    assert message.output["subject"] == "Question"
    assert message.output["body_text"] == "hello from gmail"
    assert message.output["attachments"] == [
        {"filename": "policy.pdf", "content_type": "application/pdf", "size": 128}
    ]
    assert "gmail-message" not in repr(message.output)
    assert message.source_reference.provider_message_id == "gmail-message"
    assert message.source_reference.message_id == "<message@example.com>"
    query = calls[0].url.params["q"]
    assert "is:unread" in query
    assert '"onboarding"' in query
    assert calls[0].url.params["labelIds"] == "INBOX"


def test_search_reuses_one_guarded_client_for_listing_and_message_details():
    observed = {}

    def handler(request):
        if request.url.path.endswith("/messages"):
            return httpx.Response(
                200,
                json={"messages": [{"id": "gmail-one"}, {"id": "gmail-two"}]},
            )
        message_id = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json=_message_detail(message_id))

    provider = GmailMailboxProvider(
        access_token="synthetic-access-token",
        requester=_requester(handler, observed=observed),
    )

    messages = provider.search(GmailSearchCriteria(max_results=2))

    assert len(messages) == 2
    assert observed["client_count"] == 1


def test_search_maps_session_client_initialization_failure_to_safe_provider_error():
    def client_factory(**_kwargs):
        raise httpx.ConnectError(
            "raw connection detail",
            request=httpx.Request("GET", "https://gmail.googleapis.com"),
        )

    provider = GmailMailboxProvider(
        access_token="synthetic-access-token",
        requester=OperationHttpRequester(client_factory=client_factory),
    )

    with pytest.raises(GmailMailboxError) as captured:
        provider.search(GmailSearchCriteria(max_results=1))

    assert captured.value.reason_code == "mail.gmail_provider_unavailable"
    assert "raw connection detail" not in str(captured.value)
    assert captured.value.__cause__ is None


def test_mark_read_happens_in_one_batch_after_search_results_are_available():
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path.endswith("/messages"):
            return httpx.Response(
                200,
                json={"messages": [{"id": "gmail-one"}, {"id": "gmail-two"}]},
            )
        if request.url.path.endswith("/batchModify"):
            return httpx.Response(204)
        message_id = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json=_message_detail(message_id))

    provider = GmailMailboxProvider(
        access_token="synthetic-access-token",
        requester=_requester(handler),
    )
    messages = provider.search(GmailSearchCriteria(max_results=2))
    provider.mark_read(message.source_reference for message in messages)

    assert [request.url.path for request in calls][-1].endswith("/batchModify")
    assert calls[-1].read().decode() == (
        '{"ids":["gmail-one","gmail-two"],"removeLabelIds":["UNREAD"]}'
    )


def test_fetch_failure_prevents_caller_from_reaching_mark_read():
    def handler(request):
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "gmail-message"}]})
        return httpx.Response(503, json={"error": "provider detail"})

    provider = GmailMailboxProvider(
        access_token="synthetic-access-token",
        requester=_requester(handler),
    )
    with pytest.raises(GmailMailboxError, match="^mail.gmail_provider_unavailable$"):
        provider.search(GmailSearchCriteria(max_results=1))


@pytest.mark.parametrize(
    "status,reason_code",
    [
        (401, "mail.oauth_reauthorization_required"),
        (403, "mail.oauth_reauthorization_required"),
        (429, "mail.gmail_provider_unavailable"),
        (503, "mail.gmail_provider_unavailable"),
        (400, "mail.gmail_request_rejected"),
    ],
)
def test_provider_http_failures_use_safe_reason_codes(status, reason_code):
    provider = GmailMailboxProvider(
        access_token="synthetic-access-token",
        requester=_requester(
            lambda _request: httpx.Response(status, json={"error": "raw detail"})
        ),
    )
    with pytest.raises(GmailMailboxError) as exc_info:
        provider.search(GmailSearchCriteria(max_results=1))
    assert exc_info.value.reason_code == reason_code
    assert "raw detail" not in str(exc_info.value)


def test_provider_timeout_is_safe_unavailable_error():
    def handler(request):
        raise httpx.ReadTimeout("raw timeout", request=request)

    provider = GmailMailboxProvider(
        access_token="synthetic-access-token",
        requester=_requester(handler),
    )
    with pytest.raises(GmailMailboxError) as exc_info:
        provider.search(GmailSearchCriteria(max_results=1))
    assert exc_info.value.reason_code == "mail.gmail_provider_unavailable"
    assert "raw timeout" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None


def test_provider_rejects_response_larger_than_configured_limit(monkeypatch):
    monkeypatch.setattr(provider_module, "MAX_RESPONSE_BYTES", 32)
    oversized = json.dumps({"messages": [{"id": "x" * 64}]}).encode()
    provider = GmailMailboxProvider(
        access_token="synthetic-access-token",
        requester=_requester(lambda _request: httpx.Response(200, content=oversized)),
    )

    with pytest.raises(GmailMailboxError) as exc_info:
        provider.search(GmailSearchCriteria(max_results=1))

    assert exc_info.value.reason_code == "mail.gmail_response_too_large"


def test_provider_rejects_excessively_deep_mime_tree(monkeypatch):
    monkeypatch.setattr(provider_module, "MAX_MIME_DEPTH", 2)
    nested = {"mimeType": "text/plain", "body": {"data": _encoded("body")}}
    for _ in range(4):
        nested = {"mimeType": "multipart/mixed", "parts": [nested]}

    def handler(request):
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "gmail-message"}]})
        return httpx.Response(
            200,
            json={"id": "gmail-message", "payload": nested},
        )

    provider = GmailMailboxProvider(
        access_token="synthetic-access-token",
        requester=_requester(handler),
    )

    with pytest.raises(GmailMailboxError) as exc_info:
        provider.search(GmailSearchCriteria(max_results=1))

    assert exc_info.value.reason_code == "mail.gmail_response_invalid"


@pytest.mark.parametrize("value", ["bad\r\nquery", "bad\x00query", "x" * 513])
def test_search_query_rejects_controls_and_unbounded_terms(value):
    with pytest.raises(GmailMailboxError, match="^mail.search_criteria_invalid$"):
        build_gmail_search_query(GmailSearchCriteria(keyword=value))


def test_acknowledge_uses_fixed_message_modify_endpoint():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"id": "gmail-message"})

    provider = GmailMailboxProvider(
        access_token="synthetic-access-token",
        requester=_requester(handler),
    )
    provider.acknowledge(
        source_reference=MailSourceReference(provider_message_id="gmail-message")
    )

    assert requests[0].url.path.endswith("/messages/gmail-message/modify")
    assert requests[0].method == "POST"
    assert "drafts.send" not in requests[0].url.path
    assert "messages.send" not in requests[0].url.path


@pytest.mark.parametrize(
    "provider_message_id",
    ["../drafts", "id/modify", "id\\modify", "id?format=raw", "id#fragment"],
)
def test_acknowledge_rejects_message_id_that_can_change_the_fixed_path(
    provider_message_id,
):
    calls = []
    provider = GmailMailboxProvider(
        access_token="synthetic-access-token",
        requester=_requester(lambda request: calls.append(request)),
    )

    with pytest.raises(GmailMailboxError) as captured:
        provider.acknowledge(
            source_reference=SimpleNamespace(provider_message_id=provider_message_id)
        )

    assert captured.value.reason_code == "mail.message_identity_invalid"
    assert calls == []


def test_mailbox_adapter_has_no_send_endpoint_or_callable_surface():
    production_source = inspect.getsource(provider_module)
    assert "drafts.send" not in production_source
    assert "messages.send" not in production_source
    assert not hasattr(GmailMailboxProvider, "send")
