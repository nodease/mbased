from __future__ import annotations

import json

import pytest

from apps.shared.services.outbound_operation_http import (
    OperationHttpFailure,
    OperationHttpFailurePhase,
    OperationHttpResponse,
)
from apps.shared.services.outbound_operation_policy import GOOGLE_OAUTH_REFRESH
from apps.workflow_engine.adapters.google_oauth import (
    GuardedGoogleOAuthRefreshProvider,
)
from apps.workflow_engine.application.google_oauth import GoogleOAuthRefreshError


class _Requester:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def request(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _response(status_code, payload):
    return OperationHttpResponse(
        status_code=status_code,
        headers={"content-type": "application/json"},
        content=json.dumps(payload).encode("utf-8"),
    )


def test_refresh_provider_uses_fixed_operation_and_origin() -> None:
    requester = _Requester(_response(200, {"access_token": "synthetic-token"}))
    provider = GuardedGoogleOAuthRefreshProvider(requester=requester)

    result = provider.refresh_access_token(
        client_id="client-id",
        client_secret="client-secret",
        refresh_token="synthetic-refresh-token",
    )

    assert result == {"access_token": "synthetic-token"}
    assert requester.calls[0]["operation_id"] == GOOGLE_OAUTH_REFRESH
    assert requester.calls[0]["url"] == "https://oauth2.googleapis.com/token"


def test_only_invalid_grant_requests_reauthorization() -> None:
    provider = GuardedGoogleOAuthRefreshProvider(
        requester=_Requester(_response(400, {"error": "invalid_grant"}))
    )

    with pytest.raises(GoogleOAuthRefreshError) as captured:
        provider.refresh_access_token(
            client_id="client-id",
            client_secret="client-secret",
            refresh_token="synthetic-refresh-token",
        )

    assert captured.value.reason_code == "mail.oauth_reauthorization_required"


def test_transport_failure_is_safe_and_not_replayed() -> None:
    requester = _Requester(
        OperationHttpFailure(
            "egress.request_outcome_unknown",
            OperationHttpFailurePhase.OUTCOME_UNKNOWN,
        )
    )
    provider = GuardedGoogleOAuthRefreshProvider(requester=requester)

    with pytest.raises(GoogleOAuthRefreshError) as captured:
        provider.refresh_access_token(
            client_id="client-id",
            client_secret="client-secret",
            refresh_token="synthetic-refresh-token",
        )

    assert captured.value.reason_code == "mail.oauth_token_exchange_failed"
    assert len(requester.calls) == 1
