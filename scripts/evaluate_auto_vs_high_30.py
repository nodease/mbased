"""저장된 자동 라우팅·고가 고정 출력을 별도 프로세스에서 품질 평가한다."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from types import SimpleNamespace
from typing import Any

from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))
load_dotenv(ROOT / ".env", override=False)

from scripts import experiment_judge_first_economics_80 as experiment  # noqa: E402
from scripts.experiment_auto_vs_high_30 import build_auto_vs_high_cases  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="저장된 자동 라우팅·고가 고정 출력을 익명 품질 평가"
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--source-report", required=True)
    parser.add_argument("--judge-model", default="gpt-5.4")
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


def _checkpoint_path(source_path: pathlib.Path) -> pathlib.Path:
    return source_path.with_name("quality-evaluation.partial.json")


def _load_checkpoint(path: pathlib.Path) -> dict[str, Any]:
    if not path.exists():
        return {"evaluations": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {"evaluations": {}}


def _write_checkpoint(path: pathlib.Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _finalize_report(
    report: dict[str, Any],
    evaluations: dict[str, Any],
    *,
    judge_model: str,
) -> dict[str, Any]:
    rows = list(report.get("runs") or [])
    quality_metrics: list[dict[str, Any]] = []
    for row in rows:
        evaluation = evaluations.get(str(row.get("case_id")))
        if not isinstance(evaluation, dict):
            continue
        row["quality"] = evaluation.get("quality") or {}
        row["quality_evaluation"] = evaluation.get("metadata") or {}
        quality_metrics.append(row["quality_evaluation"])

    experiment.ARMS = (experiment.AUTO_ARM, experiment.HIGH_ARM)
    report["quality_evaluation_enabled"] = True
    report["quality_judge_model"] = judge_model
    report["arm_summary"] = {
        arm: experiment._arm_summary(rows, arm) for arm in experiment.ARMS
    }
    report["quality_judge_total_cost_usd"] = sum(
        float(item.get("cost_usd") or 0) for item in quality_metrics
    )
    report["quality_judge_call_count"] = sum(
        1 for item in quality_metrics if item.get("evaluation_status") == "completed"
    )
    report["quality_judge_errors"] = [
        item for item in quality_metrics if item.get("evaluation_status") == "failed"
    ]
    return report


def main() -> None:
    args = parse_args()
    source_path = pathlib.Path(args.source_report).resolve()
    report = json.loads(source_path.read_text(encoding="utf-8"))
    case_by_id = {case.case_id: case for case in build_auto_vs_high_cases()}
    checkpoint_path = _checkpoint_path(source_path)
    checkpoint = (
        _load_checkpoint(checkpoint_path)
        if args.resume
        else {"evaluations": {}}
    )
    evaluations = checkpoint.setdefault("evaluations", {})
    experiment.ARMS = (experiment.AUTO_ARM, experiment.HIGH_ARM)
    experiment.QUALITY_JUDGE_MODEL = str(args.judge_model).strip()

    pending = [
        row
        for row in report.get("runs") or []
        if str(row.get("case_id")) not in evaluations
    ]
    if not args.execute:
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "source_case_count": len(report.get("runs") or []),
                    "pending_count": len(pending),
                    "judge_model": experiment.QUALITY_JUDGE_MODEL,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    for index, row in enumerate(pending, start=1):
        case_id = str(row.get("case_id"))
        case = case_by_id.get(case_id)
        if case is None:
            raise RuntimeError(f"실험 case를 찾지 못했습니다: {case_id}")
        arms = row.get("arms") or {}
        quality, metadata = experiment._quality_judge(
            case,
            {
                arm: SimpleNamespace(
                    output_text=str((arms.get(arm) or {}).get("output_text") or "")
                )
                for arm in experiment.ARMS
            },
        )
        evaluations[case_id] = {
            "quality": quality,
            "metadata": metadata,
        }
        checkpoint.update(
            {
                "judge_model": experiment.QUALITY_JUDGE_MODEL,
                "source_report": str(source_path),
                "completed_count": len(evaluations),
            }
        )
        _write_checkpoint(checkpoint_path, checkpoint)
        print(
            f"[quality] {len(evaluations)}/{len(report.get('runs') or [])} "
            f"({index}/{len(pending)}) completed",
            flush=True,
        )

    finalized = _finalize_report(
        report,
        evaluations,
        judge_model=experiment.QUALITY_JUDGE_MODEL,
    )
    result_path = source_path.with_name("result-with-quality.json")
    markdown_path = source_path.with_name("report-with-quality.md")
    result_path.write_text(
        json.dumps(finalized, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown_path.write_text(
        experiment.render_markdown(finalized),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "json": str(result_path),
                "markdown": str(markdown_path),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
