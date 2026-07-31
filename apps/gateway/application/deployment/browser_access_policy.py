from __future__ import annotations

import ipaddress
import hashlib
import json
import re
from collections.abc import Mapping
from enum import Enum
from typing import Any
from urllib.parse import SplitResult, urlsplit

import idna

from .browser_access_errors import BrowserAccessPolicyError
from .browser_access_models import BrowserAccessPolicy, BrowserEmbeddingPolicy

CONTRACT_VERSION = "deployment_browser_access.v1"
EMBED_CAPABLE_DEPLOYMENT_TYPES = frozenset({"chatbot", "widget"})
DEVELOPMENT_ENVIRONMENTS = frozenset(
    {"development", "dev", "test", "testing"}
)
MAX_PARENT_ORIGINS = 20
MAX_RAW_ORIGIN_BYTES = 512
MAX_FRAME_ANCESTORS_BYTES = 4096

_ASCII_TRIMMABLE_WHITESPACE = " \t\r\n\f\v"
_NUMERIC_HOST = re.compile(r"^[0-9.]+$")
_HEX_NUMERIC_HOST = re.compile(r"^0x[0-9a-f]+(?:\.0x[0-9a-f]+)*$", re.IGNORECASE)


def disabled_browser_access_policy() -> BrowserAccessPolicy:
    return BrowserAccessPolicy(
        contract_version=CONTRACT_VERSION,
        embedding=BrowserEmbeddingPolicy(enabled=False, parent_origins=()),
    )


def normalize_browser_access_policy(
    deployment_type: str | Enum,
    policy: Mapping[str, Any] | None,
    *,
    environment: str | None,
) -> BrowserAccessPolicy | None:
    type_value = _deployment_type_value(deployment_type)
    if type_value not in EMBED_CAPABLE_DEPLOYMENT_TYPES:
        if policy is None:
            return None
        raise BrowserAccessPolicyError("deployment.browser_access.not_supported")

    if policy is None:
        return disabled_browser_access_policy()

    contract_version, enabled, raw_origins = _parse_policy_shape(policy)
    if contract_version != CONTRACT_VERSION:
        raise BrowserAccessPolicyError(
            "deployment.browser_access.unsupported_contract_version"
        )
    if len(raw_origins) > MAX_PARENT_ORIGINS:
        raise BrowserAccessPolicyError(
            "deployment.browser_access.origin_limit_exceeded"
        )
    if enabled and not raw_origins:
        raise BrowserAccessPolicyError(
            "deployment.browser_access.origins_required"
        )
    if not enabled and raw_origins:
        raise BrowserAccessPolicyError(
            "deployment.browser_access.origins_not_allowed"
        )

    canonical_origins: list[str] = []
    seen: set[str] = set()
    for index, raw_origin in enumerate(raw_origins):
        canonical = _canonicalize_origin(
            raw_origin,
            environment=environment,
            origin_index=index,
        )
        if canonical in seen:
            raise BrowserAccessPolicyError(
                "deployment.browser_access.duplicate_origin",
                origin_index=index,
            )
        seen.add(canonical)
        canonical_origins.append(canonical)

    result = BrowserAccessPolicy(
        contract_version=CONTRACT_VERSION,
        embedding=BrowserEmbeddingPolicy(
            enabled=enabled,
            parent_origins=tuple(sorted(canonical_origins)),
        ),
    )
    if len(render_frame_ancestors(result).encode("utf-8")) > MAX_FRAME_ANCESTORS_BYTES:
        raise BrowserAccessPolicyError(
            "deployment.browser_access.header_limit_exceeded"
        )
    return result


def resolve_persisted_browser_access_policy(
    deployment_type: str | Enum,
    policy: object,
    *,
    environment: str | None,
) -> BrowserAccessPolicy:
    type_value = _deployment_type_value(deployment_type)
    if type_value not in EMBED_CAPABLE_DEPLOYMENT_TYPES:
        return disabled_browser_access_policy()
    try:
        normalized = normalize_browser_access_policy(
            type_value,
            policy if isinstance(policy, Mapping) else None,
            environment=environment,
        )
    except BrowserAccessPolicyError:
        return disabled_browser_access_policy()
    return normalized or disabled_browser_access_policy()


def render_frame_ancestors(policy: BrowserAccessPolicy | None) -> str:
    if policy is None or not policy.embedding.enabled:
        return "frame-ancestors 'none'"
    return "frame-ancestors " + " ".join(policy.embedding.parent_origins)


