from __future__ import annotations

import secrets
import uuid

import pytest

from apps.memory.adapters.admission import (
    PublicConversationAdmissionPolicy,
    RedisPublicConversationAdmission,
    _ADMIT_SCRIPT,
)
from apps.memory.application.public_lifecycle import (
    PublicConversationAdmissionDisposition,
    PublicDeploymentBinding,
)
from apps.memory.domain.errors import (
    MemoryAdapterUnavailableError,
    PublicConversationRateLimitedError,
)


class _Redis:
    def __init__(self, result=(1, 0), error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls = []

    def eval(self, *args):
        self.calls.append(args)
        if self.error is not None:
            raise self.error
        return self.result


def _binding() -> PublicDeploymentBinding:
    return PublicDeploymentBinding(
        organization_id=uuid.uuid4(),
        app_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        deployment_id=uuid.uuid4(),
        deployment_version=1,
        mapping_version="mapping-v1",
        memory_policy_version="memory-v1",
        memory_contract_version="conversation-memory-v1",
        storage_generation=1,
    )


def _admission(redis: _Redis) -> RedisPublicConversationAdmission:
    return RedisPublicConversationAdmission(
        redis,
        hmac_key=secrets.token_bytes(32),
        policy=PublicConversationAdmissionPolicy(
            deployment_rate_limit=2,
            organization_rate_limit=3,
            network_rate_limit=4,
            grant_rate_limit=5,
            create_window_seconds=600,
            create_deployment_rate_limit=6,
            create_organization_rate_limit=7,
            create_deployment_network_rate_limit=8,
        ),
        request_deduplication_ttl_seconds=86_400,
    )


def test_admission_uses_hashed_dimensions_not_network_or_grant_values_in_redis_key():
    redis = _Redis()
    admission = _admission(redis)
    network = "198.51.100.42"
    grant_id = uuid.uuid4()

    admission.admit(
        operation="conversation.close",
        binding=_binding(),
        grant_id=grant_id,
        network_address=network,
        request_key_hash="a" * 64,
        request_fingerprint="b" * 64,
        request_scope_digest="e" * 64,
        disposition=PublicConversationAdmissionDisposition.LOGICAL_REQUEST,
    )

    call = redis.calls[0]
    assert call[1] == 6
    request_marker = call[2]
    request_retry_counter = call[3]
    keys = call[4:8]
    assert request_retry_counter == f"{request_marker}:retry"
    assert "a" * 64 not in request_marker
    assert all(network not in key for key in keys)
    assert all(str(grant_id) not in key for key in keys)
    assert call[-9:] == (
        60,
        86_400,
        60,
        10,
        2,
        3,
        4,
        5,
        "logical_request",
    )


def test_create_uses_deployment_network_bucket_without_a_global_grant_bucket():
    redis = _Redis()
    admission = _admission(redis)
    first = _binding()
    second = _binding()
    network = "198.51.100.42"

    for binding in (first, second):
        admission.admit(
            operation="conversation.create",
            binding=binding,
            grant_id=None,
            network_address=network,
            request_key_hash="a" * 64,
            request_fingerprint="b" * 64,
            request_scope_digest="e" * 64,
            disposition=PublicConversationAdmissionDisposition.LOGICAL_REQUEST,
        )

    first_call, second_call = redis.calls
    first_keys = set(first_call[4:7])
    second_keys = set(second_call[4:7])
    assert len(first_keys) == len(second_keys) == 3
    assert not first_keys & second_keys
    assert first_call[-8:] == (600, 86_400, 60, 10, 6, 7, 8, "logical_request")
    assert second_call[-8:] == (600, 86_400, 60, 10, 6, 7, 8, "logical_request")


def test_retry_bucket_is_bounded_and_counters_expire_at_the_exact_window_boundary():
    assert "if exact_retry or redis.call('EXISTS', KEYS[1]) == 1 then" in _ADMIT_SCRIPT
    assert "ARGV[#ARGV] == 'exact_retry'" in _ADMIT_SCRIPT
    assert "redis.call('INCR', KEYS[2])" in _ADMIT_SCRIPT
    assert "retry_window_end" in _ADMIT_SCRIPT
    assert "redis.call('EXPIREAT', key, window_end)" in _ADMIT_SCRIPT
    assert "window_end + 60" not in _ADMIT_SCRIPT


def test_exact_retry_uses_only_the_per_request_retry_bucket():
    redis = _Redis()
    admission = _admission(redis)

    admission.admit(
        operation="conversation.delete",
        binding=None,
        grant_id=None,
        network_address="",
        request_scope_digest="e" * 64,
        request_key_hash="a" * 64,
        request_fingerprint="b" * 64,
        disposition=PublicConversationAdmissionDisposition.EXACT_RETRY,
    )

    call = redis.calls[0]
    assert call[1] == 2
    assert call[-5:] == (60, 86_400, 60, 10, "exact_retry")


def test_same_logical_request_uses_one_hmac_marker_for_concurrent_admission():
    redis = _Redis()
    admission = _admission(redis)
    binding = _binding()

    for _ in range(2):
        admission.admit(
            operation="conversation.create",
            binding=binding,
            grant_id=None,
            network_address="198.51.100.42",
            request_key_hash="b" * 64,
            request_fingerprint="c" * 64,
            request_scope_digest="e" * 64,
            disposition=PublicConversationAdmissionDisposition.LOGICAL_REQUEST,
        )

    assert redis.calls[0][2] == redis.calls[1][2]


def test_same_idempotency_key_with_a_different_fingerprint_uses_another_marker():
    redis = _Redis()
    admission = _admission(redis)
    binding = _binding()

    for fingerprint in ("c" * 64, "d" * 64):
        admission.admit(
            operation="conversation.create",
            binding=binding,
            grant_id=None,
            network_address="198.51.100.42",
            request_key_hash="b" * 64,
            request_fingerprint=fingerprint,
            request_scope_digest="e" * 64,
            disposition=PublicConversationAdmissionDisposition.LOGICAL_REQUEST,
        )

    assert redis.calls[0][2] != redis.calls[1][2]


def test_create_admission_preserves_retry_after_within_the_create_window():
    with pytest.raises(PublicConversationRateLimitedError) as error:
        _admission(_Redis(result=(0, b"120"))).admit(
            operation="conversation.create",
            binding=_binding(),
            grant_id=None,
            network_address="203.0.113.7",
            request_key_hash="a" * 64,
            request_fingerprint="b" * 64,
            request_scope_digest="e" * 64,
            disposition=PublicConversationAdmissionDisposition.LOGICAL_REQUEST,
        )

    assert error.value.retry_after_seconds == 120


def test_lifecycle_admission_caps_retry_after_to_its_shorter_window():
    with pytest.raises(PublicConversationRateLimitedError) as error:
        _admission(_Redis(result=(0, b"120"))).admit(
            operation="conversation.close",
            binding=_binding(),
            grant_id=uuid.uuid4(),
            network_address="203.0.113.7",
            request_key_hash="a" * 64,
            request_fingerprint="b" * 64,
            request_scope_digest="e" * 64,
            disposition=PublicConversationAdmissionDisposition.LOGICAL_REQUEST,
        )

    assert error.value.retry_after_seconds == 60


def test_admission_backend_failure_is_fail_closed():
    with pytest.raises(MemoryAdapterUnavailableError):
        _admission(_Redis(error=OSError("unavailable"))).admit(
            operation="conversation.create",
            binding=_binding(),
            grant_id=None,
            network_address="203.0.113.8",
            request_key_hash="a" * 64,
            request_fingerprint="b" * 64,
            request_scope_digest="e" * 64,
            disposition=PublicConversationAdmissionDisposition.LOGICAL_REQUEST,
        )
