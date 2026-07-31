from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import redis.asyncio as aioredis
from cryptography import x509
from cryptography.x509 import BasicConstraints

from apps.gateway.adapters.audit.connector_test import ConnectorTestAuditRecorder
from apps.gateway.adapters.connectors.postgres_probe import StrictPostgresConnectorProbe
from apps.gateway.adapters.connectors.redis_test_admission import (
    RedisConnectorTestAdmission,
)
from apps.gateway.application.connectors.models import (
    ConnectorTestPolicy,
    TrustedLocalConnectorTarget,
)
from apps.gateway.application.connectors.test_connection import TestConnectorConnection
from apps.shared.pubsub import get_async_redis_client
from apps.shared.services.egress_guard import canonicalize_network_host
from apps.shared.services.connector_tcp_transport import (
    connector_tcp_proxy_dialer_from_environment,
)

_LOCAL_ADMISSION_KEY = b"connector-test-local-development-key-v1"
_MAX_TRUSTED_LOCAL_CA_BYTES = 64 * 1024
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ConnectorTestApplication:
    use_case: TestConnectorConnection
    probe: StrictPostgresConnectorProbe
    owned_redis_client: Any | None = field(default=None, repr=False)


_application: ConnectorTestApplication | None = None


def connector_test_policy_from_environment(
    environ: Mapping[str, str],
) -> ConnectorTestPolicy:
    allowed_ports = _ports(environ, "CONNECTOR_TEST_ALLOWED_PORTS", {5432})
    trusted_local_targets, trusted_local_ca_file = _trusted_local_configuration(
        environ,
        allowed_ports=allowed_ports,
    )
    return ConnectorTestPolicy(
        allowed_ports=allowed_ports,
        trusted_local_targets=trusted_local_targets,
        trusted_local_ca_file=trusted_local_ca_file,
        rate_window_seconds=_integer(environ, "CONNECTOR_TEST_RATE_WINDOW_SECONDS", 60),
        user_rate_limit=_integer(environ, "CONNECTOR_TEST_USER_RATE_LIMIT", 5),
        organization_rate_limit=_integer(
            environ, "CONNECTOR_TEST_ORGANIZATION_RATE_LIMIT", 30
        ),
        network_rate_limit=_integer(environ, "CONNECTOR_TEST_NETWORK_RATE_LIMIT", 20),
        user_concurrency_limit=_integer(
            environ, "CONNECTOR_TEST_USER_CONCURRENCY_LIMIT", 1
        ),
        organization_concurrency_limit=_integer(
            environ, "CONNECTOR_TEST_ORGANIZATION_CONCURRENCY_LIMIT", 4
        ),
        global_concurrency_limit=_integer(
            environ, "CONNECTOR_TEST_GLOBAL_CONCURRENCY_LIMIT", 16
        ),
        connect_timeout_seconds=_integer(
            environ, "CONNECTOR_TEST_CONNECT_TIMEOUT_SECONDS", 5
        ),
        statement_timeout_seconds=_integer(
            environ, "CONNECTOR_TEST_STATEMENT_TIMEOUT_SECONDS", 3
        ),
        response_timeout_seconds=_float(
            environ, "CONNECTOR_TEST_RESPONSE_TIMEOUT_SECONDS", 10.0
        ),
        probe_hard_timeout_seconds=_float(
            environ, "CONNECTOR_TEST_PROBE_HARD_TIMEOUT_SECONDS", 20.0
        ),
        redis_operation_timeout_seconds=_float(
            environ,
            "CONNECTOR_TEST_REDIS_OPERATION_TIMEOUT_SECONDS",
            1.0,
        ),
        lease_ttl_seconds=_integer(environ, "CONNECTOR_TEST_LEASE_TTL_SECONDS", 30),
    )


def require_connector_test_security_ready(
    environ: Mapping[str, str] | None = None,
) -> None:
    values = environ if environ is not None else os.environ
    policy = connector_test_policy_from_environment(values)
    connector_proxy_dialer = connector_tcp_proxy_dialer_from_environment(values)
    if connector_proxy_dialer is not None and not policy.allowed_ports <= (
        connector_proxy_dialer.allowed_target_ports
    ):
        raise RuntimeError(
            "CONNECTOR_TEST_ALLOWED_PORTS must be allowed by connector egress"
        )
    _admission_key(values)
    _connector_test_redis_url(values)
    if policy.trusted_local_ca_file:
        _validate_trusted_local_ca_file(policy.trusted_local_ca_file)


