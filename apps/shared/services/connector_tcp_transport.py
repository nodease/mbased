from __future__ import annotations

import ipaddress
import os
import re
import socket
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import partial
from urllib.parse import urlsplit

CONNECTOR_PROXY_POLICY_REVISION = "connector-egress-v1"
CONNECTOR_PROXY_PORT = 3130
_DEFAULT_ALLOWED_TARGET_PORTS = frozenset({22, 5432})
_MAX_ALLOWED_TARGET_PORTS = 16
_MAX_PROXY_RESPONSE_BYTES = 4096
_DNS_LABEL_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_PRIVATE_PROXY_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("fc00::/7"),
)


def _original_runtime_primitive(module_name: str, attribute: str) -> object:
    try:
        from gevent import monkey
    except ImportError:
        module = __import__(module_name)
        return getattr(module, attribute)
    return monkey.get_original(module_name, attribute)


_NATIVE_START_NEW_THREAD = _original_runtime_primitive(
    "_thread",
    "start_new_thread",
)
_NATIVE_ALLOCATE_LOCK = _original_runtime_primitive("_thread", "allocate_lock")
_NATIVE_SOCKET = _original_runtime_primitive("socket", "socket")
_NATIVE_GETADDRINFO = _original_runtime_primitive("socket", "getaddrinfo")
_NATIVE_SELECT = _original_runtime_primitive("select", "select")


class _NativeEvent:
    def __init__(self) -> None:
        self._lock = _NATIVE_ALLOCATE_LOCK()
        self._value = False

    def set(self) -> None:
        with self._lock:
            self._value = True

    def is_set(self) -> bool:
        with self._lock:
            return self._value


class _NativeThread:
    def __init__(self, target: Callable[[], None]) -> None:
        self._target = target
        self._done = _NATIVE_ALLOCATE_LOCK()
        self._done.acquire()
        self._started = False

    def start(self) -> None:
        if self._started:
            raise RuntimeError("native thread is already started")
        self._started = True
        try:
            _NATIVE_START_NEW_THREAD(self._run, ())
        except BaseException:
            self._started = False
            self._done.release()
            raise

    def _run(self) -> None:
        try:
            self._target()
        except BaseException:
            pass
        finally:
            self._done.release()

    def join(self, timeout: float | None = None) -> None:
        if not self._started:
            return
        if timeout is None:
            acquired = self._done.acquire()
        else:
            acquired = self._done.acquire(True, max(0.0, timeout))
        if acquired:
            self._done.release()


def _native_create_connection(
    address: tuple[str, int],
    *,
    timeout: float,
) -> socket.socket:
    host, port = address
    last_error: OSError | None = None
    for (
        family,
        socktype,
        protocol,
        _canonical_name,
        socket_address,
    ) in _NATIVE_GETADDRINFO(host, port, 0, socket.SOCK_STREAM):
        candidate = _NATIVE_SOCKET(family, socktype, protocol)
        try:
            candidate.settimeout(timeout)
            candidate.connect(socket_address)
            return candidate
        except OSError as exc:
            last_error = exc
            candidate.close()
    if last_error is not None:
        raise last_error
    raise OSError("connector proxy address unavailable")


class ConnectorTcpProxyConfigurationError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class ConnectorTcpProxyError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def _normalize_host(raw_host: str) -> str:
    candidate = raw_host.strip().rstrip(".").lower()
    if not candidate:
        raise ConnectorTcpProxyConfigurationError(
            "connector.egress_proxy_endpoint_invalid"
        )
    try:
        return candidate.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ConnectorTcpProxyConfigurationError(
            "connector.egress_proxy_endpoint_invalid"
        ) from exc


def _is_internal_proxy_host(host: str) -> bool:
    if host == "localhost" or host.endswith(".localhost"):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        labels = host.split(".")
        if not labels or any(
            not _DNS_LABEL_PATTERN.fullmatch(label) for label in labels
        ):
            return False
        if len(labels) == 1:
            return not host.isdigit() and re.fullmatch(r"0x[0-9a-f]+", host) is None
        return host.endswith(".svc") or host.endswith(".svc.cluster.local")
    return any(address in network for network in _PRIVATE_PROXY_NETWORKS)


def _allowed_proxy_hosts(raw_hosts: str) -> tuple[str, ...]:
    try:
        hosts = tuple(
            dict.fromkeys(
                _normalize_host(value)
                for value in raw_hosts.split(",")
                if value.strip()
            )
        )
    except ConnectorTcpProxyConfigurationError as exc:
        raise ConnectorTcpProxyConfigurationError(
            "connector.egress_proxy_host_not_allowed"
        ) from exc
    if not hosts or any(not _is_internal_proxy_host(host) for host in hosts):
        raise ConnectorTcpProxyConfigurationError(
            "connector.egress_proxy_host_not_allowed"
        )
    return hosts


