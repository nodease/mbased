from __future__ import annotations

import socket
from dataclasses import replace

import httpcore
import httpx
import pytest

from apps.shared.services.egress_guard import (
    EgressGuardError,
    OutboundEgressGuard,
)
from apps.shared.services.guarded_http_transport import EgressResponseRejectedError
from apps.workflow_engine.adapters.outbound_http import (
    GuardedHttpTransport,
    GuardedHttpxOutboundAdapter,
    GuardedNetworkBackend,
    generic_http_egress_policy,
)
from apps.workflow_engine.application.outbound_http import (
    OutboundHttpError,
    OutboundHttpFailurePhase,
    OutboundHttpRequest,
)


def _address(ip: str, port: int = 443):
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    endpoint = (ip, port, 0, 0) if family == socket.AF_INET6 else (ip, port)
    return (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", endpoint)


def _request(url: str = "https://example.test/items") -> OutboundHttpRequest:
    return OutboundHttpRequest(
        method="POST",
        url=url,
        headers=(("content-type", "application/json"),),
        body_mode="json",
        json_body={"value": 1},
        timeout_seconds=2.0,
    )


class _FakeStream(httpcore.NetworkStream):
    def __init__(self, peer_ip: str) -> None:
        self.peer_ip = peer_ip
        self.closed = False

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        return b""

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        return self

    def get_extra_info(self, info: str):
        if info == "server_addr":
            return (self.peer_ip, 443)
        return None


class _FakeBackend(httpcore.NetworkBackend):
    def __init__(
        self,
        peer_ip: str,
        *,
        failed_targets: frozenset[str] = frozenset(),
    ) -> None:
        self.peer_ip = peer_ip
        self.failed_targets = failed_targets
        self.targets: list[tuple[str, int]] = []
        self.streams: list[_FakeStream] = []

    def connect_tcp(self, host, port, **_kwargs):
        self.targets.append((host, port))
        if host in self.failed_targets:
            raise httpcore.ConnectError("connect failed")
        stream = _FakeStream(self.peer_ip)
        self.streams.append(stream)
        return stream

    def connect_unix_socket(self, path, **_kwargs):
        raise AssertionError("unix socket must not be used")


def test_guarded_backend_dials_the_validated_ip(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [_address("93.184.216.34")],
    )
    backend = _FakeBackend("93.184.216.34")
    guarded = GuardedNetworkBackend(
        OutboundEgressGuard(generic_http_egress_policy()),
        backend=backend,
    )

    stream = guarded.connect_tcp("example.test", 443)

    assert backend.targets == [("93.184.216.34", 443)]
    assert stream.closed is False


def test_guarded_backend_allows_and_pins_public_ipv6(monkeypatch) -> None:
    public_ipv6 = "2606:4700:4700::1111"
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [_address(public_ipv6)],
    )
    backend = _FakeBackend(public_ipv6)
    guarded = GuardedNetworkBackend(
        OutboundEgressGuard(generic_http_egress_policy()),
        backend=backend,
    )

    guarded.connect_tcp("example.test", 443)

    assert backend.targets == [(public_ipv6, 443)]


def test_guarded_backend_falls_back_across_validated_addresses(monkeypatch) -> None:
    public_ipv6 = "2606:4700:4700::1111"
    public_ipv4 = "93.184.216.34"
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            _address(public_ipv6),
            _address(public_ipv4),
        ],
    )
    backend = _FakeBackend(
        public_ipv4,
        failed_targets=frozenset({public_ipv6}),
    )
    guarded = GuardedNetworkBackend(
        OutboundEgressGuard(generic_http_egress_policy()),
        backend=backend,
    )

    stream = guarded.connect_tcp("example.test", 443)

    assert backend.targets == [(public_ipv6, 443), (public_ipv4, 443)]
    assert stream.peer_ip == public_ipv4


def test_guarded_backend_raises_after_all_validated_addresses_fail(
    monkeypatch,
) -> None:
    addresses = ("2606:4700:4700::1111", "93.184.216.34")
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [_address(address) for address in addresses],
    )
    backend = _FakeBackend(
        addresses[-1],
        failed_targets=frozenset(addresses),
    )
    guarded = GuardedNetworkBackend(
        OutboundEgressGuard(generic_http_egress_policy()),
        backend=backend,
    )

    with pytest.raises(httpcore.ConnectError):
        guarded.connect_tcp("example.test", 443)

    assert backend.targets == [(address, 443) for address in addresses]


