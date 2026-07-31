from __future__ import annotations

import base64
import binascii
import json
import re
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable

from apps.shared.domain.mail_processing import (
    MailProcessingBoundaryError,
    MailSourceReference,
)
from apps.shared.services.outbound_operation_http import (
    OperationHttpFailure,
    OperationHttpRequester,
    OperationHttpSession,
)
from apps.shared.services.outbound_operation_policy import (
    GMAIL_MESSAGE_MODIFY,
    GMAIL_MESSAGE_READ,
)
from apps.workflow_engine.adapters.gmail_api import require_gmail_message_id

GMAIL_API_BASE_URL = "https://gmail.googleapis.com/gmail/v1/users/me"
MAX_QUERY_LENGTH = 2048
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_BODY_BYTES = 128 * 1024
MAX_BODY_CHARACTERS = 1000
MAX_ATTACHMENTS = 100
MAX_MIME_PARTS = 500
MAX_MIME_DEPTH = 20
_CONTROL_CHARACTERS = re.compile(r"[\r\n\x00]")
_FOLDER_LABELS = {
    "INBOX": "INBOX",
    "SENT": "SENT",
    "DRAFTS": "DRAFT",
    "SPAM": "SPAM",
    "TRASH": "TRASH",
}


class GmailMailboxError(RuntimeError):
    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class GmailSearchCriteria:
    keyword: str = ""
    sender: str = ""
    subject: str = ""
    start_date: str | None = None
    end_date: str | None = None
    unread_only: bool = False
    folder: str = "INBOX"
    max_results: int = 5


@dataclass(frozen=True)
class GmailMailboxMessage:
    output: dict[str, Any]
    source_reference: MailSourceReference


