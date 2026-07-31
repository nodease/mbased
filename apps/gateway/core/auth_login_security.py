from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import re
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from apps.gateway.core.http_security import resolve_session_signing_secret


_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$")


@dataclass(frozen=True, slots=True)
class LoginSecuritySettings:
    fingerprint_keyring: Mapping[str, bytes] = field(repr=False)
    policy_version: str
    trusted_proxy_cidrs: tuple[str, ...]
    redis_host: str
    redis_port: int
    redis_db: int
    redis_password: str | None = field(default=None, repr=False)

    @classmethod
    def from_environment(cls, env: Mapping[str, str]) -> "LoginSecuritySettings":
        node_env = env.get("NODE_ENV", "development").strip().lower()
        raw_keyring = env.get("AUTH_LOGIN_FINGERPRINT_KEYS", "").strip()
        primary_version = env.get(
            "AUTH_LOGIN_FINGERPRINT_PRIMARY_VERSION", ""
        ).strip()

        if not raw_keyring:
            if node_env == "production":
                raise RuntimeError("login fingerprint keyring is required")
            session_secret = resolve_session_signing_secret(
                env.get("SECRET_KEY"),
                node_env=node_env,
            )
            derived = hmac.new(
                session_secret.encode("utf-8"),
                b"nodease:auth-login:fingerprint-key:development:v1",
                hashlib.sha256,
            ).digest()
            keyring: OrderedDict[str, bytes] = OrderedDict((("dev-v1", derived),))
        else:
            try:
                decoded = json.loads(raw_keyring)
                if not isinstance(decoded, dict) or not 1 <= len(decoded) <= 2:
                    raise ValueError
                if not primary_version or primary_version not in decoded:
                    raise ValueError
                if not _VERSION_PATTERN.fullmatch(primary_version):
                    raise ValueError
                parsed: dict[str, bytes] = {}
                for version, value in decoded.items():
                    if (
                        not isinstance(version, str)
                        or not _VERSION_PATTERN.fullmatch(version)
                        or not isinstance(value, str)
                        or len(value.encode("utf-8")) < 32
                    ):
                        raise ValueError
                    parsed[version] = value.encode("utf-8")
                keyring = OrderedDict(((primary_version, parsed[primary_version]),))
                for version, value in parsed.items():
                    if version != primary_version:
                        keyring[version] = value
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                raise RuntimeError("invalid login security configuration") from exc

        policy_version = env.get("AUTH_LOGIN_LIMITER_POLICY_VERSION", "v1").strip()
        if not _VERSION_PATTERN.fullmatch(policy_version):
            raise RuntimeError("invalid login security configuration")

        try:
            trusted_proxy_cidrs = tuple(
                str(ipaddress.ip_network(item.strip(), strict=True))
                for item in env.get("AUTH_LOGIN_TRUSTED_PROXY_CIDRS", "").split(",")
                if item.strip()
            )
            redis_port = int(env.get("REDIS_PORT", "6379"))
            redis_db = int(env.get("AUTH_LOGIN_LIMITER_REDIS_DB", "2"))
            if not 1 <= redis_port <= 65535 or not 0 <= redis_db <= 255:
                raise ValueError
        except ValueError as exc:
            raise RuntimeError("invalid login security configuration") from exc

        redis_host = env.get("REDIS_HOST", "").strip()
        redis_password = env.get("REDIS_PASSWORD", "").strip() or None
        redis_url = env.get("REDIS_URL", "").strip()
        if not redis_host and redis_url:
            try:
                parsed_url = urlsplit(redis_url)
                redis_host = parsed_url.hostname or ""
                redis_port = parsed_url.port or redis_port
                if redis_password is None and parsed_url.password:
                    redis_password = parsed_url.password
            except ValueError as exc:
                raise RuntimeError("invalid login security configuration") from exc
        if not redis_host:
            redis_host = "localhost"

        return cls(
            fingerprint_keyring=keyring,
            policy_version=policy_version,
            trusted_proxy_cidrs=trusted_proxy_cidrs,
            redis_host=redis_host,
            redis_port=redis_port,
            redis_db=redis_db,
            redis_password=redis_password,
        )
