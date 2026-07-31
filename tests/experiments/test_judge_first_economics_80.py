import json
import pathlib
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from scripts.experiment_judge_first_economics_80 import (
    BENCHMARK_PHASE,
    HIGH_MODEL,
    LEARNING_PHASE,
    LOW_MODEL,
    MID_MODEL,
    QUALITY_JUDGE_MODEL,
    ROUTING_JUDGE_MODEL,
    _append_jsonl,
    _arm_execution_order,
    _clear_prior_experiment_learning,
    _learner_report_payload,
    _learning_batch_due,
    _quality_not_evaluated,
    _retry_quality_evaluation,
    _routing_accuracy,
    _write_run_config,
    benchmark_readiness_error,
    build_benchmark_cases,
    build_cases,
    build_learning_cases,
    build_phase_plan,
    graph_for_arm,
    resolve_artifact_target,
)
from apps.shared.db.models.model_routing_policy import (
    LLMNodeModelRoutingLearner,
    LLMNodeModelRoutingPolicy,
)
from apps.workflow_engine.services.model_routing_runtime_judge import (
    ModelRoutingRuntimeJudge,
)


def test_two_stage_dataset_has_one_hundred_learning_and_twenty_unseen_benchmark_cases():
    learning_cases = build_learning_cases()
    benchmark_cases = build_benchmark_cases()
    cases = build_cases()

    assert len(learning_cases) == 100
    assert len(benchmark_cases) == 20
    assert len(cases) == 120
    assert len({case.message for case in cases}) == 120
    assert {case.case_id for case in learning_cases}.isdisjoint(
        {case.case_id for case in benchmark_cases}
    )
    assert {case.message for case in learning_cases}.isdisjoint(
        {case.message for case in benchmark_cases}
    )
    assert {case.expected_difficulty for case in cases} == {
        "economy",
        "balanced",
        "advanced",
    }
    assert len({case.category for case in cases}) >= 8
    assert all(case.context is not None for case in cases)
    assert all(case.constraints for case in cases)
    assert all(case.acceptable_model_ids for case in cases)


def test_learning_phase_runs_only_automatic_without_independent_quality_judge():
    plan = build_phase_plan(LEARNING_PHASE)

    assert len(plan.cases) == 100
    assert plan.arms == ("automatic",)
    assert plan.evaluate_quality is False
    assert plan.reset_learning is True
    assert plan.requires_ready_learner is False
    assert plan.use_policy_preview is False


def test_benchmark_phase_uses_unseen_holdout_for_three_blinded_arms():
    plan = build_phase_plan(BENCHMARK_PHASE)

    assert len(plan.cases) == 20
    assert plan.arms == ("automatic", "mid_fixed", "high_fixed")
    assert plan.evaluate_quality is True
    assert plan.reset_learning is False
    assert plan.requires_ready_learner is True
    assert plan.use_policy_preview is True


def test_benchmark_requires_one_hundred_completed_runs_and_published_learner():
    assert (
        benchmark_readiness_error(
            {"judged_request_count": 70, "active_version": None},
            completed_learning_case_count=100,
        )
        == "active_learner_version_missing"
    )
    assert (
        benchmark_readiness_error(
            {"judged_request_count": 70, "active_version": 1},
            completed_learning_case_count=99,
        )
        == "incomplete_learning_phase"
    )
    assert (
        benchmark_readiness_error(
            {"judged_request_count": 70, "active_version": 1},
            completed_learning_case_count=100,
        )
        is None
    )


def test_learning_phase_marks_quality_as_not_evaluated_instead_of_failed():
    quality, metadata = _quality_not_evaluated(
        {"automatic": SimpleNamespace()}
    )

    assert quality["automatic"]["quality_score"] is None
    assert quality["automatic"]["evaluation_status"] == "not_evaluated"
    assert metadata == {
        "evaluation_status": "not_requested",
        "attempt_count": 0,
        "cost_usd": 0.0,
    }


def test_first_thirty_cases_include_semantically_similar_refund_intents():
    refund_cases = [
        case for case in build_cases()[:30] if case.category == "refund_intent"
    ]

    assert {case.expected_difficulty for case in refund_cases} == {
        "economy",
        "advanced",
    }
    assert all("환불" in case.message for case in refund_cases)


def test_arm_execution_order_is_seeded_but_not_fixed_to_arm_declaration_order():
    first = _arm_execution_order("security-01")
    second = _arm_execution_order("security-01")

    assert first == second
    assert set(first) == {"automatic", "mid_fixed", "high_fixed", "low_fixed"}
    assert first != ("automatic", "mid_fixed", "high_fixed", "low_fixed")