class GmailMailboxProvider:
    def __init__(
        self,
        *,
        access_token: str,
        requester: OperationHttpRequester | None = None,
    ) -> None:
        if not access_token or _CONTROL_CHARACTERS.search(access_token):
            raise GmailMailboxError("mail.oauth_token_invalid")
        self._headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }
        self._requester = requester or OperationHttpRequester()

    def search(self, criteria: GmailSearchCriteria) -> list[GmailMailboxMessage]:
        query = build_gmail_search_query(criteria)
        label = _folder_label(criteria.folder)
        try:
            with self._requester.open_session(
                operation_id=GMAIL_MESSAGE_READ,
                approved_endpoint=GMAIL_API_BASE_URL,
            ) as session:
                listing = self._request(
                    "GET",
                    f"{GMAIL_API_BASE_URL}/messages",
                    params={
                        "q": query,
                        "labelIds": label,
                        "maxResults": str(criteria.max_results),
                    },
                    session=session,
                )
                entries = listing.get("messages", [])
                if entries is None:
                    entries = []
                if not isinstance(entries, list):
                    raise GmailMailboxError("mail.gmail_response_invalid")
                messages: list[GmailMailboxMessage] = []
                for entry in entries[: criteria.max_results]:
                    provider_id = entry.get("id") if isinstance(entry, dict) else None
                    try:
                        provider_id = require_gmail_message_id(provider_id)
                    except (TypeError, ValueError):
                        raise GmailMailboxError("mail.gmail_response_invalid")
                    detail = self._request(
                        "GET",
                        f"{GMAIL_API_BASE_URL}/messages/{provider_id}",
                        params={"format": "full"},
                        session=session,
                    )
                    output, rfc_message_id = _message_output(detail, provider_id)
                    source = MailSourceReference.from_mapping(
                        {
                            "provider_message_id": provider_id,
                            "message_id": rfc_message_id,
                            "folder": criteria.folder,
                        }
                    )
                    messages.append(
                        GmailMailboxMessage(output=output, source_reference=source)
                    )
                return messages
        except OperationHttpFailure:
            raise GmailMailboxError("mail.gmail_provider_unavailable") from None
        except MailProcessingBoundaryError as exc:
            raise GmailMailboxError("mail.gmail_response_invalid") from exc

    def mark_read(self, source_references: Iterable[MailSourceReference]) -> None:
        provider_ids = []
        for source in source_references:
            if source.provider_message_id is None:
                raise GmailMailboxError("mail.message_identity_invalid")
            try:
                provider_ids.append(
                    require_gmail_message_id(source.provider_message_id)
                )
            except ValueError:
                raise GmailMailboxError("mail.message_identity_invalid") from None
        if not provider_ids:
            return
        self._request(
            "POST",
            f"{GMAIL_API_BASE_URL}/messages/batchModify",
            json={
                "ids": provider_ids,
                "removeLabelIds": ["UNREAD"],
            },
            allow_empty=True,
        )

    def acknowledge(self, *, source_reference: MailSourceReference) -> None:
        if source_reference.provider_message_id is None:
            raise GmailMailboxError("mail.message_identity_invalid")
        try:
            provider_id = require_gmail_message_id(source_reference.provider_message_id)
        except ValueError:
            raise GmailMailboxError("mail.message_identity_invalid") from None
        self._request(
            "POST",
            f"{GMAIL_API_BASE_URL}/messages/{provider_id}/modify",
            json={"removeLabelIds": ["UNREAD"]},
        )

    def _request(
        self,
        method: str,
        url: str,
        *,
        allow_empty: bool = False,
        session: OperationHttpSession | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        params = kwargs.pop("params", None)
        json_body = kwargs.pop("json", None)
        if kwargs:
            raise GmailMailboxError("mail.gmail_request_invalid")
        operation_id = GMAIL_MESSAGE_MODIFY if method == "POST" else GMAIL_MESSAGE_READ
        try:
            request_kwargs: dict[str, Any] = {
                "method": method,
                "url": url,
                "headers": {
                    **self._headers,
                    **(
                        {"Content-Type": "application/json"} if method == "POST" else {}
                    ),
                },
                "query_params": params,
                "json_body": json_body,
            }
            if session is not None:
                if operation_id != GMAIL_MESSAGE_READ:
                    raise GmailMailboxError("mail.gmail_request_invalid")
                response = session.request(**request_kwargs)
            else:
                response = self._requester.request(
                    operation_id=operation_id,
                    approved_endpoint=GMAIL_API_BASE_URL,
                    **request_kwargs,
                )
        except OperationHttpFailure:
            raise GmailMailboxError("mail.gmail_provider_unavailable") from None
        if response.status_code in {401, 403}:
            raise GmailMailboxError("mail.oauth_reauthorization_required")
        if response.status_code == 429 or response.status_code >= 500:
            raise GmailMailboxError("mail.gmail_provider_unavailable")
        if response.status_code >= 400:
            raise GmailMailboxError("mail.gmail_request_rejected")
        content = response.content
        if len(content) > MAX_RESPONSE_BYTES:
            raise GmailMailboxError("mail.gmail_response_too_large")
        if allow_empty and not content:
            return {}
        try:
            payload = json.loads(content)
        except (TypeError, ValueError):
            raise GmailMailboxError("mail.gmail_response_invalid") from None
        if not isinstance(payload, dict):
            raise GmailMailboxError("mail.gmail_response_invalid")
        return payload


def build_gmail_search_query(criteria: GmailSearchCriteria) -> str:
    if not 1 <= criteria.max_results <= 100:
        raise GmailMailboxError("mail.search_criteria_invalid")
    parts: list[str] = []
    if criteria.unread_only:
        parts.append("is:unread")
    if criteria.keyword:
        parts.append(_quoted_term(criteria.keyword))
    if criteria.sender:
        parts.append(f"from:{_quoted_term(criteria.sender)}")
    if criteria.subject:
        parts.append(f"subject:{_quoted_term(criteria.subject)}")
    start = _parse_date(criteria.start_date) if criteria.start_date else None
    if start is None:
        start = datetime.now().date() - timedelta(days=7)
    parts.append(f"after:{start.strftime('%Y/%m/%d')}")
    if criteria.end_date:
        end = _parse_date(criteria.end_date) + timedelta(days=1)
        parts.append(f"before:{end.strftime('%Y/%m/%d')}")
    query = " ".join(parts)
    if len(query) > MAX_QUERY_LENGTH:
        raise GmailMailboxError("mail.search_criteria_invalid")
    return query


def _quoted_term(value: str) -> str:
    if not isinstance(value, str) or _CONTROL_CHARACTERS.search(value):
        raise GmailMailboxError("mail.search_criteria_invalid")
    normalized = value.strip()
    if not normalized or len(normalized) > 512:
        raise GmailMailboxError("mail.search_criteria_invalid")
    return '"' + normalized.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _parse_date(value: str):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError) as exc:
        raise GmailMailboxError("mail.search_criteria_invalid") from exc


