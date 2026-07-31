from __future__ import annotations

from apps.gateway.adapters.authentication.redis_login_limiter import (
    RedisLoginLimiter,
)
from apps.gateway.application.authentication.models import LoginLimitDimension


class _Redis:
    def __init__(self, result):
        self.result = result
        self.calls: list[tuple] = []
        self.deleted: list[tuple[str, ...]] = []

    def eval(self, script, key_count, *args):
        self.calls.append((script, key_count, args))
        return self.result

    def delete(self, *keys):
        self.deleted.append(keys)
        return len(keys)


def _limiter(redis_client, *, keyring=None):
    return RedisLoginLimiter(
        redis_client,
        keyring=keyring or {"v2": b"n" * 32, "v1": b"o" * 32},
        policy_version="policy-v1",
        key_prefix="test:auth-login",
    )


def test_admission_uses_hmac_keys_for_all_versions_and_dimensions():
    client = _Redis([1, 0, 0])

    admission = _limiter(client).admit(
        account_identity="raw-account@example.com",
        network_identity="192.0.2.0/24",
    )

    assert admission.allowed is True
    _, key_count, args = client.calls[0]
    keys = args[:key_count]
    assert key_count == 6
    assert len(set(keys)) == 6
    assert all("raw-account" not in key for key in keys)
    assert all("192.0.2.0" not in key for key in keys)
    assert all("{login-admission}" in key for key in keys)


def test_blocked_result_returns_bounded_retry_and_allowlisted_dimensions():
    client = _Redis([0, 999_999, 5])

    admission = _limiter(client, keyring={"v1": b"k" * 32}).admit(
        account_identity="member@example.com",
        network_identity="198.51.100.0/24",
    )

    assert admission.allowed is False
    assert admission.retry_after_seconds == 300
    assert admission.limited_dimensions == (
        LoginLimitDimension.ACCOUNT,
        LoginLimitDimension.ACCOUNT_NETWORK,
    )


def test_success_reset_deletes_only_account_and_pair_keys_for_all_versions():
    client = _Redis([1, 0, 0])
    limiter = _limiter(client)

    limiter.reset_after_success(
        account_identity="member@example.com",
        network_identity="198.51.100.0/24",
    )

    assert len(client.deleted) == 1
    deleted = client.deleted[0]
    assert len(deleted) == 4
    assert all(":network:" not in key for key in deleted)


def test_malformed_redis_result_fails_closed():
    client = _Redis([1])
    limiter = _limiter(client)

    try:
        limiter.admit(
            account_identity="member@example.com",
            network_identity="198.51.100.0/24",
        )
    except Exception as exc:
        assert type(exc).__name__ == "LimiterBackendError"
    else:
        raise AssertionError("malformed Redis result must fail closed")
