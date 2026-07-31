"""기존 자동 라우팅 80건과 Sol 고정 80건을 익명 품질 Judge로 비교한다."""

from __future__ import annotations

import argparse
import importlib
import json
import pathlib
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env", override=False)

experiment = importlib.import_module("scripts.experiment_judge_first_economics_80")


SOURCE_RESULT = (
    ROOT
    / "reports/model-routing/runs/judge-first/2026-07-22__judge-first-learning-80/learning/result.json"
)
OUTPUT_DIR = (
    ROOT
    / "reports/model-routing/runs/sol-fixed-blind/2026-07-22__sol-fixed-vs-auto-80"
)
NAMESPACE = uuid.UUID("98000000-0000-0000-0000-000000000101")


def _auto_result(raw: dict[str, Any]) -> experiment.ArmResult:
    return experiment.ArmResult(
        arm=experiment.AUTO_ARM,
        selected_model=raw.get("selected_model"),
        task_cost_usd=float(raw.get("task_cost_usd") or 0),
        task_latency_ms=raw.get("task_latency_ms"),
        workflow_latency_ms=raw.get("workflow_latency_ms"),
        prompt_tokens=int(raw.get("prompt_tokens") or 0),
        completion_tokens=int(raw.get("completion_tokens") or 0),
        total_tokens=int(raw.get("total_tokens") or 0),
        output_text=str(raw.get("output_text") or ""),
        schema_pass=bool(raw.get("schema_pass")),
        workflow_success=bool(raw.get("workflow_success")),
        error=raw.get("error"),
        routing=dict(raw.get("routing") or {}),
        routing_judge_cost_usd=float(raw.get("routing_judge_cost_usd") or 0),
        routing_judge_tokens=int(raw.get("routing_judge_tokens") or 0),
        routing_judge_latency_ms=raw.get("routing_judge_latency_ms"),
    )


def _load_automatic_results() -> dict[str, experiment.ArmResult]:
    payload = json.loads(SOURCE_RESULT.read_text(encoding="utf-8"))
    rows = payload.get("runs") or []
    results = {
        str(row["case_id"]): _auto_result(dict(row["arms"][experiment.AUTO_ARM]))
        for row in rows
        if isinstance(row, dict)
        and isinstance(row.get("arms"), dict)
        and isinstance(row["arms"].get(experiment.AUTO_ARM), dict)
    }
    if len(results) != 80:
        raise RuntimeError(f"자동 라우팅 기준 결과 80건이 필요합니다: {len(results)}건")
    return results


def _read_report() -> dict[str, Any]:
    path = OUTPUT_DIR / "result.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"runs": []}


