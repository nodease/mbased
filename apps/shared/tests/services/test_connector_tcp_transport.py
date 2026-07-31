from __future__ import annotations

import socket
import threading

import pytest
from apps.shared.connectors.postgres import PostgresConnector
from apps.shared.services.connector_tcp_transport import (
    CONNECTOR_PROXY_POLICY_REVISION,
    ConnectorTcpProxyConfigurationError,
    ConnectorTcpProxyError,
    HttpConnectProxyDialer,
    LocalConnectorProxyRelay,
    connector_tcp_proxy_dialer_from_environment,
)


class _FakeProxySocket:
    def __init__(self, response: bytes) -> None:
        self._response = response
        self.sent = bytearray()
        self.closed = False

    def settimeout(self, _timeout: float) -> None:
        return None

    def sendall(self, data: bytes) -> None:
        self.sent.extend(data)

    def recv(self, size: int) -> bytes:
        response, self._response = self._response[:size], self._response[size:]
        return response

    def close(self) -> None:
        self.closed = True


def _proxy_environment(**overrides: str) -> dict[str, str]:
    environment = {
        "NODE_ENV": "production",
        "CONNECTOR_EGRESS_PROXY_URL": "http://egress-proxy:3130",
        "CONNECTOR_EGRESS_PROXY_ALLOWED_HOSTS": "egress-proxy",
        "CONNECTOR_EGRESS_POLICY_REVISION": CONNECTOR_PROXY_POLICY_REVISION,
        "CONNECTOR_EGRESS_ALLOWED_PORTS": "22,5432",
    }
    environment.update(overrides)
    return environment


def test_connector_proxy_is_required_in_production() -> None:
    with pytest.raises(ConnectorTcpProxyConfigurationError) as exc_info:
        connector_tcp_proxy_dialer_from_environment({"NODE_ENV": "production"})

    assert exc_info.value.reason_code == "connector.egress_proxy_required"


def test_connector_proxy_is_optional_for_local_direct_development() -> None:
    assert (
        connector_tcp_proxy_dialer_from_environment({"NODE_ENV": "development"})
        is None
    )


@pytest.mark.parametrize(
    ("override", "reason_code"),
    [
        (
            {"CONNECTOR_EGRESS_PROXY_URL": "http://public.example:3130"},
            "connector.egress_proxy_host_not_allowed",
        ),
        (
            {"CONNECTOR_EGRESS_PROXY_URL": "http://egress-proxy:3129"},
            "connector.egress_proxy_endpoint_invalid",
        ),
        (
            {"CONNECTOR_EGRESS_POLICY_REVISION": "stale"},
            "connector.egress_proxy_revision_invalid",
        ),
        (
            {
                "CONNECTOR_EGRESS_ALLOWED_PORTS": ",".join(
                    str(port) for port in range(1, 18)
                )
            },
            "connector.egress_target_ports_invalid",
        ),
        (
            {"CONNECTOR_EGRESS_ALLOWED_PORTS": "22,22"},
            "connector.egress_target_ports_invalid",
        ),
        (
            {"CONNECTOR_EGRESS_ALLOWED_PORTS": "0,5432"},
            "connector.egress_target_ports_invalid",
        ),
    ],
)
def test_connector_proxy_rejects_unsafe_configuration(
    override: dict[str, str], reason_code: str
) -> None:
    with pytest.raises(ConnectorTcpProxyConfigurationError) as exc_info:
        connector_tcp_proxy_dialer_from_environment(
            _proxy_environment(**override)
        )

    assert exc_info.value.reason_code == reason_code


def test_connector_proxy_accepts_deployment_managed_target_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proxy_socket = _FakeProxySocket(b"HTTP/1.1 200 Connection established\r\n\r\n")
    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda *_args, **_kwargs: proxy_socket,
    )
    dialer = connector_tcp_proxy_dialer_from_environment(
        _proxy_environment(CONNECTOR_EGRESS_ALLOWED_PORTS="22,5432,15432")
    )

    assert dialer is not None
    dialer.open_tunnel("93.184.216.34", 15432, timeout_seconds=3.0)

    assert b"CONNECT 93.184.216.34:15432 HTTP/1.1" in proxy_socket.sent


def test_connector_proxy_connects_only_to_pinned_public_ip_and_allowed_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proxy_socket = _FakeProxySocket(b"HTTP/1.1 200 Connection established\r\n\r\n")
    calls: list[tuple[tuple[str, int], float]] = []

    def _connect(address: tuple[str, int], timeout: float):
        calls.append((address, timeout))
        return proxy_socket

    monkeypatch.setattr(socket, "create_connection", _connect)
    dialer = connector_tcp_proxy_dialer_from_environment(_proxy_environment())

    assert dialer is not None
    tunnel = dialer.open_tunnel("93.184.216.34", 5432, timeout_seconds=3.0)

    assert tunnel is proxy_socket
    assert calls == [(('egress-proxy', 3130), 3.0)]
    assert bytes(proxy_socket.sent) == (
        b"CONNECT 93.184.216.34:5432 HTTP/1.1\r\n"
        b"Host: 93.184.216.34:5432\r\n\r\n"
    )


