from __future__ import annotations

import pytest

from apps.gateway.adapters.google_oauth import GuardedGoogleOAuthProvider
from apps.gateway.application.google_oauth import GoogleOAuthProviderError
from apps.shared.services.outbound_operation_http import (
    OperationHttpFailure,
    OperationHttpFailurePhase,
    OperationHttpResponse,
)
from apps.shared.services.outbound_operation_policy import (
    GMAIL_PROFILE_READ,
    GOOGLE_OAUTH_AUTHORIZATION_CODE_EXCHANGE,
)


class _Requester:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def request(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _json_response(payload, status_code=200):
    import json

    return OperationHttpResponse(
        status_code=status_code,
        headers={"content-type": "application/json"},
        content=json.dumps(payload).encode("utf-8"),
    )


@pytest.mark.asyncio
async def test_provider_binds_exchange_and_profile_to_fixed_operations() -> None:
    requester = _Requester(
        [
            _json_response({"access_token": "synthetic-token"}),
            _json_response({"emailAddress": "mailbox@example.test"}),
        ]
    )
    provider = GuardedGoogleOAuthProvider(requester=requester)

    token = await provider.exchange_authorization_code(
        client_id="client-id",
        client_secret="client-secret",
        code="synthetic-code",
        verifier="synthetic-verifier",
        redirect_uri="https://gateway.example.test/callback",
    )
    email = await provider.read_mailbox_email(access_token="synthetic-token")

    assert token == {"access_token": "synthetic-token"}
    assert email == "mailbox@example.test"
    assert [call["operation_id"] for call in requester.calls] == [
        GOOGLE_OAUTH_AUTHORIZATION_CODE_EXCHANGE,
        GMAIL_PROFILE_READ,
    ]
    assert requester.calls[0]["url"] == "https://oauth2.googleapis.com/token"
    assert requester.calls[1]["url"].endswith("/gmail/v1/users/me/profile")


@pytest.mark.asyncio
async def test_provider_maps_guard_failure_to_a_safe_domain_error() -> None:
    requester = _Requester(
        [
            OperationHttpFailure(
                "egress.connection_failed",
                OperationHttpFailurePhase.BEFORE_SEND,
            )
        ]
    )
    provider = GuardedGoogleOAuthProvider(requester=requester)

    with pytest.raises(GoogleOAuthProviderError) as captured:
        await provider.exchange_authorization_code(
            client_id="client-id",
            client_secret="client-secret",
            code="synthetic-code",
            verifier="synthetic-verifier",
            redirect_uri="https://gateway.example.test/callback",
        )

    assert str(captured.value) == "mail.oauth_provider_unavailable"
