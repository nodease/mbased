"""Judge 선택을 점진적으로 재현하는 multilingual E5 기반 local router."""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
import struct
import threading
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Protocol


DEFAULT_MULTILINGUAL_E5_MODEL_ID = "intfloat/multilingual-e5-base"
DEFAULT_MULTILINGUAL_E5_REVISION = "d13f1b27baf31030b7fd040960d60d909913633f"
E5_EMBEDDING_DIMENSION = 768
E5_PROJECTION_DIMENSION = 128
E5_PROJECTION_SEED = 1729
E5_PROJECTION_VERSION = "e5-gaussian-projection-v1"
E5_POOLING_STRATEGY = "attention-mask-mean-pooling-v1"
E5_QUERY_PREFIX_VERSION = "e5-query-prefix-v1"
STRUCTURED_FEATURE_VERSION = "routing-structure-v4-axis-specific-dynamic-signals"


class TextEmbedder(Protocol):
    model_id: str

    def encode(
        self,
        texts: list[str],
        *,
        mode: str = "plain",
    ) -> list[list[float]]: ...


@dataclass(frozen=True)
class ModelChoicePrediction:
    selected_model_id: str
    confidence: float
    probabilities: dict[str, float]


@dataclass(frozen=True)
class TaskRequirementPrediction:
    requirements: dict[str, int]
    confidence: float


class MultilingualE5Embedder:
    """동일 worker에서 공유하는 lazy-loaded multilingual E5 encoder."""

    def __init__(self, model_id: str | None = None, revision: str | None = None):
        self.model_id = (
            model_id
            or os.getenv("MODEL_ROUTING_EMBEDDING_MODEL_ID")
            or DEFAULT_MULTILINGUAL_E5_MODEL_ID
        )
        self.revision = (
            revision
            or os.getenv("MODEL_ROUTING_EMBEDDING_MODEL_REVISION")
            or DEFAULT_MULTILINGUAL_E5_REVISION
        )
        self._tokenizer: Any | None = None
        self._model: Any | None = None
        self._torch: Any | None = None
        self._load_lock = threading.Lock()

    def _load(self) -> None:
        if self._model is not None:
            return
        with self._load_lock:
            if self._model is not None:
                return
            try:
                os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
                import torch
                from transformers import AutoModel, AutoTokenizer
            except ImportError as exc:  # pragma: no cover - 배포 의존성 guard
                raise RuntimeError(
                    "로컬 모델 라우터 의존성이 설치되지 않았습니다. "
                    "workflow_engine image를 다시 빌드하세요."
                ) from exc

            self._torch = torch
            self._tokenizer = AutoTokenizer.from_pretrained(
                self.model_id,
                revision=self.revision,
                use_fast=False,
            )
            self._model = AutoModel.from_pretrained(
                self.model_id,
                revision=self.revision,
            )
            self._model.eval()

    def encode(
        self,
        texts: list[str],
        *,
        mode: str = "plain",
    ) -> list[list[float]]:
        self._load()
        assert self._tokenizer is not None
        assert self._model is not None
        assert self._torch is not None
        if not texts:
            return []

        encoded = self._tokenizer(
            [str(text or "").strip() for text in texts],
            padding=True,
            truncation=True,
            max_length=512,
            return_tensors="pt",
        )
        with self._torch.no_grad():
            output = self._model(**encoded)
        token_embeddings = output.last_hidden_state
        attention_mask = encoded["attention_mask"].unsqueeze(-1).expand_as(
            token_embeddings
        )
        pooled = (token_embeddings * attention_mask).sum(dim=1) / attention_mask.sum(
            dim=1
        ).clamp(min=1)
        return pooled.cpu().tolist()


