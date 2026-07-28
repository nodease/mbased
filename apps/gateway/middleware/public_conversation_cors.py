from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from apps.shared.domain.public_chat_conversation import (
    PUBLIC_CHAT_REQUEST_TTL_SECONDS,
)

_PUBLIC_CONVERSATION_BOUNDARY_STATE_KEY = "nodease.public_conversation_transport"
_PUBLIC_REQUEST_DEADLINE_STATE_KEY = "nodease.public_request_deadline_at"
MAX_PUBLIC_CHAT_REQUEST_BYTES = 393_216
_PUBLIC_CHAT_REQUEST_TOO_LARGE_BODY = json.dumps(
    {"detail": {"code": "conversation.request_too_large", "message": "The public conversation request is too large."}},
    separators=(",", ":"),
).encode("utf-8")


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

        if scope.get("method") == "POST" and (
            is_public_run_root or _is_public_chat_run_path(path)
        ):
            # Stamp the lifetime before request-body buffering and JSON parsing.
            public_conversation_request_deadline(scope)

        if (
            scope.get("method") == "POST"
            and _is_public_chat_run_path(path)
        ):
            buffered_messages = await _read_bounded_request(
                receive,
                max_bytes=MAX_PUBLIC_CHAT_REQUEST_BYTES,
            )
            if buffered_messages is None:
                await _send_public_request_too_large(send)
                return
            receive = _replay_receive(buffered_messages)

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


def public_conversation_request_deadline(scope: Scope) -> datetime:
    """Return the immutable Public request deadline stamped at ASGI ingress."""

    state = scope.setdefault("state", {})
    deadline = state.get(_PUBLIC_REQUEST_DEADLINE_STATE_KEY)
    if isinstance(deadline, datetime):
        return deadline
    deadline = _utc_now() + timedelta(seconds=PUBLIC_CHAT_REQUEST_TTL_SECONDS)
    state[_PUBLIC_REQUEST_DEADLINE_STATE_KEY] = deadline
    return deadline


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


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
        "chat/",
        "conversations",
        "conversations/",
        "conversation",
    } or suffix.startswith("conversation/")


def _is_public_chat_run_path(path: str) -> bool:
    prefix = "/api/v1/run-public/"
    if not path.startswith(prefix):
        return False
    remainder = path[len(prefix) :]
    slug, separator, suffix = remainder.partition("/")
    return bool(slug and separator and suffix in {"chat", "chat/"})


async def _read_bounded_request(
    receive: Receive,
    *,
    max_bytes: int,
) -> list[Message] | None:
    messages: list[Message] = []
    size = 0
    while True:
        message = await receive()
        messages.append(message)
        if message["type"] != "http.request":
            return messages
        body = message.get("body", b"")
        size += len(body)
        if size > max_bytes:
            return None
        if not message.get("more_body", False):
            return messages


def _replay_receive(messages: Sequence[Message]) -> Receive:
    remaining = iter(messages)

    async def receive() -> Message:
        try:
            return next(remaining)
        except StopIteration:
            return {"type": "http.disconnect"}

    return receive


async def _send_public_request_too_large(send: Send) -> None:
    headers = _public_response_headers(
        [
            (b"content-type", b"application/json"),
            (
                b"content-length",
                str(len(_PUBLIC_CHAT_REQUEST_TOO_LARGE_BODY)).encode("ascii"),
            ),
        ]
    )
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": headers,
        }
    )
    await send(
        {
            "type": "http.response.body",
            "body": _PUBLIC_CHAT_REQUEST_TOO_LARGE_BODY,
        }
    )


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
    "MAX_PUBLIC_CHAT_REQUEST_BYTES",
    "PublicConversationCorsBoundaryMiddleware",
    "mark_public_conversation_transport_boundary",
    "public_conversation_request_deadline",
]
