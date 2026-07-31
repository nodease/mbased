from __future__ import annotations

import asyncio
import os
import stat
import sys
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import redis.asyncio as redis_asyncio

from apps.gateway.adapters.connectors.postgres_probe import StrictPostgresConnectorProbe
from apps.gateway.adapters.connectors.redis_test_admission import (
    RedisConnectorTestAdmission,
)
from apps.gateway.application.connectors.models import (
    ConnectorTestCommand,
    ConnectorTestPolicy,
    TrustedLocalConnectorTarget,
)
from apps.gateway.application.connectors.test_connection import TestConnectorConnection


class _NoopAudit:
    def record(self, _command, _result, _duration_bucket: str) -> None:
        return None


def _settings() -> tuple[str, str, str]:
    redis_url = os.environ.get("CONNECTOR_DEMO_REDIS_URL", "").strip()
    ca_file = os.environ.get("CONNECTOR_DEMO_CA_FILE", "").strip()
    password_file = os.environ.get(
        "CONNECTOR_DEMO_POSTGRES_PASSWORD_FILE",
        "",
    ).strip()
    parsed = urlsplit(redis_url)
    if (
        parsed.scheme != "redis"
        or parsed.hostname != "connector-test-redis"
        or parsed.port != 6379
        or parsed.path != "/15"
        or not Path(ca_file).is_file()
        or not Path(password_file).is_file()
    ):
        raise RuntimeError("connector demo runtime configuration is invalid")
    password_path = Path(password_file)
    private_mode = stat.S_IMODE(password_path.stat().st_mode)
    credential_entries = list(password_path.parent.iterdir())
    if (
        private_mode != 0o600
        or credential_entries != [password_path]
        or Path("/tls/server").exists()
        or Path("/tls/admin-credentials").exists()
    ):
        raise RuntimeError("connector demo runtime credential boundary is invalid")
    password = password_path.read_text(encoding="utf-8").strip()
    if len(password) != 64 or any(
        character not in "0123456789abcdef" for character in password
    ):
        raise RuntimeError("connector demo runtime configuration is invalid")
    return redis_url, ca_file, password


async def _verify() -> None:
    redis_url, ca_file, password = _settings()
    redis_client = redis_asyncio.from_url(redis_url, decode_responses=False)
    await redis_client.ping()
    key_namespace = f"connector-demo-runtime-{uuid4().hex}"

    target = TrustedLocalConnectorTarget(
        host="connector-test-postgres",
        port=5432,
    )
    policy = ConnectorTestPolicy(
        trusted_local_targets=frozenset({target}),
        trusted_local_ca_file=ca_file,
    )
    admission = RedisConnectorTestAdmission(
        redis_client,
        policy=policy,
        hmac_key=b"docker-runtime-test-only-key-material" * 2,
        key_namespace=key_namespace,
    )
    probe = StrictPostgresConnectorProbe(policy)
    use_case = TestConnectorConnection(admission, probe, _NoopAudit(), policy)
    command = ConnectorTestCommand(
        organization_id=uuid4(),
        actor_id=uuid4(),
        network_address="127.0.0.1",
        host="connector-test-postgres",
        port=5432,
        database="connector_demo",
        username="connector_demo_user",
        password=password,
    )

    try:
        result = await use_case.execute(command)
        if not result.success:
            raise RuntimeError("connector demo runtime probe failed")
    finally:
        probe.shutdown()
        keys = [
            key
            async for key in redis_client.scan_iter(match=f"{key_namespace}:*")
        ]
        if keys:
            await redis_client.delete(*keys)
        await redis_client.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(_verify())
    except Exception as exc:
        print(
            f"connector-demo-runtime=failed:{type(exc).__name__}",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    print("connector-demo-runtime=ok")
