from __future__ import annotations

import os
from functools import lru_cache

import redis
from sqlalchemy.orm import Session

from apps.gateway.adapters.authentication.audit import LoginAuditRecorder
from apps.gateway.adapters.authentication.client_network import ClientNetworkResolver
from apps.gateway.adapters.authentication.legacy_password_authenticator import (
    LegacyPasswordAuthenticator,
)
from apps.gateway.adapters.authentication.observability import LoginObservability
from apps.gateway.adapters.authentication.redis_login_limiter import RedisLoginLimiter
from apps.gateway.application.authentication.password_login import PasswordLogin
from apps.gateway.core.auth_login_security import LoginSecuritySettings


@lru_cache(maxsize=1)
def login_security_settings() -> LoginSecuritySettings:
    return LoginSecuritySettings.from_environment(os.environ)


@lru_cache(maxsize=1)
def login_network_resolver() -> ClientNetworkResolver:
    return ClientNetworkResolver(login_security_settings().trusted_proxy_cidrs)


@lru_cache(maxsize=1)
def login_limiter() -> RedisLoginLimiter:
    settings = login_security_settings()
    client = redis.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        db=settings.redis_db,
        password=settings.redis_password,
        socket_connect_timeout=0.5,
        socket_timeout=0.5,
        health_check_interval=30,
    )
    return RedisLoginLimiter(
        client,
        keyring=settings.fingerprint_keyring,
        policy_version=settings.policy_version,
    )


def build_password_login(db: Session) -> PasswordLogin:
    settings = login_security_settings()
    return PasswordLogin(
        limiter=login_limiter(),
        authenticator=LegacyPasswordAuthenticator(db),
        audit=LoginAuditRecorder(),
        observability=LoginObservability(),
        policy_version=settings.policy_version,
    )


def validate_login_security_configuration() -> None:
    login_security_settings()
