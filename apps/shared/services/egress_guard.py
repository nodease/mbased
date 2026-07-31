import ipaddress
import json
import os
import re
import socket
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

import httpx
import requests

SENSITIVE_HEADER_NAMES = {
    "authorization",
    "cookie",
    "proxy-authorization",
    "x-api-key",
    "x-auth-token",
}

HOP_BY_HOP_HEADER_NAMES = {
    "connection",
    "content-length",
    "expect",
    "host",
    "keep-alive",
    "proxy-connection",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}

API_RESPONSE_CONTENT_TYPES = frozenset(
    {
        "application/json",
        "application/x-ndjson",
        "application/xml",
        "text/csv",
        "text/markdown",
        "text/plain",
        "text/xml",
    }
)

DOCUMENT_RESPONSE_CONTENT_TYPES = frozenset(
    {
        "application/msword",
        "application/octet-stream",
        "application/pdf",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "text/csv",
        "text/markdown",
        "text/plain",
    }
)

MAX_DB_FETCH_BATCH_SIZE = 1000
_TRUSTED_LOCAL_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("fc00::/7"),
)
_PUBLIC_EGRESS_DENIED_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "192.88.99.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",
        "240.0.0.0/4",
        "::/96",
        "::ffff:0:0/96",
        "64:ff9b::/96",
        "64:ff9b:1::/48",
        "100::/64",
        "2001::/23",
        "2001:db8::/32",
        "2002::/16",
        "3ffe::/16",
        "3fff::/20",
        "5f00::/16",
        "fc00::/7",
        "fe80::/10",
        "fec0::/10",
        "ff00::/8",
    )
)


class EgressGuardError(Exception):
    """Outbound guard failure with a sanitized reason code."""

    def __init__(self, reason_code: str, message: str = "Outbound target denied."):
        super().__init__(message)
        self.reason_code = reason_code
        self.safe_message = message


def canonicalize_network_host(host: str) -> str:
    canonical_host = str(host or "").strip().rstrip(".").lower()
    try:
        return canonical_host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise EgressGuardError("egress.invalid_host") from exc


@dataclass(frozen=True)
class EgressGuardPolicy:
    allowed_schemes: frozenset[str] = frozenset({"http", "https"})
    allowed_methods: frozenset[str] = frozenset({"GET", "POST"})
    allowed_ports: frozenset[int] | None = frozenset({80, 443})
    deny_private_networks: bool = True
    deny_url_credentials: bool = True
    deny_https_downgrade: bool = True
    max_redirects: int = 5
    timeout_seconds: float = 30.0
    max_header_count: int = 50
    max_header_name_bytes: int = 128
    max_header_value_bytes: int = 8192
    max_header_total_bytes: int = 16 * 1024
    max_request_bytes: int = 1024 * 1024
    max_response_bytes: int = 10 * 1024 * 1024
    allowed_content_types: frozenset[str] | None = None
    force_identity_encoding: bool = True
    allow_compressed_response: bool = False
    validate_peer_ip: bool = True


@dataclass(frozen=True)
class SafeHTTPResponse:
    status_code: int
    headers: dict[str, str]
    content: bytes
    final_url: str

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.text)


