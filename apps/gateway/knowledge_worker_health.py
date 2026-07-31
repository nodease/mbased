from __future__ import annotations

import contextlib
import math
import socket
from collections.abc import Callable
from typing import Any

from apps.shared.celery_app import celery_app


KNOWLEDGE_WORKER_NODENAME_PREFIX = "knowledge"
DEFAULT_HEALTH_TIMEOUT_SECONDS = 2.0


class _DiscardText:
    def write(self, value: str) -> int:
        return len(value)

    def flush(self) -> None:
        return None


def local_worker_nodename(hostname: str | None = None) -> str:
    resolved_hostname = (hostname or socket.gethostname()).strip()
    if not resolved_hostname:
        raise ValueError("knowledge worker hostname is unavailable")
    return f"{KNOWLEDGE_WORKER_NODENAME_PREFIX}@{resolved_hostname}"


def is_local_worker_ready(
    *,
    hostname: str | None = None,
    ping: Callable[..., Any] | None = None,
    timeout_seconds: float = DEFAULT_HEALTH_TIMEOUT_SECONDS,
) -> bool:
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        return False

    try:
        destination = local_worker_nodename(hostname)
    except ValueError:
        return False

    ping_worker = ping or celery_app.control.ping
    sink = _DiscardText()
    try:
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            replies = ping_worker(
                destination=[destination],
                timeout=timeout_seconds,
            )
    except Exception:
        return False

    if not isinstance(replies, list):
        return False
    for reply in replies:
        if not isinstance(reply, dict):
            continue
        response = reply.get(destination)
        if isinstance(response, dict) and response.get("ok") == "pong":
            return True
    return False


def main() -> int:
    return 0 if is_local_worker_ready() else 1


if __name__ == "__main__":
    raise SystemExit(main())
