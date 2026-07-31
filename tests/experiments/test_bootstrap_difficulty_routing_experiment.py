import pytest

from scripts.experiment_bootstrap_difficulty_routing import (
    AUTHENTICATED_EXPERIMENT_DEPLOYMENT_TYPE,
    RoutingObservation,
    _bootstrap_request_body,
    _local_http_auth_cookie_header,
    assert_rag_embedding_models_available,
    _deployment_create_body,
    _copy_llm_rag_configuration,
    build_cases,
    evaluate_routing_attempt,
    wait_for_bootstrap_classifier,
)


def test_local_docker_http_experiment_reuses_secure_login_cookie_only_locally():
    cookies = {"auth_token": "session-token"}

    assert _local_http_auth_cookie_header("http://gateway:8000", cookies) == (
        "auth_token=session-token"
    )
    assert _local_http_auth_cookie_header("http://localhost:8000", cookies) == (
        "auth_token=session-token"
    )
    assert _local_http_auth_cookie_header("https://nodease.example", cookies) is None
    assert _local_http_auth_cookie_header("http://nodease.example", cookies) is None


def test_routing_dataset_has_balanced_20_and_diverse_50_cases():
    routing_cases = build_cases(count=20, shuffle_seed=451)
    economics_cases = build_cases(count=50, shuffle_seed=451)

    assert len(routing_cases) == 20
    assert len(economics_cases) == 50
    assert {case.expected_difficulty for case in routing_cases} == {
        "economy",
        "balanced",
        "advanced",
    }
    assert all(case.question.strip() for case in economics_cases)
    assert len({case.question for case in economics_cases}) == 50
    assert max(
        sum(case.expected_difficulty == tier for case in routing_cases)
        for tier in {"economy", "balanced", "advanced"}
    ) - min(
        sum(case.expected_difficulty == tier for case in routing_cases)
        for tier in {"economy", "balanced", "advanced"}
    ) <= 1


def test_routing_attempt_rejects_default_model_lock_in():
    cases = build_cases(count=20, shuffle_seed=451)
    observations = [
        RoutingObservation(
            sequence=index,
            case=case,
            run_id=f"run-{index}",
            run_status="success",
            node_status="success",
            selected_model="gpt-4.1",
            predicted_difficulty=None,
            confidence=None,
            reason_code="bootstrap_difficulty_low_confidence",
            classification_status="fallback",
            fallback_used=False,
            cost=0.01,
            total_tokens=100,
            duration_seconds=1.0,
            rag_document_count=1,
        )
        for index, case in enumerate(cases, start=1)
    ]

    result = evaluate_routing_attempt(observations)

    assert result.passed is False
    assert result.distinct_model_count == 1
    assert result.classifier_fallback_rate == 1.0


def test_routing_attempt_accepts_balanced_multi_model_selection():
    cases = build_cases(count=20, shuffle_seed=451)
    model_by_tier = {
        "economy": "gpt-4o-mini",
        "balanced": "gpt-4.1-mini",
        "advanced": "gpt-4.1",
    }
    observations = [
        RoutingObservation(
            sequence=index,
            case=case,
            run_id=f"run-{index}",
            run_status="success",
            node_status="success",
            selected_model=model_by_tier[case.expected_difficulty],
            predicted_difficulty=case.expected_difficulty,
            confidence=0.8,
            reason_code=f"bootstrap_difficulty_{case.expected_difficulty}",
            classification_status="matched",
            fallback_used=False,
            cost=0.001,
            total_tokens=100,
            duration_seconds=1.0,
            rag_document_count=1,
        )
        for index, case in enumerate(cases, start=1)
    ]

    result = evaluate_routing_attempt(observations)

    assert result.passed is True
    assert result.difficulty_accuracy == 1.0
    assert result.distinct_model_count == 3


