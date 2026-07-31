import uuid
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest

from apps.gateway.application.google_oauth import GoogleOAuthProviderError
from apps.gateway.services.gmail_oauth_service import (
    GmailOAuthCancelled,
    GmailOAuthConfigurationMissing,
    GmailOAuthService,
    GmailOAuthStateInvalid,
    GmailOAuthTokenExchangeFailed,
    SESSION_KEY,
    resolve_gmail_oauth_redirect_uri,
)
from apps.shared.domain.mail_oauth import GMAIL_MODIFY_SCOPE


class _Provider:
    def __init__(self, *, token=None, email="Mailbox@Example.com", error=None):
        self.token = token or {
            "access_token": "synthetic-access-token",
            "refresh_token": "synthetic-refresh-token",
            "scope": GMAIL_MODIFY_SCOPE,
        }
        self.email = email
        self.error = error
        self.calls = []

    async def exchange_authorization_code(self, **kwargs):
        self.calls.append(("exchange", kwargs))
        if self.error is not None:
            raise self.error
        return self.token

    async def read_mailbox_email(self, *, access_token):
        self.calls.append(("profile", {"access_token": access_token}))
        if self.error is not None:
            raise self.error
        return self.email


def _service(*, provider=None, now=lambda: 1000):
    return GmailOAuthService(
        MagicMock(),
        client_id="client-id",
        client_secret="client-secret",
        provider=provider,
        now=now,
    )


@patch(
    "apps.gateway.services.gmail_oauth_service.has_organization_manager_permission",
    return_value=True,
)
def test_start_binds_actor_organization_and_pkce_in_signed_session(_permission):
    session = {}
    actor_id = uuid.uuid4()
    organization_id = uuid.uuid4()

    authorization_url = _service().start(
        actor_id=actor_id,
        organization_id=organization_id,
        credential_name="Gmail Inbox",
        redirect_uri="https://gateway.example.test/callback",
        session=session,
    )

    query = parse_qs(urlparse(authorization_url).query)
    assert query["scope"] == [GMAIL_MODIFY_SCOPE]
    assert query["code_challenge_method"] == ["S256"]
    assert query["prompt"] == ["consent"]
    assert query["access_type"] == ["offline"]
    flow = session[SESSION_KEY][query["state"][0]]
    assert flow["actor_id"] == str(actor_id)
    assert flow["organization_id"] == str(organization_id)
    assert "client-secret" not in str(flow)


@patch(
    "apps.gateway.services.gmail_oauth_service.has_organization_manager_permission",
    return_value=True,
)
def test_start_keeps_multiple_bounded_pending_flows_in_same_session(_permission):
    session = {}
    service = _service()
    actor_id = uuid.uuid4()
    organization_id = uuid.uuid4()

    urls = [
        service.start(
            actor_id=actor_id,
            organization_id=organization_id,
            credential_name=f"Gmail {index}",
            redirect_uri="https://gateway.example.test/callback",
            session=session,
        )
        for index in range(7)
    ]

    states = [parse_qs(urlparse(url).query)["state"][0] for url in urls]
    assert list(session[SESSION_KEY]) == states[-5:]


@patch(
    "apps.gateway.services.gmail_oauth_service.has_organization_manager_permission",
    return_value=True,
)
def test_state_mismatch_preserves_other_pending_oauth_flows(_permission):
    actor_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    session = {}
    service = _service()
    authorization_url = service.start(
        actor_id=actor_id,
        organization_id=organization_id,
        credential_name="Gmail",
        redirect_uri="https://gateway.example.test/callback",
        session=session,
    )
    expected_state = parse_qs(urlparse(authorization_url).query)["state"][0]

    with pytest.raises(GmailOAuthStateInvalid):
        service.cancel(
            actor_id=actor_id,
            state="unrelated-state",
            session=session,
        )

    assert expected_state in session[SESSION_KEY]


def test_start_fails_safe_without_oauth_configuration():
    with pytest.raises(GmailOAuthConfigurationMissing):
        GmailOAuthService(MagicMock(), client_id="", client_secret="").start(
            actor_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            credential_name="Gmail",
            redirect_uri="https://gateway.example.test/callback",
            session={},
        )


def test_redirect_uri_requires_explicit_configuration_outside_localhost(
    monkeypatch,
):
    monkeypatch.delenv("GOOGLE_OAUTH_REDIRECT_URI", raising=False)
    assert resolve_gmail_oauth_redirect_uri(
        "http://localhost:8000/api/v1/mail/credentials/oauth/google/callback"
    ).startswith("http://localhost:8000/")
    with pytest.raises(GmailOAuthConfigurationMissing):
        resolve_gmail_oauth_redirect_uri(
            "https://attacker.example.test/api/v1/mail/credentials/oauth/google/callback"
        )


