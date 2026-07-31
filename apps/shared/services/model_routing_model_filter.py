"""모델 라우팅 후보에서 노드별 제외 모델을 걸러내는 공통 helper."""

from typing import Any

from apps.shared.services.model_routing_global_profile_catalog import (
    canonical_model_routing_id,
)


def normalize_model_routing_model_id(value: Any) -> str:
    """권한/제외 비교에 사용할 provider-agnostic 모델 ID를 반환한다."""
    return str(value or "").strip().lower().removeprefix("models/")


def model_routing_excluded_model_ids(node_data: dict[str, Any]) -> set[str]:
    """노드 설정에서 자동 라우팅 제외 모델 ID를 읽는다."""
    policy = node_data.get("model_routing_policy")
    raw_values = policy.get("excluded_model_ids") if isinstance(policy, dict) else None
    if not isinstance(raw_values, list):
        return set()
    return {
        normalized
        for value in raw_values
        if (normalized := canonical_model_routing_id(value))
    }


def filter_model_routing_available_model_ids(
    model_ids: list[str] | set[str] | tuple[str, ...],
    *,
    node_data: dict[str, Any],
) -> list[str]:
    """원래 catalog 표기를 보존하면서 노드에서 제외한 모델만 제거한다."""
    excluded_model_ids = model_routing_excluded_model_ids(node_data)
    return [
        model_id
        for model_id in model_ids
        if canonical_model_routing_id(model_id) not in excluded_model_ids
    ]


def filter_supported_model_routing_candidates(
    model_ids: list[str] | set[str] | tuple[str, ...],
) -> list[str]:
    """자동 라우팅용으로 검증된 명시적 catalog 후보군만 남긴다."""

    from apps.shared.services.model_routing_global_profile_catalog import (
        supported_model_routing_ids,
    )

    return supported_model_routing_ids(model_ids)
