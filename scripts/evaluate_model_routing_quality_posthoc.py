"""자동 라우팅 출력과 고성능 기준 출력을 사후 익명 품질 평가한다.

두 출력을 한 요청에 같이 넣으면 위치 편향이 생길 수 있다. 이 스크립트는 각
출력을 별도 요청으로 익명 채점한다. 라우팅 Judge와 다른 강한 품질 Judge를
사용해 모델 선택 판단과 출력 품질 판단도 분리한다.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv


ROOT = pathlib.Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT.parent):
    if str(path) not in sys.path:
        sys.path.append(str(path))
load_dotenv(ROOT / ".env", override=False)

from apps.shared.db.session import SessionLocal  # noqa: E402
from apps.workflow_engine.services.llm_service import LLMService  # noqa: E402
from scripts.experiment_judge_first_economics_80 import (  # noqa: E402
    AUTO_ARM,
    ORG_ID,
    USER_ID,
    _json_from_text,
    _schema_pass,
)


DEFAULT_SOURCE = ROOT / (
    "reports/model-routing/runs/judge-first/"
    "2026-07-22__axis-signals-diagnostic-30-e5-isolated/learning/learning/"
    "judge_first_economics_80_learning.json"
)
DEFAULT_OUTPUT_DIR = DEFAULT_SOURCE.parent.parent / "posthoc-quality-pointwise-v3"
DEFAULT_REFERENCE_CACHE = DEFAULT_SOURCE.parent.parent / (
    "posthoc-quality-pointwise-v2/comparison.partial.json"
)
DEFAULT_REFERENCE_MODEL = "gpt-5.6-sol"
DEFAULT_JUDGE_MODELS = ("gpt-5.4",)
MAX_ATTEMPTS = 3

SYSTEM_PROMPT = (
    "당신은 기업용 AI 워크플로우의 범용 요청 처리 노드입니다. 요청, 제공된 문맥, 제약을 "
    "함께 검토하고 필요한 판단·계획·답변을 작성하세요. 반드시 JSON object 하나만 반환하세요. "
    "필수 필드는 분류(string), 우선순위(low|medium|high|critical), 승인필요(boolean), "
    "대응계획(string 배열), 답변초안(string)입니다. 보안, 개인정보, 보상, 법무, 결제, "
    "장애는 사실이 불명확하면 보수적으로 설명하되 근거 없는 확정 약속은 하지 마세요."
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _user_prompt(row: dict[str, Any]) -> str:
    payload = row["input"]
    constraints = json.dumps(payload.get("constraints") or [], ensure_ascii=False)
    return (
        f"요청자 등급: {row.get('customer_tier') or ''}\n"
        f"요청: {payload.get('request') or ''}\n"
        f"제공 문맥: {payload.get('context') or ''}\n"
        f"제약: {constraints}\n"
        f"출력 모드: {payload.get('output_mode') or ''}"
    )


def _usage(response: dict[str, Any]) -> tuple[int, int]:
    usage = response.get("usage") if isinstance(response, dict) else {}
    usage = usage if isinstance(usage, dict) else {}
    return (
        int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0),
        int(usage.get("completion_tokens") or usage.get("output_tokens") or 0),
    )


def _invoke_json_model(
    *, model_id: str, messages: list[dict[str, str]], max_tokens: int
) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        db = SessionLocal()
        try:
            selection = LLMService.get_runtime_client_for_user(
                db, USER_ID, model_id, ORG_ID
            )
            started = time.perf_counter()
            response = selection.client.invoke_sync(
                messages,
                temperature=0,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
            latency_ms = int((time.perf_counter() - started) * 1000)
            text = str(
                response.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )
            prompt_tokens, completion_tokens = _usage(response)
            return {
                "model_id": model_id,
                "text": text,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
                "cost_usd": float(
                    LLMService.calculate_cost(
                        db, model_id, prompt_tokens, completion_tokens
                    )
                ),
                "latency_ms": latency_ms,
                "attempt_count": attempt,
            }
        except Exception as exc:  # provider의 일시 오류만 제한적으로 재시도한다.
            last_error = exc
            if attempt < MAX_ATTEMPTS:
                time.sleep(attempt)
        finally:
            db.close()
    assert last_error is not None
    raise last_error


def _pointwise_prompt(row: dict[str, Any], output_text: str) -> dict[str, Any]:
    payload = row["input"]
    return {
        "task": "기업 요청에 대한 JSON 출력 하나를 절대 품질 기준으로 평가하세요.",
        "input": {
            "customerTier": row.get("customer_tier"),
            "request": payload.get("request"),
            "context": payload.get("context"),
            "constraints": payload.get("constraints") or [],
            "outputMode": payload.get("output_mode"),
        },
        "required_contract": {
            "fields": ["분류", "우선순위", "승인필요", "대응계획", "답변초안"],
            "rules": [
                "필수 필드와 타입을 지켜야 합니다.",
                "요청과 제공 문맥에 없는 정책, 수치, 사실을 만들어내면 안 됩니다.",
                "보안, 개인정보, 결제, 환불, SLA, 법무 위험은 보수적으로 처리해야 합니다.",
                "대응계획은 요청 범위 안에서 실행 가능하고 안전해야 합니다.",
                "답변초안은 요청을 직접 해결하고 사용자가 이해할 수 있어야 합니다.",
                "문장 길이, 모델 추정, 비용과 속도는 평가 근거로 사용하지 마세요.",
            ],
        },
        "anonymous_output": output_text,
        "response_contract": {
            "quality_score": "0..100",
            "contract_pass": "boolean",
            "safety_pass": "boolean",
            "unsupported_claims": "string array",
            "reason": "short Korean",
        },
    }


def _normalize_judgment(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        score = max(0.0, min(100.0, float(payload.get("quality_score"))))
    except (TypeError, ValueError):
        score = 0.0
    claims = payload.get("unsupported_claims")
    return {
        "quality_score": score,
        "contract_pass": bool(payload.get("contract_pass")),
        "safety_pass": bool(payload.get("safety_pass")),
        "unsupported_claims": [str(item)[:300] for item in claims]
        if isinstance(claims, list)
        else [],
        "reason": str(payload.get("reason") or "평가 근거 없음")[:700],
    }


def _evaluate_output(
    row: dict[str, Any], *, output_name: str, output_text: str, judge_model: str
) -> dict[str, Any]:
    response = _invoke_json_model(
        model_id=judge_model,
        messages=[
            {
                "role": "system",
                "content": (
                    "당신은 엄격하고 공정한 workflow 출력 품질 평가자입니다. "
                    "출력의 모델이나 출처를 추측하지 말고 반드시 JSON object 하나만 반환하세요."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    _pointwise_prompt(row, output_text), ensure_ascii=False
                ),
            },
        ],
        max_tokens=1000,
    )
    parsed = _json_from_text(response["text"])
    if not parsed:
        raise RuntimeError("품질 Judge 응답을 JSON으로 파싱하지 못했습니다.")
    return {
        "output": output_name,
        "judge_model": judge_model,
        "judgment": _normalize_judgment(parsed),
        "judge_usage": {
            key: response[key]
            for key in (
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "cost_usd",
                "latency_ms",
                "attempt_count",
            )
        },
    }


def _selected_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in report.get("runs") or []
        if (row.get("routing_accuracy") or {}).get("classification")
        == "underpowered"
        and isinstance((row.get("arms") or {}).get(AUTO_ARM), dict)
    ]


def _aggregate_output(evaluations: list[dict[str, Any]], output: str) -> dict[str, Any]:
    rows = [item["judgment"] for item in evaluations if item["output"] == output]
    scores = [float(row["quality_score"]) for row in rows]
    contract_votes = [bool(row["contract_pass"]) for row in rows]
    safety_votes = [bool(row["safety_pass"]) for row in rows]
    return {
        "quality_score_average": round(statistics.mean(scores), 2),
        "quality_score_range": round(max(scores) - min(scores), 2),
        "contract_pass": all(contract_votes),
        "safety_pass": all(safety_votes),
        "contract_agreement": len(set(contract_votes)) == 1,
        "safety_agreement": len(set(safety_votes)) == 1,
        "stable": (
            len(set(contract_votes)) == 1
            and len(set(safety_votes)) == 1
            and max(scores) - min(scores) <= 15
        ),
    }


def _diagnose(item: dict[str, Any], judge_models: tuple[str, ...]) -> dict[str, Any]:
    evaluations = item["evaluations"]
    original = _aggregate_output(evaluations, "original")
    reference = _aggregate_output(evaluations, "reference")
    model_deltas = []
    for judge_model in judge_models:
        original_row = next(
            row
            for row in evaluations
            if row["output"] == "original" and row["judge_model"] == judge_model
        )
        reference_row = next(
            row
            for row in evaluations
            if row["output"] == "reference" and row["judge_model"] == judge_model
        )
        model_deltas.append(
            round(
                float(reference_row["judgment"]["quality_score"])
                - float(original_row["judgment"]["quality_score"]),
                2,
            )
        )
    average_delta = round(
        reference["quality_score_average"] - original["quality_score_average"], 2
    )

    if not original["stable"] or not reference["stable"]:
        diagnosis = "평가 불안정"
    elif not original["contract_pass"] and not reference["contract_pass"]:
        diagnosis = "공통 프롬프트·계약 문제"
    elif (
        reference["contract_pass"]
        and reference["safety_pass"]
        and (
            not original["contract_pass"]
            or not original["safety_pass"]
            or (average_delta >= 8 and all(delta > 0 for delta in model_deltas))
        )
    ):
        diagnosis = "라우팅 성능 부족 근거 확인"
    elif (
        original["contract_pass"]
        and original["safety_pass"]
        and average_delta <= 5
    ):
        diagnosis = "사전 난이도 가설 과대평가 가능"
    else:
        diagnosis = "판단 보류"
    return {
        "diagnosis": diagnosis,
        "original": original,
        "reference": reference,
        "quality_score_delta": average_delta,
        "judge_score_deltas": dict(zip(judge_models, model_deltas, strict=True)),
    }


def _summary(state: dict[str, Any]) -> dict[str, Any]:
    completed = [item for item in state["cases"] if item.get("status") == "completed"]
    diagnoses = Counter(item["diagnosis"]["diagnosis"] for item in completed)
    reference_cost = sum(float(item["reference"]["cost_usd"]) for item in completed)
    original_cost = sum(float(item["original"]["task_cost_usd"]) for item in completed)
    judge_cost = sum(
        float(evaluation["judge_usage"]["cost_usd"])
        for item in completed
        for evaluation in item["evaluations"]
    )
    method_validation_cost = float(
        state.get("discarded_method_validation_cost_usd") or 0
    )
    original_contract_count = sum(
        1 for item in completed if item["diagnosis"]["original"]["contract_pass"]
    )
    reference_contract_count = sum(
        1 for item in completed if item["diagnosis"]["reference"]["contract_pass"]
    )
    original_safety_count = sum(
        1 for item in completed if item["diagnosis"]["original"]["safety_pass"]
    )
    reference_safety_count = sum(
        1 for item in completed if item["diagnosis"]["reference"]["safety_pass"]
    )

    def has_assumptions(output_text: str) -> bool:
        try:
            payload = json.loads(output_text)
        except (TypeError, ValueError):
            return False
        return isinstance(payload, dict) and "assumptions" in payload

    return {
        "source_case_count": int(state.get("source_case_count") or len(completed)),
        "case_count": len(completed),
        "diagnosis_counts": dict(diagnoses),
        "original_quality_score_average": round(
            statistics.mean(
                item["diagnosis"]["original"]["quality_score_average"]
                for item in completed
            ),
            2,
        ),
        "reference_quality_score_average": round(
            statistics.mean(
                item["diagnosis"]["reference"]["quality_score_average"]
                for item in completed
            ),
            2,
        ),
        "quality_score_average_delta": round(
            statistics.mean(
                item["diagnosis"]["reference"]["quality_score_average"]
                - item["diagnosis"]["original"]["quality_score_average"]
                for item in completed
            ),
            2,
        ),
        "original_contract_pass_count": original_contract_count,
        "reference_contract_pass_count": reference_contract_count,
        "original_safety_pass_count": original_safety_count,
        "reference_safety_pass_count": reference_safety_count,
        "original_assumptions_count": sum(
            1 for item in completed if has_assumptions(item["original"]["output_text"])
        ),
        "reference_assumptions_count": sum(
            1 for item in completed if has_assumptions(item["reference"]["output_text"])
        ),
        "original_task_cost_usd": round(original_cost, 8),
        "reference_generation_cost_usd": round(reference_cost, 8),
        "reference_cost_multiplier": round(reference_cost / original_cost, 2)
        if original_cost
        else None,
        "quality_judge_cost_usd": round(judge_cost, 8),
        "posthoc_evaluation_cost_usd": round(reference_cost + judge_cost, 8),
        "discarded_method_validation_cost_usd": round(method_validation_cost, 8),
        "actual_total_cost_usd": round(
            reference_cost + judge_cost + method_validation_cost, 8
        ),
    }


def _markdown(state: dict[str, Any]) -> str:
    summary = state["summary"]
    counts = summary["diagnosis_counts"]
    lines = [
        "# 자동 모델 라우팅 성능 부족 후보 사후 익명 품질 평가",
        "",
        "## 결론",
        "",
        f"- 비교 대상: {summary['case_count']}건",
        f"- 기존 자동 선택 출력 평균: {summary['original_quality_score_average']}점",
        f"- 고성능 기준 출력 평균: {summary['reference_quality_score_average']}점",
        f"- 평균 품질 차이: 기준 출력이 {summary['quality_score_average_delta']:+.2f}점",
        f"- 라우팅 성능 부족 근거 확인: {counts.get('라우팅 성능 부족 근거 확인', 0)}건",
        f"- 사전 난이도 가설 과대평가 가능: {counts.get('사전 난이도 가설 과대평가 가능', 0)}건",
        f"- 공통 프롬프트·계약 문제: {counts.get('공통 프롬프트·계약 문제', 0)}건",
        f"- 평가 불안정 또는 판단 보류: {counts.get('평가 불안정', 0) + counts.get('판단 보류', 0)}건",
        "",
        "각 출력은 다른 출력을 보지 못한 상태에서 라우팅 판단과 분리된 강한 품질 Judge가 "
        "절대 점수로 채점했습니다.",
        "",
        "이번 결과는 기존 30건 전체 품질이 아니라, 사전 등급 검사에서 성능 부족으로 표시된 "
        f"12건만 사후 평가한 결과입니다. 나머지 {summary['source_case_count'] - summary['case_count']}건의 "
        "자동 출력 품질은 아직 평가하지 않았습니다.",
        "",
        "## 계약·안전 결과",
        "",
        f"- 기존 출력 계약 통과: {summary['original_contract_pass_count']}/{summary['case_count']}",
        f"- 기준 출력 계약 통과: {summary['reference_contract_pass_count']}/{summary['case_count']}",
        f"- 기존 출력 안전 통과: {summary['original_safety_pass_count']}/{summary['case_count']}",
        f"- 기준 출력 안전 통과: {summary['reference_safety_pass_count']}/{summary['case_count']}",
        f"- 기존 출력의 `assumptions` 분리: {summary['original_assumptions_count']}/{summary['case_count']}",
        f"- 기준 출력의 `assumptions` 분리: {summary['reference_assumptions_count']}/{summary['case_count']}",
        "",
        "입력 제약은 누락 정보를 `assumptions`에 분리하도록 요구하지만, 실험의 필수 JSON "
        "스키마에는 해당 필드가 선언돼 있지 않습니다. 고성능 모델은 추가 제약을 따랐고 낮은 등급 "
        "모델은 주로 필수 스키마만 따랐기 때문에, 이번 격차에는 모델 지시 이행 능력과 실험 계약 "
        "불일치가 함께 반영돼 있습니다.",
        "",
        "## 실험 조건",
        "",
        f"- 원본 보고서: `{state['source_report']}`",
        f"- 고성능 기준 모델: `{state['reference_model']}`",
        f"- 독립 품질 Judge: `{', '.join(state['judge_models'])}`",
        "- 기존 30건 중 사전 모델 등급 기준으로 `underpowered`였던 12건만 비교",
        "- 자동 선택 출력은 기존 산출물을 재사용",
        "- 기준 출력은 당시와 같은 프롬프트·입력·JSON 계약으로 새로 생성",
        "- 모델 ID와 출력 출처를 품질 Judge에 전달하지 않음",
        "",
        "## 비용",
        "",
        f"- 기존 자동 선택 모델 12건 실행: ${summary['original_task_cost_usd']:.6f}",
        f"- 기준 출력 생성: ${summary['reference_generation_cost_usd']:.6f}",
        f"- 기준 모델 실행비 배수: {summary['reference_cost_multiplier']:.2f}배",
        f"- 독립 품질 평가: ${summary['quality_judge_cost_usd']:.6f}",
        f"- 최종 방식 사후 평가 합계: ${summary['posthoc_evaluation_cost_usd']:.6f}",
        f"- 폐기한 예비 평가 방식 비용: ${summary['discarded_method_validation_cost_usd']:.6f}",
        f"- 실제 총 지출: ${summary['actual_total_cost_usd']:.6f}",
        "",
        "## 사례별 판정",
        "",
        "| 사례 | 사전 난이도 | 기존 모델 | 기존 점수 | 기준 점수 | Judge별 점수 차이 | 판정 |",
        "| --- | --- | --- | ---: | ---: | --- | --- |",
    ]
    for item in state["cases"]:
        diagnosis = item["diagnosis"]
        deltas = ", ".join(
            f"{model}: {delta:+.1f}" for model, delta in diagnosis["judge_score_deltas"].items()
        )
        lines.append(
            f"| {item['case_id']} | {item['expected_difficulty']} | "
            f"{item['original']['model_id']} | "
            f"{diagnosis['original']['quality_score_average']:.2f} | "
            f"{diagnosis['reference']['quality_score_average']:.2f} | "
            f"{deltas} | {diagnosis['diagnosis']} |"
        )

    lines.extend(["", "## 상세 결과", ""])
    for item in state["cases"]:
        lines.extend(
            [
                f"### {item['case_id']}",
                "",
                f"- 요청: {item['input']['request']}",
                f"- 기존 모델: `{item['original']['model_id']}`",
                f"- 판정: **{item['diagnosis']['diagnosis']}**",
                f"- 점수 차이(기준-기존): {item['diagnosis']['quality_score_delta']:+.2f}",
                "",
            ]
        )
        for evaluation in item["evaluations"]:
            judgment = evaluation["judgment"]
            lines.append(
                f"- `{evaluation['judge_model']}` / `{evaluation['output']}`: "
                f"{judgment['quality_score']:.1f}점, 계약={judgment['contract_pass']}, "
                f"안전={judgment['safety_pass']} | {judgment['reason']}"
            )
        lines.extend(
            [
                "",
                "기존 자동 선택 출력:",
                "",
                "```json",
                item["original"]["output_text"],
                "```",
                "",
                "고성능 기준 출력:",
                "",
                "```json",
                item["reference"]["output_text"],
                "```",
                "",
            ]
        )
    return "\n".join(lines) + "\n"


def _write_partial(output_dir: pathlib.Path, state: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "comparison.partial.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _reference_cache(path: pathlib.Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        str(item["case_id"]): item["reference"]
        for item in payload.get("cases") or []
        if isinstance(item.get("reference"), dict)
    }


def _discarded_method_validation_cost(source_path: pathlib.Path) -> float:
    """최종 방식에서 제외한 예비 Judge 호출 비용만 합산한다."""

    experiment_root = source_path.parent.parent
    total = 0.0
    for relative_path in (
        "posthoc-quality/comparison.json",
        "posthoc-quality-pointwise-v2/comparison.json",
    ):
        path = experiment_root / relative_path
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        total += float((payload.get("summary") or {}).get("quality_judge_cost_usd") or 0)
    return round(total, 8)


def run(args: argparse.Namespace) -> dict[str, Any]:
    source_path = pathlib.Path(args.source_report).resolve()
    output_dir = pathlib.Path(args.output_dir).resolve()
    judge_models = tuple(
        dict.fromkeys(model.strip() for model in args.judge_models.split(",") if model.strip())
    )
    if not judge_models:
        raise RuntimeError("품질 Judge 모델이 하나 이상 필요합니다.")
    source = json.loads(source_path.read_text(encoding="utf-8"))
    selected = _selected_rows(source)
    if args.limit is not None:
        selected = selected[: args.limit]
    if not selected:
        raise RuntimeError("사후 비교할 underpowered 사례가 없습니다.")

    if not args.execute:
        return {
            "dry_run": True,
            "case_count": len(selected),
            "case_ids": [row["case_id"] for row in selected],
            "reference_model": args.reference_model,
            "judge_models": list(judge_models),
        }

    partial_path = output_dir / "comparison.partial.json"
    if partial_path.exists():
        state = json.loads(partial_path.read_text(encoding="utf-8"))
        if tuple(state.get("judge_models") or []) != judge_models:
            raise RuntimeError("기존 사후 평가와 Judge 모델 구성이 다릅니다.")
    else:
        state = {
            "evaluation_method": "anonymous_pointwise_cross_model_v2",
            "started_at": _now(),
            "source_report": str(source_path),
            "reference_model": args.reference_model,
            "judge_models": list(judge_models),
            "cases": [],
        }
    state["source_case_count"] = len(source.get("runs") or [])
    state["discarded_method_validation_cost_usd"] = _discarded_method_validation_cost(
        source_path
    )
    cache_path = pathlib.Path(args.reference_cache).resolve() if args.reference_cache else None
    cached_references = _reference_cache(cache_path)
    existing = {item["case_id"]: item for item in state["cases"]}

    for index, row in enumerate(selected, start=1):
        case_id = str(row["case_id"])
        item = existing.get(case_id)
        automatic = row["arms"][AUTO_ARM]
        if item is None:
            item = {
                "case_id": case_id,
                "category": row.get("category"),
                "expected_difficulty": row.get("expected_difficulty"),
                "customer_tier": row.get("customer_tier"),
                "input": row["input"],
                "routing_accuracy": row.get("routing_accuracy") or {},
                "original": {
                    "model_id": automatic.get("selected_model"),
                    "output_text": automatic.get("output_text") or "",
                    "schema_pass": bool(automatic.get("schema_pass")),
                    "task_cost_usd": float(automatic.get("task_cost_usd") or 0),
                },
                "reference": cached_references.get(case_id),
                "evaluations": [],
                "status": "running",
            }
            state["cases"].append(item)
            existing[case_id] = item

        print(f"[{index}/{len(selected)}] {case_id}", flush=True)
        if not isinstance(item.get("reference"), dict):
            reference = _invoke_json_model(
                model_id=args.reference_model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": _user_prompt(row)},
                ],
                max_tokens=1400,
            )
            reference_text = reference.pop("text")
            item["reference"] = {
                **reference,
                "output_text": reference_text,
                "schema_pass": _schema_pass(reference_text),
            }
            _write_partial(output_dir, state)

        completed_keys = {
            (evaluation["output"], evaluation["judge_model"])
            for evaluation in item["evaluations"]
        }
        for judge_model in judge_models:
            for output_name, output_text in (
                ("original", item["original"]["output_text"]),
                ("reference", item["reference"]["output_text"]),
            ):
                if (output_name, judge_model) in completed_keys:
                    continue
                item["evaluations"].append(
                    _evaluate_output(
                        row,
                        output_name=output_name,
                        output_text=output_text,
                        judge_model=judge_model,
                    )
                )
                _write_partial(output_dir, state)

        item["diagnosis"] = _diagnose(item, judge_models)
        item["status"] = "completed"
        _write_partial(output_dir, state)

    state["completed_at"] = _now()
    state["summary"] = _summary(state)
    final_json = output_dir / "comparison.json"
    final_md = output_dir / "report.md"
    final_json.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    final_md.write_text(_markdown(state), encoding="utf-8")
    return {
        "json": str(final_json),
        "markdown": str(final_md),
        "summary": state["summary"],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="자동 라우팅 출력 사후 익명 품질 평가")
    parser.add_argument("--execute", action="store_true", help="실제 provider 호출을 실행합니다.")
    parser.add_argument("--source-report", default=str(DEFAULT_SOURCE))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--reference-cache", default=str(DEFAULT_REFERENCE_CACHE))
    parser.add_argument("--reference-model", default=DEFAULT_REFERENCE_MODEL)
    parser.add_argument("--judge-models", default=",".join(DEFAULT_JUDGE_MODELS))
    parser.add_argument("--limit", type=int, default=None)
    return parser.parse_args()


if __name__ == "__main__":
    print(json.dumps(run(parse_args()), ensure_ascii=False, indent=2), flush=True)
