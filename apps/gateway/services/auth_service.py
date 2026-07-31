import os
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from apps.gateway.services.organization_context import ensure_user_default_organization
from apps.shared.db.models.user import User
from apps.shared.schemas.auth import (
    LoginRequest,
    LoginResponse,
    SessionInfo,
    SignupRequest,
    UserResponse,
)
from apps.shared.services.password_hashing import (
    hash_password as hash_password_value,
    verify_password as verify_password_value,
)


SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 6
_DUMMY_PASSWORD_HASH = hash_password_value("nodease-password-login-dummy")


class AuthService:
    @staticmethod
    def get_or_create_social_user(
        db: Session,
        email: str,
        name: str,
        social_provider: str,
        social_id: str,
        avatar_url: str | None = None,
    ) -> User:
        user = db.query(User).filter(User.email == email).first()

        if user:
            AuthService.ensure_user_active(user)

            is_updated = False
            if user.social_provider != social_provider:
                user.social_provider = social_provider
                is_updated = True
            if user.social_id != social_id:
                user.social_id = social_id
                is_updated = True
            if avatar_url and user.avatar_url != avatar_url:
                user.avatar_url = avatar_url
                is_updated = True

            if is_updated:
                db.commit()
                db.refresh(user)

            return user

        new_user = User(
            id=uuid.uuid4(),
            email=email,
            name=name,
            social_provider=social_provider,
            social_id=social_id,
            avatar_url=avatar_url,
        )
        db.add(new_user)
        ensure_user_default_organization(db, new_user)
        db.commit()
        db.refresh(new_user)
        return new_user

    @staticmethod
    def hash_password(password: str) -> str:
        return hash_password_value(password)

    @staticmethod
    def verify_password(password: str, hashed_password: str) -> bool:
        return verify_password_value(password, hashed_password)

    @staticmethod
    def ensure_user_active(user: User) -> None:
        if user.deactivated_at is not None:
            raise HTTPException(status_code=403, detail="비활성화된 계정입니다")

    @staticmethod
    def mark_login_success(db: Session, user: User) -> None:
        user.last_login_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(user)

    @staticmethod
    def create_jwt_token(user_id: str) -> str:
        expires_at = datetime.now(timezone.utc) + timedelta(
            hours=ACCESS_TOKEN_EXPIRE_HOURS
        )
        payload = {
            "user_id": user_id,
            "exp": expires_at,
        }
        return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

    @staticmethod
    def get_token_expiry() -> datetime:
        return datetime.now(timezone.utc) + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)

    @staticmethod
    def verify_jwt_token(token: str) -> str | None:
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            user_id: str | None = payload.get("user_id")
            return user_id
        except JWTError:
            return None

    @staticmethod
    def get_user_from_token(db: Session, token: str | None) -> User:
        if not token:
            raise HTTPException(status_code=401, detail="로그인이 필요합니다")

        user_id = AuthService.verify_jwt_token(token)
        if not user_id:
            raise HTTPException(
                status_code=401, detail="유효하지 않거나 만료된 토큰입니다"
            )

        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=401, detail="유저를 찾을 수 없습니다")

        AuthService.ensure_user_active(user)
        return user

    @staticmethod
    def signup(db: Session, request: SignupRequest) -> LoginResponse:
        existing_user = db.query(User).filter(User.email == request.email).first()
        if existing_user:
            raise HTTPException(status_code=400, detail="이미 등록된 이메일입니다")

        hashed_pwd = AuthService.hash_password(request.password)
        new_user = User(
            id=uuid.uuid4(),
            email=request.email,
            name=request.name,
            password=hashed_pwd,
            social_provider="none",
            last_login_at=datetime.now(timezone.utc),
        )
        db.add(new_user)
        ensure_user_default_organization(db, new_user)
        db.commit()
        db.refresh(new_user)

        token = AuthService.create_jwt_token(user_id=str(new_user.id))
        expires_at = AuthService.get_token_expiry()

        return LoginResponse(
            user=UserResponse(
                id=str(new_user.id),
                email=new_user.email,
                name=new_user.name,
                created_at=new_user.created_at,
            ),
            session=SessionInfo(
                token=token,
                expires_at=expires_at,
            ),
        )

    @staticmethod
    def login(db: Session, request: LoginRequest) -> LoginResponse:
        user = db.query(User).filter(User.email == request.email).first()
        if not user or not user.password:
            AuthService.verify_password(request.password, _DUMMY_PASSWORD_HASH)
            raise HTTPException(
                status_code=401, detail="이메일 또는 비밀번호가 올바르지 않습니다"
            )

        if not AuthService.verify_password(request.password, user.password):
            raise HTTPException(
                status_code=401, detail="이메일 또는 비밀번호가 올바르지 않습니다"
            )

        AuthService.ensure_user_active(user)
        AuthService.mark_login_success(db, user)

        token = AuthService.create_jwt_token(user_id=str(user.id))
        expires_at = AuthService.get_token_expiry()

        return LoginResponse(
            user=UserResponse(
                id=str(user.id),
                email=user.email,
                name=user.name,
                created_at=user.created_at,
            ),
            session=SessionInfo(
                token=token,
                expires_at=expires_at,
            ),
        )