def _allowed_target_ports(raw_ports: str) -> frozenset[int]:
    values = raw_ports.split(",")
    try:
        ports = frozenset(int(value.strip()) for value in values if value.strip())
    except ValueError as exc:
        raise ConnectorTcpProxyConfigurationError(
            "connector.egress_target_ports_invalid"
        ) from exc
    if (
        not ports
        or len(values) != len(ports)
        or len(ports) > _MAX_ALLOWED_TARGET_PORTS
        or any(not 1 <= port <= 65535 for port in ports)
    ):
        raise ConnectorTcpProxyConfigurationError(
            "connector.egress_target_ports_invalid"
        )
    return ports


@dataclass(frozen=True, slots=True)
class ConnectorTcpProxyPolicy:
    proxy_host: str
    proxy_port: int
    allowed_target_ports: frozenset[int]
    policy_revision: str


def _proxy_policy_from_environment(
    environ: Mapping[str, str],
) -> ConnectorTcpProxyPolicy | None:
    raw_url = str(environ.get("CONNECTOR_EGRESS_PROXY_URL", "")).strip()
    node_env = str(environ.get("NODE_ENV", "development")).strip().lower()
    if not raw_url:
        if node_env == "production":
            raise ConnectorTcpProxyConfigurationError(
                "connector.egress_proxy_required"
            )
        return None

    revision = str(environ.get("CONNECTOR_EGRESS_POLICY_REVISION", "")).strip()
    if revision != CONNECTOR_PROXY_POLICY_REVISION:
        raise ConnectorTcpProxyConfigurationError(
            "connector.egress_proxy_revision_invalid"
        )
    allowed_hosts = _allowed_proxy_hosts(
        str(environ.get("CONNECTOR_EGRESS_PROXY_ALLOWED_HOSTS", ""))
    )
    try:
        parsed = urlsplit(raw_url)
        proxy_port = parsed.port
    except ValueError as exc:
        raise ConnectorTcpProxyConfigurationError(
            "connector.egress_proxy_endpoint_invalid"
        ) from exc
    if (
        parsed.scheme.lower() != "http"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or proxy_port != CONNECTOR_PROXY_PORT
    ):
        raise ConnectorTcpProxyConfigurationError(
            "connector.egress_proxy_endpoint_invalid"
        )
    proxy_host = _normalize_host(parsed.hostname)
    if proxy_host not in allowed_hosts or not _is_internal_proxy_host(proxy_host):
        raise ConnectorTcpProxyConfigurationError(
            "connector.egress_proxy_host_not_allowed"
        )
    allowed_ports = _allowed_target_ports(
        str(
            environ.get(
                "CONNECTOR_EGRESS_ALLOWED_PORTS",
                ",".join(str(port) for port in sorted(_DEFAULT_ALLOWED_TARGET_PORTS)),
            )
        )
    )
    return ConnectorTcpProxyPolicy(
        proxy_host=proxy_host,
        proxy_port=proxy_port,
        allowed_target_ports=allowed_ports,
        policy_revision=revision,
    )


class HttpConnectProxyDialer:
    def __init__(self, policy: ConnectorTcpProxyPolicy) -> None:
        self._policy = policy

    @property
    def allowed_target_ports(self) -> frozenset[int]:
        return self._policy.allowed_target_ports

    def open_tunnel(
        self,
        target_address: str,
        target_port: int,
        *,
        timeout_seconds: float,
    ) -> socket.socket:
        return self._open_tunnel(
            target_address,
            target_port,
            timeout_seconds=timeout_seconds,
            create_connection=socket.create_connection,
        )

    def open_native_tunnel(
        self,
        target_address: str,
        target_port: int,
        *,
        timeout_seconds: float,
    ) -> socket.socket:
        return self._open_tunnel(
            target_address,
            target_port,
            timeout_seconds=timeout_seconds,
            create_connection=_native_create_connection,
        )

    def _open_tunnel(
        self,
        target_address: str,
        target_port: int,
        *,
        timeout_seconds: float,
        create_connection: Callable[..., socket.socket],
    ) -> socket.socket:
        try:
            address = ipaddress.ip_address(target_address)
        except ValueError as exc:
            raise ConnectorTcpProxyError("connector.egress_target_not_allowed") from exc
        if (
            (
                isinstance(address, ipaddress.IPv6Address)
                and address.ipv4_mapped is not None
            )
            or not address.is_global
            or target_port not in self._policy.allowed_target_ports
        ):
            raise ConnectorTcpProxyError("connector.egress_target_not_allowed")

        proxy_socket: socket.socket | None = None
        try:
            proxy_socket = create_connection(
                (self._policy.proxy_host, self._policy.proxy_port),
                timeout=timeout_seconds,
            )
            proxy_socket.settimeout(timeout_seconds)
            address_text = f"[{address}]" if address.version == 6 else str(address)
            authority = f"{address_text}:{target_port}"
            proxy_socket.sendall(
                (
                    f"CONNECT {authority} HTTP/1.1\r\n"
                    f"Host: {authority}\r\n\r\n"
                ).encode("ascii")
            )
            response = bytearray()
            while b"\r\n\r\n" not in response:
                if len(response) >= _MAX_PROXY_RESPONSE_BYTES:
                    raise ConnectorTcpProxyError(
                        "connector.egress_proxy_unavailable"
                    )
                # Read only the CONNECT header. SSH servers can send their banner
                # immediately after Squid's response; over-reading here would drop
                # those first tunneled bytes before the consumer sees the socket.
                chunk = proxy_socket.recv(1)
                if not chunk:
                    raise ConnectorTcpProxyError(
                        "connector.egress_proxy_unavailable"
                    )
                response.extend(chunk)
            status_line = bytes(response).split(b"\r\n", 1)[0]
            status_parts = status_line.split(b" ", 2)
            if (
                len(status_parts) < 2
                or status_parts[0] not in {b"HTTP/1.0", b"HTTP/1.1"}
                or status_parts[1] != b"200"
            ):
                raise ConnectorTcpProxyError("connector.egress_proxy_unavailable")
            return proxy_socket
        except ConnectorTcpProxyError:
            if proxy_socket is not None:
                proxy_socket.close()
            raise
        except Exception as exc:
            if proxy_socket is not None:
                proxy_socket.close()
            raise ConnectorTcpProxyError("connector.egress_proxy_unavailable") from exc


