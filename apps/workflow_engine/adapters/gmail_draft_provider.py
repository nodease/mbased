from __future__ import annotations

import base64
import json
import re
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr

from apps.shared.domain.mail_processing import MailSourceReference
from apps.shared.services.outbound_operation_http import (
    OperationHttpFailure,
    OperationHttpFailurePhase,
    OperationHttpRequester,
)
from apps.shared.services.outbound_operation_policy import (
    GMAIL_DRAFT_CREATE,
    GMAIL_MESSAGE_READ,
)
from apps.workflow_engine.adapters.gmail_api import require_gmail_message_id
from apps.workflow_engine.application.mail_processing import (
    GmailDraftCreated,
    GmailDraftOutcomeUnknown,
    GmailDraftRejectedBeforeEffect,
    GmailDraftRequest,
    GmailSourceMessage,
)

GMAIL_API_BASE_URL = "https://gmail.googleapis.com/gmail/v1/users/me"
MAX_REPLY_BODY_BYTES = 128 * 1024
MAX_MIME_BYTES = 512 * 1024
MAX_REFERENCES = 20
MAX_RESPONSE_BYTES = 512 * 1024
_CONTROL_CHARACTERS = re.compile(r"[\r\n\x00]")


class GmailDraftProvider:
    def __init__(
        self,
        *,
        access_token: str,
        mailbox_email: str,
        requester: OperationHttpRequester | None = None,
    ) -> None:
        if not access_token or _CONTROL_CHARACTERS.search(access_token):
            raise GmailDraftRejectedBeforeEffect("mail.oauth_token_invalid")
        self._mailbox_email = _single_address(
            mailbox_email, reason_code="mail.mailbox_identity_invalid"
        )
        self._headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }
        self._requester = requester or OperationHttpRequester()

    def resolve_source_message(
        self, *, source_reference: MailSourceReference
    ) -> GmailSourceMessage:
        if source_reference.message_id is None:
            raise GmailDraftRejectedBeforeEffect("mail.gmail_message_id_required")
        message_id = source_reference.provider_message_id
        if message_id is None:
            listing = self._request_before_effect(
                "GET",
                f"{GMAIL_API_BASE_URL}/messages",
                params={
                    "q": f"rfc822msgid:{source_reference.message_id}",
                    "maxResults": "2",
                },
            )
            messages = listing.get("messages")
            if not isinstance(messages, list) or len(messages) != 1:
                raise GmailDraftRejectedBeforeEffect(
                    "mail.gmail_source_message_not_unique"
                )
            message_id = (
                messages[0].get("id") if isinstance(messages[0], dict) else None
            )
        try:
            message_id = require_gmail_message_id(message_id)
        except (TypeError, ValueError):
            raise GmailDraftRejectedBeforeEffect(
                "mail.gmail_source_message_invalid"
            ) from None
        detail = self._request_before_effect(
            "GET",
            f"{GMAIL_API_BASE_URL}/messages/{message_id}",
            params={
                "format": "metadata",
                "metadataHeaders": [
                    "From",
                    "Reply-To",
                    "Subject",
                    "Message-ID",
                    "References",
                ],
            },
        )
        return _source_from_gmail_detail(
            detail,
            expected_message_id=message_id,
            expected_rfc_message_id=source_reference.message_id,
        )

    def create_reply_draft(self, request: GmailDraftRequest) -> GmailDraftCreated:
        raw = build_reply_mime(
            mailbox_email=self._mailbox_email,
            source=request.source,
            reply_body=request.reply_body,
        )
        try:
            response = self._requester.request(
                operation_id=GMAIL_DRAFT_CREATE,
                approved_endpoint=GMAIL_API_BASE_URL,
                method="POST",
                url=f"{GMAIL_API_BASE_URL}/drafts",
                headers={**self._headers, "Content-Type": "application/json"},
                json_body={
                    "message": {
                        "raw": raw,
                        "threadId": request.source.thread_id,
                    }
                },
            )
        except OperationHttpFailure as exc:
            if exc.phase is OperationHttpFailurePhase.BEFORE_SEND:
                raise GmailDraftRejectedBeforeEffect(
                    "mail.draft_provider_unavailable"
                ) from None
            raise GmailDraftOutcomeUnknown("mail.draft_outcome_unknown") from None
        if response.status_code >= 500:
            raise GmailDraftOutcomeUnknown("mail.draft_outcome_unknown")
        if response.status_code >= 400:
            reason = (
                "mail.oauth_reauthorization_required"
                if response.status_code in {401, 403}
                else "mail.draft_provider_rejected"
            )
            raise GmailDraftRejectedBeforeEffect(reason)
        content = response.content
        if len(content) > MAX_RESPONSE_BYTES:
            raise GmailDraftOutcomeUnknown("mail.draft_response_invalid")
        try:
            payload = json.loads(content)
            draft_id = payload.get("id")
        except (TypeError, ValueError, AttributeError):
            raise GmailDraftOutcomeUnknown("mail.draft_response_invalid") from None
        if not isinstance(draft_id, str) or not draft_id:
            raise GmailDraftOutcomeUnknown("mail.draft_response_invalid")
        return GmailDraftCreated(provider_draft_id=draft_id)

    def _request_before_effect(self, method: str, url: str, **kwargs) -> dict:
        params = kwargs.pop("params", None)
        if kwargs:
            raise GmailDraftRejectedBeforeEffect("mail.gmail_source_lookup_failed")
        try:
            response = self._requester.request(
                operation_id=GMAIL_MESSAGE_READ,
                approved_endpoint=GMAIL_API_BASE_URL,
                method=method,
                url=url,
                headers=self._headers,
                query_params=params,
            )
        except OperationHttpFailure:
            raise GmailDraftRejectedBeforeEffect(
                "mail.gmail_source_lookup_failed"
            ) from None
        if response.status_code >= 400:
            reason = (
                "mail.oauth_reauthorization_required"
                if response.status_code in {401, 403}
                else "mail.gmail_source_lookup_failed"
            )
            raise GmailDraftRejectedBeforeEffect(reason)
        content = response.content
        if len(content) > MAX_RESPONSE_BYTES:
            raise GmailDraftRejectedBeforeEffect("mail.gmail_source_lookup_failed")
        try:
            payload = json.loads(content)
        except (TypeError, ValueError):
            raise GmailDraftRejectedBeforeEffect(
                "mail.gmail_source_lookup_failed"
            ) from None
        if not isinstance(payload, dict):
            raise GmailDraftRejectedBeforeEffect("mail.gmail_source_lookup_failed")
        return payload


