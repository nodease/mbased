from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import tiktoken

MAX_PUBLIC_CHAT_TURNS = 20
MAX_PUBLIC_CHAT_CONTEXT_TOKENS = 4096
MAX_PUBLIC_CHAT_MESSAGE_CHARS = 32_768
MAX_PUBLIC_CHAT_ENVELOPE_BYTES = 131_072
MAX_PUBLIC_CHAT_INPUT_BYTES = 131_072

_ALLOWED_ROLES = ("user", "assistant")
_ALLOWED_MESSAGE_KEYS = frozenset({"role", "content"})


class PublicChatHistoryError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def normalize_public_chat_history(
    value: Any,
) -> tuple[dict[str, str], ...]:
    if not isinstance(value, list):
        raise PublicChatHistoryError("conversation.history_invalid")
    if len(value) > MAX_PUBLIC_CHAT_TURNS * 2:
        raise PublicChatHistoryError("conversation.turn_limit_exceeded")

    normalized: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) != _ALLOWED_MESSAGE_KEYS:
            raise PublicChatHistoryError("conversation.history_invalid")
        role = item.get("role")
        content = item.get("content")
        if role not in _ALLOWED_ROLES:
            raise PublicChatHistoryError("conversation.role_invalid")
        if (
            not isinstance(content, str)
            or not content.strip()
            or len(content) > MAX_PUBLIC_CHAT_MESSAGE_CHARS
        ):
            raise PublicChatHistoryError("conversation.content_invalid")
        try:
            content.encode("utf-8")
        except UnicodeEncodeError:
            raise PublicChatHistoryError("conversation.content_invalid") from None
        normalized.append({"role": role, "content": content})

    if len(normalized) % 2 != 0:
        raise PublicChatHistoryError("conversation.history_order_invalid")
    for index, message in enumerate(normalized):
        if message["role"] != _ALLOWED_ROLES[index % 2]:
            raise PublicChatHistoryError("conversation.history_order_invalid")

    try:
        encoded = json.dumps(
            normalized,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except UnicodeEncodeError:
        raise PublicChatHistoryError("conversation.content_invalid") from None
    if len(encoded) > MAX_PUBLIC_CHAT_ENVELOPE_BYTES:
        raise PublicChatHistoryError("conversation.history_too_large")
    return tuple(normalized)


def _strict_count_tokens(text: str) -> int:
    try:
        encoding = tiktoken.encoding_for_model("gpt-4o-mini")
    except Exception:
        # This remains an exact tokenizer fallback, never a character heuristic.
        # If it is unavailable too, the public request fails closed.
        encoding = tiktoken.get_encoding("cl100k_base")
    return len(encoding.encode(text))


def count_public_chat_tokens(
    text: str,
    *,
    token_counter: Callable[[str], int] | None = None,
) -> int:
    """Count public conversation tokens without heuristic fallback."""
    return _safe_token_count(token_counter or _strict_count_tokens, text)


def remaining_public_chat_history_tokens(
    current_inputs: Mapping[str, Any],
    *,
    token_counter: Callable[[str], int] | None = None,
    max_context_tokens: int = MAX_PUBLIC_CHAT_CONTEXT_TOKENS,
) -> int:
    """Return the server-authoritative token budget left for history."""
    if max_context_tokens < 1:
        raise ValueError("max_context_tokens must be positive")
    current_tokens = count_public_chat_tokens(
        _canonical_public_chat_inputs(current_inputs),
        token_counter=token_counter,
    )
    if current_tokens > max_context_tokens:
        raise PublicChatHistoryError("conversation.current_input_too_large")
    return max_context_tokens - current_tokens


def bound_public_chat_history_projection(
    value: Sequence[Mapping[str, str]],
    *,
    projection: Callable[[tuple[dict[str, str], ...]], str],
    token_counter: Callable[[str], int] | None = None,
    max_projection_tokens: int,
) -> tuple[dict[str, str], ...]:
    """Bound the final sanitized/framed history without splitting a turn."""
    if (
        isinstance(max_projection_tokens, bool)
        or not isinstance(max_projection_tokens, int)
        or max_projection_tokens < 0
        or max_projection_tokens > MAX_PUBLIC_CHAT_CONTEXT_TOKENS
    ):
        raise PublicChatHistoryError("conversation.token_count_unavailable")
    normalized = list(_normalize_public_chat_history_projection(value))
    while normalized:
        candidate = tuple(dict(message) for message in normalized)
        projected_text = projection(candidate)
        if not isinstance(projected_text, str):
            raise PublicChatHistoryError("conversation.token_count_unavailable")
        if (
            count_public_chat_tokens(
                projected_text,
                token_counter=token_counter,
            )
            <= max_projection_tokens
        ):
            break
        del normalized[:2]
    return tuple(dict(message) for message in normalized)


def bound_public_chat_history(
    value: Any,
    *,
    current_inputs: Mapping[str, Any],
    token_counter: Callable[[str], int] | None = None,
    max_context_tokens: int = MAX_PUBLIC_CHAT_CONTEXT_TOKENS,
) -> tuple[dict[str, str], ...]:
    if token_counter is None:
        token_counter = _strict_count_tokens
    if max_context_tokens < 1:
        raise ValueError("max_context_tokens must be positive")

    normalized = list(normalize_public_chat_history(value))
    current_text = _canonical_public_chat_inputs(current_inputs)
    if _safe_token_count(token_counter, current_text) > max_context_tokens:
        raise PublicChatHistoryError("conversation.current_input_too_large")

    while (
        normalized
        and _context_token_count(
            normalized,
            current_text=current_text,
            token_counter=token_counter,
        )
        > max_context_tokens
    ):
        del normalized[:2]

    return tuple(dict(message) for message in normalized)


def _context_token_count(
    history: Sequence[Mapping[str, str]],
    *,
    current_text: str,
    token_counter: Callable[[str], int],
) -> int:
    history_text = "\n".join(
        f"{message['role']}:{message['content']}" for message in history
    )
    combined = (
        f"{history_text}\ncurrent:{current_text}" if history_text else current_text
    )
    return _safe_token_count(token_counter, combined)


def _normalize_public_chat_history_projection(
    value: Sequence[Mapping[str, str]],
) -> tuple[dict[str, str], ...]:
    """Validate trusted sanitizer output without reapplying raw size admission."""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise PublicChatHistoryError("conversation.history_invalid")
    if len(value) > MAX_PUBLIC_CHAT_TURNS * 2:
        raise PublicChatHistoryError("conversation.turn_limit_exceeded")
    if len(value) % 2 != 0:
        raise PublicChatHistoryError("conversation.history_order_invalid")

    normalized: list[dict[str, str]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping) or set(item) != _ALLOWED_MESSAGE_KEYS:
            raise PublicChatHistoryError("conversation.history_invalid")
        role = item.get("role")
        content = item.get("content")
        if role not in _ALLOWED_ROLES:
            raise PublicChatHistoryError("conversation.role_invalid")
        if role != _ALLOWED_ROLES[index % 2]:
            raise PublicChatHistoryError("conversation.history_order_invalid")
        if not isinstance(content, str):
            raise PublicChatHistoryError("conversation.content_invalid")
        try:
            content.encode("utf-8")
        except UnicodeEncodeError:
            raise PublicChatHistoryError("conversation.content_invalid") from None
        normalized.append({"role": role, "content": content})

    completed_pairs: list[dict[str, str]] = []
    for index in range(0, len(normalized), 2):
        user_message, assistant_message = normalized[index : index + 2]
        if not user_message["content"].strip() or not assistant_message[
            "content"
        ].strip():
            continue
        completed_pairs.extend((user_message, assistant_message))
    return tuple(completed_pairs)


def _safe_token_count(token_counter: Callable[[str], int], text: str) -> int:
    try:
        count = token_counter(text)
    except Exception as error:
        raise PublicChatHistoryError("conversation.token_count_unavailable") from error
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise PublicChatHistoryError("conversation.token_count_unavailable")
    return count


def _canonical_public_chat_inputs(value: Mapping[str, Any]) -> str:
    if not isinstance(value, Mapping):
        raise PublicChatHistoryError("conversation.inputs_invalid")
    text = _canonical_json(value)
    try:
        encoded = text.encode("utf-8")
    except UnicodeEncodeError:
        raise PublicChatHistoryError("conversation.inputs_invalid") from None
    if len(encoded) > MAX_PUBLIC_CHAT_INPUT_BYTES:
        raise PublicChatHistoryError("conversation.inputs_too_large")
    return text


def _canonical_json(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError, RecursionError) as error:
        raise PublicChatHistoryError("conversation.inputs_invalid") from error


__all__ = [
    "MAX_PUBLIC_CHAT_CONTEXT_TOKENS",
    "MAX_PUBLIC_CHAT_ENVELOPE_BYTES",
    "MAX_PUBLIC_CHAT_INPUT_BYTES",
    "MAX_PUBLIC_CHAT_MESSAGE_CHARS",
    "MAX_PUBLIC_CHAT_TURNS",
    "PublicChatHistoryError",
    "bound_public_chat_history",
    "bound_public_chat_history_projection",
    "count_public_chat_tokens",
    "normalize_public_chat_history",
    "remaining_public_chat_history_tokens",
]
