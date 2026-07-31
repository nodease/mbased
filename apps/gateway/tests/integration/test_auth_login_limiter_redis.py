from __future__ import annotations

import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
import redis

from apps.gateway.adapters.authentication.redis_login_limiter import (
    RedisLoginLimiter,
)
from apps.gateway.application.authentication.models import LoginLimitDimension


@pytest.fixture
def redis_client():
    client = redis.Redis.from_url(
        os.getenv("AUTH_LOGIN_TEST_REDIS_URL", "redis://127.0.0.1:6379/15"),
        socket_connect_timeout=0.5,
        socket_timeout=1,
    )
    try:
        client.ping()
    except redis.RedisError:
        pytest.skip("Redis integration test endpoint is unavailable")
    return client


@pytest.fixture
def limiter_factory(redis_client):
    prefixes = []

    def build(*, keyring=None, limits=None):
        prefix = f"test:auth-login:{uuid.uuid4().hex}"
        prefixes.append(prefix)
        return RedisLoginLimiter(
            redis_client,
            keyring=keyring or {"v1": b"k" * 32},
            policy_version="v1",
            key_prefix=prefix,
            limits=limits,
        )

    yield build

    for prefix in prefixes:
        keys = tuple(redis_client.scan_iter(match=f"{prefix}:*", count=100))
        if keys:
            redis_client.delete(*keys)


def test_concurrent_pair_burst_never_exceeds_capacity(limiter_factory):
    limiter = limiter_factory()

    def attempt(_):
        return limiter.admit(
            account_identity="member@example.com",
            network_identity="198.51.100.0/24",
        ).allowed

    with ThreadPoolExecutor(max_workers=6) as executor:
        outcomes = list(executor.map(attempt, range(6)))

    assert outcomes.count(True) == 5
    assert outcomes.count(False) == 1


def test_independent_limiter_instances_share_the_same_redis_budget(
    limiter_factory,
    redis_client,
):
    first = limiter_factory()
    second = RedisLoginLimiter(
        redis_client,
        keyring={"v1": b"k" * 32},
        policy_version="v1",
        key_prefix=first._key_prefix,
    )

    outcomes = [
        limiter.admit(
            account_identity="member@example.com",
            network_identity="198.51.100.0/24",
        ).allowed
        for limiter in (first, second, first, second, first, second)
    ]

    assert outcomes == [True, True, True, True, True, False]


def test_account_limit_is_shared_across_networks(limiter_factory):
    limiter = limiter_factory()

    outcomes = [
        limiter.admit(
            account_identity="member@example.com",
            network_identity=f"198.51.{index}.0/24",
        )
        for index in range(21)
    ]

    assert sum(item.allowed for item in outcomes) == 20
    assert outcomes[-1].limited_dimensions == (LoginLimitDimension.ACCOUNT,)


def test_network_limit_is_shared_across_accounts(limiter_factory):
    limiter = limiter_factory()

    outcomes = [
        limiter.admit(
            account_identity=f"member-{index}@example.com",
            network_identity="198.51.100.0/24",
        )
        for index in range(101)
    ]

    assert sum(item.allowed for item in outcomes) == 100
    assert outcomes[-1].limited_dimensions == (LoginLimitDimension.NETWORK,)


def test_blocked_request_does_not_create_or_consume_other_dimension_state(
    limiter_factory,
    redis_client,
):
    limiter = limiter_factory()
    for index in range(20):
        assert limiter.admit(
            account_identity="member@example.com",
            network_identity=f"198.51.{index}.0/24",
        ).allowed

    blocked_network = "203.0.113.0/24"
    result = limiter.admit(
        account_identity="member@example.com",
        network_identity=blocked_network,
    )
    keys = limiter._keys("member@example.com", blocked_network)

    assert result.allowed is False
    assert redis_client.exists(keys[1]) == 0
    assert redis_client.exists(keys[2]) == 0


def test_rotation_overlap_preserves_old_limit_without_writing_new_state(
    limiter_factory,
    redis_client,
):
    old = limiter_factory(keyring={"v1": b"o" * 32})
    for _ in range(5):
        assert old.admit(
            account_identity="member@example.com",
            network_identity="198.51.100.0/24",
        ).allowed

    rotating = RedisLoginLimiter(
        redis_client,
        keyring={"v2": b"n" * 32, "v1": b"o" * 32},
        policy_version="v1",
        key_prefix=old._key_prefix,
    )
    result = rotating.admit(
        account_identity="member@example.com",
        network_identity="198.51.100.0/24",
    )
    new_version_keys = rotating._keys(
        "member@example.com", "198.51.100.0/24"
    )[:3]

    assert result.allowed is False
    assert result.limited_dimensions == (LoginLimitDimension.ACCOUNT_NETWORK,)
    assert all(redis_client.exists(key) == 0 for key in new_version_keys)


def test_success_reset_keeps_network_bucket_only(limiter_factory, redis_client):
    limiter = limiter_factory(keyring={"v2": b"n" * 32, "v1": b"o" * 32})
    account = "member@example.com"
    network = "198.51.100.0/24"
    assert limiter.admit(
        account_identity=account,
        network_identity=network,
    ).allowed

    limiter.reset_after_success(
        account_identity=account,
        network_identity=network,
    )
    keys = limiter._keys(account, network)

    assert all(redis_client.exists(keys[index]) == 0 for index in (0, 2, 3, 5))
    assert all(redis_client.exists(keys[index]) == 1 for index in (1, 4))


def test_refill_uses_redis_time_and_state_expires_after_full_refill(
    limiter_factory,
    redis_client,
):
    limits = {
        LoginLimitDimension.ACCOUNT: (1, 150),
        LoginLimitDimension.NETWORK: (10, 150),
        LoginLimitDimension.ACCOUNT_NETWORK: (1, 150),
    }
    limiter = limiter_factory(limits=limits)
    account = "member@example.com"
    network = "198.51.100.0/24"

    assert limiter.admit(
        account_identity=account,
        network_identity=network,
    ).allowed
    blocked = limiter.admit(
        account_identity=account,
        network_identity=network,
    )
    assert blocked.allowed is False
    assert blocked.retry_after_seconds == 1

    time.sleep(0.2)
    assert limiter.admit(
        account_identity=account,
        network_identity=network,
    ).allowed

    time.sleep(0.2)
    assert all(redis_client.exists(key) == 0 for key in limiter._keys(account, network))
