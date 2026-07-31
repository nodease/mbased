from __future__ import annotations

import socket
import threading
import time
from dataclasses import replace

import httpcore
import httpx
import pytest
from apps.shared.services.egress_guard import EgressGuardError
from apps.shared.services.guarded_http_transport import (
    EgressResponseRejectedError,
    GuardedAsyncHttpTransport,
    GuardedAsyncNetworkBackend,
    GuardedHttpTransport,
    GuardedNetworkBackend,
)
from apps.shared.services.outbound_operation_policy import (
    LLM_PROVIDER_CALL,
    require_outbound_operation_profile,
)
from apps.shared.services.outbound_proxy_policy import (
    OutboundProxyPolicy,
    OutboundTransportMode,
)


def _address(ip: str, port: int = 443):
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    endpoint = (ip, port, 0, 0) if family == socket.AF_INET6 else (ip, port)
    return (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", endpoint)


class _SyncStream(httpcore.NetworkStream):
    def __init__(self, peer_ip: str) -> None:
        self.peer_ip = peer_ip
        self.closed = False

    def read(self, max_bytes, timeout=None):
        return b""

    def write(self, buffer, timeout=None):
        return None

    def close(self):
        self.closed = True

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        return self

    def get_extra_info(self, info):
        return (self.peer_ip, 443) if info == "server_addr" else None


class _SyncBackend(httpcore.NetworkBackend):
    def __init__(self, peer_ip: str) -> None:
        self.peer_ip = peer_ip
        self.targets: list[tuple[str, int]] = []

    def connect_tcp(self, host, port, **_kwargs):
        self.targets.append((host, port))
        return _SyncStream(self.peer_ip)

    def connect_unix_socket(self, path, **_kwargs):
        raise AssertionError("unix socket must not be used")


class _AsyncStream(httpcore.AsyncNetworkStream):
    def __init__(self, peer_ip: str) -> None:
        self.peer_ip = peer_ip
        self.closed = False

    async def read(self, max_bytes, timeout=None):
        return b""

    async def write(self, buffer, timeout=None):
        return None

    async def aclose(self):
        self.closed = True

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        return self

    def get_extra_info(self, info):
        return (self.peer_ip, 443) if info == "server_addr" else None


class _AsyncResponseStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b"{}"

    async def aclose(self) -> None:
        return None


class _AsyncBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, peer_ip: str) -> None:
        self.peer_ip = peer_ip
        self.targets: list[tuple[str, int]] = []

    async def connect_tcp(self, host, port, **_kwargs):
        self.targets.append((host, port))
        return _AsyncStream(self.peer_ip)

    async def connect_unix_socket(self, path, **_kwargs):
        raise AssertionError("unix socket must not be used")

    async def sleep(self, seconds):
        return None


def _bound_operation():
    return require_outbound_operation_profile(LLM_PROVIDER_CALL).bind(
        "https://provider.example/v1"
    )


def test_sync_backend_dials_only_the_validated_address(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [_address("93.184.216.34")],
    )
    backend = _SyncBackend("93.184.216.34")
    guarded = GuardedNetworkBackend(_bound_operation().guard, backend=backend)

    guarded.connect_tcp("provider.example", 443)

    assert backend.targets == [("93.184.216.34", 443)]


def test_sync_backend_rejects_peer_that_differs_from_validated_address(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [_address("93.184.216.34")],
    )
    backend = _SyncBackend("93.184.216.35")
    guarded = GuardedNetworkBackend(_bound_operation().guard, backend=backend)

    with pytest.raises(EgressGuardError) as captured:
        guarded.connect_tcp("provider.example", 443)

    assert captured.value.reason_code == "egress.peer_mismatch"
    assert backend.targets == [("93.184.216.34", 443)]


def test_sync_backend_applies_connect_timeout_to_dns_resolution(monkeypatch) -> None:
    guard = _bound_operation().guard

    def slow_resolve(_host: str, _port: int):
        time.sleep(0.05)
        return "provider.example", 443, ("93.184.216.34",)

    monkeypatch.setattr(guard, "validate_host_port_addresses", slow_resolve)
    backend = _SyncBackend("93.184.216.34")
    guarded = GuardedNetworkBackend(guard, backend=backend)

    with pytest.raises(httpcore.ConnectTimeout):
        guarded.connect_tcp("provider.example", 443, timeout=0.005)

    assert backend.targets == []