class FixedGaussianRandomProjection:
    """E5 의미 벡터를 재현 가능한 고정 행렬로 128차원화한다."""

    _matrix_cache: dict[tuple[int, int, int], tuple[tuple[float, ...], ...]] = {}
    _hash_cache: dict[tuple[int, int, int], str] = {}
    _cache_lock = threading.Lock()

    @classmethod
    def project(cls, vector: Iterable[float]) -> list[float]:
        values = [float(value) for value in vector]
        if not values:
            raise ValueError("projection input vector is required")
        matrix = cls._matrix(len(values))
        projected = [
            sum(weight * value for weight, value in zip(row, values))
            for row in matrix
        ]
        return cls._l2_normalize(projected)

    @classmethod
    def metadata(cls, *, input_dimension: int) -> dict[str, Any]:
        key = (int(input_dimension), E5_PROJECTION_DIMENSION, E5_PROJECTION_SEED)
        cls._matrix(int(input_dimension))
        return {
            "projection_type": "gaussian_random_projection",
            "projection_input_dimension": int(input_dimension),
            "projection_dimension": E5_PROJECTION_DIMENSION,
            "projection_seed": E5_PROJECTION_SEED,
            "projection_version": E5_PROJECTION_VERSION,
            "projection_matrix_hash": cls._hash_cache[key],
        }

    @classmethod
    def _matrix(cls, input_dimension: int) -> tuple[tuple[float, ...], ...]:
        if input_dimension <= 0:
            raise ValueError("projection input dimension must be positive")
        key = (input_dimension, E5_PROJECTION_DIMENSION, E5_PROJECTION_SEED)
        with cls._cache_lock:
            cached = cls._matrix_cache.get(key)
            if cached is not None:
                return cached
            generator = random.Random(E5_PROJECTION_SEED)
            scale = 1.0 / math.sqrt(E5_PROJECTION_DIMENSION)
            matrix = tuple(
                tuple(generator.gauss(0.0, scale) for _ in range(input_dimension))
                for _ in range(E5_PROJECTION_DIMENSION)
            )
            digest = hashlib.sha256()
            for row in matrix:
                for value in row:
                    digest.update(struct.pack("!d", value))
            cls._matrix_cache[key] = matrix
            cls._hash_cache[key] = digest.hexdigest()
            return matrix

    @staticmethod
    def _l2_normalize(vector: list[float]) -> list[float]:
        norm = math.sqrt(sum(value * value for value in vector))
        if norm <= 0:
            raise ValueError("0 length vector cannot be projected")
        return [value / norm for value in vector]


