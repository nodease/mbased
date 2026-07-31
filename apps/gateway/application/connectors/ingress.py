from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .errors import (
    ConnectorTestMediaTypeNotSupported,
    ConnectorTestPayloadInvalid,
    ConnectorTestPayloadTimeout,
    ConnectorTestPayloadTooLarge,
)

_CONTENT_LENGTH_RE = re.compile(rb"^[0-9]+$")


def _reject_json_constant(_: str) -> None:
    raise ValueError("non-finite JSON number")


@dataclass(frozen=True, slots=True)
class ConnectorTestIngressMetadata:
    query_present: bool = False
    content_type_headers: tuple[bytes, ...] = ()
    content_encoding_headers: tuple[bytes, ...] = ()
    content_length_headers: tuple[bytes, ...] = ()


@dataclass(frozen=True, slots=True)
class ConnectorTestIngressPolicy:
    max_body_bytes: int = 32 * 1024
    receive_timeout_seconds: float = 5.0
    clock: Callable[[], float] = field(default=time.monotonic, compare=False)

    def __post_init__(self) -> None:
        if self.max_body_bytes <= 0:
            raise ValueError("max_body_bytes must be positive")
        if self.receive_timeout_seconds <= 0:
            raise ValueError("receive_timeout_seconds must be positive")

    def validate_metadata(self, metadata: ConnectorTestIngressMetadata) -> None:
        if metadata.query_present:
            raise ConnectorTestPayloadInvalid()
        if len(metadata.content_type_headers) != 1:
            raise ConnectorTestMediaTypeNotSupported()
        try:
            content_type = metadata.content_type_headers[0].decode(
                "ascii", errors="strict"
            )
        except UnicodeDecodeError:
            raise ConnectorTestMediaTypeNotSupported() from None
        if content_type.strip().lower() != "application/json":
            raise ConnectorTestMediaTypeNotSupported()

        if metadata.content_encoding_headers:
            if len(metadata.content_encoding_headers) != 1:
                raise ConnectorTestMediaTypeNotSupported()
            try:
                content_encoding = metadata.content_encoding_headers[0].decode(
                    "ascii", errors="strict"
                )
            except UnicodeDecodeError:
                raise ConnectorTestMediaTypeNotSupported() from None
            if content_encoding.strip().lower() != "identity":
                raise ConnectorTestMediaTypeNotSupported()

        values = metadata.content_length_headers
        if not values:
            return
        if len(values) != 1 or not _CONTENT_LENGTH_RE.fullmatch(values[0]):
            raise ConnectorTestPayloadInvalid()
        try:
            declared_length = int(values[0], 10)
        except ValueError:
            raise ConnectorTestPayloadInvalid() from None
        if declared_length > self.max_body_bytes:
            raise ConnectorTestPayloadTooLarge()

    def start_deadline(self) -> float:
        return self.clock() + self.receive_timeout_seconds

    def remaining_seconds(self, deadline: float) -> float:
        remaining = deadline - self.clock()
        if remaining <= 0:
            raise ConnectorTestPayloadTimeout()
        return remaining

    def validate_actual_size(self, size: int) -> None:
        if size < 0:
            raise ConnectorTestPayloadInvalid()
        if size > self.max_body_bytes:
            raise ConnectorTestPayloadTooLarge()

    def parse_json(self, body: bytes, deadline: float) -> dict[str, Any]:
        self.remaining_seconds(deadline)
        if body.startswith(b"\xef\xbb\xbf"):
            raise ConnectorTestPayloadInvalid()
        try:
            text = body.decode("utf-8", errors="strict")
            parsed = json.loads(text, parse_constant=_reject_json_constant)
            self.remaining_seconds(deadline)
        except ConnectorTestPayloadTimeout:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
            raise ConnectorTestPayloadInvalid() from None
        if not isinstance(parsed, dict):
            raise ConnectorTestPayloadInvalid()
        return parsed


DEFAULT_CONNECTOR_TEST_INGRESS_POLICY = ConnectorTestIngressPolicy()


__all__ = [
    "ConnectorTestIngressMetadata",
    "ConnectorTestIngressPolicy",
    "DEFAULT_CONNECTOR_TEST_INGRESS_POLICY",
]
