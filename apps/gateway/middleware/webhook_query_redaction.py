from urllib.parse import unquote_to_bytes

from starlette.types import ASGIApp, Receive, Scope, Send


WEBHOOK_QUERY_TOKEN_PRESENT_STATE_KEY = "webhook_query_token_present"
CONNECTOR_TEST_QUERY_PRESENT_STATE_KEY = "connector_test_query_present"
_WEBHOOK_PATH_PREFIX = "/api/v1/hooks"
_CONNECTOR_TEST_PATH = "/api/v1/connectors/test"


def _is_token_query_field(field: bytes) -> bool:
    raw_key = field.partition(b"=")[0].replace(b"+", b" ")
    try:
        return unquote_to_bytes(raw_key) == b"token"
    except Exception:
        return False


def webhook_query_token_present(raw_query: bytes) -> bool:
    return any(_is_token_query_field(field) for field in raw_query.split(b"&"))


def redact_webhook_token_query(raw_query: bytes) -> tuple[bytes, bool]:
    retained_fields: list[bytes] = []
    token_present = False

    for field in raw_query.split(b"&"):
        if _is_token_query_field(field):
            token_present = True
        else:
            retained_fields.append(field)

    if not token_present:
        return raw_query, False
    return b"&".join(retained_fields), True


class WebhookQueryRedactionMiddleware:
    """Remove sensitive query data before downstream and access logging."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if self._is_webhook_path(path):
            sanitized_query, token_present = redact_webhook_token_query(
                scope.get("query_string", b"")
            )
            if token_present:
                scope["query_string"] = sanitized_query
                state = scope.setdefault("state", {})
                state[WEBHOOK_QUERY_TOKEN_PRESENT_STATE_KEY] = True
        elif self._is_connector_test_path(path) and scope.get("query_string", b""):
            scope["query_string"] = b""
            state = scope.setdefault("state", {})
            state[CONNECTOR_TEST_QUERY_PRESENT_STATE_KEY] = True

        await self.app(scope, receive, send)

    @staticmethod
    def _is_webhook_path(path: str) -> bool:
        return path == _WEBHOOK_PATH_PREFIX or path.startswith(
            f"{_WEBHOOK_PATH_PREFIX}/"
        )

    @staticmethod
    def _is_connector_test_path(path: str) -> bool:
        return path in {_CONNECTOR_TEST_PATH, f"{_CONNECTOR_TEST_PATH}/"}


__all__ = [
    "CONNECTOR_TEST_QUERY_PRESENT_STATE_KEY",
    "WEBHOOK_QUERY_TOKEN_PRESENT_STATE_KEY",
    "WebhookQueryRedactionMiddleware",
    "redact_webhook_token_query",
    "webhook_query_token_present",
]