def _folder_label(folder: str) -> str:
    try:
        return _FOLDER_LABELS[folder]
    except KeyError as exc:
        raise GmailMailboxError("mail.folder_select_failed") from exc


def _message_output(
    detail: dict[str, Any], expected_id: str
) -> tuple[dict[str, Any], str | None]:
    if detail.get("id") != expected_id:
        raise GmailMailboxError("mail.gmail_response_invalid")
    payload = detail.get("payload")
    if not isinstance(payload, dict):
        raise GmailMailboxError("mail.gmail_response_invalid")
    headers = _headers(payload.get("headers"))
    body_text, body_html, attachments = _extract_parts(payload)
    return (
        {
            "subject": headers.get("subject", ""),
            "from": headers.get("from", ""),
            "to": headers.get("to", ""),
            "date": headers.get("date", ""),
            "body_text": body_text[:MAX_BODY_CHARACTERS],
            "body_html": body_html[:MAX_BODY_CHARACTERS],
            "snippet": (body_text or str(detail.get("snippet") or ""))[:200],
            "has_attachments": bool(attachments),
            "attachments": attachments,
        },
        headers.get("message-id") or None,
    )


def _headers(value: Any) -> dict[str, str]:
    if not isinstance(value, list):
        return {}
    result: dict[str, str] = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip().lower()
        header_value = item.get("value")
        if (
            name
            and isinstance(header_value, str)
            and not _CONTROL_CHARACTERS.search(header_value)
            and name not in result
        ):
            result[name] = header_value[:998]
    return result


def _extract_parts(payload: dict[str, Any]) -> tuple[str, str, list[dict[str, Any]]]:
    text = ""
    html = ""
    attachments: list[dict[str, Any]] = []
    stack = deque([(payload, 0)])
    part_count = 0
    decoded_body_bytes = 0
    while stack:
        part, depth = stack.popleft()
        part_count += 1
        if part_count > MAX_MIME_PARTS or depth > MAX_MIME_DEPTH:
            raise GmailMailboxError("mail.gmail_response_invalid")
        children = part.get("parts")
        if isinstance(children, list):
            stack.extend(
                (child, depth + 1) for child in children if isinstance(child, dict)
            )
        mime_type = str(part.get("mimeType") or "").lower()
        filename = str(part.get("filename") or "")
        body = part.get("body") if isinstance(part.get("body"), dict) else {}
        if filename and len(attachments) < MAX_ATTACHMENTS:
            size = body.get("size")
            attachments.append(
                {
                    "filename": _safe_display_text(filename, max_length=255),
                    "content_type": _safe_display_text(mime_type, max_length=255),
                    "size": size if isinstance(size, int) and size >= 0 else 0,
                }
            )
            continue
        data = body.get("data")
        if not isinstance(data, str):
            continue
        decoded = _decode_body(data)
        decoded_body_bytes += len(decoded.encode("utf-8"))
        if decoded_body_bytes > MAX_BODY_BYTES:
            raise GmailMailboxError("mail.gmail_response_invalid")
        if mime_type == "text/plain" and not text:
            text = decoded
        elif mime_type == "text/html" and not html:
            html = decoded
    return text, html, attachments


def _decode_body(value: str) -> str:
    if len(value) > MAX_BODY_BYTES * 2:
        raise GmailMailboxError("mail.gmail_response_invalid")
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
    except (UnicodeEncodeError, binascii.Error, ValueError):
        raise GmailMailboxError("mail.gmail_response_invalid") from None
    if len(decoded) > MAX_BODY_BYTES:
        raise GmailMailboxError("mail.gmail_response_invalid")
    return decoded.decode("utf-8", errors="replace")


def _safe_display_text(value: str, *, max_length: int) -> str:
    normalized = _CONTROL_CHARACTERS.sub(" ", str(value)).strip()
    return normalized[:max_length]
