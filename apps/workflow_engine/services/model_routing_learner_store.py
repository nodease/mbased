"""배포 정책과 독립적인 모델 라우팅 학습기 저장 경계."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import case, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from apps.shared.db.models.model_routing_policy import (
    LLMNodeModelRoutingLearner,
    LLMNodeModelRoutingLearnerVersion,
    LLMNodeModelRoutingLearningLabel,
    LLMNodeModelRoutingPolicy,
    LLMNodeModelRoutingPolicyUpdate,
)
from apps.shared.db.models.workflow_run import WorkflowNodeRun, WorkflowRun
from apps.workflow_engine.services.model_routing_bootstrap import task_fingerprint
from apps.workflow_engine.services.model_routing_incremental_learning import (
    IncrementalTaskRequirementClassifier,
    TASK_REQUIREMENT_FEATURE_SCHEMA_VERSION,
    learning_mode_for,
)
from apps.workflow_engine.services.model_routing_local_classifier import (
    DEFAULT_MULTILINGUAL_E5_MODEL_ID,
    DEFAULT_MULTILINGUAL_E5_REVISION,
    E5_EMBEDDING_DIMENSION,
    E5_POOLING_STRATEGY,
    E5_QUERY_PREFIX_VERSION,
    STRUCTURED_FEATURE_VERSION,
    FixedGaussianRandomProjection,
    MultilingualE5ModelChoiceClassifier,
    MultilingualE5TaskRequirementClassifier,
)
from apps.workflow_engine.services.model_routing_operational_performance import (
    ModelRoutingOperationalPerformanceService,
)
from apps.workflow_engine.services.model_routing_runtime_judge import (
    ModelRoutingRuntimeJudge,
)


class ModelRoutingLearnerStore:
    """학습기 계보와 immutable version의 단일 persistence 경계."""

    @staticmethod
    def judge_contract_hash(
        *,
        judge_rubric_version: str,
        feature_schema_version: str,
        encoder_model_id: str,
        encoder_revision: str = "",
        pooling_strategy: str = "",
        query_prefix_version: str = "",
        projection_metadata: dict[str, Any] | None = None,
        structured_feature_version: str = "",
        ordinal_head_version: str = "",
    ) -> str:
        payload = {
            "judge_rubric_version": str(judge_rubric_version),
            "feature_schema_version": str(feature_schema_version),
            "encoder_model_id": str(encoder_model_id),
            "encoder_revision": str(encoder_revision),
            "pooling_strategy": str(pooling_strategy),
            "query_prefix_version": str(query_prefix_version),
            "projection_metadata": dict(projection_metadata or {}),
            "structured_feature_version": str(structured_feature_version),
            "ordinal_head_version": str(ordinal_head_version),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @classmethod
    def current_contract(cls) -> dict[str, Any]:
        rubric = ModelRoutingRuntimeJudge.REQUIREMENT_RUBRIC_VERSION
        schema = TASK_REQUIREMENT_FEATURE_SCHEMA_VERSION
        encoder = (
            os.getenv("MODEL_ROUTING_EMBEDDING_MODEL_ID")
            or DEFAULT_MULTILINGUAL_E5_MODEL_ID
        )
        encoder_revision = (
            os.getenv("MODEL_ROUTING_EMBEDDING_MODEL_REVISION")
            or DEFAULT_MULTILINGUAL_E5_REVISION
        )
        projection_metadata = FixedGaussianRandomProjection.metadata(
            input_dimension=E5_EMBEDDING_DIMENSION
        )
        ordinal_head_version = IncrementalTaskRequirementClassifier.ORDINAL_HEAD_VERSION
        return {
            "judge_rubric_version": rubric,
            "feature_schema_version": schema,
            "encoder_model_id": encoder,
            "encoder_revision": encoder_revision,
            "pooling_strategy": E5_POOLING_STRATEGY,
            "query_prefix_version": E5_QUERY_PREFIX_VERSION,
            "projection_metadata": projection_metadata,
            "structured_feature_version": STRUCTURED_FEATURE_VERSION,
            "ordinal_head_version": ordinal_head_version,
            "judge_contract_hash": cls.judge_contract_hash(
                judge_rubric_version=rubric,
                feature_schema_version=schema,
                encoder_model_id=encoder,
                encoder_revision=encoder_revision,
                pooling_strategy=E5_POOLING_STRATEGY,
                query_prefix_version=E5_QUERY_PREFIX_VERSION,
                projection_metadata=projection_metadata,
                structured_feature_version=STRUCTURED_FEATURE_VERSION,
                ordinal_head_version=ordinal_head_version,
            ),
        }

    @classmethod
    def get_or_create(
        cls,
        db: Session,
        *,
        organization_id: uuid.UUID,
        workflow_id: uuid.UUID,
        node_id: str,
        node_data: dict[str, Any],
        downstream_contract: dict[str, Any] | None = None,
    ) -> LLMNodeModelRoutingLearner:
        fingerprint = task_fingerprint(
            node_data,
            downstream_contract=downstream_contract,
        )
        contract = cls.current_contract()
        query = db.query(LLMNodeModelRoutingLearner).filter(
            LLMNodeModelRoutingLearner.organization_id == organization_id,
            LLMNodeModelRoutingLearner.workflow_id == workflow_id,
            LLMNodeModelRoutingLearner.node_id == str(node_id),
            LLMNodeModelRoutingLearner.task_fingerprint == fingerprint,
            LLMNodeModelRoutingLearner.judge_contract_hash
            == contract["judge_contract_hash"],
        )
        existing = query.first()
        if existing is not None:
            return existing

        # 같은 작업 지문이라도 Judge rubric/feature schema/encoder가 바뀌면
        # 기존 라벨과 가중치를 섞지 않는다. 과거 계보는 감사용으로 보존한다.
        (
            db.query(LLMNodeModelRoutingLearner)
            .filter(
                LLMNodeModelRoutingLearner.organization_id == organization_id,
                LLMNodeModelRoutingLearner.workflow_id == workflow_id,
                LLMNodeModelRoutingLearner.node_id == str(node_id),
                LLMNodeModelRoutingLearner.task_fingerprint == fingerprint,
                LLMNodeModelRoutingLearner.judge_contract_hash
                != contract["judge_contract_hash"],
                LLMNodeModelRoutingLearner.status != "stale",
            )
            .update(
                {LLMNodeModelRoutingLearner.status: "stale"},
                synchronize_session=False,
            )
        )

        learner = LLMNodeModelRoutingLearner(
            organization_id=organization_id,
            workflow_id=workflow_id,
            node_id=str(node_id),
            task_fingerprint=fingerprint,
            judge_contract_hash=contract["judge_contract_hash"],
            judge_rubric_version=contract["judge_rubric_version"],
            feature_schema_version=contract["feature_schema_version"],
            encoder_model_id=contract["encoder_model_id"],
            status="collecting",
            candidate_artifact={},
            judged_request_count=0,
            selected_model_counts={},
            evaluation_window=[],
            recent_evaluation={},
            local_confidence_threshold=Decimal("0.78"),
        )
        try:
            with db.begin_nested():
                db.add(learner)
                db.flush()
            return learner
        except IntegrityError:
            found = query.first()
            if found is None:
                raise
            return found

    @staticmethod
    def latest_version(
        db: Session,
        *,
        learner_id: uuid.UUID,
    ) -> LLMNodeModelRoutingLearnerVersion | None:
        return (
            db.query(LLMNodeModelRoutingLearnerVersion)
            .filter(LLMNodeModelRoutingLearnerVersion.learner_id == learner_id)
            .order_by(LLMNodeModelRoutingLearnerVersion.version.desc())
            .first()
        )

    @classmethod
    def runtime_snapshot(
        cls,
        db: Session,
        *,
        learner_id: uuid.UUID | None,
        version_id: uuid.UUID | None = None,
    ) -> dict[str, Any] | None:
        if learner_id is None:
            return None
        learner = (
            db.query(LLMNodeModelRoutingLearner)
            .filter(LLMNodeModelRoutingLearner.id == learner_id)
            .first()
        )
        if learner is None:
            return None
        version = None
        if version_id is not None:
            version = (
                db.query(LLMNodeModelRoutingLearnerVersion)
                .filter(LLMNodeModelRoutingLearnerVersion.learner_id == learner.id)
                .filter(LLMNodeModelRoutingLearnerVersion.id == version_id)
                .first()
            )
        return {
            "id": str(learner.id),
            "status": learner.status,
            "mode": "local_first" if version is not None else "judge_first",
            "task_fingerprint": learner.task_fingerprint,
            "judged_request_count": int(learner.judged_request_count or 0),
            "local_confidence_threshold": float(
                learner.local_confidence_threshold or Decimal("0.78")
            ),
            "active_version": int(version.version) if version is not None else None,
            "local_requirement_artifact": (
                dict(version.artifact or {}) if version is not None else None
            ),
            "recent_evaluation": dict(learner.recent_evaluation or {}),
        }

    @classmethod
    def public_summary(
        cls,
        db: Session,
        *,
        learner_id: uuid.UUID | None,
        version_id: uuid.UUID | None = None,
    ) -> dict[str, Any] | None:
        """원문·vector·분류기 가중치를 제외한 UI용 학습 현황."""

        snapshot = cls.runtime_snapshot(
            db, learner_id=learner_id, version_id=version_id
        )
        if snapshot is None:
            return None
        snapshot.pop("local_requirement_artifact", None)
        snapshot.update(cls.label_summary(db, learner_id=learner_id))
        return snapshot

    @classmethod
    def queue_runtime_judge_label(
        cls,
        db: Session,
        *,
        learner_id: str | uuid.UUID,
        source_policy_id: str | uuid.UUID | None,
        workflow_run_id: str | uuid.UUID,
        node_id: str,
        routing_feature_text: str,
        learning_feature_text: str,
        selected_model_id: str,
        candidate_model_ids: list[str],
        confidence: float,
        reason_code: str,
        task_requirements: dict[str, Any] | None,
    ) -> dict[str, Any]:
        learner_uuid = uuid.UUID(str(learner_id))
        learner = (
            db.query(LLMNodeModelRoutingLearner)
            .filter(LLMNodeModelRoutingLearner.id == learner_uuid)
            .first()
        )
        if learner is None:
            return {"learning_queued": False, "reason": "learner_not_found"}
        if learner.judge_contract_hash != cls.current_contract()["judge_contract_hash"]:
            return {"learning_queued": False, "reason": "learner_contract_stale"}
        try:
            vector, encoder_model_id = MultilingualE5ModelChoiceClassifier.vectorize(
                learning_feature_text,
                artifact=(
                    dict(learner.candidate_artifact or {})
                    if learner.candidate_artifact
                    else None
                ),
            )
        except (RuntimeError, ValueError, OSError) as exc:
            return {"learning_queued": False, "reason": type(exc).__name__}

        try:
            pre_learning = MultilingualE5TaskRequirementClassifier.predict_from_vector(
                dict(learner.candidate_artifact or {}),
                vector=vector,
            )
        except (RuntimeError, ValueError):
            pre_learning = None

        run_uuid = uuid.UUID(str(workflow_run_id))
        exists = (
            db.query(LLMNodeModelRoutingLearningLabel.id)
            .filter(LLMNodeModelRoutingLearningLabel.learner_id == learner.id)
            .filter(LLMNodeModelRoutingLearningLabel.workflow_run_id == run_uuid)
            .filter(LLMNodeModelRoutingLearningLabel.node_id == str(node_id))
            .first()
        )
        if exists is not None:
            return {"learning_queued": False, "reason": "already_queued"}

        from apps.workflow_engine.services.model_routing_decision_cache import (
            routing_feature_hash,
        )

        label = LLMNodeModelRoutingLearningLabel(
            learner_id=learner.id,
            source_policy_id=(
                uuid.UUID(str(source_policy_id)) if source_policy_id else None
            ),
            workflow_run_id=run_uuid,
            node_id=str(node_id),
            selected_model_id=str(selected_model_id),
            candidate_model_ids=[str(item) for item in candidate_model_ids],
            feature_vector=[float(item) for item in vector],
            routing_feature_hash=routing_feature_hash(routing_feature_text),
            encoder_model_id=encoder_model_id,
            confidence=Decimal(str(confidence)),
            reason_code=str(reason_code)[:128],
            task_requirements=cls.safe_task_requirements(task_requirements),
            local_prediction=(
                dict(pre_learning.requirements)
                if pre_learning is not None
                else None
            ),
            local_confidence=(
                Decimal(str(pre_learning.confidence))
                if pre_learning is not None
                else None
            ),
            local_distance_score=(
                Decimal(str(pre_learning.distance_score))
                if pre_learning is not None
                else None
            ),
            local_margin=(
                Decimal(str(pre_learning.margin))
                if pre_learning is not None
                else None
            ),
        )
        try:
            # 동시 실행의 중복 여부는 learner 행 잠금 대신 DB unique constraint로
            # 결정한다. E5 추론 동안 같은 학습기의 다른 요청을 막지 않는다.
            with db.begin_nested():
                db.add(label)
                db.flush()
        except IntegrityError:
            return {"learning_queued": False, "reason": "already_queued"}
        return {
            "learning_queued": True,
            "learner_id": str(learner.id),
            "learning_mode": "local_first"
            if cls.latest_version(db, learner_id=learner.id) is not None
            else "judge_first",
        }

    @classmethod
    def finalize_labels(
        cls,
        db: Session,
        *,
        learner_id: uuid.UUID,
        workflow_run: WorkflowRun,
        node_run: WorkflowNodeRun,
    ) -> int:
        labels = (
            db.query(LLMNodeModelRoutingLearningLabel)
            .filter(LLMNodeModelRoutingLearningLabel.learner_id == learner_id)
            .filter(LLMNodeModelRoutingLearningLabel.workflow_run_id == workflow_run.id)
            .filter(LLMNodeModelRoutingLearningLabel.node_id == node_run.node_id)
            .filter(LLMNodeModelRoutingLearningLabel.status == "pending")
            .all()
        )
        contract_passed, reason = (
            ModelRoutingOperationalPerformanceService.learning_contract_outcome(
                workflow_run=workflow_run,
                node_run=node_run,
            )
        )
        execution_succeeded, schema_status, downstream_status, fallback_used = (
            cls._outcome_fields(workflow_run=workflow_run, node_run=node_run)
        )
        accepted = 0
        for label in labels:
            if cls._finalize_label(
                label=label,
                contract_passed=contract_passed,
                reason=reason,
                execution_succeeded=execution_succeeded,
                schema_status=schema_status,
                downstream_status=downstream_status,
                fallback_used=fallback_used,
            ):
                accepted += 1
        if labels:
            cls._write_learning_outcome(
                node_run=node_run,
                status="accepted" if accepted else "rejected",
                reason=reason,
            )
        return accepted

    @classmethod
    def _finalize_label(
        cls,
        *,
        label: LLMNodeModelRoutingLearningLabel,
        contract_passed: bool,
        reason: str,
        execution_succeeded: bool,
        schema_status: str,
        downstream_status: str,
        fallback_used: bool,
    ) -> bool:
        """라벨 결과만 확정하고 실제 모델 학습은 Celery batch에 맡긴다."""

        requirements = cls.safe_task_requirements(label.task_requirements)
        label.execution_succeeded = execution_succeeded
        label.schema_status = schema_status
        label.downstream_status = downstream_status
        label.fallback_used = fallback_used
        label.outcome_reason = reason
        label.finalized_at = datetime.now(timezone.utc)
        if contract_passed and requirements is not None:
            label.status = "accepted"
            return True
        label.status = "rejected"
        if requirements is None:
            label.outcome_reason = "task_requirements_missing"
        return False

    @staticmethod
    def _write_learning_outcome(
        *, node_run: WorkflowNodeRun, status: str, reason: str
    ) -> None:
        """원문이나 vector 없이 학습 포함 결과 코드만 node trace에 보강한다."""

        trace = dict(node_run.trace_metadata or {})
        llm = dict(trace.get("llm") or {})
        llm["learning_status"] = status
        llm["learning_outcome_reason"] = reason
        trace["llm"] = llm
        node_run.trace_metadata = trace

        outputs = dict(node_run.outputs or {})
        metadata = dict(outputs.get("metadata") or {})
        routing = dict(metadata.get("model_routing") or {})
        if routing:
            routing["learning_status"] = status
            routing["learning_outcome_reason"] = reason
            metadata["model_routing"] = routing
            outputs["metadata"] = metadata
            node_run.outputs = outputs

    @staticmethod
    def _outcome_fields(
        *, workflow_run: WorkflowRun, node_run: WorkflowNodeRun
    ) -> tuple[bool, str, str, bool]:
        """실행·schema·downstream·fallback을 서로 독립된 안전 신호로 만든다."""

        service = ModelRoutingOperationalPerformanceService
        workflow_succeeded = (
            service._status(getattr(workflow_run, "status", None)) == "success"
        )
        node_succeeded = service._status(getattr(node_run, "status", None)) == "success"
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
        fallback_used = bool(llm.get("fallback_used") or routing.get("fallback_used"))

        raw_schema = llm.get("schema_status") or trace.get("schema_status")
        schema_evaluated, schema_passed = service._schema_evaluation(raw_schema)
        schema_status = (
            "passed" if schema_evaluated and schema_passed
            else "failed" if schema_evaluated
            else "not_applicable"
        )

        raw_downstream = llm.get("downstream_status") or trace.get("downstream_status")
        downstream_status = (
            "passed"
            if (
                service._passed(raw_downstream)
                if raw_downstream is not None
                else workflow_succeeded
            )
            else "failed"
        )
        return (
            node_succeeded and workflow_succeeded,
            schema_status,
            downstream_status,
            fallback_used,
        )

    @classmethod
    def label_summary(
        cls, db: Session, *, learner_id: str | uuid.UUID
    ) -> dict[str, Any]:
        learner_uuid = uuid.UUID(str(learner_id))
        counts = (
            db.query(
                func.sum(
                    case(
                        (LLMNodeModelRoutingLearningLabel.status == "pending", 1),
                        else_=0,
                    )
                ),
                func.sum(
                    case(
                        (LLMNodeModelRoutingLearningLabel.status == "accepted", 1),
                        else_=0,
                    )
                ),
                func.sum(
                    case(
                        (LLMNodeModelRoutingLearningLabel.status == "rejected", 1),
                        else_=0,
                    )
                ),
            )
            .filter(LLMNodeModelRoutingLearningLabel.learner_id == learner_uuid)
            .first()
        )
        pending, accepted, rejected = counts or (0, 0, 0)
        return {
            "pending_count": int(pending or 0),
            "accepted_count": int(accepted or 0),
            "rejected_count": int(rejected or 0),
        }

    @classmethod
    def publish_if_qualified(
        cls,
        db: Session,
        *,
        learner: LLMNodeModelRoutingLearner,
    ) -> LLMNodeModelRoutingLearnerVersion | None:
        evaluation = dict(learner.recent_evaluation or {})
        outcome_rates = cls._outcome_rates(db, learner_id=learner.id)
        candidate = dict(learner.candidate_artifact or {})
        if not candidate:
            learner.status = "collecting"
            return None
        mode = learning_mode_for(
            judged_request_count=int(learner.judged_request_count or 0),
            success_rate=outcome_rates["success_rate"],
            schema_pass_rate=outcome_rates["schema_pass_rate"],
            downstream_success_rate=outcome_rates["downstream_success_rate"],
            fallback_rate=outcome_rates["fallback_rate"],
            recent_judge_match_rate=evaluation.get("judge_match_rate"),
            recent_axis_accuracies=evaluation.get("axis_accuracies"),
            recent_axis_mean_errors=evaluation.get("axis_mean_errors"),
            recent_judge_label_diversity=evaluation.get("judge_label_diversity"),
            recent_local_prediction_diversity=evaluation.get(
                "local_prediction_diversity"
            ),
            recent_contract_pass_rate=evaluation.get("contract_pass_rate"),
            recent_evaluation_sample_count=evaluation.get("sample_count"),
            high_risk_underestimation_count=evaluation.get(
                "high_risk_underestimation_count"
            ),
        )
        current = cls.latest_version(db, learner_id=learner.id)
        if mode != "local_first":
            learner.status = "degraded" if current is not None else "collecting"
            contract_pass_rate = float(
                evaluation.get("contract_pass_rate") or 0
            )
            if current is not None and contract_pass_rate < 0.95:
                db.query(LLMNodeModelRoutingPolicy).filter(
                    LLMNodeModelRoutingPolicy.learner_id == learner.id
                ).update(
                    {LLMNodeModelRoutingPolicy.active_learner_version_id: None},
                    synchronize_session=False,
                )
            return None

        artifact_hash = hashlib.sha256(
            json.dumps(candidate, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        if current is not None and current.artifact_hash == artifact_hash:
            learner.status = "ready"
            cls._activate_version(
                db,
                learner=learner,
                version=current,
                trigger="learner_version_reactivated",
            )
            return current

        matching = (
            db.query(LLMNodeModelRoutingLearnerVersion)
            .filter(LLMNodeModelRoutingLearnerVersion.learner_id == learner.id)
            .filter(LLMNodeModelRoutingLearnerVersion.artifact_hash == artifact_hash)
            .first()
        )
        if matching is not None:
            learner.status = "ready"
            cls._activate_version(
                db,
                learner=learner,
                version=matching,
                trigger="learner_version_reactivated",
            )
            return matching

        version = LLMNodeModelRoutingLearnerVersion(
            learner_id=learner.id,
            version=(int(current.version) + 1 if current is not None else 1),
            artifact=candidate,
            artifact_hash=artifact_hash,
            sample_count=int(learner.judged_request_count or 0),
            evaluation_summary={
                **evaluation,
                "operational_contract": outcome_rates,
            },
            publish_reason="quality_gate_passed",
        )
        db.add(version)
        db.flush()
        learner.status = "ready"
        cls._activate_version(
            db,
            learner=learner,
            version=version,
            trigger="learner_version_published",
        )
        return version

    @staticmethod
    def _activate_version(
        db: Session,
        *,
        learner: LLMNodeModelRoutingLearner,
        version: LLMNodeModelRoutingLearnerVersion,
        trigger: str,
    ) -> None:
        """검증 버전을 연결하되 이미 같은 연결에는 감사 이벤트를 중복 생성하지 않는다."""

        policies = (
            db.query(LLMNodeModelRoutingPolicy)
            .filter(LLMNodeModelRoutingPolicy.learner_id == learner.id)
            .filter(LLMNodeModelRoutingPolicy.enabled.is_(True))
            .all()
        )
        for policy in policies:
            if policy.active_learner_version_id == version.id:
                continue
            policy.active_learner_version_id = version.id
            policy.policy_version = f"routing-policy-learner-v{version.version}"
            db.add(
                LLMNodeModelRoutingPolicyUpdate(
                    policy_id=policy.id,
                    trigger=trigger,
                    status="applied",
                    input_summary={"learner_id": str(learner.id)},
                    output_summary={"learner_version": version.version},
                    new_policy_version=policy.policy_version,
                )
            )

    @classmethod
    def _outcome_rates(
        cls, db: Session, *, learner_id: uuid.UUID
    ) -> dict[str, float | None]:
        """최근 확정 라벨에서 버전 발행용 실제 계약 지표를 계산한다."""

        labels = (
            db.query(LLMNodeModelRoutingLearningLabel)
            .filter(LLMNodeModelRoutingLearningLabel.learner_id == learner_id)
            .filter(
                LLMNodeModelRoutingLearningLabel.status.in_(
                    ("accepted", "rejected")
                )
            )
            .order_by(LLMNodeModelRoutingLearningLabel.finalized_at.desc())
            .limit(100)
            .all()
        )

        def ratio(values: list[bool]) -> float | None:
            return sum(values) / len(values) if values else None

        schema_values = [
            label.schema_status == "passed"
            for label in labels
            if label.schema_status in {"passed", "failed"}
        ]
        downstream_values = [
            label.downstream_status == "passed"
            for label in labels
            if label.downstream_status in {"passed", "failed"}
        ]
        schema_pass_rate = ratio(schema_values)
        return {
            "success_rate": ratio(
                [bool(label.execution_succeeded) for label in labels]
            ),
            # Schema 계약이 없는 노드는 실패가 아니라 검사 비대상이다.
            "schema_pass_rate": (
                schema_pass_rate if schema_pass_rate is not None else 1.0
            ),
            "downstream_success_rate": ratio(downstream_values),
            "fallback_rate": ratio([bool(label.fallback_used) for label in labels]),
        }

    @staticmethod
    def safe_task_requirements(value: Any) -> dict[str, int] | None:
        if not isinstance(value, dict):
            return None
        result: dict[str, int] = {}
        for key in ("task_complexity", "decision_impact", "evidence_synthesis"):
            raw = value.get(key)
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                return None
            if isinstance(raw, float) and not raw.is_integer():
                return None
            result[key] = max(0, min(3, int(raw)))
        return result
