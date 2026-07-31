from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, fields
from types import MappingProxyType
from typing import Any, Mapping
from urllib.parse import urlsplit

from apps.shared.services.egress_guard import (
    API_RESPONSE_CONTENT_TYPES,
    DOCUMENT_RESPONSE_CONTENT_TYPES,
    EgressGuardError,
    EgressGuardPolicy,
    OutboundEgressGuard,
    canonicalize_network_host,
)

LLM_PROVIDER_CALL = "llm.provider.call"
LLM_MODEL_DISCOVERY = "llm.model.discovery"
KNOWLEDGE_API_FETCH = "knowledge.api.fetch"
KNOWLEDGE_DOCUMENT_FETCH = "knowledge.document.fetch"
WORKFLOW_REMOTE_FILE_FETCH = "workflow.remote_file.fetch"
GOOGLE_OAUTH_AUTHORIZATION_CODE_EXCHANGE = "google.oauth.authorization_code.exchange"
GOOGLE_OAUTH_REFRESH = "google.oauth.refresh"
GOOGLE_LOGIN_OIDC = "google.login.oidc"
GMAIL_PROFILE_READ = "gmail.profile.read"
GMAIL_MESSAGE_READ = "gmail.message.read"
GMAIL_MESSAGE_MODIFY = "gmail.message.modify"
GMAIL_DRAFT_CREATE = "gmail.draft.create"
GITHUB_PULL_REQUEST_READ = "github.pull_request.read"
GITHUB_ISSUE_COMMENT_CREATE = "github.issue_comment.create"
SLACK_CHAT_POST_MESSAGE = "slack.chat.post_message"
SLACK_INCOMING_WEBHOOK_POST = "slack.incoming_webhook.post"


def _canonical_value(value: Any) -> Any:
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    return value