def test_guarded_backend_closes_peer_mismatch(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            _address("93.184.216.34"),
            _address("93.184.216.35"),
        ],
    )
    backend = _FakeBackend("93.184.216.35")
    guarded = GuardedNetworkBackend(
        OutboundEgressGuard(generic_http_egress_policy()),
        backend=backend,
    )

    with pytest.raises(EgressGuardError) as captured:
        guarded.connect_tcp("example.test", 443)

    assert captured.value.reason_code == "egress.peer_mismatch"
    assert backend.targets == [("93.184.216.34", 443)]
    assert backend.streams[0].closed is True


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "100.64.0.1",
        "169.254.169.254",
        "192.0.2.1",
        "198.18.0.1",
        "224.0.0.1",
        "240.0.0.1",
        "0.0.0.0",
        "::1",
        "::ffff:8.8.8.8",
        "64:ff9b::808:808",
        "100::1",
        "2001:db8::1",
        "2002::1",
        "3fff::1",
        "5f00::1",
        "fc00::1",
        "fe80::1",
        "fec0::1",
        "ff00::1",
    ],
)
def test_private_and_metadata_targets_are_rejected_before_transport(
    monkeypatch,
    address,
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [_address(address)],
    )
    transport_built = False

    def transport_factory(_guard):
        nonlocal transport_built
        transport_built = True
        return httpx.MockTransport(lambda _request: httpx.Response(200))

    adapter = GuardedHttpxOutboundAdapter(transport_factory=transport_factory)

    with pytest.raises(OutboundHttpError) as captured:
        adapter.send(_request())

    assert captured.value.code == "invalid_prepared_request"
    assert captured.value.phase is OutboundHttpFailurePhase.BEFORE_SEND
    assert captured.value.retryable_before_send is False
    assert transport_built is False


def test_mixed_public_private_dns_result_is_fail_closed(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            _address("93.184.216.34"),
            _address("10.0.0.1"),
        ],
    )
    adapter = GuardedHttpxOutboundAdapter(
        transport_factory=lambda _guard: pytest.fail("transport must not be built")
    )

    with pytest.raises(OutboundHttpError) as captured:
        adapter.send(_request())

    assert captured.value.code == "invalid_prepared_request"


def test_redirect_is_returned_without_following_private_location(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [_address("93.184.216.34")],
    )
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            302,
            headers={"location": "http://169.254.169.254/latest/meta-data"},
            content=b"redirect",
        )

    adapter = GuardedHttpxOutboundAdapter(
        transport_factory=lambda _guard: httpx.MockTransport(handler)
    )

    response = adapter.send(_request())

    assert response.status_code == 302
    assert response.content == b"redirect"
    assert len(calls) == 1
    assert calls[0].headers["accept-encoding"] == "identity"


def test_duplicate_application_header_order_is_preserved(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [_address("93.184.216.34")],
    )
    observed: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.extend(
            (name, value)
            for name, value in request.headers.multi_items()
            if name == "x-trace"
        )
        return httpx.Response(200, content=b"{}")

    adapter = GuardedHttpxOutboundAdapter(
        transport_factory=lambda _guard: httpx.MockTransport(handler)
    )
    request = replace(
        _request(),
        headers=(("x-trace", "first"), ("x-trace", "second")),
    )

    adapter.send(request)

    assert observed == [("x-trace", "first"), ("x-trace", "second")]


def test_public_request_keeps_httpx_json_wire_shape(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [_address("93.184.216.34")],
    )
    observed: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        return httpx.Response(
            503,
            headers={"content-type": "application/json"},
            content=b'{"available":false}',
        )

    adapter = GuardedHttpxOutboundAdapter(
        transport_factory=lambda _guard: httpx.MockTransport(handler)
    )

    response = adapter.send(_request())

    assert response.status_code == 503
    assert response.json_or_text() == {"available": False}
    assert observed[0].method == "POST"
    assert observed[0].url == "https://example.test/items"
    assert observed[0].content == b'{"value":1}'
    assert observed[0].headers["content-type"] == "application/json"
    assert observed[0].headers["accept-encoding"] == "identity"


def test_response_limit_after_request_is_outcome_unknown(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [_address("93.184.216.34")],
    )
    base = generic_http_egress_policy()
    guard = OutboundEgressGuard(replace(base, max_response_bytes=4))
    adapter = GuardedHttpxOutboundAdapter(
        guard=guard,
        transport_factory=lambda _guard: httpx.MockTransport(
            lambda _request: httpx.Response(200, content=b"12345")
        ),
    )

    with pytest.raises(OutboundHttpError) as captured:
        adapter.send(_request())

    assert captured.value.code == "response_lost"
    assert captured.value.phase is OutboundHttpFailurePhase.OUTCOME_UNKNOWN


