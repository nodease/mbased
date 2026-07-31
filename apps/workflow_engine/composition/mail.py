from apps.workflow_engine.adapters.gmail_draft_provider import GmailDraftProvider
from apps.workflow_engine.adapters.gmail_mailbox_provider import GmailMailboxProvider
from apps.workflow_engine.adapters.google_oauth import (
    GuardedGoogleOAuthRefreshProvider,
)
from apps.workflow_engine.services.google_oauth_service import GoogleOAuthTokenService


def build_google_oauth_token_service() -> GoogleOAuthTokenService:
    return GoogleOAuthTokenService(provider=GuardedGoogleOAuthRefreshProvider())


def build_gmail_mailbox_provider(*, access_token: str) -> GmailMailboxProvider:
    return GmailMailboxProvider(access_token=access_token)


def build_gmail_draft_provider(
    *, access_token: str, mailbox_email: str
) -> GmailDraftProvider:
    return GmailDraftProvider(
        access_token=access_token,
        mailbox_email=mailbox_email,
    )


__all__ = [
    "build_gmail_draft_provider",
    "build_gmail_mailbox_provider",
    "build_google_oauth_token_service",
]
