from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from apps.gateway.composition import connectors as connector_composition
from apps.gateway.composition.connectors import (
    connector_test_policy_from_environment,
    require_connector_test_security_ready,
)


def _certificate_pem(
    *,
    is_ca: bool = True,
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
) -> bytes:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "Connector Test CA Fixture")]
    )
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(valid_from or now - timedelta(minutes=1))
        .not_valid_after(valid_until or now + timedelta(minutes=10))
        .add_extension(
            x509.BasicConstraints(ca=is_ca, path_length=0 if is_ca else None),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    return certificate.public_bytes(serialization.Encoding.PEM)


def test_default_policy_matches_documented_limits() -> None:
    policy = connector_test_policy_from_environment({})

    assert policy.rate_window_seconds == 60
    assert policy.user_rate_limit == 5
    assert policy.organization_rate_limit == 30
    assert policy.network_rate_limit == 20
    assert policy.user_concurrency_limit == 1
    assert policy.organization_concurrency_limit == 4
    assert policy.global_concurrency_limit == 16
    assert policy.probe_hard_timeout_seconds == 20.0
    assert policy.redis_operation_timeout_seconds == 1.0
    assert policy.allowed_ports == frozenset({5432})
    assert policy.trusted_local_targets == frozenset()
    assert policy.trusted_local_ca_file is None


def test_policy_parses_deployment_managed_allowed_ports() -> None:
    policy = connector_test_policy_from_environment(
        {"CONNECTOR_TEST_ALLOWED_PORTS": "5432, 15432,55432"}
    )

    assert policy.allowed_ports == frozenset({5432, 15432, 55432})


def test_policy_parses_bounded_redis_operation_timeout() -> None:
    policy = connector_test_policy_from_environment(
        {"CONNECTOR_TEST_REDIS_OPERATION_TIMEOUT_SECONDS": "0.25"}
    )

    assert policy.redis_operation_timeout_seconds == 0.25


@pytest.mark.parametrize(
    "redis_url",
    [
        "http://connector-test-redis:6379/15",
        "redis:///15",
        "redis://connector-test-redis:6379",
        "redis://connector-test-redis:6379/256",
        "redis://connector-test-redis:6379/15?mode=unsafe",
        " redis://connector-test-redis:6379/15",
    ],
)
def test_invalid_connector_test_redis_url_fails_security_readiness(
    redis_url: str,
) -> None:
    with pytest.raises(RuntimeError):
        require_connector_test_security_ready(
            {"CONNECTOR_TEST_REDIS_URL": redis_url}
        )


@pytest.mark.asyncio
async def test_connector_specific_redis_is_owned_and_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeRedis:
        def __init__(self) -> None:
            self.closed = False

        async def aclose(self) -> None:
            self.closed = True

    fake_redis = FakeRedis()
    expected_url = "redis://connector-test-redis:6379/15"
    calls: list[tuple[str, bool]] = []

    def from_url(url: str, *, decode_responses: bool):
        calls.append((url, decode_responses))
        return fake_redis

    monkeypatch.setattr(connector_composition, "_application", None)
    monkeypatch.setenv("CONNECTOR_TEST_REDIS_URL", expected_url)
    monkeypatch.setattr(
        connector_composition,
        "connector_test_policy_from_environment",
        lambda _environ: connector_composition.ConnectorTestPolicy(),
    )
    monkeypatch.setattr(
        connector_composition,
        "_admission_key",
        lambda _environ: b"x" * 32,
    )
    monkeypatch.setattr(connector_composition.aioredis, "from_url", from_url)
    monkeypatch.setattr(
        connector_composition,
        "get_async_redis_client",
        lambda: pytest.fail("shared Redis must not serve connector demo admission"),
    )

    try:
        application = connector_composition.get_connector_test_application()

        assert calls == [(expected_url, False)]
        assert application.owned_redis_client is fake_redis
        assert application.use_case._admission._redis is fake_redis
    finally:
        await connector_composition.shutdown_connector_test_application()

    assert fake_redis.closed is True


def test_explicitly_disabled_local_profile_keeps_public_only_policy() -> None:
    policy = connector_test_policy_from_environment(
        {
            "NODE_ENV": "development",
            "CONNECTOR_TEST_LOCAL_PROFILE_ENABLED": "false",
        }
    )

    assert policy.trusted_local_targets == frozenset()
    assert policy.trusted_local_ca_file is None


def test_development_policy_parses_exact_trusted_local_target(tmp_path) -> None:
    ca_file = tmp_path / "ca.crt"
    ca_file.write_bytes(_certificate_pem())
    environment = {
        "NODE_ENV": "development",
        "CONNECTOR_TEST_LOCAL_PROFILE_ENABLED": "true",
        "CONNECTOR_TEST_ALLOWED_PORTS": "5432,55432",
        "CONNECTOR_TEST_TRUSTED_LOCAL_TARGETS": (
            "LOCALHOST.:55432,connector-test-postgres:5432"
        ),
        "CONNECTOR_TEST_TRUSTED_LOCAL_CA_FILE": str(ca_file),
    }

    policy = connector_test_policy_from_environment(environment)

    assert {(target.host, target.port) for target in policy.trusted_local_targets} == {
        ("localhost", 55432),
        ("connector-test-postgres", 5432),
    }
    assert policy.trusted_local_ca_file == str(ca_file)
    require_connector_test_security_ready(environment)


@pytest.mark.parametrize(
    "local_setting",
    [
        {"CONNECTOR_TEST_LOCAL_PROFILE_ENABLED": "true"},
        {"CONNECTOR_TEST_TRUSTED_LOCAL_TARGETS": "localhost:5432"},
        {"CONNECTOR_TEST_TRUSTED_LOCAL_CA_FILE": "local-ca.crt"},
        {
            "CONNECTOR_TEST_LOCAL_PROFILE_ENABLED": "true",
            "CONNECTOR_TEST_TRUSTED_LOCAL_TARGETS": "localhost:5432",
            "CONNECTOR_TEST_TRUSTED_LOCAL_CA_FILE": "local-ca.crt",
        },
    ],
)
@pytest.mark.parametrize("node_env", ["production", " PRODUCTION "])
def test_production_rejects_every_trusted_local_setting(
    node_env: str,
    local_setting: dict[str, str],
) -> None:
    with pytest.raises(RuntimeError):
        connector_test_policy_from_environment(
            {"NODE_ENV": node_env, **local_setting}
        )


@pytest.mark.parametrize(
    "environment",
    [
        {
            "NODE_ENV": "development",
            "CONNECTOR_TEST_LOCAL_PROFILE_ENABLED": "true",
            "CONNECTOR_TEST_TRUSTED_LOCAL_TARGETS": "localhost:5432",
        },
        {
            "NODE_ENV": "development",
            "CONNECTOR_TEST_LOCAL_PROFILE_ENABLED": "true",
            "CONNECTOR_TEST_TRUSTED_LOCAL_CA_FILE": "local-ca.crt",
        },
        {
            "CONNECTOR_TEST_TRUSTED_LOCAL_TARGETS": "localhost:5432",
            "CONNECTOR_TEST_TRUSTED_LOCAL_CA_FILE": "local-ca.crt",
        },
        {
            "NODE_ENV": "development",
            "CONNECTOR_TEST_LOCAL_PROFILE_ENABLED": "false",
            "CONNECTOR_TEST_TRUSTED_LOCAL_TARGETS": "localhost:5432",
            "CONNECTOR_TEST_TRUSTED_LOCAL_CA_FILE": "local-ca.crt",
        },
        {
            "NODE_ENV": "development",
            "CONNECTOR_TEST_LOCAL_PROFILE_ENABLED": "true",
        },
        {
            "NODE_ENV": "development",
            "CONNECTOR_TEST_LOCAL_PROFILE_ENABLED": "yes",
        },
        {
            "NODE_ENV": "development",
            "CONNECTOR_TEST_LOCAL_PROFILE_ENABLED": "true",
            "CONNECTOR_TEST_TRUSTED_LOCAL_TARGETS": "*:5432",
            "CONNECTOR_TEST_TRUSTED_LOCAL_CA_FILE": "local-ca.crt",
        },
        {
            "NODE_ENV": "development",
            "CONNECTOR_TEST_LOCAL_PROFILE_ENABLED": "true",
            "CONNECTOR_TEST_TRUSTED_LOCAL_TARGETS": "10.0.0.0/8:5432",
            "CONNECTOR_TEST_TRUSTED_LOCAL_CA_FILE": "local-ca.crt",
        },
        {
            "NODE_ENV": "development",
            "CONNECTOR_TEST_LOCAL_PROFILE_ENABLED": "true",
            "CONNECTOR_TEST_TRUSTED_LOCAL_TARGETS": "127.0.0.1:5432",
            "CONNECTOR_TEST_TRUSTED_LOCAL_CA_FILE": "local-ca.crt",
        },
        {
            "NODE_ENV": "development",
            "CONNECTOR_TEST_LOCAL_PROFILE_ENABLED": "true",
            "CONNECTOR_TEST_TRUSTED_LOCAL_TARGETS": "localhost:55432",
            "CONNECTOR_TEST_TRUSTED_LOCAL_CA_FILE": "local-ca.crt",
        },
        {
            "NODE_ENV": "development",
            "CONNECTOR_TEST_LOCAL_PROFILE_ENABLED": "true",
            "CONNECTOR_TEST_TRUSTED_LOCAL_TARGETS": (
                "localhost:5432,LOCALHOST.:5432"
            ),
            "CONNECTOR_TEST_TRUSTED_LOCAL_CA_FILE": "local-ca.crt",
        },
    ],
)
def test_invalid_trusted_local_configuration_fails_startup(
    environment: dict[str, str],
) -> None:
    with pytest.raises((RuntimeError, ValueError)):
        connector_test_policy_from_environment(environment)


def test_missing_trusted_local_ca_file_fails_security_readiness(tmp_path) -> None:
    with pytest.raises(RuntimeError):
        require_connector_test_security_ready(
            {
                "NODE_ENV": "development",
                "CONNECTOR_TEST_LOCAL_PROFILE_ENABLED": "true",
                "CONNECTOR_TEST_TRUSTED_LOCAL_TARGETS": "localhost:5432",
                "CONNECTOR_TEST_TRUSTED_LOCAL_CA_FILE": str(tmp_path / "missing.crt"),
            }
        )


@pytest.mark.parametrize(
    "ca_payload",
    [
        b"",
        b"not-a-certificate",
        _certificate_pem(is_ca=False),
        _certificate_pem(
            valid_from=datetime.now(UTC) - timedelta(minutes=10),
            valid_until=datetime.now(UTC) - timedelta(minutes=1),
        ),
        _certificate_pem() + _certificate_pem(),
        _certificate_pem() + b"-----BEGIN PRIVATE KEY-----\nredacted\n",
        b"x" * (64 * 1024 + 1),
    ],
    ids=[
        "empty",
        "invalid-pem",
        "leaf-certificate",
        "expired-ca",
        "multiple-certificates",
        "private-key-marker",
        "oversized",
    ],
)
def test_invalid_trusted_local_ca_material_fails_security_readiness(
    tmp_path,
    ca_payload: bytes,
) -> None:
    ca_file = tmp_path / "ca.crt"
    ca_file.write_bytes(ca_payload)

    with pytest.raises(RuntimeError):
        require_connector_test_security_ready(
            {
                "NODE_ENV": "development",
                "CONNECTOR_TEST_LOCAL_PROFILE_ENABLED": "true",
                "CONNECTOR_TEST_TRUSTED_LOCAL_TARGETS": "localhost:5432",
                "CONNECTOR_TEST_TRUSTED_LOCAL_CA_FILE": str(ca_file),
            }
        )


def test_future_trusted_local_ca_fails_security_readiness(tmp_path) -> None:
    now = datetime.now(UTC)
    ca_file = tmp_path / "ca.crt"
    ca_file.write_bytes(
        _certificate_pem(
            valid_from=now + timedelta(minutes=10),
            valid_until=now + timedelta(minutes=20),
        )
    )

    with pytest.raises(RuntimeError):
        require_connector_test_security_ready(
            {
                "NODE_ENV": "development",
                "CONNECTOR_TEST_LOCAL_PROFILE_ENABLED": "true",
                "CONNECTOR_TEST_TRUSTED_LOCAL_TARGETS": "localhost:5432",
                "CONNECTOR_TEST_TRUSTED_LOCAL_CA_FILE": str(ca_file),
            }
        )


def test_production_requires_strong_admission_hmac_key() -> None:
    with pytest.raises(RuntimeError):
        require_connector_test_security_ready({"NODE_ENV": "production"})
    with pytest.raises(RuntimeError):
        require_connector_test_security_ready(
            {
                "NODE_ENV": "production",
                "CONNECTOR_TEST_ADMISSION_HMAC_KEY": "short",
            }
        )

    require_connector_test_security_ready(
        {
            "NODE_ENV": "production",
            "CONNECTOR_TEST_ADMISSION_HMAC_KEY": "x" * 32,
            "CONNECTOR_EGRESS_PROXY_URL": "http://egress-proxy:3130",
            "CONNECTOR_EGRESS_PROXY_ALLOWED_HOSTS": "egress-proxy",
            "CONNECTOR_EGRESS_POLICY_REVISION": "connector-egress-v1",
            "CONNECTOR_EGRESS_ALLOWED_PORTS": "22,5432",
        }
    )


def test_directory_or_unreadable_trusted_local_ca_fails_security_readiness(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = {
        "NODE_ENV": "development",
        "CONNECTOR_TEST_LOCAL_PROFILE_ENABLED": "true",
        "CONNECTOR_TEST_TRUSTED_LOCAL_TARGETS": "localhost:5432",
        "CONNECTOR_TEST_TRUSTED_LOCAL_CA_FILE": str(tmp_path),
    }
    with pytest.raises(RuntimeError):
        require_connector_test_security_ready(environment)

    ca_file = tmp_path / "ca.crt"
    ca_file.write_bytes(_certificate_pem())
    environment["CONNECTOR_TEST_TRUSTED_LOCAL_CA_FILE"] = str(ca_file)
    original_read_bytes = type(ca_file).read_bytes

    def unreadable(path):
        if path == ca_file:
            raise OSError("test-only unreadable file")
        return original_read_bytes(path)

    monkeypatch.setattr(type(ca_file), "read_bytes", unreadable)
    with pytest.raises(RuntimeError):
        require_connector_test_security_ready(environment)


@pytest.mark.parametrize(
    "environment",
    [
        {"CONNECTOR_TEST_USER_RATE_LIMIT": "0"},
        {"CONNECTOR_TEST_GLOBAL_CONCURRENCY_LIMIT": "not-an-integer"},
        {
            "CONNECTOR_TEST_USER_CONCURRENCY_LIMIT": "5",
            "CONNECTOR_TEST_ORGANIZATION_CONCURRENCY_LIMIT": "4",
        },
        {
            "CONNECTOR_TEST_RESPONSE_TIMEOUT_SECONDS": "5",
            "CONNECTOR_TEST_CONNECT_TIMEOUT_SECONDS": "5",
        },
        {"CONNECTOR_TEST_RESPONSE_TIMEOUT_SECONDS": "nan"},
        {"CONNECTOR_TEST_USER_RATE_LIMIT": "101"},
        {"CONNECTOR_TEST_ORGANIZATION_RATE_LIMIT": "1001"},
        {"CONNECTOR_TEST_NETWORK_RATE_LIMIT": "1001"},
        {"CONNECTOR_TEST_CONNECT_TIMEOUT_SECONDS": "11"},
        {"CONNECTOR_TEST_STATEMENT_TIMEOUT_SECONDS": "11"},
        {"CONNECTOR_TEST_RESPONSE_TIMEOUT_SECONDS": "31"},
        {"CONNECTOR_TEST_PROBE_HARD_TIMEOUT_SECONDS": "0"},
        {"CONNECTOR_TEST_PROBE_HARD_TIMEOUT_SECONDS": "nan"},
        {"CONNECTOR_TEST_PROBE_HARD_TIMEOUT_SECONDS": "61"},
        {"CONNECTOR_TEST_PROBE_HARD_TIMEOUT_SECONDS": "10"},
        {"CONNECTOR_TEST_REDIS_OPERATION_TIMEOUT_SECONDS": "0"},
        {"CONNECTOR_TEST_REDIS_OPERATION_TIMEOUT_SECONDS": "nan"},
        {"CONNECTOR_TEST_REDIS_OPERATION_TIMEOUT_SECONDS": "6"},
        {
            "CONNECTOR_TEST_REDIS_OPERATION_TIMEOUT_SECONDS": "10",
        },
        {
            "CONNECTOR_TEST_REDIS_OPERATION_TIMEOUT_SECONDS": "1",
            "CONNECTOR_TEST_LEASE_TTL_SECONDS": "3",
        },
        {"CONNECTOR_TEST_LEASE_TTL_SECONDS": "121"},
        {"CONNECTOR_TEST_ALLOWED_PORTS": "5432,"},
        {"CONNECTOR_TEST_ALLOWED_PORTS": "5432,5432"},
        {"CONNECTOR_TEST_ALLOWED_PORTS": "0"},
        {"CONNECTOR_TEST_ALLOWED_PORTS": "65536"},
        {"CONNECTOR_TEST_ALLOWED_PORTS": "not-a-port"},
        {
            "CONNECTOR_TEST_ALLOWED_PORTS": ",".join(
                str(port) for port in range(10000, 10017)
            )
        },
    ],
)
def test_invalid_security_limits_fail_startup(environment: dict[str, str]) -> None:
    with pytest.raises((RuntimeError, ValueError)):
        require_connector_test_security_ready(environment)
