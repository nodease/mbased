from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import math
import re
import secrets
from typing import Any

from apps.gateway.application.connectors.errors import (
    ConnectorTestAdmissionUnavailable,
    ConnectorTestBusy,
    ConnectorTestRateLimited,
)
from apps.gateway.application.connectors.models import (
    AdmissionLease,
    ConnectorTestCommand,
    ConnectorTestPolicy,
)

logger = logging.getLogger(__name__)

_DEFAULT_KEY_NAMESPACE = "connector-test"
_KEY_NAMESPACE_PATTERN = re.compile(
    r"[a-z0-9](?:[a-z0-9_-]{0,62}[a-z0-9])?"
)

_ACQUIRE_SCRIPT = r"""
local time = redis.call('TIME')
local now = tonumber(time[1]) + (tonumber(time[2]) / 1000000)
local now_second = math.floor(now)
local window_seconds = tonumber(ARGV[1])
local window_id = math.floor(now_second / window_seconds)
local window_end = (window_id + 1) * window_seconds

local expired_fields = redis.call(
    'ZRANGEBYSCORE', KEYS[2], '-inf', now_second, 'LIMIT', 0, 1000
)
if #expired_fields > 0 then
    redis.call('HDEL', KEYS[1], unpack(expired_fields))
    redis.call('ZREM', KEYS[2], unpack(expired_fields))
end
redis.call('ZREMRANGEBYSCORE', KEYS[3], '-inf', now)

local rate_scopes = {
    {'user', ARGV[2], tonumber(ARGV[5])},
    {'organization', ARGV[3], tonumber(ARGV[6])},
    {'network', ARGV[4], tonumber(ARGV[7])}
}
local rate_fields = {}
for index, scope in ipairs(rate_scopes) do
    local field = scope[1] .. ':' .. scope[2] .. ':' .. window_id
    rate_fields[index] = field
    local current = tonumber(redis.call('HGET', KEYS[1], field) or '0')
    if current >= scope[3] then
        return {'RATE', tostring(math.max(1, window_end - now_second))}
    end
end

local user_prefix = ARGV[2] .. ':'
local organization_marker = ':' .. ARGV[3] .. ':'
local user_count = 0
local organization_count = 0
local active_members = redis.call('ZRANGE', KEYS[3], 0, -1, 'WITHSCORES')
local earliest_expiry = nil
for index = 1, #active_members, 2 do
    local member = active_members[index]
    local score = tonumber(active_members[index + 1])
    if string.sub(member, 1, string.len(user_prefix)) == user_prefix then
        user_count = user_count + 1
    end
    if string.find(member, organization_marker, 1, true) then
        organization_count = organization_count + 1
    end
    if earliest_expiry == nil or score < earliest_expiry then
        earliest_expiry = score
    end
end

local global_count = #active_members / 2
if user_count >= tonumber(ARGV[8])
    or organization_count >= tonumber(ARGV[9])
    or global_count >= tonumber(ARGV[10]) then
    local retry_after = 1
    if earliest_expiry ~= nil then
        retry_after = math.max(1, math.ceil(earliest_expiry - now))
    end
    return {'BUSY', tostring(retry_after)}
end

for _, field in ipairs(rate_fields) do
    redis.call('HINCRBY', KEYS[1], field, 1)
    redis.call('ZADD', KEYS[2], window_end + 120, field)
end

local member = ARGV[2] .. ':' .. ARGV[3] .. ':' .. ARGV[11]
redis.call('ZADD', KEYS[3], now + tonumber(ARGV[12]), member)
redis.call('EXPIRE', KEYS[1], 600)
redis.call('EXPIRE', KEYS[2], 600)
redis.call('EXPIRE', KEYS[3], 600)
return {'OK', member}
"""

_RELEASE_SCRIPT = r"""
return redis.call('ZREM', KEYS[1], ARGV[1])
"""

_RENEW_SCRIPT = r"""
local time = redis.call('TIME')
local now = tonumber(time[1]) + (tonumber(time[2]) / 1000000)
if redis.call('ZSCORE', KEYS[1], ARGV[1]) == false then
    return 0
end
redis.call('ZADD', KEYS[1], 'XX', now + tonumber(ARGV[2]), ARGV[1])
redis.call('EXPIRE', KEYS[1], 600)
return 1
"""


