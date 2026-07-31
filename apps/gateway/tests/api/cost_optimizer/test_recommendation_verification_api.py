from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from apps.gateway.api.v1.endpoints import workflow as workflow_endpoint
from apps.gateway.auth.dependencies import get_current_user
from apps.gateway.main import app
from apps.gateway.services.cost_optimizer_recommendation_verification_service import (
    CostOptimizerRecommendationVerificationService,
    RecommendationVerificationClaim,
)
from apps.shared.db.session import get_db


def _workflow_with_llm_node(workflow_id, organization_id):
    return SimpleNamespace(
        id=workflow_id,
        organization_id=organization_id,
        app_id=uuid4(),
        graph={
            "nodes": [
                {"id": "start", "type": "startNode", "data": {}},
                {
                    "id": "llm-triage",
                    "type": "llmNode",
                    "data": {
                        "model_id": "gpt-4.1",
                        "parameters": {"max_tokens": 1600},
                    },
                }
            ],
            "edges": [
                {"id": "start-llm", "source": "start", "target": "llm-triage"}
            ],
        },
    )


class TestRecommendationInlineVerificationApi:
    def setup_method(self):
        self.client = TestClient(app)
        self.replay_existing_patcher = patch.object(
            workflow_endpoint.CostOptimizerRecommendationVerificationService,
            "replay_existing",
            return_value=None,
        )
        self.replay_existing = self.replay_existing_patcher.start()

    def teardown_method(self):
        self.replay_existing_patcher.stop()
        app.dependency_overrides = {}

    def test_fr13_verify_returns_modal_contract_for_latest_success_baseline(self):
        workflow_id = uuid4()
        organization_id = uuid4()
        user_id = uuid4()
        db = SimpleNamespace()
        workflow = _workflow_with_llm_node(workflow_id, organization_id)
        expected = {
            "verification_status": "completed",
            "comparison_id": str(uuid4()),
            "candidate_id": str(uuid4()),
            "baseline": {
                "label": "최신 비교 가능한 성공 기록",
                "workflow_node_run_id": str(uuid4()),
                "model": "gpt-4.1",
                "metrics": {"cost": 0.012, "latency_ms": 2400, "total_tokens": 900},
            },
            "candidate": {
                "status": "success",
                "model": "gpt-4.1-mini",
                "metrics": {"cost": 0.002, "latency_ms": 1100, "total_tokens": 520},
            },
            "metrics": {
                "cost": {"baseline": 0.012, "candidate": 0.002, "delta": -0.01},
                "latency_ms": {"baseline": 2400, "candidate": 1100, "delta": -1300},
                "total_tokens": {"baseline": 900, "candidate": 520, "delta": -380},
            },
            "quality_evaluation": {
                "status": "completed",
                "baseline": {"score": 76},
                "candidate": {"score": 79},
                "delta": 3,
                "dimensions": {},
                "confidence": "high",
                "safe_summary": "두 출력 모두 요청을 충족합니다.",
            },
            "schema_validation": {"status": "passed", "issues": []},
            "downstream_compatibility": {"state": "compatible"},
            "incurred_cost": {
                "candidate_execution_cost": 0.002,
                "quality_judge_cost": 0.0003,
                "total_new_cost": 0.0023,
            },
            "apply": {
                "allowed": True,
                "requires_confirmation": False,
                "reasons": [],
            },
        }
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with (
            patch(
                "apps.gateway.api.v1.endpoints.workflow.ensure_workflow_permission",
                return_value=workflow,
            ),
            patch(
                "apps.gateway.api.v1.endpoints.workflow._verify_cost_optimizer_recommendations",
                return_value=expected,
                create=True,
            ) as verify,
            patch(
                "apps.gateway.api.v1.endpoints.workflow."
                "_bind_and_preflight_authenticated_graph",
                return_value=workflow.graph,
            ),
        ):
            response = self.client.post(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage/"
                "cost-optimizer/recommendations/verify",
                headers={"Idempotency-Key": "fr13-verify-001"},
                json={
                    "recommendation_ids": ["max_tokens"],
                    "baseline_mode": "latest_success",
                },
            )

        assert response.status_code == 200
        assert response.json() == expected
        verify.assert_called_once()
        assert verify.call_args.kwargs["idempotency_key"] == "fr13-verify-001"
        assert verify.call_args.kwargs["request_body"].recommendation_ids == ["max_tokens"]

    def test_fr13_verify_returns_stale_without_starting_candidate_execution(self):
        workflow_id = uuid4()
        user_id = uuid4()
        db = SimpleNamespace()
        workflow = _workflow_with_llm_node(workflow_id, uuid4())
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with (
            patch(
                "apps.gateway.api.v1.endpoints.workflow.ensure_workflow_permission",
                return_value=workflow,
            ),
            patch(
                "apps.gateway.api.v1.endpoints.workflow._verify_cost_optimizer_recommendations",
                return_value={
                    "verification_status": "stale",
                    "comparison_id": None,
                    "candidate_id": None,
                    "apply": {
                        "allowed": False,
                        "requires_confirmation": False,
                        "reasons": ["recommendation_stale"],
                    },
                },
                create=True,
            ) as verify,
            patch(
                "apps.gateway.api.v1.endpoints.workflow."
                "_bind_and_preflight_authenticated_graph",
                return_value=workflow.graph,
            ),
        ):
            response = self.client.post(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage/"
                "cost-optimizer/recommendations/verify",
                headers={"Idempotency-Key": "fr13-stale-001"},
                json={
                    "recommendation_ids": ["max_tokens"],
                    "baseline_mode": "latest_success",
                },
            )

        assert response.status_code == 200
        assert response.json()["verification_status"] == "stale"
        assert response.json()["apply"]["allowed"] is False
        verify.assert_called_once()

    def test_fr13_completed_replay_skips_configuration_preflight(self):
        workflow_id = uuid4()
        user_id = uuid4()
        organization_id = uuid4()
        workflow = _workflow_with_llm_node(workflow_id, organization_id)
        workflow.graph = {"nodes": [], "edges": []}
        replay = {
            "verification_status": "completed",
            "comparison_id": str(uuid4()),
            "candidate_id": str(uuid4()),
            "apply": {
                "allowed": True,
                "requires_confirmation": False,
                "reasons": [],
            },
        }
        self.replay_existing.return_value = replay
        app.dependency_overrides[get_db] = lambda: SimpleNamespace()
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with (
            patch(
                "apps.gateway.api.v1.endpoints.workflow.ensure_workflow_permission",
                return_value=workflow,
            ),
            patch(
                "apps.gateway.api.v1.endpoints.workflow."
                "_ensure_workflow_matches_active_organization",
            ) as scope_check,
            patch(
                "apps.gateway.api.v1.endpoints.workflow."
                "_bind_and_preflight_authenticated_graph",
            ) as preflight,
            patch(
                "apps.gateway.api.v1.endpoints.workflow."
                "_verify_cost_optimizer_recommendations",
            ) as verify,
        ):
            response = self.client.post(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage/"
                "cost-optimizer/recommendations/verify",
                headers={
                    "Idempotency-Key": "fr13-replay-001",
                    "X-Organization-Id": str(organization_id),
                },
                json={
                    "recommendation_ids": ["max_tokens"],
                    "baseline_mode": "latest_success",
                },
            )

        assert response.status_code == 200
        assert response.json() == replay
        scope_check.assert_called_once()
        preflight.assert_not_called()
        verify.assert_not_called()

    def test_fr13_verify_requires_idempotency_key(self):
        app.dependency_overrides[get_db] = lambda: SimpleNamespace()
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uuid4())
        response = self.client.post(
            f"/api/v1/workflows/{uuid4()}/llm-nodes/llm-triage/"
            "cost-optimizer/recommendations/verify",
            json={
                "recommendation_ids": ["max_tokens"],
                "baseline_mode": "latest_success",
            },
        )

        assert response.status_code == 422

    def test_fr13_verify_runs_only_candidate_and_keeps_partial_when_judge_is_unavailable(self):
        workflow_id = uuid4()
        user_id = uuid4()
        db = SimpleNamespace()
        workflow = _workflow_with_llm_node(workflow_id, uuid4())
        baseline = {
            "baseline_id": str(uuid4()),
            "input": {"message": "정산 파일을 다시 생성해 주세요."},
            "output": {"text": "처리하겠습니다."},
            "run_started_at": "2026-07-11T10:00:00+00:00",
            "model": "gpt-4.1",
            "deployment_id": str(uuid4()),
            "usage": {
                "cost": 0.012,
                "latency_ms": 2400,
                "prompt_tokens": 510,
                "completion_tokens": 390,
                "total_tokens": 900,
            },
        }
        candidate = workflow_endpoint.CostOptimizerCandidateRequest(
            model_id="gpt-4.1-mini",
            parameters={"max_tokens": 800},
            output_format={"type": "text"},
        )
        experiment = SimpleNamespace(id=uuid4())
        candidate_row = SimpleNamespace(id=uuid4())
        verification = SimpleNamespace(id=uuid4())
        candidate_result = {
            "status": "success",
            "output": {"text": "정산 파일을 재생성하고 결과를 안내하겠습니다."},
            "usage": {
                "cost": 0.002,
                "latency_ms": 1100,
                "prompt_tokens": 280,
                "completion_tokens": 240,
                "total_tokens": 520,
            },
            "schema_validation": {"status": "skipped", "errors": []},
        }
        quality = {
            "status": "unavailable",
            "baseline": {"score": None},
            "candidate": {"score": None},
            "delta": None,
            "dimensions": {},
            "confidence": "unavailable",
            "safe_summary": "품질 평가에 사용할 수 있는 LLM credential/model이 없습니다.",
            "judge_cost": None,
            "judge_usage_log_id": None,
        }

        with (
            patch.object(
                workflow_endpoint.CostOptimizerRecommendationVerificationService,
                "claim",
                return_value=RecommendationVerificationClaim(record=verification),
            ),
            patch.object(
                workflow_endpoint.CostOptimizerRecommendationVerificationService,
                "complete",
            ) as complete,
            patch.object(
                workflow_endpoint.CostOptimizerParameterRecommendationService,
                "recommend",
                return_value={
                    "policy_version": "llm-parameter-recommendation-rules-v1",
                    "recommendations": [{"parameter_key": "max_tokens"}],
                },
            ),
            patch.object(
                workflow_endpoint,
                "_cost_optimizer_candidate_from_recommendations",
                return_value=(candidate, ["max_tokens"]),
            ),
            patch.object(
                workflow_endpoint,
                "_materialize_cost_optimizer_candidate_model_routing_policy",
                return_value=candidate,
            ),
            patch.object(workflow_endpoint, "_validate_cost_optimizer_candidate_shape"),
            patch.object(workflow_endpoint, "_ensure_cost_optimizer_candidate_knowledge_available"),
            patch.object(workflow_endpoint, "_ensure_cost_optimizer_candidate_models_available"),
            patch.object(
                workflow_endpoint,
                "get_cost_optimizer_latest_operation_baseline",
                return_value=baseline,
            ),
            patch.object(
                workflow_endpoint.DeploymentParameterOptimizationService,
                "ensure_validation_budget_available",
            ) as ensure_budget,
            patch.object(
                workflow_endpoint,
                "_create_cost_optimizer_comparison",
                return_value=(experiment, candidate_row),
            ),
            patch.object(
                workflow_endpoint,
                "_run_cost_optimizer_candidate",
                return_value=candidate_result,
            ) as run_candidate,
            patch.object(
                workflow_endpoint,
                "_resolve_cost_optimizer_downstream_compatibility",
                return_value={"state": "compatible", "checked_node_count": 2},
            ),
            patch.object(
                workflow_endpoint.CostOptimizerOutputQualityService,
                "evaluate",
                return_value=quality,
            ) as evaluate_quality,
            patch.object(
                workflow_endpoint,
                "_persist_cost_optimizer_comparison",
                return_value=experiment,
            ) as persist,
            patch.object(
                workflow_endpoint.DeploymentParameterOptimizationService,
                "record_validation_spend",
            ) as record_spend,
        ):
            result = workflow_endpoint._verify_cost_optimizer_recommendations(
                db=db,
                workflow=workflow,
                execution_graph=workflow.graph,
                node_id="llm-triage",
                current_user=SimpleNamespace(id=user_id),
                request=SimpleNamespace(),
                request_body=workflow_endpoint.CostOptimizerRecommendationVerifyRequest(
                    recommendation_ids=["max_tokens"],
                    baseline_mode="latest_success",
                ),
                idempotency_key="fr13-partial-001",
            )

        assert result["verification_status"] == "partial"
        assert result["incurred_cost"] == {
            "candidate_execution_cost": 0.002,
            "quality_judge_cost": None,
            "total_new_cost": 0.002,
            "currency": "USD",
        }
        assert result["schema_validation"] == {"status": "not_applicable", "issues": []}
        assert result["apply"]["allowed"] is True
        assert result["apply"]["requires_confirmation"] is True
        run_candidate.assert_called_once()
        evaluate_quality.assert_called_once()
        assert evaluate_quality.call_args.kwargs["candidate_result"]["input"] == baseline["input"]
        assert persist.call_args.kwargs["quality_evaluation"] == quality
        ensure_budget.assert_called_once_with(
            db,
            deployment_id=baseline["deployment_id"],
            node_id="llm-triage",
        )
        record_spend.assert_called_once_with(
            db,
            deployment_id=baseline["deployment_id"],
            node_id="llm-triage",
            amount_usd=0.002,
        )
        complete.assert_called_once()

    def test_fr13_verify_stale_fingerprint_does_not_create_candidate(self):
        workflow_id = uuid4()
        workflow = _workflow_with_llm_node(workflow_id, uuid4())
        verification = SimpleNamespace(id=uuid4())

        with (
            patch.object(
                workflow_endpoint.CostOptimizerRecommendationVerificationService,
                "claim",
                return_value=RecommendationVerificationClaim(record=verification),
            ),
            patch.object(
                workflow_endpoint.CostOptimizerRecommendationVerificationService,
                "complete",
            ) as complete,
            patch.object(
                workflow_endpoint.CostOptimizerParameterRecommendationService,
                "recommend",
                return_value={"policy_version": "current-policy", "recommendations": []},
            ),
            patch.object(workflow_endpoint, "_run_cost_optimizer_candidate") as run_candidate,
        ):
            result = workflow_endpoint._verify_cost_optimizer_recommendations(
                db=SimpleNamespace(),
                workflow=workflow,
                execution_graph=workflow.graph,
                node_id="llm-triage",
                current_user=SimpleNamespace(id=uuid4()),
                request=SimpleNamespace(),
                request_body=workflow_endpoint.CostOptimizerRecommendationVerifyRequest(
                    recommendation_ids=["max_tokens"],
                    baseline_mode="latest_success",
                    node_config_fingerprint="outdated-fingerprint",
                ),
                idempotency_key="fr13-stale-fingerprint-001",
            )

        assert result["verification_status"] == "stale"
        assert result["apply"]["reasons"] == ["recommendation_stale"]
        run_candidate.assert_not_called()
        complete.assert_called_once()

    def test_fr13_verify_stale_recommendation_payload_does_not_create_candidate(self):
        workflow_id = uuid4()
        workflow = _workflow_with_llm_node(workflow_id, uuid4())
        verification = SimpleNamespace(id=uuid4())

        with (
            patch.object(
                workflow_endpoint.CostOptimizerRecommendationVerificationService,
                "claim",
                return_value=RecommendationVerificationClaim(record=verification),
            ),
            patch.object(
                workflow_endpoint.CostOptimizerRecommendationVerificationService,
                "complete",
            ) as complete,
            patch.object(
                workflow_endpoint.CostOptimizerParameterRecommendationService,
                "recommend",
                return_value={
                    "policy_version": "current-policy",
                    "recommendation_fingerprint": "current-recommendations",
                    "recommendations": [],
                },
            ),
            patch.object(workflow_endpoint, "_run_cost_optimizer_candidate") as run_candidate,
        ):
            result = workflow_endpoint._verify_cost_optimizer_recommendations(
                db=SimpleNamespace(),
                workflow=workflow,
                execution_graph=workflow.graph,
                node_id="llm-triage",
                current_user=SimpleNamespace(id=uuid4()),
                request=SimpleNamespace(),
                request_body=workflow_endpoint.CostOptimizerRecommendationVerifyRequest(
                    recommendation_ids=["max_tokens"],
                    baseline_mode="latest_success",
                    recommendation_fingerprint="outdated-recommendations",
                ),
                idempotency_key="fr13-stale-recommendations-001",
            )

        assert result["verification_status"] == "stale"
        assert result["apply"]["reasons"] == ["recommendation_stale"]
        run_candidate.assert_not_called()
        complete.assert_called_once()

    def test_fr13_latest_baseline_uses_active_deployment_when_trace_config_was_redacted(
        self,
    ):
        workflow_id = uuid4()
        deployment_id = uuid4()
        workflow = _workflow_with_llm_node(workflow_id, uuid4())
        fingerprint = workflow_endpoint._node_config_fingerprint(
            workflow.graph["nodes"][0]["data"]
        )
        matching_old = {
            "baseline_id": str(uuid4()),
            "run_started_at": "2026-07-10T10:00:00+00:00",
            "deployment_id": str(deployment_id),
            "node_config_fingerprint": fingerprint,
            "input_available": True,
            "output_available": True,
            "usage_available": True,
            "compare_available": True,
        }
        redacted_fingerprint = workflow_endpoint._node_config_fingerprint(
            {
                "model_id": "gpt-4.1",
                "parameters": {"max_tokens": "[REDACTED]"},
            }
        )
        matching_new = {
            **matching_old,
            "baseline_id": str(uuid4()),
            "run_started_at": "2026-07-11T10:00:00+00:00",
            "node_config_fingerprint": redacted_fingerprint,
        }
        wrong_deployment = {
            **matching_new,
            "baseline_id": str(uuid4()),
            "deployment_id": str(uuid4()),
        }

        with (
            patch.object(
                workflow_endpoint,
                "_resolve_operation_cohort",
                return_value={"status": "active_deployment", "deployment_id": deployment_id},
            ),
            patch.object(
                workflow_endpoint,
                "_cost_optimizer_baseline_rows",
                return_value=[wrong_deployment, matching_old, matching_new],
            ),
        ):
            baseline = workflow_endpoint.get_cost_optimizer_latest_operation_baseline(
                SimpleNamespace(), workflow, "llm-triage"
            )

        assert baseline["baseline_id"] == matching_new["baseline_id"]

    def test_fr13_draft_deployment_mismatch_returns_stale_without_candidate_execution(
        self,
    ):
        workflow_id = uuid4()
        workflow = _workflow_with_llm_node(workflow_id, uuid4())
        verification = SimpleNamespace(id=uuid4())
        candidate = workflow_endpoint.CostOptimizerCandidateRequest(
            model_id="gpt-4.1-mini",
            parameters={"max_tokens": 800},
            output_format={"type": "text"},
        )

        with (
            patch.object(
                workflow_endpoint.CostOptimizerRecommendationVerificationService,
                "claim",
                return_value=RecommendationVerificationClaim(record=verification),
            ),
            patch.object(
                workflow_endpoint.CostOptimizerRecommendationVerificationService,
                "complete",
            ) as complete,
            patch.object(
                workflow_endpoint.CostOptimizerParameterRecommendationService,
                "recommend",
                return_value={
                    "policy_version": "llm-parameter-recommendation-rules-v1",
                    "recommendations": [{"parameter_key": "max_tokens"}],
                },
            ),
            patch.object(
                workflow_endpoint,
                "_cost_optimizer_candidate_from_recommendations",
                return_value=(candidate, ["max_tokens"]),
            ),
            patch.object(
                workflow_endpoint,
                "_materialize_cost_optimizer_candidate_model_routing_policy",
                return_value=candidate,
            ),
            patch.object(workflow_endpoint, "_validate_cost_optimizer_candidate_shape"),
            patch.object(
                workflow_endpoint,
                "_ensure_cost_optimizer_candidate_knowledge_available",
            ),
            patch.object(
                workflow_endpoint,
                "_ensure_cost_optimizer_candidate_models_available",
            ),
            patch.object(
                workflow_endpoint,
                "_resolve_operation_cohort",
                return_value={"status": "draft_not_deployed", "deployment_id": None},
            ),
            patch.object(
                workflow_endpoint,
                "_cost_optimizer_baseline_rows",
            ) as baseline_rows,
            patch.object(
                workflow_endpoint,
                "_run_cost_optimizer_candidate",
            ) as run_candidate,
        ):
            result = workflow_endpoint._verify_cost_optimizer_recommendations(
                db=SimpleNamespace(),
                workflow=workflow,
                execution_graph=workflow.graph,
                node_id="llm-triage",
                current_user=SimpleNamespace(id=uuid4()),
                request=SimpleNamespace(),
                request_body=workflow_endpoint.CostOptimizerRecommendationVerifyRequest(
                    recommendation_ids=["max_tokens"],
                    baseline_mode="latest_success",
                ),
                idempotency_key="fr13-draft-deployment-stale-001",
            )

        assert result["verification_status"] == "stale"
        assert result["apply"]["reasons"] == ["recommendation_stale"]
        baseline_rows.assert_not_called()
        run_candidate.assert_not_called()
        complete.assert_called_once()


