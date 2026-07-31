"""Judge label을 실행 경로 밖에서 순서대로 평가하고 학습한다."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from apps.workflow_engine.services.model_routing_incremental_learning import (
    IncrementalTaskRequirementClassifier,
    TASK_REQUIREMENT_FEATURE_SCHEMA_VERSION,
)
from apps.workflow_engine.services.model_routing_local_classifier import (
    MultilingualE5TaskRequirementClassifier,
)
from apps.workflow_engine.services.model_routing_decision_cache import (
    remember_accepted_decision,
)


EVALUATION_WINDOW_SIZE = 50
INITIAL_TRAINING_LABELS = 50
INITIAL_VALIDATION_END_LABEL = 100
ONGOING_VALIDATION_SAMPLE_INTERVAL = 5
DEFAULT_LEARNING_BATCH_SIZE = 10
MAX_BATCH_WAIT_SECONDS = 300


@dataclass(frozen=True)
class ModelRoutingLearningBatchResult:
    learner_state: dict[str, Any]
    processed_count: int
    remaining_count: int
    deferred_seconds: int | None = None


class ModelRoutingLearningBatchService:
    """학습 전 예측과 Judge 정답을 비교한 뒤 candidate artifact를 갱신한다."""

    @classmethod
    def train_labels(
        cls,
        *,
        learner_state: dict[str, Any],
        labels: Iterable[Any],
        batch_size: int = DEFAULT_LEARNING_BATCH_SIZE,
        processed_at: datetime | None = None,
    ) -> ModelRoutingLearningBatchResult:
        learning = deepcopy(learner_state)
        pending = [
            label
            for label in labels
            if getattr(label, "learning_processed_at", None) is None
            and str(getattr(label, "status", "")) in {"accepted", "rejected"}
        ]
        batch = pending[: max(1, int(batch_size))]
        stored_artifact = deepcopy(
            learning.get("candidate_requirement_artifact")
            or learning.get("local_requirement_artifact")
            or {}
        )
        validation_window = list(
            learning.get("validation_window")
            or learning.get("evaluation_window")
            or []
        )
        judged_request_count = int(learning.get("judged_request_count") or 0)
        selected_model_ids = set(learning.get("selected_model_ids") or [])
        selected_model_counts = dict(learning.get("selected_model_counts") or {})
        if (
            stored_artifact.get("feature_schema_version")
            != TASK_REQUIREMENT_FEATURE_SCHEMA_VERSION
        ):
            artifact: dict[str, Any] = {}
            validation_window = []
            judged_request_count = 0
            selected_model_ids = set()
            selected_model_counts = {}
        else:
            artifact = stored_artifact
        timestamp = processed_at or datetime.now(timezone.utc)

        valid_rows: list[tuple[Any, dict[str, int]]] = []
        for label in batch:
            try:
                valid_rows.append(
                    (
                        label,
                        cls._requirements(getattr(label, "task_requirements", None)),
                    )
                )
            except (KeyError, TypeError, ValueError):
                label.learning_processed_at = timestamp

        first_training_batch = int(artifact.get("trained_example_count") or 0) <= 0
        if first_training_batch and valid_rows:
            first_vector = list(getattr(valid_rows[0][0], "feature_vector", None) or [])
            if first_vector:
                artifact = IncrementalTaskRequirementClassifier.initialize_from_requirements(
                    task_requirements=[truth for _label, truth in valid_rows],
                    dimensions=len(first_vector),
                )

        for label, truth in valid_rows:
            contract_passed = str(getattr(label, "status", "")) == "accepted"
            judged_request_count += 1
            encoder_model_id = str(getattr(label, "encoder_model_id", "") or "").strip()
            artifact_encoder = str(artifact.get("encoder_model_id") or "").strip()
            if artifact_encoder and encoder_model_id != artifact_encoder:
                label.learning_processed_at = timestamp
                continue
            is_validation_sample = (
                not first_training_batch
                and (
                    INITIAL_TRAINING_LABELS
                    < judged_request_count
                    <= INITIAL_VALIDATION_END_LABEL
                    or (
                        judged_request_count > INITIAL_VALIDATION_END_LABEL
                        and judged_request_count
                        % ONGOING_VALIDATION_SAMPLE_INTERVAL
                        == 0
                    )
                )
            )
            prediction = None
            if not first_training_batch:
                prediction = MultilingualE5TaskRequirementClassifier.predict_from_vector(
                    artifact,
                    vector=getattr(label, "feature_vector", None) or [],
                )
                if prediction is not None and getattr(label, "local_prediction", None) is None:
                    label.local_prediction = dict(prediction.requirements)
                    label.local_confidence = prediction.confidence
                    label.local_distance_score = prediction.distance_score
                    label.local_margin = prediction.margin
            else:
                # 구형 0-weight artifact가 queue 시점에 만든 (2,2,2)를 통계에서 제거한다.
                label.local_prediction = None
                label.local_confidence = None
                label.local_distance_score = None
                label.local_margin = None

            if is_validation_sample and prediction is not None:
                validation_window.append(
                    cls._evaluation_entry(
                        prediction=dict(prediction.requirements),
                        truth=truth,
                        contract_passed=contract_passed,
                    )
                )

            # 정확도에는 학습 전에 만든 예측만 기록하고, 이후 같은 정답을 정확히
            # 한 번 학습한다. 검증 표본을 버리지 않으면서도 미래 정보 누출을 막는다.
            artifact = MultilingualE5TaskRequirementClassifier.update_from_vector(
                artifact,
                vector=getattr(label, "feature_vector", None) or [],
                encoder_model_id=encoder_model_id,
                task_requirements=truth,
            )
            artifact["feature_schema_version"] = (
                TASK_REQUIREMENT_FEATURE_SCHEMA_VERSION
            )

            if contract_passed:
                selected_model_id = str(
                    getattr(label, "selected_model_id", "") or ""
                ).strip()
                if selected_model_id:
                    selected_model_ids.add(selected_model_id)
                    selected_model_counts[selected_model_id] = (
                        int(selected_model_counts.get(selected_model_id) or 0) + 1
                    )
                    remember_accepted_decision(
                        learning,
                        feature_hash=getattr(label, "routing_feature_hash", None),
                        selected_model_id=selected_model_id,
                        confidence=getattr(label, "confidence", None),
                        reason_code=getattr(label, "reason_code", None),
                    )
            label.learning_processed_at = timestamp

        validation_window = validation_window[-EVALUATION_WINDOW_SIZE:]
        evaluation = cls._evaluation_summary(validation_window)
        artifact["recent_judge_match_rate"] = evaluation["judge_match_rate"]
        artifact["recent_contract_pass_rate"] = evaluation["contract_pass_rate"]
        learning["candidate_requirement_artifact"] = artifact
        learning["judged_request_count"] = judged_request_count
        learning["selected_model_ids"] = sorted(selected_model_ids)
        learning["selected_model_counts"] = selected_model_counts
        # DB column 이름은 호환성을 위해 유지하되 state 의미는 validation-only다.
        learning["validation_window"] = validation_window
        learning["evaluation_window"] = validation_window
        learning["recent_evaluation"] = evaluation
        return ModelRoutingLearningBatchResult(
            learner_state=learning,
            processed_count=len(batch),
            remaining_count=max(0, len(pending) - len(batch)),
        )

    @classmethod
    def train_pending(
        cls,
        db,
        *,
        learner_id: str,
        force: bool = False,
        now: datetime | None = None,
    ) -> ModelRoutingLearningBatchResult:
        """학습기별 대기 label을 잠근 뒤 10건 또는 최대 5분 단위로 학습한다."""

        from apps.shared.db.models.model_routing_policy import (
            LLMNodeModelRoutingLearningLabel,
            LLMNodeModelRoutingLearner,
        )
        from apps.workflow_engine.services.model_routing_learner_store import (
            ModelRoutingLearnerStore,
        )

        timestamp = now or datetime.now(timezone.utc)
        learner = (
            db.query(LLMNodeModelRoutingLearner)
            .filter(LLMNodeModelRoutingLearner.id == learner_id)
            .populate_existing()
            .with_for_update()
            .first()
        )
        if learner is None:
            return ModelRoutingLearningBatchResult({}, 0, 0)

        labels = (
            db.query(LLMNodeModelRoutingLearningLabel)
            .filter(LLMNodeModelRoutingLearningLabel.learner_id == learner.id)
            .filter(
                LLMNodeModelRoutingLearningLabel.status.in_(
                    ("accepted", "rejected")
                )
            )
            .filter(LLMNodeModelRoutingLearningLabel.learning_processed_at.is_(None))
            .order_by(LLMNodeModelRoutingLearningLabel.created_at.asc())
            .all()
        )
        if not labels:
            return ModelRoutingLearningBatchResult(cls._state(learner), 0, 0)

        if len(labels) < DEFAULT_LEARNING_BATCH_SIZE and not force:
            oldest = getattr(labels[0], "finalized_at", None) or labels[0].created_at
            if oldest.tzinfo is None:
                oldest = oldest.replace(tzinfo=timezone.utc)
            elapsed = max(0, int((timestamp - oldest).total_seconds()))
            remaining_wait = max(0, MAX_BATCH_WAIT_SECONDS - elapsed)
            if remaining_wait:
                return ModelRoutingLearningBatchResult(
                    cls._state(learner),
                    0,
                    len(labels),
                    deferred_seconds=remaining_wait,
                )

        result = cls.train_labels(
            learner_state=cls._state(learner),
            labels=labels,
            batch_size=DEFAULT_LEARNING_BATCH_SIZE,
            processed_at=timestamp,
        )
        state = result.learner_state
        learner.candidate_artifact = dict(
            state.get("candidate_requirement_artifact") or {}
        )
        learner.judged_request_count = int(state.get("judged_request_count") or 0)
        learner.selected_model_counts = dict(state.get("selected_model_counts") or {})
        learner.evaluation_window = list(state.get("validation_window") or [])
        learner.recent_evaluation = dict(state.get("recent_evaluation") or {})
        ModelRoutingLearnerStore.publish_if_qualified(db, learner=learner)
        db.flush()
        return ModelRoutingLearningBatchResult(
            learner_state=cls._state(learner),
            processed_count=result.processed_count,
            remaining_count=result.remaining_count,
        )

    @staticmethod
    def _state(learner: Any) -> dict[str, Any]:
        return {
            "mode": "judge_first",
            "candidate_requirement_artifact": dict(
                getattr(learner, "candidate_artifact", None) or {}
            ),
            "judged_request_count": int(
                getattr(learner, "judged_request_count", 0) or 0
            ),
            "selected_model_ids": sorted(
                (getattr(learner, "selected_model_counts", None) or {}).keys()
            ),
            "selected_model_counts": dict(
                getattr(learner, "selected_model_counts", None) or {}
            ),
            "validation_window": list(
                getattr(learner, "evaluation_window", None) or []
            ),
            "recent_evaluation": dict(
                getattr(learner, "recent_evaluation", None) or {}
            ),
            "local_confidence_threshold": float(
                getattr(learner, "local_confidence_threshold", 0.78) or 0.78
            ),
        }

    @staticmethod
    def _requirements(value: Any) -> dict[str, int]:
        if not isinstance(value, dict):
            raise ValueError("task_requirements is required")
        return {
            key: max(0, min(3, int(value[key])))
            for key in IncrementalTaskRequirementClassifier.REQUIREMENT_KEYS
        }

    @classmethod
    def _evaluation_entry(
        cls,
        *,
        prediction: dict[str, int],
        truth: dict[str, int],
        contract_passed: bool,
    ) -> dict[str, Any]:
        errors = {
            key: abs(int(prediction[key]) - int(truth[key]))
            for key in IncrementalTaskRequirementClassifier.REQUIREMENT_KEYS
        }
        return {
            "prediction": prediction,
            "judge": truth,
            "exact_match": all(error == 0 for error in errors.values()),
            "axis_errors": errors,
            "contract_passed": contract_passed,
        }

    @classmethod
    def _evaluation_summary(cls, window: list[dict[str, Any]]) -> dict[str, Any]:
        count = len(window)
        keys = IncrementalTaskRequirementClassifier.REQUIREMENT_KEYS
        if not count:
            return {
                "sample_count": 0,
                "judge_match_rate": 0.0,
                "axis_mean_errors": {key: 0.0 for key in keys},
                "axis_accuracies": {key: 0.0 for key in keys},
                "judge_label_diversity": 0,
                "local_prediction_diversity": 0,
                "contract_pass_rate": 0.0,
                "high_risk_underestimation_count": 0,
            }
        judge_labels = {
            tuple(int(row["judge"][key]) for key in keys) for row in window
        }
        predictions = {
            tuple(int(row["prediction"][key]) for key in keys) for row in window
        }
        return {
            "sample_count": count,
            "judge_match_rate": round(
                sum(bool(row["exact_match"]) for row in window) / count, 4
            ),
            "axis_mean_errors": {
                key: round(
                    sum(float(row["axis_errors"][key]) for row in window) / count,
                    4,
                )
                for key in keys
            },
            "axis_accuracies": {
                key: round(
                    sum(float(row["axis_errors"][key]) == 0.0 for row in window)
                    / count,
                    4,
                )
                for key in keys
            },
            "judge_label_diversity": len(judge_labels),
            "local_prediction_diversity": len(predictions),
            "contract_pass_rate": round(
                sum(bool(row["contract_passed"]) for row in window) / count,
                4,
            ),
            "high_risk_underestimation_count": sum(
                int(row["judge"]["decision_impact"]) == 3
                and int(row["prediction"]["decision_impact"]) < 3
                for row in window
            ),
        }