def test_sync_backend_passes_only_remaining_deadline_to_tcp(monkeypatch) -> None:
    guard = _bound_operation().guard
    observed_timeouts: list[float | None] = []

    def resolve(_host: str, _port: int):
        time.sleep(0.02)
        return "provider.example", 443, ("93.184.216.34",)

    class RecordingBackend(_SyncBackend):
        def connect_tcp(self, host, port, **kwargs):
            observed_timeouts.append(kwargs.get("timeout"))
            return super().connect_tcp(host, port, **kwargs)

    monkeypatch.setattr(guard, "validate_host_port_addresses", resolve)
    backend = RecordingBackend("93.184.216.34")
    guarded = GuardedNetworkBackend(guard, backend=backend)

    guarded.connect_tcp("provider.example", 443, timeout=0.2)

    assert observed_timeouts
    assert observed_timeouts[0] is not None
    assert 0 < observed_timeouts[0] < 0.2


@pytest.mark.asyncio
async def test_async_backend_dials_only_the_validated_address(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [_address("93.184.216.34")],
    )
    backend = _AsyncBackend("93.184.216.34")
    guarded = GuardedAsyncNetworkBackend(_bound_operation().guard, backend=backend)

    await guarded.connect_tcp("provider.example", 443)

    assert backend.targets == [("93.184.216.34", 443)]


@pytest.mark.asyncio
async def test_async_backend_resolves_dns_outside_the_event_loop_thread(
    monkeypatch,
) -> None:
    event_loop_thread = threading.get_ident()
    resolver_threads: list[int] = []
    guard = _bound_operation().guard

    def resolve(_host: str, _port: int):
        resolver_threads.append(threading.get_ident())
        return "provider.example", 443, ("93.184.216.34",)

    monkeypatch.setattr(guard, "validate_host_port_addresses", resolve)
    backend = _AsyncBackend("93.184.216.34")
    guarded = GuardedAsyncNetworkBackend(guard, backend=backend)

    await guarded.connect_tcp("provider.example", 443, timeout=1.0)

    assert resolver_threads
    assert resolver_threads[0] != event_loop_thread
    assert backend.targets == [("93.184.216.34", 443)]


@pytest.mark.asyncio
async def test_async_backend_applies_connect_timeout_to_dns_resolution(
    monkeypatch,
) -> None:
    guard = _bound_operation().guard

    def slow_resolve(_host: str, _port: int):
        time.sleep(0.05)
        return "provider.example", 443, ("93.184.216.34",)

    monkeypatch.setattr(guard, "validate_host_port_addresses", slow_resolve)
    backend = _AsyncBackend("93.184.216.34")
    guarded = GuardedAsyncNetworkBackend(guard, backend=backend)

    with pytest.raises(httpcore.ConnectTimeout):
        await guarded.connect_tcp("provider.example", 443, timeout=0.005)

    assert backend.targets == []


@pytest.mark.asyncio
async def test_async_transport_defers_dns_to_guarded_network_backend(
    monkeypatch,
) -> None:
    transport = GuardedAsyncHttpTransport(operation=_bound_operation())
    request = httpx.Request(
        "POST",
        "https://provider.example/v1/responses",
        json={"input": "synthetic"},
    )
    pool_calls: list[httpx.Request] = []

    def unexpected_dns(*_args, **_kwargs):
        pytest.fail("request preflight must not resolve DNS on the event loop")

    async def handle_request(_self, received_request):
        pool_calls.append(received_request)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            stream=_AsyncResponseStream(),
            request=received_request,
        )

    monkeypatch.setattr(socket, "getaddrinfo", unexpected_dns)
    monkeypatch.setattr(
        httpx.AsyncHTTPTransport,
        "handle_async_request",
        handle_request,
    )

    response = await transport.handle_async_request(request)

    assert pool_calls == [request]
    await response.aclose()
    await transport.aclose()


def test_bound_transport_rejects_another_origin_before_pool_call(monkeypatch) -> None:
    transport = GuardedHttpTransport(operation=_bound_operation())
    monkeypatch.setattr(
        transport._pool,
        "handle_request",
        lambda *_args, **_kwargs: pytest.fail("network pool must not be called"),
    )

    request = httpx.Request(
        "POST",
        "https://unapproved.example/v1/responses",
        json={"input": "synthetic"},
    )
    with pytest.raises(EgressGuardError) as captured:
        transport.handle_request(request)

    assert captured.value.reason_code == "egress.origin_not_allowed"
    transport.close()


