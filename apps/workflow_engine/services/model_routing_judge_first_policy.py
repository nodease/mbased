"""Judge-first + 점진적 local router 정책의 단일 생성 경계."""

from __future__ import annotations

from typing import Any, Iterable

from apps.shared.services.model_routing_global_profile_catalog import (
    catalog_metadata_for_model_id,
    canonical_model_routing_id,
    deduplicate_model_routing_ids,
)


JUDGE_FIRST_STRATEGY_ID = "judge_bootstrap_incremental_v1"
DEFAULT_LOCAL_CONFIDENCE_THRESHOLD = 0.78
RUNTIME_JUDGE_MODEL_PREFERENCES = {
    "openai": ("gpt-5.4-mini", "gpt-5-mini", "gpt-4.1-mini"),
    "anthropic": (
        "claude-sonnet-4-6",
        "claude-sonnet-4-5-20250929",
        "claude-haiku-4-5-20251001",
    ),
    "google": ("gemini-3.5-flash", "gemini-2.5-flash", "gemini-3.1-flash-lite"),
}


def build_judge_first_active_policy(
    *,
    policy_version: str,
    default_model_id: str,
    fallback_model_id: str | None,
    candidate_model_ids: Iterable[str],
    judge_model_id: str | None = None,
) -> dict[str, Any]:
    """실행 가능한 후보만 포함한 Judge-first active policy를 만든다."""

    candidates = deduplicate_model_routing_ids(candidate_model_ids)
    default_model = str(default_model_id or "").strip()
    if not default_model:
        raise ValueError("model_routing.default_model_required")
    default_model = _available_representative(default_model, candidates) or default_model
    if default_model not in candidates:
        candidates.insert(0, default_model)

    fallback_model = str(fallback_model_id or "").strip() or None
    if fallback_model:
        fallback_model = _available_representative(fallback_model, candidates) or fallback_model
    if fallback_model and fallback_model not in candidates:
        candidates.append(fallback_model)
    if fallback_model == default_model:
        fallback_model = None

    judge_model = select_runtime_judge_model_id(
        candidates,
        default_model_id=default_model,
        configured_judge_model_id=judge_model_id,
    )

    return {
        "strategy_id": JUDGE_FIRST_STRATEGY_ID,
        "policy_version": str(policy_version or "judge-first-v1"),
        "default_model_id": default_model,
        "fallback_model_id": fallback_model,
        "judge_model_id": judge_model,
        "candidate_model_ids": candidates,
    }


def normalize_judge_first_active_policy(
    active_policy: dict[str, Any] | None,
    *,
    policy_version: str,
    default_model_id: str,
    fallback_model_id: str | None,
    candidate_model_ids: Iterable[str],
) -> dict[str, Any]:
    """구형 전략 필드는 버리고 유효한 Judge-first 학습 상태만 보존한다."""

    source = active_policy if isinstance(active_policy, dict) else {}
    return build_judge_first_active_policy(
        policy_version=policy_version,
        default_model_id=default_model_id,
        fallback_model_id=fallback_model_id,
        candidate_model_ids=candidate_model_ids,
        judge_model_id=_explicit_judge_model_id(source, default_model_id),
    )


def is_judge_first_active_policy(active_policy: Any) -> bool:
    return (
        isinstance(active_policy, dict)
        and active_policy.get("strategy_id") == JUDGE_FIRST_STRATEGY_ID
    )


def _available_representative(model_id: str, candidates: list[str]) -> str | None:
    canonical_id = canonical_model_routing_id(model_id)
    return next(
        (
            candidate
            for candidate in candidates
            if canonical_model_routing_id(candidate) == canonical_id
        ),
        None,
    )


def select_runtime_judge_model_id(
    candidate_model_ids: Iterable[str],
    *,
    default_model_id: str,
    configured_judge_model_id: str | None = None,
) -> str:
    """Select a stable Judge independently from the routed task model.

    Older policies stored the task default as ``judge_model_id``. Treat that
    coupling as legacy and prefer the same provider's known Judge model when it
    is executable for the current subject.
    """

    candidates = deduplicate_model_routing_ids(candidate_model_ids)
    default_model = _available_representative(default_model_id, candidates)
    default_model = default_model or str(default_model_id or "").strip()
    configured = _available_representative(
        str(configured_judge_model_id or "").strip(), candidates
    )
    if configured and canonical_model_routing_id(configured) != canonical_model_routing_id(
        default_model
    ):
        return configured

    provider = catalog_metadata_for_model_id(default_model).get("provider")
    for preferred_model_id in RUNTIME_JUDGE_MODEL_PREFERENCES.get(provider, ()):
        available = _available_representative(preferred_model_id, candidates)
        if available:
            return available
    if configured:
        return configured
    if default_model:
        return default_model
    if candidates:
        return candidates[0]
    raise ValueError("model_routing.judge_model_required")


def _explicit_judge_model_id(
    active_policy: dict[str, Any], default_model_id: str
) -> str | None:
    configured = str(active_policy.get("judge_model_id") or "").strip()
    if not configured:
        return None
    if canonical_model_routing_id(configured) == canonical_model_routing_id(
        default_model_id
    ):
        return None
    return configured