class OutboundEgressGuard:
    """Knowledge/RAG server-side outbound access를 검증하는 중앙 guard."""

    def __init__(self, policy: EgressGuardPolicy | None = None) -> None:
        self.policy = policy or EgressGuardPolicy()

    def _validate_url_policy(self, url: str) -> tuple[str, str]:
        try:
            parts = urlsplit(str(url or "").strip())
            scheme = parts.scheme.lower()
            hostname = parts.hostname
            has_credentials = bool(parts.username or parts.password)
        except ValueError as exc:
            raise EgressGuardError("egress.invalid_url") from exc
        if not scheme or scheme not in self.policy.allowed_schemes:
            raise EgressGuardError("egress.unsupported_scheme")
        if not hostname:
            raise EgressGuardError("egress.invalid_url")
        if self.policy.deny_url_credentials and has_credentials:
            raise EgressGuardError("egress.url_credentials_not_allowed")

        host = self._canonical_host(hostname)
        try:
            port = parts.port
        except ValueError as exc:
            raise EgressGuardError("egress.invalid_port") from exc
        if port is not None and (port < 1 or port > 65535):
            raise EgressGuardError("egress.invalid_port")
        self._validate_allowed_port(scheme, port)

        netloc = f"[{host}]" if ":" in host else host
        if port is not None:
            netloc = f"{netloc}:{port}"
        canonical_url = urlunsplit(
            (scheme, netloc, parts.path or "/", parts.query, "")
        )
        return host, canonical_url

    def validate_url_policy(self, url: str) -> str:
        """Validate URL policy without resolving its network destination."""
        _host, canonical_url = self._validate_url_policy(url)
        return canonical_url

    def validate_url(self, url: str) -> str:
        host, canonical_url = self._validate_url_policy(url)
        self._validate_resolved_addresses(host)
        return canonical_url

    def validate_redirect(self, from_url: str, to_url: str) -> str:
        from_scheme = urlsplit(from_url).scheme.lower()
        to_scheme = urlsplit(to_url).scheme.lower()
        if (
            self.policy.deny_https_downgrade
            and from_scheme == "https"
            and to_scheme == "http"
        ):
            raise EgressGuardError("egress.https_downgrade")
        return self.validate_url(to_url)

    def validate_host_port(
        self,
        host: str,
        port: int,
        *,
        allowed_ports: frozenset[int] | None = None,
        trusted_local_targets: frozenset[tuple[str, int]] = frozenset(),
    ) -> tuple[str, int, str]:
        canonical_host, safe_port, addresses = self.validate_host_port_addresses(
            host,
            port,
            allowed_ports=allowed_ports,
            trusted_local_targets=trusted_local_targets,
        )
        return canonical_host, safe_port, addresses[0]

    def validate_host_port_addresses(
        self,
        host: str,
        port: int,
        *,
        allowed_ports: frozenset[int] | None = None,
        trusted_local_targets: frozenset[tuple[str, int]] = frozenset(),
    ) -> tuple[str, int, tuple[str, ...]]:
        canonical_host = self._canonical_host(str(host or ""))
        if not canonical_host:
            raise EgressGuardError("egress.invalid_host")
        try:
            safe_port = int(port)
        except (TypeError, ValueError) as exc:
            raise EgressGuardError("egress.invalid_port") from exc
        if safe_port < 1 or safe_port > 65535:
            raise EgressGuardError("egress.invalid_port")
        port_policy = (
            self.policy.allowed_ports if allowed_ports is None else allowed_ports
        )
        if port_policy is not None and safe_port not in port_policy:
            raise EgressGuardError("egress.disallowed_port")
        is_trusted_local = (canonical_host, safe_port) in trusted_local_targets
        addresses = self._validate_resolved_addresses(
            canonical_host,
            port=safe_port,
            trusted_local=is_trusted_local,
        )
        return canonical_host, safe_port, tuple(dict.fromkeys(addresses))

    def validate_method(self, method: str) -> str:
        safe_method = str(method or "GET").upper()
        if safe_method not in self.policy.allowed_methods:
            raise EgressGuardError("egress.unsupported_method")
        return safe_method

    def validate_request_body(self, *, json_body: Any | None, data: Any | None) -> None:
        size = 0
        if json_body is not None:
            try:
                body = json.dumps(
                    json_body,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            except (TypeError, ValueError) as exc:
                raise EgressGuardError("egress.unsupported_request_body") from exc
            size += len(body.encode("utf-8"))
        if data is not None:
            if isinstance(data, bytes):
                size += len(data)
            elif isinstance(data, str):
                size += len(data.encode("utf-8"))
            else:
                raise EgressGuardError("egress.unsupported_request_body")
        if size > self.policy.max_request_bytes:
            raise EgressGuardError("egress.request_too_large")

    def sanitize_request_headers(
        self,
        headers: Mapping[str, Any] | None,
        *,
        strip_sensitive: bool = False,
    ) -> dict[str, str]:
        return dict(
            self.validate_request_header_items(
                (headers or {}).items(),
                strip_sensitive=strip_sensitive,
            )
        )

    def validate_request_header_items(
        self,
        headers: Iterable[tuple[str, Any]],
        *,
        reject_hop_by_hop: bool = False,
        strip_sensitive: bool = False,
    ) -> tuple[tuple[str, str], ...]:
        safe_headers: list[tuple[str, str]] = []
        total_bytes = 0
        for key, value in headers:
            if len(safe_headers) >= self.policy.max_header_count:
                raise EgressGuardError("egress.headers_too_large")
            name = str(key).strip()
            if not name:
                continue
            if ":" in name or "\r" in name or "\n" in name:
                raise EgressGuardError("egress.invalid_header")
            value_text = str(value)
            if "\r" in value_text or "\n" in value_text:
                raise EgressGuardError("egress.invalid_header")
            name_bytes = len(name.encode("utf-8"))
            value_bytes = len(value_text.encode("utf-8"))
            if name_bytes > self.policy.max_header_name_bytes:
                raise EgressGuardError("egress.headers_too_large")
            if value_bytes > self.policy.max_header_value_bytes:
                raise EgressGuardError("egress.headers_too_large")
            total_bytes += name_bytes + value_bytes
            if total_bytes > self.policy.max_header_total_bytes:
                raise EgressGuardError("egress.headers_too_large")
            lower_name = name.lower()
            if lower_name in HOP_BY_HOP_HEADER_NAMES:
                if reject_hop_by_hop:
                    raise EgressGuardError("egress.invalid_header")
                continue
            if strip_sensitive and lower_name in SENSITIVE_HEADER_NAMES:
                continue
            safe_headers.append((name, value_text))
        return tuple(safe_headers)

    def sanitize_response_headers(self, headers: Mapping[str, Any]) -> dict[str, str]:
        safe_headers: dict[str, str] = {}
        for key, value in headers.items():
            lower_name = str(key).lower()
            if lower_name in SENSITIVE_HEADER_NAMES or lower_name == "set-cookie":
                continue
            safe_headers[str(key)] = str(value)
        return safe_headers

    def validate_response_headers(
        self,
        headers: Mapping[str, Any],
        *,
        enforce_content_type: bool = True,
    ) -> None:
        content_encoding = str(headers.get("content-encoding", "")).strip().lower()
        if content_encoding and content_encoding != "identity":
            if not self.policy.allow_compressed_response:
                raise EgressGuardError("egress.compressed_response_not_allowed")

        content_length = str(headers.get("content-length", "")).strip()
        if content_length:
            try:
                if int(content_length) > self.policy.max_response_bytes:
                    raise EgressGuardError("egress.response_too_large")
            except ValueError as exc:
                raise EgressGuardError("egress.invalid_content_length") from exc

        if self.policy.allowed_content_types is None or not enforce_content_type:
            return
        content_type = str(headers.get("content-type", "")).split(";")[0].lower()
        if not _content_type_allowed(content_type, self.policy.allowed_content_types):
            raise EgressGuardError("egress.unsupported_content_type")

    def validate_response_peer(self, response: requests.Response) -> None:
        if not self.policy.validate_peer_ip:
            return
        peer_ip = _extract_response_peer_ip(response)
        self.validate_response_peer_ip(peer_ip)

    def validate_response_peer_ip(self, peer_ip: str | None) -> None:
        """Validate a peer address extracted by a non-requests HTTP adapter."""
        if not self.policy.validate_peer_ip:
            return
        if not peer_ip:
            raise EgressGuardError("egress.peer_unverified")
        try:
            ip = ipaddress.ip_address(peer_ip)
        except ValueError as exc:
            raise EgressGuardError("egress.invalid_peer") from exc
        if self._is_denied_ip(ip):
            raise EgressGuardError("egress.private_target")

    def _canonical_host(self, host: str) -> str:
        return canonicalize_network_host(host)

    def _validate_resolved_addresses(
        self,
        host: str,
        *,
        port: int | None = None,
        trusted_local: bool = False,
    ) -> tuple[str, ...]:
        try:
            addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise EgressGuardError("egress.dns_resolution_failed") from exc
        if not addresses:
            raise EgressGuardError("egress.dns_resolution_failed")

        safe_addresses: list[str] = []
        for address in addresses:
            ip_text = address[4][0]
            ip = ipaddress.ip_address(ip_text)
            if trusted_local:
                if not self._is_trusted_local_ip(ip):
                    raise EgressGuardError("egress.private_target")
            elif self._is_denied_ip(ip):
                raise EgressGuardError("egress.private_target")
            safe_addresses.append(ip_text)
        if trusted_local:
            safe_addresses.sort(
                key=lambda value: ipaddress.ip_address(value).version != 4
            )
        return tuple(safe_addresses)

    def _is_denied_ip(self, ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        if not self.policy.deny_private_networks:
            return False
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return True
        return any(
            ip.version == network.version and ip in network
            for network in _PUBLIC_EGRESS_DENIED_NETWORKS
        )

    @staticmethod
    def _is_trusted_local_ip(
        ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
    ) -> bool:
        if ip.is_loopback:
            return True
        if ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            return False
        return any(ip in network for network in _TRUSTED_LOCAL_NETWORKS)

    def _validate_allowed_port(self, scheme: str, port: int | None) -> None:
        if self.policy.allowed_ports is None:
            return
        effective_port = port
        if effective_port is None:
            effective_port = 443 if scheme == "https" else 80
        if effective_port not in self.policy.allowed_ports:
            raise EgressGuardError("egress.disallowed_port")


def safe_http_request(
    method: str,
    url: str,
    *,
    headers: Mapping[str, Any] | None = None,
    json_body: Any | None = None,
    data: Any | None = None,
    policy: EgressGuardPolicy | None = None,
    operation_id: str | None = None,
) -> SafeHTTPResponse:
    from apps.shared.services.guarded_http_transport import GuardedHttpTransport
    from apps.shared.services.outbound_operation_policy import (
        require_outbound_operation_profile,
    )

    if operation_id is not None and policy is not None:
        raise EgressGuardError("egress.ambiguous_policy")
    operation = (
        require_outbound_operation_profile(operation_id).bind(url)
        if operation_id is not None
        else None
    )
    guard = operation.guard if operation is not None else OutboundEgressGuard(policy)
    current_url = (
        operation.validate_url(url) if operation is not None else guard.validate_url(url)
    )
    current_headers = guard.sanitize_request_headers(headers)
    method = guard.validate_method(method)
    if method == "GET":
        json_body = None
        data = None
    guard.validate_request_body(json_body=json_body, data=data)
    if guard.policy.force_identity_encoding:
        current_headers["Accept-Encoding"] = "identity"

    transport = (
        GuardedHttpTransport(operation=operation)
        if operation is not None
        else GuardedHttpTransport(guard)
    )
    with httpx.Client(
        transport=transport,
        timeout=guard.policy.timeout_seconds,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        for redirect_index in range(guard.policy.max_redirects + 1):
            try:
                request_kwargs: dict[str, Any] = {
                    "method": method,
                    "url": current_url,
                    "headers": current_headers,
                }
                if method != "GET" and json_body is not None:
                    request_kwargs["json"] = json_body
                elif method != "GET" and data is not None:
                    request_kwargs["content"] = data
                response = client.request(
                    **request_kwargs,
                )
            except httpx.TimeoutException as exc:
                raise EgressGuardError("egress.timeout") from exc
            except httpx.RequestError as exc:
                raise EgressGuardError("egress.connection_failed") from exc

            if response.is_redirect:
                if redirect_index >= guard.policy.max_redirects:
                    response.close()
                    raise EgressGuardError("egress.too_many_redirects")
                location = response.headers.get("location")
                response.close()
                if not location:
                    raise EgressGuardError("egress.invalid_redirect")
                next_url = urljoin(current_url, location)
                if operation is not None:
                    operation.validate_origin(next_url)
                next_url = guard.validate_redirect(current_url, next_url)
                if _origin(current_url) != _origin(next_url):
                    current_headers = guard.sanitize_request_headers(
                        current_headers,
                        strip_sensitive=True,
                    )
                current_url = next_url
                continue

            try:
                guard.validate_response_headers(response.headers)
                content = response.content
                if len(content) > guard.policy.max_response_bytes:
                    raise EgressGuardError("egress.response_too_large")
            except Exception:
                response.close()
                raise
            return SafeHTTPResponse(
                status_code=response.status_code,
                headers=guard.sanitize_response_headers(response.headers),
                content=content,
                final_url=current_url,
            )

    raise EgressGuardError("egress.connection_failed")


def download_url_to_temp_file(
    url: str,
    *,
    suffix: str = ".tmp",
    policy: EgressGuardPolicy | None = None,
    operation_id: str | None = None,
) -> str:
    from apps.shared.services.guarded_http_transport import GuardedHttpTransport
    from apps.shared.services.outbound_operation_policy import (
        require_outbound_operation_profile,
    )

    if operation_id is not None and policy is not None:
        raise EgressGuardError("egress.ambiguous_policy")
    operation = (
        require_outbound_operation_profile(operation_id).bind(url)
        if operation_id is not None
        else None
    )
    guard = operation.guard if operation is not None else OutboundEgressGuard(policy)
    current_url = (
        operation.validate_url(url) if operation is not None else guard.validate_url(url)
    )
    guard.validate_method("GET")
    current_headers = guard.sanitize_request_headers(None)
    if guard.policy.force_identity_encoding:
        current_headers["Accept-Encoding"] = "identity"

    transport = (
        GuardedHttpTransport(operation=operation)
        if operation is not None
        else GuardedHttpTransport(guard)
    )
    with httpx.Client(
        transport=transport,
        timeout=guard.policy.timeout_seconds,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        for redirect_index in range(guard.policy.max_redirects + 1):
            try:
                with client.stream(
                    "GET",
                    current_url,
                    headers=current_headers,
                ) as response:
                    if response.is_redirect:
                        if redirect_index >= guard.policy.max_redirects:
                            raise EgressGuardError("egress.too_many_redirects")
                        location = response.headers.get("location")
                        if not location:
                            raise EgressGuardError("egress.invalid_redirect")
                        next_url = urljoin(current_url, location)
                        if operation is not None:
                            operation.validate_origin(next_url)
                        current_url = guard.validate_redirect(current_url, next_url)
                        continue

                    if not 200 <= response.status_code < 300:
                        raise EgressGuardError("egress.http_status_rejected")
                    guard.validate_response_headers(response.headers)
                    temp_path: str | None = None
                    try:
                        with tempfile.NamedTemporaryFile(
                            delete=False,
                            suffix=suffix,
                        ) as tmp:
                            temp_path = tmp.name
                            total = 0
                            for chunk in response.iter_raw():
                                total += len(chunk)
                                if total > guard.policy.max_response_bytes:
                                    raise EgressGuardError(
                                        "egress.response_too_large"
                                    )
                                tmp.write(chunk)
                        return temp_path
                    except Exception as exc:
                        if temp_path:
                            try:
                                os.remove(temp_path)
                            except OSError:
                                pass
                            if isinstance(exc, EgressGuardError):
                                exc.partial_file_path = temp_path
                        raise
            except httpx.TimeoutException as exc:
                raise EgressGuardError("egress.timeout") from exc
            except httpx.RequestError as exc:
                raise EgressGuardError("egress.connection_failed") from exc

    raise EgressGuardError("egress.connection_failed")


def ensure_db_probe_allowed(query: str) -> None:
    # Knowledge DB source는 장기적으로 query builder/allowlist로 좁혀야 한다.
    # 그 전까지는 free-form SELECT가 side effect, 파일 접근, 지연 함수를 호출하지 못하게 막는다.
    normalized = " ".join(str(query or "").strip().split()).lower()
    if not normalized.startswith("select "):
        raise EgressGuardError("adapter.sql_not_allowed")
    blocked_tokens = [";", "--", "/*", "*/"]
    if any(token in normalized for token in blocked_tokens):
        raise EgressGuardError("adapter.sql_not_allowed")

    blocked_keywords = [
        "alter",
        "analyze",
        "call",
        "copy",
        "create",
        "delete",
        "do",
        "drop",
        "execute",
        "grant",
        "insert",
        "listen",
        "notify",
        "reset",
        "revoke",
        "set",
        "truncate",
        "update",
        "vacuum",
    ]
    keyword_pattern = r"\b(" + "|".join(blocked_keywords) + r")\b"
    if re.search(keyword_pattern, normalized):
        raise EgressGuardError("adapter.sql_not_allowed")

    blocked_functions = [
        "database_to_xml",
        "dblink",
        "file_fdw",
        "generate_series",
        "lo_export",
        "lo_import",
        "pg_advisory_lock",
        "pg_advisory_xact_lock",
        "pg_ls_dir",
        "pg_notify",
        "pg_read_binary_file",
        "pg_read_file",
        "pg_sleep",
        "pg_stat_file",
        "postgres_fdw",
        "query_to_xml",
        "schema_to_xml",
        "set_config",
        "table_to_xml",
    ]
    if any(
        re.search(rf"\b{function}\s*\(", normalized) for function in blocked_functions
    ):
        raise EgressGuardError("adapter.sql_not_allowed")


def safe_db_fetch_batch_size(batch_size: int | None) -> int:
    try:
        parsed = MAX_DB_FETCH_BATCH_SIZE if batch_size is None else int(batch_size)
    except (TypeError, ValueError):
        parsed = MAX_DB_FETCH_BATCH_SIZE
    return max(1, min(parsed, MAX_DB_FETCH_BATCH_SIZE))


def ensure_network_target_allowed(
    host: str,
    port: int,
    *,
    allowed_ports: frozenset[int] | None,
    trusted_local_targets: frozenset[tuple[str, int]] = frozenset(),
) -> tuple[str, int, str]:
    guard = OutboundEgressGuard(
        EgressGuardPolicy(
            allowed_ports=allowed_ports,
            allowed_methods=frozenset({"GET"}),
            validate_peer_ip=False,
        )
    )
    return guard.validate_host_port(
        host,
        port,
        allowed_ports=allowed_ports,
        trusted_local_targets=trusted_local_targets,
    )


def ensure_ssh_tunnel_allowed(enabled: bool, *, allow_tunnel: bool = False) -> None:
    if enabled and not allow_tunnel:
        raise EgressGuardError("adapter.ssh_tunnel_not_allowed")


def ensure_ssh_command_allowed(command: str | None = None) -> None:
    if command:
        raise EgressGuardError("adapter.ssh_command_not_allowed")


def ensure_object_listing_allowed(
    *,
    bucket: str,
    prefix: str | None,
    recursive: bool,
    max_keys: int,
    max_allowed_keys: int = 1000,
) -> None:
    if not bucket:
        raise EgressGuardError("adapter.object_listing_too_broad")
    normalized_prefix = str(prefix or "").strip("/")
    if recursive and not normalized_prefix:
        raise EgressGuardError("adapter.object_listing_too_broad")
    if max_keys > max_allowed_keys:
        raise EgressGuardError("adapter.object_listing_too_broad")


def _origin(url: str) -> tuple[str, str, int | None]:
    parts = urlsplit(url)
    port = parts.port
    if port is None and parts.scheme.lower() in {"http", "https"}:
        port = 443 if parts.scheme.lower() == "https" else 80
    return (parts.scheme.lower(), parts.hostname or "", port)


def _read_capped_response(response: requests.Response, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    try:
        for chunk in response.iter_content(chunk_size=8192):
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                raise EgressGuardError("egress.response_too_large")
            chunks.append(chunk)
    finally:
        response.close()
    return b"".join(chunks)


def _content_type_allowed(
    content_type: str,
    allowed_content_types: frozenset[str],
) -> bool:
    if content_type in allowed_content_types:
        return True
    if content_type.endswith("+json") and "application/json" in allowed_content_types:
        return True
    if content_type.endswith("+xml") and "application/xml" in allowed_content_types:
        return True
    return False


def _extract_response_peer_ip(response: requests.Response) -> str | None:
    raw = getattr(response, "raw", None)
    connection = getattr(raw, "_connection", None)
    sock = getattr(connection, "sock", None)
    if sock is None:
        original_response = getattr(raw, "_original_response", None)
        fp = getattr(original_response, "fp", None)
        raw_fp = getattr(fp, "raw", None)
        sock = getattr(raw_fp, "_sock", None)
    if sock is None or not hasattr(sock, "getpeername"):
        return None
    peer = sock.getpeername()
    if not peer:
        return None
    return str(peer[0])


def safe_quote_filename(filename: str) -> str:
    return quote(filename)
