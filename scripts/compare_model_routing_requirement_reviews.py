"""독립 Requirement Judge 검토 JSON을 합의 기준으로 비교한다."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.workflow_engine.services.model_routing_requirement_consensus import (  # noqa: E402
    build_consensus_report,
)


DEFAULT_CASES = ROOT / "tests" / "fixtures" / "model_routing" / "requirement_judge_review_v1.json"


def _read_json(path: pathlib.Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object가 아닙니다: {path}")
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--review-a", required=True, type=pathlib.Path)
    parser.add_argument("--review-b", required=True, type=pathlib.Path)
    parser.add_argument("--review-c", type=pathlib.Path)
    parser.add_argument("--cases", type=pathlib.Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=pathlib.Path)
    parser.add_argument("--minimum-eligible", type=int, default=18)
    args = parser.parse_args()

    cases_document = _read_json(args.cases)
    cases = cases_document.get("cases")
    if not isinstance(cases, list):
        raise ValueError("cases fixture에 cases 배열이 필요합니다")
    report = build_consensus_report(
        cases=cases,
        review_a=_read_json(args.review_a),
        review_b=_read_json(args.review_b),
        review_c=_read_json(args.review_c) if args.review_c else None,
    )
    report["case_count"] = len(cases)
    report["source"] = "multi-agent-consensus"
    report["minimum_eligible"] = args.minimum_eligible
    report["is_usable"] = (
        not report["needs_adjudication_case_ids"]
        and report["eligible_case_count"] >= args.minimum_eligible
    )
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    print(output)
    if not report["is_usable"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
