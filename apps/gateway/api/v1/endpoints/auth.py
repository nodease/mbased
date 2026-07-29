import logging
import os
import re
import secrets
from collections.abc import Mapping
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from apps.gateway.application.authentication.errors import (
    InactiveAccount,
    InvalidCredentials,
    LoginRateLimited,
    LoginTemporarilyUnavailable,
    PasswordLoginInternalError,
)
from apps.gateway.application.authentication.models import PasswordLoginCommand
from apps.gateway.application.csrf.token import (
    CSRF_ANON_COOKIE_NAME,
    CSRF_COOKIE_NAME,
    CSRF_ORGANIZATION_HEADER_NAME,
    CsrfBindingKind,
)
from apps.gateway.auth.oauth import oauth
from apps.gateway.composition.authentication import (
    build_password_login,
    login_network_resolver,
)
from apps.gateway.composition.csrf import csrf_token_service
from apps.gateway.services.auth_return_service import AuthReturnService
from apps.gateway.services.auth_service import AuthService
from apps.gateway.utils.api_errors import error_response
from apps.shared.audit import record_audit
from apps.shared.audit.actions import AuditAction
from apps.shared.audit.context import get_current_metadata
from apps.shared.db.session import get_db
from apps.shared.schemas.auth import (
    LoginRequest,
    LoginResponse,
    SessionInfo,
    SignupRequest,
    UserResponse,
)
from apps.shared.schemas.csrf import CsrfTokenResponse

router = APIRouter()
logger = logging.getLogger(__name__)
_ANONYMOUS_SEED_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")


def _request_hostname(request: Request) -> str:
    try:
        return urlsplit(f"//{request.headers.get('host', '')}").hostname or ""
    except ValueError:
        return ""


def _is_loopback_hostname(hostname: str) -> bool:
    return hostname.lower() in {"localhost", "127.0.0.1", "::1"}


def _request_meta(request: Request) -> dict:
    """감사 로그용 요청 메타데이터(ip, user_agent)."""
    meta = get_current_metadata()
    if request.client and "ip" not in meta:
        meta["ip"] = request.client.host
    meta.setdefault("user_agent", request.headers.get("user-agent"))
    meta.setdefault("request_id", getattr(request.state, "request_id", None))
    return meta


def _user_snapshot(user) -> dict:
    return {"id": str(user.id), "email": user.email, "name": user.name}


def _record_auth_success(
    action: str,
    request: Request,
    user,
    **metadata,
) -> None:
    record_audit(
        action=action,
        category="action",
        actor_id=user.id,
        actor_type="user",
        metadata={
            **metadata,
            "actor": _user_snapshot(user),
            **_request_meta(request),
        },
    )


def _record_auth_failure(
    action: str,
    request: Request,
    email: str,
    error: Exception,
) -> None:
    record_audit(
        action=action,
        category="action",
        actor_type="system",
        status="failure",
        metadata={
            "email": email,
            "error_type": type(error).__name__,
            **_request_meta(request),
        },
    )


def _record_google_oauth_failure(request: Request, reason: str) -> None:
    record_audit(
        action=AuditAction.USER_LOGIN_FAILED,
        category="action",
        actor_type="system",
        status="failure",
        metadata={
            "provider": "google",
            "reason": reason,
            **_request_meta(request),
        },
    )


def _recorded_login_error(
    status_code: int,
    detail: str,
    *,
    retry_after: int | None = None,
) -> HTTPException:
    headers = {"Retry-After": str(retry_after)} if retry_after is not None else None
    exc = HTTPException(status_code=status_code, detail=detail, headers=headers)
    setattr(exc, "audit_recorded", True)
    return exc


def _get_cookie_config(request: Request) -> tuple[bool, str | None]:
    """
    환경 감지 및 쿠키 도메인 설정 헬퍼 함수

    Returns:
        (is_production, cookie_domain)
    """
    host = _request_hostname(request)
    is_production = not _is_loopback_hostname(host)

    # 쿠키 도메인 (환경변수 우선, 없으면 호스트에서 자동 추출)
    cookie_domain = os.getenv("COOKIE_DOMAIN")
    if not cookie_domain and is_production:
        # api.moviepick.shop → .moviepick.shop
        parts = host.split(".")
        if len(parts) >= 2:
            cookie_domain = f".{'.'.join(parts[-2:])}"

    return is_production, cookie_domain


