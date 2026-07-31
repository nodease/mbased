from __future__ import annotations

from dataclasses import dataclass

from .models import LoginLimitDimension


class PasswordLoginError(Exception):
    """Base error for expected password login outcomes."""


class InvalidCredentials(PasswordLoginError):
    pass


class InactiveAccount(PasswordLoginError):
    pass


@dataclass(frozen=True)
class LoginRateLimited(PasswordLoginError):
    retry_after_seconds: int
    limited_dimensions: tuple[LoginLimitDimension, ...]


class LoginTemporarilyUnavailable(PasswordLoginError):
    pass


class PasswordLoginInternalError(PasswordLoginError):
    pass


@dataclass(frozen=True)
class LimiterBackendError(Exception):
    operation: str