def test_append_jsonl_keeps_each_completed_request_as_an_independent_record():
    with tempfile.TemporaryDirectory(dir=pathlib.Path.cwd()) as temp_dir:
        path = pathlib.Path(temp_dir) / "execution-events.jsonl"

        _append_jsonl(path, {"case_id": "case-01", "event": "execution_complete"})
        _append_jsonl(path, {"case_id": "case-02", "event": "execution_complete"})

        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        assert [row["case_id"] for row in rows] == ["case-01", "case-02"]


def test_routing_accuracy_distinguishes_appropriate_underpowered_and_overprovisioned():
    case = build_cases()[0]
    allowed = list(case.acceptable_model_ids)
    appropriate = _routing_accuracy(case, allowed[0])
    underpowered = _routing_accuracy(case, "gpt-4o-mini")
    overprovisioned = _routing_accuracy(case, "gpt-5.6-sol")

    assert appropriate["classification"] == "appropriate"
    assert underpowered["classification"] in {"underpowered", "appropriate"}
    assert overprovisioned["classification"] in {"overprovisioned", "appropriate"}


def test_routing_accuracy_matches_dated_provider_model_to_its_catalog_family():
    case = next(
        item for item in build_cases() if "gpt-5.4-mini" in item.acceptable_model_ids
    )

    result = _routing_accuracy(case, "gpt-5.4-mini-2026-03-17")

    assert result["classification"] == "appropriate"


def test_routing_accuracy_uses_capability_catalog_instead_of_static_model_lists():
    case = next(
        item for item in build_cases() if item.expected_difficulty == "advanced"
    )

    result = _routing_accuracy(case, "gpt-5.4-mini-2026-03-17")

    assert result["classification"] != "unclassified"
    assert result["canonical_model_id"] == "gpt-5.4-mini"
    assert result["capability_source"] == "official_provider_catalog"


def test_quality_evaluation_retries_only_the_failed_evaluation():
    attempts = []

    def evaluate():
        attempts.append(True)
        if len(attempts) == 1:
            return None, {"error": "temporary_provider_error"}
        return {"output_1": {"quality_score": 90}}, {"model": "judge"}

    payload, metadata = _retry_quality_evaluation(evaluate, max_attempts=3)

    assert len(attempts) == 2
    assert payload == {"output_1": {"quality_score": 90}}
    assert metadata["evaluation_status"] == "completed"
    assert metadata["attempt_count"] == 2


def test_norag_experiment_graph_maps_generic_request_payload_fields():
    graph = graph_for_arm("automatic")
    trigger = next(node for node in graph["nodes"] if node["id"] == "webhook-ticket")
    llm = next(node for node in graph["nodes"] if node["id"] == "llm-triage")

    assert {item["variable_name"] for item in trigger["data"]["variable_mappings"]} == {
        "customerTier",
        "request",
        "context",
        "constraints",
        "outputMode",
    }
    assert llm["data"]["knowledgeBases"] == []


def test_economics_experiment_uses_distinct_high_low_and_judge_models():
    assert HIGH_MODEL != LOW_MODEL
    assert MID_MODEL not in {HIGH_MODEL, LOW_MODEL}
    assert ROUTING_JUDGE_MODEL not in {"gpt-5.6-sol"}
    assert QUALITY_JUDGE_MODEL not in {"gpt-5.6-sol"}


def test_run_id_creates_a_self_contained_judge_first_artifact_folder():
    output_dir, report_name = resolve_artifact_target(
        output_dir="reports/model-routing/legacy",
        run_id="2026-07-18__ticket-json-v1__judge-gpt-5.4-mini__out-256",
    )

    assert output_dir.as_posix() == (
        "reports/model-routing/runs/judge-first/"
        "2026-07-18__ticket-json-v1__judge-gpt-5.4-mini__out-256"
    )
    assert report_name is None