def test_redirect_uri_uses_validated_configured_value(monkeypatch):
    expected = (
        "https://gateway.example.test/api/v1/mail/credentials/oauth/google/callback"
    )
    monkeypatch.setenv("GOOGLE_OAUTH_REDIRECT_URI", expected)

    assert (
        resolve_gmail_oauth_redirect_uri("http://untrusted.test/callback") == expected
    )


def test_production_oauth_requires_safe_session_signing_key(monkeypatch):
    monkeypatch.setenv("NODE_ENV", "production")
    service = GmailOAuthService(
        MagicMock(),
        client_id="client-id",
        client_secret="client-secret",
        session_signing_key="short",
    )

    with pytest.raises(GmailOAuthConfigurationMissing):
        service.start(
            actor_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            credential_name="Gmail",
            redirect_uri="https://gateway.example.test/callback",
            session={},
        )


@pytest.mark.asyncio
@patch(
    "apps.gateway.services.gmail_oauth_service.has_organization_manager_permission",
    return_value=True,
)
async def test_complete_exchanges_code_and_persists_through_credential_service(
    _permission,
):
    provider = _Provider()
    service = _service(provider=provider)
    actor_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    session = {
        SESSION_KEY: {
            "actor_id": str(actor_id),
            "organization_id": str(organization_id),
            "credential_name": "Gmail Inbox",
            "redirect_uri": "https://gateway.example.test/callback",
            "state": "expected-state",
            "verifier": "pkce-verifier",
            "created_at": 1000,
        }
    }
    expected = MagicMock()
    with patch(
        "apps.gateway.services.gmail_oauth_service."
        "MailCredentialService.create_gmail_oauth",
        return_value=expected,
    ) as create:
        result = await service.complete(
            actor_id=actor_id,
            state="expected-state",
            code="authorization-code",
            session=session,
        )

    assert result is expected
    assert SESSION_KEY not in session
    create.assert_called_once_with(
        actor_id=actor_id,
        organization_id=organization_id,
        credential_name="Gmail Inbox",
        email_address="mailbox@example.com",
        refresh_token="synthetic-refresh-token",
        scopes=(GMAIL_MODIFY_SCOPE,),
    )
    assert [call[0] for call in provider.calls] == ["exchange", "profile"]


@pytest.mark.asyncio
async def test_complete_consumes_flow_and_rejects_state_mismatch():
    session = {SESSION_KEY: {"state": "expected-state"}}
    with pytest.raises(GmailOAuthStateInvalid):
        await _service().complete(
            actor_id=uuid.uuid4(),
            state="attacker-state",
            code="code",
            session=session,
        )
    assert SESSION_KEY not in session


@patch(
    "apps.gateway.services.gmail_oauth_service.has_organization_manager_permission",
    return_value=True,
)
def test_cancel_consumes_valid_flow_without_provider_request(_permission):
    actor_id = uuid.uuid4()
    session = {
        SESSION_KEY: {
            "actor_id": str(actor_id),
            "organization_id": str(uuid.uuid4()),
            "credential_name": "Gmail Inbox",
            "redirect_uri": "https://gateway.example.test/callback",
            "state": "expected-state",
            "verifier": "pkce-verifier",
            "created_at": 1000,
        }
    }

    with pytest.raises(GmailOAuthCancelled):
        _service().cancel(
            actor_id=actor_id,
            state="expected-state",
            session=session,
        )
    assert SESSION_KEY not in session


@pytest.mark.asyncio
@patch(
    "apps.gateway.services.gmail_oauth_service.has_organization_manager_permission",
    return_value=True,
)
async def test_provider_error_body_is_not_exposed(_permission):
    provider = _Provider(error=GoogleOAuthProviderError("raw-provider-secret-detail"))
    actor_id = uuid.uuid4()
    session = {
        SESSION_KEY: {
            "actor_id": str(actor_id),
            "organization_id": str(uuid.uuid4()),
            "credential_name": "Gmail Inbox",
            "redirect_uri": "https://gateway.example.test/callback",
            "state": "expected-state",
            "verifier": "pkce-verifier",
            "created_at": 1000,
        }
    }
    with pytest.raises(GmailOAuthTokenExchangeFailed) as exc_info:
        await _service(provider=provider).complete(
            actor_id=actor_id,
            state="expected-state",
            code="authorization-code",
            session=session,
        )
    assert "raw-provider-secret-detail" not in str(exc_info.value)