def test_connector_proxy_preserves_immediate_tunneled_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    banner = b"SSH-2.0-example\r\n"
    proxy_socket = _FakeProxySocket(
        b"HTTP/1.1 200 Connection established\r\n\r\n" + banner
    )
    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda *_args, **_kwargs: proxy_socket,
    )
    dialer = connector_tcp_proxy_dialer_from_environment(_proxy_environment())

    assert dialer is not None
    tunnel = dialer.open_tunnel("93.184.216.34", 22, timeout_seconds=3.0)

    assert tunnel.recv(len(banner)) == banner


@pytest.mark.parametrize(
    ("address", "port"),
    [
        ("127.0.0.1", 5432),
        ("10.0.0.1", 5432),
        ("169.254.169.254", 5432),
        ("::ffff:93.184.216.34", 5432),
        ("93.184.216.34", 443),
    ],
)
def test_connector_proxy_rejects_non_public_mapped_or_disallowed_targets(
    monkeypatch: pytest.MonkeyPatch,
    address: str,
    port: int,
) -> None:
    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda *_args, **_kwargs: pytest.fail("proxy dial must not start"),
    )
    dialer = connector_tcp_proxy_dialer_from_environment(_proxy_environment())

    assert dialer is not None
    with pytest.raises(ConnectorTcpProxyError):
        dialer.open_tunnel(address, port, timeout_seconds=3.0)


def test_connector_proxy_rejects_non_successful_or_oversized_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        (
            b"HTTP/1.1 403 Forbidden\r\n\r\n",
            b"HTTP/1.1 200 OK\r\nX-Fill: " + b"a" * 8192,
        )
    )
    dialer = connector_tcp_proxy_dialer_from_environment(_proxy_environment())

    assert dialer is not None
    for _ in range(2):
        proxy_socket = _FakeProxySocket(next(responses))
        monkeypatch.setattr(socket, "create_connection", lambda *_a, **_k: proxy_socket)
        with pytest.raises(ConnectorTcpProxyError):
            dialer.open_tunnel("93.184.216.34", 5432, timeout_seconds=3.0)
        assert proxy_socket.closed is True


def test_local_connector_relay_forwards_bytes_without_re_resolving_target() -> None:
    observed: list[tuple[str, int]] = []

    class _SocketPairDialer(HttpConnectProxyDialer):
        def __init__(self) -> None:
            pass

        def open_native_tunnel(
            self, target_address: str, target_port: int, *, timeout_seconds: float
        ) -> socket.socket:
            del timeout_seconds
            observed.append((target_address, target_port))
            client, peer = socket.socketpair()

            def _echo() -> None:
                with peer:
                    data = peer.recv(64)
                    peer.sendall(data.upper())

            threading.Thread(target=_echo, daemon=True).start()
            return client

    relay = LocalConnectorProxyRelay(
        _SocketPairDialer(),
        target_address="93.184.216.34",
        target_port=5432,
        connect_timeout_seconds=3.0,
    )
    relay.start()
    try:
        with socket.create_connection(("127.0.0.1", relay.local_bind_port)) as client:
            client.sendall(b"select one")
            assert client.recv(64) == b"SELECT ONE"
    finally:
        relay.stop()

    assert observed == [("93.184.216.34", 5432)]


def test_postgres_connector_routes_validated_address_through_local_proxy_relay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dialer = connector_tcp_proxy_dialer_from_environment(_proxy_environment())
    observed: dict[str, object] = {}

    class _Relay:
        local_bind_port = 6543

        def __init__(
            self,
            received_dialer: HttpConnectProxyDialer,
            *,
            target_address: str,
            target_port: int,
            connect_timeout_seconds: float,
        ) -> None:
            observed.update(
                dialer=received_dialer,
                target_address=target_address,
                target_port=target_port,
                timeout=connect_timeout_seconds,
            )

        def start(self) -> None:
            observed["started"] = True

        def stop(self) -> None:
            observed["stopped"] = True

    monkeypatch.setattr(
        "apps.shared.connectors.postgres.ensure_network_target_allowed",
        lambda *_args, **_kwargs: ("db.example", 5432, "93.184.216.34"),
    )
    monkeypatch.setattr(
        "apps.shared.connectors.postgres.LocalConnectorProxyRelay",
        _Relay,
    )
    connector = PostgresConnector(connector_proxy_dialer=dialer)

    engine, relay = connector._create_tunnel_and_engine(
        {
            "host": "db.example",
            "port": 5432,
            "username": "user",
            "password": "not-a-real-secret",
            "database": "database",
        }
    )
    try:
        assert engine.url.host == "db.example"
        assert engine.url.query["hostaddr"] == "127.0.0.1"
        assert engine.url.port == 6543
        assert observed == {
            "dialer": dialer,
            "target_address": "93.184.216.34",
            "target_port": 5432,
            "timeout": 5,
            "started": True,
        }
    finally:
        engine.dispose()
        assert relay is not None
        relay.stop()

    assert observed["stopped"] is True


