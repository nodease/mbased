from apps.workflow_engine.services.model_routing_policy_change_guard import (
    ModelRoutingPolicyChangeGuard,
)


def _policy(model_id: str, *, cost: float, latency: int, quality: float):
    return {
        "default_model_id": "gpt-4.1",
        "fallback_model_id": "gpt-4.1",
        "rules": [
            {
                "id": "prior-guided-short",
                "when": {"input_length_bucket": "short"},
                "selected_model_id": model_id,
            }
        ],
        "decision_profiles": [
            {
                "profile": "short",
                "selected_model_id": model_id,
                "candidate_scores": {
                    "gpt-4.1": {
                        "quality_lower_bound": 0.94,
                        "expected_total_cost_usd": 0.01,
                        "expected_latency_ms": 1000,
                        "utility_score": 0.70,
                    },
                    "gpt-4.1-mini": {
                        "quality_lower_bound": quality,
                        "expected_total_cost_usd": cost,
                        "expected_latency_ms": latency,
                        "utility_score": 0.74,
                    },
                },
            }
        ],
    }


def test_same_routing_result_keeps_current_policy_version():
    current = _policy("gpt-4.1", cost=0.0098, latency=980, quality=0.94)
    proposed = _policy("gpt-4.1", cost=0.0095, latency=950, quality=0.95)

    decision = ModelRoutingPolicyChangeGuard.evaluate(current, proposed)

    assert decision.status == "kept_current"
    assert decision.reason_code == "routing_selection_unchanged"


def test_small_efficiency_change_does_not_replace_policy():
    current = _policy("gpt-4.1", cost=0.0098, latency=980, quality=0.94)
    proposed = _policy("gpt-4.1-mini", cost=0.0095, latency=950, quality=0.94)

    decision = ModelRoutingPolicyChangeGuard.evaluate(current, proposed)

    assert decision.status == "kept_current"
    assert decision.reason_code == "improvement_below_threshold"


def test_meaningful_cost_improvement_with_quality_preserved_applies_policy():
    current = _policy("gpt-4.1", cost=0.01, latency=1000, quality=0.94)
    proposed = _policy("gpt-4.1-mini", cost=0.0075, latency=950, quality=0.94)

    decision = ModelRoutingPolicyChangeGuard.evaluate(current, proposed)

    assert decision.status == "applied"
    assert decision.reason_code == "material_efficiency_improvement"
    assert decision.max_cost_improvement >= 0.25


def test_cheaper_model_is_not_applied_when_quality_lower_bound_drops():
    current = _policy("gpt-4.1", cost=0.01, latency=1000, quality=0.94)
    proposed = _policy("gpt-4.1-mini", cost=0.006, latency=700, quality=0.90)

    decision = ModelRoutingPolicyChangeGuard.evaluate(current, proposed)

    assert decision.status == "kept_current"
    assert decision.reason_code == "quality_guard_failed"