class MultilingualE5ModelChoiceClassifier:
    """원문을 저장하지 않고 Judge label을 online classification head에 누적한다."""

    _embedder_cache: dict[tuple[str, str], MultilingualE5Embedder] = {}
    _embedder_cache_lock = threading.Lock()
    _SEMANTIC_GROUP_WEIGHTS = {
        "primary_request": 0.75,
        "dynamic_context": 0.15,
    }
    _STRUCTURED_FEATURE_KEYS = (
        "schema_required",
        "knowledge_enabled",
        "retrieved_source_count",
        "downstream_contract_required",
        "customer_facing",
        "input_token_bucket",
        "has_file_input",
        "external_write_reachable",
        "external_read_reachable",
        "local_execution_reachable",
        "customer_output_reachable",
        "control_gate_present",
        "irreversible_effect_possible",
        "reachable_effect_count",
        "human_approval_required",
        "information_request",
        "recommendation_request",
        "approval_decision",
        "state_change_requested",
        "external_send_requested",
        "negated_action",
        "informational_scope",
        "workflow_effect_match",
        "action_confidence",
        "context_field_count",
        "context_value_count",
        "context_char_bucket",
        "comparison_requested",
        "synthesis_requested",
        "request_retrieved_source_count",
        "multi_source_context",
        "comparison_criterion_count",
    )

    @classmethod
    def update(
        cls,
        artifact: dict[str, Any] | None,
        *,
        text: str,
        selected_model_id: str,
        candidate_model_ids: Iterable[str],
        embedder: TextEmbedder | None = None,
    ) -> dict[str, Any]:
        from apps.workflow_engine.services.model_routing_incremental_learning import (
            IncrementalModelChoiceClassifier,
        )

        vector, encoder_model_id = cls._vector(
            text,
            artifact=artifact,
            embedder=embedder,
        )
        updated = IncrementalModelChoiceClassifier.update(
            artifact,
            vector=vector,
            selected_model_id=selected_model_id,
            candidate_model_ids=candidate_model_ids,
        )
        updated["encoder_model_id"] = encoder_model_id
        updated.update(cls._artifact_contract_metadata())
        updated["classification_strategy"] = (
            "frozen_multilingual_e5_projection_128_online_model_choice_v2"
        )
        return updated

    @classmethod
    def vectorize(
        cls,
        text: str,
        *,
        artifact: dict[str, Any] | None,
        embedder: TextEmbedder | None = None,
    ) -> tuple[list[float], str]:
        """원문을 저장하지 않고 현재 요청의 학습용 vector만 만든다."""
        return cls._vector(text, artifact=artifact, embedder=embedder)

    @classmethod
    def update_from_vector(
        cls,
        artifact: dict[str, Any] | None,
        *,
        vector: Iterable[float],
        encoder_model_id: str | None,
        selected_model_id: str,
        candidate_model_ids: Iterable[str],
    ) -> dict[str, Any]:
        from apps.workflow_engine.services.model_routing_incremental_learning import (
            IncrementalModelChoiceClassifier,
        )

        updated = IncrementalModelChoiceClassifier.update(
            artifact,
            vector=vector,
            selected_model_id=selected_model_id,
            candidate_model_ids=candidate_model_ids,
        )
        updated["encoder_model_id"] = str(encoder_model_id or "")
        updated.update(cls._artifact_contract_metadata())
        updated["classification_strategy"] = (
            "frozen_multilingual_e5_projection_128_online_model_choice_v2"
        )
        return updated

    @classmethod
    def predict(
        cls,
        artifact: dict[str, Any],
        *,
        text: str,
        available_model_ids: Iterable[str],
        embedder: TextEmbedder | None = None,
    ) -> ModelChoicePrediction:
        from apps.workflow_engine.services.model_routing_incremental_learning import (
            IncrementalModelChoiceClassifier,
        )

        vector, _ = cls._vector(text, artifact=artifact, embedder=embedder)
        result = IncrementalModelChoiceClassifier.predict(
            artifact,
            vector=vector,
            available_model_ids=available_model_ids,
        )
        return ModelChoicePrediction(
            selected_model_id=result.selected_model_id,
            confidence=result.confidence,
            probabilities=result.probabilities,
        )

    @classmethod
    def _vector(
        cls,
        text: str,
        *,
        artifact: dict[str, Any] | None,
        embedder: TextEmbedder | None,
    ) -> tuple[list[float], str]:
        encoder_model_id = str((artifact or {}).get("encoder_model_id") or "")
        runtime_embedder = embedder or cls._shared_embedder(encoder_model_id or None)
        grouped_texts, structured_vector = cls._learning_inputs(text)
        vectors = cls._encode(
            runtime_embedder,
            [serialized for _group, serialized in grouped_texts],
        )
        if not vectors or len(vectors) != len(grouped_texts):
            raise ValueError("모델 선택 학습용 multilingual E5 벡터를 만들지 못했습니다.")
        if isinstance(runtime_embedder, MultilingualE5Embedder) and any(
            len(vector) != E5_EMBEDDING_DIMENSION for vector in vectors
        ):
            raise ValueError(
                "multilingual E5 embedding은 "
                f"{E5_EMBEDDING_DIMENSION}차원이어야 합니다."
            )
        normalized_vectors = [cls._l2_normalize(vector) for vector in vectors]
        total_weight = sum(
            cls._SEMANTIC_GROUP_WEIGHTS[group] for group, _text in grouped_texts
        )
        combined = [0.0] * len(normalized_vectors[0])
        for (group, _text), vector in zip(grouped_texts, normalized_vectors):
            if len(vector) != len(combined):
                raise ValueError("학습 feature group의 embedding 차원이 다릅니다.")
            weight = cls._SEMANTIC_GROUP_WEIGHTS[group] / total_weight
            combined = [
                current + weight * value
                for current, value in zip(combined, vector)
            ]
        projected = FixedGaussianRandomProjection.project(
            cls._l2_normalize(combined)
        )
        return [*projected, *structured_vector], runtime_embedder.model_id

    @classmethod
    def _learning_inputs(
        cls,
        text: str,
    ) -> tuple[list[tuple[str, str]], list[float]]:
        """의미 텍스트와 명시적 구조 특징을 서로 다른 입력으로 만든다."""

        try:
            payload = json.loads(str(text or ""))
        except (TypeError, json.JSONDecodeError):
            payload = None
        groups: list[tuple[str, str]] = []
        if isinstance(payload, Mapping):
            for group in cls._SEMANTIC_GROUP_WEIGHTS:
                value = payload.get(group)
                if isinstance(value, Mapping) and value:
                    groups.append(
                        (
                            group,
                            "query: " + json.dumps(
                                {"group": group, "value": value},
                                ensure_ascii=False,
                                sort_keys=True,
                                default=str,
                            ),
                        )
                    )
        if not groups:
            groups = [
                (
                    "primary_request",
                    "query: " + json.dumps(
                        {
                            "group": "primary_request",
                            "value": str(text or "").strip(),
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                )
            ]
        structured = payload.get("structured_features") if isinstance(payload, Mapping) else {}
        return groups, cls._structured_vector(structured)

    @classmethod
    def _structured_vector(cls, value: Any) -> list[float]:
        structured = value if isinstance(value, Mapping) else {}
        contract = structured.get("routing_contract")
        contract = contract if isinstance(contract, Mapping) else {}
        rag = structured.get("rag")
        rag = rag if isinstance(rag, Mapping) else {}
        requested_action = structured.get("requested_action")
        requested_action = (
            requested_action if isinstance(requested_action, Mapping) else {}
        )
        request_evidence = structured.get("request_evidence")
        request_evidence = (
            request_evidence if isinstance(request_evidence, Mapping) else {}
        )
        retrieved_count = rag.get("source_count", rag.get("retrieved_chunk_count", 0))
        try:
            normalized_retrieved_count = max(0.0, min(16.0, float(retrieved_count))) / 16.0
        except (TypeError, ValueError):
            normalized_retrieved_count = 0.0
        try:
            input_bucket = max(0.0, min(3.0, float(contract.get("input_token_bucket", 0)))) / 3.0
        except (TypeError, ValueError):
            input_bucket = 0.0
        try:
            effect_count = max(
                0.0,
                min(8.0, float(contract.get("reachable_effect_count", 0))),
            ) / 8.0
        except (TypeError, ValueError):
            effect_count = 0.0
        return [
            float(bool(contract.get("schema_required"))),
            float(bool(contract.get("knowledge_enabled") or rag.get("used"))),
            normalized_retrieved_count,
            float(bool(contract.get("downstream_contract_required"))),
            float(bool(contract.get("customer_facing"))),
            input_bucket,
            float(bool(contract.get("has_file_input"))),
            float(bool(contract.get("external_write_reachable"))),
            float(bool(contract.get("external_read_reachable"))),
            float(bool(contract.get("local_execution_reachable"))),
            float(bool(contract.get("customer_output_reachable"))),
            float(bool(contract.get("control_gate_present"))),
            float(bool(contract.get("irreversible_effect_possible"))),
            effect_count,
            float(bool(contract.get("human_approval_required"))),
            float(bool(requested_action.get("information_request"))),
            float(bool(requested_action.get("recommendation_request"))),
            float(bool(requested_action.get("approval_decision"))),
            float(bool(requested_action.get("state_change_requested"))),
            float(bool(requested_action.get("external_send_requested"))),
            float(bool(requested_action.get("negated_action"))),
            float(bool(requested_action.get("informational_scope"))),
            float(bool(requested_action.get("workflow_effect_match"))),
            cls._bounded_ratio(
                requested_action.get("confidence"),
                maximum=1.0,
            ),
            cls._bounded_ratio(
                request_evidence.get("context_field_count"),
                maximum=8.0,
            ),
            cls._bounded_ratio(
                request_evidence.get("context_value_count"),
                maximum=16.0,
            ),
            cls._bounded_ratio(
                request_evidence.get("context_char_bucket"),
                maximum=3.0,
            ),
            float(bool(request_evidence.get("comparison_requested"))),
            float(bool(request_evidence.get("synthesis_requested"))),
            cls._bounded_ratio(
                request_evidence.get("retrieved_source_count"),
                maximum=16.0,
            ),
            float(bool(request_evidence.get("multi_source_context"))),
            cls._bounded_ratio(
                request_evidence.get("comparison_criterion_count"),
                maximum=8.0,
            ),
        ]

    @staticmethod
    def _bounded_ratio(value: Any, *, maximum: float) -> float:
        try:
            return max(0.0, min(maximum, float(value or 0.0))) / maximum
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _artifact_contract_metadata() -> dict[str, Any]:
        return {
            "encoder_revision": (
                os.getenv("MODEL_ROUTING_EMBEDDING_MODEL_REVISION")
                or DEFAULT_MULTILINGUAL_E5_REVISION
            ),
            "pooling_strategy": E5_POOLING_STRATEGY,
            "query_prefix_version": E5_QUERY_PREFIX_VERSION,
            "structured_feature_version": STRUCTURED_FEATURE_VERSION,
            **FixedGaussianRandomProjection.metadata(
                input_dimension=E5_EMBEDDING_DIMENSION
            ),
        }

    @classmethod
    def _shared_embedder(cls, model_id: str | None = None) -> MultilingualE5Embedder:
        resolved_model_id = (
            model_id
            or os.getenv("MODEL_ROUTING_EMBEDDING_MODEL_ID")
            or DEFAULT_MULTILINGUAL_E5_MODEL_ID
        )
        resolved_revision = (
            os.getenv("MODEL_ROUTING_EMBEDDING_MODEL_REVISION")
            or DEFAULT_MULTILINGUAL_E5_REVISION
        )
        cache_key = (resolved_model_id, resolved_revision)
        with cls._embedder_cache_lock:
            embedder = cls._embedder_cache.get(cache_key)
            if embedder is None:
                embedder = MultilingualE5Embedder(
                    resolved_model_id,
                    revision=resolved_revision,
                )
                cls._embedder_cache[cache_key] = embedder
            return embedder

    @staticmethod
    def _encode(
        embedder: TextEmbedder,
        texts: list[str],
    ) -> list[list[float]]:
        try:
            return embedder.encode(texts, mode="plain")
        except TypeError as exc:
            if "mode" not in str(exc):
                raise
            return embedder.encode(texts)

    @staticmethod
    def _l2_normalize(vector: list[float]) -> list[float]:
        values = [float(value) for value in vector]
        norm = math.sqrt(sum(value * value for value in values))
        if norm <= 0:
            raise ValueError("0 길이 embedding은 모델 선택에 사용할 수 없습니다.")
        return [value / norm for value in values]


class MultilingualE5TaskRequirementClassifier:
    """multilingual E5 vector에서 모델 ID가 아닌 요청 요구 능력을 학습한다."""

    @classmethod
    def update_from_vector(
        cls,
        artifact: dict[str, Any] | None,
        *,
        vector: Iterable[float],
        encoder_model_id: str | None,
        task_requirements: dict[str, Any],
    ) -> dict[str, Any]:
        from apps.workflow_engine.services.model_routing_incremental_learning import (
            IncrementalTaskRequirementClassifier,
        )

        updated = IncrementalTaskRequirementClassifier.update(
            artifact,
            vector=vector,
            axis_vectors=cls._axis_vectors(vector),
            task_requirements=task_requirements,
        )
        updated["encoder_model_id"] = str(encoder_model_id or "")
        updated.update(MultilingualE5ModelChoiceClassifier._artifact_contract_metadata())
        updated["classification_strategy"] = (
            "frozen_multilingual_e5_projection_128_ordinal_requirements_v4_axis_specific"
        )
        return updated

    @classmethod
    def predict(
        cls,
        artifact: dict[str, Any],
        *,
        text: str,
        embedder: TextEmbedder | None = None,
    ) -> TaskRequirementPrediction | None:
        from apps.workflow_engine.services.model_routing_incremental_learning import (
            IncrementalTaskRequirementClassifier,
        )

        vector, _ = MultilingualE5ModelChoiceClassifier._vector(
            text,
            artifact=artifact,
            embedder=embedder,
        )
        result = IncrementalTaskRequirementClassifier.predict(
            artifact,
            vector=vector,
            axis_vectors=cls._axis_vectors(vector),
        )
        if result is None:
            return None
        return TaskRequirementPrediction(
            requirements=result.requirements,
            confidence=result.confidence,
        )

    @classmethod
    def predict_from_vector(
        cls,
        artifact: dict[str, Any] | None,
        *,
        vector: Iterable[float],
    ):
        """이미 만든 안전 vector로 Judge 호출 전 요구 수준을 예측한다."""
        from apps.workflow_engine.services.model_routing_incremental_learning import (
            IncrementalTaskRequirementClassifier,
        )

        return IncrementalTaskRequirementClassifier.predict(
            artifact,
            vector=vector,
            axis_vectors=cls._axis_vectors(vector),
        )

    @staticmethod
    def _axis_vectors(vector: Iterable[float]) -> dict[str, list[float]]:
        """평가축마다 근거가 되는 특징만 남겨 의미 유사도의 과신을 줄인다."""

        values = [float(value) for value in vector]
        if len(values) <= E5_PROJECTION_DIMENSION:
            return {
                key: list(values)
                for key in (
                    "task_complexity",
                    "decision_impact",
                    "evidence_synthesis",
                )
            }
        semantic = values[:E5_PROJECTION_DIMENSION]
        structured = values[E5_PROJECTION_DIMENSION:]

        def build(*, semantic_scale: float, structural_indexes: set[int]) -> list[float]:
            return [
                *(value * semantic_scale for value in semantic),
                *(
                    value if index in structural_indexes else 0.0
                    for index, value in enumerate(structured)
                ),
            ]

        return {
            "task_complexity": build(
                semantic_scale=1.0,
                structural_indexes={0, 3, 5, 6, 9},
            ),
            "decision_impact": build(
                # 같은 환불 주제라도 "정책 설명"과 "승인 실행"은 의미가 다르다.
                # 요청 의도 의미와 실제 후속 동작 특징을 함께 사용한다.
                semantic_scale=1.0,
                structural_indexes={
                    3,
                    4,
                    7,
                    9,
                    10,
                    11,
                    12,
                    13,
                    14,
                    15,
                    16,
                    17,
                    18,
                    19,
                    20,
                    21,
                    22,
                    23,
                },
            ),
            "evidence_synthesis": build(
                semantic_scale=1.0,
                structural_indexes={
                    0,
                    1,
                    2,
                    3,
                    5,
                    6,
                    8,
                    24,
                    25,
                    26,
                    27,
                    28,
                    29,
                    30,
                    31,
                },
            ),
        }
