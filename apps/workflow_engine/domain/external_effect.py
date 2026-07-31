from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from apps.shared.domain.external_effect_error import (
    ALLOWED_EFFECT_ERROR_CODES as ALLOWED_EFFECT_ERROR_CODES,
    ALLOWED_EXTERNAL_EFFECT_CONTROL_CODES,
    safe_effect_error_code as safe_effect_error_code,
)


MAX_REPLAY_RESULT_BYTES = 65_536


class ProviderReplayCapability(str, Enum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


class ResultReuseCapability(str, Enum):
    SUPPORTED = "supported"
    UNAVAILABLE = "unavailable"


class EffectAttemptStatus(str, Enum):
    PREPARED = "prepared"
    IN_FLIGHT = "in_flight"
    TERMINAL = "terminal"


class EffectOutcome(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED_BEFORE_EFFECT = "failed_before_effect"
    EFFECT_OUTCOME_UNKNOWN = "effect_outcome_unknown"


class ReplayDecision(str, Enum):
    REUSE_RESULT = "reuse_result"
    RESULT_UNAVAILABLE = "result_unavailable"
    RETRY_BEFORE_EFFECT = "retry_before_effect"
    REPLAY_SAME_KEY = "replay_same_key"
    STOP = "stop"


def canonical_json_pointer_parts(pointer: str) -> tuple[str, ...]:
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ValueError("body key field must be a canonical JSON Pointer")
    parts: list[str] = []
    for raw_part in pointer[1:].split("/"):
        index = 0
        decoded: list[str] = []
        while index < len(raw_part):
            if raw_part[index] != "~":
                decoded.append(raw_part[index])
                index += 1
                continue
            if index + 1 >= len(raw_part) or raw_part[index + 1] not in {"0", "1"}:
                raise ValueError("body key field contains an invalid JSON Pointer")
            decoded.append("~" if raw_part[index + 1] == "0" else "/")
            index += 2
        parts.append("".join(decoded))
    return tuple(parts)


@dataclass(frozen=True)
class ExternalEffectContext:
    organization_id: uuid.UUID
    app_id: uuid.UUID
    workflow_id: uuid.UUID
    execution_id: uuid.UUID
    node_invocation_id: uuid.UUID
    node_id: str
    workflow_run_id: uuid.UUID | None = None
    node_run_id: uuid.UUID | None = None

    def __post_init__(self) -> None:
        for name in (
            "organization_id",
            "app_id",
            "workflow_id",
            "execution_id",
            "node_invocation_id",
        ):
            if not isinstance(getattr(self, name), uuid.UUID):
                raise ValueError(f"invalid external effect context: {name}")
        if not self.node_id:
            raise ValueError("invalid external effect context: node_id")


@dataclass(frozen=True)
class ProviderContractProfile:
    provider: str
    operation: str
    contract_version: str
    provider_replay: ProviderReplayCapability
    result_reuse: ResultReuseCapability
    key_transport: str
    key_field: str | None
    key_format: str | None
    key_max_length: int | None
    retention: timedelta | None
    duplicate_semantics: str
    official_reference: str
    request_semantics: str
    response_semantics: str
    replay_projection_semantics: str | None
    test_only: bool = False

    def __post_init__(self) -> None:
        if not self.request_semantics or not self.response_semantics:
            raise ValueError("provider contract semantics must be explicit")
        if (
            self.result_reuse is ResultReuseCapability.SUPPORTED
            and not self.replay_projection_semantics
        ):
            raise ValueError("reusable results require replay projection semantics")
        if (
            self.result_reuse is ResultReuseCapability.UNAVAILABLE
            and self.replay_projection_semantics is not None
        ):
            raise ValueError(
                "unavailable results cannot define replay projection semantics"
            )
        if self.provider_replay is ProviderReplayCapability.SUPPORTED:
            if self.key_transport not in {"header", "body"}:
                raise ValueError("supported provider requires key transport")
            if not self.key_field or not self.key_format or not self.key_max_length:
                raise ValueError("supported provider requires a complete key contract")
            if self.retention is None or self.retention.total_seconds() <= 0:
                raise ValueError("supported provider requires positive retention")
            if self.key_transport == "body":
                canonical_json_pointer_parts(self.key_field)
        elif self.key_transport not in {"none", "unknown"}:
            raise ValueError("non-supported provider cannot receive a system key")


class ProviderContractRegistry:
    def __init__(
        self,
        profiles: Iterable[ProviderContractProfile],
        *,
        active_versions: Mapping[tuple[str, str], str],
    ) -> None:
        indexed: dict[tuple[str, str, str], ProviderContractProfile] = {}
        for profile in profiles:
            key = (profile.provider, profile.operation, profile.contract_version)
            if key in indexed:
                raise ValueError("duplicate provider contract profile")
            indexed[key] = profile
        available_operations = {
            (profile.provider, profile.operation) for profile in indexed.values()
        }
        if set(active_versions) != available_operations:
            raise ValueError("each provider operation requires one active contract")
        active: dict[tuple[str, str], ProviderContractProfile] = {}
        for operation_key, version in active_versions.items():
            profile = indexed.get((*operation_key, version))
            if profile is None:
                raise ValueError("active provider contract is not registered")
            active[operation_key] = profile
        self._profiles: Mapping[tuple[str, str, str], ProviderContractProfile] = (
            MappingProxyType(indexed)
        )
        self._active: Mapping[tuple[str, str], ProviderContractProfile] = (
            MappingProxyType(active)
        )

    def get(
        self, provider: str, operation: str, version: str
    ) -> ProviderContractProfile:
        return self._profiles[(provider, operation, version)]

    def profiles(self) -> tuple[ProviderContractProfile, ...]:
        return tuple(self._profiles.values())

    def active(self, provider: str, operation: str) -> ProviderContractProfile:
        return self._active[(provider, operation)]

    def profiles_for(
        self,
        provider: str,
        operation: str,
    ) -> tuple[ProviderContractProfile, ...]:
        return tuple(
            profile
            for profile in self._profiles.values()
            if profile.provider == provider and profile.operation == operation
        )


_PRODUCTION_PROFILES = (
    ProviderContractProfile(
        provider="generic_http",
        operation="generic_http.request",
        contract_version="generic_http.request.v1",
        provider_replay=ProviderReplayCapability.UNKNOWN,
        result_reuse=ResultReuseCapability.UNAVAILABLE,
        key_transport="unknown",
        key_field=None,
        key_format=None,
        key_max_length=None,
        retention=None,
        duplicate_semantics="unknown",
        official_reference="https://www.rfc-editor.org/rfc/rfc9110.html",
        request_semantics="generic_http.request.v1",
        response_semantics="generic_http.response.v1",
        replay_projection_semantics=None,
    ),
    ProviderContractProfile(
        provider="slack",
        operation="slack.http.request",
        contract_version="slack.http.request.v1",
        provider_replay=ProviderReplayCapability.UNKNOWN,
        result_reuse=ResultReuseCapability.UNAVAILABLE,
        key_transport="unknown",
        key_field=None,
        key_format=None,
        key_max_length=None,
        retention=None,
        duplicate_semantics="unknown",
        official_reference="https://docs.slack.dev/reference/methods/chat.postMessage/",
        request_semantics="generic_http.request.v1",
        response_semantics="generic_http.response.v1",
        replay_projection_semantics=None,
    ),
    ProviderContractProfile(
        provider="slack",
        operation="slack.chat.post_message",
        contract_version="slack.chat.post_message.v1",
        provider_replay=ProviderReplayCapability.UNKNOWN,
        result_reuse=ResultReuseCapability.SUPPORTED,
        key_transport="unknown",
        key_field=None,
        key_format=None,
        key_max_length=None,
        retention=None,
        duplicate_semantics=(
            "Slack does not accept a Nodease idempotency key; a completed local "
            "attempt reuses only the stored safe result projection"
        ),
        official_reference="https://docs.slack.dev/reference/methods/chat.postMessage/",
        request_semantics="slack.chat.post_message.request.v1",
        response_semantics="slack.chat.post_message.response.v1",
        replay_projection_semantics="slack.delivery.replay_projection.v1",
    ),
    ProviderContractProfile(
        provider="slack",
        operation="slack.incoming_webhook.post",
        contract_version="slack.incoming_webhook.post.v1",
        provider_replay=ProviderReplayCapability.UNKNOWN,
        result_reuse=ResultReuseCapability.SUPPORTED,
        key_transport="unknown",
        key_field=None,
        key_format=None,
        key_max_length=None,
        retention=None,
        duplicate_semantics=(
            "Slack does not accept a Nodease idempotency key; a completed local "
            "attempt reuses only the stored safe result projection"
        ),
        official_reference="https://docs.slack.dev/messaging/sending-messages-using-incoming-webhooks/",
        request_semantics="slack.incoming_webhook.post.request.v1",
        response_semantics="slack.incoming_webhook.post.response.v1",
        replay_projection_semantics="slack.delivery.replay_projection.v1",
    ),
    ProviderContractProfile(
        provider="github",
        operation="github.issue_comment.create",
        contract_version="github.issue_comment.create.v1",
        provider_replay=ProviderReplayCapability.UNSUPPORTED,
        result_reuse=ResultReuseCapability.UNAVAILABLE,
        key_transport="none",
        key_field=None,
        key_format=None,
        key_max_length=None,
        retention=None,
        duplicate_semantics="unavailable",
        official_reference="https://docs.github.com/en/rest/issues/comments#create-an-issue-comment",
        request_semantics="github.issue_comment.request.v1",
        response_semantics="github.issue_comment.response.v1",
        replay_projection_semantics=None,
    ),
)

_TEST_PROFILE = ProviderContractProfile(
    provider="fake",
    operation="fake.create_effect",
    contract_version="fake.create_effect.v1",
    provider_replay=ProviderReplayCapability.SUPPORTED,
    result_reuse=ResultReuseCapability.SUPPORTED,
    key_transport="header",
    key_field="Idempotency-Key",
    key_format="hmac-b64url-v1",
    key_max_length=64,
    retention=timedelta(hours=24),
    duplicate_semantics="same key and request returns 200 with the original effect id",
    official_reference="test-only contract",
    request_semantics="fake.request.v1",
    response_semantics="fake.response.v1",
    replay_projection_semantics="fake.replay_projection.v1",
    test_only=True,
)

_ACTIVE_PRODUCTION_VERSIONS = {
    (profile.provider, profile.operation): profile.contract_version
    for profile in _PRODUCTION_PROFILES
}


def provider_contract_registry(
    *, include_test_profiles: bool = False
) -> ProviderContractRegistry:
    profiles = _PRODUCTION_PROFILES + (
        (_TEST_PROFILE,) if include_test_profiles else ()
    )
    active_versions = dict(_ACTIVE_PRODUCTION_VERSIONS)
    if include_test_profiles:
        active_versions[(_TEST_PROFILE.provider, _TEST_PROFILE.operation)] = (
            _TEST_PROFILE.contract_version
        )
    return ProviderContractRegistry(profiles, active_versions=active_versions)


def _framed_field(name: str, value: str) -> bytes:
    name_bytes = name.encode("utf-8")
    value_bytes = value.encode("utf-8")
    if len(name_bytes) >= 2**32 or len(value_bytes) >= 2**32:
        raise ValueError("provider key field is too large")
    return (
        len(name_bytes).to_bytes(4, "big")
        + name_bytes
        + len(value_bytes).to_bytes(4, "big")
        + value_bytes
    )


def build_provider_idempotency_key(
    secret: bytes,
    *,
    organization_id: uuid.UUID,
    app_id: uuid.UUID,
    workflow_id: uuid.UUID,
    execution_id: uuid.UUID,
    node_invocation_id: uuid.UUID,
    operation: str,
    effect_sequence: int,
) -> str:
    if not secret:
        raise ValueError("idempotency key secret is missing")
    if effect_sequence < 0:
        raise ValueError("effect sequence must be non-negative")
    fields = (
        ("domain", "nodease.external-effect-key.v1"),
        ("organization_id", str(organization_id).lower()),
        ("app_id", str(app_id).lower()),
        ("workflow_id", str(workflow_id).lower()),
        ("execution_id", str(execution_id).lower()),
        ("node_invocation_id", str(node_invocation_id).lower()),
        ("operation", operation),
        ("effect_sequence", str(effect_sequence)),
    )
    framed = b"".join(_framed_field(name, value) for name, value in fields)
    digest = hmac.new(secret, framed, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


@dataclass(frozen=True)
class CanonicalReplayResult:
    value: Any
    canonical_bytes: bytes

    @property
    def size_bytes(self) -> int:
        return len(self.canonical_bytes)


def canonical_replay_result(value: Any) -> CanonicalReplayResult:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValueError("replay result must be canonical JSON") from exc
    if len(encoded) > MAX_REPLAY_RESULT_BYTES:
        raise ValueError("replay result exceeds 65,536 bytes")
    return CanonicalReplayResult(value=value, canonical_bytes=encoded)


def decide_replay(
    *,
    outcome: EffectOutcome,
    provider_replay: ProviderReplayCapability,
    result_reuse: ResultReuseCapability,
    has_replay_result: bool,
    retry_before_effect: bool = True,
    now: datetime | None = None,
    replay_deadline_at: datetime | None = None,
) -> ReplayDecision:
    if outcome is EffectOutcome.SUCCEEDED:
        if result_reuse is ResultReuseCapability.SUPPORTED and has_replay_result:
            return ReplayDecision.REUSE_RESULT
        return ReplayDecision.RESULT_UNAVAILABLE
    if outcome is EffectOutcome.FAILED_BEFORE_EFFECT:
        return (
            ReplayDecision.RETRY_BEFORE_EFFECT
            if retry_before_effect
            else ReplayDecision.STOP
        )
    if provider_replay is not ProviderReplayCapability.SUPPORTED:
        return ReplayDecision.STOP
    if replay_deadline_at is None:
        return ReplayDecision.STOP
    current = now or datetime.now(timezone.utc)
    return (
        ReplayDecision.REPLAY_SAME_KEY
        if current < replay_deadline_at
        else ReplayDecision.STOP
    )


@dataclass(frozen=True)
class PreparedEffectRequest:
    request: Any
    effect_input_digest: str
    profile: ProviderContractProfile | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class PreparedProviderCall:
    request: Any
    idempotency_key: str | None
    profile: ProviderContractProfile


@dataclass(frozen=True)
class ProviderInvocationResult:
    output: Any
    provider_status_code: int | None = None


class EffectInvocationFailure(Exception):
    def __init__(
        self,
        *,
        outcome: EffectOutcome,
        error_code: str,
        provider_status_code: int | None = None,
        retry_before_effect: bool = True,
    ) -> None:
        super().__init__(error_code)
        self.outcome = outcome
        self.error_code = error_code
        self.provider_status_code = provider_status_code
        self.retry_before_effect = retry_before_effect


class ExternalEffectError(Exception):
    def __init__(
        self,
        code: str,
        *,
        retryable: bool,
        node_id: str | None = None,
    ) -> None:
        if (
            not isinstance(code, str)
            or code not in ALLOWED_EXTERNAL_EFFECT_CONTROL_CODES
        ):
            code = "external_effect.stopped"
            retryable = False
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.node_id = node_id

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.code,
            "retryable": self.retryable,
        }
        if self.node_id:
            payload["node_id"] = self.node_id
        return payload


class ExternalEffectRetrySignal(ExternalEffectError):
    def __init__(
        self,
        code: str,
        *,
        node_id: str | None = None,
        terminal_code: str | None = None,
    ) -> None:
        super().__init__(code, retryable=True, node_id=node_id)
        terminal_error = ExternalEffectError(
            terminal_code or self.code,
            retryable=False,
            node_id=node_id,
        )
        self.terminal_code = terminal_error.code
