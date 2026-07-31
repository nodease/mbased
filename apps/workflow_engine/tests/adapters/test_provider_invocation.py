from __future__ import annotations

import pytest

from apps.shared.services.llm_client.base import (
    ProviderFailurePhase,
    ProviderInvocationError,
)
from apps.workflow_engine.adapters.provider_invocation import (
    ProviderClientInvocationLease,
)
from apps.workflow_engine.application.provider_execution import (
    ProviderExecutionConfigurationError,
    ProviderInvocationNotSentError,
    ProviderInvocationOutcomeUnknownError,
    ProviderInvocationRejectedError,
)


class _Client:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def invoke_sync(self, *, messages, **parameters):
        self.calls.append({"messages": messages, "parameters": parameters})
        return {"choices": [{"message": {"content": "ok"}}]}


class _OutcomeUnknownClient:
    def invoke_sync(self, *, messages, **parameters):
        raise ProviderInvocationError(
            "Provider response rejected.",
            reason_code="provider_response_rejected",
            failure_phase=ProviderFailurePhase.OUTCOME_UNKNOWN,
        )


class _BeforeSendClient:
    def invoke_sync(self, *, messages, **parameters):
        raise ProviderInvocationError(
            "Provider request was not sent.",
            reason_code="provider_request_not_sent",
            failure_phase=ProviderFailurePhase.BEFORE_SEND,
        )


class _ResponseReceivedClient:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code

    def invoke_sync(self, *, messages, **parameters):
        raise ProviderInvocationError(
            "Provider request failed.",
            reason_code="provider_http_error",
            status_code=self.status_code,
            failure_phase=ProviderFailurePhase.RESPONSE_RECEIVED,
        )


def test_invocation_lease_seals_request_and_allows_only_one_attempt():
    client = _Client()
    messages = [{"role": "user", "content": [{"text": "before"}]}]
    parameters = {"tools": [{"function": {"name": "safe_tool"}}]}
    lease = ProviderClientInvocationLease(
        client=client,
        messages=tuple(messages),
        parameters=parameters,
        attribution=None,
    )

    messages[0]["content"][0]["text"] = "after"
    parameters["tools"][0]["function"]["name"] = "changed_tool"

    lease.invoke()

    assert client.calls == [
        {
            "messages": [{"role": "user", "content": [{"text": "before"}]}],
            "parameters": {"tools": [{"function": {"name": "safe_tool"}}]},
        }
    ]
    with pytest.raises(ProviderExecutionConfigurationError):
        lease.invoke()
    assert len(client.calls) == 1


def test_json_schema_revalidation_failure_prevents_provider_io() -> None:
    class _SchemaClient(_Client):
        @staticmethod
        def build_json_schema_response_format(*, name, schema):
            return {
                "type": "json_schema",
                "json_schema": {"name": name, "schema": schema},
            }

    client = _SchemaClient()

    def reject_final_request(*, messages, parameters):
        assert messages == ({"role": "user", "content": "synthetic"},)
        assert "response_format" in parameters
        raise ProviderExecutionConfigurationError()

    lease = ProviderClientInvocationLease(
        client=client,
        messages=({"role": "user", "content": "synthetic"},),
        parameters={"max_tokens": 5},
        attribution=None,
        request_revalidator=reject_final_request,
    )

    with pytest.raises(ProviderExecutionConfigurationError):
        lease.apply_json_schema_response_format(
            name="workflow_node_output",
            schema={"type": "object"},
        )

    with pytest.raises(ProviderExecutionConfigurationError):
        lease.invoke()
    assert client.calls == []


def test_invocation_lease_translates_outcome_unknown_to_application_error():
    lease = ProviderClientInvocationLease(
        client=_OutcomeUnknownClient(),
        messages=({"role": "user", "content": "synthetic"},),
        parameters={},
        attribution=None,
    )

    with pytest.raises(ProviderInvocationOutcomeUnknownError) as captured:
        lease.invoke()

    assert captured.value.failure_phase == "outcome_unknown"


def test_invocation_lease_translates_before_send_to_definitive_application_error():
    lease = ProviderClientInvocationLease(
        client=_BeforeSendClient(),
        messages=({"role": "user", "content": "synthetic"},),
        parameters={},
        attribution=None,
    )

    with pytest.raises(ProviderInvocationNotSentError) as captured:
        lease.invoke()

    assert captured.value.failure_phase == "before_send"


@pytest.mark.parametrize("status_code", [401, 403])
def test_invocation_lease_translates_auth_rejection_to_definitive_error(
    status_code: int,
) -> None:
    lease = ProviderClientInvocationLease(
        client=_ResponseReceivedClient(status_code),
        messages=({"role": "user", "content": "synthetic"},),
        parameters={},
        attribution=None,
    )

    with pytest.raises(ProviderInvocationRejectedError) as captured:
        lease.invoke()

    assert captured.value.failure_phase == "response_received"


def test_invocation_lease_does_not_treat_rate_limit_as_definitive_rejection() -> None:
    lease = ProviderClientInvocationLease(
        client=_ResponseReceivedClient(429),
        messages=({"role": "user", "content": "synthetic"},),
        parameters={},
        attribution=None,
    )

    with pytest.raises(ProviderInvocationError) as captured:
        lease.invoke()

    assert captured.value.status_code == 429
