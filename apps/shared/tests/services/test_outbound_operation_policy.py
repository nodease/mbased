from dataclasses import FrozenInstanceError

import pytest
from apps.shared.services.egress_guard import EgressGuardError
from apps.shared.services.outbound_operation_policy import (
    GITHUB_ISSUE_COMMENT_CREATE,
    GITHUB_PULL_REQUEST_READ,
    GMAIL_DRAFT_CREATE,
    GMAIL_MESSAGE_MODIFY,
    GMAIL_MESSAGE_READ,
    GMAIL_PROFILE_READ,
    GOOGLE_LOGIN_OIDC,
    GOOGLE_OAUTH_AUTHORIZATION_CODE_EXCHANGE,
    GOOGLE_OAUTH_REFRESH,
    KNOWLEDGE_API_FETCH,
    KNOWLEDGE_DOCUMENT_FETCH,
    LLM_MODEL_DISCOVERY,
    LLM_PROVIDER_CALL,
    SLACK_CHAT_POST_MESSAGE,
    SLACK_INCOMING_WEBHOOK_POST,
    WORKFLOW_REMOTE_FILE_FETCH,
    OutboundOperationProfile,
    outbound_operation_profiles,
)


def test_registered_sensitive_operations_are_https_only_and_versioned() -> None:
    profiles = outbound_operation_profiles()

    assert set(profiles) == {
        GOOGLE_OAUTH_AUTHORIZATION_CODE_EXCHANGE,
        GOOGLE_OAUTH_REFRESH,
        GOOGLE_LOGIN_OIDC,
        GMAIL_PROFILE_READ,
        GMAIL_MESSAGE_READ,
        GMAIL_MESSAGE_MODIFY,
        GMAIL_DRAFT_CREATE,
        GITHUB_PULL_REQUEST_READ,
        GITHUB_ISSUE_COMMENT_CREATE,
        SLACK_CHAT_POST_MESSAGE,
        SLACK_INCOMING_WEBHOOK_POST,
        LLM_PROVIDER_CALL,
        LLM_MODEL_DISCOVERY,
        KNOWLEDGE_API_FETCH,
        KNOWLEDGE_DOCUMENT_FETCH,
        WORKFLOW_REMOTE_FILE_FETCH,
    }
    for profile in profiles.values():
        assert profile.policy.allowed_schemes == frozenset({"https"})
        assert profile.policy.allowed_ports == frozenset({443})
        assert len(profile.revision) == 64

    discovery = profiles[LLM_MODEL_DISCOVERY]
    assert discovery.policy.allowed_methods == frozenset({"GET"})
    assert discovery.policy.timeout_seconds == 10.0


@pytest.mark.parametrize(
    ("operation_id", "methods", "request_bytes", "response_bytes"),
    [
        (GOOGLE_OAUTH_AUTHORIZATION_CODE_EXCHANGE, {"POST"}, 64 * 1024, 1024 * 1024),
        (GOOGLE_OAUTH_REFRESH, {"POST"}, 64 * 1024, 1024 * 1024),
        (GOOGLE_LOGIN_OIDC, {"GET", "POST"}, 64 * 1024, 1024 * 1024),
        (GMAIL_PROFILE_READ, {"GET"}, 0, 256 * 1024),
        (GMAIL_MESSAGE_READ, {"GET"}, 0, 2 * 1024 * 1024),
        (GMAIL_MESSAGE_MODIFY, {"POST"}, 256 * 1024, 512 * 1024),
        (GMAIL_DRAFT_CREATE, {"POST"}, 1024 * 1024, 512 * 1024),
        (GITHUB_PULL_REQUEST_READ, {"GET"}, 0, 10 * 1024 * 1024),
        (GITHUB_ISSUE_COMMENT_CREATE, {"POST"}, 512 * 1024, 1024 * 1024),
        (SLACK_CHAT_POST_MESSAGE, {"POST"}, 256 * 1024, 64 * 1024),
        (SLACK_INCOMING_WEBHOOK_POST, {"POST"}, 256 * 1024, 64 * 1024),
    ],
)
def test_fixed_saas_operation_profiles_are_narrowly_bounded(
    operation_id,
    methods,
    request_bytes,
    response_bytes,
) -> None:
    profile = outbound_operation_profiles()[operation_id]

    assert profile.policy.allowed_methods == frozenset(methods)
    assert profile.policy.max_request_bytes == request_bytes
    assert profile.policy.max_response_bytes == response_bytes
    assert profile.policy.max_redirects == 0
    assert profile.policy.force_identity_encoding is True
    assert profile.policy.allow_compressed_response is False


def test_profile_registry_and_profile_are_immutable() -> None:
    profiles = outbound_operation_profiles()

    with pytest.raises(TypeError):
        profiles["new.operation"] = profiles[LLM_PROVIDER_CALL]  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        profiles[LLM_PROVIDER_CALL].operation_id = "changed"  # type: ignore[misc]


def test_profile_revision_is_deterministic_and_changes_with_policy() -> None:
    current = outbound_operation_profiles()[LLM_PROVIDER_CALL]
    same = OutboundOperationProfile(
        operation_id=current.operation_id,
        policy=current.policy,
    )
    changed = OutboundOperationProfile(
        operation_id=current.operation_id,
        policy=current.policy.__class__(
            **{
                **current.policy.__dict__,
                "max_response_bytes": current.policy.max_response_bytes + 1,
            }
        ),
    )

    assert same.revision == current.revision
    assert changed.revision != current.revision


def test_bound_operation_rejects_unapproved_origin_before_transport(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda *_args, **_kwargs: [
            (2, 1, 6, "", ("93.184.216.34", 443)),
        ],
    )
    bound = outbound_operation_profiles()[LLM_PROVIDER_CALL].bind(
        "https://provider.example/v1"
    )

    assert (
        bound.validate_url("https://provider.example/v1/responses")
        == "https://provider.example/v1/responses"
    )
    with pytest.raises(EgressGuardError) as captured:
        bound.validate_url("https://exfiltration.example/v1/responses")

    assert captured.value.reason_code == "egress.origin_not_allowed"


def test_operation_binding_accepts_path_and_query_on_the_approved_origin(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda *_args, **_kwargs: [
            (2, 1, 6, "", ("93.184.216.34", 443)),
        ],
    )
    bound = outbound_operation_profiles()[KNOWLEDGE_DOCUMENT_FETCH].bind(
        "https://documents.example/files/policy.pdf?signature=opaque"
    )

    assert bound.validate_url(
        "https://documents.example/files/policy.pdf?signature=opaque"
    ).endswith("/files/policy.pdf?signature=opaque")


@pytest.mark.parametrize(
    "origin",
    [
        "http://provider.example",
        "https://user@provider.example",
        "https://*.provider.example",
        "https://provider.example/path#fragment",
    ],
)
def test_operation_binding_rejects_non_origin_or_insecure_authority(origin) -> None:
    profile = outbound_operation_profiles()[LLM_PROVIDER_CALL]

    with pytest.raises(EgressGuardError):
        profile.bind(origin)
