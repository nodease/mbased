from __future__ import annotations

import os
from dataclasses import dataclass, field

from apps.shared.domain.mail_oauth import GmailOAuthSecret, MailOAuthSecretError
from apps.workflow_engine.application.google_oauth import (
    GoogleOAuthRefreshError,
    GoogleOAuthRefreshProviderPort,
)
from apps.workflow_engine.application.mail_processing import (
    GmailDraftRejectedBeforeEffect,
)


@dataclass(frozen=True)
class GoogleAccessToken:
    value: str
    rotated_secret_payload: str | None = field(default=None, repr=False)

    def __repr__(self) -> str:
        return "GoogleAccessToken(value=[redacted])"


class GoogleOAuthTokenService:
    def __init__(
        self,
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
        provider: GoogleOAuthRefreshProviderPort | None = None,
    ) -> None:
        self._client_id = (client_id or os.getenv("GOOGLE_CLIENT_ID", "")).strip()
        self._client_secret = (
            client_secret or os.getenv("GOOGLE_CLIENT_SECRET", "")
        ).strip()
        self._provider = provider

    def refresh(self, encrypted_payload: str) -> GoogleAccessToken:
        if not self._client_id or not self._client_secret:
            raise GmailDraftRejectedBeforeEffect("mail.oauth_configuration_missing")
        try:
            secret = GmailOAuthSecret.parse(encrypted_payload)
        except MailOAuthSecretError as exc:
            raise GmailDraftRejectedBeforeEffect(exc.reason_code) from exc
        try:
            payload = self._require_provider().refresh_access_token(
                client_id=self._client_id,
                client_secret=self._client_secret,
                refresh_token=secret.refresh_token,
            )
        except GoogleOAuthRefreshError as exc:
            raise GmailDraftRejectedBeforeEffect(exc.reason_code) from None
        try:
            access_token = payload.get("access_token")
        except (TypeError, ValueError, AttributeError):
            raise GmailDraftRejectedBeforeEffect(
                "mail.oauth_token_exchange_failed"
            ) from None
        if not isinstance(access_token, str) or not access_token:
            raise GmailDraftRejectedBeforeEffect("mail.oauth_token_exchange_failed")
        rotated_secret_payload = None
        replacement_refresh_token = payload.get("refresh_token")
        if replacement_refresh_token is not None:
            if not isinstance(replacement_refresh_token, str):
                raise GmailDraftRejectedBeforeEffect("mail.oauth_token_exchange_failed")
            if (
                replacement_refresh_token
                and replacement_refresh_token != secret.refresh_token
            ):
                rotated_secret_payload = GmailOAuthSecret(
                    refresh_token=replacement_refresh_token,
                    scopes=secret.scopes,
                ).serialize()
        return GoogleAccessToken(
            access_token,
            rotated_secret_payload=rotated_secret_payload,
        )

    def _require_provider(self) -> GoogleOAuthRefreshProviderPort:
        if self._provider is None:
            raise GoogleOAuthRefreshError("mail.oauth_token_exchange_failed")
        return self._provider