def test_transport_response_rejection_is_outcome_unknown(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [_address("93.184.216.34")],
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        raise EgressResponseRejectedError(
            "egress.compressed_response_not_allowed"
        )

    adapter = GuardedHttpxOutboundAdapter(
        transport_factory=lambda _guard: httpx.MockTransport(handler)
    )

    with pytest.raises(OutboundHttpError) as captured:
        adapter.send(_request())

    assert captured.value.code == "response_lost"
    assert captured.value.phase is OutboundHttpFailurePhase.OUTCOME_UNKNOWN


def test_userinfo_and_invalid_headers_are_safe_pre_send_failures(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [_address("93.184.216.34")],
    )
    adapter = GuardedHttpxOutboundAdapter(
        transport_factory=lambda _guard: pytest.fail("transport must not be built")
    )
    requests = [
        _request("https://user:opaque@example.test/items"),
        replace(_request(), headers=(("Host", "internal.test"),)),
        replace(_request(), headers=((" X-Trace", "value"),)),
        replace(_request(), headers=(("X-Trace", "비 ASCII 값"),)),
    ]

    for request in requests:
        with pytest.raises(OutboundHttpError) as captured:
            adapter.send(request)
        assert str(captured.value) == "invalid_prepared_request"
        assert "opaque" not in str(captured.value)
        assert captured.value.phase is OutboundHttpFailurePhase.BEFORE_SEND


def test_invalid_headers_are_rejected_before_dns_resolution(monkeypatch) -> None:
    dns_calls = 0

    def resolve(*_args, **_kwargs):
        nonlocal dns_calls
        dns_calls += 1
        return [_address("93.184.216.34")]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    adapter = GuardedHttpxOutboundAdapter(
        transport_factory=lambda _guard: pytest.fail("transport must not be built")
    )

    with pytest.raises(OutboundHttpError):
        adapter.send(replace(_request(), headers=(("Host", "internal.test"),)))

    assert dns_calls == 0


@pytest.mark.parametrize(
    "outbound_request",
    [
        _request("https://example.test/items#internal-fragment"),
        _request("https://example.test:8443/items"),
        _request("ftp://example.test/items"),
        replace(_request(), method="TRACE"),
        replace(_request(), timeout_seconds=31.0),
        replace(
            _request(),
            headers=(("Proxy-Authorization", "opaque-proxy-value"),),
        ),
    ],
)
def test_unsupported_target_and_proxy_control_header_are_rejected_before_transport(
    monkeypatch,
    outbound_request,
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [_address("93.184.216.34")],
    )
    adapter = GuardedHttpxOutboundAdapter(
        transport_factory=lambda _guard: pytest.fail("transport must not be built")
    )

    with pytest.raises(OutboundHttpError) as captured:
        adapter.send(outbound_request)

    assert captured.value.code == "invalid_prepared_request"
    assert captured.value.phase is OutboundHttpFailurePhase.BEFORE_SEND
    assert "opaque-proxy-value" not in str(captured.value)


def test_dns_failure_is_retryable_before_send(monkeypatch) -> None:
    def fail_resolution(*_args, **_kwargs):
        raise socket.gaierror("opaque resolver detail")

    monkeypatch.setattr(socket, "getaddrinfo", fail_resolution)
    adapter = GuardedHttpxOutboundAdapter(
        transport_factory=lambda _guard: pytest.fail("transport must not be built")
    )

    with pytest.raises(OutboundHttpError) as captured:
        adapter.send(_request("https://unresolved.example.test/items"))

    assert captured.value.code == "connection_failed"
    assert captured.value.phase is OutboundHttpFailurePhase.BEFORE_SEND
    assert captured.value.retryable_before_send is True
    assert "opaque" not in str(captured.value)


def test_peer_mismatch_is_non_retryable_before_send(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [_address("93.184.216.34")],
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        raise EgressGuardError("egress.peer_mismatch")

    adapter = GuardedHttpxOutboundAdapter(
        transport_factory=lambda _guard: httpx.MockTransport(handler)
    )

    with pytest.raises(OutboundHttpError) as captured:
        adapter.send(_request())

    assert captured.value.code == "invalid_prepared_request"
    assert captured.value.phase is OutboundHttpFailurePhase.BEFORE_SEND
    assert captured.value.retryable_before_send is False


def test_request_body_limit_is_enforced_before_transport(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [_address("93.184.216.34")],
    )
    guard = OutboundEgressGuard(
        replace(generic_http_egress_policy(), max_request_bytes=4)
    )
    adapter = GuardedHttpxOutboundAdapter(
        guard=guard,
        transport_factory=lambda _guard: pytest.fail("transport must not be built"),
    )

    with pytest.raises(OutboundHttpError) as captured:
        adapter.send(replace(_request(), json_body={"value": "too-large"}))

    assert captured.value.code == "invalid_prepared_request"
    assert captured.value.phase is OutboundHttpFailurePhase.BEFORE_SEND


def test_read_timeout_is_outcome_unknown(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [_address("93.184.216.34")],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("opaque timeout detail", request=request)

    adapter = GuardedHttpxOutboundAdapter(
        transport_factory=lambda _guard: httpx.MockTransport(handler)
    )

    with pytest.raises(OutboundHttpError) as captured:
        adapter.send(_request())

    assert captured.value.code == "response_lost"
    assert captured.value.phase is OutboundHttpFailurePhase.OUTCOME_UNKNOWN
    assert "opaque" not in str(captured.value)


def test_guarded_http_transport_uses_supported_httpcore_pool_shape() -> None:
    transport = GuardedHttpTransport(OutboundEgressGuard(generic_http_egress_policy()))

    assert isinstance(transport._pool._network_backend, GuardedNetworkBackend)
    assert transport._pool._ssl_context.check_hostname is True
    transport.close()
