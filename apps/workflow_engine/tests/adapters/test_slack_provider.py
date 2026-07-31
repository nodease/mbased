import copy
import pickle

import httpx
import pytest

from apps.shared.services.guarded_http_transport import GuardedHttpTransport
from apps.shared.services.outbound_operation_http import (
    OperationHttpFailure,
    OperationHttpFailurePhase,
    OperationHttpRequester,
)
from apps.workflow_engine.adapters.providers.slack import (
    SlackDeliveryMode,
    SlackDeliveryPolicy,
    SlackEffectAdapter,
    SlackEffectRequest,
    SlackSecretMaterial,
    parse_retry_after,
)
from apps.workflow_engine.domain.external_effect import (
    EffectInvocationFailure,
    EffectOutcome,
)


class _FailingRequester:
    def request(self, **_kwargs):
        raise OperationHttpFailure(
            "egress.peer_mismatch",
            OperationHttpFailurePhase.OUTCOME_UNKNOWN,
        )


def _client_factory(handler, observed: dict | None = None):
    def factory(**kwargs):
        if observed is not None:
            observed.update(kwargs)
        kwargs.pop("transport")
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    return factory


def _adapter(
    mode: SlackDeliveryMode,
    handler,
    *,
    policy: SlackDeliveryPolicy | None = None,
    observed: dict | None = None,
    requester=None,
) -> SlackEffectAdapter:
    return SlackEffectAdapter(
        mode,
        policy=policy,
        requester=requester
        or OperationHttpRequester(
            client_factory=_client_factory(handler, observed),
        ),
    )


def _request(
    mode: SlackDeliveryMode,
    *,
    payload: dict | None = None,
    secret: str | None = None,
) -> SlackEffectRequest:
    if payload is None:
        payload = (
            {"channel": "C123", "text": "message"}
            if mode is SlackDeliveryMode.API
            else {"text": "message"}
        )
    if secret is None:
        secret = (
            "token-value"
            if mode is SlackDeliveryMode.API
            else "https://hooks.slack.com/services/a/b/c"
        )
    return SlackEffectRequest(mode, payload, SlackSecretMaterial(secret))


def _invoke(adapter: SlackEffectAdapter, request: SlackEffectRequest):
    prepared = adapter.prepare_effect(request)
    call = adapter.finalize_provider_call(prepared, None)
    return adapter.invoke_effect(call)


def test_api_success_uses_hardened_transport_and_safe_projection() -> None:
    observed: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("https://slack.com/api/chat.postMessage")
        assert request.headers["authorization"] == "Bearer token-value"
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(
            200,
            headers={"content-type": "application/json; charset=utf-8"},
            json={"ok": True, "ts": "123.456"},
        )

    adapter = _adapter(SlackDeliveryMode.API, handler, observed=observed)

    result = _invoke(adapter, _request(SlackDeliveryMode.API))

    assert result.output == {
        "status": 200,
        "delivery_status": "delivered",
        "delivery_mode": "api",
        "message_ref": "123.456",
    }
    assert observed["follow_redirects"] is False
    assert observed["trust_env"] is False
    assert isinstance(observed["transport"], GuardedHttpTransport)
    timeout = observed["timeout"]
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.connect == 3
    assert timeout.write == 5
    assert timeout.read == 10
    assert timeout.pool == 2
    assert adapter.trace_metadata["slack"]["has_message_ref"] is True
    assert "token-value" not in repr(adapter.trace_metadata)


def test_webhook_success_has_no_authorization_or_message_reference() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "authorization" not in request.headers
        return httpx.Response(200, text="ok")

    adapter = _adapter(SlackDeliveryMode.WEBHOOK, handler)

    result = _invoke(adapter, _request(SlackDeliveryMode.WEBHOOK))

    assert result.output == {
        "status": 200,
        "delivery_status": "delivered",
        "delivery_mode": "webhook",
    }


@pytest.mark.parametrize(
    ("provider_error", "expected_outcome", "expected_reason"),
    [
        ("channel_not_found", EffectOutcome.FAILED_BEFORE_EFFECT, "request_rejected"),
        (
            "internal_error",
            EffectOutcome.EFFECT_OUTCOME_UNKNOWN,
            "provider_unavailable",
        ),
        ("future_error", EffectOutcome.EFFECT_OUTCOME_UNKNOWN, "response_unverified"),
    ],
)
def test_api_ok_false_is_classified_by_explicit_allowlist(
    provider_error: str,
    expected_outcome: EffectOutcome,
    expected_reason: str,
) -> None:
    adapter = _adapter(
        SlackDeliveryMode.API,
        lambda request: httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"ok": False, "error": provider_error},
        ),
    )

    with pytest.raises(EffectInvocationFailure) as error:
        _invoke(adapter, _request(SlackDeliveryMode.API))

    assert error.value.outcome is expected_outcome
    assert error.value.retry_before_effect is False
    assert adapter.trace_metadata["slack"]["provider_reason"] == expected_reason
    assert provider_error not in repr(adapter.trace_metadata)


def test_rate_limit_is_a_non_replayed_failure_with_bounded_hint() -> None:
    adapter = _adapter(
        SlackDeliveryMode.API,
        lambda request: httpx.Response(
            429,
            headers={"retry-after": "12"},
            text="rate limited",
        ),
    )

    with pytest.raises(EffectInvocationFailure) as error:
        _invoke(adapter, _request(SlackDeliveryMode.API))

    assert error.value.outcome is EffectOutcome.FAILED_BEFORE_EFFECT
    assert error.value.retry_before_effect is False
    assert adapter.trace_metadata["slack"]["provider_retryable"] is True
    assert adapter.trace_metadata["slack"]["retry_after_seconds"] == 12


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, headers={"content-type": "text/plain"}, text="ok"),
        httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=b"not-json",
        ),
        httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"ok": True},
        ),
        httpx.Response(503, text="unavailable"),
    ],
)
def test_unverified_api_response_stops_as_outcome_unknown(
    response: httpx.Response,
) -> None:
    adapter = _adapter(SlackDeliveryMode.API, lambda request: response)

    with pytest.raises(EffectInvocationFailure) as error:
        _invoke(adapter, _request(SlackDeliveryMode.API))

    assert error.value.outcome is EffectOutcome.EFFECT_OUTCOME_UNKNOWN
    assert error.value.retry_before_effect is False