def _write_report(rows: list[dict[str, Any]]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    auto_rows = [row["arms"][experiment.AUTO_ARM] for row in rows]
    sol_rows = [row["arms"][experiment.HIGH_ARM] for row in rows]

    def summary(items: list[dict[str, Any]], *, include_judge_cost: bool) -> dict[str, Any]:
        count = len(items)
        return {
            "count": count,
            "task_cost_usd": round(sum(float(item.get("task_cost_usd") or 0) for item in items), 6),
            "routing_judge_cost_usd": round(
                sum(float(item.get("routing_judge_cost_usd") or 0) for item in items)
                if include_judge_cost
                else 0,
                6,
            ),
            "schema_pass_rate": round(
                sum(bool(item.get("schema_pass")) for item in items) / count, 4
            )
            if count
            else None,
            "workflow_success_rate": round(
                sum(bool(item.get("workflow_success")) for item in items) / count, 4
            )
            if count
            else None,
            "average_task_latency_ms": round(
                sum(float(item.get("task_latency_ms") or 0) for item in items) / count, 1
            )
            if count
            else None,
        }

    quality_cost = sum(float(row.get("quality_meta", {}).get("cost_usd") or 0) for row in rows)
    quality_scores = {
        arm: [
            float(row.get("quality", {}).get(arm, {}).get("quality_score") or 0)
            for row in rows
        ]
        for arm in (experiment.AUTO_ARM, experiment.HIGH_ARM)
    }
    payload = {
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "source_result": str(SOURCE_RESULT),
        "case_count": len(rows),
        "runs": rows,
        "summary": {
            "automatic": summary(auto_rows, include_judge_cost=True),
            "sol_fixed": summary(sol_rows, include_judge_cost=False),
            "blind_quality": {
                "judge_model": experiment.QUALITY_JUDGE_MODEL,
                "evaluation_cost_usd": round(quality_cost, 6),
                "automatic_average_score": round(sum(quality_scores[experiment.AUTO_ARM]) / len(rows), 2) if rows else None,
                "sol_average_score": round(sum(quality_scores[experiment.HIGH_ARM]) / len(rows), 2) if rows else None,
                "automatic_contract_pass_rate": round(
                    sum(bool(row.get("quality", {}).get(experiment.AUTO_ARM, {}).get("contract_pass")) for row in rows) / len(rows), 4
                ) if rows else None,
                "sol_contract_pass_rate": round(
                    sum(bool(row.get("quality", {}).get(experiment.HIGH_ARM, {}).get("contract_pass")) for row in rows) / len(rows), 4
                ) if rows else None,
            },
        },
    }
    (OUTPUT_DIR / "result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Sol 고정 80건 블라인드 비교",
        "",
        "자동 라우팅의 기존 실제 출력과 `gpt-5.6-sol` 고정 실제 출력을 모델명 없이 품질 Judge에 전달했다.",
        "",
        "| 방식 | JSON 계약 통과 | workflow 성공 | 처리 비용 | 평균 노드 시간 | 평균 블라인드 품질 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for label, key, include_judge in (("자동 라우팅", "automatic", True), ("Sol 고정", "sol_fixed", False)):
        item = payload["summary"][key]
        score_key = "automatic_average_score" if key == "automatic" else "sol_average_score"
        total = item["task_cost_usd"] + (item["routing_judge_cost_usd"] if include_judge else 0)
        lines.append(
            f"| {label} | {item['schema_pass_rate']:.2%} | {item['workflow_success_rate']:.2%} | "
            f"${total:.6f} | {item['average_task_latency_ms']:.0f}ms | {payload['summary']['blind_quality'][score_key]:.2f} |"
        )
    lines.extend([
        "",
        f"- 독립 품질 Judge: `{experiment.QUALITY_JUDGE_MODEL}`",
        f"- 품질 평가 비용: `${payload['summary']['blind_quality']['evaluation_cost_usd']:.6f}` (제품 실행 비용과 분리)",
        "- 각 요청의 자동/Sol 출력은 임의 순서로 전달돼 Judge는 모델명과 arm을 알 수 없다.",
    ])
    (OUTPUT_DIR / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Sol 고정 80건 블라인드 비교")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({"dry_run": True, "case_count": args.count, "source": str(SOURCE_RESULT)}, ensure_ascii=False))
        return

    automatic = _load_automatic_results()
    all_cases = experiment.build_learning_cases()
    if args.offset < 0 or args.count < 1 or args.offset + args.count > len(all_cases):
        raise SystemExit("offset/count 범위가 80건 학습 데이터셋을 벗어났습니다.")
    previous = _read_report() if args.resume else {"runs": []}
    rows = list(previous.get("runs") or [])
    existing_ids = {str(row.get("case_id")) for row in rows}

    original_arms = experiment.ARMS
    original_run_id = experiment._run_id
    experiment.ARMS = (experiment.AUTO_ARM, experiment.HIGH_ARM)
    experiment._run_id = lambda arm, case_id: uuid.uuid5(NAMESPACE, f"sol-fixed-blind-v1:{arm}:{case_id}")
    try:
        with experiment.synchronous_experiment_tasks():
            for case in all_cases[args.offset : args.offset + args.count]:
                if case.case_id in existing_ids:
                    continue
                sol = experiment._execute_case(
                    experiment.HIGH_ARM,
                    case,
                    include_in_learning=False,
                    use_policy_preview=False,
                )
                results = {experiment.AUTO_ARM: automatic[case.case_id], experiment.HIGH_ARM: sol}
                quality, quality_meta = experiment._quality_judge(case, results)
                rows.append({
                    "case_id": case.case_id,
                    "category": case.category,
                    "expected_difficulty": case.expected_difficulty,
                    "arms": {arm: asdict(result) for arm, result in results.items()},
                    "quality": quality,
                    "quality_meta": quality_meta,
                })
                _write_report(rows)
                print(f"[progress] {len(rows)}/80: {case.case_id}", flush=True)
    finally:
        experiment.ARMS = original_arms
        experiment._run_id = original_run_id
    _write_report(rows)
    print(json.dumps({"json": str((OUTPUT_DIR / 'result.json').resolve()), "markdown": str((OUTPUT_DIR / 'report.md').resolve())}, ensure_ascii=False))


if __name__ == "__main__":
    main()
