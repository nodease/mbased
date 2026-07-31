import uuid

import httpx
import pytest

from apps.shared.services.outbound_operation_http import OperationHttpRequester
from apps.workflow_engine.adapters.providers.slack import (
    SlackDeliveryMode,
    SlackEffectAdapter,
    SlackEffectRequest,
    SlackSecretMaterial,
)
from apps.workflow_engine.application.external_effect import ExternalEffectExecutor
from apps.workflow_engine.domain.external_effect import (
    ExternalEffectContext,
    ExternalEffectError,
    ReplayDecision,
)
from apps.workflow_engine.tests.fakes.external_effects import (
    InMemoryEffectAttemptRepository,
)


def _client_factory(handler):
    def factory(**kwargs):
        kwargs.pop("transport")
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    return factory


def _adapter(mode: SlackDeliveryMode, handler) -> SlackEffectAdapter:
    return SlackEffectAdapter(
        mode,
        requester=OperationHttpRequester(
            client_factory=_client_factory(handler),
        ),
    )


def _request(
    mode: SlackDeliveryMode,
    *,
    text: str = "message",
) -> SlackEffectRequest:
    if mode is SlackDeliveryMode.API:
        return SlackEffectRequest(
            mode,
            {"channel": "C123", "text": text},
            SlackSecretMaterial("token-value"),
        )
    return SlackEffectRequest(
        mode,
        {"text": text},
        SlackSecretMaterial("https://hooks.slack.com/services/a/b/c"),
    )


def _context() -> ExternalEffectContext:
    return ExternalEffectContext(
        organization_id=uuid.uuid4(),
        app_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        execution_id=uuid.uuid4(),
        node_invocation_id=uuid.uuid4(),
        node_id="slack-1",
        workflow_run_id=uuid.uuid4(),
        node_run_id=uuid.uuid4(),
    )


def _executor(repository) -> ExternalEffectExecutor:
    return ExternalEffectExecutor(
        repository=repository,
        retry_available=lambda: True,
    )


def test_successful_same_slot_reentry_reuses_result_without_second_post() -> None:
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"ok": True, "ts": "1.2"},
        )

    repository = InMemoryEffectAttemptRepository()
    executor = _executor(repository)
    context = _context()
    adapter = _adapter(SlackDeliveryMode.API, handler)
    request = _request(SlackDeliveryMode.API)

    first = executor.execute(context=context, adapter=adapter, payload=request)
    second = executor.execute(context=context, adapter=adapter, payload=request)

    assert (
        first
        == second
        == {
            "status": 200,
            "delivery_status": "delivered",
            "delivery_mode": "api",
            "message_ref": "1.2",
        }
    )
    assert calls == 1
    record = repository.find_by_slot(context=context, effect_sequence=0)
    assert record.replay_decision is ReplayDecision.REUSE_RESULT
    assert record.replay_result == first
    assert "token-value" not in repr(record)


def test_rate_limit_is_terminal_even_when_task_retry_is_available() -> None:
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"retry-after": "5"})

    executor = _executor(InMemoryEffectAttemptRepository())
    context = _context()
    adapter = _adapter(SlackDeliveryMode.API, handler)
    request = _request(SlackDeliveryMode.API)

    for _ in range(2):
        with pytest.raises(ExternalEffectError) as error:
            executor.execute(context=context, adapter=adapter, payload=request)
        assert error.value.code == "external_effect.provider_rejected_request"
        assert error.value.retryable is False

    assert calls == 1


def test_unknown_outcome_is_terminal_and_never_replayed() -> None:
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"ok": False, "error": "internal_error"},
        )

    executor = _executor(InMemoryEffectAttemptRepository())
    context = _context()
    adapter = _adapter(SlackDeliveryMode.API, handler)
    request = _request(SlackDeliveryMode.API)

    for _ in range(2):
        with pytest.raises(ExternalEffectError) as error:
            executor.execute(context=context, adapter=adapter, payload=request)
        assert error.value.code == "external_effect.outcome_unknown"
        assert error.value.retryable is False

    assert calls == 1


def test_same_slot_payload_change_fails_identity_check_without_second_post() -> None:
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"ok": True, "ts": "1.2"},
        )

    executor = _executor(InMemoryEffectAttemptRepository())
    context = _context()
    adapter = _adapter(SlackDeliveryMode.API, handler)
    executor.execute(
        context=context,
        adapter=adapter,
        payload=_request(SlackDeliveryMode.API, text="first"),
    )

    with pytest.raises(ExternalEffectError) as error:
        executor.execute(
            context=context,
            adapter=adapter,
            payload=_request(SlackDeliveryMode.API, text="changed"),
        )

    assert error.value.code == "external_effect.identity_conflict"
    assert calls == 1


def test_same_slot_api_to_webhook_operation_change_is_rejected() -> None:
    api_calls = 0
    webhook_calls = 0

    def api_handler(request):
        nonlocal api_calls
        api_calls += 1
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"ok": True, "ts": "1.2"},
        )

    def webhook_handler(request):
        nonlocal webhook_calls
        webhook_calls += 1
        return httpx.Response(200, text="ok")

    executor = _executor(InMemoryEffectAttemptRepository())
    context = _context()
    executor.execute(
        context=context,
        adapter=_adapter(SlackDeliveryMode.API, api_handler),
        payload=_request(SlackDeliveryMode.API),
    )

    with pytest.raises(ExternalEffectError) as error:
        executor.execute(
            context=context,
            adapter=_adapter(SlackDeliveryMode.WEBHOOK, webhook_handler),
            payload=_request(SlackDeliveryMode.WEBHOOK),
        )

    assert error.value.code == "external_effect.identity_conflict"
    assert api_calls == 1
    assert webhook_calls == 0
