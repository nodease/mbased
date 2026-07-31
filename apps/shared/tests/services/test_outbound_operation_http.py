from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest
from apps.shared.services.guarded_http_transport import (
    EgressResponseRejectedError,
    GuardedAsyncHttpTransport,
    GuardedHttpTransport,
)
from apps.shared.services.outbound_operation_http import (
    AsyncOperationHttpRequester,
    OperationHttpFailure,
    OperationHttpFailurePhase,
    OperationHttpRequester,
    OperationHttpTimeouts,
)
from apps.shared.services.outbound_operation_policy import (
    GMAIL_MESSAGE_READ,
    GMAIL_PROFILE_READ,
)


class _SyncClient:
    def __init__(self, response_or_error, observed: dict) -> None:
        self._response_or_error = response_or_error
        self._observed = observed

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def request(self, method, url, **kwargs):
        self._observed["request"] = (method, url, kwargs)
        if isinstance(self._response_or_error, Exception):
            raise self._response_or_error
        return self._response_or_error


class _AsyncClient:
    def __init__(self, response_or_error, observed: dict) -> None:
        self._response_or_error = response_or_error
        self._observed = observed

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def request(self, method, url, **kwargs):
        self._observed["request"] = (method, url, kwargs)
        if isinstance(self._response_or_error, Exception):
            raise self._response_or_error
        return self._response_or_error


def _sync_factory(response_or_error, observed: dict) -> Callable[..., _SyncClient]:
    def factory(**kwargs):
        observed["client"] = kwargs
        return _SyncClient(response_or_error, observed)

    return factory


def _async_factory(response_or_error, observed: dict) -> Callable[..., _AsyncClient]:
    def factory(**kwargs):
        observed["client"] = kwargs
        return _AsyncClient(response_or_error, observed)

    return factory


def test_sync_requester_builds_a_non_redirecting_operation_bound_client() -> None:
    observed = {}
    requester = OperationHttpRequester(
        client_factory=_sync_factory(
            httpx.Response(200, json={"emailAddress": "mailbox@example.test"}),
            observed,
        )
    )

    response = requester.request(
        operation_id=GMAIL_PROFILE_READ,
        approved_endpoint="https://gmail.googleapis.com",
        method="GET",
        url="https://gmail.googleapis.com/gmail/v1/users/me/profile",
        headers={"Authorization": "Bearer synthetic-token"},
    )

    assert response.status_code == 200
    assert response.json() == {"emailAddress": "mailbox@example.test"}
    assert observed["client"]["follow_redirects"] is False
    assert observed["client"]["trust_env"] is False
    assert isinstance(observed["client"]["transport"], GuardedHttpTransport)


def test_sync_operation_session_reuses_one_client_for_bounded_requests() -> None:
    observed = {"client_count": 0, "requests": []}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["requests"].append(request.url.path)
        return httpx.Response(200, json={"id": request.url.path.rsplit("/", 1)[-1]})

    def client_factory(**kwargs):
        observed["client_count"] += 1
        kwargs["transport"] = httpx.MockTransport(handler)
        return httpx.Client(**kwargs)

    requester = OperationHttpRequester(client_factory=client_factory)

    with requester.open_session(
        operation_id=GMAIL_MESSAGE_READ,
        approved_endpoint="https://gmail.googleapis.com",
    ) as session:
        first = session.request(
            method="GET",
            url="https://gmail.googleapis.com/gmail/v1/users/me/messages/one",
        )
        second = session.request(
            method="GET",
            url="https://gmail.googleapis.com/gmail/v1/users/me/messages/two",
        )
        with pytest.raises(OperationHttpFailure) as captured:
            session.request(
                method="GET",
                url="https://public-attacker.example/messages/three",
            )

    assert first.json() == {"id": "one"}
    assert second.json() == {"id": "two"}
    assert captured.value.reason_code == "egress.origin_not_allowed"
    assert captured.value.phase is OperationHttpFailurePhase.BEFORE_SEND
    assert observed["client_count"] == 1
    assert observed["requests"] == [
        "/gmail/v1/users/me/messages/one",
        "/gmail/v1/users/me/messages/two",
    ]