def _policy_revision(operation_id: str, policy: EgressGuardPolicy) -> str:
    payload = {
        "operation_id": operation_id,
        "policy": {
            item.name: _canonical_value(getattr(policy, item.name))
            for item in fields(policy)
        },
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _canonical_origin(value: str, *, allowed_schemes: frozenset[str]) -> str:
    try:
        parsed = urlsplit(str(value or "").strip())
        scheme = parsed.scheme.lower()
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise EgressGuardError("egress.invalid_url") from exc
    if (
        scheme not in allowed_schemes
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or "*" in hostname
    ):
        raise EgressGuardError("egress.invalid_url")
    host = canonicalize_network_host(hostname)
    default_port = 443 if scheme == "https" else 80
    netloc = f"[{host}]" if ":" in host else host
    if port is not None and port != default_port:
        netloc = f"{netloc}:{port}"
    return f"{scheme}://{netloc}"


@dataclass(frozen=True)
class OutboundOperationProfile:
    operation_id: str
    policy: EgressGuardPolicy
    revision: str = field(init=False)

    def __post_init__(self) -> None:
        operation_id = self.operation_id.strip()
        if not operation_id or operation_id != self.operation_id:
            raise ValueError("Outbound operation ID is invalid")
        object.__setattr__(
            self, "revision", _policy_revision(operation_id, self.policy)
        )

    def bind(self, approved_endpoint: str) -> BoundOutboundOperation:
        origin = _canonical_origin(
            approved_endpoint,
            allowed_schemes=self.policy.allowed_schemes,
        )
        return BoundOutboundOperation(profile=self, allowed_origins=frozenset({origin}))


@dataclass(frozen=True)
class BoundOutboundOperation:
    profile: OutboundOperationProfile
    allowed_origins: frozenset[str]

    def __post_init__(self) -> None:
        if not self.allowed_origins:
            raise ValueError("At least one approved origin is required")

    @property
    def guard(self) -> OutboundEgressGuard:
        return OutboundEgressGuard(self.profile.policy)

    def validate_origin(self, url: str) -> None:
        request_origin = _canonical_origin(
            url,
            allowed_schemes=self.profile.policy.allowed_schemes,
        )
        if request_origin not in self.allowed_origins:
            raise EgressGuardError("egress.origin_not_allowed")

    def validate_url(self, url: str) -> str:
        self.validate_origin(url)
        return self.guard.validate_url(url)

    def validate_url_policy(self, url: str) -> str:
        self.validate_origin(url)
        return self.guard.validate_url_policy(url)


def _profile(
    operation_id: str,
    *,
    methods: frozenset[str],
    max_request_bytes: int,
    max_response_bytes: int,
    timeout_seconds: float,
    content_types: frozenset[str] | None,
    max_redirects: int = 0,
) -> OutboundOperationProfile:
    return OutboundOperationProfile(
        operation_id=operation_id,
        policy=EgressGuardPolicy(
            allowed_schemes=frozenset({"https"}),
            allowed_methods=methods,
            allowed_ports=frozenset({443}),
            deny_private_networks=True,
            deny_url_credentials=True,
            deny_https_downgrade=True,
            max_redirects=max_redirects,
            timeout_seconds=timeout_seconds,
            max_request_bytes=max_request_bytes,
            max_response_bytes=max_response_bytes,
            allowed_content_types=content_types,
            force_identity_encoding=True,
            allow_compressed_response=False,
            validate_peer_ip=True,
        ),
    )


_PROFILES: Mapping[str, OutboundOperationProfile] = MappingProxyType(
    {
        LLM_PROVIDER_CALL: _profile(
            LLM_PROVIDER_CALL,
            methods=frozenset({"GET", "POST"}),
            max_request_bytes=16 * 1024 * 1024,
            max_response_bytes=16 * 1024 * 1024,
            timeout_seconds=180.0,
            content_types=frozenset({"application/json", "text/event-stream"}),
        ),
        LLM_MODEL_DISCOVERY: _profile(
            LLM_MODEL_DISCOVERY,
            methods=frozenset({"GET"}),
            max_request_bytes=0,
            max_response_bytes=4 * 1024 * 1024,
            timeout_seconds=10.0,
            content_types=frozenset({"application/json"}),
        ),
        KNOWLEDGE_API_FETCH: _profile(
            KNOWLEDGE_API_FETCH,
            methods=frozenset({"GET", "POST"}),
            max_request_bytes=1024 * 1024,
            max_response_bytes=10 * 1024 * 1024,
            timeout_seconds=30.0,
            content_types=API_RESPONSE_CONTENT_TYPES,
            max_redirects=3,
        ),
        KNOWLEDGE_DOCUMENT_FETCH: _profile(
            KNOWLEDGE_DOCUMENT_FETCH,
            methods=frozenset({"GET"}),
            max_request_bytes=0,
            max_response_bytes=50 * 1024 * 1024,
            timeout_seconds=30.0,
            content_types=DOCUMENT_RESPONSE_CONTENT_TYPES,
            max_redirects=3,
        ),
        WORKFLOW_REMOTE_FILE_FETCH: _profile(
            WORKFLOW_REMOTE_FILE_FETCH,
            methods=frozenset({"GET"}),
            max_request_bytes=0,
            max_response_bytes=50 * 1024 * 1024,
            timeout_seconds=30.0,
            content_types=DOCUMENT_RESPONSE_CONTENT_TYPES,
            max_redirects=3,
        ),
        GOOGLE_OAUTH_AUTHORIZATION_CODE_EXCHANGE: _profile(
            GOOGLE_OAUTH_AUTHORIZATION_CODE_EXCHANGE,
            methods=frozenset({"POST"}),
            max_request_bytes=64 * 1024,
            max_response_bytes=1024 * 1024,
            timeout_seconds=10.0,
            content_types=frozenset({"application/json"}),
        ),
        GOOGLE_OAUTH_REFRESH: _profile(
            GOOGLE_OAUTH_REFRESH,
            methods=frozenset({"POST"}),
            max_request_bytes=64 * 1024,
            max_response_bytes=1024 * 1024,
            timeout_seconds=10.0,
            content_types=frozenset({"application/json"}),
        ),
        GOOGLE_LOGIN_OIDC: _profile(
            GOOGLE_LOGIN_OIDC,
            methods=frozenset({"GET", "POST"}),
            max_request_bytes=64 * 1024,
            max_response_bytes=1024 * 1024,
            timeout_seconds=10.0,
            content_types=frozenset({"application/json"}),
        ),
        GMAIL_PROFILE_READ: _profile(
            GMAIL_PROFILE_READ,
            methods=frozenset({"GET"}),
            max_request_bytes=0,
            max_response_bytes=256 * 1024,
            timeout_seconds=10.0,
            content_types=frozenset({"application/json"}),
        ),
        GMAIL_MESSAGE_READ: _profile(
            GMAIL_MESSAGE_READ,
            methods=frozenset({"GET"}),
            max_request_bytes=0,
            max_response_bytes=2 * 1024 * 1024,
            timeout_seconds=10.0,
            content_types=frozenset({"application/json"}),
        ),
        GMAIL_MESSAGE_MODIFY: _profile(
            GMAIL_MESSAGE_MODIFY,
            methods=frozenset({"POST"}),
            max_request_bytes=256 * 1024,
            max_response_bytes=512 * 1024,
            timeout_seconds=10.0,
            content_types=None,
        ),
        GMAIL_DRAFT_CREATE: _profile(
            GMAIL_DRAFT_CREATE,
            methods=frozenset({"POST"}),
            max_request_bytes=1024 * 1024,
            max_response_bytes=512 * 1024,
            timeout_seconds=15.0,
            content_types=frozenset({"application/json"}),
        ),
        GITHUB_PULL_REQUEST_READ: _profile(
            GITHUB_PULL_REQUEST_READ,
            methods=frozenset({"GET"}),
            max_request_bytes=0,
            max_response_bytes=10 * 1024 * 1024,
            timeout_seconds=30.0,
            content_types=frozenset({"application/json"}),
        ),
        GITHUB_ISSUE_COMMENT_CREATE: _profile(
            GITHUB_ISSUE_COMMENT_CREATE,
            methods=frozenset({"POST"}),
            max_request_bytes=512 * 1024,
            max_response_bytes=1024 * 1024,
            timeout_seconds=30.0,
            content_types=frozenset({"application/json"}),
        ),
        SLACK_CHAT_POST_MESSAGE: _profile(
            SLACK_CHAT_POST_MESSAGE,
            methods=frozenset({"POST"}),
            max_request_bytes=256 * 1024,
            max_response_bytes=64 * 1024,
            timeout_seconds=10.0,
            content_types=None,
        ),
        SLACK_INCOMING_WEBHOOK_POST: _profile(
            SLACK_INCOMING_WEBHOOK_POST,
            methods=frozenset({"POST"}),
            max_request_bytes=256 * 1024,
            max_response_bytes=64 * 1024,
            timeout_seconds=10.0,
            content_types=None,
        ),
    }
)


def outbound_operation_profiles() -> Mapping[str, OutboundOperationProfile]:
    return _PROFILES


def require_outbound_operation_profile(operation_id: str) -> OutboundOperationProfile:
    try:
        return _PROFILES[operation_id]
    except KeyError as exc:
        raise EgressGuardError("egress.operation_not_allowed") from exc


__all__ = [
    "BoundOutboundOperation",
    "GITHUB_ISSUE_COMMENT_CREATE",
    "GITHUB_PULL_REQUEST_READ",
    "GMAIL_DRAFT_CREATE",
    "GMAIL_MESSAGE_MODIFY",
    "GMAIL_MESSAGE_READ",
    "GMAIL_PROFILE_READ",
    "GOOGLE_OAUTH_AUTHORIZATION_CODE_EXCHANGE",
    "GOOGLE_OAUTH_REFRESH",
    "GOOGLE_LOGIN_OIDC",
    "KNOWLEDGE_API_FETCH",
    "KNOWLEDGE_DOCUMENT_FETCH",
    "LLM_MODEL_DISCOVERY",
    "LLM_PROVIDER_CALL",
    "OutboundOperationProfile",
    "SLACK_CHAT_POST_MESSAGE",
    "SLACK_INCOMING_WEBHOOK_POST",
    "WORKFLOW_REMOTE_FILE_FETCH",
    "outbound_operation_profiles",
    "require_outbound_operation_profile",
]
