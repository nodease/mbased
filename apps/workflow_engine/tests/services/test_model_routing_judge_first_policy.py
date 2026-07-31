from apps.workflow_engine.services.model_routing_judge_first_policy import (
    JUDGE_FIRST_STRATEGY_ID,
    build_judge_first_active_policy,
    normalize_judge_first_active_policy,
    select_runtime_judge_model_id,
)


def test_builder_creates_only_judge_first_policy_contract():
    policy = build_judge_first_active_policy(
        policy_version="judge-first-v1",
        default_model_id="gpt-4.1-mini",
        fallback_model_id="gpt-4.1",
        candidate_model_ids=["gpt-4.1-mini", "gpt-4.1", "gpt-4.1-mini"],
    )

    assert policy == {
        "strategy_id": JUDGE_FIRST_STRATEGY_ID,
        "policy_version": "judge-first-v1",
        "default_model_id": "gpt-4.1-mini",
        "fallback_model_id": "gpt-4.1",
        "judge_model_id": "gpt-4.1-mini",
        "candidate_model_ids": ["gpt-4.1-mini", "gpt-4.1"],
    }


def test_builder_collapses_provider_aliases_to_one_executable_candidate():
    policy = build_judge_first_active_policy(
        policy_version="judge-first-v2",
        default_model_id="gpt-5.6",
        fallback_model_id="o3",
        candidate_model_ids=["gpt-5.6", "gpt-5.6-sol", "o3"],
    )

    assert policy["default_model_id"] == "gpt-5.6-sol"
    assert policy["candidate_model_ids"] == ["gpt-5.6-sol", "o3"]


def test_builder_uses_a_dedicated_openai_judge_instead_of_the_task_default():
    policy = build_judge_first_active_policy(
        policy_version="judge-first-v3",
        default_model_id="gpt-4o-mini",
        fallback_model_id="gpt-4.1",
        candidate_model_ids=["gpt-4o-mini", "gpt-4.1", "gpt-5.4-mini"],
    )

    assert policy["default_model_id"] == "gpt-4o-mini"
    assert policy["judge_model_id"] == "gpt-5.4-mini"


def test_legacy_default_coupled_judge_is_upgraded_at_runtime():
    selected = select_runtime_judge_model_id(
        ["gpt-5-mini", "gpt-4.1-mini", "gpt-5.4-mini"],
        default_model_id="gpt-5-mini",
        configured_judge_model_id="gpt-5-mini",
    )

    assert selected == "gpt-5.4-mini"


def test_explicit_non_default_judge_is_preserved():
    selected = select_runtime_judge_model_id(
        ["gpt-4o-mini", "gpt-5.4-mini", "gpt-4.1"],
        default_model_id="gpt-4o-mini",
        configured_judge_model_id="gpt-4.1",
    )

    assert selected == "gpt-4.1"


def test_judge_preference_stays_with_the_default_models_provider():
    selected = select_runtime_judge_model_id(
        ["gpt-5.4-mini", "gemini-2.5-flash", "gemini-3.5-flash"],
        default_model_id="gemini-2.5-flash",
    )

    assert selected == "gemini-3.5-flash"


def test_legacy_policy_is_normalized_without_reusing_legacy_rules():
    legacy = {
        "strategy_id": "prior_guided_adaptive_v1",
        "policy_version": "legacy-v8",
        "default_model_id": "gpt-4.1-mini",
        "fallback_model_id": "gpt-4.1",
        "rules": [
            {
                "id": "legacy-high-risk",
                "selected_model_id": "gpt-5",
                "when": {"keyword_any": ["보상"]},
            }
        ],
        "decision_profiles": [{"id": "legacy-profile"}],
    }

    normalized = normalize_judge_first_active_policy(
        legacy,
        policy_version="judge-first-v1",
        default_model_id="gpt-4.1-mini",
        fallback_model_id="gpt-4.1",
        candidate_model_ids=["gpt-4.1-mini", "gpt-4.1", "gpt-5"],
    )

    assert normalized["strategy_id"] == JUDGE_FIRST_STRATEGY_ID
    assert "learning" not in normalized
    assert "rules" not in normalized
    assert "decision_profiles" not in normalized


def test_existing_policy_embedded_learning_artifact_is_removed():
    existing = build_judge_first_active_policy(
        policy_version="judge-first-v1",
        default_model_id="gpt-4.1-mini",
        fallback_model_id="gpt-4.1",
        candidate_model_ids=["gpt-4.1-mini", "gpt-4.1"],
    )
    existing["learning"] = {
        "mode": "local_first",
        "judged_request_count": 31,
        "selected_model_ids": ["gpt-4.1-mini", "gpt-4.1"],
        "local_confidence_threshold": 0.81,
        "local_router_artifact": {"kind": "incremental-model-choice-v1"},
    }

    normalized = normalize_judge_first_active_policy(
        existing,
        policy_version="judge-first-v2",
        default_model_id="gpt-4.1-mini",
        fallback_model_id="gpt-4.1",
        candidate_model_ids=["gpt-4.1-mini", "gpt-4.1"],
    )

    assert normalized["policy_version"] == "judge-first-v2"
    assert "learning" not in normalized
