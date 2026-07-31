from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class LoginLimitDimension(StrEnum):
    ACCOUNT = "account"
    NETWORK = "network"
    ACCOUNT_NETWORK = "account_network"


@dataclass(frozen=True, slots=True)
class LoginAdmission:
    allowed: bool
    retry_after_seconds: int = 0
    limited_dimensions: tuple[LoginLimitDimension, ...] = ()

    def __post_init__(self) -> None:
        if self.allowed:
            if self.retry_after_seconds != 0 or self.limited_dimensions:
                raise ValueError("allowed admission cannot contain limit details")
            return
        if not 1 <= self.retry_after_seconds <= 300 or not self.limited_dimensions:
            raise ValueError("blocked admission requires bounded limit details")
        if len(set(self.limited_dimensions)) != len(self.limited_dimensions):
            raise ValueError("limited dimensions must be unique")


@dataclass(frozen=True, slots=True)
class PasswordLoginCommand:
    account: str
    password: str = field(repr=False)
    source_network: str
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class PasswordLoginUser:
    id: str
    email: str
    name: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PasswordLoginSession:
    token: str = field(repr=False)
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class PasswordLoginResult:
    user: PasswordLoginUser
    session: PasswordLoginSession
