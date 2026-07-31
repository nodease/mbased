from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_direct_connector_proxy_dialer_remains_cooperative_after_gevent_patch() -> None:
    script = textwrap.dedent(
        """
        from gevent import monkey

        monkey.patch_all()

        import apps.shared.services.connector_tcp_transport as transport

        class FakeProxySocket:
            def __init__(self):
                self.responses = iter(
                    [b"HTTP/1.1 200 Connection established\\r\\n\\r\\n"]
                )

            def settimeout(self, _timeout):
                pass

            def sendall(self, _payload):
                pass

            def recv(self, _size):
                return next(self.responses, b"")

            def close(self):
                pass

        observed = []

        def cooperative_create_connection(address, *, timeout):
            observed.append((address, timeout))
            return FakeProxySocket()

        def reject_native_connection(*_args, **_kwargs):
            raise AssertionError("direct dialer used native connector")

        transport.socket.create_connection = cooperative_create_connection
        transport._native_create_connection = reject_native_connection
        dialer = transport.HttpConnectProxyDialer(
            transport.ConnectorTcpProxyPolicy(
                proxy_host="127.0.0.1",
                proxy_port=3130,
                allowed_target_ports=frozenset({22}),
                policy_revision=transport.CONNECTOR_PROXY_POLICY_REVISION,
            )
        )

        tunnel = dialer.open_tunnel(
            "93.184.216.34",
            22,
            timeout_seconds=2.0,
        )

        assert isinstance(tunnel, FakeProxySocket)
        assert observed == [(("127.0.0.1", 3130), 2.0)]
        print("ok")
        """
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT)

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr[-1000:]
    assert result.stdout.strip() == "ok"


def test_connector_proxy_relay_progresses_after_gevent_patch() -> None:
    script = textwrap.dedent(
        """
        import _thread
        import socket

        native_allocate_lock = _thread.allocate_lock
        native_start_new_thread = _thread.start_new_thread
        native_socket = socket.socket

        from gevent import monkey

        monkey.patch_all()

        from apps.shared.services.connector_tcp_transport import (
            CONNECTOR_PROXY_POLICY_REVISION,
            ConnectorTcpProxyPolicy,
            HttpConnectProxyDialer,
            LocalConnectorProxyRelay,
        )

        proxy_listener = native_socket(socket.AF_INET, socket.SOCK_STREAM)
        proxy_listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        proxy_listener.bind(("127.0.0.1", 0))
        proxy_listener.listen(1)
        proxy_listener.settimeout(3.0)
        proxy_port = proxy_listener.getsockname()[1]

        proxy_done = native_allocate_lock()
        proxy_done.acquire()

        def serve_proxy():
            try:
                connection, _address = proxy_listener.accept()
                with connection:
                    request = bytearray()
                    while b"\\r\\n\\r\\n" not in request:
                        request.extend(connection.recv(1))
                    assert request.startswith(
                        b"CONNECT 93.184.216.34:5432 HTTP/1.1\\r\\n"
                    )
                    connection.sendall(
                        b"HTTP/1.1 200 Connection established\\r\\n\\r\\n"
                    )
                    connection.sendall(connection.recv(64).upper())
            finally:
                proxy_listener.close()
                proxy_done.release()

        native_start_new_thread(serve_proxy, ())

        dialer = HttpConnectProxyDialer(
            ConnectorTcpProxyPolicy(
                proxy_host="127.0.0.1",
                proxy_port=proxy_port,
                allowed_target_ports=frozenset({5432}),
                policy_revision=CONNECTOR_PROXY_POLICY_REVISION,
            )
        )
        relay = LocalConnectorProxyRelay(
            dialer,
            target_address="93.184.216.34",
            target_port=5432,
            connect_timeout_seconds=2.0,
        )
        relay.start()
        try:
            with native_socket(socket.AF_INET, socket.SOCK_STREAM) as client:
                client.settimeout(2.0)
                client.connect(("127.0.0.1", relay.local_bind_port))
                client.sendall(b"select one")
                assert client.recv(64) == b"SELECT ONE"
        finally:
            relay.stop()

        assert proxy_done.acquire(timeout=2.0)
        proxy_done.release()
        print("ok")
        """
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT)

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr[-1000:]
    assert result.stdout.strip() == "ok"
