from __future__ import annotations

from .errors import (
    InactiveAccount,
    InvalidCredentials,
    LoginRateLimited,
    LoginTemporarilyUnavailable,
    LimiterBackendError,
    PasswordLoginInternalError,
)
from .models import PasswordLoginCommand, PasswordLoginResult
from .policies import normalize_login_account
from .ports import (
    LoginAuditPort,
    LoginLimiterPort,
    LoginObservabilityPort,
    PasswordAuthenticatorPort,
)


class PasswordLogin:
    def __init__(
        self,
        *,
        limiter: LoginLimiterPort,
        authenticator: PasswordAuthenticatorPort,
        audit: LoginAuditPort,
        observability: LoginObservabilityPort,
        policy_version: str,
    ) -> None:
        self._limiter = limiter
        self._authenticator = authenticator
        self._audit = audit
        self._observability = observability
        self._policy_version = policy_version

    def execute(self, command: PasswordLoginCommand) -> PasswordLoginResult:
        account_identity = normalize_login_account(command.account)
        try:
            admission = self._limiter.admit(
                account_identity=account_identity,
                network_identity=command.source_network,
            )
        except LimiterBackendError:
            self._record_failure(
                command,
                reason_code="auth.login.limiter_unavailable",
            )
            self._observe("limiter_unavailable", operation="admission")
            raise LoginTemporarilyUnavailable() from None

        if not admission.allowed:
            dimensions = tuple(item.value for item in admission.limited_dimensions)
            self._record_failure(
                command,
                reason_code="auth.login.rate_limited",
                limited_dimensions=dimensions,
            )
            self._observe("rate_limited", operation="admission")
            raise LoginRateLimited(
                retry_after_seconds=admission.retry_after_seconds,
                limited_dimensions=admission.limited_dimensions,
            )

        try:
            result = self._authenticator.authenticate(
                account_identity=command.account,
                password=command.password,
            )
        except InvalidCredentials:
            self._record_failure(
                command,
                reason_code="auth.login.invalid_credentials",
            )
            self._observe("invalid_credentials", operation="login")
            raise
        except InactiveAccount:
            self._record_failure(command, reason_code="auth.login.inactive")
            self._observe("inactive", operation="login")
            raise
        except Exception:
            self._record_failure(command, reason_code="auth.login.internal_error")
            self._observe("internal_error", operation="login")
            raise PasswordLoginInternalError() from None

        try:
            self._limiter.reset_after_success(
                account_identity=account_identity,
                network_identity=command.source_network,
            )
        except LimiterBackendError:
            self._observe("reset_failed", operation="reset")

        self._record_success(command, result)
        self._observe("succeeded", operation="login")
        return result

    def _record_success(
        self,
        command: PasswordLoginCommand,
        result: PasswordLoginResult,
    ) -> None:
        try:
            self._audit.record_success(
                request_id=command.request_id,
                policy_version=self._policy_version,
                actor_id=result.user.id,
                actor_name=result.user.name,
            )
        except Exception:
            self._observe("audit_failed", operation="audit")

    def _record_failure(
        self,
        command: PasswordLoginCommand,
        *,
        reason_code: str,
        limited_dimensions: tuple[str, ...] = (),
    ) -> None:
        try:
            self._audit.record_failure(
                request_id=command.request_id,
                policy_version=self._policy_version,
                reason_code=reason_code,
                limited_dimensions=limited_dimensions,
            )
        except Exception:
            self._observe("audit_failed", operation="audit")

    def _observe(self, outcome: str, *, operation: str) -> None:
        try:
            self._observability.record(
                outcome=outcome,
                policy_version=self._policy_version,
                operation=operation,
            )
        except Exception:
            pass
