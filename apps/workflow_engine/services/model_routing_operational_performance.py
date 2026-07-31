"""배포 실행이 끝날 때 모델 라우팅 운영 성적을 누적한다."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
import uuid

from sqlalchemy.orm import Session

from apps.shared.db.models.model_routing_policy import (
    LLMNodeModelRoutingPerformance,
)
from apps.workflow_engine.services.model_router import ModelPerformance, NodeRunProfile


@dataclass(frozen=True)
class ModelRoutingPerformanceSample:
    model_id: str
    input_profile: str
    run_count: int = 1
    success_count: int = 0
    schema_pass_count: int = 0
    schema_eval_count: int = 0
    downstream_success_count: int = 0
    downstream_eval_count: int = 0
    fallback_count: int = 0
    retry_count: int = 0
    total_cost: float = 0.0
    total_tokens: int = 0
    total_latency_ms: int = 0


class ModelRoutingOperationalPerformanceService:
    """원본 실행 로그를 정책 평가에 쓰는 작은 누계로 변환한다."""

    MIN_NEW_RUNS = 3
    QUALITY_CHANGE_THRESHOLD = 0.05
    EFFICIENCY_IMPROVEMENT_THRESHOLD = 0.10
    _SCHEMA_NOT_REQUIRED_STATUSES = {
        "not_applicable",
        "not_required",
    }

    @classmethod
    def learning_contract_outcome(
        cls,
        *,
        workflow_run: Any,
        node_run: Any,
    ) -> tuple[bool, str]:
        """완료된 운영 실행이 local router 학습 label로 안전한지 판정한다."""
        if cls._status(getattr(node_run, "status", None)) != "success":
            return False, "node_failed"
        if cls._status(getattr(workflow_run, "status", None)) != "success":
            return False, "downstream_failed"

        trace = (
            node_run.trace_metadata
            if isinstance(getattr(node_run, "trace_metadata", None), dict)
            else {}
        )
        llm = trace.get("llm") if isinstance(trace.get("llm"), dict) else {}
        outputs = (
            node_run.outputs
            if isinstance(getattr(node_run, "outputs", None), dict)
            else {}
        )
        metadata = (
            outputs.get("metadata")
            if isinstance(outputs.get("metadata"), dict)
            else {}
        )
        routing = (
            metadata.get("model_routing")
            if isinstance(metadata.get("model_routing"), dict)
            else {}
        )
        if bool(llm.get("fallback_used") or routing.get("fallback_used")):
            return False, "fallback_used"

        schema_status = llm.get("schema_status") or trace.get("schema_status")
        schema_evaluated, schema_passed = cls._schema_evaluation(schema_status)
        if schema_evaluated and not schema_passed:
            return False, "schema_failed"
        downstream_status = llm.get("downstream_status") or trace.get(
            "downstream_status"
        )
        if downstream_status is not None and not cls._passed(downstream_status):
            return False, "downstream_failed"
        return True, "contract_passed"

    @classmethod
    def sample_from_run(
        cls,
        *,
        workflow_run: Any,
        node_run: Any,
        usage_log: Any | None,
        usage_model_id: str | None,
    ) -> ModelRoutingPerformanceSample:
        trace = (
            node_run.trace_metadata
            if isinstance(getattr(node_run, "trace_metadata", None), dict)
            else {}
        )
        llm = trace.get("llm") if isinstance(trace.get("llm"), dict) else {}
        outputs = (
            node_run.outputs
            if isinstance(getattr(node_run, "outputs", None), dict)
            else {}
        )
        output_metadata = (
            outputs.get("metadata") if isinstance(outputs.get("metadata"), dict) else {}
        )
        output_routing = (
            output_metadata.get("model_routing")
            if isinstance(output_metadata.get("model_routing"), dict)
            else {}
        )
        runtime_context = (
            llm.get("runtime_context")
            if isinstance(llm.get("runtime_context"), dict)
            else {}
        )
        model_id = (
            str(
                usage_model_id
                or llm.get("selected_model")
                or output_routing.get("selected_model")
                or outputs.get("model")
                or ""
            )
            .strip()
            .lower()
            .removeprefix("models/")
        )
        if not model_id:
            raise ValueError("model routing performance model is unavailable")

        node_status = cls._status(getattr(node_run, "status", None))
        workflow_status = cls._status(getattr(workflow_run, "status", None))
        usage_status = cls._status(getattr(usage_log, "status", None))
        success = node_status == "success" and usage_status in {"", "success"}
        schema_status = llm.get("schema_status") or trace.get("schema_status")
        downstream_status = llm.get("downstream_status") or trace.get(
            "downstream_status"
        )
        schema_evaluated, schema_passed = cls._schema_evaluation(schema_status)
        downstream_evaluated = downstream_status is not None or workflow_status in {
            "success",
            "failed",
        }
        downstream_passed = cls._passed(downstream_status)
        if downstream_status is None:
            downstream_passed = workflow_status == "success"

        return ModelRoutingPerformanceSample(
            model_id=model_id,
            input_profile=str(runtime_context.get("input_length_bucket") or "unknown"),
            success_count=int(success),
            schema_pass_count=int(schema_evaluated and schema_passed),
            schema_eval_count=int(schema_evaluated),
            downstream_success_count=int(downstream_evaluated and downstream_passed),
            downstream_eval_count=int(downstream_evaluated),
            fallback_count=int(
                bool(llm.get("fallback_used") or output_routing.get("fallback_used"))
            ),
            retry_count=int(getattr(node_run, "retry_count", 0) or 0),
            total_cost=float(getattr(usage_log, "total_cost", 0) or 0),
            total_tokens=int(getattr(usage_log, "prompt_tokens", 0) or 0)
            + int(getattr(usage_log, "completion_tokens", 0) or 0),
            total_latency_ms=int(getattr(usage_log, "latency_ms", 0) or 0),
        )

    @classmethod
    def _schema_evaluation(cls, value: Any) -> tuple[bool, bool]:
        if value is None:
            return False, False
        normalized = cls._status(value)
        if normalized in cls._SCHEMA_NOT_REQUIRED_STATUSES:
            return False, False
        return True, cls._passed(value)

    @classmethod
    def record_sample(
        cls,
        db: Session,
        *,
        policy_id: uuid.UUID,
        sample: ModelRoutingPerformanceSample,
    ) -> LLMNodeModelRoutingPerformance:
        row = (
            db.query(LLMNodeModelRoutingPerformance)
            .filter(LLMNodeModelRoutingPerformance.policy_id == policy_id)
            .filter(LLMNodeModelRoutingPerformance.model_id == sample.model_id)
            .filter(
                LLMNodeModelRoutingPerformance.input_profile == sample.input_profile
            )
            .first()
        )
        if row is None:
            row = LLMNodeModelRoutingPerformance(
                policy_id=policy_id,
                model_id=sample.model_id,
                input_profile=sample.input_profile,
            )
            db.add(row)
        for field in (
            "run_count",
            "success_count",
            "schema_pass_count",
            "schema_eval_count",
            "downstream_success_count",
            "downstream_eval_count",
            "fallback_count",
            "retry_count",
            "total_tokens",
            "total_latency_ms",
        ):
            setattr(
                row,
                field,
                int(getattr(row, field, 0) or 0) + int(getattr(sample, field)),
            )
        row.total_cost = Decimal(str(row.total_cost or 0)) + Decimal(
            str(sample.total_cost)
        )
        db.flush()
        return row

    @classmethod
    def checkpoint_snapshot(
        cls, db: Session, *, policy_id: uuid.UUID
    ) -> dict[str, Any]:
        rows = cls._rows(db, policy_id=policy_id)
        models = {
            f"{row.model_id}:{row.input_profile}": cls._row_summary(row) for row in rows
        }
        return {
            "total_runs": sum(row.run_count for row in rows),
            "models": models,
        }

    @classmethod
    def response_summary(cls, db: Session, *, policy_id: uuid.UUID) -> dict[str, Any]:
        rows = cls._rows(db, policy_id=policy_id)
        last_updated = max(
            (row.updated_at for row in rows if row.updated_at is not None),
            default=None,
        )
        return {
            "total_runs": sum(row.run_count for row in rows),
            "model_count": len({row.model_id for row in rows}),
            "last_recorded_at": last_updated.isoformat() if last_updated else None,
            "models": [
                {
                    "model_id": row.model_id,
                    "input_profile": row.input_profile,
                    **cls._row_summary(row),
                }
                for row in rows
            ],
        }

    @classmethod
    def candidate_contract_evidence(
        cls,
        db: Session,
        *,
        policy_id: uuid.UUID | str,
        candidate_model_ids: list[str],
        input_profile: str | None = None,
        minimum_profile_run_count: int | None = None,
    ) -> dict[str, dict[str, float | int | None]]:
        """후보별 실제 계약 성적을 Judge 입력용 안전 요약으로 만든다.

        이 값은 답변의 문장 품질을 평가한 결과가 아니다. schema, 후속 노드, fallback
        같은 workflow 계약 신호만 합산한다. 따라서 카탈로그의 사전 품질값을 대체하지
        않고, 충분한 운영 표본이 쌓인 후보에만 보정 근거로 사용한다.
        """

        candidate_ids = {str(model_id or "").strip().lower() for model_id in candidate_model_ids}
        totals: dict[str, dict[str, int]] = {}
        profile_totals: dict[str, dict[str, int]] = {}
        normalized_input_profile = str(input_profile or "").strip().lower()
        for row in cls._rows(db, policy_id=policy_id):
            model_id = str(getattr(row, "model_id", "") or "").strip().lower()
            if not model_id or model_id not in candidate_ids:
                continue
            target = cls._empty_contract_totals(totals, model_id)
            cls._add_contract_row(target, row)
            if (
                normalized_input_profile
                and str(getattr(row, "input_profile", "") or "").strip().lower()
                == normalized_input_profile
            ):
                profile_target = cls._empty_contract_totals(profile_totals, model_id)
                cls._add_contract_row(profile_target, row)

        if not normalized_input_profile:
            selected_totals = totals
        elif minimum_profile_run_count is None:
            selected_totals = profile_totals
        else:
            minimum_run_count = max(1, int(minimum_profile_run_count))
            selected_totals = {
                model_id: (
                    profile_totals[model_id]
                    if profile_totals.get(model_id, {}).get("run_count", 0)
                    >= minimum_run_count
                    else total
                )
                for model_id, total in totals.items()
            }

        evidence: dict[str, dict[str, float | int | None]] = {}
        for model_id, total in selected_totals.items():
            run_count = total["run_count"]
            if run_count <= 0:
                continue
            evidence[model_id] = {
                "operational_run_count": run_count,
                "operational_success_rate": cls._ratio(total["success_count"], run_count) or 0.0,
                "operational_schema_pass_rate": cls._ratio(
                    total["schema_pass_count"], total["schema_eval_count"]
                )
                if total["schema_eval_count"]
                else None,
                "operational_downstream_success_rate": cls._ratio(
                    total["downstream_success_count"], total["downstream_eval_count"]
                )
                if total["downstream_eval_count"]
                else None,
                "operational_fallback_rate": cls._ratio(total["fallback_count"], run_count) or 0.0,
            }
        return evidence

    @staticmethod
    def _empty_contract_totals(
        totals: dict[str, dict[str, int]], model_id: str
    ) -> dict[str, int]:
        return totals.setdefault(
            model_id,
            {
                "run_count": 0,
                "success_count": 0,
                "schema_pass_count": 0,
                "schema_eval_count": 0,
                "downstream_success_count": 0,
                "downstream_eval_count": 0,
                "fallback_count": 0,
            },
        )

    @staticmethod
    def _add_contract_row(target: dict[str, int], row: Any) -> None:
        for key in target:
            target[key] += int(getattr(row, key, 0) or 0)

    @classmethod
    def profile_for_policy(cls, db: Session, *, policy_id: uuid.UUID) -> NodeRunProfile:
        rows = cls._rows(db, policy_id=policy_id)
        by_model: dict[str, ModelPerformance] = {}
        by_input_profile: dict[str, dict[str, Any]] = {}
        for row in rows:
            target = by_model.setdefault(
                row.model_id, ModelPerformance(model_id=row.model_id)
            )
            segment = by_input_profile.setdefault(
                row.input_profile,
                {
                    "conditions": {"input_length_bucket": row.input_profile},
                    "model_performance": {},
                },
            )
            segment_target = segment["model_performance"].setdefault(
                row.model_id,
                ModelPerformance(model_id=row.model_id),
            )
            for field in (
                "run_count",
                "success_count",
                "schema_pass_count",
                "schema_eval_count",
                "downstream_success_count",
                "downstream_eval_count",
                "fallback_count",
                "retry_count",
                "total_tokens",
                "total_latency_ms",
            ):
                value = int(getattr(row, field) or 0)
                setattr(target, field, getattr(target, field) + value)
                setattr(
                    segment_target,
                    field,
                    getattr(segment_target, field) + value,
                )
            cost = float(row.total_cost or 0)
            target.total_cost += cost
            segment_target.total_cost += cost
        return NodeRunProfile(
            operational_usable_runs=sum(item.run_count for item in by_model.values()),
            model_performance=by_model,
            segment_performance=by_input_profile,
        )

    @classmethod
    def has_material_change(
        cls,
        checkpoint: dict[str, Any] | None,
        current: dict[str, Any] | None,
    ) -> bool:
        checkpoint = checkpoint or {}
        current = current or {}
        old_total = int(checkpoint.get("total_runs") or 0)
        new_total = int(current.get("total_runs") or 0)
        if new_total - old_total < cls.MIN_NEW_RUNS:
            return False
        old_models = (
            checkpoint.get("models")
            if isinstance(checkpoint.get("models"), dict)
            else {}
        )
        new_models = (
            current.get("models") if isinstance(current.get("models"), dict) else {}
        )
        if not old_models:
            return True
        for key, new_row in new_models.items():
            if not isinstance(new_row, dict):
                continue
            old_row = old_models.get(key)
            if not isinstance(old_row, dict):
                if int(new_row.get("run_count") or 0) >= cls.MIN_NEW_RUNS:
                    return True
                continue
            if (
                abs(
                    float(new_row.get("quality_score") or 0)
                    - float(old_row.get("quality_score") or 0)
                )
                >= cls.QUALITY_CHANGE_THRESHOLD
            ):
                return True
            for metric in ("avg_cost", "avg_latency_ms"):
                old_value = float(old_row.get(metric) or 0)
                new_value = float(new_row.get(metric) or 0)
                if (
                    old_value > 0
                    and abs(new_value - old_value) / old_value
                    >= cls.EFFICIENCY_IMPROVEMENT_THRESHOLD
                ):
                    return True
        return False

    @staticmethod
    def _rows(
        db: Session, *, policy_id: uuid.UUID
    ) -> list[LLMNodeModelRoutingPerformance]:
        return (
            db.query(LLMNodeModelRoutingPerformance)
            .filter(LLMNodeModelRoutingPerformance.policy_id == policy_id)
            .order_by(
                LLMNodeModelRoutingPerformance.model_id.asc(),
                LLMNodeModelRoutingPerformance.input_profile.asc(),
            )
            .all()
        )

    @classmethod
    def _row_summary(cls, row: Any) -> dict[str, Any]:
        run_count = int(row.run_count or 0)
        success_rate = cls._ratio(row.success_count, run_count)
        schema_rate = cls._ratio(row.schema_pass_count, row.schema_eval_count)
        downstream_rate = cls._ratio(
            row.downstream_success_count, row.downstream_eval_count
        )
        quality_parts = [
            item
            for item in (success_rate, schema_rate, downstream_rate)
            if item is not None
        ]
        return {
            "run_count": run_count,
            "success_rate": success_rate,
            "schema_pass_rate": schema_rate,
            "downstream_success_rate": downstream_rate,
            "fallback_rate": cls._ratio(row.fallback_count, run_count),
            "quality_score": min(quality_parts) if quality_parts else 0.0,
            "avg_cost": float(row.total_cost or 0) / run_count if run_count else None,
            "avg_latency_ms": int(row.total_latency_ms or 0) / run_count
            if run_count
            else None,
            "avg_total_tokens": int(row.total_tokens or 0) / run_count
            if run_count
            else None,
        }

    @staticmethod
    def _ratio(numerator: Any, denominator: Any) -> float | None:
        denominator = int(denominator or 0)
        return int(numerator or 0) / denominator if denominator else None

    @staticmethod
    def _status(value: Any) -> str:
        return str(getattr(value, "value", value) or "").lower()

    @classmethod
    def _passed(cls, value: Any) -> bool:
        return value is True or cls._status(value) in {
            "passed",
            "pass",
            "valid",
            "compatible",
            "success",
        }
