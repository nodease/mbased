"""운영 증거가 부족한 시점에만 쓰는 수동 모델 라우팅 점수."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from apps.shared.services.model_routing_global_profile_catalog import (
    canonical_model_routing_id,
    normalize_model_id,
)


BOOTSTRAP_ROUTING_SCORE_VERSION = "manual-bootstrap-score-v1"
GENERATIVE_RESPONSE_SCORE_FLOOR = 1.70
SAFE_FALLBACK_SCORE_MARGIN = 0.35

# 이 값은 모델 품질의 객관적 점수가 아니다. 공급자 포지셔닝과 Nodease의 보수적
# 초기 정책을 0~3 구간에 배치한 낮은 신뢰도의 prior이며, 운영 증거가 충분해지면
# 기존 operational evidence 선택 경계가 우선한다.
BOOTSTRAP_MODEL_ROUTING_SCORES: dict[str, float] = {
    "gpt-4o-mini": 1.00,
    "gpt-5-nano": 1.05,
    "gpt-5.4-nano": 1.10,
    "gpt-4.1-mini": 1.70,
    "gpt-4o": 1.85,
    "gpt-5-mini": 1.90,
    "gpt-5.6-luna": 1.95,
    "gpt-4.1": 2.10,
    "gpt-5": 2.20,
    "gpt-5.1": 2.20,
    "gpt-5.2": 2.20,
    "gpt-5.4-mini": 2.35,
    "gpt-5.6-terra": 2.65,
    "gpt-5.4": 2.75,
    "gpt-5.5": 2.75,
    "o3": 2.85,
    "gpt-5.4-pro": 2.95,
    "gpt-5.5-pro": 2.95,
    "gpt-5.6": 3.00,
    "gpt-5.6-sol": 3.00,
    "o3-pro": 3.00,
    "claude-haiku-4-5": 1.80,
    "claude-haiku-4-5-20251001": 1.80,
    "claude-sonnet-4-5-20250929": 2.65,
    "claude-sonnet-4-6": 2.70,
    "claude-sonnet-5": 2.75,
    "claude-fable-5": 2.90,
    "claude-opus-4-5-20251101": 2.90,
    "claude-opus-4-6": 2.95,
    "claude-opus-4-7": 2.95,
    "claude-opus-4-8": 3.00,
    "gemini-2.5-flash-lite": 1.00,
    "gemini-3.1-flash-lite": 1.65,
    "gemini-2.5-flash": 1.90,
    "gemini-3.5-flash": 2.55,
    "gemini-3-flash-preview": 2.60,
    "gemini-3.1-pro-preview": 2.75,
    "gemini-2.5-pro": 2.80,
}

CRITICAL_AXIS_SCORE_FLOORS: dict[str, float] = {
    "task_complexity": 2.40,
    "decision_impact": 2.90,
    "evidence_synthesis": 2.50,
}


def _axis_score(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        return max(0.0, min(3.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def required_bootstrap_score(
    requirements: Mapping[str, Any],
    *,
    structural_facts: Mapping[str, Any] | None = None,
) -> float:
    """세 요구 축 평균과 출력 계약에 필요한 보수적 하한을 계산한다."""

    axes = {
        name: _axis_score(requirements.get(name))
        for name in CRITICAL_AXIS_SCORE_FLOORS
    }
    required = sum(axes.values()) / len(axes)
    for name, floor in CRITICAL_AXIS_SCORE_FLOORS.items():
        if axes[name] >= 3.0:
            required = max(required, floor)
    facts = structural_facts if isinstance(structural_facts, Mapping) else {}
    task_intent = str(facts.get("task_intent") or "").strip().lower()
    if task_intent in {"generate", "generation", "respond", "response", "chat"}:
        required = max(required, GENERATIVE_RESPONSE_SCORE_FLOOR)
    return required


def model_bootstrap_score(model_id: object) -> float | None:
    """실행 alias를 canonical ID로 맞춘 뒤 수동 초기 점수를 반환한다."""

    normalized = normalize_model_id(model_id)
    canonical = canonical_model_routing_id(normalized)
    value = BOOTSTRAP_MODEL_ROUTING_SCORES.get(canonical)
    if value is None:
        value = BOOTSTRAP_MODEL_ROUTING_SCORES.get(normalized)
    return value


def required_safe_fallback_score(
    requirements: Mapping[str, Any],
    *,
    structural_facts: Mapping[str, Any] | None = None,
) -> float:
    """불확실한 Judge 판정에서 일반 선택보다 한 단계 보수적인 하한을 만든다."""

    return min(
        3.0,
        required_bootstrap_score(
            requirements,
            structural_facts=structural_facts,
        )
        + SAFE_FALLBACK_SCORE_MARGIN,
    )
