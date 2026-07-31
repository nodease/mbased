import json
from pathlib import Path


FIXTURE_PATH = (
    Path(__file__).resolve().parents[4]
    / "tests"
    / "fixtures"
    / "model_routing"
    / "requirement_judge_review_v1.json"
)


def test_requirement_judge_review_fixture_covers_required_cross_category_cases():
    document = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    cases = document["cases"]
    by_id = {case["case_id"]: case for case in cases}

    assert document["rubric_version"] == "routing-requirements-v4"
    assert len(cases) == 24
    assert len(by_id) == 24
    assert {
        "routine_guidance",
        "rag_synthesis",
        "action_pair",
        "security_privacy",
    }.issubset({case["category"] for case in cases})

    for case in cases:
        lower_impact_case_id = case.get("higher_impact_than")
        if lower_impact_case_id:
            assert lower_impact_case_id in by_id
        invariant_case_id = case.get("invariant_with")
        if invariant_case_id:
            assert invariant_case_id in by_id
