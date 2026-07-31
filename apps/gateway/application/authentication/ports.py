from __future__ import annotations

from typing import Protocol

from .models import LoginAdmission, PasswordLoginResult


class LoginLimiterPort(Protocol):
    def admit(
        self,
        *,
        account_identity: str,
        network_identity: str,
    ) -> LoginAdmission: ...

    def reset_after_success(
        self,
        *,
        account_identity: str,
        network_identity: str,
    ) -> None: ...


class PasswordAuthenticatorPort(Protocol):
    def authenticate(
        self,
        *,
        account_identity: str,
        password: str,
    ) -> PasswordLoginResult: ...


class LoginAuditPort(Protocol):
    def record_success(
        self,
        *,
        request_id: str | None,
        policy_version: str,
        actor_id: str,
        actor_name: str,
    ) -> None: ...

    def record_failure(
        self,
        *,
        request_id: str | None,
        policy_version: str,
        reason_code: str,
        limited_dimensions: tuple[str, ...] = (),
    ) -> None: ...


class LoginObservabilityPort(Protocol):
    def record(
        self,
        *,
        outcome: str,
        policy_version: str,
        operation: str,
    ) -> None: ...
