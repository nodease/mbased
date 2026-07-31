import uuid

import pytest

from apps.gateway.application.agent_builder.intent_usage import (
    AGENT_BUILDER_INTENT_RUNTIME_SURFACE,
    AgentBuilderIntentUsageContext,
    AgentBuilderIntentUsageSampleError,
    normalize_intent_usage_sample,
)


def test_normalize_intent_usage_sample_keeps_only_billing_fields():
    credential_id = uuid.uuid4()
    model_id = uuid.uuid4()
    provider_response = {
        "choices": [{"message": {"content": "{}"}}],
        "usage": {
            "prompt_tokens": 120,
            "completion_tokens": 30,
            "total_tokens": 150,
            "provider_detail": {"ignored": True},
        },
        "provider_metadata": {"ignored": True},
    }

    sample = normalize_intent_usage_sample(
        provider_response["usage"],
        credential_id=credential_id,
        model_id=model_id,
        model_api_id="gpt-example",
        attempt=1,
        latency_ms=42,
    )

    assert sample.credential_id == credential_id
    assert sample.model_id == model_id
    assert sample.model_api_id == "gpt-example"
    assert sample.attempt == 1
    assert sample.prompt_tokens == 120
    assert sample.completion_tokens == 30
    assert sample.latency_ms == 42
    assert not hasattr(sample, "choices")
    assert not hasattr(sample, "provider_metadata")


def test_normalize_intent_usage_sample_accepts_provider_token_aliases():
    sample = normalize_intent_usage_sample(
        {"input_tokens": 10, "output_tokens": 4},
        credential_id=uuid.uuid4(),
        model_id=uuid.uuid4(),
        model_api_id="model-example",
        attempt=2,
        latency_ms=8.8,
    )

    assert sample.prompt_tokens == 10
    assert sample.completion_tokens == 4
    assert sample.latency_ms == 9


@pytest.mark.parametrize(
    "usage",
    [
        {},
        None,
        {"prompt_tokens": 1},
        {"prompt_tokens": -1, "completion_tokens": 1},
        {"prompt_tokens": True, "completion_tokens": 1},
        {
            "choices": [{"message": {"content": "must-not-enter-normalizer"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        },
    ],
)
def test_normalize_intent_usage_sample_rejects_unknown_or_invalid_usage(usage):
    with pytest.raises(AgentBuilderIntentUsageSampleError):
        normalize_intent_usage_sample(
            usage,
            credential_id=uuid.uuid4(),
            model_id=uuid.uuid4(),
            model_api_id="model-example",
            attempt=1,
            latency_ms=1,
        )


def test_intent_usage_context_uses_stable_runtime_surface():
    context = AgentBuilderIntentUsageContext(
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        request_id=uuid.uuid4(),
    )

    assert context.runtime_surface == AGENT_BUILDER_INTENT_RUNTIME_SURFACE