def test_postgres_connector_uses_proxy_deployment_ports_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}
    dialer = connector_tcp_proxy_dialer_from_environment(
        _proxy_environment(CONNECTOR_EGRESS_ALLOWED_PORTS="22,5432,55432")
    )
    assert dialer is not None

    class _Relay:
        local_bind_port = 6543

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def start(self) -> None:
            observed["started"] = True

        def stop(self) -> None:
            observed["stopped"] = True

    def _allow_target(
        host: str,
        port: int,
        *,
        allowed_ports: frozenset[int] | None,
    ) -> tuple[str, int, str]:
        observed["target"] = (host, port)
        observed["allowed_ports"] = allowed_ports
        return host, port, "93.184.216.34"

    monkeypatch.setattr(
        "apps.shared.connectors.postgres.ensure_network_target_allowed",
        _allow_target,
    )
    monkeypatch.setattr(
        "apps.shared.connectors.postgres.LocalConnectorProxyRelay",
        _Relay,
    )
    connector = PostgresConnector(connector_proxy_dialer=dialer)

    engine, relay = connector._create_tunnel_and_engine(
        {
            "host": "db.example",
            "port": 55432,
            "username": "user",
            "password": "not-a-real-secret",
            "database": "database",
        }
    )
    try:
        assert observed["target"] == ("db.example", 55432)
        assert observed["allowed_ports"] == frozenset({22, 5432, 55432})
    finally:
        engine.dispose()
        assert relay is not None
        relay.stop()

    assert observed["stopped"] is True


def test_postgres_connector_keeps_default_port_for_direct_development(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def _allow_target(
        host: str,
        port: int,
        *,
        allowed_ports: frozenset[int] | None,
    ) -> tuple[str, int, str]:
        observed["allowed_ports"] = allowed_ports
        return host, port, "93.184.216.34"

    monkeypatch.setattr(
        "apps.shared.connectors.postgres.ensure_network_target_allowed",
        _allow_target,
    )
    connector = PostgresConnector(connector_proxy_dialer=None)

    engine, relay = connector._create_tunnel_and_engine(
        {
            "host": "db.example",
            "port": 5432,
            "username": "user",
            "password": "not-a-real-secret",
            "database": "database",
        }
    )
    try:
        assert observed["allowed_ports"] == frozenset({5432})
        assert relay is None
    finally:
        engine.dispose()


def test_postgres_connector_stops_proxy_relay_when_engine_creation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    class _Relay:
        local_bind_port = 6543

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def start(self) -> None:
            observed["started"] = True

        def stop(self) -> None:
            observed["stopped"] = True

    monkeypatch.setattr(
        "apps.shared.connectors.postgres.ensure_network_target_allowed",
        lambda *_args, **_kwargs: ("db.example", 5432, "93.184.216.34"),
    )
    monkeypatch.setattr(
        "apps.shared.connectors.postgres.LocalConnectorProxyRelay",
        _Relay,
    )
    monkeypatch.setattr(
        "apps.shared.connectors.postgres.create_engine",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("failed")),
    )
    connector = PostgresConnector(
        connector_proxy_dialer=connector_tcp_proxy_dialer_from_environment(
            _proxy_environment()
        )
    )

    with pytest.raises(RuntimeError, match="failed"):
        connector._create_tunnel_and_engine(
            {
                "host": "db.example",
                "port": 5432,
                "username": "user",
                "password": "not-a-real-secret",
                "database": "database",
            }
        )

    assert observed == {"started": True, "stopped": True}


def test_postgres_connector_stops_proxy_relay_when_url_creation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    class _Relay:
        local_bind_port = 6543

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def start(self) -> None:
            observed["started"] = True

        def stop(self) -> None:
            observed["stopped"] = True

    monkeypatch.setattr(
        "apps.shared.connectors.postgres.ensure_network_target_allowed",
        lambda *_args, **_kwargs: ("db.example", 5432, "93.184.216.34"),
    )
    monkeypatch.setattr(
        "apps.shared.connectors.postgres.LocalConnectorProxyRelay",
        _Relay,
    )
    monkeypatch.setattr(
        "apps.shared.connectors.postgres.URL.create",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("failed")),
    )
    connector = PostgresConnector(
        connector_proxy_dialer=connector_tcp_proxy_dialer_from_environment(
            _proxy_environment()
        )
    )

    with pytest.raises(RuntimeError, match="failed"):
        connector._create_tunnel_and_engine(
            {
                "host": "db.example",
                "port": 5432,
                "username": "user",
                "password": "not-a-real-secret",
                "database": "database",
            }
        )

    assert observed == {"started": True, "stopped": True}


