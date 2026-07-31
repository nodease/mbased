"""Judge 선택을 점진적으로 재현하는 작은 다중 모델 분류기.

이 모듈은 임베더가 만든 숫자 벡터만 받는다. 원문 요청은 저장하지 않으며, policy
JSON에는 모델 ID, 선형 head 가중치, 표본 수만 남는다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


# Local routing begins only after enough deployed, contract-passing Judge labels
# have accumulated to make its first autonomous choice meaningful.
MIN_JUDGED_REQUESTS = 100
MIN_RECENT_EVALUATION_SAMPLES = 50
MIN_EXACT_MATCH_RATE = 0.75
MIN_AXIS_ACCURACY = 0.85
MIN_DISTINCT_SELECTED_MODELS = 2
MAX_SELECTED_MODEL_SHARE = 0.70
MIN_SUCCESS_RATE = 0.95
MIN_SCHEMA_PASS_RATE = 0.95
MIN_DOWNSTREAM_SUCCESS_RATE = 0.95
MAX_FALLBACK_RATE = 0.05
TASK_REQUIREMENT_FEATURE_SCHEMA_VERSION = (
    "grouped_runtime_variables_v8_e5_projection_128_axis_specific_dynamic_signals"
)


@dataclass(frozen=True)
class LocalModelChoicePrediction:
    selected_model_id: str
    confidence: float
    probabilities: dict[str, float]


@dataclass(frozen=True)
class LocalTaskRequirementPrediction:
    """현재 요청이 요구하는 능력 수준의 local 추정값."""

    requirements: dict[str, int]
    confidence: float
    raw_requirements: dict[str, float]
    distance_score: float
    margin: float
    ordinal_probabilities: dict[str, list[float]]
    coverage_score: float


class IncrementalTaskRequirementClassifier:
    """각 요구 축을 세 개의 누적 임계값으로 학습하는 online ordinal head.

    축마다 하나의 weight vector와 정렬된 세 threshold를 공유하므로
    ``P(level>=1) >= P(level>=2) >= P(level>=3)``가 구조적으로 보장된다.
    """

    ARTIFACT_KIND = "multilingual_e5_task_requirements_ordinal_v3"
    ORDINAL_HEAD_VERSION = "coral-linear-ordinal-9-output-v2"
    REQUIREMENT_KEYS = (
        "task_complexity",
        "decision_impact",
        "evidence_synthesis",
    )
    LEARNING_RATE = 0.05
    L2 = 0.0005
    INITIAL_THRESHOLDS = (-1.0, 0.0, 1.0)
    MIN_THRESHOLD_CLASS_SAMPLES = 10

    @classmethod
    def update(
        cls,
        artifact: dict[str, Any] | None,
        *,
        vector: Iterable[float],
        axis_vectors: Mapping[str, Iterable[float]] | None = None,
        task_requirements: dict[str, Any],
    ) -> dict[str, Any]:
        values = cls._vector(vector)
        vectors = cls._axis_vectors(values, axis_vectors)
        targets = cls._requirements(task_requirements)
        state = cls._state(artifact, len(values))
        total_error = 0.0
        for key in cls.REQUIREMENT_KEYS:
            axis_values = vectors[key]
            probabilities = cls._ordinal_probabilities(state, key, axis_values)
            ordinal_targets = cls.ordinal_targets(targets[key])
            errors = [
                float(target) - probability
                for target, probability in zip(ordinal_targets, probabilities)
            ]
            total_error += sum(abs(error) for error in errors) / 3.0
            weights = state["weights"][key]
            shared_error = sum(errors) / 3.0
            state["weights"][key] = [
                weight
                + cls.LEARNING_RATE * (shared_error * feature - cls.L2 * weight)
                for weight, feature in zip(weights, axis_values)
            ]
            updated_thresholds = [
                threshold - cls.LEARNING_RATE * error
                for threshold, error in zip(state["thresholds"][key], errors)
            ]
            state["thresholds"][key] = sorted(updated_thresholds)
            cls._record_threshold_targets(state, key, ordinal_targets)

        count = int(state["trained_example_count"]) + 1
        previous_error = float(state.get("training_error_ema") or 0.0)
        state["training_error_ema"] = (
            total_error / len(cls.REQUIREMENT_KEYS)
            if count == 1
            else previous_error * 0.9
            + (total_error / len(cls.REQUIREMENT_KEYS)) * 0.1
        )
        state["trained_example_count"] = count
        state["feature_schema_version"] = TASK_REQUIREMENT_FEATURE_SCHEMA_VERSION
        state["ordinal_head_version"] = cls.ORDINAL_HEAD_VERSION
        cls._update_centroid(state, values, targets)
        return state

    @classmethod
    def predict(
        cls,
        artifact: dict[str, Any] | None,
        *,
        vector: Iterable[float],
        axis_vectors: Mapping[str, Iterable[float]] | None = None,
    ) -> LocalTaskRequirementPrediction | None:
        values = cls._vector(vector)
        if not isinstance(artifact, dict) or int(
            artifact.get("trained_example_count") or 0
        ) <= 0:
            return None
        vectors = cls._axis_vectors(values, axis_vectors)
        state = cls._state(artifact, len(values))
        ordinal_probabilities = {
            key: cls._ordinal_probabilities(state, key, vectors[key])
            for key in cls.REQUIREMENT_KEYS
        }
        raw_requirements = {
            key: sum(probabilities)
            for key, probabilities in ordinal_probabilities.items()
        }
        requirements = {
            key: sum(probability >= 0.5 for probability in probabilities)
            for key, probabilities in ordinal_probabilities.items()
        }
        distance_score = cls._distance_score(state, values)
        margin = min(
            cls._probability_margin(probability)
            for probabilities in ordinal_probabilities.values()
            for probability in probabilities
        )
        coverage_score = cls._coverage_score(
            state,
            ordinal_probabilities=ordinal_probabilities,
        )
        recent_match_rate = max(
            0.0, min(1.0, float(state.get("recent_judge_match_rate") or 0.0))
        )
        recent_contract_rate = max(
            0.0, min(1.0, float(state.get("recent_contract_pass_rate") or 0.0))
        )
        return LocalTaskRequirementPrediction(
            requirements=requirements,
            confidence=round(
                recent_match_rate
                * distance_score
                * recent_contract_rate
                * coverage_score
                * (0.9 + 0.1 * margin),
                4,
            ),
            raw_requirements={
                key: round(value, 4) for key, value in raw_requirements.items()
            },
            distance_score=round(distance_score, 4),
            margin=round(margin, 4),
            ordinal_probabilities={
                key: [round(value, 6) for value in probabilities]
                for key, probabilities in ordinal_probabilities.items()
            },
            coverage_score=round(coverage_score, 4),
        )

    @classmethod
    def initialize_from_requirements(
        cls,
        task_requirements: Iterable[dict[str, Any]],
        *,
        dimensions: int,
    ) -> dict[str, Any]:
        """첫 batch의 클래스 분포로 ordinal threshold의 시작점을 정한다.

        아직 학습되지 않은 0 가중치 모델의 임의 예측은 만들지 않는다. Laplace
        smoothing을 적용해 한 클래스만 있는 작은 첫 batch에서도 무한 threshold가
        생기지 않도록 한다.
        """

        rows = [cls._requirements(value) for value in task_requirements]
        state = cls._state(None, dimensions)
        if not rows:
            return state
        for key in cls.REQUIREMENT_KEYS:
            thresholds: list[float] = []
            for level in (1, 2, 3):
                positives = sum(int(row[key] >= level) for row in rows)
                probability = (positives + 1.0) / (len(rows) + 2.0)
                logit = math.log(probability / (1.0 - probability))
                # score가 0일 때 sigmoid(score - threshold)가 관측 비율이 된다.
                thresholds.append(-logit)
            state["thresholds"][key] = sorted(thresholds)
        state["initialized_from_label_distribution"] = True
        state["initialization_sample_count"] = len(rows)
        return state

    @classmethod
    def _axis_vectors(
        cls,
        values: list[float],
        axis_vectors: Mapping[str, Iterable[float]] | None,
    ) -> dict[str, list[float]]:
        if axis_vectors is None:
            return {key: list(values) for key in cls.REQUIREMENT_KEYS}
        result: dict[str, list[float]] = {}
        for key in cls.REQUIREMENT_KEYS:
            candidate = cls._vector(axis_vectors.get(key, values))
            if len(candidate) != len(values):
                raise ValueError("요구 축별 feature vector 차원이 일치하지 않습니다.")
            result[key] = candidate
        return result

    @classmethod
    def _state(cls, artifact: dict[str, Any] | None, dimensions: int) -> dict[str, Any]:
        previous = artifact if isinstance(artifact, dict) else {}
        if previous.get("kind") not in {None, cls.ARTIFACT_KIND}:
            previous = {}
        weights = previous.get("weights") if isinstance(previous.get("weights"), dict) else {}
        thresholds = (
            previous.get("thresholds")
            if isinstance(previous.get("thresholds"), dict)
            else {}
        )
        threshold_counts = (
            previous.get("ordinal_threshold_counts")
            if isinstance(previous.get("ordinal_threshold_counts"), dict)
            else {}
        )
        return {
            "kind": cls.ARTIFACT_KIND,
            "ordinal_head_version": cls.ORDINAL_HEAD_VERSION,
            "weights": {
                key: [float(value) for value in weights.get(key, [])]
                if isinstance(weights.get(key), list) and len(weights.get(key)) == dimensions
                else [0.0] * dimensions
                for key in cls.REQUIREMENT_KEYS
            },
            "thresholds": {
                key: sorted(float(value) for value in thresholds.get(key, []))
                if isinstance(thresholds.get(key), list)
                and len(thresholds.get(key)) == 3
                else list(cls.INITIAL_THRESHOLDS)
                for key in cls.REQUIREMENT_KEYS
            },
            "ordinal_threshold_counts": {
                key: cls._normalized_threshold_counts(threshold_counts.get(key))
                for key in cls.REQUIREMENT_KEYS
            },
            "trained_example_count": int(previous.get("trained_example_count") or 0),
            "training_error_ema": float(previous.get("training_error_ema") or 0.0),
            "requirement_centroids": dict(previous.get("requirement_centroids") or {}),
            "recent_judge_match_rate": float(
                previous.get("recent_judge_match_rate") or 0.0
            ),
            "recent_contract_pass_rate": float(
                previous.get("recent_contract_pass_rate") or 0.0
            ),
        }

    @staticmethod
    def _vector(vector: Iterable[float]) -> list[float]:
        values = [float(value) for value in vector]
        if not values:
            raise ValueError("vector is required")
        return values

    @classmethod
    def _requirements(cls, value: dict[str, Any]) -> dict[str, int]:
        if not isinstance(value, dict):
            raise ValueError("task_requirements is required")
        result: dict[str, int] = {}
        for key in cls.REQUIREMENT_KEYS:
            raw = value.get(key)
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                raise ValueError(f"{key} is required")
            if isinstance(raw, float) and not raw.is_integer():
                raise ValueError(f"{key} must be an integer")
            result[key] = max(0, min(3, int(raw)))
        return result

    @staticmethod
    def ordinal_targets(level: int) -> list[int]:
        normalized = max(0, min(3, int(level)))
        return [1 if normalized >= threshold else 0 for threshold in (1, 2, 3)]

    @classmethod
    def _ordinal_probabilities(
        cls,
        state: dict[str, Any],
        key: str,
        vector: list[float],
    ) -> list[float]:
        score = sum(
            weight * value
            for weight, value in zip(state["weights"][key], vector)
        )
        probabilities = [
            cls._sigmoid(score - float(threshold))
            for threshold in state["thresholds"][key]
        ]
        # 과거 artifact나 부동소수점 오차가 있어도 추론 계약은 단조로 보정한다.
        return [
            probabilities[0],
            min(probabilities[0], probabilities[1]),
            min(probabilities[0], probabilities[1], probabilities[2]),
        ]

    @staticmethod
    def _sigmoid(value: float) -> float:
        bounded = max(-30.0, min(30.0, float(value)))
        return 1.0 / (1.0 + math.exp(-bounded))

    @classmethod
    def _record_threshold_targets(
        cls,
        state: dict[str, Any],
        key: str,
        targets: list[int],
    ) -> None:
        counts = state["ordinal_threshold_counts"][key]
        for index, target in enumerate(targets):
            bucket = "positive" if target else "negative"
            counts[bucket][index] = int(counts[bucket][index]) + 1

    @staticmethod
    def _normalized_threshold_counts(value: Any) -> dict[str, list[int]]:
        row = value if isinstance(value, dict) else {}
        result: dict[str, list[int]] = {}
        for bucket in ("positive", "negative"):
            counts = row.get(bucket)
            result[bucket] = (
                [max(0, int(item)) for item in counts]
                if isinstance(counts, list) and len(counts) == 3
                else [0, 0, 0]
            )
        return result

    @classmethod
    def _coverage_score(
        cls,
        state: dict[str, Any],
        *,
        ordinal_probabilities: dict[str, list[float]],
    ) -> float:
        covered = 0
        total = 0
        for key, probabilities in ordinal_probabilities.items():
            counts = state["ordinal_threshold_counts"][key]
            for index, probability in enumerate(probabilities):
                bucket = "positive" if probability >= 0.5 else "negative"
                total += 1
                if int(counts[bucket][index]) >= cls.MIN_THRESHOLD_CLASS_SAMPLES:
                    covered += 1
        return 1.0 if total and covered == total else 0.0

    @classmethod
    def _update_centroid(
        cls,
        state: dict[str, Any],
        vector: list[float],
        targets: dict[str, int],
    ) -> None:
        key = ":".join(str(targets[name]) for name in cls.REQUIREMENT_KEYS)
        centroids = state["requirement_centroids"]
        current = centroids.get(key) if isinstance(centroids.get(key), dict) else {}
        count = int(current.get("count") or 0)
        previous = current.get("vector") if isinstance(current.get("vector"), list) else []
        if len(previous) != len(vector):
            previous = [0.0] * len(vector)
            count = 0
        next_count = count + 1
        centroids[key] = {
            "count": next_count,
            "vector": [
                ((float(old) * count) + value) / next_count
                for old, value in zip(previous, vector)
            ],
        }

    @staticmethod
    def _distance_score(state: dict[str, Any], vector: list[float]) -> float:
        centroids = state.get("requirement_centroids")
        if not isinstance(centroids, dict) or not centroids:
            return 0.0
        vector_norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        similarities: list[float] = []
        for centroid in centroids.values():
            row = centroid.get("vector") if isinstance(centroid, dict) else None
            if not isinstance(row, list) or len(row) != len(vector):
                continue
            row_values = [float(value) for value in row]
            row_norm = math.sqrt(sum(value * value for value in row_values)) or 1.0
            cosine = sum(a * b for a, b in zip(vector, row_values)) / (
                vector_norm * row_norm
            )
            similarities.append(max(0.0, min(1.0, cosine)))
        return max(similarities, default=0.0)

    @staticmethod
    def _probability_margin(probability: float) -> float:
        return max(0.0, min(1.0, abs(float(probability) - 0.5) * 2.0))


class IncrementalModelChoiceClassifier:
    """고정 encoder 위에서 online softmax head만 갱신한다."""

    ARTIFACT_KIND = "multilingual_e5_model_choice_online_v1"
    LEARNING_RATE = 0.16
    L2 = 0.0005

    @classmethod
    def update(
        cls,
        artifact: dict[str, Any] | None,
        *,
        vector: Iterable[float],
        selected_model_id: str,
        candidate_model_ids: Iterable[str],
    ) -> dict[str, Any]:
        values = cls._vector(vector)
        candidates = cls._models(candidate_model_ids, selected_model_id)
        state = cls._state(artifact, candidates, len(values))
        model_ids = state["model_ids"]
        probabilities = cls._probabilities(state, values, model_ids)

        for model_id in model_ids:
            target = 1.0 if model_id == selected_model_id else 0.0
            error = target - probabilities[model_id]
            weights = state["weights"][model_id]
            state["weights"][model_id] = [
                weight + cls.LEARNING_RATE * (error * feature - cls.L2 * weight)
                for weight, feature in zip(weights, values)
            ]
            state["bias"][model_id] = float(state["bias"][model_id]) + cls.LEARNING_RATE * error

        state["trained_example_count"] = int(state["trained_example_count"]) + 1
        labels = set(state.get("selected_model_ids") or [])
        labels.add(selected_model_id)
        state["selected_model_ids"] = sorted(labels)
        return state

    @classmethod
    def predict(
        cls,
        artifact: dict[str, Any] | None,
        *,
        vector: Iterable[float],
        available_model_ids: Iterable[str],
    ) -> LocalModelChoicePrediction:
        values = cls._vector(vector)
        allowed = cls._models(available_model_ids)
        if not allowed:
            raise ValueError("available_model_ids is required")
        state = cls._state(artifact, allowed, len(values))
        candidates = [model_id for model_id in allowed if model_id in state["model_ids"]]
        if not candidates:
            candidates = allowed
        probabilities = cls._probabilities(state, values, candidates)
        selected = max(candidates, key=lambda model_id: probabilities[model_id])
        return LocalModelChoicePrediction(
            selected_model_id=selected,
            confidence=probabilities[selected],
            probabilities=probabilities,
        )

    @classmethod
    def _state(
        cls,
        artifact: dict[str, Any] | None,
        model_ids: list[str],
        dimensions: int,
    ) -> dict[str, Any]:
        previous = artifact if isinstance(artifact, dict) else {}
        prior_ids = cls._models(previous.get("model_ids") or [])
        all_ids = cls._models([*prior_ids, *model_ids])
        weights = previous.get("weights") if isinstance(previous.get("weights"), dict) else {}
        bias = previous.get("bias") if isinstance(previous.get("bias"), dict) else {}
        normalized_weights: dict[str, list[float]] = {}
        normalized_bias: dict[str, float] = {}
        for model_id in all_ids:
            row = weights.get(model_id)
            normalized_weights[model_id] = (
                [float(value) for value in row]
                if isinstance(row, list) and len(row) == dimensions
                else [0.0] * dimensions
            )
            normalized_bias[model_id] = float(bias.get(model_id) or 0.0)
        return {
            "kind": cls.ARTIFACT_KIND,
            "model_ids": all_ids,
            "weights": normalized_weights,
            "bias": normalized_bias,
            "trained_example_count": int(previous.get("trained_example_count") or 0),
            "selected_model_ids": cls._models(previous.get("selected_model_ids") or []),
        }

    @staticmethod
    def _vector(vector: Iterable[float]) -> list[float]:
        values = [float(value) for value in vector]
        if not values:
            raise ValueError("vector is required")
        return values

    @staticmethod
    def _models(model_ids: Iterable[str], extra: str | None = None) -> list[str]:
        result: list[str] = []
        for raw in [*model_ids, extra]:
            model_id = str(raw or "").strip()
            if model_id and model_id not in result:
                result.append(model_id)
        return result

    @staticmethod
    def _probabilities(state: dict[str, Any], vector: list[float], model_ids: list[str]) -> dict[str, float]:
        logits = {
            model_id: sum(weight * value for weight, value in zip(state["weights"].get(model_id, []), vector))
            + float(state["bias"].get(model_id) or 0.0)
            for model_id in model_ids
        }
        max_logit = max(logits.values())
        exp_values = {model_id: math.exp(logit - max_logit) for model_id, logit in logits.items()}
        denominator = sum(exp_values.values()) or 1.0
        return {model_id: value / denominator for model_id, value in exp_values.items()}


def learning_mode_for(
    *,
    judged_request_count: int,
    distinct_selected_model_count: int | None = None,
    success_rate: float | None,
    schema_pass_rate: float | None,
    downstream_success_rate: float | None,
    fallback_rate: float | None,
    largest_selected_model_share: float | None = None,
    recent_judge_match_rate: float | None = None,
    recent_axis_accuracies: dict[str, float] | None = None,
    recent_axis_mean_errors: dict[str, float] | None = None,
    recent_judge_label_diversity: int | None = None,
    recent_local_prediction_diversity: int | None = None,
    recent_contract_pass_rate: float | None = None,
    recent_evaluation_sample_count: int | None = None,
    high_risk_underestimation_count: int | None = None,
) -> str:
    """정확한 표본과 운영 결과가 모였을 때만 local-first로 바꾼다."""

    if judged_request_count < MIN_JUDGED_REQUESTS:
        return "judge_first"
    if (recent_evaluation_sample_count or 0) < MIN_RECENT_EVALUATION_SAMPLES:
        return "judge_first"
    if recent_judge_match_rate is None or recent_judge_match_rate < MIN_EXACT_MATCH_RATE:
        return "judge_first"
    axis_accuracies = recent_axis_accuracies or {}
    if any(
        axis_accuracies.get(key) is None
        or float(axis_accuracies[key]) < MIN_AXIS_ACCURACY
        for key in IncrementalTaskRequirementClassifier.REQUIREMENT_KEYS
    ):
        return "judge_first"
    axis_errors = recent_axis_mean_errors or {}
    if any(
        axis_errors.get(key) is None or float(axis_errors[key]) > 0.5
        for key in IncrementalTaskRequirementClassifier.REQUIREMENT_KEYS
    ):
        return "judge_first"
    if (
        (recent_judge_label_diversity or 0) > 1
        and (recent_local_prediction_diversity or 0) <= 1
    ):
        return "judge_first"
    if recent_contract_pass_rate is None or recent_contract_pass_rate < 0.95:
        return "judge_first"
    if int(high_risk_underestimation_count or 0) > 0:
        return "judge_first"
    if (success_rate or 0.0) < MIN_SUCCESS_RATE:
        return "judge_first"
    if (schema_pass_rate or 0.0) < MIN_SCHEMA_PASS_RATE:
        return "judge_first"
    if (downstream_success_rate or 0.0) < MIN_DOWNSTREAM_SUCCESS_RATE:
        return "judge_first"
    if (fallback_rate or 0.0) > MAX_FALLBACK_RATE:
        return "judge_first"
    return "local_first"
