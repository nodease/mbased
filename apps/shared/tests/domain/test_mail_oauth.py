import pytest
from apps.shared.domain.mail_oauth import (
    GMAIL_MODIFY_SCOPE,
    GmailOAuthSecret,
    MailOAuthSecretError,
)


def test_gmail_oauth_secret_round_trip_and_repr_redaction():
    secret = GmailOAuthSecret(
        refresh_token="synthetic-refresh-token",
        scopes=(GMAIL_MODIFY_SCOPE,),
    )
    serialized = secret.serialize()
    parsed = GmailOAuthSecret.parse(serialized)

    assert parsed.refresh_token == "synthetic-refresh-token"
    assert GMAIL_MODIFY_SCOPE in parsed.scopes
    assert "synthetic-refresh-token" not in repr(parsed)


def test_gmail_oauth_secret_requires_modify_scope():
    with pytest.raises(MailOAuthSecretError) as exc_info:
        GmailOAuthSecret(
            refresh_token="synthetic-refresh-token",
            scopes=("openid",),
        ).serialize()
    assert exc_info.value.reason_code == "mail.oauth_scope_insufficient"


def test_gmail_oauth_secret_rejects_legacy_compose_only_scope():
    with pytest.raises(MailOAuthSecretError) as exc_info:
        GmailOAuthSecret(
            refresh_token="synthetic-refresh-token",
            scopes=("https://www.googleapis.com/auth/gmail.compose",),
        ).serialize()
    assert exc_info.value.reason_code == "mail.oauth_scope_insufficient"


@pytest.mark.parametrize(
    "raw",
    [
        "not-json",
        '{"version":2,"refresh_token":"x","scopes":[]}',
        '{"version":1,"refresh_token":"x","scopes":"bad"}',
    ],
)
def test_gmail_oauth_secret_rejects_malformed_payload(raw):
    with pytest.raises(MailOAuthSecretError):
        GmailOAuthSecret.parse(raw)
