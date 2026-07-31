import pytest

from apps.shared.domain.mail_oauth import GMAIL_MODIFY_SCOPE, GmailOAuthSecret
from apps.workflow_engine.application.mail_processing import (
    GmailDraftRejectedBeforeEffect,
)
from apps.workflow_engine.application.google_oauth import GoogleOAuthRefreshError
from apps.workflow_engine.services.google_oauth_service import (
    GoogleOAuthTokenService,
)


def _secret():
    return GmailOAuthSecret(
        refresh_token="synthetic-refresh-token",
        scopes=(GMAIL_MODIFY_SCOPE,),
    ).serialize()


class _Provider:
    def __init__(self, payload=None, error=None):
        self.payload = payload or {"access_token": "synthetic-access-token"}
        self.error = error
        self.calls = []

    def refresh_access_token(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.payload


def test_refresh_returns_redacted_access_token_wrapper():
    provider = _Provider()
    token = GoogleOAuthTokenService(
        client_id="client-id",
        client_secret="client-secret",
        provider=provider,
    ).refresh(_secret())

    assert token.value == "synthetic-access-token"
    assert "synthetic-access-token" not in repr(token)
    assert provider.calls[0]["refresh_token"] == "synthetic-refresh-token"


def test_refresh_fails_safe_without_configuration():
    with pytest.raises(GmailDraftRejectedBeforeEffect) as exc_info:
        GoogleOAuthTokenService(client_id="", client_secret="").refresh(_secret())
    assert exc_info.value.reason_code == "mail.oauth_configuration_missing"


def test_refresh_does_not_expose_provider_error_body():
    with pytest.raises(GmailDraftRejectedBeforeEffect) as exc_info:
        GoogleOAuthTokenService(
            client_id="client-id",
            client_secret="client-secret",
            provider=_Provider(
                error=GoogleOAuthRefreshError("mail.oauth_token_exchange_failed")
            ),
        ).refresh(_secret())
    assert str(exc_info.value) == "mail.oauth_token_exchange_failed"
    assert "raw-provider-detail" not in str(exc_info.value)


def test_refresh_returns_validated_replacement_secret_for_atomic_rotation():
    token = GoogleOAuthTokenService(
        client_id="client-id",
        client_secret="client-secret",
        provider=_Provider(
            {
                "access_token": "synthetic-access-token",
                "refresh_token": "replacement-refresh-token",
            }
        ),
    ).refresh(_secret())

    rotated = GmailOAuthSecret.parse(token.rotated_secret_payload)
    assert rotated.refresh_token == "replacement-refresh-token"
    assert GMAIL_MODIFY_SCOPE in rotated.scopes
    assert "replacement-refresh-token" not in repr(token)


def test_invalid_grant_is_the_only_provider_error_requiring_reauthorization():
    token_service = GoogleOAuthTokenService(
        client_id="client-id",
        client_secret="client-secret",
        provider=_Provider(
            error=GoogleOAuthRefreshError("mail.oauth_reauthorization_required")
        ),
    )

    with pytest.raises(GmailDraftRejectedBeforeEffect) as exc_info:
        token_service.refresh(_secret())

    assert exc_info.value.reason_code == "mail.oauth_reauthorization_required"