def test_bound_transport_rejects_host_header_override_before_pool_call(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [_address("93.184.216.34")],
    )
    transport = GuardedHttpTransport(operation=_bound_operation())
    monkeypatch.setattr(
        transport._pool,
        "handle_request",
        lambda *_args, **_kwargs: pytest.fail("network pool must not be called"),
    )

    request = httpx.Request(
        "POST",
        "https://provider.example/v1/responses",
        headers={"Host": "unapproved.example"},
        json={"input": "synthetic"},
    )
    with pytest.raises(EgressGuardError) as captured:
        transport.handle_request(request)

    assert captured.value.reason_code == "egress.host_header_mismatch"
    transport.close()


def test_sync_transport_marks_response_header_rejection_after_send(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [_address("93.184.216.34")],
    )
    transport = GuardedHttpTransport(operation=_bound_operation())
    request = httpx.Request(
        "POST",
        "https://provider.example/v1/responses",
        json={"input": "synthetic"},
    )
    response = httpx.Response(
        200,
        headers={
            "content-encoding": "gzip",
            "content-type": "application/json",
        },
        stream=httpx.ByteStream(b"synthetic"),
        request=request,
    )
    monkeypatch.setattr(
        httpx.HTTPTransport,
        "handle_request",
        lambda _self, _request: response,
    )

    with pytest.raises(EgressResponseRejectedError) as captured:
        transport.handle_request(request)

    assert captured.value.reason_code == "egress.compressed_response_not_allowed"
    assert response.is_closed is True
    transport.close()


def test_sync_transport_marks_response_body_limit_after_send(monkeypatch) -> None:
    transport = GuardedHttpTransport(operation=_bound_operation())
    transport._guard.policy = replace(  # noqa: SLF001 - transport contract fixture
        transport._guard.policy,  # noqa: SLF001
        max_response_bytes=4,
    )
    request = httpx.Request(
        "POST",
        "https://provider.example/v1/responses",
        json={"input": "synthetic"},
    )
    response = httpx.Response(
        200,
        headers={"content-type": "application/json"},
        stream=httpx.ByteStream(b"12345"),
        request=request,
    )
    monkeypatch.setattr(
        httpx.HTTPTransport,
        "handle_request",
        lambda _self, _request: response,
    )

    guarded_response = transport.handle_request(request)
    with pytest.raises(EgressResponseRejectedError) as captured:
        guarded_response.read()

    assert captured.value.reason_code == "egress.response_too_large"
    transport.close()


@pytest.mark.asyncio
async def test_async_transport_marks_response_body_limit_after_send(
    monkeypatch,
) -> None:
    class OversizedAsyncStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"12345"

        async def aclose(self) -> None:
            return None

    transport = GuardedAsyncHttpTransport(operation=_bound_operation())
    transport._guard.policy = replace(  # noqa: SLF001 - transport contract fixture
        transport._guard.policy,  # noqa: SLF001
        max_response_bytes=4,
    )
    request = httpx.Request(
        "POST",
        "https://provider.example/v1/responses",
        json={"input": "synthetic"},
    )
    response = httpx.Response(
        200,
        headers={"content-type": "application/json"},
        stream=OversizedAsyncStream(),
        request=request,
    )

    async def handle_request(_self, _request):
        return response

    monkeypatch.setattr(
        httpx.AsyncHTTPTransport,
        "handle_async_request",
        handle_request,
    )

    guarded_response = await transport.handle_async_request(request)
    with pytest.raises(EgressResponseRejectedError) as captured:
        await guarded_response.aread()

    assert captured.value.reason_code == "egress.response_too_large"
    await transport.aclose()


def test_sync_and_async_transports_disable_ambient_proxy_and_pin_network() -> None:
    operation = _bound_operation()
    sync_transport = GuardedHttpTransport(operation=operation)
    async_transport = GuardedAsyncHttpTransport(operation=operation)

    assert isinstance(sync_transport._pool._network_backend, GuardedNetworkBackend)
    assert isinstance(
        async_transport._pool._network_backend,
        GuardedAsyncNetworkBackend,
    )
    assert sync_transport._pool._ssl_context.check_hostname is True
    assert async_transport._pool._ssl_context.check_hostname is True

    sync_transport.close()


