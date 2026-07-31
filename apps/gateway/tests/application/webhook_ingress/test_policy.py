import json

import pytest

from apps.gateway.application.webhook_ingress import (
    DEFAULT_WEBHOOK_INGRESS_POLICY,
    WebhookIngressError,
    WebhookIngressLimits,
    WebhookIngressPolicy,
    WebhookIngressRequestMetadata,
)


class ManualClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


def metadata(
    *,
    query_token_present: bool = False,
    authorization: tuple[bytes, ...] = (),
    webhook_secret: tuple[bytes, ...] = (),
    content_type: tuple[bytes, ...] = (b"application/json",),
    content_encoding: tuple[bytes, ...] = (),
    content_length: tuple[bytes, ...] = (),
) -> WebhookIngressRequestMetadata:
    return WebhookIngressRequestMetadata(
        query_token_present=query_token_present,
        authorization_headers=authorization,
        webhook_secret_headers=webhook_secret,
        content_type_headers=content_type,
        content_encoding_headers=content_encoding,
        content_length_headers=content_length,
    )


def assert_ingress_error(
    exc_info: pytest.ExceptionInfo[WebhookIngressError],
    *,
    status_code: int,
    code: str,
) -> None:
    assert exc_info.value.status_code == status_code
    assert exc_info.value.code == code
    assert str(exc_info.value) == code


def credential_verifier(expected_secret: str | None):
    def verify(candidate: bytes) -> bool:
        if not isinstance(expected_secret, str):
            return False
        try:
            return candidate == expected_secret.encode("ascii", errors="strict")
        except UnicodeEncodeError:
            return False

    return verify


@pytest.mark.parametrize(
    ("authorization", "webhook_secret"),
    [
        ((b"Bearer secret",), ()),
        ((b"bEaReR secret",), ()),
        ((), (b"secret",)),
    ],
)
def test_authenticate_accepts_exactly_one_valid_header_source(
    authorization: tuple[bytes, ...],
    webhook_secret: tuple[bytes, ...],
) -> None:
    DEFAULT_WEBHOOK_INGRESS_POLICY.authenticate(
        metadata(authorization=authorization, webhook_secret=webhook_secret),
        credential_verifier=credential_verifier("secret"),
    )


@pytest.mark.parametrize("length", [1, 512])
def test_authenticate_accepts_credential_byte_boundaries(length: int) -> None:
    secret = "s" * length

    DEFAULT_WEBHOOK_INGRESS_POLICY.authenticate(
        metadata(authorization=(f"Bearer {secret}".encode("ascii"),)),
        credential_verifier=credential_verifier(secret),
    )


@pytest.mark.parametrize(
    "request_metadata",
    [
        metadata(query_token_present=True),
        metadata(
            query_token_present=True,
            authorization=(b"Bearer secret",),
        ),
    ],
)
def test_authenticate_rejects_query_token_before_header_authentication(
    request_metadata: WebhookIngressRequestMetadata,
) -> None:
    with pytest.raises(WebhookIngressError) as exc_info:
        DEFAULT_WEBHOOK_INGRESS_POLICY.authenticate(
            request_metadata,
            credential_verifier=credential_verifier("secret"),
        )

    assert_ingress_error(
        exc_info,
        status_code=400,
        code="webhook.query_secret_not_supported",
    )


@pytest.mark.parametrize(
    "request_metadata",
    [
        metadata(
            authorization=(b"Bearer secret",),
            webhook_secret=(b"secret",),
        ),
        metadata(authorization=(b"Bearer secret", b"Bearer secret")),
        metadata(webhook_secret=(b"secret", b"secret")),
        metadata(authorization=(b"Bearer secret,Bearer secret",)),
        metadata(webhook_secret=(b"secret,secret",)),
    ],
)
def test_authenticate_rejects_ambiguous_credential_sources(
    request_metadata: WebhookIngressRequestMetadata,
) -> None:
    with pytest.raises(WebhookIngressError) as exc_info:
        DEFAULT_WEBHOOK_INGRESS_POLICY.authenticate(
            request_metadata,
            credential_verifier=credential_verifier("secret"),
        )

    assert_ingress_error(
        exc_info,
        status_code=400,
        code="webhook.credential_ambiguous",
    )


