import json

import pytest

from apps.workflow_engine.services.model_routing_incremental_learning import (
    IncrementalTaskRequirementClassifier,
    learning_mode_for,
)
from apps.workflow_engine.services.model_routing_local_classifier import (
    DEFAULT_MULTILINGUAL_E5_MODEL_ID,
    E5_PROJECTION_DIMENSION,
    FixedGaussianRandomProjection,
    MultilingualE5Embedder,
    MultilingualE5ModelChoiceClassifier,
)
from apps.workflow_engine.services.model_router import ModelRouter
from apps.workflow_engine.services.model_routing_learner_store import (
    ModelRoutingLearnerStore,
)


def test_requirement_level_is_encoded_as_three_cumulative_ordinal_labels():
    assert IncrementalTaskRequirementClassifier.ordinal_targets(0) == [0, 0, 0]
    assert IncrementalTaskRequirementClassifier.ordinal_targets(1) == [1, 0, 0]
    assert IncrementalTaskRequirementClassifier.ordinal_targets(2) == [1, 1, 0]
    assert IncrementalTaskRequirementClassifier.ordinal_targets(3) == [1, 1, 1]


def test_untrained_ordinal_head_returns_prediction_unavailable():
    prediction = IncrementalTaskRequirementClassifier.predict(
        None,
        vector=[1.0, 0.0],
    )

    assert prediction is None


def test_first_batch_initializes_thresholds_from_label_distribution():
    artifact = IncrementalTaskRequirementClassifier.initialize_from_requirements(
        [
            {
                "task_complexity": 0,
                "decision_impact": 0,
                "evidence_synthesis": 0,
            },
            {
                "task_complexity": 1,
                "decision_impact": 1,
                "evidence_synthesis": 1,
            },
            {
                "task_complexity": 3,
                "decision_impact": 2,
                "evidence_synthesis": 2,
            },
        ],
        dimensions=2,
    )

    assert artifact["initialized_from_label_distribution"] is True
    assert artifact["trained_example_count"] == 0
    assert artifact["thresholds"]["task_complexity"] != list(
        IncrementalTaskRequirementClassifier.INITIAL_THRESHOLDS
    )


def test_ordinal_head_exposes_nine_monotonic_probabilities_without_rounding():
    artifact = None
    samples = (
        ([1.0, 0.0], 0),
        ([0.7, 0.3], 1),
        ([0.3, 0.7], 2),
        ([0.0, 1.0], 3),
    )
    for _ in range(40):
        for vector, level in samples:
            artifact = IncrementalTaskRequirementClassifier.update(
                artifact,
                vector=vector,
                task_requirements={
                    "task_complexity": level,
                    "decision_impact": level,
                    "evidence_synthesis": level,
                },
            )

    prediction = IncrementalTaskRequirementClassifier.predict(
        artifact,
        vector=[0.0, 1.0],
    )

    assert prediction is not None
    assert artifact["kind"] == "multilingual_e5_task_requirements_ordinal_v3"
    assert set(prediction.ordinal_probabilities) == {
        "task_complexity",
        "decision_impact",
        "evidence_synthesis",
    }
    assert sum(len(row) for row in prediction.ordinal_probabilities.values()) == 9
    for probabilities in prediction.ordinal_probabilities.values():
        assert probabilities[0] >= probabilities[1] >= probabilities[2]
    assert prediction.requirements["task_complexity"] >= 2
    assert "bias" not in artifact


def test_fixed_projection_is_reproducible_normalized_and_not_a_prefix_slice():
    source = [float(index % 17) / 17.0 for index in range(768)]

    first = FixedGaussianRandomProjection.project(source)
    second = FixedGaussianRandomProjection.project(source)

    assert len(first) == E5_PROJECTION_DIMENSION == 128
    assert first == second
    assert first != source[:E5_PROJECTION_DIMENSION]
    assert sum(value * value for value in first) == pytest.approx(1.0)
    metadata = FixedGaussianRandomProjection.metadata(input_dimension=768)
    assert metadata["projection_type"] == "gaussian_random_projection"
    assert metadata["projection_dimension"] == 128
    assert len(metadata["projection_matrix_hash"]) == 64


def test_uncovered_ordinal_segment_forces_judge_confidence_to_zero():
    artifact = None
    for _ in range(20):
        artifact = IncrementalTaskRequirementClassifier.update(
            artifact,
            vector=[1.0, 0.0],
            task_requirements={
                "task_complexity": 0,
                "decision_impact": 0,
                "evidence_synthesis": 0,
            },
        )
    artifact["recent_judge_match_rate"] = 1.0
    artifact["recent_contract_pass_rate"] = 1.0
    for key in IncrementalTaskRequirementClassifier.REQUIREMENT_KEYS:
        artifact["weights"][key] = [10.0, 0.0]

    prediction = IncrementalTaskRequirementClassifier.predict(
        artifact,
        vector=[1.0, 0.0],
    )

    assert prediction is not None
    assert all(level == 3 for level in prediction.requirements.values())
    assert prediction.coverage_score == 0.0
    assert prediction.confidence == 0.0