def build_reply_mime(
    *, mailbox_email: str, source: GmailSourceMessage, reply_body: str
) -> str:
    mailbox = _single_address(
        mailbox_email, reason_code="mail.mailbox_identity_invalid"
    )
    recipient = _single_address(
        source.reply_to, reason_code="mail.reply_recipient_invalid"
    )
    if not isinstance(reply_body, str) or not reply_body.strip():
        raise GmailDraftRejectedBeforeEffect("mail.reply_body_invalid")
    if len(reply_body.encode("utf-8")) > MAX_REPLY_BODY_BYTES:
        raise GmailDraftRejectedBeforeEffect("mail.reply_body_too_large")
    message_id = _safe_header(
        source.rfc_message_id, reason_code="mail.gmail_source_message_invalid"
    )
    subject = _reply_subject(source.subject)
    references = _bounded_references(source.references, message_id)

    message = EmailMessage()
    message["From"] = mailbox
    message["To"] = recipient
    message["Subject"] = subject
    message["In-Reply-To"] = message_id
    message["References"] = " ".join(references)
    message.set_content(reply_body, subtype="plain", charset="utf-8")
    mime_bytes = message.as_bytes()
    if len(mime_bytes) > MAX_MIME_BYTES:
        raise GmailDraftRejectedBeforeEffect("mail.reply_mime_too_large")
    return base64.urlsafe_b64encode(mime_bytes).decode("ascii")


def _source_from_gmail_detail(
    detail: dict,
    *,
    expected_message_id: str,
    expected_rfc_message_id: str,
) -> GmailSourceMessage:
    message_id = detail.get("id")
    thread_id = detail.get("threadId")
    payload = detail.get("payload")
    headers = payload.get("headers") if isinstance(payload, dict) else None
    if (
        message_id != expected_message_id
        or not isinstance(thread_id, str)
        or not thread_id
        or not isinstance(headers, list)
    ):
        raise GmailDraftRejectedBeforeEffect("mail.gmail_source_message_invalid")
    normalized: dict[str, str] = {}
    for header in headers:
        if not isinstance(header, dict):
            continue
        name = str(header.get("name", "")).strip().lower()
        value = header.get("value")
        if name and isinstance(value, str) and name not in normalized:
            normalized[name] = value
    rfc_message_id = _safe_header(
        normalized.get("message-id", ""),
        reason_code="mail.gmail_source_message_invalid",
    )
    if rfc_message_id != expected_rfc_message_id:
        raise GmailDraftRejectedBeforeEffect("mail.gmail_source_message_invalid")
    reply_to = normalized.get("reply-to") or normalized.get("from") or ""
    references = tuple((normalized.get("references") or "").split())
    return GmailSourceMessage(
        message_id=message_id,
        thread_id=thread_id,
        rfc_message_id=rfc_message_id,
        references=references,
        reply_to=reply_to,
        subject=normalized.get("subject", ""),
    )


def _single_address(value: str, *, reason_code: str) -> str:
    if not isinstance(value, str) or _CONTROL_CHARACTERS.search(value):
        raise GmailDraftRejectedBeforeEffect(reason_code)
    addresses = [address for _name, address in getaddresses([value]) if address]
    if len(addresses) != 1:
        raise GmailDraftRejectedBeforeEffect(reason_code)
    _display, address = parseaddr(value)
    if address != addresses[0] or "@" not in address:
        raise GmailDraftRejectedBeforeEffect(reason_code)
    return address.lower()


def _safe_header(value: str, *, reason_code: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 998
        or _CONTROL_CHARACTERS.search(value)
    ):
        raise GmailDraftRejectedBeforeEffect(reason_code)
    return value.strip()


def _reply_subject(value: str) -> str:
    subject = _safe_header(value or "(no subject)", reason_code="mail.subject_invalid")
    if re.match(r"^\s*re\s*:", subject, flags=re.IGNORECASE):
        return subject
    return f"Re: {subject}"


def _bounded_references(
    references: tuple[str, ...], message_id: str
) -> tuple[str, ...]:
    safe = []
    for reference in references:
        try:
            normalized = _safe_header(
                reference, reason_code="mail.gmail_source_message_invalid"
            )
        except GmailDraftRejectedBeforeEffect:
            continue
        if normalized.startswith("<") and normalized.endswith(">"):
            safe.append(normalized)
    safe.append(message_id)
    return tuple(dict.fromkeys(safe[-MAX_REFERENCES:]))