def connector_tcp_proxy_dialer_from_environment(
    environ: Mapping[str, str] | None = None,
) -> HttpConnectProxyDialer | None:
    environment = os.environ if environ is None else environ
    policy = _proxy_policy_from_environment(environment)
    return None if policy is None else HttpConnectProxyDialer(policy)


def require_connector_tcp_proxy_security_ready(
    environ: Mapping[str, str] | None = None,
) -> None:
    connector_tcp_proxy_dialer_from_environment(environ)


class LocalConnectorProxyRelay:
    def __init__(
        self,
        dialer: HttpConnectProxyDialer,
        *,
        target_address: str,
        target_port: int,
        connect_timeout_seconds: float,
    ) -> None:
        self._dialer = dialer
        self._target_address = target_address
        self._target_port = target_port
        self._connect_timeout_seconds = connect_timeout_seconds
        self._stop_event = _NativeEvent()
        self._listener: socket.socket | None = None
        self._accept_thread: _NativeThread | None = None
        self._workers: list[_NativeThread] = []
        self._active_sockets: set[socket.socket] = set()
        self._lock = _NATIVE_ALLOCATE_LOCK()

    @property
    def local_bind_port(self) -> int:
        if self._listener is None:
            raise RuntimeError("connector relay is not started")
        return int(self._listener.getsockname()[1])

    def start(self) -> None:
        if self._listener is not None:
            raise RuntimeError("connector relay is already started")
        listener = _NATIVE_SOCKET(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(16)
        listener.settimeout(0.2)
        self._listener = listener
        self._accept_thread = _NativeThread(self._accept_loop)
        self._accept_thread.start()

    def _accept_loop(self) -> None:
        listener = self._listener
        if listener is None:
            return
        while not self._stop_event.is_set():
            try:
                client, _address = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            worker = _NativeThread(partial(self._serve_client, client))
            with self._lock:
                self._workers.append(worker)
            worker.start()

    def _serve_client(self, client: socket.socket) -> None:
        upstream: socket.socket | None = None
        self._track_socket(client)
        try:
            upstream = self._dialer.open_native_tunnel(
                self._target_address,
                self._target_port,
                timeout_seconds=self._connect_timeout_seconds,
            )
            self._track_socket(upstream)
            client.setblocking(False)
            upstream.setblocking(False)
            peers = {client: upstream, upstream: client}
            while not self._stop_event.is_set():
                readable, _, _ = _NATIVE_SELECT(tuple(peers), (), (), 0.2)
                for source in readable:
                    try:
                        data = source.recv(64 * 1024)
                    except BlockingIOError:
                        continue
                    if not data:
                        return
                    destination = peers[source]
                    destination.settimeout(self._connect_timeout_seconds)
                    try:
                        destination.sendall(data)
                    finally:
                        destination.setblocking(False)
        except Exception:
            return
        finally:
            self._close_tracked_socket(client)
            if upstream is not None:
                self._close_tracked_socket(upstream)

    def _track_socket(self, value: socket.socket) -> None:
        with self._lock:
            self._active_sockets.add(value)

    def _close_tracked_socket(self, value: socket.socket) -> None:
        with self._lock:
            self._active_sockets.discard(value)
        try:
            value.close()
        except OSError:
            pass

    def stop(self) -> None:
        self._stop_event.set()
        listener, self._listener = self._listener, None
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
        with self._lock:
            active = tuple(self._active_sockets)
        for value in active:
            self._close_tracked_socket(value)
        if self._accept_thread is not None:
            self._accept_thread.join(timeout=1.0)
            self._accept_thread = None
        with self._lock:
            workers = tuple(self._workers)
            self._workers.clear()
        for worker in workers:
            worker.join(timeout=1.0)


__all__ = [
    "CONNECTOR_PROXY_POLICY_REVISION",
    "CONNECTOR_PROXY_PORT",
    "ConnectorTcpProxyConfigurationError",
    "ConnectorTcpProxyError",
    "HttpConnectProxyDialer",
    "LocalConnectorProxyRelay",
    "connector_tcp_proxy_dialer_from_environment",
    "require_connector_tcp_proxy_security_ready",
]