def get_connector_test_application() -> ConnectorTestApplication:
    global _application
    if _application is None:
        policy = connector_test_policy_from_environment(os.environ)
        redis_url = _connector_test_redis_url(os.environ)
        owned_redis_client = (
            aioredis.from_url(redis_url, decode_responses=False)
            if redis_url
            else None
        )
        admission = RedisConnectorTestAdmission(
            (
                owned_redis_client
                if owned_redis_client is not None
                else get_async_redis_client()
            ),
            policy=policy,
            hmac_key=_admission_key(os.environ),
        )
        connector_proxy_dialer = connector_tcp_proxy_dialer_from_environment(
            os.environ
        )
        if connector_proxy_dialer is not None and not policy.allowed_ports <= (
            connector_proxy_dialer.allowed_target_ports
        ):
            raise RuntimeError(
                "CONNECTOR_TEST_ALLOWED_PORTS must be allowed by connector egress"
            )
        probe = StrictPostgresConnectorProbe(
            policy,
            connector_proxy_dialer=connector_proxy_dialer,
        )
        _application = ConnectorTestApplication(
            use_case=TestConnectorConnection(
                admission,
                probe,
                ConnectorTestAuditRecorder(),
                policy,
            ),
            probe=probe,
            owned_redis_client=owned_redis_client,
        )
    return _application


async def shutdown_connector_test_application() -> None:
    global _application
    application = _application
    _application = None
    if application is not None:
        application.probe.shutdown()
        if application.owned_redis_client is not None:
            try:
                await application.owned_redis_client.aclose()
            except Exception as exc:
                logger.error(
                    "Connector test Redis shutdown failed: error_type=%s",
                    type(exc).__name__,
                )


def _admission_key(environ: Mapping[str, str]) -> bytes:
    raw_key = environ.get("CONNECTOR_TEST_ADMISSION_HMAC_KEY", "")
    if not raw_key:
        if _environment_name(environ) == "production":
            raise RuntimeError(
                "CONNECTOR_TEST_ADMISSION_HMAC_KEY is required in production"
            )
        return _LOCAL_ADMISSION_KEY
    key = raw_key.encode("utf-8")
    if len(key) < 32:
        raise RuntimeError(
            "CONNECTOR_TEST_ADMISSION_HMAC_KEY must contain at least 32 bytes"
        )
    return key