def test_routing_attempt_treats_planner_rule_as_successful_routing():
    """첫 배포 Planner 규칙 분기는 기본 모델 fallback으로 집계하지 않는다."""
    cases = build_cases(count=20, shuffle_seed=451)
    model_by_tier = {
        "economy": "gpt-4o-mini",
        "balanced": "gpt-4.1-mini",
        "advanced": "gpt-4.1",
    }
    observations = [
        RoutingObservation(
            sequence=index,
            case=case,
            run_id=f"run-{index}",
            run_status="success",
            node_status="success",
            selected_model=model_by_tier[case.expected_difficulty],
            predicted_difficulty=case.expected_difficulty,
            confidence=0.8,
            reason_code=f"bootstrap_planner_rule_{case.expected_difficulty}",
            classification_status="planner_rule",
            fallback_used=False,
            cost=0.001,
            total_tokens=100,
            duration_seconds=1.0,
            rag_document_count=1,
        )
        for index, case in enumerate(cases, start=1)
    ]

    result = evaluate_routing_attempt(observations)

    assert result.passed is True
    assert result.classifier_fallback_rate == 0.0


def test_routing_attempt_treats_individually_validated_planner_rule_as_successful_routing():
    """부분 검증 policy의 rule 분기도 기본 모델 fallback으로 집계하지 않는다."""
    cases = build_cases(count=20, shuffle_seed=451)
    observations = [
        RoutingObservation(
            sequence=index,
            case=case,
            run_id=f"run-{index}",
            run_status="success",
            node_status="success",
            selected_model="gpt-4o-mini",
            predicted_difficulty=case.expected_difficulty,
            confidence=0.8,
            reason_code=f"bootstrap_planner_rule_{case.expected_difficulty}",
            classification_status="validated_planner_rule",
            fallback_used=False,
            cost=0.001,
            total_tokens=100,
            duration_seconds=1.0,
            rag_document_count=1,
        )
        for index, case in enumerate(cases, start=1)
    ]

    result = evaluate_routing_attempt(observations)

    assert result.classifier_fallback_rate == 0.0


def test_routing_attempt_uses_reason_code_when_trace_omits_planner_status():
    """실제 trace의 optional status 누락은 기본 모델 fallback이 아니다."""
    cases = build_cases(count=20, shuffle_seed=451)
    model_by_tier = {
        "economy": "gpt-4o-mini",
        "balanced": "gpt-5-mini",
        "advanced": "gpt-5.4",
    }
    observations = [
        RoutingObservation(
            sequence=index,
            case=case,
            run_id=f"run-{index}",
            run_status="success",
            node_status="success",
            selected_model=model_by_tier[case.expected_difficulty],
            predicted_difficulty=case.expected_difficulty,
            confidence=0.8,
            reason_code=f"bootstrap_planner_rule_{case.expected_difficulty}",
            classification_status=None,
            fallback_used=False,
            cost=0.001,
            total_tokens=100,
            duration_seconds=1.0,
            rag_document_count=1,
        )
        for index, case in enumerate(cases, start=1)
    ]

    result = evaluate_routing_attempt(observations)

    assert result.passed is True
    assert result.classifier_fallback_rate == 0.0


def test_wait_for_bootstrap_classifier_blocks_deployment_until_ready():
    """실험은 pending artifact로 첫 요청을 보내면 안 된다."""
    responses = iter(
        [
            {"generation_summary": {"classifier_status": "pending"}},
            {"generation_summary": {"classifier_status": "ready"}},
        ]
    )

    class _Client:
        def get_json(self, _path):
            return next(responses)

    result = wait_for_bootstrap_classifier(
        _Client(),
        workflow_id="workflow-1",
        node_id="llm-answer",
        timeout_seconds=10,
        poll_interval_seconds=0,
        clock=lambda: 0.0,
        sleep=lambda _seconds: None,
    )

    assert result["generation_summary"]["classifier_status"] == "ready"


def test_bootstrap_request_uses_canonical_metadata_from_saved_draft():
    request = _bootstrap_request_body(
        draft_metadata={
            "graph_hash": "a" * 64,
            "updated_at": "2026-07-18T08:30:00+00:00",
        },
        default_model_id="gpt-4.1-mini",
        fallback_model_id="gpt-4.1",
        initial_budget_usd=0.25,
    )

    assert request["expected_graph_hash"] == "a" * 64
    assert request["expected_updated_at"] == "2026-07-18T08:30:00+00:00"
    assert request["default_model_id"] == "gpt-4.1-mini"


