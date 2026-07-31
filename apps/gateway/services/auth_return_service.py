"""Safe authentication continuation handling for Gateway-owned OAuth flows."""

import os
import re
import time
from typing import Any
from urllib.parse import unquote, urlsplit

from fastapi import Request


DEFAULT_AUTH_RETURN_PATH = "/dashboard"
MAX_AUTH_RETURN_PATH_LENGTH = 2048
MAX_AUTH_RETURN_DECODE_PASSES = 5
_SESSION_KEY = "auth_return_path"
_SESSION_TTL_SECONDS = 10 * 60
_INVALID_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")


class AuthReturnService:
    """Validate, store, and consume same-origin relative auth return paths."""

    @staticmethod
    def resolve_path(value: Any) -> str:
        if not isinstance(value, str) or not value:
            return DEFAULT_AUTH_RETURN_PATH
        if len(value) > MAX_AUTH_RETURN_PATH_LENGTH:
            return DEFAULT_AUTH_RETURN_PATH
        if _INVALID_PERCENT_ESCAPE.search(value):
            return DEFAULT_AUTH_RETURN_PATH

        decoded_values = [value]
        decoded = value
        stabilized = False
        for _ in range(MAX_AUTH_RETURN_DECODE_PASSES):
            decoded_once = unquote(decoded)
            if decoded_once == decoded:
                stabilized = True
                break
            decoded_values.append(decoded_once)
            decoded = decoded_once
        if not stabilized and unquote(decoded) != decoded:
            return DEFAULT_AUTH_RETURN_PATH

        for candidate in decoded_values:
            if AuthReturnService._is_unsafe_candidate(candidate):
                return DEFAULT_AUTH_RETURN_PATH

        parsed = urlsplit(value)
        return parsed.path + (
            f"?{parsed.query}" if parsed.query else ""
        ) + (f"#{parsed.fragment}" if parsed.fragment else "")

    @staticmethod
    def remember(
        request: Request,
        value: Any,
        *,
        now: float | None = None,
    ) -> str:
        safe_path = AuthReturnService.resolve_path(value)
        request.session[_SESSION_KEY] = {
            "path": safe_path,
            "issued_at": time.time() if now is None else now,
        }
        return safe_path

    @staticmethod
    def consume(request: Request, *, now: float | None = None) -> str:
        context = request.session.pop(_SESSION_KEY, None)
        if not isinstance(context, dict):
            return DEFAULT_AUTH_RETURN_PATH

        issued_at = context.get("issued_at")
        if isinstance(issued_at, bool) or not isinstance(issued_at, (int, float)):
            return DEFAULT_AUTH_RETURN_PATH
        current_time = time.time() if now is None else now
        if issued_at > current_time or current_time - issued_at > _SESSION_TTL_SECONDS:
            return DEFAULT_AUTH_RETURN_PATH
        return AuthReturnService.resolve_path(context.get("path"))

    @staticmethod
    def build_client_redirect(request: Request, return_path: Any) -> str:
        safe_path = AuthReturnService.resolve_path(return_path)
        configured_origin = os.getenv("AUTH_FRONTEND_ORIGIN")
        if configured_origin:
            return AuthReturnService._normalize_frontend_origin(
                configured_origin
            ) + safe_path

        host = request.headers.get("host", "").lower()
        if host == "localhost:8000":
            return f"http://localhost:3000{safe_path}"
        if host == "127.0.0.1:8000":
            return f"http://127.0.0.1:3000{safe_path}"
        return safe_path

    @staticmethod
    def _is_unsafe_candidate(candidate: str) -> bool:
        if not candidate.startswith("/") or candidate.startswith("//"):
            return True
        if "\\" in candidate:
            return True
        if any(
            ord(character) < 32 or ord(character) == 127
            for character in candidate
        ):
            return True

        parsed = urlsplit(candidate)
        if parsed.scheme or parsed.netloc:
            return True

        decoded_path = parsed.path
        if decoded_path.startswith("//"):
            return True
        if any(segment in {".", ".."} for segment in decoded_path.split("/")):
            return True
        return False

    @staticmethod
    def _normalize_frontend_origin(value: str) -> str:
        candidate = value.strip()
        parsed = urlsplit(candidate)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise RuntimeError("AUTH_FRONTEND_ORIGIN must be an HTTP(S) origin")
        return f"{parsed.scheme}://{parsed.netloc}"