class TestRecommendationVerificationIdempotency:
    def test_fr13_same_idempotency_key_replays_completed_response(self):
        class _Query:
            def __init__(self, db):
                self.db = db

            def filter(self, *args):
                return self

            def first(self):
                return self.db.record

        class _Db:
            record = None

            def query(self, *args):
                return _Query(self)

            def add(self, record):
                self.record = record

            def commit(self):
                pass

            def rollback(self):
                pass

        db = _Db()
        user_id = uuid4()
        workflow_id = uuid4()
        request_fingerprint = CostOptimizerRecommendationVerificationService.request_fingerprint(
            workflow_id=workflow_id,
            node_id="llm-triage",
            recommendation_ids=["max_tokens"],
            baseline_mode="latest_success",
        )

        first = CostOptimizerRecommendationVerificationService.claim(
            db,
            workflow_id=workflow_id,
            node_id="llm-triage",
            user_id=user_id,
            idempotency_key="fr13-idempotency-001",
            request_fingerprint=request_fingerprint,
        )
        response = {
            "verification_status": "completed",
            "comparison_id": str(uuid4()),
            "candidate_id": str(uuid4()),
        }
        CostOptimizerRecommendationVerificationService.complete(
            db,
            record=first.record,
            response=response,
            experiment_id=None,
            candidate_id=None,
        )

        replay = CostOptimizerRecommendationVerificationService.claim(
            db,
            workflow_id=workflow_id,
            node_id="llm-triage",
            user_id=user_id,
            idempotency_key="fr13-idempotency-001",
            request_fingerprint=request_fingerprint,
        )
        replay_without_claim = (
            CostOptimizerRecommendationVerificationService.replay_existing(
                db,
                user_id=user_id,
                idempotency_key="fr13-idempotency-001",
                request_fingerprint=request_fingerprint,
            )
        )

        assert replay.record.id == first.record.id
        assert replay.replay_response == response
        assert replay_without_claim == response
