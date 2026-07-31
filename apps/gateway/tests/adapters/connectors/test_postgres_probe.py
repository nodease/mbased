import asyncio
import threading
from contextlib import nullcontext
from uuid import uuid4

import pytest

from apps.gateway.adapters.connectors import postgres_probe as probe_module
from apps.gateway.adapters.connectors.postgres_probe import StrictPostgresConnectorProbe
from apps.gateway.application.connectors.errors import (
    ConnectorProbeCapacityExceeded,
    ConnectorProbeFailed,
    ConnectorTargetNotAllowed,
)
from apps.gateway.application.connectors.models import (
    ConnectorTestCommand,
    ConnectorTestPolicy,
    TrustedLocalConnectorTarget,
)
from apps.shared.services.egress_guard import EgressGuardError
from apps.shared.services.connector_tcp_transport import HttpConnectProxyDialer


def command(*, host: str = "db.example.com", port: int = 5432) -> ConnectorTestCommand:
    return ConnectorTestCommand(
        organization_id=uuid4(),
        actor_id=uuid4(),
        network_address="203.0.113.9",
        host=host,
        port=port,
        database="app",
        username="app-user",
        password="placeholder-secret",
    )


class FakeResult:
    def scalar_one(self) -> int:
        return 1


class FakeConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement):
        self.statements.append(str(statement))
        return FakeResult()


class FakeEngine:
    def __init__(self) -> None:
        self.connection = FakeConnection()
        self.disposed = False

    def connect(self):
        return nullcontext(self.connection)

    def dispose(self) -> None:
        self.disposed = True