@pytest.mark.parametrize(
    ("request_metadata", "expected_secret"),
    [
        (metadata(), "secret"),
        (metadata(authorization=(b"Basic secret",)), "secret"),
        (metadata(authorization=(b"Bearer ",)), "secret"),
        (metadata(authorization=(b"Bearer secret ",)), "secret"),
        (metadata(authorization=(b"Bearer wrong",)), "secret"),
        (metadata(authorization=(b"Bearer \xff",)), "secret"),
        (metadata(authorization=(b"Bearer " + (b"s" * 513),)), "s" * 513),
        (metadata(authorization=(b"Bearer secret",)), None),
        (metadata(authorization=(b"Bearer secret",)), ""),
        (metadata(authorization=(b"Bearer secret",)), "비밀"),
    ],
)
def test_authenticate_uses_one_generic_failure_for_invalid_credentials(
    request_metadata: WebhookIngressRequestMetadata,
    expected_secret: str | None,
) -> None:
    with pytest.raises(WebhookIngressError) as exc_info:
        DEFAULT_WEBHOOK_INGRESS_POLICY.authenticate(
            request_metadata,
            credential_verifier=credential_verifier(expected_secret),
        )

    assert_ingress_error(
        exc_info,
        status_code=403,
        code="webhook.authentication_failed",
    )


@pytest.mark.parametrize(
    "content_type",
    [
        b"application/json",
        b"Application/JSON",
        b"application/problem+json",
        b'application/json; charset="utf-8"',
        b"application/vnd.nodease.event+json; version=1; charset=UTF-8",
    ],
)
def test_validate_payload_metadata_accepts_supported_json_media(
    content_type: bytes,
) -> None:
    declared_length = DEFAULT_WEBHOOK_INGRESS_POLICY.validate_payload_metadata(
        metadata(content_type=(content_type,), content_encoding=(b"identity",))
    )

    assert declared_length is None


@pytest.mark.parametrize(
    "request_metadata",
    [
        metadata(content_type=()),
        metadata(content_type=(b"application/json", b"application/json")),
        metadata(content_type=(b"text/plain",)),
        metadata(content_type=(b"application/json; charset=utf-16",)),
        metadata(content_type=(b'application/json; charset="utf-8',)),
        metadata(content_type=(b"application/+json",)),
        metadata(content_encoding=(b"gzip",)),
        metadata(content_encoding=(b"identity,gzip",)),
        metadata(content_encoding=(b"identity", b"identity")),
    ],
)
def test_validate_payload_metadata_rejects_unsupported_media_contract(
    request_metadata: WebhookIngressRequestMetadata,
) -> None:
    with pytest.raises(WebhookIngressError) as exc_info:
        DEFAULT_WEBHOOK_INGRESS_POLICY.validate_payload_metadata(request_metadata)

    assert_ingress_error(
        exc_info,
        status_code=415,
        code="webhook.payload.unsupported_media_type",
    )


@pytest.mark.parametrize(
    ("content_length", "expected"),
    [
        ((), None),
        ((b"0",), 0),
        ((b"1048576",), 1_048_576),
    ],
)
def test_validate_payload_metadata_accepts_declared_length_boundaries(
    content_length: tuple[bytes, ...],
    expected: int | None,
) -> None:
    assert (
        DEFAULT_WEBHOOK_INGRESS_POLICY.validate_payload_metadata(
            metadata(content_length=content_length)
        )
        == expected
    )


def test_validate_payload_metadata_rejects_declared_oversize() -> None:
    with pytest.raises(WebhookIngressError) as exc_info:
        DEFAULT_WEBHOOK_INGRESS_POLICY.validate_payload_metadata(
            metadata(content_length=(b"1048577",))
        )

    assert_ingress_error(
        exc_info,
        status_code=413,
        code="webhook.payload.too_large",
    )


@pytest.mark.parametrize(
    "content_length",
    [
        (b"1", b"1"),
        (b"-1",),
        (b"+1",),
        (b"1.0",),
        (b" 1",),
        (b"1 ",),
        (b"",),
    ],
)
def test_validate_payload_metadata_rejects_invalid_declared_length(
    content_length: tuple[bytes, ...],
) -> None:
    with pytest.raises(WebhookIngressError) as exc_info:
        DEFAULT_WEBHOOK_INGRESS_POLICY.validate_payload_metadata(
            metadata(content_length=content_length)
        )

    assert_ingress_error(
        exc_info,
        status_code=400,
        code="webhook.payload.invalid",
    )


@pytest.mark.parametrize("size", [0, 1_048_576])
def test_validate_actual_body_size_accepts_boundary(size: int) -> None:
    DEFAULT_WEBHOOK_INGRESS_POLICY.validate_actual_body_size(size)


def test_validate_actual_body_size_rejects_oversize() -> None:
    with pytest.raises(WebhookIngressError) as exc_info:
        DEFAULT_WEBHOOK_INGRESS_POLICY.validate_actual_body_size(1_048_577)

    assert_ingress_error(
        exc_info,
        status_code=413,
        code="webhook.payload.too_large",
    )


@pytest.mark.parametrize("payload", [{}, {"nested": ["text", 1, 1.5, True, None]}])
def test_parse_json_accepts_object_root_and_preserves_nested_types(
    payload: dict[str, object],
) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    deadline = DEFAULT_WEBHOOK_INGRESS_POLICY.start_deadline()

    assert DEFAULT_WEBHOOK_INGRESS_POLICY.parse_json(body, deadline) == payload


