from sqlalchemy.orm import Session

from apps.gateway.adapters.google_oauth import GuardedGoogleOAuthProvider
from apps.gateway.services.gmail_oauth_service import GmailOAuthService


def build_gmail_oauth_service(db: Session) -> GmailOAuthService:
    return GmailOAuthService(db, provider=GuardedGoogleOAuthProvider())


__all__ = ["build_gmail_oauth_service"]
