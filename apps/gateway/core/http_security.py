"""Fail-closed HTTP/session configuration helpers."""

import ipaddress
from urllib.parse import urlsplit


_DEVELOPMENT_SESSION_SECRET = "your-secret-key-change-in-production"
_INTERNAL_HOST_SUFFIXES = (
    ".cluster.local",
    ".home.arpa",
    ".internal",
    ".local",
    ".svc",
)


def resolve_session_signing_secret(
    configured_secret: str | None,
    *,
    node_env: str | None,
) -> str:
    secret = configured_secret or ""
    is_blank = not secret.strip()
    normalized_environment = (node_env or "").strip().lower()
    if normalized_environment == "production" and (
        is_blank or secret.strip() == _DEVELOPMENT_SESSION_SECRET
    ):
        raise RuntimeError("Production session signing secret is not configured")
    return secret if not is_blank else _DEVELOPMENT_SESSION_SECRET


def _is_local_environment(node_env: str | None) -> bool:
    return (node_env or "").strip().lower() in {"development", "local", "test"}


def _is_loopback_hostname(hostname: str) -> bool:
    normalized = hostname.strip().lower().rstrip(".")
    if normalized == "localhost" or normalized.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _is_public_hostname(hostname: str) -> bool:
    normalized = hostname.strip().lower().rstrip(".")
    try:
        return ipaddress.ip_address(normalized).is_global
    except ValueError:
        return bool(
            "." in normalized
            and not any(
                normalized.endswith(suffix) for suffix in _INTERNAL_HOST_SUFFIXES
            )
        )


def parse_credentialed_cors_origins(
    raw_value: str,
    *,
    node_env: str | None,
) -> list[str]:
    is_local_environment = _is_local_environment(node_env)
    origins: list[str] = []
    for raw_origin in raw_value.split(","):
        origin = raw_origin.strip()
        if not origin:
            continue
        if origin == "*":
            raise RuntimeError("Credentialed CORS does not allow wildcard origins")

        try:
            parsed = urlsplit(origin)
            hostname = parsed.hostname
            port = parsed.port
        except ValueError as exc:
            raise RuntimeError("CORS_ORIGINS must contain HTTP(S) origins") from exc
        if (
            parsed.scheme not in {"http", "https"}
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or "*" in hostname
            or hostname.endswith(".")
        ):
            raise RuntimeError("CORS_ORIGINS must contain HTTP(S) origins")

        if parsed.scheme == "http":
            if not is_local_environment:
                raise RuntimeError("Production credentialed CORS requires HTTPS origins")
            if not _is_loopback_hostname(hostname):
                raise RuntimeError(
                    "Development credentialed CORS allows HTTP loopback origins only"
                )
        elif not _is_public_hostname(hostname):
            raise RuntimeError("Credentialed CORS requires public HTTPS origins")

        normalized_host = hostname.lower()
        if ":" in normalized_host:
            normalized_host = f"[{normalized_host}]"
        default_port = 443 if parsed.scheme == "https" else 80
        normalized_port = "" if port in {None, default_port} else f":{port}"
        normalized = f"{parsed.scheme}://{normalized_host}{normalized_port}"
        if normalized not in origins:
            origins.append(normalized)

    if not origins:
        raise RuntimeError("At least one credentialed CORS origin is required")
    return origins
