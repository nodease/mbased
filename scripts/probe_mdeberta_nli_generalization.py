"""Bootstrap 난이도 분류기의 미학습 문장 일반화 가능성을 점검한다.

이 스크립트는 workflow나 DB를 변경하지 않는다. 현재 실험 데이터셋의 질문을
다국어 NLI(Natural Language Inference) 모델에 넣어, 경제형/균형형/고성능형
난이도 설명 중 어느 설명이 가장 잘 성립하는지 측정한다.

실행 예시:
    docker cp scripts/probe_mdeberta_nli_generalization.py <workflow-engine>:/tmp/
    docker compose exec workflow_engine python /tmp/probe_mdeberta_nli_generalization.py
"""

from __future__ import annotations

import json
import ast
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer


MODEL_ID = "MoritzLaurer/mDeBERTa-v3-base-mnli-xnli"
TASK_DESCRIPTION = (
    "이 노드는 사내 공통·플랫폼·영업·재무 온보딩 문서를 검색해 한국어 질문에 근거 기반으로 "
    "답합니다. 검색 근거가 없으면 추측하지 않고 확인할 정보를 안내해야 합니다."
)
TIER_HYPOTHESES = {
    "economy": "이 요청은 한 가지 사실, 메뉴 위치, 문서 이름, 일정 또는 짧은 경로만 확인하면 충분한 간단한 요청이다.",
    "balanced": "이 요청은 여러 절차를 순서대로 정리하거나, 두 역할 또는 정책을 비교하고 함께 종합해야 하는 요청이다.",
    "advanced": "이 요청은 상충하는 규정, 보안·개인정보·재무 위험, 긴급 예외 또는 여러 제약을 함께 판단해야 하는 요청이다.",
}


def _load_cases() -> list[Any]:
    """전체 실험 모듈의 부수 의존성 없이 질문 데이터만 읽는다."""

    source = Path("/tmp/experiment_bootstrap_difficulty_routing.py")
    if not source.exists():
        source = Path(__file__).resolve().parent / "experiment_bootstrap_difficulty_routing.py"
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    namespace: dict[str, Any] = {
        "dataclass": dataclass,
        "Literal": Literal,
        "random": __import__("random"),
    }
    needed_names = {
        "Difficulty",
        "ExperimentCase",
        "ECONOMY_QUESTIONS",
        "BALANCED_QUESTIONS",
        "ADVANCED_QUESTIONS",
        "build_cases",
    }
    for node in tree.body:
        names = {
            target.id
            for target in getattr(node, "targets", [])
            if isinstance(target, ast.Name)
        }
        node_name = getattr(node, "name", None)
        if names & needed_names or node_name in needed_names:
            exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), namespace)
    build_cases = namespace.get("build_cases")
    if not callable(build_cases):
        raise RuntimeError("실험 입력의 build_cases를 읽지 못했습니다.")
    return build_cases(count=20, shuffle_seed=451)


def _entailment_index(model: Any) -> int:
    for index, label in model.config.id2label.items():
        if str(label).casefold() == "entailment":
            return int(index)
    raise RuntimeError(f"NLI entailment label을 찾지 못했습니다: {model.config.id2label}")


def _predict(
    *,
    tokenizer: Any,
    model: Any,
    entailment_index: int,
    question: str,
) -> tuple[str, dict[str, float]]:
    premise = f"작업: {TASK_DESCRIPTION}\n\n현재 요청: {question}"
    tiers = list(TIER_HYPOTHESES)
    encoded = tokenizer(
        [premise] * len(tiers),
        [TIER_HYPOTHESES[tier] for tier in tiers],
        padding=True,
        truncation=True,
        max_length=512,
        return_tensors="pt",
    )
    with torch.no_grad():
        logits = model(**encoded).logits[:, entailment_index]
    probabilities = torch.softmax(logits, dim=0).tolist()
    scores = {tier: float(score) for tier, score in zip(tiers, probabilities, strict=True)}
    return max(scores, key=scores.__getitem__), scores


def main() -> None:
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, use_fast=False)
    model = AutoModelForSequenceClassification.from_pretrained(MODEL_ID)
    model.eval()
    entailment_index = _entailment_index(model)

    rows = []
    for case in _load_cases():
        predicted, scores = _predict(
            tokenizer=tokenizer,
            model=model,
            entailment_index=entailment_index,
            question=case.question,
        )
        rows.append(
            {
                "case_id": case.case_id,
                "expected": case.expected_difficulty,
                "predicted": predicted,
                "correct": predicted == case.expected_difficulty,
                "scores": scores,
            }
        )

    accuracy = sum(row["correct"] for row in rows) / len(rows)
    summary = {
        "model_id": MODEL_ID,
        "case_count": len(rows),
        "accuracy": accuracy,
        "predicted_counts": dict(Counter(row["predicted"] for row in rows)),
        "rows": rows,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
