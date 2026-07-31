from types import SimpleNamespace

import pytest

from apps.gateway.services.auth_return_service import (
    DEFAULT_AUTH_RETURN_PATH,
    AuthReturnService,
)


def _request(*, host: str = "gateway.example"):
    return SimpleNamespace(session={}, headers={"host": host})


def test_safe_auth_return_path_preserves_path_query_and_fragment():
    value = "/modules/workflow/run?deploymentId=deployment-1&tab=history#result"

    assert AuthReturnService.resolve_path(value) == value


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "dashboard",
        "https://attacker.example/path",
        "//attacker.example/path",
        "/\\attacker.example/path",
        "/%5c%5cattacker.example/path",
        "/%2f%2fattacker.example/path",
        "/%252f%252fattacker.example/path",
        "/%25252f%25252fattacker.example/path",
        "/%2525252525252f%2525252525252fattacker.example/path",
        "/%2e%2e//attacker.example/path",
        "/safe/../attacker",
        "/safe\nheader",
        "/safe%0d%0aheader",
        "/malformed%escape",
        "/" + "x" * 2048,
    ],
)
def test_unsafe_auth_return_path_falls_back(value):
    assert AuthReturnService.resolve_path(value) == DEFAULT_AUTH_RETURN_PATH


def test_oauth_return_path_is_consumed_once():
    request = _request()
    path = "/modules/workflow/run?deploymentId=deployment-1"

    assert AuthReturnService.remember(request, path, now=1_000) == path
    assert AuthReturnService.consume(request, now=1_001) == path
    assert AuthReturnService.consume(request, now=1_002) == DEFAULT_AUTH_RETURN_PATH


@pytest.mark.parametrize(
    ("issued_at", "consumed_at"),
    [
        (1_000, 1_601),
        (1_001, 1_000),
    ],
)
def test_expired_or_future_oauth_return_context_falls_back(
    issued_at,
    consumed_at,
):
    request = _request()
    AuthReturnService.remember(
        request,
        "/modules/workflow/run",
        now=issued_at,
    )

    assert (
        AuthReturnService.consume(request, now=consumed_at)
        == DEFAULT_AUTH_RETURN_PATH
    )


def test_local_gateway_redirects_to_matching_client_origin(monkeypatch):
    monkeypatch.delenv("AUTH_FRONTEND_ORIGIN", raising=False)

    assert AuthReturnService.build_client_redirect(
        _request(host="127.0.0.1:8000"),
        "/modules/workflow/run",
    ) == "http://127.0.0.1:3000/modules/workflow/run"


def test_configured_frontend_origin_is_server_owned(monkeypatch):
    monkeypatch.setenv("AUTH_FRONTEND_ORIGIN", "https://client.example")

    assert AuthReturnService.build_client_redirect(
        _request(),
        "/modules/workflow/run?deploymentId=deployment-1",
    ) == "https://client.example/modules/workflow/run?deploymentId=deployment-1"


@pytest.mark.parametrize(
    "origin",
    [
        "//client.example",
        "javascript:alert(1)",
        "https://user@client.example",
        "https://client.example/path",
        "https://client.example?query=1",
    ],
)
def test_invalid_configured_frontend_origin_fails_closed(monkeypatch, origin):
    monkeypatch.setenv("AUTH_FRONTEND_ORIGIN", origin)

    with pytest.raises(RuntimeError, match=r"HTTP\(S\) origin"):
        AuthReturnService.build_client_redirect(_request(), "/dashboard")


def test_lookalike_local_gateway_host_does_not_get_local_client_redirect(
    monkeypatch,
):
    monkeypatch.delenv("AUTH_FRONTEND_ORIGIN", raising=False)

    assert AuthReturnService.build_client_redirect(
        _request(host="localhost:8000.attacker.example"),
        "/dashboard",
    ) == "/dashboard"
