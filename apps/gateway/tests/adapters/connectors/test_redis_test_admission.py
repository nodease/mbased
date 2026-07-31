import asyncio
from uuid import uuid4

import pytest

from apps.gateway.adapters.connectors.redis_test_admission import (
    RedisConnectorTestAdmission,
)
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


class FakeRedis:
    def __init__(self, *responses: object) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[object, ...]] = []

    async def eval(self, *args: object) -> object:
        self.calls.append(args)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class HangingRedis(FakeRedis):
    async def eval(self, *args: object) -> object:
        self.calls.append(args)
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


def command(*, network_address: str = "203.0.113.9") -> ConnectorTestCommand:
    return ConnectorTestCommand(
        organization_id=uuid4(),
        actor_id=uuid4(),
        network_address=network_address,
        host="db.example.com",
        port=5432,
        database="app",
        username="app-user",
        password="placeholder-secret",
    )


def adapter(redis_client: FakeRedis) -> RedisConnectorTestAdmission:
    return RedisConnectorTestAdmission(
        redis_client,
        policy=ConnectorTestPolicy(),
        hmac_key=b"a" * 32,
    )


@pytest.mark.asyncio
async def test_acquire_hashes_identity_scopes_and_release_uses_opaque_lease() -> None:
    redis_client = FakeRedis([b"OK", b"opaque-lease"], 1, 1)
    admission = adapter(redis_client)
    value = command()

    lease = await admission.acquire(value)
    await admission.renew(lease)
    await admission.release(lease)

    acquire_args = redis_client.calls[0]
    serialized_args = "|".join(str(item) for item in acquire_args[1:])
    assert str(value.actor_id) not in serialized_args
    assert str(value.organization_id) not in serialized_args
    assert value.network_address not in serialized_args
    assert "placeholder-secret" not in serialized_args
    keys = acquire_args[2:5]
    assert all("{admission-v1}" in str(key) for key in keys)
    assert redis_client.calls[1][-2:] == ("opaque-lease", 30)
    assert redis_client.calls[2][-1] == "opaque-lease"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "error_type", "retry_after"),
    [
        ([b"RATE", b"12"], ConnectorTestRateLimited, 12),
        ([b"BUSY", b"999"], ConnectorTestBusy, 60),
        ([b"BUSY", b"invalid"], ConnectorTestBusy, 1),
    ],
)
async def test_admission_maps_lua_outcomes_to_bounded_errors(
    response: list[bytes],
    error_type: type[Exception],
    retry_after: int,
) -> None:
    admission = adapter(FakeRedis(response))

    with pytest.raises(error_type) as exc_info:
        await admission.acquire(command())

    assert getattr(exc_info.value, "retry_after") == retry_after


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        RuntimeError("redis unavailable"),
        [b"UNKNOWN", b"1"],
        [b"OK"],
        b"OK",
        [b"\xff", b"1"],
    ],
)
async def test_admission_fails_closed_for_redis_or_protocol_errors(
    response: object,
) -> None:
    admission = adapter(FakeRedis(response))

    with pytest.raises(ConnectorTestAdmissionUnavailable):
        await admission.acquire(command())


@pytest.mark.asyncio
async def test_missing_transport_identity_fails_before_redis() -> None:
    redis_client = FakeRedis([b"OK", b"lease"])

    with pytest.raises(ConnectorTestAdmissionUnavailable):
        await adapter(redis_client).acquire(command(network_address=""))

    assert redis_client.calls == []


def test_short_hmac_key_is_rejected_at_composition_boundary() -> None:
    with pytest.raises(ValueError):
        RedisConnectorTestAdmission(
            FakeRedis(),
            policy=ConnectorTestPolicy(),
            hmac_key=b"short",
        )


@pytest.mark.parametrize(
    "namespace",
    [
        "",
        "-leading",
        "trailing-",
        "UPPERCASE",
        "contains space",
        "contains:{hash-tag}",
        "x" * 65,
    ],
)
def test_unsafe_admission_key_namespace_is_rejected(namespace: str) -> None:
    with pytest.raises(ValueError):
        RedisConnectorTestAdmission(
            FakeRedis(),
            policy=ConnectorTestPolicy(),
            hmac_key=b"a" * 32,
            key_namespace=namespace,
        )


@pytest.mark.asyncio
async def test_explicit_admission_namespace_keeps_cluster_hash_tag() -> None:
    redis_client = FakeRedis([b"OK", b"opaque-lease"])
    admission = RedisConnectorTestAdmission(
        redis_client,
        policy=ConnectorTestPolicy(),
        hmac_key=b"a" * 32,
        key_namespace="connector-test-isolated",
    )

    await admission.acquire(command())

    keys = redis_client.calls[0][2:5]
    assert all(
        str(key).startswith("connector-test-isolated:{admission-v1}:")
        for key in keys
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [0, RuntimeError("redis unavailable")])
async def test_renewal_fails_closed_when_owner_lease_is_missing(response: object) -> None:
    admission = adapter(FakeRedis(response))

    with pytest.raises(ConnectorTestAdmissionUnavailable):
        await admission.renew(AdmissionLease("opaque-lease"))


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["acquire", "renew", "release"])
async def test_every_redis_operation_has_a_fail_closed_deadline(
    operation: str,
) -> None:
    redis_client = HangingRedis()
    admission = RedisConnectorTestAdmission(
        redis_client,
        policy=ConnectorTestPolicy(redis_operation_timeout_seconds=0.01),
        hmac_key=b"a" * 32,
    )
    lease = AdmissionLease("opaque-lease")

    if operation == "acquire":
        call = admission.acquire(command())
    elif operation == "renew":
        call = admission.renew(lease)
    else:
        call = admission.release(lease)

    with pytest.raises(ConnectorTestAdmissionUnavailable):
        await asyncio.wait_for(call, timeout=0.2)

    assert len(redis_client.calls) == 1