def test_probe_pins_public_ip_enforces_tls_and_runs_constant_read_only_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    engine = FakeEngine()

    def fake_guard(
        host: str,
        port: int,
        *,
        allowed_ports,
        trusted_local_targets,
    ):
        captured["guard"] = (
            host,
            port,
            allowed_ports,
            trusted_local_targets,
        )
        return "db.example.com", 55432, "203.0.113.20"

    def fake_create_engine(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return engine

    monkeypatch.setattr(probe_module, "ensure_network_target_allowed", fake_guard)
    monkeypatch.setattr(probe_module, "create_engine", fake_create_engine)
    monkeypatch.setattr(probe_module, "_system_ca_file", lambda: "/system/ca.pem")
    policy = ConnectorTestPolicy(allowed_ports=frozenset({5432, 55432}))
    probe = StrictPostgresConnectorProbe(policy)

    try:
        assert probe._probe_sync(command(port=55432)) is True
    finally:
        probe.shutdown()

    url = captured["url"]
    assert url.host == "db.example.com"
    assert url.port == 55432
    assert dict(url.query) == {
        "hostaddr": "203.0.113.20",
        "sslmode": "verify-full",
        "sslrootcert": "/system/ca.pem",
    }
    assert captured["guard"] == (
        "db.example.com",
        55432,
        frozenset({5432, 55432}),
        frozenset(),
    )
    assert captured["kwargs"]["connect_args"] == {
        "connect_timeout": 5,
        "options": "-c statement_timeout=3000",
    }
    assert engine.connection.statements == ["SET TRANSACTION READ ONLY", "SELECT 1"]
    assert engine.disposed is True


def test_probe_preserves_tls_hostname_while_dialing_validated_ip_through_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    engine = FakeEngine()

    class _Dialer(HttpConnectProxyDialer):
        def __init__(self) -> None:
            pass

    class _Relay:
        local_bind_port = 6543

        def __init__(
            self,
            dialer: HttpConnectProxyDialer,
            *,
            target_address: str,
            target_port: int,
            connect_timeout_seconds: float,
        ) -> None:
            captured["relay"] = (
                dialer,
                target_address,
                target_port,
                connect_timeout_seconds,
            )

        def start(self) -> None:
            captured["relay_started"] = True

        def stop(self) -> None:
            captured["relay_stopped"] = True

    monkeypatch.setattr(
        probe_module,
        "ensure_network_target_allowed",
        lambda *_args, **_kwargs: ("db.example.com", 5432, "203.0.113.20"),
    )
    monkeypatch.setattr(probe_module, "_system_ca_file", lambda: "/system/ca.pem")
    monkeypatch.setattr(probe_module, "LocalConnectorProxyRelay", _Relay)

    def fake_create_engine(url, **_kwargs):
        captured["url"] = url
        return engine

    monkeypatch.setattr(probe_module, "create_engine", fake_create_engine)
    dialer = _Dialer()
    probe = StrictPostgresConnectorProbe(
        ConnectorTestPolicy(), connector_proxy_dialer=dialer
    )
    try:
        assert probe._probe_sync(command()) is True
    finally:
        probe.shutdown()

    url = captured["url"]
    assert url.host == "db.example.com"
    assert url.port == 6543
    assert dict(url.query) == {
        "hostaddr": "127.0.0.1",
        "sslmode": "verify-full",
        "sslrootcert": "/system/ca.pem",
    }
    assert captured["relay"] == (dialer, "203.0.113.20", 5432, 5)
    assert captured["relay_started"] is True
    assert captured["relay_stopped"] is True


def test_probe_uses_deployment_ca_for_exact_trusted_local_target(
    monkeypatch,
    tmp_path,
) -> None:
    captured: dict[str, object] = {}
    engine = FakeEngine()
    ca_file = tmp_path / "ca.crt"
    ca_file.write_text("test-only-ca-placeholder", encoding="utf-8")

    def fake_guard(
        host: str,
        port: int,
        *,
        allowed_ports,
        trusted_local_targets,
    ):
        captured["guard"] = (
            host,
            port,
            allowed_ports,
            trusted_local_targets,
        )
        return "connector-test-postgres", 5432, "172.20.0.20"

    def fake_create_engine(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return engine

    monkeypatch.setattr(probe_module, "ensure_network_target_allowed", fake_guard)
    monkeypatch.setattr(probe_module, "create_engine", fake_create_engine)
    monkeypatch.setattr(
        probe_module,
        "_system_ca_file",
        lambda: pytest.fail("system CA must not be used for a trusted local target"),
    )
    target = TrustedLocalConnectorTarget(
        host="connector-test-postgres",
        port=5432,
    )
    policy = ConnectorTestPolicy(
        trusted_local_targets=frozenset({target}),
        trusted_local_ca_file=str(ca_file),
    )
    probe = StrictPostgresConnectorProbe(policy)

    try:
        assert probe._probe_sync(command(host="CONNECTOR-TEST-POSTGRES.")) is True
    finally:
        probe.shutdown()

    url = captured["url"]
    assert url.host == "connector-test-postgres"
    assert dict(url.query) == {
        "hostaddr": "172.20.0.20",
        "sslmode": "verify-full",
        "sslrootcert": str(ca_file),
    }
    assert captured["guard"] == (
        "CONNECTOR-TEST-POSTGRES.",
        5432,
        frozenset({5432}),
        frozenset({("connector-test-postgres", 5432)}),
    )


def test_local_profile_does_not_replace_system_ca_for_public_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    captured: dict[str, object] = {}
    engine = FakeEngine()
    local_ca_file = tmp_path / "local-ca.crt"
    local_ca_file.write_text("test-only-local-ca-placeholder", encoding="utf-8")
    target = TrustedLocalConnectorTarget(host="localhost", port=55432)
    policy = ConnectorTestPolicy(
        allowed_ports=frozenset({5432, 55432}),
        trusted_local_targets=frozenset({target}),
        trusted_local_ca_file=str(local_ca_file),
    )

    monkeypatch.setattr(probe_module, "_system_ca_file", lambda: "/system/ca.pem")
    monkeypatch.setattr(
        probe_module,
        "ensure_network_target_allowed",
        lambda *_args, **_kwargs: ("db.example.com", 5432, "203.0.113.20"),
    )

    def fake_create_engine(url, **_kwargs):
        captured["url"] = url
        return engine

    monkeypatch.setattr(probe_module, "create_engine", fake_create_engine)
    probe = StrictPostgresConnectorProbe(policy)
    try:
        assert probe._probe_sync(command()) is True
    finally:
        probe.shutdown()

    assert dict(captured["url"].query)["sslrootcert"] == "/system/ca.pem"


def test_probe_rejects_missing_trusted_local_ca_before_dns(tmp_path, monkeypatch) -> None:
    def guard(*_args, **_kwargs):
        pytest.fail("DNS guard must not run")

    monkeypatch.setattr(probe_module, "ensure_network_target_allowed", guard)
    target = TrustedLocalConnectorTarget(host="localhost", port=5432)
    probe = StrictPostgresConnectorProbe(
        ConnectorTestPolicy(
            trusted_local_targets=frozenset({target}),
            trusted_local_ca_file=str(tmp_path / "missing.crt"),
        )
    )

    try:
        with pytest.raises(ConnectorProbeFailed):
            probe._probe_sync(command(host="localhost"))
    finally:
        probe.shutdown()


def test_probe_fails_closed_before_dns_when_system_ca_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_ca_file() -> str:
        raise ConnectorProbeFailed()

    def guard(*_args, **_kwargs):
        pytest.fail("DNS guard must not run")

    monkeypatch.setattr(probe_module, "_system_ca_file", no_ca_file)
    monkeypatch.setattr(probe_module, "ensure_network_target_allowed", guard)
    probe = StrictPostgresConnectorProbe(ConnectorTestPolicy())

    try:
        with pytest.raises(ConnectorProbeFailed):
            probe._probe_sync(command())
    finally:
        probe.shutdown()


@pytest.mark.parametrize(
    "host",
    [
        "postgresql://db.example.com",
        "db.example.com/path",
        "user@db.example.com",
        "db.example.com?sslmode=disable",
        "db.example.com#fragment",
    ],
)
def test_probe_rejects_url_shaped_host_before_dns(
    host: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def guard(*_args, **_kwargs):
        pytest.fail("DNS guard must not run")

    monkeypatch.setattr(probe_module, "ensure_network_target_allowed", guard)
    probe = StrictPostgresConnectorProbe(ConnectorTestPolicy())

    try:
        with pytest.raises(ConnectorTargetNotAllowed):
            probe._probe_sync(command(host=host))
    finally:
        probe.shutdown()


def test_probe_normalizes_egress_and_driver_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(probe_module, "_system_ca_file", lambda: "/system/ca.pem")
    probe = StrictPostgresConnectorProbe(ConnectorTestPolicy())
    try:
        monkeypatch.setattr(
            probe_module,
            "ensure_network_target_allowed",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                EgressGuardError("egress.private_target")
            ),
        )
        with pytest.raises(ConnectorTargetNotAllowed):
            probe._probe_sync(command())

        monkeypatch.setattr(
            probe_module,
            "ensure_network_target_allowed",
            lambda *_args, **_kwargs: ("db.example.com", 5432, "203.0.113.20"),
        )
        monkeypatch.setattr(
            probe_module,
            "create_engine",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("raw-driver-detail")
            ),
        )
        with pytest.raises(ConnectorProbeFailed) as exc_info:
            probe._probe_sync(command())
        assert "raw-driver-detail" not in str(exc_info.value)
    finally:
        probe.shutdown()


@pytest.mark.asyncio
async def test_local_executor_capacity_fails_without_queueing() -> None:
    probe = StrictPostgresConnectorProbe(ConnectorTestPolicy())
    acquired = [
        probe._capacity.acquire(blocking=False)
        for _ in range(ConnectorTestPolicy().global_concurrency_limit)
    ]
    assert all(acquired)
    try:
        with pytest.raises(ConnectorProbeCapacityExceeded):
            probe.reserve()
    finally:
        for _ in acquired:
            probe._capacity.release()
        probe.shutdown()


@pytest.mark.asyncio
async def test_cancelled_probe_keeps_capacity_until_driver_thread_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    finish = threading.Event()
    policy = ConnectorTestPolicy(
        organization_concurrency_limit=1,
        global_concurrency_limit=1,
    )
    probe = StrictPostgresConnectorProbe(policy)

    def blocking_probe(_command: ConnectorTestCommand) -> bool:
        started.set()
        finish.wait(timeout=1)
        return True

    monkeypatch.setattr(probe, "_probe_sync", blocking_probe)
    reservation = probe.reserve()
    task = asyncio.create_task(reservation.probe(command()))

    try:
        assert await asyncio.to_thread(started.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        with pytest.raises(ConnectorProbeCapacityExceeded):
            probe.reserve()

        finish.set()
        for _ in range(100):
            try:
                restored_reservation = probe.reserve()
            except ConnectorProbeCapacityExceeded:
                pass
            else:
                restored_reservation.release()
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("driver completion did not restore connector probe capacity")

        next_reservation = probe.reserve()
        assert await next_reservation.probe(command()) is True
    finally:
        finish.set()
        probe.shutdown()