def test_redirect_and_post_request_peer_failure_are_outcome_unknown() -> None:
    redirect = _adapter(
        SlackDeliveryMode.API,
        lambda request: httpx.Response(
            302, headers={"location": "https://example.com"}
        ),
    )
    peer_rejected = _adapter(
        SlackDeliveryMode.API,
        lambda request: httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"ok": True, "ts": "1.2"},
        ),
        requester=_FailingRequester(),
    )

    for adapter in (redirect, peer_rejected):
        with pytest.raises(EffectInvocationFailure) as error:
            _invoke(adapter, _request(SlackDeliveryMode.API))
        assert error.value.outcome is EffectOutcome.EFFECT_OUTCOME_UNKNOWN


def test_webhook_documented_rejection_is_failed_before_effect() -> None:
    adapter = _adapter(
        SlackDeliveryMode.WEBHOOK,
        lambda request: httpx.Response(403, text="action_prohibited"),
    )

    with pytest.raises(EffectInvocationFailure) as error:
        _invoke(adapter, _request(SlackDeliveryMode.WEBHOOK))

    assert error.value.outcome is EffectOutcome.FAILED_BEFORE_EFFECT
    assert error.value.retry_before_effect is False


def test_invalid_webhook_is_rejected_before_transport() -> None:
    transport_called = False

    def handler(request):
        nonlocal transport_called
        transport_called = True
        return httpx.Response(200, text="ok")

    adapter = _adapter(SlackDeliveryMode.WEBHOOK, handler)

    with pytest.raises(ValueError, match="webhook"):
        adapter.prepare_effect(
            _request(
                SlackDeliveryMode.WEBHOOK,
                secret="https://hooks.slack.com/services/a/b/c?leak=1",
            )
        )

    assert transport_called is False


def test_canonical_digest_binds_payload_and_secret_without_exposing_them() -> None:
    adapter = _adapter(
        SlackDeliveryMode.API,
        lambda request: httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"ok": True, "ts": "1.2"},
        ),
    )
    first = adapter.prepare_effect(
        _request(
            SlackDeliveryMode.API,
            payload={"text": "message", "channel": "C123"},
        )
    )
    reordered = adapter.prepare_effect(
        _request(
            SlackDeliveryMode.API,
            payload={"channel": "C123", "text": "message"},
        )
    )
    changed_secret = adapter.prepare_effect(
        _request(SlackDeliveryMode.API, secret="another-token")
    )

    assert first.effect_input_digest == reordered.effect_input_digest
    assert first.effect_input_digest != changed_secret.effect_input_digest
    assert "token-value" not in repr(first)


def test_replay_projection_rejects_raw_or_mode_incompatible_output() -> None:
    api = _adapter(SlackDeliveryMode.API, lambda request: httpx.Response(500))
    webhook = _adapter(SlackDeliveryMode.WEBHOOK, lambda request: httpx.Response(500))
    safe = {
        "status": 200,
        "delivery_status": "delivered",
        "delivery_mode": "api",
        "message_ref": "1.2",
    }

    assert api.replay_projection(safe, profile=api.profile) == safe
    with pytest.raises(ValueError):
        api.replay_projection({**safe, "raw": {}}, profile=api.profile)
    with pytest.raises(ValueError):
        webhook.replay_projection(safe, profile=webhook.profile)


def test_secret_holder_cannot_be_logged_copied_or_serialized() -> None:
    secret = SlackSecretMaterial("sensitive-value")

    assert repr(secret) == "SlackSecretMaterial(<redacted>)"
    assert "sensitive-value" not in str(secret)
    with pytest.raises(TypeError):
        copy.deepcopy(secret)
    with pytest.raises(TypeError):
        pickle.dumps(secret)


def test_prepared_request_repr_redacts_message_and_secret() -> None:
    adapter = _adapter(
        SlackDeliveryMode.API,
        lambda request: httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"ok": True, "ts": "1.2"},
        ),
    )
    prepared = adapter.prepare_effect(
        _request(
            SlackDeliveryMode.API,
            payload={"channel": "C123", "text": "sensitive-message"},
            secret="sensitive-token",
        )
    )

    rendered = repr(prepared.request)

    assert "sensitive-message" not in rendered
    assert "sensitive-token" not in rendered
    assert "<redacted>" in rendered


def test_webhook_success_body_must_be_exact_ok() -> None:
    adapter = _adapter(
        SlackDeliveryMode.WEBHOOK,
        lambda request: httpx.Response(200, content=b"ok\n"),
    )

    with pytest.raises(EffectInvocationFailure) as error:
        _invoke(adapter, _request(SlackDeliveryMode.WEBHOOK))

    assert error.value.outcome is EffectOutcome.EFFECT_OUTCOME_UNKNOWN


def test_retry_after_parser_rejects_ambiguous_or_unbounded_values() -> None:
    assert parse_retry_after(["12"], cap=60) == 12
    assert parse_retry_after(["61"], cap=60) is None
    assert parse_retry_after(["1", "2"], cap=60) is None
    assert parse_retry_after(["Wed, 21 Oct 2015 07:28:00 GMT"], cap=60) is None
