"""50건 Judge-first RAG 공정 비교 실험의 사전 준비 계약."""

from scripts.experiment_judge_first_rag_50_blind import (
    ARMS,
    build_blind_quality_packets,
    build_schedule,
    load_cases,
)


def test_dataset_has_fifty_diverse_cases_without_ten_per_tier_split():
    cases = load_cases()

    assert len(cases) == 50
    assert len({case.case_id for case in cases}) == 50
    assert len({case.category for case in cases}) >= 6
    assert sum(case.requires_grounding for case in cases) >= 12
    difficulty_counts = {
        difficulty: sum(case.expected_difficulty == difficulty for case in cases)
        for difficulty in ("economy", "balanced", "advanced")
    }
    assert difficulty_counts != {"economy": 10, "balanced": 10, "advanced": 10}


def test_schedule_runs_every_case_in_all_four_arms_and_marks_ten_case_reports():
    cases = load_cases()
    schedule = build_schedule(cases)

    assert len(schedule) == 200
    for case in cases:
        assignments = [row["arm"] for row in schedule if row["case_id"] == case.case_id]
        assert set(assignments) == set(ARMS)
    checkpoints = [row for row in schedule if row["report_checkpoint_after"]]
    assert [row["completed_case_count"] for row in checkpoints] == [10, 20, 30, 40, 50]
    assert [row["completed_execution_count"] for row in checkpoints] == [40, 80, 120, 160, 200]


def test_blind_quality_packet_hides_arm_model_cost_and_execution_order():
    cases = load_cases()
    execution_rows = [
        {
            "case_id": case.case_id,
            "variants": [
                {"arm": arm, "output": f"synthetic response for {case.case_id}"}
                for arm in ARMS
            ],
        }
        for case in cases
    ]

    packets, mapping = build_blind_quality_packets(cases, execution_rows)

    assert len(packets) == 50
    assert set(mapping) == {case.case_id for case in cases}
    serialized_packet = str(packets[0])
    for forbidden in (*ARMS, "gpt-", "cost", "latency", "execution_order"):
        assert forbidden not in serialized_packet
    assert set(mapping[cases[0].case_id].values()) == set(ARMS)
