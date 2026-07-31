"""정책 교체가 실제 품질 또는 효율 개선일 때만 허용한다."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ModelRoutingPolicyChangeDecision:
    status: str
    reason_code: str
    max_cost_improvement: float = 0.0
    max_latency_improvement: float = 0.0


class ModelRoutingPolicyChangeGuard:
    QUALITY_DROP_TOLERANCE = 0.01
    EFFICIENCY_IMPROVEMENT_THRESHOLD = 0.10

    @classmethod
    def evaluate(
        cls,
        current: dict[str, Any] | None,
        proposed: dict[str, Any] | None,
    ) -> ModelRoutingPolicyChangeDecision:
        current = current or {}
        proposed = proposed or {}
        current_profiles = cls._profiles(current)
        proposed_profiles = cls._profiles(proposed)
        if cls._routing_signature(current) == cls._routing_signature(proposed):
            return ModelRoutingPolicyChangeDecision(
                status="kept_current",
                reason_code="routing_selection_unchanged",
            )

        max_cost = 0.0
        max_latency = 0.0
        for profile_name, proposed_model in proposed_profiles.items():
            current_model = current_profiles.get(profile_name)
            if not current_model or current_model == proposed_model:
                continue
            current_score = cls._candidate_score(current, profile_name, current_model)
            proposed_score = cls._candidate_score(
                proposed, profile_name, proposed_model
            )
            if not current_score or not proposed_score:
                continue
            old_quality = float(current_score.get("quality_lower_bound") or 0)
            new_quality = float(proposed_score.get("quality_lower_bound") or 0)
            if new_quality + cls.QUALITY_DROP_TOLERANCE < old_quality:
                return ModelRoutingPolicyChangeDecision(
                    status="kept_current",
                    reason_code="quality_guard_failed",
                )
            max_cost = max(
                max_cost,
                cls._improvement(
                    current_score.get("expected_total_cost_usd"),
                    proposed_score.get("expected_total_cost_usd"),
                ),
            )
            max_latency = max(
                max_latency,
                cls._improvement(
                    current_score.get("expected_latency_ms"),
                    proposed_score.get("expected_latency_ms"),
                ),
            )

        if max(max_cost, max_latency) < cls.EFFICIENCY_IMPROVEMENT_THRESHOLD:
            return ModelRoutingPolicyChangeDecision(
                status="kept_current",
                reason_code="improvement_below_threshold",
                max_cost_improvement=max_cost,
                max_latency_improvement=max_latency,
            )
        return ModelRoutingPolicyChangeDecision(
            status="applied",
            reason_code="material_efficiency_improvement",
            max_cost_improvement=max_cost,
            max_latency_improvement=max_latency,
        )

    @staticmethod
    def _profiles(policy: dict[str, Any]) -> dict[str, str]:
        return {
            str(row.get("profile")): str(row.get("selected_model_id"))
            for row in policy.get("decision_profiles") or []
            if isinstance(row, dict)
            and row.get("profile")
            and row.get("selected_model_id")
        }

    @classmethod
    def _routing_signature(cls, policy: dict[str, Any]) -> tuple[Any, ...]:
        return (
            policy.get("default_model_id"),
            tuple(sorted(cls._profiles(policy).items())),
        )

    @staticmethod
    def _candidate_score(
        policy: dict[str, Any], profile_name: str, model_id: str
    ) -> dict[str, Any] | None:
        for row in policy.get("decision_profiles") or []:
            if not isinstance(row, dict) or str(row.get("profile")) != profile_name:
                continue
            scores = row.get("candidate_scores")
            if isinstance(scores, dict) and isinstance(scores.get(model_id), dict):
                return scores[model_id]
        return None

    @staticmethod
    def _improvement(old: Any, new: Any) -> float:
        try:
            old_value = float(old)
            new_value = float(new)
        except (TypeError, ValueError):
            return 0.0
        if old_value <= 0:
            return 0.0
        return max(0.0, (old_value - new_value) / old_value)
