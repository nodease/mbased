from scripts.experiment_auto_vs_high_30 import (
    build_auto_vs_high_cases,
    build_auto_vs_high_plan,
)
from scripts.experiment_judge_first_economics_80 import build_learning_cases


def test_auto_vs_high_dataset_has_thirty_unseen_requests():
    cases = build_auto_vs_high_cases()
    learning_messages = {case.message for case in build_learning_cases()}

    assert len(cases) == 30
    assert len({case.case_id for case in cases}) == 30
    assert len({case.message for case in cases}) == 30
    assert {case.message for case in cases}.isdisjoint(learning_messages)
    assert {case.expected_difficulty for case in cases} == {
        "economy",
        "balanced",
        "advanced",
    }


def test_auto_vs_high_plan_compares_only_two_arms_with_blind_quality_scoring():
    plan = build_auto_vs_high_plan()

    assert plan.arms == ("automatic", "high_fixed")
    assert plan.evaluate_quality is True
    assert plan.reset_learning is False
    assert plan.requires_ready_learner is False
    assert plan.use_policy_preview is False
    assert len(plan.cases) == 30