@pytest.mark.parametrize(
    "draft_metadata",
    [
        {},
        {"graph_hash": "short", "updated_at": "2026-07-18T08:30:00+00:00"},
        {"graph_hash": "a" * 64, "updated_at": "not-a-date"},
        {"graph_hash": "a" * 64, "updated_at": "2026-07-18T08:30:00"},
    ],
)
def test_bootstrap_request_rejects_missing_or_invalid_canonical_metadata(
    draft_metadata,
):
    with pytest.raises(RuntimeError, match="canonical"):
        _bootstrap_request_body(
            draft_metadata=draft_metadata,
            default_model_id="gpt-4.1-mini",
            fallback_model_id="gpt-4.1",
            initial_budget_usd=0.25,
        )


def test_experiment_clone_restores_the_source_llm_rag_contract():
    """앱 복제가 KB 연결을 비우더라도 실험은 원본과 같은 RAG 조건에서 실행한다."""
    source_graph = {
        "nodes": [
            {
                "id": "llm-answer",
                "data": {
                    "knowledgeBases": [{"id": "kb-company", "name": "공통 온보딩"}],
                    "knowledgeCollections": [{"id": "collection-1"}],
                    "topK": 4,
                    "scoreThreshold": 0.3,
                    "retrievedContextMaxChars": 12000,
                    "retrievedContextCompression": "extractive",
                    "dedupeRetrievedContext": True,
                    "includeSourceMetadata": True,
                },
            }
        ]
    }
    cloned_graph = {"nodes": [{"id": "llm-answer", "data": {"topK": 1}}]}

    restored = _copy_llm_rag_configuration(source_graph, cloned_graph)

    assert restored["nodes"][0]["data"] == source_graph["nodes"][0]["data"]


def test_private_rag_experiment_uses_authenticated_deployment_surface():
    """Private KB를 복원한 실험은 공개 webhook으로 배포하면 안 된다."""
    graph = {"nodes": [{"id": "llm-answer", "data": {"knowledgeBases": [{"id": "kb-1"}]}}]}

    request = _deployment_create_body(
        app_id="app-1",
        name="routing experiment",
        graph_snapshot=graph,
    )

    assert request["type"] == AUTHENTICATED_EXPERIMENT_DEPLOYMENT_TYPE
    assert request["graph_snapshot"] is graph


def test_rag_experiment_stops_before_live_runs_when_embedding_model_is_unavailable():
    """RAG가 검색 벡터를 만들 수 없으면 라우팅 실험을 시작하면 안 된다."""

    class _Client:
        def get_json(self, path):
            if path == "/api/v1/knowledge/kb-1":
                return {"id": "kb-1", "embedding_model": "text-embedding-3-small"}

        def get_json_list(self, path):
            assert path == "/api/v1/llm/my-embedding-models"
            return []

    source_graph = {
        "nodes": [
            {
                "id": "llm-answer",
                "data": {
                    "knowledgeBases": [{"id": "kb-1"}],
                    "knowledgeCollections": [],
                },
            }
        ]
    }

    try:
        assert_rag_embedding_models_available(_Client(), source_graph)
    except RuntimeError as exc:
        assert "text-embedding-3-small" in str(exc)
    else:
        raise AssertionError("missing embedding model must stop the experiment")


def test_rag_experiment_accepts_all_embedding_models_used_by_the_source_graph():
    class _Client:
        def get_json(self, path):
            if path == "/api/v1/knowledge/kb-1":
                return {"id": "kb-1", "embedding_model": "text-embedding-3-small"}

        def get_json_list(self, path):
            assert path == "/api/v1/llm/my-embedding-models"
            return [
                {"model_id_for_api_call": "text-embedding-3-small"},
                {"model_id_for_api_call": "text-embedding-3-large"},
            ]

    source_graph = {
        "nodes": [
            {
                "id": "llm-answer",
                "data": {
                    "knowledgeBases": [{"id": "kb-1"}],
                    "knowledgeCollections": [],
                },
            }
        ]
    }

    assert assert_rag_embedding_models_available(_Client(), source_graph) == {
        "text-embedding-3-small"
    }