@pytest.mark.parametrize("payload", [[], "text", 123, 1.5, True, False, None])
def test_parse_json_rejects_non_object_root_before_downstream(payload: object) -> None:
    deadline = DEFAULT_WEBHOOK_INGRESS_POLICY.start_deadline()

    with pytest.raises(WebhookIngressError) as exc_info:
        DEFAULT_WEBHOOK_INGRESS_POLICY.parse_json(
            json.dumps(payload).encode("ascii"),
            deadline,
        )

    assert_ingress_error(
        exc_info,
        status_code=400,
        code="webhook.payload.invalid",
    )


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"{",
        b"{} trailing",
        b"\xff",
        b"\xef\xbb\xbf{}",
        b"NaN",
        b"Infinity",
        b"-Infinity",
    ],
)
def test_parse_json_rejects_invalid_or_non_standard_json(body: bytes) -> None:
    deadline = DEFAULT_WEBHOOK_INGRESS_POLICY.start_deadline()

    with pytest.raises(WebhookIngressError) as exc_info:
        DEFAULT_WEBHOOK_INGRESS_POLICY.parse_json(body, deadline)

    assert_ingress_error(
        exc_info,
        status_code=400,
        code="webhook.payload.invalid",
    )


def nested_object(depth: int) -> dict[str, object]:
    value: object = 0
    for _ in range(depth - 1):
        value = {"value": value}
    assert isinstance(value, dict)
    return value


@pytest.mark.parametrize("depth", [19, 20])
def test_parse_json_accepts_depth_boundary(depth: int) -> None:
    body = json.dumps(nested_object(depth)).encode("ascii")
    deadline = DEFAULT_WEBHOOK_INGRESS_POLICY.start_deadline()

    assert DEFAULT_WEBHOOK_INGRESS_POLICY.parse_json(body, deadline) == nested_object(
        depth
    )


def test_parse_json_rejects_depth_over_limit_without_recursive_validation() -> None:
    body = json.dumps(nested_object(21)).encode("ascii")
    deadline = DEFAULT_WEBHOOK_INGRESS_POLICY.start_deadline()

    with pytest.raises(WebhookIngressError) as exc_info:
        DEFAULT_WEBHOOK_INGRESS_POLICY.parse_json(body, deadline)

    assert_ingress_error(
        exc_info,
        status_code=400,
        code="webhook.payload.invalid",
    )


@pytest.mark.parametrize("node_count", [9_999, 10_000])
def test_parse_json_accepts_node_count_boundary(node_count: int) -> None:
    payload = {"items": [None] * (node_count - 2)}
    body = json.dumps(payload).encode("ascii")
    deadline = DEFAULT_WEBHOOK_INGRESS_POLICY.start_deadline()

    assert DEFAULT_WEBHOOK_INGRESS_POLICY.parse_json(body, deadline) == payload


def test_parse_json_rejects_node_count_over_limit() -> None:
    body = json.dumps({"items": [None] * 9_999}).encode("ascii")
    deadline = DEFAULT_WEBHOOK_INGRESS_POLICY.start_deadline()

    with pytest.raises(WebhookIngressError) as exc_info:
        DEFAULT_WEBHOOK_INGRESS_POLICY.parse_json(body, deadline)

    assert_ingress_error(
        exc_info,
        status_code=400,
        code="webhook.payload.invalid",
    )


def test_parse_json_preserves_internal_looking_keys_as_payload_only() -> None:
    payload = {
        "app_id": "attacker-app",
        "organization_id": "attacker-org",
        "workflow_id": "attacker-workflow",
        "deployment_id": "attacker-deployment",
        "user_id": "attacker-user",
        "trigger_mode": "interactive",
        "execution_context": {"admin": True},
    }
    deadline = DEFAULT_WEBHOOK_INGRESS_POLICY.start_deadline()

    assert DEFAULT_WEBHOOK_INGRESS_POLICY.parse_json(
        json.dumps(payload).encode("ascii"),
        deadline,
    ) == payload


def test_deadline_is_shared_by_decode_parse_and_complexity_validation() -> None:
    clock = ManualClock()
    policy = WebhookIngressPolicy(
        limits=WebhookIngressLimits(processing_timeout_seconds=5.0),
        clock=clock,
    )
    deadline = policy.start_deadline()
    clock.value = 5.1

    with pytest.raises(WebhookIngressError) as exc_info:
        policy.parse_json(b"{}", deadline)

    assert_ingress_error(
        exc_info,
        status_code=408,
        code="webhook.payload.timeout",
    )


def test_policy_limits_are_immutable() -> None:
    with pytest.raises((AttributeError, TypeError)):
        DEFAULT_WEBHOOK_INGRESS_POLICY.limits.max_body_bytes = 10
