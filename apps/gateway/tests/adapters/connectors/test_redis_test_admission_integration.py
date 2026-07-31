import asyncio
import os
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import pytest
import redis.asyncio as redis_asyncio

from apps.gateway.adapters.connectors.redis_test_admission import (
    RedisConnectorTestAdmission,
)
from apps.gateway.application.connectors.errors import (
    ConnectorTestBusy,
    ConnectorTestRateLimited,
)
from apps.gateway.application.connectors.models import (
    AdmissionLease,
    ConnectorTestCommand,
    ConnectorTestPolicy,
)


def _integration_redis_url() -> str:
    raw_url = os.getenv("CONNECTOR_TEST_INTEGRATION_REDIS_URL", "").strip()
    if not raw_url:
        pytest.skip("dedicated connector-test Redis integration URL is not configured")
    parsed = urlsplit(raw_url)
    database = parsed.path.lstrip("/")
    if (
        parsed.scheme not in {"redis", "rediss"}
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or database != "15"
    ):
        raise RuntimeError(
            "connector Redis integration requires a local dedicated database"
        )
    return raw_url


def _command(
    *,
    actor_id: UUID | None = None,
    organization_id: UUID | None = None,
    network_address: str = "198.51.100.10",
) -> ConnectorTestCommand:
    return ConnectorTestCommand(
        organization_id=organization_id or uuid4(),
        actor_id=actor_id or uuid4(),
        network_address=network_address,
        host="db.example.com",
        port=5432,
        database="app",
        username="app-user",
        password="not-a-secret",
    )


def _namespace() -> str:
    return f"connector-test-it-{uuid4().hex}"


async def _delete_namespace(redis_client, namespace: str) -> None:
    keys = [key async for key in redis_client.scan_iter(match=f"{namespace}:*")]
    if keys:
        await redis_client.delete(*keys)


def _adapter(
    redis_client,
    policy: ConnectorTestPolicy,
    *,
    namespace: str,
) -> RedisConnectorTestAdmission:
    return RedisConnectorTestAdmission(
        redis_client,
        policy=policy,
        hmac_key=b"integration-test-only-key-material" * 2,
        key_namespace=namespace,
    )


