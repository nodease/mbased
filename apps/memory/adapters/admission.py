from __future__ import annotations

import hashlib
import hmac
import math
import re
from dataclasses import dataclass
from typing import Any

from apps.memory.application.public_lifecycle import (
    PublicConversationAdmissionDisposition,
    PublicDeploymentBinding,
)
from apps.memory.domain.errors import (
    MemoryAdapterUnavailableError,
    PublicConversationRateLimitedError,
)

_KEY_NAMESPACE_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9_-]{0,62}[a-z0-9])?")

_ADMIT_SCRIPT = r"""
local time = redis.call('TIME')
local now = tonumber(time[1]) + (tonumber(time[2]) / 1000000)
local window_seconds = tonumber(ARGV[1])
local deduplication_ttl_seconds = tonumber(ARGV[2])
local retry_window_seconds = tonumber(ARGV[3])
local retry_limit = tonumber(ARGV[4])
local exact_retry = ARGV[#ARGV] == 'exact_retry'
local window = math.floor(now / window_seconds)
local window_end = (window + 1) * window_seconds
local retry_window = math.floor(now / retry_window_seconds)
local retry_window_end = (retry_window + 1) * retry_window_seconds

if exact_retry or redis.call('EXISTS', KEYS[1]) == 1 then
  local retry_count = tonumber(redis.call('GET', KEYS[2]) or '0')
  if retry_count >= retry_limit then
    return {0, tostring(math.max(1, math.ceil(retry_window_end - now)))}
  end
  redis.call('INCR', KEYS[2])
  redis.call('EXPIREAT', KEYS[2], retry_window_end)
  return {1, '0'}
end

for index = 3, #KEYS do
  local key = KEYS[index]
  local current = tonumber(redis.call('GET', key) or '0')
  if current >= tonumber(ARGV[index + 2]) then
    return {0, tostring(math.max(1, math.ceil(window_end - now)))}
  end
end

for index = 3, #KEYS do
  local key = KEYS[index]
  redis.call('INCR', key)
  redis.call('EXPIREAT', key, window_end)
end
redis.call('SET', KEYS[1], '1', 'EX', deduplication_ttl_seconds)
return {1, '0'}
"""


@dataclass(frozen=True, slots=True)
class PublicConversationAdmissionPolicy:
    window_seconds: int = 60
    deployment_rate_limit: int = 120
    organization_rate_limit: int = 600
    network_rate_limit: int = 60
    grant_rate_limit: int = 20
    create_window_seconds: int = 600
    create_deployment_rate_limit: int = 200
    create_organization_rate_limit: int = 1_000
    create_deployment_network_rate_limit: int = 10
    retry_window_seconds: int = 60
    request_retry_rate_limit: int = 10

    def __post_init__(self) -> None:
        windows = (
            self.window_seconds,
            self.create_window_seconds,
            self.retry_window_seconds,
        )
        rate_limits = (
            self.deployment_rate_limit,
            self.organization_rate_limit,
            self.network_rate_limit,
            self.grant_rate_limit,
            self.create_deployment_rate_limit,
            self.create_organization_rate_limit,
            self.create_deployment_network_rate_limit,
            self.request_retry_rate_limit,
        )
        if any(value < 1 for value in (*windows, *rate_limits)):
            raise ValueError("public conversation admission policy is invalid")
        if max(windows) > 3600 or max(rate_limits) > 100_000:
            raise ValueError("public conversation admission policy is unbounded")


