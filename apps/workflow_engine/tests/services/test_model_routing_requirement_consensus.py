from apps.workflow_engine.services.model_routing_requirement_consensus import (
    build_consensus_report,
)


CASES = [
    {"case_id": "guide"},
    {"case_id": "approval"},
    {"case_id": "security"},
]


def _reviewer(labels):
    return {
        "rubric_version": "routing-requirements-v4",
        "reviewer_id": "reviewer",
        "labels": labels,
    }


def _label(case_id, task, impact, evidence):
    return {
        "case_id": case_id,
        "task_complexity": task,
        "decision_impact": impact,
        "evidence_synthesis": evidence,
        "confidence": 0.8,
        "ambiguity_flags": [],
        "reason_codes": [],
        "rationale": "독립 검토 결과",
    }


def test_consensus_uses_exact_agreement_without_third_reviewer():
    report = build_consensus_report(
        cases=CASES[:1],
        review_a=_reviewer([_label("guide", 1, 1, 0)]),
        review_b=_reviewer([_label("guide", 1, 1, 0)]),
    )

    row = report["consensus"][0]
    assert row["consensus_strength"] == "high"
    assert row["included_in_accuracy"] is True
    assert row["task_requirements"] == {
        "task_complexity": 1,
        "decision_impact": 1,
        "evidence_synthesis": 0,
    }
    assert report["needs_adjudication_case_ids"] == []


def test_consensus_requires_blind_third_review_for_disagreement():
    report = build_consensus_report(
        cases=CASES[:1],
        review_a=_reviewer([_label("guide", 0, 0, 0)]),
        review_b=_reviewer([_label("guide", 1, 1, 0)]),
    )

    assert report["needs_adjudication_case_ids"] == ["guide"]
    assert report["consensus"] == []


def test_consensus_marks_large_three_way_difference_as_low_consensus():
    report = build_consensus_report(
        cases=CASES[:1],
        review_a=_reviewer([_label("guide", 0, 0, 0)]),
        review_b=_reviewer([_label("guide", 2, 1, 0)]),
        review_c=_reviewer([_label("guide", 1, 1, 0)]),
    )

    row = report["consensus"][0]
    assert row["consensus_strength"] == "low"
    assert row["included_in_accuracy"] is False
    assert row["task_requirements"]["task_complexity"] == 1


def test_consensus_reports_axis_and_exact_agreement():
    report = build_consensus_report(
        cases=CASES[:2],
        review_a=_reviewer([
            _label("guide", 1, 1, 0),
            _label("approval", 2, 2, 1),
        ]),
        review_b=_reviewer([
            _label("guide", 1, 1, 0),
            _label("approval", 2, 1, 1),
        ]),
        review_c=_reviewer([_label("approval", 2, 2, 1)]),
    )

    assert report["agreement"]["exact_triplet_rate"] == 0.5
    assert report["agreement"]["axis_agreement_rates"]["task_complexity"] == 1.0
    assert report["agreement"]["axis_agreement_rates"]["decision_impact"] == 0.5