def test_sync_operation_session_rejects_invalid_first_url_before_client() -> None:
    observed = {}
    requester = OperationHttpRequester(
        client_factory=_sync_factory(httpx.Response(200), observed)
    )

    with requester.open_session(
        operation_id=GMAIL_MESSAGE_READ,
        approved_endpoint="https://gmail.googleapis.com",
    ) as session:
        with pytest.raises(OperationHttpFailure) as captured:
            session.request(
                method="GET",
                url="https://public-attacker.example/messages/one",
            )

    assert captured.value.reason_code == "egress.origin_not_allowed"
    assert captured.value.phase is OperationHttpFailurePhase.BEFORE_SEND
    assert "client" not in observed


def test_requester_preserves_bounded_phase_timeouts() -> None:
    observed = {}
    requester = OperationHttpRequester(
        client_factory=_sync_factory(httpx.Response(200), observed)
    )

    requester.request(
        operation_id=GMAIL_PROFILE_READ,
        approved_endpoint="https://gmail.googleapis.com",
        method="GET",
        url="https://gmail.googleapis.com/gmail/v1/users/me/profile",
        timeouts=OperationHttpTimeouts(
            connect_seconds=3,
            write_seconds=5,
            read_seconds=10,
            pool_seconds=2,
        ),
    )

    timeout = observed["client"]["timeout"]
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.connect == 3
    assert timeout.write == 5
    assert timeout.read == 10
    assert timeout.pool == 2


def test_requester_rejects_phase_timeout_above_operation_limit() -> None:
    observed = {}
    requester = OperationHttpRequester(
        client_factory=_sync_factory(httpx.Response(200), observed)
    )

    with pytest.raises(OperationHttpFailure) as captured:
        requester.request(
            operation_id=GMAIL_PROFILE_READ,
            approved_endpoint="https://gmail.googleapis.com",
            method="GET",
            url="https://gmail.googleapis.com/gmail/v1/users/me/profile",
            timeouts=OperationHttpTimeouts(
                connect_seconds=11,
                write_seconds=5,
                read_seconds=10,
                pool_seconds=2,
            ),
        )

    assert captured.value.reason_code == "egress.timeout_policy_invalid"
    assert captured.value.phase is OperationHttpFailurePhase.BEFORE_SEND
    assert "client" not in observed


def test_requester_rejects_wrong_origin_before_client_construction() -> None:
    observed = {}
    requester = OperationHttpRequester(
        client_factory=_sync_factory(httpx.Response(200), observed)
    )

    with pytest.raises(OperationHttpFailure) as captured:
        requester.request(
            operation_id=GMAIL_PROFILE_READ,
            approved_endpoint="https://gmail.googleapis.com",
            method="GET",
            url="https://public-attacker.example/profile",
        )

    assert captured.value.reason_code == "egress.origin_not_allowed"
    assert captured.value.phase is OperationHttpFailurePhase.BEFORE_SEND
    assert "client" not in observed


@pytest.mark.parametrize(
    ("error", "phase"),
    [
        (
            httpx.ConnectTimeout("timeout", request=httpx.Request("GET", "https://x")),
            OperationHttpFailurePhase.BEFORE_SEND,
        ),
        (
            EgressResponseRejectedError("egress.response_too_large"),
            OperationHttpFailurePhase.OUTCOME_UNKNOWN,
        ),
    ],
)
def test_requester_classifies_failure_without_exposing_raw_exception(
    error,
    phase,
) -> None:
    requester = OperationHttpRequester(client_factory=_sync_factory(error, {}))

    with pytest.raises(OperationHttpFailure) as captured:
        requester.request(
            operation_id=GMAIL_PROFILE_READ,
            approved_endpoint="https://gmail.googleapis.com",
            method="GET",
            url="https://gmail.googleapis.com/gmail/v1/users/me/profile",
        )

    assert captured.value.phase is phase
    assert "https://x" not in str(captured.value)


@pytest.mark.asyncio
async def test_async_requester_uses_the_same_operation_boundary() -> None:
    observed = {}
    requester = AsyncOperationHttpRequester(
        client_factory=_async_factory(httpx.Response(200, json={"ok": True}), observed)
    )

    response = await requester.request(
        operation_id=GMAIL_PROFILE_READ,
        approved_endpoint="https://gmail.googleapis.com",
        method="GET",
        url="https://gmail.googleapis.com/gmail/v1/users/me/profile",
    )

    assert response.json() == {"ok": True}
    assert observed["client"]["follow_redirects"] is False
    assert observed["client"]["trust_env"] is False
    assert isinstance(observed["client"]["transport"], GuardedAsyncHttpTransport)
