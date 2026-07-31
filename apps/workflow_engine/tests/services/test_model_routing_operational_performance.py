from types import SimpleNamespace
from decimal import Decimal

from apps.workflow_engine.services.model_routing_operational_performance import (
    ModelRoutingOperationalPerformanceService,
)


def _node_run(*, status="success", schema_status="passed", fallback_used=False):
    return SimpleNamespace(
        status=status,
        retry_count=1,
        trace_metadata={
            "llm": {
                "selected_model": "gpt-4.1-mini",
                "schema_status": schema_status,
                "downstream_status": "passed",
                "fallback_used": fallback_used,
                "runtime_context": {"input_length_bucket": "short"},
            }
        },
        outputs=None,
    )


def test_operational_sample_contains_success_cost_and_contract_results():
    """운영 실행 한 건을 모델·입력 길이별 누적 성적으로 변환한다."""
    sample = ModelRoutingOperationalPerformanceService.sample_from_run(
        workflow_run=SimpleNamespace(status="success"),
        node_run=_node_run(),
        usage_log=SimpleNamespace(
            status="success",
            total_cost=0.0012,
            prompt_tokens=120,
            completion_tokens=40,
            latency_ms=850,
        ),
        usage_model_id="gpt-4.1-mini",
    )

    assert sample.model_id == "gpt-4.1-mini"
    assert sample.input_profile == "short"
    assert sample.run_count == 1
    assert sample.success_count == 1
    assert sample.schema_eval_count == 1
    assert sample.schema_pass_count == 1
    assert sample.downstream_eval_count == 1
    assert sample.downstream_success_count == 1
    assert sample.retry_count == 1
    assert sample.total_cost == 0.0012
    assert sample.total_tokens == 160
    assert sample.total_latency_ms == 850


def test_failed_operational_sample_is_kept_as_negative_quality_evidence():
    """실패 실행도 버리지 않고 해당 모델의 실패 성적으로 누적한다."""
    sample = ModelRoutingOperationalPerformanceService.sample_from_run(
        workflow_run=SimpleNamespace(status="failed"),
        node_run=_node_run(status="failed", schema_status="failed"),
        usage_log=None,
        usage_model_id=None,
    )

    assert sample.model_id == "gpt-4.1-mini"
    assert sample.run_count == 1
    assert sample.success_count == 0
    assert sample.schema_eval_count == 1
    assert sample.schema_pass_count == 0
    assert sample.downstream_eval_count == 1
    assert sample.downstream_success_count == 1


def test_schema_not_required_is_neutral_for_learning_and_metrics():
    workflow_run = SimpleNamespace(status="success")
    node_run = _node_run(schema_status="not_required")

    assert ModelRoutingOperationalPerformanceService.learning_contract_outcome(
        workflow_run=workflow_run,
        node_run=node_run,
    ) == (True, "contract_passed")

    sample = ModelRoutingOperationalPerformanceService.sample_from_run(
        workflow_run=workflow_run,
        node_run=node_run,
        usage_log=SimpleNamespace(
            status="success",
            total_cost=0,
            prompt_tokens=1,
            completion_tokens=1,
            latency_ms=10,
        ),
        usage_model_id="gpt-4.1-mini",
    )

    assert sample.schema_eval_count == 0
    assert sample.schema_pass_count == 0


def test_schema_not_evaluated_is_rejected_and_counted_as_failed_evaluation():
    workflow_run = SimpleNamespace(status="success")
    node_run = _node_run(schema_status="not_evaluated")

    assert ModelRoutingOperationalPerformanceService.learning_contract_outcome(
        workflow_run=workflow_run,
        node_run=node_run,
    ) == (False, "schema_failed")

    sample = ModelRoutingOperationalPerformanceService.sample_from_run(
        workflow_run=workflow_run,
        node_run=node_run,
        usage_log=SimpleNamespace(
            status="success",
            total_cost=0,
            prompt_tokens=1,
            completion_tokens=1,
            latency_ms=10,
        ),
        usage_model_id="gpt-4.1-mini",
    )

    assert sample.schema_eval_count == 1
    assert sample.schema_pass_count == 0