def test_postgres_ssh_tunnel_uses_validated_bastion_ip_through_connector_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    class _ProxySocket:
        def close(self) -> None:
            observed["proxy_closed"] = True

    class _Dialer(HttpConnectProxyDialer):
        def __init__(self) -> None:
            pass

        def open_tunnel(
            self,
            target_address: str,
            target_port: int,
            *,
            timeout_seconds: float,
        ) -> _ProxySocket:
            observed["proxy_target"] = (
                target_address,
                target_port,
                timeout_seconds,
            )
            return _ProxySocket()

    class _SshTunnel:
        local_bind_port = 6543

        def __init__(self, **kwargs: object) -> None:
            observed["ssh_params"] = kwargs

        def start(self) -> None:
            observed["ssh_started"] = True

        def stop(self) -> None:
            observed["ssh_stopped"] = True

    monkeypatch.setattr(
        "apps.shared.connectors.postgres.ensure_network_target_allowed",
        lambda *_args, **_kwargs: ("bastion.example", 22, "93.184.216.35"),
    )
    monkeypatch.setattr(
        "apps.shared.connectors.postgres._create_ssh_tunnel",
        lambda **kwargs: _SshTunnel(**kwargs),
    )
    dialer = _Dialer()
    connector = PostgresConnector(
        allow_ssh_tunnel=True,
        allowed_db_ports=None,
        connector_proxy_dialer=dialer,
    )

    engine, tunnel = connector._create_tunnel_and_engine(
        {
            "host": "private-db.internal",
            "port": 5432,
            "username": "db-user",
            "password": "not-a-real-secret",
            "database": "database",
            "ssh": {
                "enabled": True,
                "host": "bastion.example",
                "port": 22,
                "username": "ssh-user",
                "auth_type": "password",
                "password": "not-a-real-secret",
            },
        }
    )
    try:
        assert observed["proxy_target"] == ("93.184.216.35", 22, 5)
        ssh_params = observed["ssh_params"]
        assert isinstance(ssh_params, dict)
        assert ssh_params["ssh_address_or_host"] == ("bastion.example", 22)
        assert isinstance(ssh_params["ssh_proxy"], _ProxySocket)
        assert observed["ssh_started"] is True
        assert engine.url.host == "127.0.0.1"
        assert engine.url.port == 6543
    finally:
        engine.dispose()
        assert tunnel is not None
        tunnel.stop()

    assert observed["ssh_stopped"] is True


def test_postgres_connector_closes_proxy_when_ssh_tunnel_start_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[str] = []

    class _ProxySocket:
        def close(self) -> None:
            observed.append("proxy_closed")

    class _Dialer(HttpConnectProxyDialer):
        def __init__(self) -> None:
            pass

        def open_tunnel(
            self,
            target_address: str,
            target_port: int,
            *,
            timeout_seconds: float,
        ) -> _ProxySocket:
            del target_address, target_port, timeout_seconds
            return _ProxySocket()

    class _FailingSshTunnel:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def start(self) -> None:
            raise RuntimeError("failed")

        def stop(self) -> None:
            observed.append("tunnel_stopped")

    monkeypatch.setattr(
        "apps.shared.connectors.postgres.ensure_network_target_allowed",
        lambda *_args, **_kwargs: ("bastion.example", 22, "93.184.216.35"),
    )
    monkeypatch.setattr(
        "apps.shared.connectors.postgres._create_ssh_tunnel",
        lambda **kwargs: _FailingSshTunnel(**kwargs),
    )
    connector = PostgresConnector(
        allow_ssh_tunnel=True,
        allowed_db_ports=None,
        connector_proxy_dialer=_Dialer(),
    )

    with pytest.raises(RuntimeError, match="failed"):
        connector._create_tunnel_and_engine(
            {
                "host": "private-db.internal",
                "port": 5432,
                "username": "db-user",
                "password": "not-a-real-secret",
                "database": "database",
                "ssh": {
                    "enabled": True,
                    "host": "bastion.example",
                    "port": 22,
                    "username": "ssh-user",
                    "auth_type": "password",
                    "password": "not-a-real-secret",
                },
            }
        )

    assert observed == ["tunnel_stopped", "proxy_closed"]
