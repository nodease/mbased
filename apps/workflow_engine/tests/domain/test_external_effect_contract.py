import re
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from apps.workflow_engine.domain.external_effect import (
    EffectInvocationFailure,
    EffectOutcome,
    ExternalEffectError,
    ProviderReplayCapability,
    ProviderContractRegistry,
    ReplayDecision,
    ResultReuseCapability,
    build_provider_idempotency_key,
    canonical_replay_result,
    decide_replay,
    provider_contract_registry,
)
from apps.workflow_engine.tests.fakes.external_effects import FakeEffectAdapter


def test_production_profiles_are_conservative() -> None:
    registry = provider_contract_registry(include_test_profiles=False)

    http = registry.get(
        "generic_http", "generic_http.request", "generic_http.request.v1"
    )
    slack = registry.get("slack", "slack.http.request", "slack.http.request.v1")
    github = registry.get(
        "github", "github.issue_comment.create", "github.issue_comment.create.v1"
    )

    assert http.provider_replay is ProviderReplayCapability.UNKNOWN
    assert slack.provider_replay is ProviderReplayCapability.UNKNOWN
    assert github.provider_replay is ProviderReplayCapability.UNSUPPORTED
    assert {
        http.result_reuse,
        slack.result_reuse,
        github.result_reuse,
    } == {ResultReuseCapability.UNAVAILABLE}
    assert all(profile.key_field is None for profile in (http, slack, github))


def test_dedicated_slack_profiles_reuse_only_safe_local_results() -> None:
    registry = provider_contract_registry(include_test_profiles=False)
    api = registry.get(
        "slack",
        "slack.chat.post_message",
        "slack.chat.post_message.v1",
    )
    webhook = registry.get(
        "slack",
        "slack.incoming_webhook.post",
        "slack.incoming_webhook.post.v1",
    )

    for profile in (api, webhook):
        assert profile.provider_replay is ProviderReplayCapability.UNKNOWN
        assert profile.result_reuse is ResultReuseCapability.SUPPORTED
        assert profile.key_transport == "unknown"
        assert profile.key_field is None
        assert profile.replay_projection_semantics == (
            "slack.delivery.replay_projection.v1"
        )


def test_fake_profile_is_not_in_production_registry() -> None:
    registry = provider_contract_registry(include_test_profiles=False)

    with pytest.raises(KeyError):
        registry.get("fake", "fake.create_effect", "fake.create_effect.v1")


def test_registry_requires_one_active_version_for_every_operation() -> None:
    profile = provider_contract_registry().active(
        "generic_http",
        "generic_http.request",
    )

    with pytest.raises(ValueError, match="one active contract"):
        ProviderContractRegistry((profile,), active_versions={})
    with pytest.raises(ValueError, match="not registered"):
        ProviderContractRegistry(
            (profile,),
            active_versions={
                (profile.provider, profile.operation): "generic_http.request.v2"
            },
        )


def test_supported_body_profile_requires_canonical_json_pointer() -> None:
    profile = provider_contract_registry(include_test_profiles=True).get(
        "fake",
        "fake.create_effect",
        "fake.create_effect.v1",
    )

    with pytest.raises(ValueError, match="JSON Pointer"):
        replace(profile, key_transport="body", key_field="metadata/request_id")


def test_external_effect_error_rejects_unallowlisted_message() -> None:
    marker = "provider-secret-in-error-code"

    error = ExternalEffectError(marker, retryable=True)

    assert error.code == "external_effect.stopped"
    assert error.retryable is False
    assert marker not in str(error)


def test_supported_fake_key_is_stable_base64url_and_within_limit() -> None:
    identity = {
        "organization_id": uuid.uuid4(),
        "app_id": uuid.uuid4(),
        "workflow_id": uuid.uuid4(),
        "execution_id": uuid.uuid4(),
        "node_invocation_id": uuid.uuid4(),
        "operation": "fake.create_effect",
        "effect_sequence": 0,
    }

    first = build_provider_idempotency_key(b"test-secret", **identity)
    second = build_provider_idempotency_key(b"test-secret", **identity)

    assert first == second
    assert len(first) == 43
    assert re.fullmatch(r"[A-Za-z0-9_-]+", first)