def browser_access_policy_digest(policy: BrowserAccessPolicy) -> str:
    canonical_json = json.dumps(
        policy.to_dict(),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def _parse_policy_shape(policy: Mapping[str, Any]) -> tuple[str, bool, list[str]]:
    if set(policy) != {"contract_version", "embedding"}:
        raise BrowserAccessPolicyError("deployment.browser_access.invalid_policy")
    contract_version = policy.get("contract_version")
    embedding = policy.get("embedding")
    if not isinstance(contract_version, str) or not isinstance(embedding, Mapping):
        raise BrowserAccessPolicyError("deployment.browser_access.invalid_policy")
    if set(embedding) != {"enabled", "parent_origins"}:
        raise BrowserAccessPolicyError("deployment.browser_access.invalid_policy")
    enabled = embedding.get("enabled")
    raw_origins = embedding.get("parent_origins")
    if type(enabled) is not bool or not isinstance(raw_origins, list):
        raise BrowserAccessPolicyError("deployment.browser_access.invalid_policy")
    if not all(isinstance(origin, str) for origin in raw_origins):
        raise BrowserAccessPolicyError("deployment.browser_access.invalid_origin")
    return contract_version, enabled, raw_origins


def _canonicalize_origin(
    raw_origin: str,
    *,
    environment: str | None,
    origin_index: int,
) -> str:
    if len(raw_origin.encode("utf-8")) > MAX_RAW_ORIGIN_BYTES:
        raise _invalid_origin(origin_index)
    origin = raw_origin.strip(_ASCII_TRIMMABLE_WHITESPACE)
    if not origin or any(_is_disallowed_character(character) for character in origin):
        raise _invalid_origin(origin_index)

    try:
        parsed = urlsplit(origin)
        port = parsed.port
    except (UnicodeError, ValueError):
        raise _invalid_origin(origin_index) from None

    if not _has_valid_origin_shape(parsed):
        raise _invalid_origin(origin_index)
    if parsed.netloc.endswith(":"):
        raise _invalid_origin(origin_index)
    if port is not None and not 1 <= port <= 65535:
        raise _invalid_origin(origin_index)

    raw_host = parsed.hostname
    if raw_host is None or "%" in raw_host or raw_host.endswith("."):
        raise _invalid_origin(origin_index)
    canonical_host, is_ipv6 = _canonicalize_host(raw_host, origin_index)

    scheme = parsed.scheme.lower()
    if scheme == "http":
        normalized_environment = (environment or "").strip().lower()
        if (
            normalized_environment not in DEVELOPMENT_ENVIRONMENTS
            or canonical_host not in {"localhost", "127.0.0.1", "::1"}
        ):
            raise _invalid_origin(origin_index)

    if port == _default_port(scheme):
        port = None
    rendered_host = f"[{canonical_host}]" if is_ipv6 else canonical_host
    return f"{scheme}://{rendered_host}{f':{port}' if port is not None else ''}"


def _has_valid_origin_shape(parsed: SplitResult) -> bool:
    return (
        parsed.scheme.lower() in {"http", "https"}
        and bool(parsed.netloc)
        and parsed.hostname is not None
        and parsed.username is None
        and parsed.password is None
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
    )


def _canonicalize_host(raw_host: str, origin_index: int) -> tuple[str, bool]:
    try:
        address = ipaddress.ip_address(raw_host)
    except ValueError:
        address = None

    if isinstance(address, ipaddress.IPv4Address):
        return str(address), False
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            raise _invalid_origin(origin_index)
        return address.compressed, True

    lowered_host = raw_host.lower()
    if _NUMERIC_HOST.fullmatch(lowered_host) or _HEX_NUMERIC_HOST.fullmatch(
        lowered_host
    ):
        raise _invalid_origin(origin_index)
    try:
        canonical = idna.encode(
            raw_host,
            uts46=True,
            transitional=False,
            std3_rules=True,
        ).decode("ascii")
    except (idna.IDNAError, UnicodeError):
        raise _invalid_origin(origin_index) from None
    return canonical.lower(), False


def _is_disallowed_character(character: str) -> bool:
    codepoint = ord(character)
    return (
        character.isspace()
        or codepoint <= 0x1F
        or 0x7F <= codepoint <= 0x9F
    )


def _default_port(scheme: str) -> int:
    return 443 if scheme == "https" else 80


def _deployment_type_value(deployment_type: str | Enum) -> str:
    value = getattr(deployment_type, "value", deployment_type)
    return str(value)


def _invalid_origin(origin_index: int) -> BrowserAccessPolicyError:
    return BrowserAccessPolicyError(
        "deployment.browser_access.invalid_origin",
        origin_index=origin_index,
    )
