from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from apps.gateway.application.google_oauth import GoogleOAuthProviderError
from apps.shared.services.outbound_operation_http import (
    AsyncOperationHttpRequester,
    OperationHttpFailure,
)
from apps.shared.services.outbound_operation_policy import (
    GMAIL_PROFILE_READ,
    GOOGLE_OAUTH_AUTHORIZATION_CODE_EXCHANGE,
)

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_PROFILE_URL = "https://gmail.googleapis.com/gmail/v1/users/me/profile"


class GuardedGoogleOAuthProvider:
    def __init__(self, *, requester: AsyncOperationHttpRequester | None = None) -> None:
        self._requester = requester or AsyncOperationHttpRequester()

    async def exchange_authorization_code(
        self,
        *,
        client_id: str,
        client_secret: str,
        code: str,
        verifier: str,
        redirect_uri: str,
    ) -> Mapping[str, Any]:
        try:
            response = await self._requester.request(
                operation_id=GOOGLE_OAUTH_AUTHORIZATION_CODE_EXCHANGE,
                approved_endpoint=GOOGLE_TOKEN_URL,
                method="POST",
                url=GOOGLE_TOKEN_URL,
                form_data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code,
                    "code_verifier": verifier,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect_uri,
                },
                headers={"Accept": "application/json"},
            )
        except OperationHttpFailure:
            raise GoogleOAuthProviderError("mail.oauth_provider_unavailable") from None
        if response.status_code != 200:
            raise GoogleOAuthProviderError("mail.oauth_provider_rejected")
        try:
            payload = response.json()
        except (TypeError, ValueError):
            raise GoogleOAuthProviderError(
                "mail.oauth_provider_response_invalid"
            ) from None
        if not isinstance(payload, dict):
            raise GoogleOAuthProviderError("mail.oauth_provider_response_invalid")
        return payload

    async def read_mailbox_email(self, *, access_token: str) -> str:
        try:
            response = await self._requester.request(
                operation_id=GMAIL_PROFILE_READ,
                approved_endpoint=GMAIL_PROFILE_URL,
                method="GET",
                url=GMAIL_PROFILE_URL,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/json",
                },
            )
        except OperationHttpFailure:
            raise GoogleOAuthProviderError("mail.oauth_provider_unavailable") from None
        if response.status_code != 200:
            raise GoogleOAuthProviderError("mail.oauth_provider_rejected")
        try:
            payload = response.json()
            email_address = payload.get("emailAddress")
        except (AttributeError, TypeError, ValueError):
            raise GoogleOAuthProviderError(
                "mail.oauth_provider_response_invalid"
            ) from None
        if not isinstance(email_address, str):
            raise GoogleOAuthProviderError("mail.oauth_provider_response_invalid")
        return email_address


__all__ = ["GuardedGoogleOAuthProvider"]
