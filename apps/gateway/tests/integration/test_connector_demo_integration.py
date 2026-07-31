import os
import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest
import psycopg2
import redis.asyncio as redis_asyncio
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from apps.gateway.adapters.connectors.postgres_probe import StrictPostgresConnectorProbe
from apps.gateway.adapters.connectors.redis_test_admission import (
    RedisConnectorTestAdmission,
)
from apps.gateway.api.v1.endpoints import connectors as connector_endpoint
from apps.gateway.application.connectors.errors import ConnectorProbeFailed
from apps.gateway.application.connectors.models import (
    ConnectorTestCommand,
    ConnectorTestPolicy,
    TrustedLocalConnectorTarget,
)
from apps.gateway.application.connectors.test_connection import (
    TestConnectorConnection as ConnectorConnectionUseCase,
)
from apps.gateway.main import app
from apps.shared.db.models.user import User


class _NoopAudit:
    def __init__(self) -> None:
        self.results: list[bool] = []

    def record(self, _command, result, _duration_bucket: str) -> None:
        self.results.append(result.success)


def _integration_settings() -> tuple[str, str, str]:
    redis_url = os.getenv("CONNECTOR_TEST_INTEGRATION_REDIS_URL", "").strip()
    password = os.getenv("CONNECTOR_TEST_INTEGRATION_POSTGRES_PASSWORD", "")
    ca_file = os.getenv("CONNECTOR_TEST_INTEGRATION_POSTGRES_CA_FILE", "").strip()
    if not redis_url or not password or not ca_file:
        pytest.skip("connector demo integration settings are not configured")

    parsed_redis = urlsplit(redis_url)
    if (
        parsed_redis.scheme not in {"redis", "rediss"}
        or parsed_redis.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed_redis.path.lstrip("/") != "15"
    ):
        raise RuntimeError(
            "connector demo integration requires a local dedicated Redis database"
        )
    if not Path(ca_file).is_file():
        raise RuntimeError("connector demo integration CA file is unavailable")
    return redis_url, password, ca_file


def _namespace() -> str:
    return f"connector-demo-it-{uuid4().hex}"


async def _delete_namespace(redis_client, namespace: str) -> None:
    keys = [key async for key in redis_client.scan_iter(match=f"{namespace}:*")]
    if keys:
        await redis_client.delete(*keys)


def _command(*, host: str, password: str) -> ConnectorTestCommand:
    return ConnectorTestCommand(
        organization_id=uuid4(),
        actor_id=uuid4(),
        network_address="127.0.0.1",
        host=host,
        port=55432,
        database="connector_demo",
        username="connector_demo_user",
        password=password,
    )


def _write_unrelated_ca(path: Path) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "Connector Test Unrelated CA")]
    )
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(minutes=10))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(key, hashes.SHA256())
    )
    path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))


@pytest.mark.asyncio
async def test_authenticated_api_uses_real_redis_and_local_tls_postgres(
    monkeypatch,
    caplog,
) -> None:
    redis_url, password, ca_file = _integration_settings()
    redis_client = redis_asyncio.from_url(redis_url, decode_responses=False)
    await redis_client.ping()
    namespace = _namespace()

    target = TrustedLocalConnectorTarget(host="localhost", port=55432)
    policy = ConnectorTestPolicy(
        allowed_ports=frozenset({55432}),
        trusted_local_targets=frozenset({target}),
        trusted_local_ca_file=ca_file,
    )
    admission = RedisConnectorTestAdmission(
        redis_client,
        policy=policy,
        hmac_key=b"integration-test-only-key-material" * 2,
        key_namespace=namespace,
    )
    probe = StrictPostgresConnectorProbe(policy)
    audit = _NoopAudit()
    use_case = ConnectorConnectionUseCase(admission, probe, audit, policy)
    user = User(
        id=uuid4(),
        email="connector-integration@example.com",
        name="Connector Integration",
        social_provider="local",
    )
    organization_id = uuid4()

    app.dependency_overrides[connector_endpoint.get_db] = lambda: object()
    app.dependency_overrides[connector_endpoint.get_current_user] = lambda: user
    monkeypatch.setattr(
        connector_endpoint,
        "resolve_active_organization_id",
        lambda *_args, **_kwargs: organization_id,
    )
    monkeypatch.setattr(
        connector_endpoint,
        "get_connector_test_application",
        lambda: SimpleNamespace(use_case=use_case),
    )

    try:
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 41000))
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://connector-test.local",
        ) as client:
            response = await client.post(
                "/api/v1/connectors/test",
                headers={"X-Organization-Id": str(organization_id)},
                json={
                    "connection_name": "local-tls-demo",
                    "type": "postgres",
                    "host": "localhost",
                    "port": 55432,
                    "database": "connector_demo",
                    "username": "connector_demo_user",
                    "password": password,
                    "ssh": None,
                },
            )

        assert response.status_code == 200
        assert response.json()["success"] is True
        assert response.json()["reason_code"] is None
        assert audit.results == [True]
        if password in response.text or password in caplog.text:
            raise AssertionError("connector credential appeared in observable output")
    finally:
        app.dependency_overrides = {}
        probe.shutdown()
        await _delete_namespace(redis_client, namespace)
        await redis_client.aclose()


