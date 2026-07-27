from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

_PUBLIC_CONVERSATION_BOUNDARY_STATE_KEY = "nodease.public_conversation_transport"


class PublicConversationCorsBoundaryMiddleware:
    """Enforce the transport boundary for public Conversation API responses.

    Public parent origins authorize iframe embedding through CSP only.  The
    iframe document calls this API same-origin, so target lifecycle routes must
    never inherit CORS grants and every response remains non-cacheable and
    non-referring, including framework and router failures.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        is_conversation_path = _is_public_conversation_path(path)
        is_public_run_root = _is_public_run_root_path(path)
        if not is_conversation_path and not is_public_run_root:
            await self.app(scope, receive, send)
            return

        if is_conversation_path and scope["method"] == "OPTIONS":
            await send(
                {
                    "type": "http.response.start",
                    "status": 404,
                    "headers": _public_response_headers([(b"content-length", b"0")]),
                }
            )
            await send({"type": "http.response.body", "body": b""})
            return

        async def send_without_cors(message: Message) -> None:
            if message["type"] == "http.response.start" and (
                is_conversation_path or _is_marked_public_conversation_response(scope)
            ):
                message = dict(message)
                message["headers"] = _public_response_headers(
                    message.get("headers", [])
                )
            await send(message)

        await self.app(scope, receive, send_without_cors)


def mark_public_conversation_transport_boundary(scope: Scope) -> None:
    """Mark a root public-run response as owned by Conversation transport."""

    scope.setdefault("state", {})[_PUBLIC_CONVERSATION_BOUNDARY_STATE_KEY] = True


def _is_marked_public_conversation_response(scope: Scope) -> bool:
    state = scope.get("state", {})
    return bool(state.get(_PUBLIC_CONVERSATION_BOUNDARY_STATE_KEY))


def _is_public_run_root_path(path: str) -> bool:
    prefix = "/api/v1/run-public/"
    if not path.startswith(prefix):
        return False
    remainder = path[len(prefix) :]
    return bool(remainder) and "/" not in remainder


def _is_public_conversation_path(path: str) -> bool:
    prefix = "/api/v1/run-public/"
    if not path.startswith(prefix):
        return False
    remainder = path[len(prefix) :]
    _slug, separator, suffix = remainder.partition("/")
    if not _slug or not separator:
        return False
    return suffix in {
        "chat",
        "conversations",
        "conversations/",
        "conversation",
    } or suffix.startswith("conversation/")


def _public_response_headers(
    headers: list[tuple[bytes, bytes]],
) -> list[tuple[bytes, bytes]]:
    sanitized: list[tuple[bytes, bytes]] = []
    for name, value in headers:
        normalized_name = name.lower()
        if normalized_name.startswith(b"access-control-"):
            continue
        if normalized_name in {b"cache-control", b"referrer-policy"}:
            continue
        if normalized_name == b"vary":
            retained = [
                part.strip()
                for part in value.decode("latin-1").split(",")
                if part.strip().lower() != "origin"
            ]
            if retained:
                sanitized.append((name, ", ".join(retained).encode("latin-1")))
            continue
        sanitized.append((name, value))
    sanitized.extend(
        (
            (b"cache-control", b"no-store"),
            (b"referrer-policy", b"no-referrer"),
        )
    )
    return sanitized


__all__ = [
    "PublicConversationCorsBoundaryMiddleware",
    "mark_public_conversation_transport_boundary",
]