def _connector_test_redis_url(environ: Mapping[str, str]) -> str | None:
    value = str(environ.get("CONNECTOR_TEST_REDIS_URL", ""))
    if not value:
        return None
    if value != value.strip():
        raise RuntimeError("CONNECTOR_TEST_REDIS_URL must not contain whitespace")
    try:
        parsed = urlsplit(value)
        port = parsed.port or 6379
        database = int(parsed.path.removeprefix("/"))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("CONNECTOR_TEST_REDIS_URL is invalid") from exc
    if (
        parsed.scheme not in {"redis", "rediss"}
        or not parsed.hostname
        or not 1 <= port <= 65535
        or not parsed.path.startswith("/")
        or parsed.path.count("/") != 1
        or not 0 <= database <= 255
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("CONNECTOR_TEST_REDIS_URL is invalid")
    return value


def _environment_name(environ: Mapping[str, str]) -> str:
    return str(environ.get("NODE_ENV", "")).strip().lower()


def _trusted_local_configuration(
    environ: Mapping[str, str],
    *,
    allowed_ports: frozenset[int],
) -> tuple[frozenset[TrustedLocalConnectorTarget], str | None]:
    raw_targets = str(
        environ.get("CONNECTOR_TEST_TRUSTED_LOCAL_TARGETS", "")
    ).strip()
    raw_ca_file = str(
        environ.get("CONNECTOR_TEST_TRUSTED_LOCAL_CA_FILE", "")
    ).strip()
    profile_enabled = _strict_boolean(
        environ,
        "CONNECTOR_TEST_LOCAL_PROFILE_ENABLED",
        False,
    )
    environment_name = _environment_name(environ)

    if environment_name == "production" and (
        profile_enabled or raw_targets or raw_ca_file
    ):
        raise RuntimeError(
            "trusted local connector profile is forbidden in production"
        )
    if (raw_targets or raw_ca_file) and not profile_enabled:
        raise RuntimeError(
            "trusted local connector settings require the explicit local profile"
        )
    if profile_enabled and not (raw_targets or raw_ca_file):
        raise RuntimeError(
            "trusted local connector profile requires exact targets and a CA file"
        )
    if bool(raw_targets) != bool(raw_ca_file):
        raise RuntimeError(
            "trusted local connector targets and CA file must be configured together"
        )
    if not raw_targets:
        return frozenset(), None
    if environment_name != "development":
        raise RuntimeError(
            "trusted local connector targets require NODE_ENV=development"
        )

    parts = [part.strip() for part in raw_targets.split(",")]
    if any(not part for part in parts) or len(parts) > 4:
        raise RuntimeError(
            "CONNECTOR_TEST_TRUSTED_LOCAL_TARGETS must contain 1 to 4 targets"
        )

    targets: list[TrustedLocalConnectorTarget] = []
    for part in parts:
        if part.count(":") != 1:
            raise RuntimeError(
                "trusted local connector target must use exact host:port syntax"
            )
        raw_host, raw_port = part.rsplit(":", 1)
        try:
            target = TrustedLocalConnectorTarget(
                host=canonicalize_network_host(raw_host),
                port=int(raw_port),
            )
        except (ValueError, TypeError) as exc:
            raise RuntimeError("trusted local connector target is invalid") from exc
        if target.port not in allowed_ports:
            raise RuntimeError(
                "trusted local connector target port is not deployment-allowed"
            )
        targets.append(target)

    if len(set(targets)) != len(targets):
        raise RuntimeError("trusted local connector targets must not contain duplicates")
    return frozenset(targets), raw_ca_file


def _integer(environ: Mapping[str, str], name: str, default: int) -> int:
    raw_value = environ.get(name)
    if raw_value is None or raw_value == "":
        return default
    try:
        return int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc


def _ports(
    environ: Mapping[str, str],
    name: str,
    default: set[int],
) -> frozenset[int]:
    raw_value = environ.get(name)
    if raw_value is None or raw_value.strip() == "":
        return frozenset(default)

    parts = [part.strip() for part in raw_value.split(",")]
    if any(not part for part in parts):
        raise RuntimeError(f"{name} must be a comma-separated port list")
    try:
        ports = [int(part) for part in parts]
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a comma-separated port list") from exc
    if len(set(ports)) != len(ports):
        raise RuntimeError(f"{name} must not contain duplicate ports")
    return frozenset(ports)


def _float(environ: Mapping[str, str], name: str, default: float) -> float:
    raw_value = environ.get(name)
    if raw_value is None or raw_value == "":
        return default
    try:
        return float(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be numeric") from exc


def _strict_boolean(
    environ: Mapping[str, str],
    name: str,
    default: bool,
) -> bool:
    raw_value = environ.get(name)
    if raw_value is None or raw_value == "":
        return default
    normalized = str(raw_value).strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise RuntimeError(f"{name} must be true or false")


def _validate_trusted_local_ca_file(raw_path: str) -> None:
    path = Path(raw_path)
    try:
        if not path.is_file():
            raise RuntimeError(
                "CONNECTOR_TEST_TRUSTED_LOCAL_CA_FILE must reference a regular file"
            )
        data = path.read_bytes()
    except OSError as exc:
        raise RuntimeError(
            "CONNECTOR_TEST_TRUSTED_LOCAL_CA_FILE must be readable"
        ) from exc
    if (
        not data
        or len(data) > _MAX_TRUSTED_LOCAL_CA_BYTES
        or data.count(b"-----BEGIN CERTIFICATE-----") != 1
        or data.count(b"-----END CERTIFICATE-----") != 1
        or b"PRIVATE KEY" in data
    ):
        raise RuntimeError(
            "CONNECTOR_TEST_TRUSTED_LOCAL_CA_FILE must contain one public CA certificate"
        )
    try:
        certificate = x509.load_pem_x509_certificate(data)
        constraints = certificate.extensions.get_extension_for_class(
            BasicConstraints
        ).value
    except (ValueError, x509.ExtensionNotFound) as exc:
        raise RuntimeError(
            "CONNECTOR_TEST_TRUSTED_LOCAL_CA_FILE must contain a valid CA certificate"
        ) from exc
    now = datetime.now(UTC)
    if (
        not constraints.ca
        or certificate.not_valid_before_utc > now
        or certificate.not_valid_after_utc <= now
    ):
        raise RuntimeError(
            "CONNECTOR_TEST_TRUSTED_LOCAL_CA_FILE must contain a currently valid CA"
        )


__all__ = [
    "ConnectorTestApplication",
    "connector_test_policy_from_environment",
    "get_connector_test_application",
    "require_connector_test_security_ready",
    "shutdown_connector_test_application",
]