def test_learner_contract_tracks_encoder_projection_and_ordinal_versions():
    contract = ModelRoutingLearnerStore.current_contract()

    assert contract["encoder_revision"]
    assert contract["pooling_strategy"] == "attention-mask-mean-pooling-v1"
    assert contract["projection_metadata"]["projection_dimension"] == 128
    assert contract["structured_feature_version"] == (
        "routing-structure-v4-axis-specific-dynamic-signals"
    )
    assert contract["ordinal_head_version"] == "coral-linear-ordinal-9-output-v2"

    changed = ModelRoutingLearnerStore.judge_contract_hash(
        judge_rubric_version=contract["judge_rubric_version"],
        feature_schema_version=contract["feature_schema_version"],
        encoder_model_id=contract["encoder_model_id"],
        encoder_revision=contract["encoder_revision"],
        pooling_strategy=contract["pooling_strategy"],
        query_prefix_version=contract["query_prefix_version"],
        projection_metadata={
            **contract["projection_metadata"],
            "projection_version": "different-version",
        },
        structured_feature_version=contract["structured_feature_version"],
        ordinal_head_version=contract["ordinal_head_version"],
    )

    assert changed != contract["judge_contract_hash"]


class _ProjectionEmbedder:
    model_id = "projection-test-encoder"
    revision = "immutable-test-revision"

    def encode(self, texts, *, mode="plain"):
        vectors = []
        for text in texts:
            group = json.loads(text.removeprefix("query: "))["group"]
            offset = 0 if group == "primary_request" else 1
            vectors.append(
                [float((index + offset) % 11) / 11.0 for index in range(768)]
            )
        return vectors


def test_vectorizer_projects_semantics_then_appends_explicit_structural_features():
    feature = json.dumps(
        {
            "primary_request": {"request": "결제 장애를 확인해 주세요."},
            "dynamic_context": {"context": "복구 중"},
            "structured_features": {
                "routing_contract": {
                    "schema_required": True,
                    "knowledge_enabled": True,
                    "downstream_contract_required": True,
                    "customer_facing": True,
                    "has_file_input": False,
                    "input_token_bucket": 2,
                },
                "rag": {"retrieved_chunk_count": 3},
            },
        },
        ensure_ascii=False,
    )

    vector, model_id = MultilingualE5ModelChoiceClassifier.vectorize(
        feature,
        artifact=None,
        embedder=_ProjectionEmbedder(),
    )

    assert model_id == "projection-test-encoder"
    assert len(vector) > E5_PROJECTION_DIMENSION
    assert vector[E5_PROJECTION_DIMENSION:] == pytest.approx(
        [
            1.0,
            1.0,
            3.0 / 16.0,
            1.0,
            1.0,
            2.0 / 3.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
        ]
    )


def test_local_first_requires_one_hundred_labels_and_fifty_recent_evaluations():
    healthy = {
        "judged_request_count": 100,
        "recent_judge_match_rate": 0.75,
        "recent_axis_accuracies": {
            "task_complexity": 0.86,
            "decision_impact": 0.88,
            "evidence_synthesis": 0.85,
        },
        "recent_axis_mean_errors": {
            "task_complexity": 0.4,
            "decision_impact": 0.3,
            "evidence_synthesis": 0.5,
        },
        "recent_judge_label_diversity": 3,
        "recent_local_prediction_diversity": 3,
        "recent_contract_pass_rate": 0.96,
        "recent_evaluation_sample_count": 50,
        "high_risk_underestimation_count": 0,
        "success_rate": 0.98,
        "schema_pass_rate": 0.99,
        "downstream_success_rate": 0.99,
        "fallback_rate": 0.01,
    }

    assert learning_mode_for(**healthy) == "local_first"
    assert learning_mode_for(**{**healthy, "judged_request_count": 99}) == "judge_first"
    assert learning_mode_for(
        **{**healthy, "recent_evaluation_sample_count": 49}
    ) == "judge_first"
    assert learning_mode_for(
        **{**healthy, "high_risk_underestimation_count": 1}
    ) == "judge_first"
    assert learning_mode_for(
        **{
            **healthy,
            "recent_axis_accuracies": {
                **healthy["recent_axis_accuracies"],
                "decision_impact": 0.84,
            },
        }
    ) == "judge_first"


def test_local_router_audit_sampling_is_deterministic_and_close_to_ten_percent():
    samples = [
        ModelRouter.should_audit_local_prediction(f"request-{index}")
        for index in range(1000)
    ]

    assert ModelRouter.should_audit_local_prediction("same-request") == (
        ModelRouter.should_audit_local_prediction("same-request")
    )
    assert 70 <= sum(samples) <= 130


def test_shared_e5_cache_isolated_by_immutable_revision(monkeypatch):
    MultilingualE5ModelChoiceClassifier._embedder_cache.clear()
    monkeypatch.setenv("MODEL_ROUTING_EMBEDDING_MODEL_REVISION", "revision-a")
    first = MultilingualE5ModelChoiceClassifier._shared_embedder(
        DEFAULT_MULTILINGUAL_E5_MODEL_ID
    )

    monkeypatch.setenv("MODEL_ROUTING_EMBEDDING_MODEL_REVISION", "revision-b")
    second = MultilingualE5ModelChoiceClassifier._shared_embedder(
        DEFAULT_MULTILINGUAL_E5_MODEL_ID
    )

    assert isinstance(first, MultilingualE5Embedder)
    assert isinstance(second, MultilingualE5Embedder)
    assert first is not second
    assert first.revision == "revision-a"
    assert second.revision == "revision-b"


class _WrongDimensionE5Embedder(MultilingualE5Embedder):
    def __init__(self):
        super().__init__(DEFAULT_MULTILINGUAL_E5_MODEL_ID)

    def encode(self, texts, *, mode="plain"):
        return [[0.1] * 384 for _text in texts]


def test_production_e5_dimension_mismatch_fails_before_projection():
    with pytest.raises(ValueError, match="768"):
        MultilingualE5ModelChoiceClassifier.vectorize(
            "차원 계약 검사",
            artifact=None,
            embedder=_WrongDimensionE5Embedder(),
        )