def _proxy_policy() -> OutboundProxyPolicy:
    return OutboundProxyPolicy(
        mode=OutboundTransportMode.PROXY_GUARDED_EXTERNAL,
        proxy_url="http://egress-proxy:3128",
        allowed_proxy_hosts=("egress-proxy",),
        policy_revision="proxy-v1",
    )


def test_proxy_transport_uses_only_explicit_proxy_and_ignores_ambient_env(
    monkeypatch,
) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://ambient.invalid:9999")
    monkeypatch.setenv("HTTPS_PROXY", "http://ambient.invalid:9999")
    monkeypatch.setenv("NO_PROXY", "*")

    transport = GuardedHttpTransport(
        operation=_bound_operation(),
        transport_policy=_proxy_policy(),
    )

    assert isinstance(transport._pool, httpcore.HTTPProxy)  # noqa: SLF001
    assert transport._pool._proxy_url.host == b"egress-proxy"  # noqa: SLF001
    assert transport._pool._proxy_url.port == 3128  # noqa: SLF001
    assert not isinstance(
        transport._pool._network_backend,  # noqa: SLF001
        GuardedNetworkBackend,
    )
    transport.close()


def test_proxy_transport_bounds_origin_dns_validation(monkeypatch) -> None:
    transport = GuardedHttpTransport(
        operation=_bound_operation(),
        transport_policy=_proxy_policy(),
    )
    transport._guard.policy = replace(  # noqa: SLF001
        transport._guard.policy,  # noqa: SLF001
        timeout_seconds=0.005,
    )

    def slow_resolve(_host: str, _port: int):
        time.sleep(0.05)
        return "provider.example", 443, ("93.184.216.34",)

    monkeypatch.setattr(
        transport._guard,  # noqa: SLF001
        "validate_host_port_addresses",
        slow_resolve,
    )
    request = httpx.Request("GET", "https://provider.example/v1/models")

    with pytest.raises(EgressGuardError) as captured:
        transport.handle_request(request)

    assert captured.value.reason_code == "egress.dns_resolution_failed"
    transport.close()


@pytest.mark.asyncio
async def test_proxy_transport_validates_origin_dns_before_proxy_send(
    monkeypatch,
) -> None:
    operation = _bound_operation()
    validated: list[tuple[str, int]] = []

    def validate(host: str, port: int):
        validated.append((host, port))
        return host, port, ("203.0.113.10",)

    transport = GuardedAsyncHttpTransport(
        operation=operation,
        transport_policy=_proxy_policy(),
    )
    monkeypatch.setattr(transport._guard, "validate_host_port_addresses", validate)
    request = httpx.Request(
        "POST",
        "https://provider.example/v1/responses",
        json={"input": "synthetic"},
    )

    async def fail_before_network(_self, _request):
        raise httpx.ConnectError("proxy unavailable")

    monkeypatch.setattr(
        httpx.AsyncHTTPTransport,
        "handle_async_request",
        fail_before_network,
    )

    with pytest.raises(httpx.ConnectError):
        await transport.handle_async_request(request)

    assert validated == [("provider.example", 443)]
    await transport.aclose()


@pytest.mark.asyncio
async def test_async_proxy_transport_bounds_origin_dns_validation(
    monkeypatch,
) -> None:
    transport = GuardedAsyncHttpTransport(
        operation=_bound_operation(),
        transport_policy=_proxy_policy(),
    )
    transport._guard.policy = replace(  # noqa: SLF001
        transport._guard.policy,  # noqa: SLF001
        timeout_seconds=0.005,
    )

    def slow_resolve(_host: str, _port: int):
        time.sleep(0.05)
        return "provider.example", 443, ("93.184.216.34",)

    monkeypatch.setattr(
        transport._guard,  # noqa: SLF001
        "validate_host_port_addresses",
        slow_resolve,
    )
    request = httpx.Request("GET", "https://provider.example/v1/models")

    with pytest.raises(EgressGuardError) as captured:
        await transport.handle_async_request(request)

    assert captured.value.reason_code == "egress.dns_resolution_failed"
    await transport.aclose()
