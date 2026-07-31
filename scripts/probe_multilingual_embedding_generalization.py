"""Bootstrap 예문 기반 난이도 라우팅의 다국어 의미 일반화 성능을 측정한다.

원본 mDeBERTa encoder는 문장 유사도 학습이 목적이 아니어서 synthetic 예문과
표현이 달라지면 tier prototype을 안정적으로 비교하지 못할 수 있다. 이 도구는
다국어 retrieval embedding 모델에 같은 표본을 넣어, 별도 미학습 질문 20건에서
의미 유사도 기반 tier 판정이 개선되는지 확인한다.

workflow, DB, provider 호출은 하지 않는다.
"""

from __future__ import annotations

import argparse
import ast
import json
import random
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import torch
from transformers import AutoModel, AutoTokenizer


DEFAULT_MODEL_ID = "intfloat/multilingual-e5-small"
TIERS = ("economy", "balanced", "advanced")


def _load_cases(source: Path) -> list[Any]:
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    namespace: dict[str, Any] = {
        "dataclass": dataclass,
        "Literal": Literal,
        "random": random,
    }
    needed = {
        "Difficulty",
        "ExperimentCase",
        "ECONOMY_QUESTIONS",
        "BALANCED_QUESTIONS",
        "ADVANCED_QUESTIONS",
        "build_cases",
    }
    for node in tree.body:
        assigned = {
            target.id
            for target in getattr(node, "targets", [])
            if isinstance(target, ast.Name)
        }
        if assigned & needed or getattr(node, "name", None) in needed:
            exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), namespace)
    build_cases = namespace.get("build_cases")
    if not callable(build_cases):
        raise RuntimeError("실험 입력의 build_cases를 찾지 못했습니다.")
    return build_cases(count=20, shuffle_seed=451)


def _sample_text(sample: dict[str, Any]) -> str:
    safe_input = sample.get("safe_input_summary")
    if not isinstance(safe_input, dict):
        return ""
    values = [str(value).strip() for value in safe_input.values() if str(value).strip()]
    return "\n".join(values)


def _encode(tokenizer: Any, model: Any, texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    encoded = tokenizer(
        texts,
        padding=True,
        truncation=True,
        max_length=512,
        return_tensors="pt",
    )
    with torch.no_grad():
        output = model(**encoded).last_hidden_state
    mask = encoded["attention_mask"].unsqueeze(-1).expand_as(output)
    pooled = (output * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1)
    normalized = torch.nn.functional.normalize(pooled, p=2, dim=1)
    return normalized.cpu().tolist()


def _cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


def _predict(
    query: list[float],
    tier_vectors: dict[str, list[list[float]]],
) -> tuple[str, dict[str, float]]:
    scores: dict[str, float] = {}
    for tier, vectors in tier_vectors.items():
        top = sorted((_cosine(query, vector) for vector in vectors), reverse=True)[:2]
        scores[tier] = sum(top) / len(top)
    return max(scores, key=scores.__getitem__), scores


def _fit_linear_classifier(
    vectors: list[list[float]],
    labels: list[str],
) -> tuple[torch.nn.Module, dict[str, int]]:
    """작은 ridge-softmax 분류기가 prototype보다 일반화하는지 빠르게 점검한다."""

    label_index = {tier: index for index, tier in enumerate(TIERS)}
    features = torch.tensor(vectors, dtype=torch.float32)
    targets = torch.tensor([label_index[label] for label in labels], dtype=torch.long)
    classifier = torch.nn.Linear(features.shape[1], len(TIERS))
    optimizer = torch.optim.AdamW(classifier.parameters(), lr=0.03, weight_decay=0.08)
    for _ in range(600):
        optimizer.zero_grad()
        loss = torch.nn.functional.cross_entropy(classifier(features), targets)
        loss.backward()
        optimizer.step()
    classifier.eval()
    return classifier, label_index


def _predict_linear(
    classifier: torch.nn.Module,
    vector: list[float],
) -> tuple[str, dict[str, float]]:
    with torch.no_grad():
        probabilities = torch.softmax(
            classifier(torch.tensor([vector], dtype=torch.float32))[0], dim=0
        ).tolist()
    scores = {tier: float(probabilities[index]) for index, tier in enumerate(TIERS)}
    return max(scores, key=scores.__getitem__), scores


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument(
        "--case-source",
        type=Path,
        default=Path("/tmp/scripts/experiment_bootstrap_difficulty_routing.py"),
    )
    parser.add_argument("--model-id", default=DEFAULT_MODEL_ID)
    parser.add_argument(
        "--strategy",
        choices=("nearest_prototype", "linear"),
        default="nearest_prototype",
    )
    args = parser.parse_args()

    state = json.loads(args.state.read_text(encoding="utf-8"))
    bootstrap = state.get("bootstrap")
    if not isinstance(bootstrap, dict):
        raise RuntimeError("state에 bootstrap 정보가 없습니다.")
    samples = bootstrap.get("samples")
    if not isinstance(samples, list):
        raise RuntimeError("bootstrap samples가 없습니다.")
    labeled = [
        (str(sample.get("difficulty") or ""), _sample_text(sample))
        for sample in samples
        if isinstance(sample, dict)
        and str(sample.get("difficulty") or "") in TIERS
        and _sample_text(sample)
    ]
    if len(labeled) < 3:
        raise RuntimeError("난이도별 예문이 충분하지 않습니다.")

    tokenizer = AutoTokenizer.from_pretrained(args.model_id, use_fast=False)
    model = AutoModel.from_pretrained(args.model_id)
    model.eval()
    sample_vectors = _encode(
        tokenizer,
        model,
        [f"passage: {text}" for _, text in labeled],
    )
    tier_vectors: dict[str, list[list[float]]] = {tier: [] for tier in TIERS}
    for (tier, _), vector in zip(labeled, sample_vectors, strict=True):
        tier_vectors[tier].append(vector)
    linear_classifier, _ = _fit_linear_classifier(
        sample_vectors,
        [tier for tier, _ in labeled],
    )

    rows = []
    for case in _load_cases(args.case_source):
        vector = _encode(tokenizer, model, [f"query: {case.question}"])[0]
        if args.strategy == "linear":
            predicted, scores = _predict_linear(linear_classifier, vector)
        else:
            predicted, scores = _predict(vector, tier_vectors)
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
    print(
        json.dumps(
            {
                "model_id": args.model_id,
                "strategy": args.strategy,
                "case_count": len(rows),
                "accuracy": accuracy,
                "predicted_counts": dict(Counter(row["predicted"] for row in rows)),
                "rows": rows,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