def test_local_postgres_requires_tls_and_uses_a_read_only_non_superuser() -> None:
    _, password, ca_file = _integration_settings()
    connection = psycopg2.connect(
        host="localhost",
        port=55432,
        dbname="connector_demo",
        user="connector_demo_user",
        password=password,
        sslmode="verify-full",
        sslrootcert=ca_file,
        connect_timeout=5,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    (SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()),
                    (SELECT version FROM pg_stat_ssl WHERE pid = pg_backend_pid()),
                    current_setting('transaction_read_only')::boolean,
                    NOT (rolsuper OR rolcreatedb OR rolcreaterole OR
                         rolreplication OR rolbypassrls),
                    rolconnlimit = 4
                FROM pg_roles
                WHERE rolname = current_user
                """
            )
            row = cursor.fetchone()
            assert row is not None
            assert row[0] is True
            assert row[1] in {"TLSv1.2", "TLSv1.3"}
            assert row[2:] == (True, True, True)
            with pytest.raises(psycopg2.Error):
                cursor.execute("CREATE TEMP TABLE connector_probe_write(value integer)")
    finally:
        connection.close()

    with pytest.raises(psycopg2.OperationalError):
        psycopg2.connect(
            host="127.0.0.1",
            port=55432,
            dbname="connector_demo",
            user="connector_demo_user",
            password=password,
            sslmode="disable",
            connect_timeout=3,
        )


def test_local_postgres_rejects_wrong_ca_and_san_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, password, ca_file = _integration_settings()
    wrong_ca_file = tmp_path / "unrelated-ca.crt"
    _write_unrelated_ca(wrong_ca_file)

    wrong_ca_target = TrustedLocalConnectorTarget(host="localhost", port=55432)
    wrong_ca_probe = StrictPostgresConnectorProbe(
        ConnectorTestPolicy(
            allowed_ports=frozenset({55432}),
            trusted_local_targets=frozenset({wrong_ca_target}),
            trusted_local_ca_file=str(wrong_ca_file),
        )
    )
    try:
        with pytest.raises(ConnectorProbeFailed):
            wrong_ca_probe._probe_sync(_command(host="localhost", password=password))
    finally:
        wrong_ca_probe.shutdown()

    mismatch_host = "san-mismatch.local"
    mismatch_target = TrustedLocalConnectorTarget(host=mismatch_host, port=55432)
    original_getaddrinfo = socket.getaddrinfo

    def resolve_mismatch_host(host, port, *args, **kwargs):
        if host == mismatch_host:
            return [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    ("127.0.0.1", port),
                )
            ]
        return original_getaddrinfo(host, port, *args, **kwargs)

    monkeypatch.setattr(
        "apps.shared.services.egress_guard.socket.getaddrinfo",
        resolve_mismatch_host,
    )
    mismatch_probe = StrictPostgresConnectorProbe(
        ConnectorTestPolicy(
            allowed_ports=frozenset({55432}),
            trusted_local_targets=frozenset({mismatch_target}),
            trusted_local_ca_file=ca_file,
        )
    )
    try:
        with pytest.raises(ConnectorProbeFailed):
            mismatch_probe._probe_sync(
                _command(host=mismatch_host, password=password)
            )
    finally:
        mismatch_probe.shutdown()


class _NeverProbe:
    def __init__(self) -> None:
        self.calls = 0
        self.reservations = 0
        self.releases = 0

    def reserve(self):
        self.reservations += 1
        return self

    def release(self) -> None:
        self.releases += 1

    async def probe(self, _command) -> bool:
        self.calls += 1
        pytest.fail("Redis-down boundary must not start the connector probe")


@pytest.mark.asyncio
async def test_authenticated_api_returns_503_before_probe_when_redis_is_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unavailable_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    unavailable_socket.bind(("127.0.0.1", 0))
    unavailable_port = unavailable_socket.getsockname()[1]
    unavailable_socket.close()
    redis_client = redis_asyncio.from_url(
        f"redis://127.0.0.1:{unavailable_port}/15",
        decode_responses=False,
        socket_connect_timeout=0.2,
        socket_timeout=0.2,
    )
    policy = ConnectorTestPolicy()
    admission = RedisConnectorTestAdmission(
        redis_client,
        policy=policy,
        hmac_key=b"integration-test-only-key-material" * 2,
        key_namespace=_namespace(),
    )
    probe = _NeverProbe()
    use_case = ConnectorConnectionUseCase(admission, probe, _NoopAudit(), policy)
    user = User(
        id=uuid4(),
        email="connector-redis-down@example.com",
        name="Connector Redis Down",
        social_provider="local",
    )
    organization_id = uuid4()
    app.dependency_overrides[connector_endpoint.get_db] = lambda: object()
    app.dependency_overrides[connector_endpoint.get_current_user] = lambda: user
    monkeypatch.setattr(
        connector_endpoint,
        "resolve_active_organization_id",
        lambda *_args, **_kwargs: organization_id,
    )
    monkeypatch.setattr(
        connector_endpoint,
        "get_connector_test_application",
        lambda: SimpleNamespace(use_case=use_case),
    )

    try:
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 41001))
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://connector-test.local",
        ) as client:
            response = await client.post(
                "/api/v1/connectors/test",
                headers={"X-Organization-Id": str(organization_id)},
                json={
                    "connection_name": "redis-down-boundary",
                    "type": "postgres",
                    "host": "db.example.com",
                    "port": 5432,
                    "database": "app",
                    "username": "app-user",
                    "password": "placeholder-secret",
                    "ssh": None,
                },
            )

        assert response.status_code == 503
        assert response.json()["error"]["code"] == "connector.admission_unavailable"
        assert probe.reservations == 1
        assert probe.releases == 1
        assert probe.calls == 0
        assert "placeholder-secret" not in response.text
    finally:
        app.dependency_overrides = {}
        await redis_client.aclose()