def test_run_config_records_models_and_rejects_a_different_judge(monkeypatch):
    monkeypatch.setattr(
        "scripts.experiment_judge_first_economics_80.build_cases",
        lambda: [object()] * 80,
    )
    with tempfile.TemporaryDirectory(dir=pathlib.Path.cwd()) as temp_dir:
        output_dir = pathlib.Path(temp_dir) / "judge-run"
        config_path = _write_run_config(
            output_dir,
            run_id="2026-07-18__ticket-json-v1__judge-gpt-5-mini__out-256",
            report_name=None,
            batch_size=10,
        )

        config = json.loads(config_path.read_text(encoding="utf-8"))
        assert config["routing_judge_model"] == ROUTING_JUDGE_MODEL
        assert (
            config["routing_judge_max_output_tokens"]
            == ModelRoutingRuntimeJudge.MAX_OUTPUT_TOKENS
        )
        assert config["artifact_files"]["learning"]["result"] == (
            "learning/result.json"
        )
        assert config["artifact_files"]["benchmark"]["result"] == (
            "benchmark/result.json"
        )
        assert set(config["comparison_arms"]) == {
            "automatic",
            "mid_fixed",
            "high_fixed",
        }
        assert config["phases"]["learning"]["case_count"] == 100
        assert config["phases"]["learning"]["quality_judge"] is False
        assert config["phases"]["benchmark"]["case_count"] == 20
        assert config["phases"]["benchmark"]["quality_judge"] is True

        monkeypatch.setattr(
            "scripts.experiment_judge_first_economics_80.ROUTING_JUDGE_MODEL",
            "gpt-5-mini",
        )
        with pytest.raises(RuntimeError, match="routing_judge_model"):
            _write_run_config(
                output_dir,
                run_id="2026-07-18__ticket-json-v1__judge-gpt-5-mini__out-256",
                report_name=None,
                batch_size=10,
            )


def test_run_config_rejects_resuming_with_different_comparison_arms(monkeypatch):
    monkeypatch.setattr(
        "scripts.experiment_judge_first_economics_80.build_cases",
        lambda: [object()] * 80,
    )
    with tempfile.TemporaryDirectory(dir=pathlib.Path.cwd()) as temp_dir:
        output_dir = pathlib.Path(temp_dir) / "judge-run"
        config_path = _write_run_config(
            output_dir,
            run_id="2026-07-18__ticket-json-v1__judge-gpt-5-mini__out-256",
            report_name=None,
            batch_size=10,
        )
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["comparison_arms"].pop("mid_fixed")
        config_path.write_text(
            json.dumps(config, ensure_ascii=False), encoding="utf-8"
        )

        with pytest.raises(RuntimeError, match="comparison_arms"):
            _write_run_config(
                output_dir,
                run_id="2026-07-18__ticket-json-v1__judge-gpt-5-mini__out-256",
                report_name=None,
                batch_size=10,
            )


def test_learning_batch_runs_every_ten_cases_and_after_final_partial_batch():
    assert _learning_batch_due(completed_case_count=10, total_case_count=23) is True
    assert _learning_batch_due(completed_case_count=20, total_case_count=23) is True
    assert _learning_batch_due(completed_case_count=23, total_case_count=23) is True
    assert _learning_batch_due(completed_case_count=9, total_case_count=23) is False


def test_learner_report_uses_persisted_learner_instead_of_legacy_policy_json():
    policy = SimpleNamespace(
        status="active",
        policy_version="routing-policy-learner-v2",
        learner_id="learner-1",
        active_learner_version_id="version-2",
    )
    learner = SimpleNamespace(
        id="learner-1",
        status="ready",
        task_fingerprint="task-fingerprint",
        judged_request_count=50,
        selected_model_counts={"gpt-4.1": 20, "gpt-5.4-mini": 30},
        recent_evaluation={"judge_match_rate": 0.82},
        candidate_artifact={
            "kind": "ordinal_task_requirement_classifier",
            "encoder_model_id": "multilingual-e5-base",
            "trained_example_count": 50,
        },
    )
    version = SimpleNamespace(version=2, sample_count=50)

    result = _learner_report_payload(
        policy=policy,
        learner=learner,
        version=version,
        label_summary={
            "pending_count": 0,
            "accepted_count": 48,
            "rejected_count": 2,
        },
    )

    assert result["learning_mode"] == "local_first"
    assert result["judged_request_count"] == 50
    assert result["accepted_count"] == 48
    assert result["active_version"] == 2
    assert result["selected_model_counts"] == {
        "gpt-4.1": 20,
        "gpt-5.4-mini": 30,
    }
    assert "feature_vector" not in result


def test_fresh_experiment_detaches_policy_and_deletes_previous_learner_lineage():
    policy = SimpleNamespace(
        learner_id="learner-1",
        active_learner_version_id="version-1",
    )
    policy_query = MagicMock()
    policy_query.filter.return_value = policy_query
    policy_query.all.return_value = [policy]
    learner_query = MagicMock()
    learner_query.filter.return_value = learner_query
    learner_query.delete.return_value = 1
    db = MagicMock()

    def query(model):
        if model is LLMNodeModelRoutingPolicy:
            return policy_query
        if model is LLMNodeModelRoutingLearner:
            return learner_query
        raise AssertionError(f"unexpected query model: {model}")

    db.query.side_effect = query

    _clear_prior_experiment_learning(db)

    assert policy.learner_id is None
    assert policy.active_learner_version_id is None
    learner_query.delete.assert_called_once_with(synchronize_session=False)
    assert db.flush.call_count == 2
