import asyncio

import pytest
from uvicorn.protocols.utils import get_path_with_query_string

from apps.gateway.middleware.webhook_query_redaction import (
    CONNECTOR_TEST_QUERY_PRESENT_STATE_KEY,
    WEBHOOK_QUERY_TOKEN_PRESENT_STATE_KEY,
    WebhookQueryRedactionMiddleware,
)


def run_middleware(*, path: str, query_string: bytes) -> dict[str, object]:
    observed: dict[str, object] = {}

    async def downstream(scope, receive, send) -> None:
        observed["query_string"] = scope["query_string"]
        observed["state"] = dict(scope.get("state", {}))
        observed["access_path"] = get_path_with_query_string(scope)
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message) -> None:
        return None

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": query_string,
        "headers": [],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }

    asyncio.run(WebhookQueryRedactionMiddleware(downstream)(scope, receive, send))
    observed["final_query_string"] = scope["query_string"]
    return observed


@pytest.mark.parametrize(
    ("query_string", "expected"),
    [
        (b"token=secret", b""),
        (b"token", b""),
        (b"token=&provider=jira", b"provider=jira"),
        (b"provider=jira&token=secret", b"provider=jira"),
        (b"token=one&token=two&provider=jira", b"provider=jira"),
        (b"t%6fken=secret&capture_id=nonce", b"capture_id=nonce"),
    ],
)
def test_webhook_token_query_is_removed_before_downstream_and_access_log(
    query_string: bytes,
    expected: bytes,
) -> None:
    observed = run_middleware(
        path="/api/v1/hooks/webhook-slug",
        query_string=query_string,
    )

    assert observed["query_string"] == expected
    assert observed["final_query_string"] == expected
    assert observed["state"] == {WEBHOOK_QUERY_TOKEN_PRESENT_STATE_KEY: True}
    expected_suffix = f"?{expected.decode('ascii')}" if expected else ""
    assert observed["access_path"] == (
        f"/api/v1/hooks/webhook-slug{expected_suffix}"
    )
    assert "secret" not in str(observed["access_path"])


def test_webhook_non_secret_query_is_preserved_without_marker() -> None:
    observed = run_middleware(
        path="/api/v1/hooks/webhook-slug/capture/status",
        query_string=b"capture_id=nonce&provider=jira",
    )

    assert observed["query_string"] == b"capture_id=nonce&provider=jira"
    assert observed["state"] == {}


def test_non_webhook_query_is_not_modified() -> None:
    observed = run_middleware(
        path="/api/v1/run/app-slug",
        query_string=b"token=not-a-webhook-secret",
    )

    assert observed["query_string"] == b"token=not-a-webhook-secret"
    assert observed["state"] == {}


@pytest.mark.parametrize(
    "path",
    ["/api/v1/connectors/test", "/api/v1/connectors/test/"],
)
def test_connector_test_query_is_removed_before_downstream_and_access_log(
    path: str,
) -> None:
    observed = run_middleware(
        path=path,
        query_string=b"password=must-not-reach-access-log&token=value",
    )

    assert observed["query_string"] == b""
    assert observed["final_query_string"] == b""
    assert observed["state"] == {CONNECTOR_TEST_QUERY_PRESENT_STATE_KEY: True}
    assert observed["access_path"] == path
    assert "password" not in str(observed["access_path"])


def test_connector_test_child_path_query_is_not_modified() -> None:
    observed = run_middleware(
        path="/api/v1/connectors/test/status",
        query_string=b"page=1",
    )

    assert observed["query_string"] == b"page=1"
    assert observed["state"] == {}


def test_gateway_registers_query_redaction_as_outermost_user_middleware() -> None:
    from apps.gateway.main import app

    assert app.user_middleware[0].cls is WebhookQueryRedactionMiddleware
