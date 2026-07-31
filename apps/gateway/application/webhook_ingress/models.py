from dataclasses import dataclass
from typing import TypeAlias


JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class WebhookIngressLimits:
    max_body_bytes: int = 1_048_576
    processing_timeout_seconds: float = 5.0
    max_json_depth: int = 20
    max_json_nodes: int = 10_000
    max_credential_bytes: int = 512
    deadline_check_interval: int = 256

    def __post_init__(self) -> None:
        if self.max_body_bytes <= 0:
            raise ValueError("max_body_bytes must be positive")
        if self.processing_timeout_seconds <= 0:
            raise ValueError("processing_timeout_seconds must be positive")
        if self.max_json_depth <= 0:
            raise ValueError("max_json_depth must be positive")
        if self.max_json_nodes <= 0:
            raise ValueError("max_json_nodes must be positive")
        if self.max_credential_bytes <= 0:
            raise ValueError("max_credential_bytes must be positive")
        if self.deadline_check_interval <= 0:
            raise ValueError("deadline_check_interval must be positive")


@dataclass(frozen=True, slots=True)
class WebhookIngressRequestMetadata:
    query_token_present: bool = False
    authorization_headers: tuple[bytes, ...] = ()
    webhook_secret_headers: tuple[bytes, ...] = ()
    content_type_headers: tuple[bytes, ...] = ()
    content_encoding_headers: tuple[bytes, ...] = ()
    content_length_headers: tuple[bytes, ...] = ()


__all__ = [
    "JsonScalar",
    "JsonObject",
    "JsonValue",
    "WebhookIngressLimits",
    "WebhookIngressRequestMetadata",
]
