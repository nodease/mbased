from starlette.requests import Request

from apps.gateway.adapters.authentication.client_network import ClientNetworkResolver


def _request(peer: str, **headers: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/auth/login",
            "headers": [
                (name.lower().encode("ascii"), value.encode("ascii"))
                for name, value in headers.items()
            ],
            "client": (peer, 12345),
            "server": ("testserver", 80),
            "scheme": "http",
            "query_string": b"",
        }
    )


def test_untrusted_peer_cannot_spoof_forwarded_network():
    resolver = ClientNetworkResolver(trusted_proxy_cidrs=("10.0.0.0/8",))

    assert (
        resolver.resolve(_request("198.51.100.17", **{"X-Forwarded-For": "203.0.113.9"}))
        == "198.51.100.0/24"
    )


def test_trusted_proxy_uses_first_untrusted_hop_from_right():
    resolver = ClientNetworkResolver(
        trusted_proxy_cidrs=("10.0.0.0/8", "192.0.2.0/24")
    )
    request = _request(
        "10.0.0.5",
        **{"X-Forwarded-For": "203.0.113.44, 192.0.2.10, 10.0.0.4"},
    )

    assert resolver.resolve(request) == "203.0.113.0/24"


def test_malformed_forwarded_chain_falls_back_to_peer():
    resolver = ClientNetworkResolver(trusted_proxy_cidrs=("10.0.0.0/8",))

    assert (
        resolver.resolve(
            _request("10.0.0.5", **{"X-Forwarded-For": "203.0.113.9, invalid"})
        )
        == "10.0.0.0/24"
    )


def test_ip_network_normalization_preserves_loopback_and_ipv6_prefix():
    resolver = ClientNetworkResolver()

    assert resolver.resolve(_request("127.0.0.1")) == "127.0.0.1"
    assert resolver.resolve(_request("::1")) == "::1"
    assert resolver.resolve(_request("2001:db8:1:2:3:4:5:6")) == "2001:db8:1:2::/64"


def test_non_ip_test_peer_uses_stable_unknown_identity():
    assert ClientNetworkResolver().resolve(_request("testclient")) == "unknown"