def test_provider_key_v1_fixed_vector() -> None:
    key = build_provider_idempotency_key(
        b"test-secret",
        organization_id=uuid.UUID(int=1),
        app_id=uuid.UUID(int=2),
        workflow_id=uuid.UUID(int=3),
        execution_id=uuid.UUID(int=4),
        node_invocation_id=uuid.UUID(int=5),
        operation="fake.create_effect",
        effect_sequence=0,
    )

    assert key == "KtVvBZ84myT3jzC5D35FIl75QFyilr0xoFBIXxFjJWw"


def test_replay_matrix_blocks_unknown_provider_after_ambiguous_effect() -> None:
    decision = decide_replay(
        outcome=EffectOutcome.EFFECT_OUTCOME_UNKNOWN,
        provider_replay=ProviderReplayCapability.UNKNOWN,
        result_reuse=ResultReuseCapability.UNAVAILABLE,
        has_replay_result=False,
    )

    assert decision is ReplayDecision.STOP


def test_supported_provider_can_only_replay_before_frozen_deadline() -> None:
    now = datetime.now(timezone.utc)

    assert (
        decide_replay(
            outcome=EffectOutcome.EFFECT_OUTCOME_UNKNOWN,
            provider_replay=ProviderReplayCapability.SUPPORTED,
            result_reuse=ResultReuseCapability.SUPPORTED,
            has_replay_result=False,
            now=now,
            replay_deadline_at=now + timedelta(seconds=1),
        )
        is ReplayDecision.REPLAY_SAME_KEY
    )
    assert (
        decide_replay(
            outcome=EffectOutcome.EFFECT_OUTCOME_UNKNOWN,
            provider_replay=ProviderReplayCapability.SUPPORTED,
            result_reuse=ResultReuseCapability.SUPPORTED,
            has_replay_result=False,
            now=now,
            replay_deadline_at=now,
        )
        is ReplayDecision.STOP
    )


def test_canonical_replay_result_enforces_json_and_size_boundary() -> None:
    small = {"effect_id": "x" * 65_510}
    encoded = canonical_replay_result(small)

    assert encoded.value == small
    assert encoded.size_bytes <= 65_536

    with pytest.raises(ValueError, match="65,536"):
        canonical_replay_result({"effect_id": "x" * 65_537})
    with pytest.raises(ValueError, match="JSON"):
        canonical_replay_result({"value": float("nan")})


def test_fake_provider_duplicate_conflict_and_retention_contract() -> None:
    now = datetime(2026, 7, 13, tzinfo=timezone.utc)
    clock = [now]
    adapter = FakeEffectAdapter(clock=lambda: clock[0])
    key = "A" * 43
    first_call = adapter.finalize_provider_call(
        adapter.prepare_effect({"value": 1}),
        key,
    )

    first = adapter.invoke_effect(first_call)
    duplicate = adapter.invoke_effect(first_call)

    assert first.provider_status_code == 201
    assert duplicate.provider_status_code == 200
    assert duplicate.output == first.output
    assert set(first.output) == {"effect_id"}
    assert adapter.provider.effect_count == 1

    conflicting_call = adapter.finalize_provider_call(
        adapter.prepare_effect({"value": 2}),
        key,
    )
    with pytest.raises(EffectInvocationFailure) as captured:
        adapter.invoke_effect(conflicting_call)
    assert getattr(captured.value, "provider_status_code", None) == 409
    assert adapter.provider.effect_count == 1

    clock[0] = now + timedelta(hours=24)
    after_retention = adapter.invoke_effect(first_call)
    assert after_retention.provider_status_code == 201
    assert after_retention.output != first.output
    assert adapter.provider.effect_count == 2


def test_fake_provider_rejects_invalid_supported_key_before_call() -> None:
    adapter = FakeEffectAdapter()

    with pytest.raises(ValueError, match="idempotency key"):
        adapter.finalize_provider_call(
            adapter.prepare_effect({"value": 1}),
            "not-valid",
        )
