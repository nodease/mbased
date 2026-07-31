from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping


class MailProcessingStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    ACK_PENDING = "ack_pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    OUTCOME_UNKNOWN = "outcome_unknown"


class MailDraftEffectStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    SUCCEEDED = "succeeded"
    FAILED_BEFORE_EFFECT = "failed_before_effect"
    OUTCOME_UNKNOWN = "outcome_unknown"
    EXHAUSTED = "exhausted"


PROCESSING_TERMINAL_STATUSES = frozenset(
    {
        MailProcessingStatus.SUCCEEDED,
        MailProcessingStatus.FAILED,
        MailProcessingStatus.OUTCOME_UNKNOWN,
    }
)
DRAFT_EFFECT_TERMINAL_STATUSES = frozenset(
    {
        MailDraftEffectStatus.SUCCEEDED,
        MailDraftEffectStatus.OUTCOME_UNKNOWN,
        MailDraftEffectStatus.EXHAUSTED,
    }
)


class MailProcessingBoundaryError(ValueError):
    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


_RFC_MESSAGE_ID_RE = re.compile(r'^<[^\s<>@"\\]+@[^\s<>@"\\]+>$')
_PROVIDER_MESSAGE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,255}$")


@dataclass(frozen=True)
class MailSourceReference:
    uid_validity: int | None = None
    uid: int | None = None
    message_id: str | None = None
    folder: str = "INBOX"
    provider_message_id: str | None = None

    def __post_init__(self) -> None:
        has_imap_identity = self.uid_validity is not None or self.uid is not None
        if has_imap_identity and (
            self.uid_validity is None
            or self.uid is None
            or self.uid_validity < 1
            or self.uid < 1
        ):
            raise MailProcessingBoundaryError("mail.message_identity_invalid")
        provider_message_id = normalize_provider_message_id(self.provider_message_id)
        if has_imap_identity == (provider_message_id is not None):
            raise MailProcessingBoundaryError("mail.message_identity_invalid")
        folder = str(self.folder or "").strip()
        if (
            not folder
            or len(folder) > 255
            or any(char in folder for char in ("\r", "\n", "\x00"))
        ):
            raise MailProcessingBoundaryError("mail.message_identity_invalid")
        object.__setattr__(
            self, "message_id", normalize_rfc_message_id(self.message_id)
        )
        object.__setattr__(self, "folder", folder)
        object.__setattr__(self, "provider_message_id", provider_message_id)

    def to_json(self) -> str:
        return json.dumps(
            {
                "uid_validity": self.uid_validity,
                "uid": self.uid,
                "message_id": self.message_id,
                "folder": self.folder,
                "provider_message_id": self.provider_message_id,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "MailSourceReference":
        raw_uid_validity = value.get("uid_validity")
        raw_uid = value.get("uid")
        try:
            uid_validity = (
                int(raw_uid_validity) if raw_uid_validity is not None else None
            )
            uid = int(raw_uid) if raw_uid is not None else None
        except (TypeError, ValueError) as exc:
            raise MailProcessingBoundaryError("mail.message_identity_invalid") from exc
        has_imap_identity = uid_validity is not None or uid is not None
        if has_imap_identity and (
            uid_validity is None or uid is None or uid_validity < 1 or uid < 1
        ):
            raise MailProcessingBoundaryError("mail.message_identity_invalid")
        provider_message_id = normalize_provider_message_id(
            value.get("provider_message_id")
        )
        if has_imap_identity == (provider_message_id is not None):
            raise MailProcessingBoundaryError("mail.message_identity_invalid")
        message_id = normalize_rfc_message_id(value.get("message_id"))
        folder = str(value.get("folder") or "").strip()
        if (
            not folder
            or len(folder) > 255
            or any(char in folder for char in ("\r", "\n", "\x00"))
        ):
            raise MailProcessingBoundaryError("mail.message_identity_invalid")
        return cls(
            uid_validity=uid_validity,
            uid=uid,
            message_id=message_id,
            folder=folder,
            provider_message_id=provider_message_id,
        )


def normalize_rfc_message_id(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    if len(normalized) > 998 or any(
        char in normalized for char in ("\r", "\n", "\x00")
    ):
        raise MailProcessingBoundaryError("mail.message_identity_invalid")
    if not _RFC_MESSAGE_ID_RE.fullmatch(normalized):
        raise MailProcessingBoundaryError("mail.message_identity_invalid")
    return normalized


def normalize_provider_message_id(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    if not _PROVIDER_MESSAGE_ID_RE.fullmatch(normalized):
        raise MailProcessingBoundaryError("mail.message_identity_invalid")
    return normalized


def build_message_identity_hash(
    *,
    organization_id: uuid.UUID,
    workflow_id: uuid.UUID,
    source_node_id: str,
    credential_id: uuid.UUID,
    provider: str,
    source: MailSourceReference,
) -> str:
    normalized_node_id = source_node_id.strip()
    normalized_provider = provider.strip().lower()
    if not normalized_node_id or len(normalized_node_id) > 255:
        raise MailProcessingBoundaryError("mail.message_identity_invalid")
    if not normalized_provider or len(normalized_provider) > 32:
        raise MailProcessingBoundaryError("mail.message_identity_invalid")
    if source.provider_message_id is not None:
        source_identity = ("provider", source.provider_message_id)
    else:
        source_identity = (
            "imap",
            source.folder,
            str(source.uid_validity or ""),
            str(source.uid or ""),
            source.message_id or "",
        )
    canonical = "\x1f".join(
        (
            str(organization_id),
            str(workflow_id),
            normalized_node_id,
            str(credential_id),
            normalized_provider,
            *source_identity,
        )
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_draft_operation_key_hash(*, processing_id: uuid.UUID, node_id: str) -> str:
    normalized_node_id = node_id.strip()
    if not normalized_node_id or len(normalized_node_id) > 255:
        raise MailProcessingBoundaryError("mail.draft_operation_invalid")
    return hashlib.sha256(
        f"gmail-draft\x1f{processing_id}\x1f{normalized_node_id}".encode("utf-8")
    ).hexdigest()


def build_required_effect_contract_hash(
    *,
    processing_selector: list[str],
    effect_selectors: list[list[str]],
    deployment_id: uuid.UUID | None,
) -> str:
    selectors = [processing_selector, *effect_selectors]
    if any(
        not isinstance(selector, list)
        or len(selector) < 2
        or any(not isinstance(item, str) or not item.strip() for item in selector)
        for selector in selectors
    ):
        raise MailProcessingBoundaryError("mail.effect_contract_invalid")
    canonical = json.dumps(
        {
            "deployment_id": str(deployment_id) if deployment_id is not None else None,
            "processing_selector": processing_selector,
            "effect_selectors": sorted(effect_selectors),
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def digest_reply_body(body: str) -> str:
    if not isinstance(body, str) or not body.strip():
        raise MailProcessingBoundaryError("mail.reply_body_invalid")
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def parse_opaque_reference(value: Any, *, reason_code: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise MailProcessingBoundaryError(reason_code) from exc


_PROCESSING_TRANSITIONS = {
    MailProcessingStatus.PENDING: frozenset(
        {MailProcessingStatus.PROCESSING, MailProcessingStatus.FAILED}
    ),
    MailProcessingStatus.PROCESSING: frozenset(
        {
            MailProcessingStatus.ACK_PENDING,
            MailProcessingStatus.FAILED,
            MailProcessingStatus.OUTCOME_UNKNOWN,
        }
    ),
    MailProcessingStatus.ACK_PENDING: frozenset(
        {MailProcessingStatus.SUCCEEDED, MailProcessingStatus.FAILED}
    ),
}

_DRAFT_TRANSITIONS = {
    MailDraftEffectStatus.PENDING: frozenset({MailDraftEffectStatus.CLAIMED}),
    MailDraftEffectStatus.CLAIMED: frozenset(
        {
            MailDraftEffectStatus.SUCCEEDED,
            MailDraftEffectStatus.FAILED_BEFORE_EFFECT,
            MailDraftEffectStatus.OUTCOME_UNKNOWN,
        }
    ),
    MailDraftEffectStatus.FAILED_BEFORE_EFFECT: frozenset(
        {MailDraftEffectStatus.CLAIMED, MailDraftEffectStatus.EXHAUSTED}
    ),
}


def require_processing_transition(current: str, target: str) -> None:
    try:
        current_status = MailProcessingStatus(current)
        target_status = MailProcessingStatus(target)
    except ValueError as exc:
        raise MailProcessingBoundaryError("mail.processing_state_invalid") from exc
    if target_status not in _PROCESSING_TRANSITIONS.get(current_status, frozenset()):
        raise MailProcessingBoundaryError("mail.processing_state_invalid")


def require_draft_effect_transition(current: str, target: str) -> None:
    try:
        current_status = MailDraftEffectStatus(current)
        target_status = MailDraftEffectStatus(target)
    except ValueError as exc:
        raise MailProcessingBoundaryError("mail.draft_effect_state_invalid") from exc
    if target_status not in _DRAFT_TRANSITIONS.get(current_status, frozenset()):
        raise MailProcessingBoundaryError("mail.draft_effect_state_invalid")