@pytest.mark.asyncio
async def test_real_redis_enforces_atomic_rate_lease_ownership_and_ttl() -> None:
    redis_client = redis_asyncio.from_url(
        _integration_redis_url(),
        decode_responses=False,
    )
    await redis_client.ping()
    namespaces: list[str] = []
    try:
        rate_namespace = _namespace()
        namespaces.append(rate_namespace)
        rate_policy = ConnectorTestPolicy(
            user_rate_limit=1,
            organization_rate_limit=10,
            network_rate_limit=10,
            user_concurrency_limit=1,
            organization_concurrency_limit=4,
            global_concurrency_limit=8,
        )
        rate_adapter = _adapter(
            redis_client,
            rate_policy,
            namespace=rate_namespace,
        )
        rate_command = _command()
        rate_results = await asyncio.gather(
            rate_adapter.acquire(rate_command),
            rate_adapter.acquire(rate_command),
            return_exceptions=True,
        )
        leases = [result for result in rate_results if isinstance(result, AdmissionLease)]
        rate_errors = [
            result
            for result in rate_results
            if isinstance(result, ConnectorTestRateLimited)
        ]
        assert len(leases) == 1
        assert len(rate_errors) == 1
        await rate_adapter.release(leases[0])

        concurrency_namespace = _namespace()
        namespaces.append(concurrency_namespace)
        concurrency_policy = ConnectorTestPolicy(
            user_rate_limit=100,
            organization_rate_limit=100,
            network_rate_limit=100,
            user_concurrency_limit=1,
            organization_concurrency_limit=2,
            global_concurrency_limit=2,
        )
        adapter_a = _adapter(
            redis_client,
            concurrency_policy,
            namespace=concurrency_namespace,
        )
        adapter_b = _adapter(
            redis_client,
            concurrency_policy,
            namespace=concurrency_namespace,
        )
        lease_a = await adapter_a.acquire(_command(network_address="198.51.100.11"))
        lease_b = await adapter_b.acquire(_command(network_address="198.51.100.12"))
        with pytest.raises(ConnectorTestBusy):
            await adapter_a.acquire(_command(network_address="198.51.100.13"))
        await adapter_a.release(lease_a)
        await adapter_b.release(lease_b)

        ownership_namespace = _namespace()
        namespaces.append(ownership_namespace)
        ownership_adapter = _adapter(
            redis_client,
            concurrency_policy,
            namespace=ownership_namespace,
        )
        ownership_command = _command()
        owner_lease = await ownership_adapter.acquire(ownership_command)
        await ownership_adapter.release(
            AdmissionLease(member=f"{owner_lease.member}-wrong-owner")
        )
        with pytest.raises(ConnectorTestBusy):
            await ownership_adapter.acquire(ownership_command)
        await ownership_adapter.release(owner_lease)
        await ownership_adapter.release(owner_lease)
        replacement_lease = await ownership_adapter.acquire(ownership_command)
        await ownership_adapter.release(replacement_lease)

        ttl_namespace = _namespace()
        namespaces.append(ttl_namespace)
        ttl_policy = ConnectorTestPolicy(
            user_rate_limit=100,
            organization_rate_limit=100,
            network_rate_limit=100,
            connect_timeout_seconds=1,
            statement_timeout_seconds=1,
            response_timeout_seconds=1.5,
            redis_operation_timeout_seconds=0.25,
            lease_ttl_seconds=2,
        )
        ttl_adapter = _adapter(
            redis_client,
            ttl_policy,
            namespace=ttl_namespace,
        )
        ttl_command = _command()
        await ttl_adapter.acquire(ttl_command)
        await asyncio.sleep(2.2)
        recovered_lease = await ttl_adapter.acquire(ttl_command)
        await ttl_adapter.release(recovered_lease)
    finally:
        for namespace in namespaces:
            await _delete_namespace(redis_client, namespace)
        await redis_client.aclose()


@pytest.mark.asyncio
async def test_real_redis_busy_result_does_not_consume_rate_or_touch_other_keys() -> None:
    redis_client = redis_asyncio.from_url(
        _integration_redis_url(),
        decode_responses=False,
    )
    await redis_client.ping()
    namespace = _namespace()
    unrelated_key = f"unrelated-{uuid4().hex}"
    policy = ConnectorTestPolicy(
        user_rate_limit=2,
        organization_rate_limit=10,
        network_rate_limit=10,
        user_concurrency_limit=1,
        organization_concurrency_limit=4,
        global_concurrency_limit=8,
    )
    adapter = _adapter(redis_client, policy, namespace=namespace)
    actor_id = uuid4()
    organization_id = uuid4()
    first_command = _command(
        actor_id=actor_id,
        organization_id=organization_id,
    )
    second_command = _command(
        actor_id=actor_id,
        organization_id=organization_id,
        network_address="198.51.100.11",
    )
    await redis_client.set(unrelated_key, b"preserve")
    try:
        first_lease = await adapter.acquire(first_command)
        with pytest.raises(ConnectorTestBusy):
            await adapter.acquire(second_command)
        await adapter.release(first_lease)

        second_lease = await adapter.acquire(second_command)
        await adapter.release(second_lease)

        keys = [key async for key in redis_client.scan_iter(match=f"{namespace}:*")]
        decoded_keys = [
            key.decode("ascii") if isinstance(key, bytes) else str(key) for key in keys
        ]
        assert decoded_keys
        assert all(str(actor_id) not in key for key in decoded_keys)
        assert all(str(organization_id) not in key for key in decoded_keys)
        assert await redis_client.get(unrelated_key) == b"preserve"
    finally:
        await _delete_namespace(redis_client, namespace)
        await redis_client.delete(unrelated_key)
        await redis_client.aclose()
