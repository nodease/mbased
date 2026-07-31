"""Judge-first + 점진적 local learning 정책 갱신 orchestration."""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from apps.shared.db.models.model_routing_policy import (
    LLMNodeModelRoutingPolicy,
    LLMNodeModelRoutingPolicyRunEvent,
    LLMNodeModelRoutingPolicyUpdate,
)
from apps.shared.db.models.workflow_deployment import WorkflowDeployment
from apps.shared.services.model_routing_model_filter import (
    filter_model_routing_available_model_ids,
    filter_supported_model_routing_candidates,
)
from apps.workflow_engine.services.llm_service import LLMService
from apps.workflow_engine.services.model_routing_operational_performance import (
    ModelRoutingOperationalPerformanceService,
)
from apps.workflow_engine.services.model_routing_policy_lifecycle import (
    ModelRoutingPolicyLifecycleService,
)
from apps.workflow_engine.services.model_routing_judge_first_policy import (
    JUDGE_FIRST_STRATEGY_ID,
    normalize_judge_first_active_policy,
)


logger = logging.getLogger(__name__)


class PersistedModelRoutingPolicyRefreshService:
    """Judge label과 운영 품질로 local-first 전환 여부만 갱신한다."""

    @classmethod
    def refresh(cls, db: Session, *, policy_id: str | uuid.UUID, trigger: str):
        policy = (
            db.query(LLMNodeModelRoutingPolicy)
            .filter(LLMNodeModelRoutingPolicy.id == uuid.UUID(str(policy_id)))
            .first()
        )
        if policy is None:
            return None

        requested_at = policy.refresh_requested_at or datetime.now(timezone.utc)
        update = LLMNodeModelRoutingPolicyUpdate(
            policy_id=policy.id,
            trigger=trigger,
            status="failed",
            input_summary={},
            output_summary={},
        )
        db.add(update)

        try:
            deployment = (
                db.query(WorkflowDeployment)
                .filter(WorkflowDeployment.id == policy.deployment_id)
                .first()
            )
            node_data = (
                cls._node_data(
                    deployment.graph_snapshot if deployment else {},
                    policy.node_id,
                )
                or {}
            )

            if not bool(node_data.get("auto_model_routing")):
                ModelRoutingPolicyLifecycleService.apply_refresh_result(
                    policy,
                    status="kept_current",
                    proposed_policy=policy.active_policy or {},
                    policy_version=policy.policy_version,
                )
                ModelRoutingPolicyLifecycleService.complete_refresh_cycle(
                    policy,
                    eligible_runs_since_last_refresh=cls._remaining_event_count(
                        db,
                        policy,
                        requested_at,
                    ),
                )
                update.status = "kept_current"
                update.output_summary = {
                    "reason": "자동 모델 라우팅이 꺼져 있어 기존 정책을 유지했습니다."
                }
                db.flush()
                return update

            active_policy = (
                policy.active_policy
                if isinstance(policy.active_policy, dict)
                else {}
            )
            if active_policy.get("strategy_id") != JUDGE_FIRST_STRATEGY_ID:
                active_policy = cls._migrate_legacy_policy(
                    db,
                    policy=policy,
                    node_data=node_data,
                )
                policy.active_policy = active_policy
                policy.policy_version = str(active_policy["policy_version"])

            return cls._refresh_judge_bootstrap_incremental_policy(
                db,
                policy=policy,
                update=update,
                requested_at=requested_at,
            )
        except Exception as exc:
            logger.exception(
                "[Model-Routing] Judge-first refresh failed: policy_id=%s trigger=%s error_type=%s",
                policy.id,
                trigger,
                type(exc).__name__,
            )
            ModelRoutingPolicyLifecycleService.apply_refresh_result(
                policy,
                status="failed",
                proposed_policy=policy.active_policy or {},
                policy_version=policy.policy_version,
            )
            policy.refresh_requested_at = None
            update.status = "failed"
            update.error_code = type(exc).__name__
            update.output_summary = {
                "reason": "Judge-first 모델 라우팅 정책 갱신에 실패했습니다."
            }
            db.flush()
            return update

    @classmethod
    def _refresh_judge_bootstrap_incremental_policy(
        cls,
        db: Session,
        *,
        policy: LLMNodeModelRoutingPolicy,
        update: LLMNodeModelRoutingPolicyUpdate,
        requested_at: datetime,
    ) -> LLMNodeModelRoutingPolicyUpdate:
        """Judge label과 완료된 운영 결과를 반영해 local-first 전환만 재평가한다."""
        from apps.workflow_engine.services.model_routing_learner_store import (
            ModelRoutingLearnerStore,
        )

        active_policy = (
            dict(policy.active_policy) if isinstance(policy.active_policy, dict) else {}
        )
        learning = ModelRoutingLearnerStore.runtime_snapshot(
            db,
            learner_id=policy.learner_id,
            version_id=policy.active_learner_version_id,
        )
        learning = learning if isinstance(learning, dict) else {}
        profile = ModelRoutingOperationalPerformanceService.profile_for_policy(
            db,
            policy_id=policy.id,
        )
        operational_contract_healthy = cls._operational_contract_is_healthy(profile)
        if (
            policy.active_learner_version_id is not None
            and not operational_contract_healthy
        ):
            # 학습 버전은 보존하되, 이 배포에서 관측한 계약 품질이 무너지면
            # 해당 정책만 Judge-first로 되돌린다.
            policy.active_learner_version_id = None
            learning = {**learning, "mode": "judge_first", "active_version": None}
        ModelRoutingPolicyLifecycleService.apply_refresh_result(
            policy,
            status="kept_current",
            proposed_policy=active_policy,
            policy_version=policy.policy_version,
        )
        policy.last_refreshed_at = requested_at
        policy.performance_checkpoint = (
            ModelRoutingOperationalPerformanceService.checkpoint_snapshot(
                db,
                policy_id=policy.id,
            )
        )
        ModelRoutingPolicyLifecycleService.complete_refresh_cycle(
            policy,
            eligible_runs_since_last_refresh=cls._remaining_event_count(
                db,
                policy,
                requested_at,
            ),
        )
        update.status = "kept_current"
        update.eligible_run_count = profile.operational_usable_runs
        update.excluded_run_count = cls._excluded_run_count(
            db,
            policy,
            requested_at,
            usable_run_count=profile.operational_usable_runs,
        )
        update.input_summary = {
            "strategy_id": JUDGE_FIRST_STRATEGY_ID,
            "judged_request_count": int(learning.get("judged_request_count") or 0),
            "learner_id": learning.get("id"),
        }
        update.output_summary = {
            "reason": (
                "운영 계약 품질이 기준 아래로 내려가 Judge 우선 선택으로 전환했습니다."
                if not operational_contract_healthy
                else "Judge 선택과 운영 품질을 다시 확인했습니다. 모델은 이 갱신에서 임의로 바꾸지 않습니다."
            ),
            "reason_code": (
                "operational_contract_degraded"
                if not operational_contract_healthy
                else "operational_contract_healthy"
            ),
            "learning_mode": learning.get("mode") or "judge_first",
            "local_router_ready": learning.get("mode") == "local_first",
        }
        update.error_code = None
        update.judge_model = None
        update.judge_provider = None
        update.prompt_version = None
        update.new_policy_version = None
        db.flush()
        return update

    @staticmethod
    def _operational_contract_is_healthy(profile: Any) -> bool:
        """충분한 운영 표본에서 local-first 안전 계약이 유지되는지 확인한다."""

        model_performance = getattr(profile, "model_performance", None)
        rows = (
            list(model_performance.values())
            if isinstance(model_performance, dict)
            else []
        )
        total_runs = sum(int(getattr(row, "run_count", 0) or 0) for row in rows)
        if total_runs < 20:
            return True

        success_count = sum(
            int(getattr(row, "success_count", 0) or 0) for row in rows
        )
        schema_eval_count = sum(
            int(getattr(row, "schema_eval_count", 0) or 0) for row in rows
        )
        schema_pass_count = sum(
            int(getattr(row, "schema_pass_count", 0) or 0) for row in rows
        )
        downstream_eval_count = sum(
            int(getattr(row, "downstream_eval_count", 0) or 0) for row in rows
        )
        downstream_success_count = sum(
            int(getattr(row, "downstream_success_count", 0) or 0) for row in rows
        )
        fallback_count = sum(
            int(getattr(row, "fallback_count", 0) or 0) for row in rows
        )
        success_rate = success_count / total_runs
        schema_pass_rate = (
            schema_pass_count / schema_eval_count if schema_eval_count else 1.0
        )
        downstream_success_rate = (
            downstream_success_count / downstream_eval_count
            if downstream_eval_count
            else 1.0
        )
        fallback_rate = fallback_count / total_runs
        return (
            success_rate >= 0.95
            and schema_pass_rate >= 0.95
            and downstream_success_rate >= 0.95
            and fallback_rate <= 0.05
        )

    @classmethod
    def _migrate_legacy_policy(
        cls,
        db: Session,
        *,
        policy: LLMNodeModelRoutingPolicy,
        node_data: dict[str, Any],
    ) -> dict[str, Any]:
        """구형 정책의 규칙은 실행하지 않고 Judge-first 기본값만 이관한다."""

        execution_subject_id = getattr(
            policy, "execution_subject_user_id", None
        ) or getattr(policy, "judge_user_id", None)
        if execution_subject_id is None or policy.organization_id is None:
            raise ValueError("model routing execution subject is unavailable")

        available_model_ids = list(
            LLMService.get_runtime_available_model_ids_for_user(
                db,
                user_id=execution_subject_id,
                organization_id=policy.organization_id,
            )
        )
        available_model_ids = filter_supported_model_routing_candidates(
            filter_model_routing_available_model_ids(
                available_model_ids,
                node_data=node_data,
            )
        )
        if not available_model_ids:
            raise ValueError("model routing candidate is unavailable")

        configured_model_id = str(node_data.get("model_id") or "").strip()
        active_policy = (
            policy.active_policy if isinstance(policy.active_policy, dict) else {}
        )
        current_default_model_id = str(
            active_policy.get("default_model_id") or ""
        ).strip()
        default_model_id = next(
            (
                model_id
                for model_id in (configured_model_id, current_default_model_id)
                if model_id in available_model_ids
            ),
            sorted(available_model_ids)[0],
        )
        fallback_model_id = str(
            active_policy.get("fallback_model_id")
            or node_data.get("fallback_model_id")
            or ""
        ).strip() or None
        if fallback_model_id not in available_model_ids:
            fallback_model_id = next(
                (
                    model_id
                    for model_id in available_model_ids
                    if model_id != default_model_id
                ),
                None,
            )
        return normalize_judge_first_active_policy(
            active_policy,
            policy_version="judge-first-migrated-v1",
            default_model_id=default_model_id,
            fallback_model_id=fallback_model_id,
            candidate_model_ids=available_model_ids,
        )

    @staticmethod
    def _node_data(graph: dict[str, Any], node_id: str) -> dict[str, Any] | None:
        for node in graph.get("nodes") or []:
            if isinstance(node, dict) and str(node.get("id")) == node_id:
                data = node.get("data")
                return data if isinstance(data, dict) else None
        return None

    @staticmethod
    def _remaining_event_count(
        db: Session,
        policy: LLMNodeModelRoutingPolicy,
        cutoff: datetime,
    ) -> int:
        return (
            db.query(LLMNodeModelRoutingPolicyRunEvent)
            .filter(LLMNodeModelRoutingPolicyRunEvent.policy_id == policy.id)
            .filter(LLMNodeModelRoutingPolicyRunEvent.created_at > cutoff)
            .count()
        )

    @staticmethod
    def _excluded_run_count(
        db: Session,
        policy: LLMNodeModelRoutingPolicy,
        cutoff: datetime,
        *,
        usable_run_count: int,
    ) -> int:
        total = (
            db.query(LLMNodeModelRoutingPolicyRunEvent)
            .filter(LLMNodeModelRoutingPolicyRunEvent.policy_id == policy.id)
            .filter(LLMNodeModelRoutingPolicyRunEvent.created_at <= cutoff)
            .count()
        )
        return max(0, total - usable_run_count)
