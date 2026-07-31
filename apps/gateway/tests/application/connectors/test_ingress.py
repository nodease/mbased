import json

import pytest

from apps.gateway.application.connectors.errors import ConnectorTestIngressError
from apps.gateway.application.connectors.ingress import (
    ConnectorTestIngressMetadata,
    ConnectorTestIngressPolicy,
)


def metadata(
    *,
    query_present: bool = False,
    content_type: tuple[bytes, ...] = (b"application/json",),
    content_encoding: tuple[bytes, ...] = (),
    content_length: tuple[bytes, ...] = (),
) -> ConnectorTestIngressMetadata:
    return ConnectorTestIngressMetadata(
        query_present=query_present,
        content_type_headers=content_type,
        content_encoding_headers=content_encoding,
        content_length_headers=content_length,
    )


@pytest.mark.parametrize(
    ("request_metadata", "status_code", "code"),
    [
        (metadata(query_present=True), 400, "connector.test_payload_invalid"),
        (metadata(content_type=()), 415, "connector.test_media_type_not_supported"),
        (
            metadata(content_type=(b"application/json", b"application/json")),
            415,
            "connector.test_media_type_not_supported",
        ),
        (
            metadata(content_type=(b"application/json; charset=utf-8",)),
            415,
            "connector.test_media_type_not_supported",
        ),
        (
            metadata(content_encoding=(b"gzip",)),
            415,
            "connector.test_media_type_not_supported",
        ),
        (
            metadata(content_encoding=(b"identity", b"identity")),
            415,
            "connector.test_media_type_not_supported",
        ),
        (
            metadata(content_length=(b"32769",)),
            413,
            "connector.test_payload_too_large",
        ),
        (
            metadata(content_length=(b"-1",)),
            400,
            "connector.test_payload_invalid",
        ),
        (
            metadata(content_length=(b"1", b"1")),
            400,
            "connector.test_payload_invalid",
        ),
    ],
)
def test_metadata_rejects_ambiguous_or_unbounded_inputs(
    request_metadata: ConnectorTestIngressMetadata,
    status_code: int,
    code: str,
) -> None:
    policy = ConnectorTestIngressPolicy()

    with pytest.raises(ConnectorTestIngressError) as exc_info:
        policy.validate_metadata(request_metadata)

    assert exc_info.value.status_code == status_code
    assert exc_info.value.code == code


@pytest.mark.parametrize(
    "request_metadata",
    [
        metadata(),
        metadata(content_encoding=(b"identity",)),
        metadata(content_length=(b"32768",)),
    ],
)
def test_metadata_accepts_strict_json_identity_contract(
    request_metadata: ConnectorTestIngressMetadata,
) -> None:
    ConnectorTestIngressPolicy().validate_metadata(request_metadata)


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"[]",
        b'"string"',
        b"\xef\xbb\xbf{}",
        b"\xff",
        b'{"value": NaN}',
        b'{"unterminated": true',
    ],
)
def test_parse_json_rejects_non_object_and_noncanonical_json(body: bytes) -> None:
    policy = ConnectorTestIngressPolicy()

    with pytest.raises(ConnectorTestIngressError) as exc_info:
        policy.parse_json(body, policy.start_deadline())

    assert exc_info.value.code == "connector.test_payload_invalid"


def test_parse_json_preserves_object_without_serializing_secrets() -> None:
    policy = ConnectorTestIngressPolicy()
    payload = {"password": "placeholder-secret", "port": 5432}

    assert policy.parse_json(
        json.dumps(payload).encode("utf-8"), policy.start_deadline()
    ) == payload


def test_actual_body_limit_rejects_first_byte_over_boundary() -> None:
    policy = ConnectorTestIngressPolicy(max_body_bytes=10)

    policy.validate_actual_size(10)
    with pytest.raises(ConnectorTestIngressError) as exc_info:
        policy.validate_actual_size(11)

    assert exc_info.value.status_code == 413


def test_deadline_uses_monotonic_budget() -> None:
    now = [10.0]
    policy = ConnectorTestIngressPolicy(
        receive_timeout_seconds=5,
        clock=lambda: now[0],
    )
    deadline = policy.start_deadline()
    now[0] = 15.0

    with pytest.raises(ConnectorTestIngressError) as exc_info:
        policy.remaining_seconds(deadline)

    assert exc_info.value.status_code == 408
