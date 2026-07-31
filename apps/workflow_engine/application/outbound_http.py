from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol


class OutboundHttpFailurePhase(str, Enum):
    BEFORE_SEND = "before_send"
    OUTCOME_UNKNOWN = "outcome_unknown"


class OutboundHttpError(Exception):
    """Sanitized outbound failure classified at the transport boundary."""

    def __init__(
        self,
        code: str,
        *,
        phase: OutboundHttpFailurePhase,
        retryable_before_send: bool = False,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.phase = phase
        self.retryable_before_send = retryable_before_send


@dataclass(frozen=True)
class OutboundHttpRequest:
    method: str
    url: str
    headers: tuple[tuple[str, str], ...]
    body_mode: str
    json_body: Any
    timeout_seconds: float


@dataclass(frozen=True)
class OutboundHttpResponse:
    status_code: int
    headers: tuple[tuple[str, str], ...]
    content: bytes

    def json_or_text(self) -> Any:
        try:
            return json.loads(self.content)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self.text

    @property
    def text(self) -> str:
        encoding = "utf-8"
        for name, value in self.headers:
            if name.lower() != "content-type":
                continue
            match = re.search(r"charset\s*=\s*['\"]?([^;'\"\s]+)", value, re.I)
            if match:
                encoding = match.group(1)
            break
        try:
            return self.content.decode(encoding, errors="replace")
        except LookupError:
            return self.content.decode("utf-8", errors="replace")


class OutboundHttpPort(Protocol):
    def send(self, request: OutboundHttpRequest) -> OutboundHttpResponse: ...
