from datetime import datetime

from pydantic import BaseModel


class CsrfTokenResponse(BaseModel):
    """브라우저 쿠키 인증 변경 요청용 단기 CSRF 토큰 응답."""

    token: str
    expires_at: datetime