def _csrf_cookie_options(request: Request) -> dict[str, object]:
    is_production, _ = _get_cookie_config(request)
    return {
        "httponly": True,
        "secure": is_production,
        "samesite": "none" if is_production else "lax",
        "path": "/api/v1",
        "max_age": 600,
    }


def _set_csrf_cookie(
    response: Response,
    request: Request,
    *,
    key: str,
    value: str,
) -> None:
    response.set_cookie(
        key=key,
        value=value,
        **_csrf_cookie_options(request),
    )


def _clear_csrf_cookie_family(request: Request, response: Response) -> None:
    options = _csrf_cookie_options(request)
    for key in (CSRF_COOKIE_NAME, CSRF_ANON_COOKIE_NAME):
        response.delete_cookie(
            key=key,
            path=str(options["path"]),
            secure=bool(options["secure"]),
            httponly=True,
            samesite=str(options["samesite"]),
        )


@router.get("/csrf", response_model=CsrfTokenResponse)
def bootstrap_csrf_token(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    auth_cookie = request.cookies.get("auth_token")
    anonymous_seed: str | None = None
    if auth_cookie:
        # Invalid authentication must never downgrade to an anonymous binding.
        try:
            AuthService.get_user_from_token(db, auth_cookie)
        except HTTPException as exc:
            if exc.status_code != 401:
                raise
            record_audit(
                action=AuditAction.AUTH_PERMISSION_DENIED,
                category="action",
                actor_type="system",
                status="failure",
                metadata={
                    "reason": "auth.csrf_bootstrap_invalid_session",
                    **_request_meta(request),
                },
            )
            invalid_response = error_response(
                request,
                401,
                "auth.invalid",
                "Authentication is invalid.",
            )
            _, cookie_domain = _get_cookie_config(request)
            invalid_response.delete_cookie(
                key="auth_token",
                path="/",
                domain=cookie_domain,
            )
            _clear_csrf_cookie_family(request, invalid_response)
            invalid_response.headers["Cache-Control"] = "no-store"
            invalid_response.headers["Pragma"] = "no-cache"
            return invalid_response
        binding_kind = CsrfBindingKind.AUTHENTICATED
        binding_secret = auth_cookie
    else:
        candidate = request.cookies.get(CSRF_ANON_COOKIE_NAME)
        anonymous_seed = (
            candidate
            if candidate and _ANONYMOUS_SEED_PATTERN.fullmatch(candidate)
            else secrets.token_urlsafe(32)
        )
        binding_kind = CsrfBindingKind.PRE_AUTH
        binding_secret = anonymous_seed

    try:
        issued = csrf_token_service().issue(
            binding_kind=binding_kind,
            binding_secret=binding_secret,
            organization_scope=request.headers.get(CSRF_ORGANIZATION_HEADER_NAME),
        )
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid CSRF request context",
        ) from None

    _set_csrf_cookie(
        response,
        request,
        key=CSRF_COOKIE_NAME,
        value=issued.token,
    )
    if anonymous_seed is None:
        options = _csrf_cookie_options(request)
        response.delete_cookie(
            key=CSRF_ANON_COOKIE_NAME,
            path=str(options["path"]),
            secure=bool(options["secure"]),
            httponly=True,
            samesite=str(options["samesite"]),
        )
    else:
        _set_csrf_cookie(
            response,
            request,
            key=CSRF_ANON_COOKIE_NAME,
            value=anonymous_seed,
        )

    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return CsrfTokenResponse(
        token=issued.token,
        expires_at=issued.expires_at,
    )


@router.post("/signup", response_model=LoginResponse)
def signup(
    request_obj: Request,
    request: SignupRequest,
    response: Response,
    db: Session = Depends(get_db),
):
    """
    이메일/비밀번호 회원가입

    Args:
        request_obj: FastAPI Request (호스트 확인용)
        request: 회원가입 요청 (email, password, name)
        response: FastAPI Response (쿠키 설정용)
        db: 데이터베이스 세션

    Returns:
        LoginResponse: 사용자 정보 + JWT 토큰
    """
    try:
        result = AuthService.signup(db, request)
    except Exception as e:
        _record_auth_failure(
            AuditAction.USER_SIGNUP_FAILED, request_obj, request.email, e
        )
        raise

    _record_auth_success(AuditAction.USER_SIGNUP, request_obj, result.user)

    # 환경 감지 및 쿠키 도메인 설정
    is_production, cookie_domain = _get_cookie_config(request_obj)

    cookie_params = {
        "key": "auth_token",
        "value": result.session.token,
        "httponly": True,
        "samesite": "none" if is_production else "lax",
        "max_age": 21600,  # 6시간
        "path": "/",
    }

    if is_production:
        cookie_params["secure"] = True
        if cookie_domain:
            cookie_params["domain"] = cookie_domain
    else:
        cookie_params["secure"] = False

    _clear_csrf_cookie_family(request_obj, response)
    response.set_cookie(**cookie_params)

    return result


