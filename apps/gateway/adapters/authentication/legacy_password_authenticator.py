from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from apps.gateway.application.authentication.errors import (
    InactiveAccount,
    InvalidCredentials,
)
from apps.gateway.application.authentication.models import (
    PasswordLoginResult,
    PasswordLoginSession,
    PasswordLoginUser,
)
from apps.gateway.services.auth_service import AuthService
from apps.shared.schemas.auth import LoginRequest


class LegacyPasswordAuthenticator:
    def __init__(self, db: Session) -> None:
        self._db = db

    def authenticate(
        self,
        *,
        account_identity: str,
        password: str,
    ) -> PasswordLoginResult:
        try:
            result = AuthService.login(
                self._db,
                LoginRequest(email=account_identity, password=password),
            )
        except HTTPException as exc:
            if exc.status_code == 401:
                raise InvalidCredentials() from None
            if exc.status_code == 403:
                raise InactiveAccount() from None
            raise
        return PasswordLoginResult(
            user=PasswordLoginUser(
                id=result.user.id,
                email=result.user.email,
                name=result.user.name,
                created_at=result.user.created_at,
            ),
            session=PasswordLoginSession(
                token=result.session.token,
                expires_at=result.session.expires_at,
            ),
        )
