from __future__ import annotations

import hashlib
import hmac
import math
from collections.abc import Mapping

from apps.gateway.application.authentication.errors import LimiterBackendError
from apps.gateway.application.authentication.models import (
    LoginAdmission,
    LoginLimitDimension,
)


_TOKEN_BUCKET_SCRIPT = r"""
local time = redis.call('TIME')
local now_ms = tonumber(time[1]) * 1000 + math.floor(tonumber(time[2]) / 1000)
local states = {}
local blocked_mask = 0
local retry_ms = 0

for index, key in ipairs(KEYS) do
  local capacity = tonumber(ARGV[(index - 1) * 2 + 1])
  local refill_ms = tonumber(ARGV[(index - 1) * 2 + 2])
  local state = redis.call('HMGET', key, 'tokens', 'updated_ms')
  local tokens = tonumber(state[1]) or capacity
  local updated_ms = tonumber(state[2]) or now_ms
  if now_ms > updated_ms then
    tokens = math.min(capacity, tokens + ((now_ms - updated_ms) * capacity / refill_ms))
  end
  states[index] = {tokens, capacity, refill_ms}
  if tokens < 1 then
    local dimension = ((index - 1) % 3) + 1
    local dimension_bit = dimension == 1 and 1 or (dimension == 2 and 2 or 4)
    blocked_mask = bit.bor(blocked_mask, dimension_bit)
    retry_ms = math.max(retry_ms, math.ceil((1 - tokens) * refill_ms / capacity))
  end
end

if blocked_mask ~= 0 then
  return {0, retry_ms, blocked_mask}
end

for index, key in ipairs(KEYS) do
  local state = states[index]
  redis.call('HSET', key, 'tokens', state[1] - 1, 'updated_ms', now_ms)
  redis.call('PEXPIRE', key, state[3])
end
return {1, 0, 0}
"""


class RedisLoginLimiter:
    _DIMENSIONS = (
        LoginLimitDimension.ACCOUNT,
        LoginLimitDimension.NETWORK,
        LoginLimitDimension.ACCOUNT_NETWORK,
    )
    _LIMITS = {
        LoginLimitDimension.ACCOUNT: (20, 900_000),
        LoginLimitDimension.NETWORK: (100, 300_000),
        LoginLimitDimension.ACCOUNT_NETWORK: (5, 300_000),
    }

    def __init__(
        self,
        redis_client,
        *,
        keyring: Mapping[str, bytes],
        policy_version: str,
        key_prefix: str = "nodease:auth-login",
        limits: Mapping[LoginLimitDimension, tuple[int, int]] | None = None,
    ) -> None:
        if not keyring or len(keyring) > 2:
            raise ValueError("login limiter keyring must have one or two versions")
        self._redis = redis_client
        self._keyring = tuple(keyring.items())
        self._policy_version = policy_version
        self._key_prefix = key_prefix
        self._limits = dict(limits or self._LIMITS)
        if set(self._limits) != set(self._DIMENSIONS) or any(
            capacity <= 0 or refill_ms <= 0
            for capacity, refill_ms in self._limits.values()
        ):
            raise ValueError("login limiter limits are invalid")

    def admit(
        self,
        *,
        account_identity: str,
        network_identity: str,
    ) -> LoginAdmission:
        keys = self._keys(account_identity, network_identity)
        arguments: list[int] = []
        for _ in self._keyring:
            for dimension in self._DIMENSIONS:
                arguments.extend(self._limits[dimension])
        try:
            result = self._redis.eval(
                _TOKEN_BUCKET_SCRIPT,
                len(keys),
                *keys,
                *arguments,
            )
            return self._parse_result(result)
        except LimiterBackendError:
            raise
        except Exception as exc:
            raise LimiterBackendError(operation="admission") from exc

    def reset_after_success(
        self,
        *,
        account_identity: str,
        network_identity: str,
    ) -> None:
        keys = self._keys(account_identity, network_identity)
        reset_keys = tuple(
            key
            for index, key in enumerate(keys)
            if self._DIMENSIONS[index % len(self._DIMENSIONS)]
            in {
                LoginLimitDimension.ACCOUNT,
                LoginLimitDimension.ACCOUNT_NETWORK,
            }
        )
        try:
            self._redis.delete(*reset_keys)
        except Exception as exc:
            raise LimiterBackendError(operation="reset") from exc

    def _keys(self, account_identity: str, network_identity: str) -> tuple[str, ...]:
        keys: list[str] = []
        for version, secret in self._keyring:
            identities = {
                LoginLimitDimension.ACCOUNT: account_identity,
                LoginLimitDimension.NETWORK: network_identity,
                LoginLimitDimension.ACCOUNT_NETWORK: (
                    f"{len(account_identity)}:{account_identity}{network_identity}"
                ),
            }
            for dimension in self._DIMENSIONS:
                digest = hmac.new(
                    secret,
                    (
                        f"nodease:auth-login:{self._policy_version}:"
                        f"{dimension.value}\0{identities[dimension]}"
                    ).encode("utf-8"),
                    hashlib.sha256,
                ).hexdigest()
                keys.append(
                    f"{self._key_prefix}:{{login-admission}}:{self._policy_version}:"
                    f"{version}:{dimension.value}:{digest}"
                )
        return tuple(keys)

    @staticmethod
    def _parse_result(result) -> LoginAdmission:
        if not isinstance(result, (list, tuple)) or len(result) != 3:
            raise LimiterBackendError(operation="admission")
        try:
            allowed = int(result[0])
            retry_ms = int(result[1])
            mask = int(result[2])
        except (TypeError, ValueError) as exc:
            raise LimiterBackendError(operation="admission") from exc
        if allowed not in {0, 1} or retry_ms < 0 or mask < 0 or mask > 7:
            raise LimiterBackendError(operation="admission")
        if allowed == 1:
            if retry_ms != 0 or mask != 0:
                raise LimiterBackendError(operation="admission")
            return LoginAdmission(allowed=True)
        if mask == 0:
            raise LimiterBackendError(operation="admission")
        dimensions = tuple(
            dimension
            for bit, dimension in (
                (1, LoginLimitDimension.ACCOUNT),
                (2, LoginLimitDimension.NETWORK),
                (4, LoginLimitDimension.ACCOUNT_NETWORK),
            )
            if mask & bit
        )
        return LoginAdmission(
            allowed=False,
            retry_after_seconds=max(1, min(300, math.ceil(retry_ms / 1000))),
            limited_dimensions=dimensions,
        )