@router.post("/login", response_model=LoginResponse)
def login(
    request_obj: Request,
    request: LoginRequest,
    response: Response,
    db: Session = Depends(get_db),
):
    """
    이메일/비밀번호 로그인

    Args:
        request_obj: FastAPI Request (호스트 확인용)
        request: 로그인 요청 (email, password)
        response: FastAPI Response (쿠키 설정용)
        db: 데이터베이스 세션

    Returns:
        LoginResponse: 사용자 정보 + JWT 토큰
    """
    use_case = build_password_login(db)
    try:
        result = use_case.execute(
            PasswordLoginCommand(
                account=str(request.email),
                password=request.password,
                source_network=login_network_resolver().resolve(request_obj),
                request_id=(
                    getattr(request_obj.state, "request_id", None)
                    or request_obj.headers.get("X-Request-ID")
                ),
            )
        )
    except InvalidCredentials:
        raise _recorded_login_error(
            401,
            "이메일 또는 비밀번호가 올바르지 않습니다",
        ) from None
    except InactiveAccount:
        raise _recorded_login_error(403, "비활성화된 계정입니다") from None
    except LoginRateLimited as exc:
        raise _recorded_login_error(
            429,
            "로그인 시도가 너무 많습니다. 잠시 후 다시 시도해주세요.",
            retry_after=exc.retry_after_seconds,
        ) from None
    except LoginTemporarilyUnavailable:
        raise _recorded_login_error(
            503,
            "로그인을 일시적으로 사용할 수 없습니다.",
            retry_after=30,
        ) from None
    except PasswordLoginInternalError:
        raise _recorded_login_error(
            500,
            "로그인을 처리할 수 없습니다.",
        ) from None

    response_result = LoginResponse(
        user=UserResponse(
            id=result.user.id,
            email=result.user.email,
            name=result.user.name,
            created_at=result.user.created_at,
        ),
        session=SessionInfo(
            token=result.session.token,
            expires_at=result.session.expires_at,
        ),
    )

    # 환경 감지 및 쿠키 도메인 설정
    is_production, cookie_domain = _get_cookie_config(request_obj)

    cookie_params = {
        "key": "auth_token",
        "value": response_result.session.token,
        "httponly": True,
        "samesite": "none" if is_production else "lax",
        "max_age": 21600,  # 6시간
        "path": "/",
    }

    if is_production:
        cookie_params["secure"] = True
        if cookie_domain:
            cookie_params["domain"] = cookie_domain
    else:
        cookie_params["secure"] = False

    _clear_csrf_cookie_family(request_obj, response)
    response.set_cookie(**cookie_params)

    return response_result


@router.post("/logout")
def logout(request_obj: Request, response: Response):
    """로그아웃 - 쿠키 삭제"""
    # 환경 감지 및 쿠키 도메인 설정
    _, cookie_domain = _get_cookie_config(request_obj)

    # 쿠키 삭제 (설정 시와 동일한 domain으로)
    delete_params = {"key": "auth_token", "path": "/"}
    if cookie_domain:
        delete_params["domain"] = cookie_domain

    response.delete_cookie(**delete_params)
    _clear_csrf_cookie_family(request_obj, response)

    # 로그아웃은 actor를 시그니처에서 알 수 없어(쿠키 삭제 시점) actor_id 없이 기록한다.
    record_audit(
        action=AuditAction.USER_LOGOUT,
        category="action",
        actor_type="user",
        metadata=_request_meta(request_obj),
    )
    return {"message": "Logged out successfully"}


@router.get("/me", response_model=LoginResponse)
def get_current_user(request: Request, db: Session = Depends(get_db)):
    """
    현재 로그인된 사용자 정보 조회

    Args:
        request: FastAPI Request (쿠키 읽기용)
        db: 데이터베이스 세션

    Returns:
        LoginResponse: 사용자 정보 + 세션 정보
    """

    # 쿠키에서 토큰 가져오기
    token = request.cookies.get("auth_token")
    user = AuthService.get_user_from_token(db, token)

    from apps.shared.schemas.auth import SessionInfo, UserResponse

    return LoginResponse(
        user=UserResponse(
            id=str(user.id),
            email=user.email,
            name=user.name,
            created_at=user.created_at,
        ),
        session=SessionInfo(
            token=token,
            expires_at=AuthService.get_token_expiry(),
        ),
    )


