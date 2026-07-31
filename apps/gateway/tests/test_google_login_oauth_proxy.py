from __future__ import annotations

import asyncio

from apps.gateway.auth.oauth import build_google_login_oauth_registry
from apps.shared.services.guarded_http_transport import GuardedAsyncHttpTransport


def test_google_login_oauth_uses_fresh_guarded_explicit_proxy_transport(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OUTBOUND_TRANSPORT_MODE", "proxy_guarded_external")
    monkeypatch.setenv("OUTBOUND_PROXY_URL", "http://proxy:3128")
    monkeypatch.setenv("OUTBOUND_PROXY_ALLOWED_HOSTS", "proxy")
    monkeypatch.setenv("OUTBOUND_PROXY_POLICY_REVISION", "proxy-v1")
    monkeypatch.setenv("HTTPS_PROXY", "http://ambient.invalid:9999")

    registry = build_google_login_oauth_registry()
    first = registry.google._get_oauth_client()
    second = registry.google._get_oauth_client()
    try:
        assert isinstance(first._transport, GuardedAsyncHttpTransport)
        assert isinstance(second._transport, GuardedAsyncHttpTransport)
        assert first._transport is not second._transport
        assert first._trust_env is False
        assert first._transport._transport_policy.proxy_url == "http://proxy:3128"
        assert first._transport._operation.allowed_origins == frozenset(
            {
                "https://accounts.google.com",
                "https://oauth2.googleapis.com",
                "https://openidconnect.googleapis.com",
                "https://www.googleapis.com",
            }
        )
    finally:
        asyncio.run(first.aclose())
        asyncio.run(second.aclose())
