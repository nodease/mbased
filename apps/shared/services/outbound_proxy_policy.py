from __future__ import annotations

import ipaddress
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlsplit


class OutboundTransportMode(str, Enum):
    DIRECT_PINNED_INTERNAL_OR_DEDICATED = "direct_pinned_internal_or_dedicated"
    PROXY_GUARDED_EXTERNAL = "proxy_guarded_external"


class OutboundProxyConfigurationError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


_PROXY_POLICY_REVISION = "proxy-v1"
_DIRECT_POLICY_REVISION = "direct-v1"
_APPROVED_PROXY_PORTS = frozenset({3128, 3129})
_PRIVATE_PROXY_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("fc00::/7"),
)
_DNS_LABEL_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def _normalize_host(raw_host: str) -> str:
    candidate = raw_host.strip().rstrip(".").lower()
    if not candidate:
        raise OutboundProxyConfigurationError("egress.proxy_endpoint_invalid")
    try:
        return candidate.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise OutboundProxyConfigurationError("egress.proxy_endpoint_invalid") from exc


def _is_approved_internal_host(host: str) -> bool:
    if host == "localhost" or host.endswith(".localhost"):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        labels = host.split(".")
        if not labels or any(
            not _DNS_LABEL_PATTERN.fullmatch(label) for label in labels
        ):
            return False
        if len(labels) == 1:
            if host.isdigit() or re.fullmatch(r"0x[0-9a-f]+", host):
                return False
            return True
        return host.endswith(".svc") or host.endswith(".svc.cluster.local")
    return any(address in network for network in _PRIVATE_PROXY_NETWORKS)


def _parse_allowed_hosts(raw_hosts: str) -> tuple[str, ...]:
    try:
        hosts = tuple(
            dict.fromkeys(
                _normalize_host(value)
                for value in raw_hosts.split(",")
                if value.strip()
            )
        )
    except OutboundProxyConfigurationError as exc:
        raise OutboundProxyConfigurationError("egress.proxy_host_not_allowed") from exc
    if not hosts or any(not _is_approved_internal_host(host) for host in hosts):
        raise OutboundProxyConfigurationError("egress.proxy_host_not_allowed")
    return hosts


def _normalize_proxy_url(raw_url: str, allowed_hosts: tuple[str, ...]) -> str:
    if not raw_url.strip():
        raise OutboundProxyConfigurationError("egress.proxy_endpoint_required")
    try:
        parsed = urlsplit(raw_url.strip())
        port = parsed.port
    except ValueError as exc:
        raise OutboundProxyConfigurationError("egress.proxy_endpoint_invalid") from exc

    if (
        parsed.scheme.lower() != "http"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or port not in _APPROVED_PROXY_PORTS
    ):
        raise OutboundProxyConfigurationError("egress.proxy_endpoint_invalid")

    host = _normalize_host(parsed.hostname)
    if host not in allowed_hosts:
        raise OutboundProxyConfigurationError("egress.proxy_host_not_allowed")
    if not _is_approved_internal_host(host):
        raise OutboundProxyConfigurationError("egress.proxy_host_not_allowed")

    formatted_host = f"[{host}]" if ":" in host else host
    return f"http://{formatted_host}:{port}"


@dataclass(frozen=True)
class OutboundProxyPolicy:
    mode: OutboundTransportMode
    proxy_url: str | None
    allowed_proxy_hosts: tuple[str, ...]
    policy_revision: str

    def __post_init__(self) -> None:
        if self.mode is OutboundTransportMode.PROXY_GUARDED_EXTERNAL:
            if self.policy_revision != _PROXY_POLICY_REVISION:
                raise OutboundProxyConfigurationError(
                    "egress.proxy_policy_revision_invalid"
                )
            allowed_hosts = _parse_allowed_hosts(",".join(self.allowed_proxy_hosts))
            normalized_url = _normalize_proxy_url(self.proxy_url or "", allowed_hosts)
            object.__setattr__(self, "allowed_proxy_hosts", allowed_hosts)
            object.__setattr__(self, "proxy_url", normalized_url)
            return

        if self.mode is not OutboundTransportMode.DIRECT_PINNED_INTERNAL_OR_DEDICATED:
            raise OutboundProxyConfigurationError("egress.transport_mode_invalid")
        if self.proxy_url is not None or self.allowed_proxy_hosts:
            raise OutboundProxyConfigurationError("egress.transport_mode_invalid")
        if self.policy_revision != _DIRECT_POLICY_REVISION:
            raise OutboundProxyConfigurationError(
                "egress.proxy_policy_revision_invalid"
            )


def outbound_proxy_policy_from_environment(
    environ: Mapping[str, str] | None = None,
) -> OutboundProxyPolicy:
    environment = os.environ if environ is None else environ
    node_env = environment.get("NODE_ENV", "development").strip().lower()
    raw_mode = environment.get("OUTBOUND_TRANSPORT_MODE", "").strip().lower()
    if not raw_mode:
        raw_mode = OutboundTransportMode.DIRECT_PINNED_INTERNAL_OR_DEDICATED.value
    try:
        mode = OutboundTransportMode(raw_mode)
    except ValueError as exc:
        raise OutboundProxyConfigurationError("egress.transport_mode_invalid") from exc

    if mode is OutboundTransportMode.DIRECT_PINNED_INTERNAL_OR_DEDICATED:
        if node_env == "production":
            raise OutboundProxyConfigurationError("egress.proxy_required_in_production")
        return OutboundProxyPolicy(
            mode=mode,
            proxy_url=None,
            allowed_proxy_hosts=(),
            policy_revision=_DIRECT_POLICY_REVISION,
        )

    revision = environment.get("OUTBOUND_PROXY_POLICY_REVISION", "").strip()
    if revision != _PROXY_POLICY_REVISION:
        raise OutboundProxyConfigurationError("egress.proxy_policy_revision_invalid")
    allowed_hosts = _parse_allowed_hosts(
        environment.get("OUTBOUND_PROXY_ALLOWED_HOSTS", "")
    )
    proxy_url = _normalize_proxy_url(
        environment.get("OUTBOUND_PROXY_URL", ""),
        allowed_hosts,
    )
    return OutboundProxyPolicy(
        mode=mode,
        proxy_url=proxy_url,
        allowed_proxy_hosts=allowed_hosts,
        policy_revision=revision,
    )


def require_outbound_proxy_security_ready(
    environ: Mapping[str, str] | None = None,
) -> OutboundProxyPolicy:
    """Validate the immutable outbound transport before accepting work."""

    return outbound_proxy_policy_from_environment(environ)


__all__ = [
    "OutboundProxyConfigurationError",
    "OutboundProxyPolicy",
    "OutboundTransportMode",
    "outbound_proxy_policy_from_environment",
    "require_outbound_proxy_security_ready",
]
