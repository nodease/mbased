from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from apps.shared.services.outbound_operation_http import (
    OperationHttpFailure,
    OperationHttpRequester,
)
from apps.shared.services.outbound_operation_policy import GOOGLE_OAUTH_REFRESH
from apps.workflow_engine.application.google_oauth import GoogleOAuthRefreshError

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


class GuardedGoogleOAuthRefreshProvider:
    def __init__(self, *, requester: OperationHttpRequester | None = None) -> None:
        self._requester = requester or OperationHttpRequester()

    def refresh_access_token(
        self,
        *,
        client_id: str,
        client_secret: str,
        refresh_token: str,
    ) -> Mapping[str, Any]:
        try:
            response = self._requester.request(
                operation_id=GOOGLE_OAUTH_REFRESH,
                approved_endpoint=GOOGLE_TOKEN_URL,
                method="POST",
                url=GOOGLE_TOKEN_URL,
                form_data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
                headers={"Accept": "application/json"},
            )
        except OperationHttpFailure:
            raise GoogleOAuthRefreshError("mail.oauth_token_exchange_failed") from None
        try:
            payload = response.json()
        except (TypeError, ValueError):
            raise GoogleOAuthRefreshError("mail.oauth_token_exchange_failed") from None
        if not isinstance(payload, dict):
            raise GoogleOAuthRefreshError("mail.oauth_token_exchange_failed")
        if response.status_code != 200:
            reason_code = "mail.oauth_token_exchange_failed"
            if response.status_code == 400 and payload.get("error") == "invalid_grant":
                reason_code = "mail.oauth_reauthorization_required"
            raise GoogleOAuthRefreshError(reason_code)
        return payload


__all__ = ["GuardedGoogleOAuthRefreshProvider"]