def test_operational_sample_normalizes_provider_model_prefix():
    """Google 계열 model id도 catalog와 같은 canonical id로 누적한다."""
    sample = ModelRoutingOperationalPerformanceService.sample_from_run(
        workflow_run=SimpleNamespace(status="success"),
        node_run=_node_run(),
        usage_log=SimpleNamespace(
            status="success",
            total_cost=0,
            prompt_tokens=1,
            completion_tokens=1,
            latency_ms=10,
        ),
        usage_model_id="models/Gemini-2.5-Flash",
    )

    assert sample.model_id == "gemini-2.5-flash"


def test_policy_evaluation_waits_until_accumulated_score_changes_materially():
    """작은 표본 변동은 정책 재평가를 예약하지 않고 유의미한 변화만 통과시킨다."""
    checkpoint = {
        "total_runs": 20,
        "models": {
            "gpt-4.1-mini:short": {
                "run_count": 20,
                "quality_score": 0.98,
                "avg_cost": 0.001,
                "avg_latency_ms": 800,
            }
        },
    }
    small_change = {
        "total_runs": 21,
        "models": {
            "gpt-4.1-mini:short": {
                "run_count": 21,
                "quality_score": 0.979,
                "avg_cost": 0.00098,
                "avg_latency_ms": 790,
            }
        },
    }
    material_change = {
        "total_runs": 24,
        "models": {
            "gpt-4.1-mini:short": {
                "run_count": 24,
                "quality_score": 0.98,
                "avg_cost": 0.00075,
                "avg_latency_ms": 790,
            }
        },
    }

    assert (
        ModelRoutingOperationalPerformanceService.has_material_change(
            checkpoint,
            small_change,
        )
        is False
    )
    assert (
        ModelRoutingOperationalPerformanceService.has_material_change(
            checkpoint,
            material_change,
        )
        is True
    )


def test_policy_profile_keeps_input_bucket_performance_separate(monkeypatch):
    """짧은 입력 성적이 중간·긴 입력 규칙의 근거로 섞이지 않는다."""
    rows = [
        SimpleNamespace(
            model_id="gpt-4.1-mini",
            input_profile="short",
            run_count=4,
            success_count=4,
            schema_pass_count=4,
            schema_eval_count=4,
            downstream_success_count=4,
            downstream_eval_count=4,
            fallback_count=0,
            retry_count=0,
            total_cost=Decimal("0.004"),
            total_tokens=400,
            total_latency_ms=2400,
        ),
        SimpleNamespace(
            model_id="gpt-4.1-mini",
            input_profile="long",
            run_count=2,
            success_count=1,
            schema_pass_count=1,
            schema_eval_count=2,
            downstream_success_count=1,
            downstream_eval_count=2,
            fallback_count=1,
            retry_count=1,
            total_cost=Decimal("0.008"),
            total_tokens=1800,
            total_latency_ms=4200,
        ),
    ]
    monkeypatch.setattr(
        ModelRoutingOperationalPerformanceService,
        "_rows",
        classmethod(lambda cls, db, *, policy_id: rows),
    )

    profile = ModelRoutingOperationalPerformanceService.profile_for_policy(
        object(),
        policy_id="policy-id",
    )

    assert profile.model_performance["gpt-4.1-mini"].run_count == 6
    short = profile.segment_performance["short"]["model_performance"]
    long = profile.segment_performance["long"]["model_performance"]
    assert short["gpt-4.1-mini"].success_rate == 1.0
    assert long["gpt-4.1-mini"].success_rate == 0.5


def test_candidate_contract_evidence_aggregates_schema_downstream_and_fallback(monkeypatch):
    rows = [
        SimpleNamespace(
            model_id="gpt-4o-mini",
            input_profile="short",
            run_count=4,
            success_count=4,
            schema_pass_count=4,
            schema_eval_count=4,
            downstream_success_count=4,
            downstream_eval_count=4,
            fallback_count=0,
        ),
        SimpleNamespace(
            model_id="gpt-4o-mini",
            input_profile="long",
            run_count=2,
            success_count=1,
            schema_pass_count=1,
            schema_eval_count=2,
            downstream_success_count=1,
            downstream_eval_count=2,
            fallback_count=1,
        ),
    ]
    monkeypatch.setattr(
        ModelRoutingOperationalPerformanceService,
        "_rows",
        classmethod(lambda cls, db, *, policy_id: rows),
    )

    evidence = ModelRoutingOperationalPerformanceService.candidate_contract_evidence(
        object(),
        policy_id="policy-id",
        candidate_model_ids=["gpt-4o-mini", "gpt-5.4"],
    )

    assert evidence == {
        "gpt-4o-mini": {
            "operational_run_count": 6,
            "operational_success_rate": 5 / 6,
            "operational_schema_pass_rate": 5 / 6,
            "operational_downstream_success_rate": 5 / 6,
            "operational_fallback_rate": 1 / 6,
        }
    }