class RedisConnectorTestAdmission:
    def __init__(
        self,
        redis_client: Any,
        *,
        policy: ConnectorTestPolicy,
        hmac_key: bytes,
        key_namespace: str = _DEFAULT_KEY_NAMESPACE,
    ) -> None:
        if len(hmac_key) < 32:
            raise ValueError("connector test admission HMAC key must be at least 32 bytes")
        if not isinstance(key_namespace, str) or not _KEY_NAMESPACE_PATTERN.fullmatch(
            key_namespace
        ):
            raise ValueError("connector test admission key namespace is invalid")
        self._redis = redis_client
        self._policy = policy
        self._hmac_key = hmac_key
        key_tag = f"{key_namespace}:{{admission-v1}}"
        self._rate_key = f"{key_tag}:rate"
        self._rate_expiry_key = f"{key_tag}:rate-expiry"
        self._lease_key = f"{key_tag}:leases"

    async def acquire(self, command: ConnectorTestCommand) -> AdmissionLease:
        if not command.network_address:
            raise ConnectorTestAdmissionUnavailable()

        user_scope = self._digest("user", str(command.actor_id))
        organization_scope = self._digest("organization", str(command.organization_id))
        network_scope = self._digest("network", command.network_address)
        token = secrets.token_hex(16)
        response = await self._eval(
            "acquire",
            _ACQUIRE_SCRIPT,
            3,
            self._rate_key,
            self._rate_expiry_key,
            self._lease_key,
            self._policy.rate_window_seconds,
            user_scope,
            organization_scope,
            network_scope,
            self._policy.user_rate_limit,
            self._policy.organization_rate_limit,
            self._policy.network_rate_limit,
            self._policy.user_concurrency_limit,
            self._policy.organization_concurrency_limit,
            self._policy.global_concurrency_limit,
            token,
            self._policy.lease_ttl_seconds,
        )

        status, value = self._decode_response(response)
        if status == "OK":
            return AdmissionLease(member=value)
        retry_after = self._bounded_retry_after(value)
        if status == "RATE":
            raise ConnectorTestRateLimited(retry_after)
        if status == "BUSY":
            raise ConnectorTestBusy(retry_after)
        raise ConnectorTestAdmissionUnavailable()

    async def release(self, lease: AdmissionLease) -> None:
        await self._eval(
            "release",
            _RELEASE_SCRIPT,
            1,
            self._lease_key,
            lease.member,
        )

    async def renew(self, lease: AdmissionLease) -> None:
        renewed = await self._eval(
            "renew",
            _RENEW_SCRIPT,
            1,
            self._lease_key,
            lease.member,
            self._policy.lease_ttl_seconds,
        )
        if renewed != 1:
            raise ConnectorTestAdmissionUnavailable()

    async def _eval(self, operation: str, *args: object) -> object:
        try:
            return await asyncio.wait_for(
                self._redis.eval(*args),
                timeout=self._policy.redis_operation_timeout_seconds,
            )
        except Exception as exc:
            logger.error(
                "Connector test admission operation failed: operation=%s error_type=%s",
                operation,
                type(exc).__name__,
            )
            raise ConnectorTestAdmissionUnavailable() from None

    def _digest(self, scope: str, value: str) -> str:
        payload = f"connector-test-admission-v1:{scope}:{value}".encode("utf-8")
        return hmac.new(self._hmac_key, payload, hashlib.sha256).hexdigest()

    @staticmethod
    def _decode_response(response: Any) -> tuple[str, str]:
        if not isinstance(response, (list, tuple)) or len(response) != 2:
            raise ConnectorTestAdmissionUnavailable()
        decoded: list[str] = []
        try:
            for value in response:
                if isinstance(value, bytes):
                    decoded.append(value.decode("ascii", errors="strict"))
                elif isinstance(value, str):
                    decoded.append(value)
                else:
                    decoded.append(str(value))
        except UnicodeDecodeError:
            raise ConnectorTestAdmissionUnavailable() from None
        return decoded[0], decoded[1]

    @staticmethod
    def _bounded_retry_after(value: str) -> int:
        try:
            retry_after = math.ceil(float(value))
        except (TypeError, ValueError, OverflowError):
            return 1
        return max(1, min(60, retry_after))


__all__ = ["RedisConnectorTestAdmission"]
