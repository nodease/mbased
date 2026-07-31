import os

from authlib.integrations.httpx_client import AsyncOAuth2Client
from authlib.integrations.starlette_client import OAuth
from authlib.integrations.starlette_client.apps import StarletteOAuth2App

from apps.shared.services.guarded_http_transport import GuardedAsyncHttpTransport
from apps.shared.services.outbound_operation_policy import (
    GOOGLE_LOGIN_OIDC,
    BoundOutboundOperation,
    require_outbound_operation_profile,
)


_GOOGLE_LOGIN_SERVER_ENDPOINTS = (
    "https://accounts.google.com/.well-known/openid-configuration",
    "https://oauth2.googleapis.com/token",
    "https://openidconnect.googleapis.com/v1/userinfo",
    "https://www.googleapis.com/oauth2/v3/certs",
)

# 환경 변수 공백 제거 (Copy/Paste 오류 방지)
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()


def _google_login_operation() -> BoundOutboundOperation:
    profile = require_outbound_operation_profile(GOOGLE_LOGIN_OIDC)
    allowed_origins = frozenset(
        origin
        for endpoint in _GOOGLE_LOGIN_SERVER_ENDPOINTS
        for origin in profile.bind(endpoint).allowed_origins
    )
    return BoundOutboundOperation(profile=profile, allowed_origins=allowed_origins)


class _GuardedGoogleLoginOAuth2Client(AsyncOAuth2Client):
    def __init__(self, *args, **kwargs) -> None:
        profile = require_outbound_operation_profile(GOOGLE_LOGIN_OIDC)
        kwargs["transport"] = GuardedAsyncHttpTransport(
            operation=_google_login_operation()
        )
        kwargs["trust_env"] = False
        kwargs["follow_redirects"] = False
        kwargs["timeout"] = profile.policy.timeout_seconds
        super().__init__(*args, **kwargs)


class _GuardedGoogleLoginOAuthApp(StarletteOAuth2App):
    client_cls = _GuardedGoogleLoginOAuth2Client


def build_google_login_oauth_registry() -> OAuth:
    registry = OAuth()
    registry.register(
        name="google",
        client_cls=_GuardedGoogleLoginOAuthApp,
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        server_metadata_url=_GOOGLE_LOGIN_SERVER_ENDPOINTS[0],
        client_kwargs={"scope": "openid email profile"},
    )
    return registry


oauth = build_google_login_oauth_registry()


__all__ = ["build_google_login_oauth_registry", "oauth"]