def test_candidate_contract_evidence_can_be_limited_to_current_input_profile(monkeypatch):
    rows = [
        SimpleNamespace(
            model_id="gpt-4o-mini",
            input_profile="short",
            run_count=5,
            success_count=5,
            schema_pass_count=5,
            schema_eval_count=5,
            downstream_success_count=5,
            downstream_eval_count=5,
            fallback_count=0,
        ),
        SimpleNamespace(
            model_id="gpt-4o-mini",
            input_profile="long",
            run_count=5,
            success_count=1,
            schema_pass_count=1,
            schema_eval_count=5,
            downstream_success_count=1,
            downstream_eval_count=5,
            fallback_count=4,
        ),
    ]
    monkeypatch.setattr(
        ModelRoutingOperationalPerformanceService,
        "_rows",
        classmethod(lambda cls, db, *, policy_id: rows),
    )

    evidence = ModelRoutingOperationalPerformanceService.candidate_contract_evidence(
        object(),
        policy_id="policy-id",
        candidate_model_ids=["gpt-4o-mini"],
        input_profile="short",
    )

    assert evidence["gpt-4o-mini"]["operational_run_count"] == 5
    assert evidence["gpt-4o-mini"]["operational_success_rate"] == 1.0
    assert evidence["gpt-4o-mini"]["operational_fallback_rate"] == 0.0


def test_candidate_contract_evidence_falls_back_to_global_when_profile_is_immature(
    monkeypatch,
):
    rows = [
        SimpleNamespace(
            model_id="gpt-4o-mini",
            input_profile="short",
            run_count=1,
            success_count=1,
            schema_pass_count=1,
            schema_eval_count=1,
            downstream_success_count=1,
            downstream_eval_count=1,
            fallback_count=0,
        ),
        SimpleNamespace(
            model_id="gpt-4o-mini",
            input_profile="medium",
            run_count=5,
            success_count=4,
            schema_pass_count=4,
            schema_eval_count=5,
            downstream_success_count=4,
            downstream_eval_count=5,
            fallback_count=1,
        ),
    ]
    monkeypatch.setattr(
        ModelRoutingOperationalPerformanceService,
        "_rows",
        classmethod(lambda cls, db, *, policy_id: rows),
    )

    evidence = ModelRoutingOperationalPerformanceService.candidate_contract_evidence(
        object(),
        policy_id="policy-id",
        candidate_model_ids=["gpt-4o-mini"],
        input_profile="short",
        minimum_profile_run_count=5,
    )

    assert evidence["gpt-4o-mini"] == {
        "operational_run_count": 6,
        "operational_success_rate": 5 / 6,
        "operational_schema_pass_rate": 5 / 6,
        "operational_downstream_success_rate": 5 / 6,
        "operational_fallback_rate": 1 / 6,
    }


def test_learning_contract_accepts_clean_success_and_rejects_fallback_or_contract_failure():
    workflow_run = SimpleNamespace(status="success")
    clean_run = SimpleNamespace(
        status="success",
        trace_metadata={
            "llm": {"schema_status": "passed", "downstream_status": "compatible"}
        },
        outputs={"metadata": {"model_routing": {"fallback_used": False}}},
    )

    assert ModelRoutingOperationalPerformanceService.learning_contract_outcome(
        workflow_run=workflow_run,
        node_run=clean_run,
    ) == (True, "contract_passed")

    fallback_run = SimpleNamespace(
        status="success",
        trace_metadata={
            "llm": {
                "schema_status": "passed",
                "downstream_status": "compatible",
                "fallback_used": True,
            }
        },
        outputs={},
    )
    assert ModelRoutingOperationalPerformanceService.learning_contract_outcome(
        workflow_run=workflow_run,
        node_run=fallback_run,
    ) == (False, "fallback_used")

    schema_failed_run = SimpleNamespace(
        status="success",
        trace_metadata={"llm": {"schema_status": "failed"}},
        outputs={},
    )
    assert ModelRoutingOperationalPerformanceService.learning_contract_outcome(
        workflow_run=workflow_run,
        node_run=schema_failed_run,
    ) == (False, "schema_failed")
