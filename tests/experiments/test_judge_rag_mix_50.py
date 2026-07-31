"""3차 Judge-first RAG 혼합 실험의 데이터셋/중단 규칙 계약."""

from __future__ import annotations

from scripts import experiment_judge_rag_mix_50 as experiment


def test_dataset_has_fifty_cases_and_each_batch_has_rag_and_difficulty_mix():
    cases = experiment.build_cases()

    assert len(cases) == 50
    assert len({case.case_id for case in cases}) == 50
    for batch in experiment.batches_of_ten(cases):
        assert len(batch) == 10
        assert sum(case.requires_rag for case in batch) == 4
        assert sum(case.category == "short_advanced_direct" for case in batch) == 2
        assert sum(case.category == "routine_direct" for case in batch) == 2
        assert sum(case.category == "balanced_direct" for case in batch) == 2


def test_routing_guard_stops_for_judge_failure_schema_failure_or_wrong_rag_mode():
    assert experiment.stop_reason_for(
        {"workflow_success": False, "schema_pass": False}, requires_rag=False
    ) == "workflow_failed"
    assert experiment.stop_reason_for(
        {
            "workflow_success": True,
            "schema_pass": False,
            "routing": {},
        },
        requires_rag=False,
    ) == "schema_contract_failed"
    assert experiment.stop_reason_for(
        {
            "workflow_success": True,
            "schema_pass": True,
            "routing": {
                "decision_source": "stored_model",
                "reason_code": "runtime_judge_unavailable",
            },
        },
        requires_rag=False,
    ) == "runtime_judge_unavailable"
    assert experiment.stop_reason_for(
        {
            "workflow_success": True,
            "schema_pass": True,
            "routing": {"selected_model": "gpt-5.4-mini"},
            "rag_context": {"used": False},
        },
        requires_rag=True,
    ) == "rag_mode_mismatch"