class RedisPublicConversationAdmission:
    """Fail-closed distributed fixed-window admission for public mutations."""

    def __init__(
        self,
        redis_client: Any,
        *,
        hmac_key: bytes,
        policy: PublicConversationAdmissionPolicy,
        key_namespace: str = "nodease-memory-public",
        request_deduplication_ttl_seconds: int = 86_400,
    ) -> None:
        if len(hmac_key) < 32:
            raise ValueError(
                "public conversation admission HMAC key must be at least 32 bytes"
            )
        if not _KEY_NAMESPACE_PATTERN.fullmatch(key_namespace):
            raise ValueError("public conversation admission key namespace is invalid")
        if not 60 <= request_deduplication_ttl_seconds <= 86_400:
            raise ValueError("public conversation request deduplication TTL is invalid")
        self._redis = redis_client
        self._hmac_key = hmac_key
        self._policy = policy
        self._key_prefix = f"{key_namespace}:{{admission-v1}}"
        self._request_deduplication_ttl_seconds = request_deduplication_ttl_seconds

    def admit(
        self,
        *,
        operation: str,
        binding: PublicDeploymentBinding | None,
        grant_id,
        network_address: str,
        request_scope_digest: str,
        request_key_hash: str,
        request_fingerprint: str,
        disposition: PublicConversationAdmissionDisposition,
    ) -> None:
        if not isinstance(disposition, PublicConversationAdmissionDisposition):
            raise MemoryAdapterUnavailableError()
        for value in (
            request_scope_digest,
            request_key_hash,
            request_fingerprint,
        ):
            if not re.fullmatch(r"[0-9a-f]{64}", value):
                raise MemoryAdapterUnavailableError()

        dimensions: list[tuple[str, str, int]]
        if disposition is PublicConversationAdmissionDisposition.EXACT_RETRY:
            window_seconds = self._policy.retry_window_seconds
            dimensions = []
        else:
            if binding is None or not network_address or len(network_address) > 255:
                raise MemoryAdapterUnavailableError()
            if operation == "conversation.create":
                if grant_id is not None:
                    raise MemoryAdapterUnavailableError()
                window_seconds = self._policy.create_window_seconds
                dimensions = [
                    (
                        "deployment",
                        str(binding.deployment_id),
                        self._policy.create_deployment_rate_limit,
                    ),
                    (
                        "organization",
                        str(binding.organization_id),
                        self._policy.create_organization_rate_limit,
                    ),
                    (
                        "deployment_network",
                        f"{binding.deployment_id}:{network_address}",
                        self._policy.create_deployment_network_rate_limit,
                    ),
                ]
            else:
                if grant_id is None:
                    raise MemoryAdapterUnavailableError()
                window_seconds = self._policy.window_seconds
                dimensions = [
                    (
                        "deployment",
                        str(binding.deployment_id),
                        self._policy.deployment_rate_limit,
                    ),
                    (
                        "organization",
                        str(binding.organization_id),
                        self._policy.organization_rate_limit,
                    ),
                    ("network", network_address, self._policy.network_rate_limit),
                    ("grant", str(grant_id), self._policy.grant_rate_limit),
                ]
        keys = tuple(
            f"{self._key_prefix}:{operation}:{dimension}:{self._digest(dimension, value)}"
            for dimension, value, _limit in dimensions
        )
        request_identity = ":".join(
            (
                request_scope_digest,
                request_key_hash,
                request_fingerprint,
            )
        )
        request_marker = (
            f"{self._key_prefix}:{operation}:request:"
            f"{self._digest('request', request_identity)}"
        )
        request_retry_counter = f"{request_marker}:retry"
        limits = tuple(limit for _dimension, _value, limit in dimensions)
        try:
            result = self._redis.eval(
                _ADMIT_SCRIPT,
                len(keys) + 2,
                request_marker,
                request_retry_counter,
                *keys,
                window_seconds,
                self._request_deduplication_ttl_seconds,
                self._policy.retry_window_seconds,
                self._policy.request_retry_rate_limit,
                *limits,
                disposition.value,
            )
        except Exception as exc:
            raise MemoryAdapterUnavailableError() from exc
        allowed, retry_after = self._parse_result(
            result,
            max_retry_after_seconds=max(
                window_seconds,
                self._policy.retry_window_seconds,
            ),
        )
        if not allowed:
            raise PublicConversationRateLimitedError(retry_after)

    def _digest(self, dimension: str, value: str) -> str:
        payload = f"memory-public-admission-v1:{dimension}:{value}".encode("utf-8")
        return hmac.new(self._hmac_key, payload, hashlib.sha256).hexdigest()

    @staticmethod
    def _parse_result(
        result: Any,
        *,
        max_retry_after_seconds: int,
    ) -> tuple[bool, int]:
        if not isinstance(result, (list, tuple)) or len(result) != 2:
            raise MemoryAdapterUnavailableError()
        try:
            allowed = int(result[0])
            raw_retry = result[1]
            if isinstance(raw_retry, bytes):
                raw_retry = raw_retry.decode("ascii", errors="strict")
            retry_after = math.ceil(float(raw_retry))
        except (TypeError, ValueError, UnicodeDecodeError, OverflowError) as exc:
            raise MemoryAdapterUnavailableError() from exc
        if allowed not in {0, 1} or retry_after < 0:
            raise MemoryAdapterUnavailableError()
        return bool(allowed), max(1, min(max_retry_after_seconds, retry_after))


__all__ = [
    "PublicConversationAdmissionPolicy",
    "RedisPublicConversationAdmission",
]
