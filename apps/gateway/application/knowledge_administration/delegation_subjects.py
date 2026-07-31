from __future__ import annotations

import base64
import hashlib
import re
import uuid
from dataclasses import dataclass
from typing import Literal


DelegationSubjectType = Literal["team", "user"]
MAX_SUBJECT_QUERY_LENGTH = 100
MAX_SUBJECT_CURSOR_LENGTH = 128
DEFAULT_SUBJECT_PAGE_SIZE = 25
MAX_SUBJECT_PAGE_SIZE = 50
_CURSOR_VERSION = 1
_WHITESPACE_RE = re.compile(r"\s+")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_PAGE_SIZE_RE = re.compile(r"^[0-9]+$")


class DelegationSubjectPageInvalid(Exception):
    pass


@dataclass(frozen=True)
class DelegationSubjectCursor:
    last_subject_id: uuid.UUID


def normalize_subject_query(value: str | None) -> str:
    normalized = _WHITESPACE_RE.sub(" ", str(value or "").strip())
    if len(normalized) > MAX_SUBJECT_QUERY_LENGTH or _CONTROL_RE.search(normalized):
        raise DelegationSubjectPageInvalid()
    return normalized


def normalize_subject_type(value: str | None) -> DelegationSubjectType:
    if value not in {"team", "user"}:
        raise DelegationSubjectPageInvalid()
    return value


def escape_like_prefix(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def encode_subject_cursor(
    *,
    subject_type: DelegationSubjectType,
    query: str,
    last_subject_id: uuid.UUID,
) -> str:
    payload = bytes(
        [_CURSOR_VERSION, 0 if subject_type == "team" else 1]
    ) + _query_digest(query) + last_subject_id.bytes
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_subject_cursor(
    value: str | None,
    *,
    subject_type: DelegationSubjectType,
    query: str,
) -> DelegationSubjectCursor | None:
    if value is None:
        return None
    if not value or len(value) > MAX_SUBJECT_CURSOR_LENGTH:
        raise DelegationSubjectPageInvalid()
    try:
        padding = "=" * (-len(value) % 4)
        payload = base64.b64decode(
            value + padding,
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, TypeError) as exc:
        raise DelegationSubjectPageInvalid() from exc
    if len(payload) != 26:
        raise DelegationSubjectPageInvalid()
    expected_kind = 0 if subject_type == "team" else 1
    if payload[0] != _CURSOR_VERSION or payload[1] != expected_kind:
        raise DelegationSubjectPageInvalid()
    if payload[2:10] != _query_digest(query):
        raise DelegationSubjectPageInvalid()
    return DelegationSubjectCursor(last_subject_id=uuid.UUID(bytes=payload[10:26]))


def validate_subject_page_size(limit: int | str) -> int:
    if isinstance(limit, bool):
        raise DelegationSubjectPageInvalid()
    if isinstance(limit, int):
        normalized = limit
    elif isinstance(limit, str) and _PAGE_SIZE_RE.fullmatch(limit):
        normalized = int(limit)
    else:
        raise DelegationSubjectPageInvalid()
    if normalized < 1 or normalized > MAX_SUBJECT_PAGE_SIZE:
        raise DelegationSubjectPageInvalid()
    return normalized


def _query_digest(query: str) -> bytes:
    return hashlib.sha256(query.casefold().encode("utf-8")).digest()[:8]
