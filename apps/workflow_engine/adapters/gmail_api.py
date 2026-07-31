from __future__ import annotations

import re

_MESSAGE_ID = re.compile(r"^[A-Za-z0-9_-]{1,512}$")


def require_gmail_message_id(value: str) -> str:
    if not isinstance(value, str) or not _MESSAGE_ID.fullmatch(value):
        raise ValueError("invalid Gmail message ID")
    return value


__all__ = ["require_gmail_message_id"]
