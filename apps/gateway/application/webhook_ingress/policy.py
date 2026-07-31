import json
import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from email import policy as email_policy
from email.message import Message
from typing import Any

from apps.gateway.application.webhook_ingress.errors import (
    AuthenticationFailedError,
    CredentialAmbiguousError,
    PayloadInvalidError,
    PayloadTimeoutError,
    PayloadTooLargeError,
    QuerySecretNotSupportedError,
    UnsupportedMediaTypeError,
)
from apps.gateway.application.webhook_ingress.models import (
    JsonObject,
    JsonValue,
    WebhookIngressLimits,
    WebhookIngressRequestMetadata,
)


_CONTENT_TYPE_RE = re.compile(
    r"^application/(?P<subtype>[!#$%&'*+.^_`|~0-9A-Za-z-]+)$"
)
_CONTENT_LENGTH_RE = re.compile(rb"^[0-9]+$")


def _reject_json_constant(_: str) -> None:
    raise ValueError("non-finite JSON number")


@dataclass(frozen=True, slots=True)
class WebhookIngressPolicy:
    limits: WebhookIngressLimits = field(default_factory=WebhookIngressLimits)
    clock: Callable[[], float] = time.monotonic

    def authenticate(
        self,
        metadata: WebhookIngressRequestMetadata,
        *,
        credential_verifier: Callable[[bytes], bool],
    ) -> None:
        if metadata.query_token_present:
            raise QuerySecretNotSupportedError()

        authorization = metadata.authorization_headers
        webhook_secret = metadata.webhook_secret_headers
        if (
            len(authorization) > 1
            or len(webhook_secret) > 1
            or (authorization and webhook_secret)
        ):
            raise CredentialAmbiguousError()

        values = authorization or webhook_secret
        if not values:
            raise AuthenticationFailedError()

        raw_value = values[0]
        if b"," in raw_value:
            raise CredentialAmbiguousError()

        candidate = self._credential_candidate(
            raw_value,
            is_authorization=bool(authorization),
        )
        if candidate is None:
            raise AuthenticationFailedError()
        try:
            authenticated = credential_verifier(candidate)
        except Exception:
            authenticated = False
        if not authenticated:
            raise AuthenticationFailedError()

    def validate_payload_metadata(
        self,
        metadata: WebhookIngressRequestMetadata,
    ) -> int | None:
        self._validate_content_type(metadata.content_type_headers)
        self._validate_content_encoding(metadata.content_encoding_headers)
        return self._validate_content_length(metadata.content_length_headers)

    def validate_actual_body_size(self, size: int) -> None:
        if size < 0:
            raise PayloadInvalidError()
        if size > self.limits.max_body_bytes:
            raise PayloadTooLargeError()

    def start_deadline(self) -> float:
        return self.clock() + self.limits.processing_timeout_seconds

    def remaining_seconds(self, deadline: float) -> float:
        remaining = deadline - self.clock()
        if remaining <= 0:
            raise PayloadTimeoutError()
        return remaining

    def ensure_within_deadline(self, deadline: float) -> None:
        self.remaining_seconds(deadline)

    def parse_json(self, body: bytes, deadline: float) -> JsonObject:
        self.ensure_within_deadline(deadline)
        if body.startswith(b"\xef\xbb\xbf"):
            raise PayloadInvalidError()

        try:
            text = body.decode("utf-8", errors="strict")
            self.ensure_within_deadline(deadline)
            parsed: Any = json.loads(text, parse_constant=_reject_json_constant)
            self.ensure_within_deadline(deadline)
        except PayloadTimeoutError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
            raise PayloadInvalidError() from None

        if not isinstance(parsed, dict):
            raise PayloadInvalidError()
        self._validate_json_structure(parsed, deadline)
        return parsed

    def _credential_candidate(
        self,
        raw_value: bytes,
        *,
        is_authorization: bool,
    ) -> bytes | None:
        if is_authorization:
            if len(raw_value) < 7 or raw_value[:7].lower() != b"bearer ":
                return None
            candidate = raw_value[7:]
        else:
            candidate = raw_value

        if not 1 <= len(candidate) <= self.limits.max_credential_bytes:
            return None
        try:
            candidate.decode("ascii", errors="strict")
        except UnicodeDecodeError:
            return None
        return candidate

    def _validate_content_type(self, values: tuple[bytes, ...]) -> None:
        if len(values) != 1:
            raise UnsupportedMediaTypeError()
        try:
            raw_value = values[0].decode("ascii", errors="strict")
            message = Message(policy=email_policy.HTTP)
            message["Content-Type"] = raw_value
            parsed_header = message["Content-Type"]
        except (UnicodeDecodeError, ValueError):
            raise UnsupportedMediaTypeError() from None

        if parsed_header is None or parsed_header.defects:
            raise UnsupportedMediaTypeError()

        content_type = message.get_content_type().lower()
        match = _CONTENT_TYPE_RE.fullmatch(content_type)
        if not match:
            raise UnsupportedMediaTypeError()
        subtype = match.group("subtype").lower()
        if subtype != "json" and not (subtype.endswith("+json") and len(subtype) > 5):
            raise UnsupportedMediaTypeError()

        params = message.get_params(failobj=[], header="content-type")[1:]
        seen_params: set[str] = set()
        for name, value in params:
            normalized_name = name.lower()
            if normalized_name in seen_params:
                raise UnsupportedMediaTypeError()
            seen_params.add(normalized_name)
            if normalized_name == "charset" and str(value).lower() != "utf-8":
                raise UnsupportedMediaTypeError()

    def _validate_content_encoding(self, values: tuple[bytes, ...]) -> None:
        if not values:
            return
        if len(values) != 1:
            raise UnsupportedMediaTypeError()
        try:
            encoding = values[0].decode("ascii", errors="strict")
        except UnicodeDecodeError:
            raise UnsupportedMediaTypeError() from None
        if "," in encoding or encoding.strip().lower() != "identity":
            raise UnsupportedMediaTypeError()

    def _validate_content_length(self, values: tuple[bytes, ...]) -> int | None:
        if not values:
            return None
        if len(values) != 1 or not _CONTENT_LENGTH_RE.fullmatch(values[0]):
            raise PayloadInvalidError()
        try:
            declared_length = int(values[0], 10)
        except ValueError:
            raise PayloadInvalidError() from None
        if declared_length > self.limits.max_body_bytes:
            raise PayloadTooLargeError()
        return declared_length

    def _validate_json_structure(self, value: JsonValue, deadline: float) -> None:
        stack: list[tuple[JsonValue, int]] = [(value, 1)]
        node_count = 0

        while stack:
            current, depth = stack.pop()
            node_count += 1
            if node_count > self.limits.max_json_nodes:
                raise PayloadInvalidError()
            if depth > self.limits.max_json_depth:
                raise PayloadInvalidError()
            if node_count % self.limits.deadline_check_interval == 0:
                self.ensure_within_deadline(deadline)

            if isinstance(current, dict):
                stack.extend((child, depth + 1) for child in current.values())
            elif isinstance(current, list):
                stack.extend((child, depth + 1) for child in current)
            elif isinstance(current, float) and not math.isfinite(current):
                raise PayloadInvalidError()

        self.ensure_within_deadline(deadline)


DEFAULT_WEBHOOK_INGRESS_POLICY = WebhookIngressPolicy()


__all__ = [
    "DEFAULT_WEBHOOK_INGRESS_POLICY",
    "WebhookIngressPolicy",
]