# -----------------------------------------------------------------------------
# Google OAuth
# -----------------------------------------------------------------------------


@router.get("/google/login")
async def google_login(
    request: Request,
    next_path: str | None = Query(
        default=None,
        alias="next",
        max_length=2048,
    ),
):
    """
    구글 로그인 리디렉션
    - 로컬/배포 환경에 따라 redirect_uri를 동적으로 생성
    """
    # url_for는 현재 요청의 Host 헤더(또는 Forwarded 헤더)를 기반으로 절대 경로 생성
    redirect_uri = request.url_for("auth_google_callback")

    # https로 요청 보내도록 수정
    redirect_hostname = urlsplit(str(redirect_uri)).hostname or ""
    if not _is_loopback_hostname(redirect_hostname):
        redirect_uri = str(redirect_uri).replace("http://", "https://")

    AuthReturnService.remember(request, next_path)
    try:
        return await oauth.google.authorize_redirect(request, redirect_uri)
    except Exception as exc:
        AuthReturnService.consume(request)
        logger.warning(
            "Google OAuth start failed: error_type=%s",
            type(exc).__name__,
        )
        _record_google_oauth_failure(request, "oauth_start_failed")
        return Response(status_code=503, content="OAuth login is unavailable")


@router.get("/google/callback")
async def auth_google_callback(
    request: Request, response: Response, db: Session = Depends(get_db)
):
    """
    구글 로그인 콜백
    """
    return_path = AuthReturnService.consume(request)
    try:
        token = await oauth.google.authorize_access_token(request)
    except Exception as exc:
        logger.warning(
            "Google OAuth callback token exchange failed: error_type=%s",
            type(exc).__name__,
        )
        _record_google_oauth_failure(request, "token_exchange_failed")
        return Response(status_code=400, content="OAuth authentication failed")

    if not isinstance(token, Mapping):
        logger.warning(
            "Google OAuth callback returned invalid token response: response_type=%s",
            type(token).__name__,
        )
        _record_google_oauth_failure(request, "invalid_token_response")
        return Response(status_code=400, content="OAuth authentication failed")

    # 사용자 정보 추출
    user_info = token.get("userinfo")
    if not isinstance(user_info, Mapping):
        try:
            user_info = await oauth.google.userinfo(token=token)
        except Exception as exc:
            logger.warning(
                "Google OAuth user info failed: error_type=%s",
                type(exc).__name__,
            )
            _record_google_oauth_failure(request, "user_info_failed")
            return Response(status_code=400, content="OAuth authentication failed")

    if not isinstance(user_info, Mapping):
        logger.warning(
            "Google OAuth callback returned invalid user info: response_type=%s",
            type(user_info).__name__,
        )
        _record_google_oauth_failure(request, "invalid_user_info")
        return Response(status_code=400, content="OAuth authentication failed")

    email = user_info.get("email")
    name = user_info.get("name", "Unknown")
    # Google의 sub 필드가 고유 ID
    social_id = user_info.get("sub")
    picture = user_info.get("picture")

    if not email:
        _record_google_oauth_failure(request, "email_missing")
        return Response(status_code=400, content="OAuth authentication failed")

    # 사용자 조회 또는 생성
    user = AuthService.get_or_create_social_user(
        db=db,
        email=email,
        name=name,
        social_provider="google",
        social_id=social_id,
        avatar_url=picture,
    )

    # 자체 JWT 토큰 생성
    AuthService.mark_login_success(db, user)
    access_token = AuthService.create_jwt_token(str(user.id))

    _record_auth_success(AuditAction.USER_LOGIN, request, user, provider="google")

    # 쿠키 설정
    is_production, cookie_domain = _get_cookie_config(request)

    redirect_url = AuthReturnService.build_client_redirect(request, return_path)

    redirect_response = RedirectResponse(url=redirect_url, status_code=302)
    _clear_csrf_cookie_family(request, redirect_response)
    redirect_response.set_cookie(
        key="auth_token",
        value=access_token,
        httponly=True,
        secure=is_production,
        samesite="lax",
        domain=cookie_domain,
        max_age=6 * 60 * 60,
    )

    return redirect_response
