from __future__ import annotations

import uuid


def safe_request_id(value: object) -> str:
    """Return a canonical RFC 4122 UUID without reflecting untrusted text."""
    if isinstance(value, str):
        try:
            parsed = uuid.UUID(value)
        except (AttributeError, ValueError):
            parsed = None
        if (
            parsed is not None
            and parsed.variant == uuid.RFC_4122
            and str(parsed) == value
        ):
            return value
    return str(uuid.uuid4())
