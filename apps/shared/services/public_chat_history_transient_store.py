from __future__ import annotations

import asyncio
import json
import re
import secrets
from collections.abc import Sequence
from typing import Any

from apps.shared.domain.public_chat_history import (
    PublicChatHistoryError,
    normalize_public_chat_history,
)
from apps.shared.pubsub import get_async_redis_client, get_redis_client

_REFERENCE_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_KEY_PREFIX = "nodease:public-chat-history:v1:"
_STORE_TIMEOUT_SECONDS = 2.0
_CONSUME_SCRIPT = """
local value = redis.call('GET', KEYS[1])
if value then
  redis.call('DEL', KEYS[1])
end
return value
"""


class PublicChatHistoryTransientStoreError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


async def store_public_chat_history(
    history: Sequence[dict[str, str]],
    *,
    ttl_seconds: int,
    timeout_seconds: float = _STORE_TIMEOUT_SECONDS,
    redis_client: Any = None,
) -> str:
    if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int):
        raise PublicChatHistoryTransientStoreError(
            "conversation.history_store_unavailable"
        )
    if ttl_seconds < 1:
        raise PublicChatHistoryTransientStoreError(
            "conversation.history_store_unavailable"
        )
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or timeout_seconds <= 0
    ):
        raise PublicChatHistoryTransientStoreError(
            "conversation.history_store_unavailable"
        )
    normalized = normalize_public_chat_history(list(history))
    payload = json.dumps(
        normalized,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    client = redis_client or get_async_redis_client()
    for _attempt in range(3):
        reference = secrets.token_hex(16)
        try:
            stored = await asyncio.wait_for(
                client.set(
                    _key(reference),
                    payload,
                    ex=ttl_seconds,
                    nx=True,
                ),
                timeout=float(timeout_seconds),
            )
        except Exception as error:
            raise PublicChatHistoryTransientStoreError(
                "conversation.history_store_unavailable"
            ) from error
        if stored:
            return reference
    raise PublicChatHistoryTransientStoreError("conversation.history_store_unavailable")


def consume_public_chat_history(
    reference: str,
    *,
    redis_client: Any = None,
) -> tuple[dict[str, str], ...] | None:
    if not isinstance(reference, str) or not _REFERENCE_PATTERN.fullmatch(reference):
        raise PublicChatHistoryTransientStoreError(
            "conversation.history_reference_invalid"
        )
    try:
        client = redis_client or get_redis_client()
        payload = client.eval(_CONSUME_SCRIPT, 1, _key(reference))
    except Exception as error:
        raise PublicChatHistoryTransientStoreError(
            "conversation.history_store_unavailable"
        ) from error
    if payload is None:
        return None
    try:
        decoded = payload.decode("utf-8") if isinstance(payload, bytes) else payload
        value = json.loads(decoded)
        return normalize_public_chat_history(value)
    except (UnicodeError, json.JSONDecodeError, PublicChatHistoryError) as error:
        raise PublicChatHistoryTransientStoreError(
            "conversation.history_store_corrupt"
        ) from error


def _key(reference: str) -> str:
    return f"{_KEY_PREFIX}{reference}"


__all__ = [
    "PublicChatHistoryTransientStoreError",
    "consume_public_chat_history",
    "store_public_chat_history",
]
