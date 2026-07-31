from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from apps.gateway.auth.dependencies import get_current_user
from apps.gateway.api.v1.endpoints import workflow as workflow_endpoint
from apps.gateway.application.agent_builder.graph_mutation_builder import (
    canonical_graph_hash,
)
from apps.gateway.main import app
from apps.gateway.services.workflow_service import WorkflowService
from apps.shared.db.models.cost_optimizer import (
    CostOptimizerCandidate,
    CostOptimizerExperiment,
)
from apps.shared.db.models.workflow import Workflow
from apps.shared.db.models.workflow_run import NodeRunStatus
from apps.shared.db.session import get_db


_TEST_UPDATED_AT = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)


def _workflow_with_nodes(workflow_id, organization_id, nodes):
    return SimpleNamespace(
        id=workflow_id,
        organization_id=organization_id,
        graph={
            "nodes": [
                {"position": {"x": 0, "y": index * 120}, **node}
                for index, node in enumerate(nodes)
            ],
            "edges": [],
            "viewport": {"x": 0, "y": 0, "zoom": 1},
        },
        updated_at=_TEST_UPDATED_AT,
    )


def _cas_payload(workflow, payload):
    updated_at = getattr(workflow, "updated_at", None) or _TEST_UPDATED_AT
    workflow.updated_at = updated_at
    return {
        **payload,
        "expected_graph_hash": canonical_graph_hash(workflow.graph),
        "expected_updated_at": updated_at.isoformat(),
    }


def _configure_workflow_lock_query(db, workflow):
    existing_query = db.query.return_value
    query = MagicMock()
    query.filter.return_value.with_for_update.return_value.first.return_value = workflow
    query.locked = True

    def query_for(model, *args, **kwargs):
        if model is Workflow:
            return query
        return existing_query

    db.query.side_effect = query_for
    return query


def _available_model_option(
    model_id,
    *,
    input_price_1k=None,
    output_price_1k=None,
):
    return SimpleNamespace(
        model_id_for_api_call=model_id,
        name=model_id,
        type="chat",
        is_active=True,
        input_price_1k=input_price_1k,
        output_price_1k=output_price_1k,
    )


def _available_model_options(*model_ids):
    return [_available_model_option(model_id) for model_id in model_ids]


def test_test_routing_context_does_not_reuse_an_ambiguous_active_deployment_policy(
    monkeypatch,
):
    workflow = _workflow_with_nodes(
        uuid4(),
        uuid4(),
        [
            {
                "id": "llm-router",
                "type": "llmNode",
                "data": {"model_id": "gpt-4.1", "auto_model_routing": True},
            }
        ],
    )
    snapshot = {
        "nodes": [
            {
                "id": "llm-router",
                "type": "llmNode",
                "data": {"model_id": "gpt-4.1", "auto_model_routing": True},
            }
        ]
    }
    monkeypatch.setattr(
        workflow_endpoint,
        "_active_deployments_for_workflow",
        lambda *_args: [
            SimpleNamespace(id=uuid4(), graph_snapshot=snapshot),
            SimpleNamespace(id=uuid4(), graph_snapshot=snapshot),
        ],
    )

    context = workflow_endpoint._test_routing_policy_context(
        MagicMock(), workflow=workflow, graph=workflow.graph
    )

    assert context["routing_policy_deployment_ids_by_node"] == {}
    assert context["routing_policy_ambiguous_node_ids"] == ["llm-router"]


def _configure_cost_optimizer_experiment_query(db, experiment):
    (
        db.query.return_value.options.return_value.filter.return_value.first.return_value
    ) = experiment


class TestCostOptimizerAvailabilityApi:
    def setup_method(self):
        self.client = TestClient(app)

    def teardown_method(self):
        app.dependency_overrides = {}

    def test_fr11_routing_preview_uses_execute_permission_and_returns_safe_summary(self):
        workflow_id = uuid4()
        user_id = uuid4()
        db = MagicMock()
        workflow = _workflow_with_nodes(
            workflow_id,
            uuid4(),
            [
                {
                    "id": "llm-triage",
                    "type": "llmNode",
                    "data": {"model_id": "gpt-4.1", "auto_model_routing": True},
                }
            ],
        )
        deployment = SimpleNamespace(id=uuid4(), version=2)
        preview = {
            "deployment_version": 2,
            "policy_version": "router-policy-v4",
            "decision_source": "matched_rule",
            "selected_model_id": "gpt-4o-mini",
            "fallback_model_id": "gpt-4.1-mini",
            "default_model_id": "gpt-4.1",
            "configured_fallback_model_id": "gpt-4.1-mini",
        "matched_rule_id": "route-routine-support",
        "reason_code": "validated_quality_floor_cost_reduction",
        "availability": "available",
        "strategy_id": None,
        "runtime_context": {},
        "draft_matches_deployment": False,
        "decision_factors": {},
        }
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        unsafe_preview = {
            **preview,
            "inputs": {"message": "이 값은 응답에 포함되면 안 됩니다."},
        }

        with patch(
            "apps.gateway.api.v1.endpoints.workflow.ensure_workflow_permission",
            return_value=workflow,
        ) as ensure_permission, patch(
            "apps.gateway.api.v1.endpoints.workflow._active_deployment_for_workflow",
            return_value=deployment,
        ), patch(
            "apps.gateway.api.v1.endpoints.workflow.ModelRoutingPreviewService.preview",
            return_value=unsafe_preview,
        ) as preview_service:
            response = self.client.post(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage/model-routing/preview",
                json={
                    "inputs": {
                        "message": "영수증을 다시 받고 싶습니다.",
                        "customerTier": "business",
                    }
                },
            )

        assert response.status_code == 200
        assert response.json() == preview
        assert "inputs" not in response.json()
        ensure_permission.assert_called_once_with(
            db, SimpleNamespace(id=user_id), str(workflow_id), "execute"
        )
        preview_service.assert_called_once_with(
            db,
            workflow=workflow,
            deployment=deployment,
            node_id="llm-triage",
            inputs={
                "message": "영수증을 다시 받고 싶습니다.",
                "customerTier": "business",
            },
        )

    def test_fr1_llm_node_availability_returns_available_for_builder(self):
        workflow_id = uuid4()
        organization_id = uuid4()
        user_id = uuid4()
        db = MagicMock()
        workflow = _workflow_with_nodes(
            workflow_id,
            organization_id,
            [
                {
                    "id": "llm-triage",
                    "type": "llmNode",
                    "position": {"x": 100, "y": 120},
                    "data": {"label": "티켓 처리 판단", "model_id": "gpt-4.1-mini"},
                }
            ],
        )

        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with patch(
            "apps.gateway.api.v1.endpoints.workflow.ensure_workflow_permission",
            return_value=workflow,
        ) as ensure_builder:
            response = self.client.get(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage"
                "/cost-optimizer/availability"
            )

        assert response.status_code == 200
        ensure_builder.assert_called_once_with(db, SimpleNamespace(id=user_id), str(workflow_id), "write")
        assert response.json() == {
            "available": True,
            "reason": None,
            "workflow_id": str(workflow_id),
            "node_id": "llm-triage",
            "node_type": "llmNode",
            "permission": {
                "can_compare": True,
                "can_apply": True,
                "required_auth_state": "builder",
            },
        }

    def test_fr1_non_llm_node_is_rejected(self):
        workflow_id = uuid4()
        organization_id = uuid4()
        user_id = uuid4()
        db = MagicMock()
        workflow = _workflow_with_nodes(
            workflow_id,
            organization_id,
            [
                {
                    "id": "start",
                    "type": "startNode",
                    "position": {"x": 0, "y": 0},
                    "data": {"label": "입력"},
                }
            ],
        )

        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with patch(
            "apps.gateway.api.v1.endpoints.workflow.ensure_workflow_permission",
            return_value=workflow,
        ):
            response = self.client.get(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/start"
                "/cost-optimizer/availability"
            )

        assert response.status_code == 400
        assert response.json()["detail"] == "cost_optimizer.not_llm_node"

    def test_fr1_missing_node_returns_resource_not_found(self):
        workflow_id = uuid4()
        organization_id = uuid4()
        user_id = uuid4()
        db = MagicMock()
        workflow = _workflow_with_nodes(
            workflow_id,
            organization_id,
            [
                {
                    "id": "llm-triage",
                    "type": "llmNode",
                    "position": {"x": 100, "y": 120},
                    "data": {"label": "티켓 처리 판단"},
                }
            ],
        )

        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with patch(
            "apps.gateway.api.v1.endpoints.workflow.ensure_workflow_permission",
            return_value=workflow,
        ):
            response = self.client.get(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/missing-node"
                "/cost-optimizer/availability"
            )

        assert response.status_code == 404
        assert response.json()["detail"] == "resource.not_found"

    def test_fr1_availability_requires_builder_permission(self):
        workflow_id = uuid4()
        user_id = uuid4()
        db = MagicMock()
        denied = HTTPException(status_code=403, detail="Forbidden")
        setattr(denied, "audit_recorded", True)

        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with patch(
            "apps.gateway.api.v1.endpoints.workflow.ensure_workflow_permission",
            side_effect=denied,
        ) as ensure_builder:
            response = self.client.get(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage"
                "/cost-optimizer/availability"
            )

        assert response.status_code == 403
        ensure_builder.assert_called_once()
        assert ensure_builder.call_args.args[3] == "write"


class TestModelRoutingPolicyApi:
    def setup_method(self):
        self.client = TestClient(app)

    def teardown_method(self):
        app.dependency_overrides = {}

    def test_fr11_policy_get_returns_persisted_active_policy(self):
        workflow_id = uuid4()
        user_id = uuid4()
        db = MagicMock()
        workflow = _workflow_with_nodes(
            workflow_id,
            uuid4(),
            [
                {
                    "id": "llm-triage",
                    "type": "llmNode",
                    "data": {"auto_model_routing": True},
                }
            ],
        )
        policy = SimpleNamespace(
            id=uuid4(),
            enabled=True,
            status="active",
            policy_version="router-policy-v2",
            active_policy={"default_model_id": "gpt-4.1-mini", "rules": []},
            pending_policy=None,
            refresh_every_runs=20,
            eligible_runs_since_last_refresh=7,
            last_refresh_result="applied",
            last_refreshed_at=None,
        )
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with patch(
            "apps.gateway.api.v1.endpoints.workflow.ensure_workflow_permission",
            return_value=workflow,
        ), patch(
            "apps.gateway.api.v1.endpoints.workflow._get_model_routing_policy_for_workflow",
            return_value=policy,
        ):
            response = self.client.get(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage/model-routing/policy"
            )

        assert response.status_code == 200
        body = response.json()
        assert body["policy_id"] == str(policy.id)
        assert body["status"] == "active"
        assert body["refresh"]["eligible_runs_since_last_refresh"] == 7
        assert body["refresh"]["next_refresh_after_runs"] == 13

    def test_fr11_latest_decision_summary_returns_safe_routing_reason(self):
        """최근 실행의 라우팅 결과만 반환하고 입력 원문은 반환하지 않는다."""
        deployment_id = uuid4()
        policy = SimpleNamespace(deployment_id=deployment_id, node_id="llm-triage")
        node_run = SimpleNamespace(
            finished_at=datetime(2026, 7, 18, 9, 30, tzinfo=timezone.utc),
            outputs={
                "input": {"message": "이 값은 API 응답에 포함되면 안 됩니다."},
                "metadata": {
                    "model_routing": {
                        "selected_model": "gpt-4.1-mini",
                        "fallback_model": "gpt-4.1",
                        "fallback_used": False,
                        "decision_source": "runtime_judge",
                        "reason_code": "multi_constraint",
                        "judge": {"reason_short": "여러 조건 종합"},
                    }
                },
            },
        )
        db = MagicMock()
        (
            db.query.return_value.join.return_value.filter.return_value.filter.return_value.order_by.return_value.first.return_value
        ) = node_run

        summary = workflow_endpoint._model_routing_latest_decision_summary(db, policy)

        assert summary == {
            "selected_model_id": "gpt-4.1-mini",
            "fallback_model_id": "gpt-4.1",
            "fallback_used": False,
            "decision_source": "runtime_judge",
            "reason_code": "multi_constraint",
            "reason_label": "여러 조건 종합",
            "created_at": "2026-07-18T09:30:00+00:00",
        }

    def test_fr11_latest_decision_summary_supports_first_judge_routed_run(self):
        """정책 행이 없더라도 첫 Judge 실행의 선택 사유를 조회한다."""
        deployment_id = uuid4()
        node_run = SimpleNamespace(
            finished_at=datetime(2026, 7, 18, 9, 31, tzinfo=timezone.utc),
            outputs={
                "metadata": {
                    "model_routing": {
                        "selected_model": "gpt-4o-mini",
                        "fallback_used": False,
                        "decision_source": "runtime_judge",
                        "reason_code": "simple_response",
                    }
                }
            },
        )
        db = MagicMock()
        (
            db.query.return_value.join.return_value.filter.return_value.filter.return_value.order_by.return_value.first.return_value
        ) = node_run

        summary = workflow_endpoint._model_routing_latest_decision_summary(
            db,
            None,
            deployment_id=deployment_id,
            node_id="llm-triage",
        )

        assert summary is not None
        assert summary["selected_model_id"] == "gpt-4o-mini"
        assert summary["reason_label"] == "간단한 응답 처리"

    def test_fr11_policy_patch_updates_default_and_fallback_models(self):
        """규칙에 맞지 않는 요청의 기본 모델은 draft와 persisted policy에 함께 저장한다."""
        workflow_id = uuid4()
        user_id = uuid4()
        db = MagicMock()
        workflow = _workflow_with_nodes(
            workflow_id,
            uuid4(),
            [
                {
                    "id": "llm-triage",
                    "type": "llmNode",
                    "data": {
                        "auto_model_routing": True,
                        "model_id": "gpt-4.1",
                        "fallback_model_id": "gpt-4o-mini",
                    },
                }
            ],
        )
        policy = SimpleNamespace(
            id=uuid4(),
            enabled=True,
            status="active",
            policy_version="router-policy-v4",
            active_policy={
                "default_model_id": "gpt-4.1",
                "fallback_model_id": "gpt-4o-mini",
                "rules": [],
            },
            pending_policy=None,
            refresh_every_runs=20,
            eligible_runs_since_last_refresh=0,
            last_refresh_result=None,
            last_refreshed_at=None,
            validation_budget_usd=3,
            judge_user_id=None,
            execution_subject_user_id=user_id,
            organization_id=workflow.organization_id,
        )
        (
            db.query.return_value.filter.return_value.filter.return_value.first.return_value
        ) = None
        lock_query = _configure_workflow_lock_query(db, workflow)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with patch(
            "apps.gateway.api.v1.endpoints.workflow.ensure_workflow_permission",
            return_value=workflow,
        ) as ensure_builder, patch(
            "apps.gateway.api.v1.endpoints.workflow._get_model_routing_policy_for_workflow",
            return_value=policy,
        ), patch(
            "apps.gateway.api.v1.endpoints.workflow.WorkflowRuntimeLLMService."
            "get_runtime_available_model_ids_for_user",
            return_value=["gpt-4.1-mini", "gpt-4.1"],
        ):
            response = self.client.patch(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage/model-routing/policy",
                json=_cas_payload(
                    workflow,
                    {
                        "enabled": True,
                        "refresh_every_runs": 20,
                        "validation_budget_usd": 3,
                        "max_cohorts": 6,
                        "default_model_id": "gpt-4.1-mini",
                        "fallback_model_id": "gpt-4.1",
                    },
                ),
            )

        assert response.status_code == 200
        node_data = workflow.graph["nodes"][0]["data"]
        assert node_data["model_id"] == "gpt-4.1-mini"
        assert node_data["fallback_model_id"] == "gpt-4.1"
        assert policy.active_policy["default_model_id"] == "gpt-4.1-mini"
        assert policy.active_policy["fallback_model_id"] == "gpt-4.1"
        assert lock_query.locked is True
        assert ensure_builder.call_count == 2
        db.commit.assert_called_once()

    def test_fr11_policy_patch_uses_locked_cas_and_returns_metadata(self):
        workflow_id = uuid4()
        organization_id = uuid4()
        user_id = uuid4()
        db = MagicMock()
        workflow = _workflow_with_nodes(
            workflow_id,
            organization_id,
            [
                {
                    "id": "llm-triage",
                    "type": "llmNode",
                    "data": {
                        "model_id": "gpt-4.1-mini",
                        "auto_model_routing": False,
                    },
                }
            ],
        )
        lock_query = _configure_workflow_lock_query(db, workflow)
        (
            db.query.return_value.filter.return_value.filter.return_value.first.return_value
        ) = None
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with patch(
            "apps.gateway.api.v1.endpoints.workflow.ensure_workflow_permission",
            return_value=workflow,
        ) as ensure_builder, patch(
            "apps.gateway.api.v1.endpoints.workflow."
            "_get_model_routing_policy_for_workflow",
            return_value=None,
        ):
            response = self.client.patch(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage"
                "/model-routing/policy",
                json=_cas_payload(
                    workflow,
                    {"enabled": True, "refresh_every_runs": 45},
                ),
            )

        assert response.status_code == 200, response.json()
        assert lock_query.locked is True
        assert ensure_builder.call_count == 2
        assert workflow.graph["nodes"][0]["data"]["auto_model_routing"] is True
        assert (
            workflow.graph["nodes"][0]["data"]["model_routing_policy"]["refresh"][
                "refresh_every_runs"
            ]
            == 45
        )
        assert response.json()["graph_hash"] == canonical_graph_hash(workflow.graph)
        assert response.json()["updated_at"] == workflow.updated_at.isoformat()
        db.commit.assert_called_once()

    def test_fr11_policy_patch_rejects_stale_acknowledged_graph(self):
        workflow_id = uuid4()
        organization_id = uuid4()
        user_id = uuid4()
        db = MagicMock()
        stale_workflow = _workflow_with_nodes(
            workflow_id,
            organization_id,
            [
                {
                    "id": "llm-triage",
                    "type": "llmNode",
                    "data": {
                        "model_id": "gpt-4.1-mini",
                        "auto_model_routing": False,
                    },
                }
            ],
        )
        acknowledged_workflow = _workflow_with_nodes(
            workflow_id,
            organization_id,
            [
                {
                    "id": "llm-triage",
                    "type": "llmNode",
                    "data": {
                        "model_id": "gpt-4.1-mini",
                        "system_prompt": "agent builder acknowledged prompt",
                        "auto_model_routing": False,
                    },
                }
            ],
        )
        acknowledged_workflow.updated_at = datetime(
            2026, 7, 14, 12, 5, tzinfo=timezone.utc
        )
        lock_query = _configure_workflow_lock_query(db, acknowledged_workflow)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with patch(
            "apps.gateway.api.v1.endpoints.workflow.ensure_workflow_permission",
            return_value=stale_workflow,
        ):
            response = self.client.patch(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage"
                "/model-routing/policy",
                json=_cas_payload(
                    stale_workflow,
                    {"enabled": True, "refresh_every_runs": 45},
                ),
            )

        assert response.status_code == 409
        assert response.json()["detail"] == "stale_graph"
        assert lock_query.locked is True
        assert acknowledged_workflow.graph["nodes"][0]["data"] == {
            "model_id": "gpt-4.1-mini",
            "system_prompt": "agent builder acknowledged prompt",
            "auto_model_routing": False,
        }
        db.commit.assert_not_called()

    def test_fr11_policy_patch_rejects_model_unavailable_to_execution_subject(self):
        workflow_id = uuid4()
        organization_id = uuid4()
        editor_id = uuid4()
        execution_subject_id = uuid4()
        db = MagicMock()
        workflow = _workflow_with_nodes(
            workflow_id,
            organization_id,
            [
                {
                    "id": "llm-triage",
                    "type": "llmNode",
                    "data": {
                        "auto_model_routing": True,
                        "model_id": "gpt-4.1",
                    },
                }
            ],
        )
        lock_query = _configure_workflow_lock_query(db, workflow)
        policy = SimpleNamespace(
            enabled=True,
            active_policy={"default_model_id": "gpt-4.1", "rules": []},
            execution_subject_user_id=execution_subject_id,
            organization_id=organization_id,
            refresh_every_runs=20,
            validation_budget_usd=3,
            judge_user_id=None,
        )
        (
            db.query.return_value.filter.return_value.filter.return_value.first.return_value
        ) = None
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=editor_id)

        with patch(
            "apps.gateway.api.v1.endpoints.workflow.ensure_workflow_permission",
            return_value=workflow,
        ), patch(
            "apps.gateway.api.v1.endpoints.workflow._get_model_routing_policy_for_workflow",
            return_value=policy,
        ), patch(
            "apps.gateway.api.v1.endpoints.workflow.WorkflowRuntimeLLMService."
            "get_runtime_available_model_ids_for_user",
            return_value=["gpt-4.1"],
        ):
            response = self.client.patch(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage/model-routing/policy",
                json=_cas_payload(
                    workflow,
                    {
                        "enabled": True,
                        "default_model_id": "gpt-4.1-mini",
                        "fallback_model_id": "gpt-4.1",
                    },
                ),
            )

        assert response.status_code == 422
        assert response.json()["detail"] == "model_routing.policy_model_unavailable"
        assert policy.active_policy["default_model_id"] == "gpt-4.1"
        assert lock_query.locked is True
        db.commit.assert_not_called()

    def test_fr11_policy_patch_rejects_explicit_null_default_model(self):
        workflow_id = uuid4()
        user_id = uuid4()
        db = MagicMock()
        workflow = _workflow_with_nodes(
            workflow_id,
            uuid4(),
            [{"id": "llm-triage", "type": "llmNode", "data": {"model_id": "gpt-4.1"}}],
        )
        _configure_workflow_lock_query(db, workflow)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with patch(
            "apps.gateway.api.v1.endpoints.workflow.ensure_workflow_permission",
            return_value=workflow,
        ):
            response = self.client.patch(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage/model-routing/policy",
                json=_cas_payload(
                    workflow,
                    {"enabled": True, "default_model_id": None},
                ),
            )

        assert response.status_code == 422
        assert response.json()["detail"] == "model_routing.default_model_required"

    def test_fr11_manual_refresh_marks_policy_and_schedules_task(self):
        workflow_id = uuid4()
        user_id = uuid4()
        db = MagicMock()
        workflow = _workflow_with_nodes(
            workflow_id,
            uuid4(),
            [{"id": "llm-triage", "type": "llmNode", "data": {}}],
        )
        policy = SimpleNamespace(
            id=uuid4(), enabled=True, refresh_requested_at=None, status="active", judge_user_id=None
        )
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with patch(
            "apps.gateway.api.v1.endpoints.workflow.ensure_workflow_permission",
            return_value=workflow,
        ), patch(
            "apps.gateway.api.v1.endpoints.workflow._get_model_routing_policy_for_workflow",
            return_value=policy,
        ), patch(
            "apps.gateway.api.v1.endpoints.workflow.celery_app.send_task"
        ) as send_task:
            response = self.client.post(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage/model-routing/policy/refresh"
            )

        assert response.status_code == 200
        assert response.json() == {
            "policy_id": str(policy.id),
            "status": "refreshing",
            "trigger": "manual_refresh",
            "scheduled": True,
        }
        assert policy.status == "refreshing"
        assert policy.refresh_requested_at is not None
        assert policy.judge_user_id == user_id
        db.commit.assert_called_once()
        send_task.assert_called_once_with(
            "workflow.model_routing.refresh_policy",
            args=[str(policy.id), "manual_refresh"],
            kwargs={},
            argsrepr="[workflow arguments redacted]",
            kwargsrepr="{workflow arguments redacted}",
        )

    def test_fr11_manual_refresh_restores_policy_when_broker_publish_fails(self):
        workflow_id = uuid4()
        user_id = uuid4()
        db = MagicMock()
        workflow = _workflow_with_nodes(
            workflow_id,
            uuid4(),
            [{"id": "llm-triage", "type": "llmNode", "data": {}}],
        )
        policy = SimpleNamespace(
            id=uuid4(), enabled=True, refresh_requested_at=None, status="active", judge_user_id=None
        )
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with patch(
            "apps.gateway.api.v1.endpoints.workflow.ensure_workflow_permission",
            return_value=workflow,
        ), patch(
            "apps.gateway.api.v1.endpoints.workflow._get_model_routing_policy_for_workflow",
            return_value=policy,
        ), patch(
            "apps.gateway.api.v1.endpoints.workflow.celery_app.send_task",
            side_effect=[RuntimeError("broker unavailable"), None],
        ) as send_task:
            failed_response = self.client.post(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage/model-routing/policy/refresh"
            )

            assert failed_response.status_code == 503
            assert failed_response.json()["detail"] == "model_routing.refresh_schedule_failed"
            assert policy.status == "active"
            assert policy.refresh_requested_at is None
            assert policy.judge_user_id is None
            assert db.commit.call_count == 2
            db.rollback.assert_not_called()

            retry_response = self.client.post(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage/model-routing/policy/refresh"
            )

        assert retry_response.status_code == 200
        assert retry_response.json()["scheduled"] is True
        assert policy.status == "refreshing"
        assert policy.refresh_requested_at is not None
        assert send_task.call_count == 2
        assert db.commit.call_count == 3

    def test_fr11_manual_refresh_republishes_pending_request(self):
        workflow_id = uuid4()
        db = MagicMock()
        workflow = _workflow_with_nodes(
            workflow_id,
            uuid4(),
            [{"id": "llm-triage", "type": "llmNode", "data": {}}],
        )
        policy = SimpleNamespace(
            id=uuid4(),
            enabled=True,
            refresh_requested_at=datetime.now(timezone.utc),
            status="refreshing",
            judge_user_id=uuid4(),
        )
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uuid4())

        with patch(
            "apps.gateway.api.v1.endpoints.workflow.ensure_workflow_permission",
            return_value=workflow,
        ), patch(
            "apps.gateway.api.v1.endpoints.workflow._get_model_routing_policy_for_workflow",
            return_value=policy,
        ), patch(
            "apps.gateway.api.v1.endpoints.workflow.celery_app.send_task"
        ) as send_task:
            response = self.client.post(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage/model-routing/policy/refresh"
            )

        assert response.status_code == 200
        assert response.json()["scheduled"] is True
        send_task.assert_called_once_with(
            "workflow.model_routing.refresh_policy",
            args=[str(policy.id), "manual_refresh"],
            kwargs={},
            argsrepr="[workflow arguments redacted]",
            kwargsrepr="{workflow arguments redacted}",
        )
        db.commit.assert_not_called()

    def test_fr10_cost_optimizer_endpoints_require_builder_permission(self):
        workflow_id = uuid4()
        user_id = uuid4()
        baseline_id = uuid4()
        experiment_id = uuid4()
        candidate_id = uuid4()
        db = MagicMock()
        denied = HTTPException(status_code=403, detail="permission.denied")
        setattr(denied, "audit_recorded", True)
        candidate = {
            "model_id": "gpt-4.1-mini",
            "parameters": {"max_tokens": 800, "temperature": 0.1},
        }

        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        requests = [
            (
                "GET",
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage"
                "/cost-optimizer/baselines/latest",
                None,
            ),
            (
                "GET",
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage"
                "/cost-optimizer/baselines",
                None,
            ),
            (
                "GET",
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage"
                f"/cost-optimizer/experiments?baseline_id={baseline_id}",
                None,
            ),
            (
                "GET",
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage"
                f"/cost-optimizer/experiments/{experiment_id}/candidates/{candidate_id}",
                None,
            ),
            (
                "GET",
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage"
                "/cost-optimizer/parameter-recommendations",
                None,
            ),
            (
                "PATCH",
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage"
                "/cost-optimizer/apply-recommendations",
                {
                    "recommendation_ids": ["max_tokens"],
                    "expected_graph_hash": "a" * 64,
                    "expected_updated_at": _TEST_UPDATED_AT.isoformat(),
                },
            ),
            (
                "POST",
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage"
                "/cost-optimizer/compare",
                {"baseline_id": str(baseline_id), "candidate": candidate},
            ),
            (
                "PATCH",
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage"
                "/cost-optimizer/apply",
                {
                    "comparison_id": str(uuid4()),
                    "candidate_settings": candidate,
                    "acknowledge_downstream_warning": True,
                    "expected_graph_hash": "a" * 64,
                    "expected_updated_at": _TEST_UPDATED_AT.isoformat(),
                },
            ),
        ]

        with patch(
            "apps.gateway.api.v1.endpoints.workflow.ensure_workflow_permission",
            side_effect=denied,
        ) as ensure_builder:
            for method, url, body in requests:
                response = self.client.request(method, url, json=body)

                assert response.status_code == 403
                assert response.json()["detail"] == "permission.denied"

        assert ensure_builder.call_count == len(requests)
        assert all(call.args[3] == "write" for call in ensure_builder.call_args_list)

    def test_fr12_parameter_recommendations_calls_rule_service_for_llm_node(self):
        workflow_id = uuid4()
        organization_id = uuid4()
        user_id = uuid4()
        db = MagicMock()
        workflow = _workflow_with_nodes(
            workflow_id,
            organization_id,
            [
                {
                    "id": "llm-triage",
                    "type": "llmNode",
                    "data": {
                        "model_id": "gpt-4.1-mini",
                        "parameters": {"max_tokens": 2048, "temperature": 0.2},
                    },
                }
            ],
        )
        service_payload = {
            "analysis_stage": "insufficient_logs",
            "policy_version": "llm-parameter-recommendation-rules-v1",
            "recommendations": [],
            "warnings": [{"code": "operation_logs_insufficient"}],
            "profile": {"sample_count": 0},
        }

        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with patch(
            "apps.gateway.api.v1.endpoints.workflow.ensure_workflow_permission",
            return_value=workflow,
        ) as ensure_builder, patch(
            "apps.gateway.api.v1.endpoints.workflow."
            "CostOptimizerParameterRecommendationService.recommend",
            return_value=service_payload,
        ) as recommend:
            response = self.client.get(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage"
                "/cost-optimizer/parameter-recommendations"
            )

        assert response.status_code == 200
        assert response.json() == service_payload
        ensure_builder.assert_called_once()
        assert ensure_builder.call_args.args[3] == "write"
        recommend.assert_called_once_with(db, workflow=workflow, node_id="llm-triage")

    def test_fr12_apply_recommendations_blocks_experiment_required_patch(self):
        workflow_id = uuid4()
        organization_id = uuid4()
        user_id = uuid4()
        db = MagicMock()
        workflow = _workflow_with_nodes(
            workflow_id,
            organization_id,
            [
                {
                    "id": "llm-triage",
                    "type": "llmNode",
                    "data": {
                        "model_id": "gpt-4.1-mini",
                        "parameters": {"max_tokens": 2000, "temperature": 0.2},
                    },
                }
            ],
        )
        service_payload = {
            "analysis_stage": "recommendations_available",
            "policy_version": "llm-parameter-recommendation-rules-v1",
            "recommendations": [
                {
                    "recommendation_type": "llm_parameter",
                    "parameter_key": "max_tokens",
                    "current_value": 2000,
                    "suggested_value": 600,
                    "apply_mode": "experiment_required",
                    "candidate_patch": {"parameters": {"max_tokens": 600}},
                }
            ],
            "warnings": [],
            "profile": {"sample_count": 20},
        }

        _configure_workflow_lock_query(db, workflow)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with patch(
            "apps.gateway.api.v1.endpoints.workflow.ensure_workflow_permission",
            return_value=workflow,
        ) as ensure_builder, patch(
            "apps.gateway.api.v1.endpoints.workflow."
            "CostOptimizerParameterRecommendationService.recommend",
            return_value=service_payload,
        ) as recommend, patch(
            "apps.gateway.api.v1.endpoints.workflow.LLMService.get_my_available_models",
            return_value=_available_model_options("gpt-4.1-mini"),
        ):
            response = self.client.patch(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage"
                "/cost-optimizer/apply-recommendations",
                json=_cas_payload(
                    workflow,
                    {"recommendation_ids": ["max_tokens"]},
                ),
            )

        assert response.status_code == 400
        assert response.json()["detail"] == "cost_optimizer.recommendation_requires_experiment"
        assert workflow.graph["nodes"][0]["data"]["parameters"]["max_tokens"] == 2000
        assert workflow.graph["nodes"][0]["data"]["parameters"]["temperature"] == 0.2
        db.commit.assert_not_called()
        assert ensure_builder.call_count == 2
        assert ensure_builder.call_args.args[3] == "write"
        recommend.assert_called_once_with(db, workflow=workflow, node_id="llm-triage")

    def test_fr12_apply_recommendations_uses_active_policy_model_for_auto_routing_node(self):
        workflow_id = uuid4()
        organization_id = uuid4()
        user_id = uuid4()
        db = MagicMock()
        workflow = _workflow_with_nodes(
            workflow_id,
            organization_id,
            [
                {
                    "id": "llm-triage",
                    "type": "llmNode",
                    "data": {
                        "model_id": "",
                        "fallback_model_id": None,
                        "auto_model_routing": True,
                        "model_routing_policy": {
                            "status": "collecting",
                            "refresh": {"refresh_every_runs": 100},
                            "active_policy": {
                                "default_model_id": "gpt-4.1-mini",
                                "fallback_model_id": "gpt-4.1",
                            },
                        },
                        "parameters": {"max_tokens": 2000, "temperature": 0.2},
                    },
                }
            ],
        )
        service_payload = {
            "analysis_stage": "recommendations_available",
            "policy_version": "llm-parameter-recommendation-rules-v1",
            "recommendations": [
                {
                    "recommendation_type": "model_routing_policy",
                    "parameter_key": "model_routing.refresh_interval_shorten",
                    "current_value": 100,
                    "suggested_value": 20,
                    "apply_mode": "direct_policy_update",
                    "candidate_patch": {
                        "model_routing_policy": {
                            "refresh": {"refresh_every_runs": 20}
                        }
                    },
                }
            ],
            "warnings": [],
            "profile": {"sample_count": 20},
        }

        _configure_workflow_lock_query(db, workflow)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with patch(
            "apps.gateway.api.v1.endpoints.workflow.ensure_workflow_permission",
            return_value=workflow,
        ), patch(
            "apps.gateway.api.v1.endpoints.workflow."
            "CostOptimizerParameterRecommendationService.recommend",
            return_value=service_payload,
        ), patch(
            "apps.gateway.api.v1.endpoints.workflow.LLMService.get_my_available_models",
            return_value=[
                _available_model_option(
                    "gpt-4.1-mini",
                    input_price_1k=0.0001,
                    output_price_1k=0.0004,
                ),
                _available_model_option(
                    "gpt-5-mini",
                    input_price_1k=0.001,
                    output_price_1k=0.004,
                ),
                _available_model_option(
                    "gpt-4.1",
                    input_price_1k=0.01,
                    output_price_1k=0.03,
                ),
            ],
        ):
            response = self.client.patch(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage"
                "/cost-optimizer/apply-recommendations",
                json=_cas_payload(
                    workflow,
                    {"recommendation_ids": ["model_routing.refresh_interval_shorten"]},
                ),
            )

        assert response.status_code == 200
        assert response.json()["graph_hash"] == canonical_graph_hash(workflow.graph)
        assert response.json()["updated_at"] == workflow.updated_at.isoformat()
        assert "updated_draft_revision" not in response.json()
        node_data = workflow.graph["nodes"][0]["data"]
        assert node_data["model_id"] == "gpt-4.1-mini"
        assert node_data["fallback_model_id"] == "gpt-4.1"
        assert (
            node_data["model_routing_policy"]["refresh"]["refresh_every_runs"] == 20
        )
        db.commit.assert_called_once()

    def test_fr12_apply_recommendations_rejects_stale_acknowledged_graph(self):
        workflow_id = uuid4()
        organization_id = uuid4()
        user_id = uuid4()
        db = MagicMock()
        stale_workflow = _workflow_with_nodes(
            workflow_id,
            organization_id,
            [
                {
                    "id": "llm-triage",
                    "type": "llmNode",
                    "data": {
                        "model_id": "gpt-4.1-mini",
                        "parameters": {"max_tokens": 2000, "temperature": 0.2},
                    },
                }
            ],
        )
        acknowledged_workflow = _workflow_with_nodes(
            workflow_id,
            organization_id,
            [
                {
                    "id": "llm-triage",
                    "type": "llmNode",
                    "data": {
                        "model_id": "gpt-4.1-mini",
                        "system_prompt": "agent builder acknowledged prompt",
                        "parameters": {"max_tokens": 1600, "temperature": 0.2},
                    },
                }
            ],
        )
        acknowledged_workflow.updated_at = datetime(
            2026, 7, 14, 12, 5, tzinfo=timezone.utc
        )
        service_payload = {
            "analysis_stage": "recommendations_available",
            "policy_version": "llm-parameter-recommendation-rules-v1",
            "recommendations": [
                {
                    "recommendation_type": "llm_parameter",
                    "parameter_key": "max_tokens",
                    "current_value": 2000,
                    "suggested_value": 600,
                    "apply_mode": "direct_policy_update",
                    "candidate_patch": {"parameters": {"max_tokens": 600}},
                }
            ],
            "warnings": [],
            "profile": {"sample_count": 20},
        }
        lock_query = _configure_workflow_lock_query(db, acknowledged_workflow)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with patch(
            "apps.gateway.api.v1.endpoints.workflow.ensure_workflow_permission",
            return_value=stale_workflow,
        ), patch(
            "apps.gateway.api.v1.endpoints.workflow."
            "CostOptimizerParameterRecommendationService.recommend",
            return_value=service_payload,
        ), patch(
            "apps.gateway.api.v1.endpoints.workflow.LLMService.get_my_available_models",
            return_value=_available_model_options("gpt-4.1-mini"),
        ):
            response = self.client.patch(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage"
                "/cost-optimizer/apply-recommendations",
                json=_cas_payload(
                    stale_workflow,
                    {"recommendation_ids": ["max_tokens"]},
                ),
            )

        assert response.status_code == 409
        assert response.json()["detail"] == "stale_graph"
        assert lock_query.locked is True
        assert acknowledged_workflow.graph["nodes"][0]["data"] == {
            "model_id": "gpt-4.1-mini",
            "system_prompt": "agent builder acknowledged prompt",
            "parameters": {"max_tokens": 1600, "temperature": 0.2},
        }
        db.commit.assert_not_called()

    def test_fr12_apply_recommendations_updates_model_routing_policy_controls(self):
        workflow_id = uuid4()
        organization_id = uuid4()
        user_id = uuid4()
        db = MagicMock()
        workflow = _workflow_with_nodes(
            workflow_id,
            organization_id,
            [
                {
                    "id": "llm-triage",
                    "type": "llmNode",
                    "data": {
                        "model_id": "gpt-4.1-mini",
                        "fallback_model_id": "gpt-4.1",
                        "auto_model_routing": False,
                        "model_routing_policy": {
                            "status": "collecting",
                            "refresh": {"refresh_every_runs": 100},
                        },
                        "parameters": {"max_tokens": 2000, "temperature": 0.2},
                    },
                }
            ],
        )
        service_payload = {
            "analysis_stage": "recommendations_available",
            "policy_version": "llm-parameter-recommendation-rules-v1",
            "recommendations": [
                {
                    "recommendation_type": "model_routing_policy",
                    "parameter_key": "model_routing.enable",
                    "current_value": False,
                    "suggested_value": True,
                    "apply_mode": "direct_policy_update",
                    "candidate_patch": {"auto_model_routing": True},
                },
                {
                    "recommendation_type": "model_routing_policy",
                    "parameter_key": "model_routing.refresh_interval_shorten",
                    "current_value": 100,
                    "suggested_value": 20,
                    "apply_mode": "direct_policy_update",
                    "candidate_patch": {
                        "model_routing_policy": {
                            "refresh": {"refresh_every_runs": 20}
                        }
                    },
                },
            ],
            "warnings": [],
            "profile": {"sample_count": 20},
        }

        _configure_workflow_lock_query(db, workflow)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with patch(
            "apps.gateway.api.v1.endpoints.workflow.ensure_workflow_permission",
            return_value=workflow,
        ), patch(
            "apps.gateway.api.v1.endpoints.workflow."
            "CostOptimizerParameterRecommendationService.recommend",
            return_value=service_payload,
        ), patch(
            "apps.gateway.api.v1.endpoints.workflow.LLMService.get_my_available_models",
            return_value=[
                _available_model_option(
                    "gpt-4.1-mini",
                    input_price_1k=0.0001,
                    output_price_1k=0.0004,
                ),
                _available_model_option(
                    "gpt-5-mini",
                    input_price_1k=0.001,
                    output_price_1k=0.004,
                ),
                _available_model_option(
                    "gpt-4.1",
                    input_price_1k=0.01,
                    output_price_1k=0.03,
                ),
            ],
        ):
            response = self.client.patch(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage"
                "/cost-optimizer/apply-recommendations",
                json=_cas_payload(
                    workflow,
                    {
                        "recommendation_ids": [
                            "model_routing.enable",
                            "model_routing.refresh_interval_shorten",
                        ]
                    },
                ),
            )

        assert response.status_code == 200
        assert response.json()["graph_hash"] == canonical_graph_hash(workflow.graph)
        assert response.json()["updated_at"] == workflow.updated_at.isoformat()
        node_data = workflow.graph["nodes"][0]["data"]
        assert node_data["auto_model_routing"] is True
        assert (
            node_data["model_routing_policy"]["refresh"]["refresh_every_runs"] == 20
        )
        assert node_data["model_routing_policy"]["status"] == "collecting"
        assert (
            node_data["model_routing_policy"]["policy_version"]
            == "gateway-judge-first-v1"
        )
        assert (
            node_data["model_routing_policy"]["active_policy"]["default_model_id"]
            == "gpt-4.1-mini"
        )
        assert (
            node_data["model_routing_policy"]["active_policy"]["fallback_model_id"]
            == "gpt-4.1"
        )
        assert node_data["model_id"] == "gpt-4.1-mini"
        assert node_data["fallback_model_id"] == "gpt-4.1"
        db.commit.assert_called_once()


def _baseline_row(
    baseline_id=None,
    workflow_run_id=None,
    *,
    input_available=True,
    compare_available=True,
):
    baseline_id = baseline_id or uuid4()
    workflow_run_id = workflow_run_id or uuid4()
    return {
        "baseline_id": str(baseline_id),
        "baseline_source": "workflow_node_run",
        "source_workflow_node_run_id": str(baseline_id),
        "workflow_run_id": str(workflow_run_id),
        "workflow_id": "workflow-id",
        "node_id": "llm-triage",
        "run_started_at": "2026-07-04T00:00:00Z",
        "workflow_run_status": "success",
        "node_status": "success",
        "model": "gpt-4.1-mini",
        "cost": 0.0012,
        "total_tokens": 420,
        "latency_ms": 1800,
        "input_available": input_available,
        "output_available": True,
        "usage_available": True,
        "trace_available": True,
        "compare_available": compare_available,
        "unavailable_reason": None
        if compare_available
        else "input_payload_unavailable",
        "input_preview": "고객 문의를 분류해줘",
        "output_preview": "billing",
        "has_trace": True,
        "node_options": {
            "model_id": "gpt-4.1-mini",
            "system_prompt": "baseline system prompt",
            "parameters": {"max_tokens": 800, "temperature": 0.2},
        },
        "usage": {
            "model": "gpt-4.1-mini",
            "prompt_tokens": 300,
            "completion_tokens": 120,
            "total_tokens": 420,
            "cost": 0.0012,
            "latency_ms": 1800,
            "status": "success",
        },
        "trace": {
            "input_preview": "고객 문의를 분류해줘",
            "output_preview": "billing",
            "messages_preview": [],
            "rag_summary": None,
            "error_message": None,
        },
        "downstream_compatibility": {
            "state": "compatible",
            "label": "검증 가능",
            "message": "baseline downstream is compatible",
        },
    }


class TestCostOptimizerBaselinesApi:
    def setup_method(self):
        self.client = TestClient(app)

    def teardown_method(self):
        app.dependency_overrides = {}

    def test_fr2_latest_baseline_returns_most_recent_comparable_node_run(self):
        workflow_id = uuid4()
        organization_id = uuid4()
        user_id = uuid4()
        db = MagicMock()
        workflow = _workflow_with_nodes(
            workflow_id,
            organization_id,
            [
                {
                    "id": "llm-triage",
                    "type": "llmNode",
                    "position": {"x": 100, "y": 120},
                    "data": {"label": "티켓 처리 판단"},
                }
            ],
        )
        baseline = _baseline_row()
        baseline["downstream_snapshot"] = {"contracts": ["internal-only"]}

        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with (
            patch(
                "apps.gateway.api.v1.endpoints.workflow.ensure_workflow_permission",
                return_value=workflow,
            ) as ensure_builder,
            patch(
                "apps.gateway.api.v1.endpoints.workflow.get_cost_optimizer_latest_baseline",
                return_value=baseline,
                create=True,
            ) as get_latest,
        ):
            response = self.client.get(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage"
                "/cost-optimizer/baselines/latest"
            )

        assert response.status_code == 200
        ensure_builder.assert_called_once_with(db, SimpleNamespace(id=user_id), str(workflow_id), "write")
        get_latest.assert_called_once_with(db, workflow, "llm-triage")
        payload = response.json()
        assert payload["baseline"]["baseline_id"] == baseline["baseline_id"]
        assert payload["baseline"]["input_available"] is True
        assert payload["baseline"]["output_available"] is True
        assert payload["baseline"]["usage_available"] is True
        assert payload["baseline"]["compare_available"] is True
        assert "downstream_snapshot" not in payload["baseline"]
        assert "internal-only" not in response.text

    def test_fr2_latest_baseline_returns_no_baseline_when_comparable_log_is_missing(self):
        workflow_id = uuid4()
        organization_id = uuid4()
        user_id = uuid4()
        db = MagicMock()
        workflow = _workflow_with_nodes(
            workflow_id,
            organization_id,
            [{"id": "llm-triage", "type": "llmNode", "position": {"x": 0, "y": 0}, "data": {}}],
        )

        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with (
            patch(
                "apps.gateway.api.v1.endpoints.workflow.ensure_workflow_permission",
                return_value=workflow,
            ),
            patch(
                "apps.gateway.api.v1.endpoints.workflow.get_cost_optimizer_latest_baseline",
                side_effect=HTTPException(
                    status_code=400,
                    detail="cost_optimizer.no_baseline",
                ),
                create=True,
            ),
        ):
            response = self.client.get(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage"
                "/cost-optimizer/baselines/latest"
            )

        assert response.status_code == 400
        assert response.json()["detail"] == "cost_optimizer.no_baseline"

    def test_fr2_baseline_list_returns_pagination_and_non_comparable_rows_without_secrets(self):
        workflow_id = uuid4()
        organization_id = uuid4()
        user_id = uuid4()
        db = MagicMock()
        workflow = _workflow_with_nodes(
            workflow_id,
            organization_id,
            [{"id": "llm-triage", "type": "llmNode", "position": {"x": 0, "y": 0}, "data": {}}],
        )
        rows = [
            _baseline_row(),
            _baseline_row(input_available=False, compare_available=False),
        ]
        rows[0]["downstream_snapshot"] = {"contracts": ["internal-only"]}

        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with (
            patch(
                "apps.gateway.api.v1.endpoints.workflow.ensure_workflow_permission",
                return_value=workflow,
            ),
            patch(
                "apps.gateway.api.v1.endpoints.workflow.list_cost_optimizer_baselines",
                return_value={"total": 2, "limit": 20, "offset": 0, "items": rows},
                create=True,
            ) as list_baselines,
        ):
            response = self.client.get(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage"
                "/cost-optimizer/baselines"
            )

        assert response.status_code == 200
        list_baselines.assert_called_once()
        payload = response.json()
        assert payload["total"] == 2
        assert payload["items"][1]["input_available"] is False
        assert payload["items"][1]["compare_available"] is False
        assert payload["items"][1]["unavailable_reason"] == "input_payload_unavailable"
        serialized = response.text
        assert "api_key" not in serialized
        assert "encrypted_config" not in serialized
        assert "raw_payload" not in serialized
        assert "downstream_snapshot" not in serialized
        assert "internal-only" not in serialized

    def test_fr2_baseline_list_passes_search_filter_sort_and_pagination_options(self):
        workflow_id = uuid4()
        organization_id = uuid4()
        user_id = uuid4()
        db = MagicMock()
        workflow = _workflow_with_nodes(
            workflow_id,
            organization_id,
            [{"id": "llm-triage", "type": "llmNode", "position": {"x": 0, "y": 0}, "data": {}}],
        )

        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with (
            patch(
                "apps.gateway.api.v1.endpoints.workflow.ensure_workflow_permission",
                return_value=workflow,
            ),
            patch(
                "apps.gateway.api.v1.endpoints.workflow.list_cost_optimizer_baselines",
                return_value={"total": 0, "limit": 10, "offset": 20, "items": []},
                create=True,
            ) as list_baselines,
        ):
            response = self.client.get(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage"
                "/cost-optimizer/baselines"
                "?q=billing&model=gpt-4.1-mini"
                "&date_from=2026-07-01T00:00:00Z"
                "&date_to=2026-07-04T23:59:59Z"
                "&sort=cost_desc&compare_available=true&limit=10&offset=20"
            )

        assert response.status_code == 200
        list_baselines.assert_called_once_with(
            db,
            workflow,
            "llm-triage",
            q="billing",
            model="gpt-4.1-mini",
            date_from=datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc),
            date_to=datetime(2026, 7, 4, 23, 59, 59, tzinfo=timezone.utc),
            sort="cost_desc",
            compare_available=True,
            limit=10,
            offset=20,
        )


class TestCostOptimizerCompareApi:
    def setup_method(self):
        self.client = TestClient(app)
        self.configuration_preflight = patch(
            "apps.gateway.api.v1.endpoints.workflow."
            "_bind_and_preflight_authenticated_graph",
            side_effect=lambda _db, *, workflow, graph, principal_id: graph,
        )
        self.configuration_preflight.start()

    def teardown_method(self):
        self.configuration_preflight.stop()
        app.dependency_overrides = {}

    def test_fr3_compare_rejects_invalid_candidate_before_running_task(self):
        workflow_id = uuid4()
        organization_id = uuid4()
        user_id = uuid4()
        baseline_id = uuid4()
        db = MagicMock()
        workflow = _workflow_with_nodes(
            workflow_id,
            organization_id,
            [
                {
                    "id": "llm-triage",
                    "type": "llmNode",
                    "position": {"x": 0, "y": 0},
                    "data": {"model_id": "gpt-4.1"},
                }
            ],
        )
        baseline = _baseline_row(
            baseline_id=baseline_id,
            input_available=True,
            compare_available=True,
        )
        baseline["input"] = {"message": "baseline input"}

        class FakeTask:
            def get(self, timeout):
                return {"status": "success", "result": {}}

        invalid_candidates = [
            {"model_id": "", "parameters": {"max_tokens": 800, "temperature": 0.1}},
            {
                "model_id": "gpt-4.1-mini",
                "fallback_model_id": "gpt-4.1-mini",
                "parameters": {"max_tokens": 800, "temperature": 0.1},
            },
            {
                "model_id": "gpt-4.1-mini",
                "task_type": "classification",
                "parameters": {"max_tokens": 800, "temperature": 0.1},
            },
            {"model_id": "gpt-4.1-mini", "parameters": {"max_tokens": 0, "temperature": 0.1}},
            {"model_id": "gpt-4.1-mini", "parameters": {"max_tokens": 800, "temperature": 2.1}},
            {
                "model_id": "gpt-4.1-mini",
                "parameters": {"max_tokens": 800, "temperature": 0.1, "top_p": 1.1},
            },
            {
                "model_id": "gpt-4.1-mini",
                "parameters": {
                    "max_tokens": 800,
                    "temperature": 0.1,
                    "presence_penalty": -2.1,
                },
            },
            {
                "model_id": "gpt-4.1-mini",
                "parameters": {
                    "max_tokens": 800,
                    "temperature": 0.1,
                    "frequency_penalty": 2.1,
                },
            },
            {
                "model_id": "gpt-4.1-mini",
                "parameters": {
                    "max_tokens": 800,
                    "temperature": 0.1,
                    "stop": ["a", "b", "c", "d", "e"],
                },
            },
            {
                "model_id": "gpt-4.1-mini",
                "parameters": {"max_tokens": 800, "temperature": 0.1},
                "output_format": {"type": "xml"},
            },
            {
                "model_id": "gpt-4.1-mini",
                "parameters": {"max_tokens": 800, "temperature": 0.1},
                "output_format": {"type": "json", "schema": "not-object"},
            },
            {
                "model_id": "gpt-4.1-mini",
                "parameters": {"max_tokens": 800, "temperature": 0.1},
                "output_format": {
                    "type": "json",
                    "schema": {
                        "type": "object",
                        "properties": {"answer": {"type": "string"}},
                        "required": ["missing_field"],
                    },
                },
            },
            {
                "model_id": "gpt-4.1-mini",
                "parameters": {"max_tokens": 800, "temperature": 0.1},
                "knowledge": {"knowledge_base_ids": "kb-1"},
            },
            {
                "model_id": "gpt-4.1-mini",
                "parameters": {"max_tokens": 800, "temperature": 0.1},
                "knowledge": {"top_k": 0},
            },
            {
                "model_id": "gpt-4.1-mini",
                "parameters": {"max_tokens": 800, "temperature": 0.1},
                "knowledge": {"score_threshold": 1.1},
            },
            {
                "model_id": "gpt-4.1-mini",
                "parameters": {"max_tokens": 800, "temperature": 0.1},
                "knowledge": {"retrieved_context_compression": "medium"},
            },
            {
                "model_id": "gpt-4.1-mini",
                "parameters": {"max_tokens": 800, "temperature": 0.1},
                "knowledge": {"answer_grounding_check": "maybe"},
            },
            {
                "model_id": "gpt-4.1-mini",
                "parameters": {"max_tokens": 800, "temperature": 0.1},
                "knowledge": {"retrieved_context_max_chars": 0},
            },
            {
                "model_id": "gpt-4.1-mini",
                "system_prompt": "   ",
                "user_prompt": "",
                "assistant_prompt": "",
                "parameters": {"max_tokens": 800, "temperature": 0.1},
            },
            {
                "model_id": "gpt-4.1-mini",
                "referenced_variables": [
                    {"name": "message", "value_selector": ["start-1"]}
                ],
                "parameters": {"max_tokens": 800, "temperature": 0.1},
            },
        ]

        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with (
            patch(
                "apps.gateway.api.v1.endpoints.workflow.ensure_workflow_permission",
                return_value=workflow,
            ),
            patch(
                "apps.gateway.api.v1.endpoints.workflow.get_cost_optimizer_baseline_by_id",
                return_value=baseline,
                create=True,
            ),
            patch(
                "apps.gateway.api.v1.endpoints.workflow.celery_app.send_task",
                return_value=FakeTask(),
            ) as send_task,
        ):
            for candidate in invalid_candidates:
                response = self.client.post(
                    f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage"
                    "/cost-optimizer/compare",
                    json={
                        "baseline_id": str(baseline_id),
                        "candidate": candidate,
                    },
                )

                assert response.status_code == 400, {
                    "candidate": candidate,
                    "response": response.json(),
                }
                assert response.json()["detail"] == "cost_optimizer.invalid_candidate"

        send_task.assert_not_called()

    def test_fr3_compare_allows_unlimited_retrieved_context_candidate(self):
        workflow_id = uuid4()
        organization_id = uuid4()
        user_id = uuid4()
        baseline_id = uuid4()
        db = MagicMock()
        workflow = _workflow_with_nodes(
            workflow_id,
            organization_id,
            [
                {
                    "id": "llm-triage",
                    "type": "llmNode",
                    "position": {"x": 0, "y": 0},
                    "data": {"model_id": "gpt-4.1"},
                }
            ],
        )
        baseline = _baseline_row(
            baseline_id=baseline_id,
            input_available=True,
            compare_available=True,
        )
        baseline["input"] = {"message": "baseline input"}

        class FakeTask:
            def get(self, timeout):
                return {
                    "status": "success",
                    "result": {
                        "result": {"text": "candidate output"},
                        "node_results": {
                            "llm-triage": {
                                "status": "success",
                                "result": {"text": "candidate output"},
                                "duration_ms": 1200,
                            }
                        },
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
                "apps.gateway.api.v1.endpoints.workflow.get_cost_optimizer_baseline_by_id",
                return_value=baseline,
                create=True,
            ),
            patch(
                "apps.gateway.api.v1.endpoints.workflow.LLMService.get_my_available_models",
                return_value=_available_model_options("gpt-4.1-mini"),
            ),
            patch(
                "apps.gateway.api.v1.endpoints.workflow.celery_app.send_task",
                return_value=FakeTask(),
            ) as send_task,
        ):
            response = self.client.post(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage"
                "/cost-optimizer/compare",
                json={
                    "baseline_id": str(baseline_id),
                    "candidate": {
                        "model_id": "gpt-4.1-mini",
                        "parameters": {"max_tokens": 800, "temperature": 0.1},
                        "knowledge": {
                            "retrieved_context_max_chars": None,
                            "retrieved_context_compression": "off",
                            "answer_grounding_check": "off",
                        },
                    },
                },
            )

        assert response.status_code == 200
        send_task.assert_called_once()

    def test_fr3_compare_allows_auto_routing_candidate_with_active_policy(self):
        workflow_id = uuid4()
        organization_id = uuid4()
        user_id = uuid4()
        baseline_id = uuid4()
        db = MagicMock()
        workflow = _workflow_with_nodes(
            workflow_id,
            organization_id,
            [
                {
                    "id": "llm-triage",
                    "type": "llmNode",
                    "position": {"x": 0, "y": 0},
                    "data": {"model_id": ""},
                }
            ],
        )
        baseline = _baseline_row(
            baseline_id=baseline_id,
            input_available=True,
            compare_available=True,
        )
        baseline["input"] = {"message": "baseline input"}

        class FakeTask:
            def get(self, timeout):
                return {
                    "status": "success",
                    "result": {
                        "result": {"text": "candidate output"},
                        "node_results": {
                            "llm-triage": {
                                "status": "success",
                                "result": {
                                    "text": "candidate output",
                                    "model": "gpt-5-mini",
                                    "metadata": {
                                        "model_routing": {
                                            "decision_source": "active_policy",
                                            "selected_model": "gpt-5-mini",
                                            "fallback_model": "gpt-4.1",
                                            "reason_code": "policy_default",
                                        }
                                    },
                                },
                                "duration_ms": 1200,
                            }
                        },
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
                "apps.gateway.api.v1.endpoints.workflow.get_cost_optimizer_baseline_by_id",
                return_value=baseline,
                create=True,
            ),
            patch(
                "apps.gateway.api.v1.endpoints.workflow.LLMService.get_my_available_models",
                return_value=_available_model_options(
                    "gpt-5-mini",
                    "gpt-4.1",
                    "gpt-4.1-mini",
                ),
            ),
            patch(
                "apps.gateway.api.v1.endpoints.workflow.celery_app.send_task",
                return_value=FakeTask(),
            ) as send_task,
        ):
            response = self.client.post(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage"
                "/cost-optimizer/compare",
                json={
                    "baseline_id": str(baseline_id),
                    "candidate": {
                        "model_id": "",
                        "auto_model_routing": True,
                        "model_routing_policy": {
                            "status": "active",
                            "policy_version": "policy-v1",
                            "active_policy": {
                                "default_model_id": "gpt-5-mini",
                                "fallback_model_id": "gpt-4.1",
                                "rules": [
                                    {
                                        "id": "short-json",
                                        "when": {"output_format": "json"},
                                        "selected_model_id": "gpt-4.1-mini",
                                        "fallback_model_id": "gpt-4.1",
                                    }
                                ],
                            },
                        },
                        "parameters": {"max_tokens": 800, "temperature": 0.1},
                    },
                },
            )

        assert response.status_code == 200
        send_task.assert_called_once()

    def test_fr3_compare_materializes_default_policy_without_active_policy(self):
        workflow_id = uuid4()
        organization_id = uuid4()
        user_id = uuid4()
        baseline_id = uuid4()
        db = MagicMock()
        workflow = _workflow_with_nodes(
            workflow_id,
            organization_id,
            [
                {
                    "id": "llm-triage",
                    "type": "llmNode",
                    "position": {"x": 0, "y": 0},
                    "data": {"model_id": ""},
                }
            ],
        )
        baseline = _baseline_row(
            baseline_id=baseline_id,
            input_available=True,
            compare_available=True,
        )
        baseline["input"] = {"message": "baseline input"}

        class FakeTask:
            def get(self, timeout):
                return {
                    "status": "success",
                    "result": {
                        "result": {"text": "candidate output"},
                        "node_results": {
                            "llm-triage": {
                                "status": "success",
                                "result": {
                                    "text": "candidate output",
                                    "model": "gpt-5-mini",
                                    "metadata": {
                                        "model_routing": {
                                            "decision_source": "active_policy",
                                            "selected_model": "gpt-5-mini",
                                            "fallback_model": "gpt-4.1",
                                            "reason_code": "judge_bootstrap_required",
                                        }
                                    },
                                },
                                "duration_ms": 1200,
                            }
                        },
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
                "apps.gateway.api.v1.endpoints.workflow.get_cost_optimizer_baseline_by_id",
                return_value=baseline,
                create=True,
            ),
            patch(
                "apps.gateway.api.v1.endpoints.workflow.LLMService.get_my_available_models",
                return_value=[
                    _available_model_option(
                        "gpt-4.1-mini",
                        input_price_1k=0.0001,
                        output_price_1k=0.0004,
                    ),
                    _available_model_option(
                        "gpt-5-mini",
                        input_price_1k=0.001,
                        output_price_1k=0.004,
                    ),
                    _available_model_option(
                        "gpt-4.1",
                        input_price_1k=0.01,
                        output_price_1k=0.03,
                    ),
                ],
            ),
            patch(
                "apps.gateway.api.v1.endpoints.workflow.celery_app.send_task",
                return_value=FakeTask(),
            ) as send_task,
        ):
            response = self.client.post(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage"
                "/cost-optimizer/compare",
                json={
                    "baseline_id": str(baseline_id),
                    "candidate": {
                        "model_id": "",
                        "auto_model_routing": True,
                        "model_routing_policy": {"status": "collecting"},
                        "parameters": {"max_tokens": 800, "temperature": 0.1},
                    },
                },
            )

        assert response.status_code == 200
        send_task.assert_called_once()
        sent_graph = send_task.call_args.kwargs["args"][0]
        sent_llm_node = next(
            node for node in sent_graph["nodes"] if node["id"] == "llm-triage"
        )
        policy = sent_llm_node["data"]["model_routing_policy"]
        assert policy["status"] == "collecting"
        assert policy["policy_version"] == "gateway-judge-first-v1"
        assert policy["active_policy"]["default_model_id"] == "gpt-4.1-mini"
        assert policy["active_policy"]["fallback_model_id"] is None
        assert "rules" not in policy["active_policy"]
        assert (
            policy["active_policy"]["strategy_id"]
            == "judge_bootstrap_incremental_v1"
        )

    def test_fr3_compare_rejects_unusable_knowledge_base_before_running_task(self):
        workflow_id = uuid4()
        organization_id = uuid4()
        user_id = uuid4()
        baseline_id = uuid4()
        knowledge_base_id = uuid4()
        db = MagicMock()
        workflow = _workflow_with_nodes(
            workflow_id,
            organization_id,
            [
                {
                    "id": "llm-triage",
                    "type": "llmNode",
                    "position": {"x": 0, "y": 0},
                    "data": {"model_id": "gpt-4.1"},
                }
            ],
        )
        baseline = _baseline_row(
            baseline_id=baseline_id,
            input_available=True,
            compare_available=True,
        )
        baseline["input"] = {"message": "baseline input"}

        class FakeTask:
            def get(self, timeout):
                return {"status": "success", "result": {}}

        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with (
            patch(
                "apps.gateway.api.v1.endpoints.workflow.ensure_workflow_permission",
                return_value=workflow,
            ),
            patch(
                "apps.gateway.api.v1.endpoints.workflow.get_cost_optimizer_baseline_by_id",
                return_value=baseline,
                create=True,
            ),
            patch(
                "apps.gateway.api.v1.endpoints.workflow.has_knowledge_base_permission",
                return_value=False,
                create=True,
            ) as has_kb_permission,
            patch(
                "apps.gateway.api.v1.endpoints.workflow.celery_app.send_task",
                return_value=FakeTask(),
            ) as send_task,
        ):
            response = self.client.post(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage"
                "/cost-optimizer/compare",
                json={
                    "baseline_id": str(baseline_id),
                    "candidate": {
                        "model_id": "gpt-4.1-mini",
                        "parameters": {"max_tokens": 800, "temperature": 0.1},
                        "knowledge": {"knowledge_base_ids": [str(knowledge_base_id)]},
                    },
                },
            )

        assert response.status_code == 422
        assert response.json()["detail"] == "cost_optimizer.knowledge_unavailable"
        has_kb_permission.assert_called_once_with(
            db,
            user_id,
            str(knowledge_base_id),
            "use",
            organization_id,
        )
        send_task.assert_not_called()

    def test_fr3_compare_rejects_unavailable_model_before_running_task(self):
        workflow_id = uuid4()
        organization_id = uuid4()
        user_id = uuid4()
        baseline_id = uuid4()
        db = MagicMock()
        workflow = _workflow_with_nodes(
            workflow_id,
            organization_id,
            [
                {
                    "id": "llm-triage",
                    "type": "llmNode",
                    "position": {"x": 0, "y": 0},
                    "data": {"model_id": "gpt-4.1"},
                }
            ],
        )
        baseline = _baseline_row(
            baseline_id=baseline_id,
            input_available=True,
            compare_available=True,
        )
        baseline["input"] = {"message": "baseline input"}

        class FakeTask:
            def get(self, timeout):
                return {"status": "success", "result": {}}

        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with (
            patch(
                "apps.gateway.api.v1.endpoints.workflow.ensure_workflow_permission",
                return_value=workflow,
            ),
            patch(
                "apps.gateway.api.v1.endpoints.workflow.get_cost_optimizer_baseline_by_id",
                return_value=baseline,
                create=True,
            ),
            patch(
                "apps.gateway.api.v1.endpoints.workflow.LLMService.get_my_available_models",
                return_value=_available_model_options("gpt-4.1"),
            ) as get_available_models,
            patch(
                "apps.gateway.api.v1.endpoints.workflow.celery_app.send_task",
                return_value=FakeTask(),
            ) as send_task,
        ):
            response = self.client.post(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage"
                "/cost-optimizer/compare",
                json={
                    "baseline_id": str(baseline_id),
                    "candidate": {
                        "model_id": "gpt-4.1-mini",
                        "fallback_model_id": "gpt-4o-mini",
                        "parameters": {"max_tokens": 800, "temperature": 0.1},
                    },
                },
            )

        assert response.status_code == 422
        assert response.json()["detail"] == "cost_optimizer.model_unavailable"
        get_available_models.assert_called_once_with(db, user_id)
        send_task.assert_not_called()

    def test_fr4_fr5_compare_uses_baseline_input_and_runs_only_candidate_b(self):
        workflow_id = uuid4()
        organization_id = uuid4()
        app_id = uuid4()
        user_id = uuid4()
        baseline_id = uuid4()
        candidate_kb_id = uuid4()
        db = MagicMock()
        workflow = SimpleNamespace(
            id=workflow_id,
            organization_id=organization_id,
            app_id=app_id,
            graph={
                "nodes": [
                    {
                        "id": "llm-triage",
                        "type": "llmNode",
                        "position": {"x": 100, "y": 120},
                        "data": {
                            "title": "티켓 처리 판단",
                            "model_id": "gpt-4.1",
                            "system_prompt": "baseline system",
                            "user_prompt": "baseline user",
                            "parameters": {"temperature": 0.2},
                            "knowledgeBases": [
                                {"id": str(uuid4()), "name": "기존 KB"}
                            ],
                            "topK": 2,
                        },
                    }
                ],
                "edges": [],
            },
        )
        baseline = _baseline_row(
            baseline_id=baseline_id,
            workflow_run_id=uuid4(),
            input_available=True,
            compare_available=True,
        )
        baseline["input"] = {"message": "baseline input only"}
        sent_tasks = []

        class FakeTask:
            def get(self, timeout):
                return {
                    "status": "success",
                    "result": {
                        "llm-triage": {
                            "text": "candidate output",
                            "usage": {
                                "prompt_tokens": 10,
                                "completion_tokens": 5,
                            },
                            "cost": 0.0001,
                            "model": "gpt-4.1-mini",
                        }
                    },
                }

        def fake_send_task(name, args, kwargs=None, **options):
            sent_tasks.append({"name": name, "args": args, "kwargs": kwargs or {}})
            return FakeTask()

        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with (
            patch(
                "apps.gateway.api.v1.endpoints.workflow.ensure_workflow_permission",
                return_value=workflow,
            ) as ensure_builder,
            patch(
                "apps.gateway.api.v1.endpoints.workflow.get_cost_optimizer_baseline_by_id",
                return_value=baseline,
                create=True,
            ) as get_baseline,
            patch(
                "apps.gateway.api.v1.endpoints.workflow.LLMService.get_my_available_models",
                return_value=_available_model_options("gpt-4.1-mini"),
            ),
            patch(
                "apps.gateway.api.v1.endpoints.workflow.has_knowledge_base_permission",
                return_value=True,
                create=True,
            ) as has_kb_permission,
            patch(
                "apps.gateway.api.v1.endpoints.workflow.celery_app.send_task",
                side_effect=fake_send_task,
            ),
        ):
            response = self.client.post(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage"
                "/cost-optimizer/compare",
                json={
                    "baseline_id": str(baseline_id),
                    "inputs": {"message": "request input must be ignored"},
                    "candidate": {
                        "label": "B",
                        "model_id": "gpt-4.1-mini",
                        "system_prompt": "candidate system",
                        "user_prompt": "candidate user {{message}}",
                        "referenced_variables": [
                            {
                                "name": "message",
                                "value_selector": ["start-1", "message"],
                            }
                        ],
                        "parameters": {
                            "max_tokens": 800,
                            "temperature": 0.1,
                        },
                        "knowledge": {
                            "knowledge_base_ids": [str(candidate_kb_id)],
                            "top_k": 5,
                            "score_threshold": 0.65,
                            "dedupe_retrieved_context": True,
                            "retrieved_context_max_chars": 6000,
                            "retrieved_context_compression": "light",
                            "answer_grounding_check": "basic",
                        },
                    },
                },
            )

        assert response.status_code == 200
        ensure_builder.assert_called_once_with(
            db, SimpleNamespace(id=user_id), str(workflow_id), "write"
        )
        get_baseline.assert_called_once_with(
            db, workflow, "llm-triage", str(baseline_id)
        )
        has_kb_permission.assert_called_once_with(
            db,
            user_id,
            str(candidate_kb_id),
            "use",
            organization_id,
        )
        assert len(sent_tasks) == 1
        assert sent_tasks[0]["name"] == "workflow.execute"
        assert sent_tasks[0]["args"][1] == {"message": "baseline input only"}
        assert UUID(sent_tasks[0]["args"][2]["workflow_run_id"])
        assert sent_tasks[0]["args"][2]["execution_subject"] == {
            "type": "user",
            "id": str(user_id),
        }
        patched_graph = sent_tasks[0]["args"][0]
        assert [node["type"] for node in patched_graph["nodes"]] == [
            "startNode",
            "llmNode",
        ]
        assert patched_graph["edges"] == [
            {
                "id": "cost-optimizer-input-to-llm-triage",
                "source": "__cost_optimizer_baseline_input__",
                "target": "llm-triage",
            }
        ]
        patched_node = patched_graph["nodes"][1]
        assert patched_node["data"]["model_id"] == "gpt-4.1-mini"
        assert patched_node["data"]["system_prompt"] == "candidate system"
        assert patched_node["data"]["user_prompt"] == "candidate user {{message}}"
        assert patched_node["data"]["knowledgeBases"] == [
            {"id": str(candidate_kb_id), "name": ""}
        ]
        assert patched_node["data"]["topK"] == 5
        assert patched_node["data"]["scoreThreshold"] == 0.65
        assert patched_node["data"]["dedupeRetrievedContext"] is True
        assert patched_node["data"]["retrievedContextMaxChars"] == 6000
        assert patched_node["data"]["retrievedContextCompression"] == "light"
        assert patched_node["data"]["answerGroundingCheck"] == "basic"
        assert patched_node["data"]["referenced_variables"] == [
            {
                "name": "message",
                "value_selector": [
                    "__cost_optimizer_baseline_input__",
                    "start-1",
                    "message",
                ],
            }
        ]
        assert sent_tasks[0]["args"][2]["trigger_mode"] == "cost_optimizer_compare"

        payload = response.json()
        assert payload["baseline"]["input"] == {"message": "baseline input only"}
        assert payload["candidate"]["status"] == "success"
        assert UUID(payload["candidate"]["candidate_workflow_run_id"])
        assert payload["candidate"]["output"] == {
            "text": "candidate output",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "cost": 0.0001,
            "model": "gpt-4.1-mini",
        }
        assert payload["candidate"]["settings"]["referenced_variables"] == [
            {"name": "message", "value_selector": ["start-1", "message"]}
        ]

    def test_fr5_compare_runs_only_target_llm_with_baseline_node_input(self):
        workflow_id = uuid4()
        organization_id = uuid4()
        user_id = uuid4()
        baseline_id = uuid4()
        db = MagicMock()
        workflow = _workflow_with_nodes(
            workflow_id,
            organization_id,
            [
                {
                    "id": "webhook-ticket",
                    "type": "webhookTrigger",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "variable_mappings": [
                            {"variable_name": "message", "json_path": "message"}
                        ]
                    },
                },
                {
                    "id": "llm-triage",
                    "type": "llmNode",
                    "position": {"x": 100, "y": 0},
                    "data": {
                        "model_id": "gpt-4.1",
                        "user_prompt": "문의: {{ message }}",
                        "referenced_variables": [
                            {
                                "name": "message",
                                "value_selector": ["webhook-ticket", "message"],
                            }
                        ],
                    },
                },
                {
                    "id": "extract-ticket",
                    "type": "variableExtractionNode",
                    "position": {"x": 200, "y": 0},
                    "data": {
                        "source_selector": ["llm-triage", "text"],
                        "mappings": [{"json_path": "mailDraft"}],
                    },
                },
            ],
        )
        workflow.graph["edges"] = [
            {"id": "e1", "source": "webhook-ticket", "target": "llm-triage"},
            {"id": "e2", "source": "llm-triage", "target": "extract-ticket"},
        ]
        baseline = _baseline_row(
            baseline_id=baseline_id,
            input_available=True,
            compare_available=True,
        )
        baseline["input"] = {
            "webhook-ticket": {
                "message": "SLA 위반 가능성이 있는 장애입니다.",
            }
        }
        sent_tasks = []

        class FakeTask:
            def get(self, timeout):
                return {
                    "status": "success",
                    "result": {
                        "llm-triage": {
                            "text": '{"mailDraft": "확인했습니다."}',
                            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                            "cost": 0.0001,
                            "model": "gpt-4.1-mini",
                        }
                    },
                }

        def fake_send_task(name, args, kwargs, **options):
            sent_tasks.append({"name": name, "args": args, "kwargs": kwargs})
            return FakeTask()

        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with (
            patch(
                "apps.gateway.api.v1.endpoints.workflow.ensure_workflow_permission",
                return_value=workflow,
            ),
            patch(
                "apps.gateway.api.v1.endpoints.workflow.get_cost_optimizer_baseline_by_id",
                return_value=baseline,
                create=True,
            ),
            patch(
                "apps.gateway.api.v1.endpoints.workflow.LLMService.get_my_available_models",
                return_value=_available_model_options("gpt-4.1-mini"),
            ),
            patch(
                "apps.gateway.api.v1.endpoints.workflow.celery_app.send_task",
                side_effect=fake_send_task,
            ),
        ):
            response = self.client.post(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage"
                "/cost-optimizer/compare",
                json={
                    "baseline_id": str(baseline_id),
                    "candidate": {
                        "label": "B",
                        "model_id": "gpt-4.1-mini",
                        "user_prompt": "문의: {{ message }}",
                        "referenced_variables": [
                            {
                                "name": "message",
                                "value_selector": ["webhook-ticket", "message"],
                            }
                        ],
                        "parameters": {"max_tokens": 800, "temperature": 0.1},
                    },
                },
            )

        assert response.status_code == 200
        graph = sent_tasks[0]["args"][0]
        assert [node["id"] for node in graph["nodes"]] == [
            "__cost_optimizer_baseline_input__",
            "llm-triage",
        ]
        assert all(node["id"] != "webhook-ticket" for node in graph["nodes"])
        assert all(node["id"] != "extract-ticket" for node in graph["nodes"])
        assert sent_tasks[0]["args"][1] == baseline["input"]
        llm_node = graph["nodes"][1]
        assert llm_node["data"]["referenced_variables"] == [
            {
                "name": "message",
                "value_selector": [
                    "__cost_optimizer_baseline_input__",
                    "webhook-ticket",
                    "message",
                ],
            }
        ]

    def test_fr4_compare_rejects_baseline_without_restorable_input(self):
        workflow_id = uuid4()
        organization_id = uuid4()
        user_id = uuid4()
        baseline_id = uuid4()
        db = MagicMock()
        workflow = _workflow_with_nodes(
            workflow_id,
            organization_id,
            [
                {
                    "id": "llm-triage",
                    "type": "llmNode",
                    "position": {"x": 0, "y": 0},
                    "data": {"model_id": "gpt-4.1"},
                }
            ],
        )
        baseline = _baseline_row(
            baseline_id=baseline_id,
            input_available=False,
            compare_available=False,
        )

        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with (
            patch(
                "apps.gateway.api.v1.endpoints.workflow.ensure_workflow_permission",
                return_value=workflow,
            ),
            patch(
                "apps.gateway.api.v1.endpoints.workflow.get_cost_optimizer_baseline_by_id",
                return_value=baseline,
                create=True,
            ),
        ):
            response = self.client.post(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage"
                "/cost-optimizer/compare",
                json={
                    "baseline_id": str(baseline_id),
                    "candidate": {
                        "model_id": "gpt-4.1-mini",
                        "user_prompt": "candidate user",
                        "parameters": {"max_tokens": 800, "temperature": 0.1},
                    },
                },
            )

        assert response.status_code == 400
        assert response.json()["detail"] == "cost_optimizer.baseline_input_unavailable"

    def test_fr6_fr13_compare_response_includes_diff_trace_and_quality_evaluation(self):
        workflow_id = uuid4()
        organization_id = uuid4()
        app_id = uuid4()
        user_id = uuid4()
        baseline_id = uuid4()
        db = MagicMock()
        workflow = SimpleNamespace(
            id=workflow_id,
            organization_id=organization_id,
            app_id=app_id,
            graph={
                "nodes": [
                    {
                        "id": "llm-triage",
                        "type": "llmNode",
                        "position": {"x": 100, "y": 120},
                        "data": {"model_id": "gpt-4.1"},
                    }
                ],
                "edges": [],
            },
        )
        baseline = _baseline_row(
            baseline_id=baseline_id,
            workflow_run_id=uuid4(),
            input_available=True,
            compare_available=True,
        )
        baseline["input"] = {"message": "baseline input only"}
        baseline["output"] = {"text": "baseline output"}
        quality_evaluation = {
            "status": "completed",
            "baseline": {"score": 86},
            "candidate": {"score": 82},
            "delta": -4,
            "dimensions": {
                "clarity_consistency": {
                    "baseline": 86,
                    "candidate": 82,
                    "delta": -4,
                }
            },
            "confidence": "medium",
            "safe_summary": "후보 출력의 품질 점수가 기준 출력보다 낮게 평가되었습니다.",
            "judge_cost": 0.00008,
            "judge_usage_log_id": str(uuid4()),
        }

        class FakeTask:
            def get(self, timeout):
                return {
                    "status": "success",
                    "result": {
                        "llm-triage": {
                            "text": "candidate output",
                            "usage": {
                                "model": "gpt-4.1-mini",
                                "prompt_tokens": 160,
                                "completion_tokens": 80,
                                "total_tokens": 240,
                                "cost": 0.0006,
                                "latency_ms": 1200,
                                "status": "success",
                            },
                            "metadata": {
                                "rag_summary": {
                                    "retrieved_chunk_count": 2,
                                    "knowledge_base_count": 1,
                                    "source_summary": ["HR 정책"],
                                    "raw_chunk_content": "must-not-return-raw-chunk",
                                    "source_metadata": {
                                        "document_name": "hidden-source.md",
                                        "page": 3,
                                    },
                                    "api_key": "must-not-leak",
                                },
                                "api_key": "must-not-leak",
                            },
                        }
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
                "apps.gateway.api.v1.endpoints.workflow.get_cost_optimizer_baseline_by_id",
                return_value=baseline,
                create=True,
            ),
            patch(
                "apps.gateway.api.v1.endpoints.workflow.LLMService.get_my_available_models",
                return_value=_available_model_options("gpt-4.1-mini"),
            ),
            patch(
                "apps.gateway.api.v1.endpoints.workflow.celery_app.send_task",
                return_value=FakeTask(),
            ),
            patch.object(
                workflow_endpoint.CostOptimizerOutputQualityService,
                "evaluate",
                return_value=quality_evaluation,
            ) as evaluate_quality,
        ):
            response = self.client.post(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage"
                "/cost-optimizer/compare",
                json={
                    "baseline_id": str(baseline_id),
                    "candidate": {
                        "label": "B",
                        "model_id": "gpt-4.1-mini",
                        "parameters": {
                            "max_tokens": 800,
                            "temperature": 0.1,
                        },
                    },
                },
            )

        assert response.status_code == 200
        payload = response.json()
        assert payload["baseline"]["trace"]["output_preview"] == "billing"
        assert payload["candidate"]["trace"]["output_preview"] == "candidate output"
        assert payload["candidate"]["trace"]["rag_summary"] == {
            "retrieved_chunk_count": 2,
            "knowledge_base_count": 1,
            "source_summary": ["HR 정책"],
        }
        assert payload["diff"] == {
            "cost_delta": -0.0006,
            "cost_delta_percent": -50.0,
            "token_delta": -180,
            "latency_delta_ms": -600,
        }
        assert payload["quality_evaluation"] == quality_evaluation
        evaluate_quality.assert_called_once()
        quality_call = evaluate_quality.call_args.kwargs
        assert quality_call["baseline"] == baseline
        assert quality_call["candidate_result"]["input"] == baseline["input"]
        quality_candidate_output = quality_call["candidate_result"]["output"]
        assert quality_candidate_output["text"] == "candidate output"
        assert "api_key" not in str(quality_candidate_output)
        assert quality_call["candidate_row"].diff_summary["quality_evaluation"] == (
            quality_evaluation
        )
        serialized = response.text
        assert "must-not-leak" not in serialized
        assert "api_key" not in serialized

    def test_fr3_compare_marks_schema_failed_candidate_but_keeps_usage(self):
        workflow_id = uuid4()
        organization_id = uuid4()
        app_id = uuid4()
        user_id = uuid4()
        baseline_id = uuid4()
        db = MagicMock()
        workflow = SimpleNamespace(
            id=workflow_id,
            organization_id=organization_id,
            app_id=app_id,
            graph={
                "nodes": [
                    {
                        "id": "llm-triage",
                        "type": "llmNode",
                        "position": {"x": 100, "y": 120},
                        "data": {"model_id": "gpt-4.1"},
                    }
                ],
                "edges": [],
            },
        )
        baseline = _baseline_row(
            baseline_id=baseline_id,
            workflow_run_id=uuid4(),
            input_available=True,
            compare_available=True,
        )
        baseline["input"] = {"message": "baseline input only"}
        quality_unavailable = {
            "status": "unavailable",
            "baseline": {"score": None},
            "candidate": {"score": None},
            "delta": None,
            "dimensions": {},
            "confidence": "unavailable",
            "safe_summary": "품질 평가를 완료하지 못했습니다.",
            "judge_cost": 0.00008,
            "judge_usage_log_id": str(uuid4()),
        }

        class FakeTask:
            def get(self, timeout):
                return {
                    "status": "success",
                    "result": {
                        "llm-triage": {
                            "text": "{\"approvalRequired\": \"yes\"}",
                            "usage": {
                                "model": "gpt-4.1-mini",
                                "prompt_tokens": 160,
                                "completion_tokens": 80,
                                "total_tokens": 240,
                                "cost": 0.0006,
                                "latency_ms": 1200,
                                "status": "success",
                            },
                        }
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
                "apps.gateway.api.v1.endpoints.workflow.get_cost_optimizer_baseline_by_id",
                return_value=baseline,
                create=True,
            ),
            patch(
                "apps.gateway.api.v1.endpoints.workflow.LLMService.get_my_available_models",
                return_value=_available_model_options("gpt-4.1-mini"),
            ),
            patch(
                "apps.gateway.api.v1.endpoints.workflow.celery_app.send_task",
                return_value=FakeTask(),
            ),
            patch.object(
                workflow_endpoint.CostOptimizerOutputQualityService,
                "evaluate",
                return_value=quality_unavailable,
            ) as evaluate_quality,
        ):
            response = self.client.post(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage"
                "/cost-optimizer/compare",
                json={
                    "baseline_id": str(baseline_id),
                    "candidate": {
                        "label": "B",
                        "model_id": "gpt-4.1-mini",
                        "parameters": {
                            "max_tokens": 800,
                            "temperature": 0.1,
                        },
                        "output_format": {
                            "type": "json",
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "approvalRequired": {"type": "boolean"}
                                },
                                "required": ["approvalRequired"],
                            },
                        },
                    },
                },
            )

        assert response.status_code == 200
        payload = response.json()
        candidate = payload["candidate"]
        assert candidate["status"] == "schema_failed"
        assert candidate["usage"]["total_tokens"] == 240
        assert candidate["usage"]["cost"] == 0.0006
        assert candidate["schema_validation"]["status"] == "schema_failed"
        assert candidate["schema_validation"]["errors"] == [
            "approvalRequired must be boolean"
        ]
        assert payload["quality_evaluation"] == quality_unavailable
        evaluate_quality.assert_called_once()

    def test_fr9_rag_summary_safe_value_has_bounded_size_and_redaction(self):
        summary = {
            "retrieved_chunk_count": 30,
            "context_token_estimate": 640,
            "evidence_sufficient": True,
            "raw_chunk_content": "must-not-store",
            "source_metadata": {"filename": "hidden.md"},
            "rawChunkContent": "camel-case-must-not-store",
            "sourceMetadata": {"filename": "camel-hidden.md"},
            "score_summary": {"max": 0.98},
        }

        safe_summary = workflow_endpoint._safe_cost_optimizer_rag_summary(summary)

        assert safe_summary == {
            "retrieved_chunk_count": 30,
            "context_token_estimate": 640,
            "evidence_sufficient": True,
            "score_summary": {"max": 0.98},
        }
        serialized = str(safe_summary)
        assert "must-not-store" not in serialized
        assert "hidden.md" not in serialized
        assert "camel-case-must-not-store" not in serialized
        assert "camel-hidden.md" not in serialized

    def test_fr9_compare_marks_cost_unavailable_when_model_price_is_missing(self):
        workflow_id = uuid4()
        organization_id = uuid4()
        app_id = uuid4()
        user_id = uuid4()
        baseline_id = uuid4()
        db = MagicMock()
        workflow = SimpleNamespace(
            id=workflow_id,
            organization_id=organization_id,
            app_id=app_id,
            graph={
                "nodes": [
                    {
                        "id": "llm-triage",
                        "type": "llmNode",
                        "position": {"x": 100, "y": 120},
                        "data": {"model_id": "gpt-4.1"},
                    }
                ],
                "edges": [],
            },
        )
        baseline = _baseline_row(
            baseline_id=baseline_id,
            workflow_run_id=uuid4(),
            input_available=True,
            compare_available=True,
        )
        baseline["input"] = {"message": "baseline input only"}

        class FakeTask:
            def get(self, timeout):
                return {
                    "status": "success",
                    "result": {
                        "llm-triage": {
                            "text": "candidate output",
                            "usage": {
                                "model": "unpriced-model",
                                "prompt_tokens": 160,
                                "completion_tokens": 80,
                                "total_tokens": 240,
                                "latency_ms": 1200,
                                "status": "success",
                            },
                        }
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
                "apps.gateway.api.v1.endpoints.workflow.get_cost_optimizer_baseline_by_id",
                return_value=baseline,
                create=True,
            ),
            patch(
                "apps.gateway.api.v1.endpoints.workflow.LLMService.get_my_available_models",
                return_value=_available_model_options("unpriced-model"),
            ),
            patch(
                "apps.gateway.api.v1.endpoints.workflow.celery_app.send_task",
                return_value=FakeTask(),
            ),
        ):
            response = self.client.post(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage"
                "/cost-optimizer/compare",
                json={
                    "baseline_id": str(baseline_id),
                    "candidate": {
                        "label": "B",
                        "model_id": "unpriced-model",
                        "parameters": {
                            "max_tokens": 800,
                            "temperature": 0.1,
                        },
                    },
                },
            )

        assert response.status_code == 200
        usage = response.json()["candidate"]["usage"]
        assert usage["cost"] is None
        assert usage["cost_unavailable"] is True
        assert usage["total_tokens"] == 240

    def test_fr9_compare_reads_cost_from_node_output_when_usage_has_only_tokens(self):
        workflow_id = uuid4()
        organization_id = uuid4()
        app_id = uuid4()
        user_id = uuid4()
        baseline_id = uuid4()
        db = MagicMock()
        workflow = SimpleNamespace(
            id=workflow_id,
            organization_id=organization_id,
            app_id=app_id,
            graph={
                "nodes": [
                    {
                        "id": "llm-triage",
                        "type": "llmNode",
                        "position": {"x": 100, "y": 120},
                        "data": {"model_id": "gpt-4.1-mini"},
                    }
                ],
                "edges": [],
            },
        )
        baseline = _baseline_row(
            baseline_id=baseline_id,
            workflow_run_id=uuid4(),
            input_available=True,
            compare_available=True,
        )
        baseline["input"] = {"message": "baseline input only"}

        class FakeTask:
            def get(self, timeout):
                return {
                    "status": "success",
                    "result": {
                        "llm-triage": {
                            "text": "candidate output",
                            "cost": 0.00012,
                            "usage": {
                                "model": "gpt-4.1-mini",
                                "prompt_tokens": 116,
                                "completion_tokens": 46,
                                "total_tokens": 162,
                            },
                        }
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
                "apps.gateway.api.v1.endpoints.workflow.get_cost_optimizer_baseline_by_id",
                return_value=baseline,
                create=True,
            ),
            patch(
                "apps.gateway.api.v1.endpoints.workflow.LLMService.get_my_available_models",
                return_value=_available_model_options("gpt-4.1-mini"),
            ),
            patch(
                "apps.gateway.api.v1.endpoints.workflow.celery_app.send_task",
                return_value=FakeTask(),
            ),
        ):
            response = self.client.post(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage"
                "/cost-optimizer/compare",
                json={
                    "baseline_id": str(baseline_id),
                    "candidate": {
                        "label": "B",
                        "model_id": "gpt-4.1-mini",
                        "parameters": {
                            "max_tokens": 800,
                            "temperature": 0.1,
                        },
                    },
                },
            )

        assert response.status_code == 200
        usage = response.json()["candidate"]["usage"]
        assert usage["cost"] == 0.00012
        assert usage["cost_unavailable"] is False
        assert usage["total_tokens"] == 162

    def test_fr9_compare_persists_experiment_and_candidate_summary(self):
        workflow_id = uuid4()
        organization_id = uuid4()
        app_id = uuid4()
        user_id = uuid4()
        baseline_id = uuid4()
        workflow_run_id = uuid4()
        db = MagicMock()
        added = []
        db.add.side_effect = added.append
        workflow = SimpleNamespace(
            id=workflow_id,
            organization_id=organization_id,
            app_id=app_id,
            graph={
                "nodes": [
                    {
                        "id": "llm-triage",
                        "type": "llmNode",
                        "position": {"x": 100, "y": 120},
                        "data": {"model_id": "gpt-4.1"},
                    }
                ],
                "edges": [],
            },
        )
        baseline = _baseline_row(
            baseline_id=baseline_id,
            workflow_run_id=workflow_run_id,
            input_available=True,
            compare_available=True,
        )
        baseline["input"] = {"message": "baseline input only"}
        baseline["node_config_fingerprint"] = "node-fingerprint"
        baseline["trace"]["model_routing"] = {
            "strategy_id": "judge_bootstrap_incremental_v1",
            "selected_model": "gpt-4.1",
            "policy_version": "bootstrap-v1",
        }
        sent_tasks = []

        class FakeTask:
            def get(self, timeout):
                return {
                    "status": "success",
                    "result": {
                        "llm-triage": {
                            "text": "candidate output",
                            "usage": {
                                "model": "gpt-4.1-mini",
                                "prompt_tokens": 160,
                                "completion_tokens": 80,
                                "total_tokens": 240,
                                "cost": 0.0006,
                                "latency_ms": 1200,
                                "status": "success",
                            },
                            "metadata": {
                                "rag_summary": {
                                    "retrieved_chunk_count": 2,
                                    "knowledge_base_count": 1,
                                    "source_summary": ["HR 정책"],
                                },
                            },
                        }
                    },
                }

        def fake_send_task(name, args, kwargs=None, **options):
            sent_tasks.append({"name": name, "args": args, "kwargs": kwargs or {}})
            return FakeTask()

        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with (
            patch(
                "apps.gateway.api.v1.endpoints.workflow.ensure_workflow_permission",
                return_value=workflow,
            ),
            patch(
                "apps.gateway.api.v1.endpoints.workflow.get_cost_optimizer_baseline_by_id",
                return_value=baseline,
                create=True,
            ),
            patch(
                "apps.gateway.api.v1.endpoints.workflow.LLMService.get_my_available_models",
                return_value=_available_model_options("gpt-4.1-mini"),
            ),
            patch(
                "apps.gateway.api.v1.endpoints.workflow.celery_app.send_task",
                side_effect=fake_send_task,
            ),
        ):
            response = self.client.post(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage"
                "/cost-optimizer/compare",
                json={
                    "baseline_id": str(baseline_id),
                    "candidate": {
                        "label": "비용 절감 후보",
                        "model_id": "gpt-4.1-mini",
                        "parameters": {
                            "max_tokens": 800,
                            "temperature": 0.1,
                        },
                    },
                },
            )

        assert response.status_code == 200
        experiment = next(
            item for item in added if isinstance(item, CostOptimizerExperiment)
        )
        candidate = next(
            item for item in added if isinstance(item, CostOptimizerCandidate)
        )
        assert response.json()["comparison_id"] == str(experiment.id)
        assert experiment.organization_id == organization_id
        assert experiment.workflow_id == workflow_id
        assert experiment.app_id == app_id
        assert experiment.node_id == "llm-triage"
        assert experiment.baseline_node_run_id == baseline_id
        assert experiment.baseline_workflow_run_id == workflow_run_id
        assert experiment.created_by == user_id
        assert experiment.status == "completed"
        assert experiment.retention_expires_at > experiment.created_at
        assert candidate.experiment_id == experiment.id
        assert candidate.name == "비용 절감 후보"
        assert candidate.status == "success"
        assert candidate.model_id == "gpt-4.1-mini"
        assert candidate.candidate_settings["model_id"] == "gpt-4.1-mini"
        assert (
            candidate.candidate_settings["_baseline_node_config_fingerprint"]
            == "node-fingerprint"
        )
        assert candidate.diff_summary["routing_evidence"] == {
            "strategy_id": "judge_bootstrap_incremental_v1",
            "runtime_context": {},
            "schema_required": False,
        }
        assert candidate.total_tokens == 240
        assert float(candidate.total_cost) == 0.0006
        assert candidate.latency_ms == 1200
        assert candidate.retrieval_summary == {
            "retrieved_chunk_count": 2,
            "knowledge_base_count": 1,
            "source_summary": ["HR 정책"],
        }
        assert sent_tasks[0]["args"][2]["cost_optimizer_candidate_id"] == str(
            candidate.id
        )
        db.commit.assert_called()

    def test_fr6_fr9_compare_records_failed_candidate_when_task_raises(self):
        workflow_id = uuid4()
        organization_id = uuid4()
        app_id = uuid4()
        user_id = uuid4()
        baseline_id = uuid4()
        workflow_run_id = uuid4()
        db = MagicMock()
        added = []
        db.add.side_effect = added.append
        workflow = SimpleNamespace(
            id=workflow_id,
            organization_id=organization_id,
            app_id=app_id,
            graph={
                "nodes": [
                    {
                        "id": "llm-triage",
                        "type": "llmNode",
                        "position": {"x": 100, "y": 120},
                        "data": {"model_id": "gpt-4.1"},
                    }
                ],
                "edges": [],
            },
        )
        baseline = _baseline_row(
            baseline_id=baseline_id,
            workflow_run_id=workflow_run_id,
            input_available=True,
            compare_available=True,
        )
        baseline["input"] = {"message": "baseline input"}
        baseline["trace"] = {}

        class FailingTask:
            def get(self, timeout):
                raise TimeoutError("candidate execution timed out")

        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with (
            patch(
                "apps.gateway.api.v1.endpoints.workflow.ensure_workflow_permission",
                return_value=workflow,
            ),
            patch(
                "apps.gateway.api.v1.endpoints.workflow.get_cost_optimizer_baseline_by_id",
                return_value=baseline,
                create=True,
            ),
            patch(
                "apps.gateway.api.v1.endpoints.workflow.LLMService.get_my_available_models",
                return_value=_available_model_options("gpt-4.1-mini"),
            ),
            patch(
                "apps.gateway.api.v1.endpoints.workflow.celery_app.send_task",
                return_value=FailingTask(),
            ),
            patch.object(
                workflow_endpoint.CostOptimizerOutputQualityService,
                "evaluate",
            ) as evaluate_quality,
        ):
            response = self.client.post(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage"
                "/cost-optimizer/compare",
                json={
                    "baseline_id": str(baseline_id),
                    "candidate": {
                        "label": "timeout 후보",
                        "model_id": "gpt-4.1-mini",
                        "parameters": {
                            "max_tokens": 800,
                            "temperature": 0.1,
                        },
                    },
                },
            )

        assert response.status_code == 200
        payload = response.json()
        assert payload["baseline"]["baseline_id"] == str(baseline_id)
        assert payload["candidate"]["status"] == "failed"

        assert payload["candidate"]["error_message"] == "workflow.execution_failed"
        assert payload["candidate"]["error_detail"] is None
        assert payload["quality_evaluation"]["status"] == "unavailable"
        assert payload["quality_evaluation"]["baseline"]["score"] is None
        assert payload["quality_evaluation"]["candidate"]["score"] is None
        evaluate_quality.assert_not_called()

        experiment = next(
            item for item in added if isinstance(item, CostOptimizerExperiment)
        )
        candidate = next(
            item for item in added if isinstance(item, CostOptimizerCandidate)
        )
        assert experiment.status == "failed"
        assert candidate.status == "failed"
        db.commit.assert_called()

    def test_fr9_compare_does_not_store_raw_candidate_prompts(self):
        workflow_id = uuid4()
        organization_id = uuid4()
        app_id = uuid4()
        user_id = uuid4()
        baseline_id = uuid4()
        workflow_run_id = uuid4()
        db = MagicMock()
        added = []
        db.add.side_effect = added.append
        workflow = SimpleNamespace(
            id=workflow_id,
            organization_id=organization_id,
            app_id=app_id,
            graph={
                "nodes": [
                    {
                        "id": "llm-triage",
                        "type": "llmNode",
                        "position": {"x": 100, "y": 120},
                        "data": {"model_id": "gpt-4.1"},
                    }
                ],
                "edges": [],
            },
        )
        baseline = _baseline_row(
            baseline_id=baseline_id,
            workflow_run_id=workflow_run_id,
            input_available=True,
            compare_available=True,
        )
        baseline["input"] = {"message": "baseline input only"}

        class FakeTask:
            def get(self, timeout):
                return {
                    "status": "success",
                    "result": {
                        "llm-triage": {
                            "text": "candidate output",
                            "usage": {
                                "model": "gpt-4.1-mini",
                                "prompt_tokens": 160,
                                "completion_tokens": 80,
                                "total_tokens": 240,
                                "cost": 0.0006,
                                "latency_ms": 1200,
                                "status": "success",
                            },
                        }
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
                "apps.gateway.api.v1.endpoints.workflow.get_cost_optimizer_baseline_by_id",
                return_value=baseline,
                create=True,
            ),
            patch(
                "apps.gateway.api.v1.endpoints.workflow.LLMService.get_my_available_models",
                return_value=_available_model_options("gpt-4.1-mini"),
            ),
            patch(
                "apps.gateway.api.v1.endpoints.workflow.celery_app.send_task",
                return_value=FakeTask(),
            ),
        ):
            response = self.client.post(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage"
                "/cost-optimizer/compare",
                json={
                    "baseline_id": str(baseline_id),
                    "candidate": {
                        "label": "B",
                        "model_id": "gpt-4.1-mini",
                        "system_prompt": "must-not-store-system-prompt",
                        "user_prompt": "must-not-store-user-prompt",
                        "assistant_prompt": "must-not-store-assistant-prompt",
                        "parameters": {
                            "max_tokens": 800,
                            "temperature": 0.1,
                        },
                    },
                },
            )

        assert response.status_code == 200
        candidate = next(
            item for item in added if isinstance(item, CostOptimizerCandidate)
        )
        serialized_settings = str(candidate.candidate_settings)
        assert "must-not-store-system-prompt" not in serialized_settings
        assert "must-not-store-user-prompt" not in serialized_settings
        assert "must-not-store-assistant-prompt" not in serialized_settings
        assert candidate.candidate_settings["_settings_fingerprint"]
        assert candidate.candidate_settings["system_prompt"] == {
            "redacted": True,
            "present": True,
            "length": len("must-not-store-system-prompt"),
        }

    def test_fr7_compare_response_uses_baseline_downstream_snapshot(self):
        workflow_id = uuid4()
        organization_id = uuid4()
        app_id = uuid4()
        user_id = uuid4()
        baseline_id = uuid4()
        db = MagicMock()
        baseline_graph = {
            "nodes": [
                {"id": "llm-triage", "type": "llmNode", "data": {}},
                {
                    "id": "extract-result",
                    "type": "variableExtractionNode",
                    "data": {},
                },
            ],
            "edges": [
                {"id": "e1", "source": "llm-triage", "target": "extract-result"},
            ],
        }
        current_graph = {
            "nodes": [
                {"id": "llm-triage", "type": "llmNode", "data": {}},
                {
                    "id": "extract-result",
                    "type": "variableExtractionNode",
                    "data": {},
                },
                {"id": "answer", "type": "answerNode", "data": {}},
            ],
            "edges": [
                {"id": "e1", "source": "llm-triage", "target": "extract-result"},
                {"id": "e2", "source": "extract-result", "target": "answer"},
            ],
        }
        workflow = SimpleNamespace(
            id=workflow_id,
            organization_id=organization_id,
            app_id=app_id,
            graph=current_graph,
        )
        baseline = _baseline_row(
            baseline_id=baseline_id,
            workflow_run_id=uuid4(),
            input_available=True,
            compare_available=True,
        )
        baseline["input"] = {"message": "baseline input only"}
        baseline["downstream_snapshot"] = (
            workflow_endpoint.build_cost_optimizer_downstream_snapshot(
                baseline_graph,
                "llm-triage",
            )
        )
        baseline.pop("downstream_compatibility", None)

        class FakeTask:
            def get(self, timeout):
                return {
                    "status": "success",
                    "result": {
                        "llm-triage": {
                            "text": "candidate output",
                            "usage": {"total_tokens": 200, "cost": 0.0005},
                        }
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
                "apps.gateway.api.v1.endpoints.workflow.get_cost_optimizer_baseline_by_id",
                return_value=baseline,
                create=True,
            ),
            patch(
                "apps.gateway.api.v1.endpoints.workflow.LLMService.get_my_available_models",
                return_value=_available_model_options("gpt-4.1-mini"),
            ),
            patch(
                "apps.gateway.api.v1.endpoints.workflow.celery_app.send_task",
                return_value=FakeTask(),
            ),
        ):
            response = self.client.post(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage"
                "/cost-optimizer/compare",
                json={
                    "baseline_id": str(baseline_id),
                    "candidate": {
                        "label": "B",
                        "model_id": "gpt-4.1-mini",
                        "parameters": {
                            "max_tokens": 800,
                            "temperature": 0.1,
                        },
                    },
                },
            )

        assert response.status_code == 200
        downstream = response.json()["downstream_compatibility"]
        assert downstream["state"] == "warning"
        assert downstream["label"] == "주의 필요"
        assert downstream["first_consumer_status"] == "same"
        assert downstream["contract_check"]["status"] == "warning"


class TestCostOptimizerApplyApi:
    def setup_method(self):
        self.client = TestClient(app)

    def teardown_method(self):
        app.dependency_overrides = {}

    def test_fr8_apply_candidate_updates_current_draft_target_llm_node(self):
        workflow_id = uuid4()
        organization_id = uuid4()
        app_id = uuid4()
        user_id = uuid4()
        knowledge_base_id = uuid4()
        comparison_id = uuid4()
        db = MagicMock()
        candidate_settings = {
            "label": "B",
            "model_id": "gpt-4.1-mini",
            "fallback_model_id": "gpt-4.1",
            "task_type": "classify",
            "system_prompt": "new system",
            "user_prompt": "new user",
            "assistant_prompt": "",
            "parameters": {
                "max_tokens": 800,
                "temperature": 0.1,
                "top_p": 0.8,
                "presence_penalty": 0.2,
                "frequency_penalty": -0.1,
                "stop": ["END"],
            },
            "output_format": {"type": "json", "schema": {}},
            "knowledge": {
                "knowledge_base_ids": [str(knowledge_base_id)],
                "top_k": 5,
                "score_threshold": 0.65,
                "dedupe_retrieved_context": True,
                "retrieved_context_max_chars": 6000,
                "retrieved_context_compression": "light",
                "answer_grounding_check": "basic",
            },
        }
        request_candidate = workflow_endpoint.CostOptimizerCandidateRequest(
            **candidate_settings
        )
        _configure_cost_optimizer_experiment_query(
            db,
            SimpleNamespace(
                id=comparison_id,
                workflow_id=workflow_id,
                node_id="llm-triage",
                organization_id=organization_id,
                candidates=[
                    SimpleNamespace(
                        experiment_id=comparison_id,
                        candidate_settings=workflow_endpoint._safe_cost_optimizer_candidate_settings(
                            request_candidate
                        ),
                        status="success",
                        schema_status="pass",
                        downstream_compatibility={"state": "compatible"},
                    )
                ],
            ),
        )
        workflow = SimpleNamespace(
            id=workflow_id,
            organization_id=organization_id,
            app_id=app_id,
            graph={
                "nodes": [
                    {
                        "id": "llm-triage",
                        "type": "llmNode",
                        "position": {"x": 100, "y": 120},
                        "data": {
                            "title": "티켓 처리 판단",
                            "model_id": "gpt-4.1",
                            "system_prompt": "old system",
                            "user_prompt": "old user",
                            "parameters": {"max_tokens": 1200, "temperature": 0.7},
                            "citationDisplayMode": "detailed",
                        },
                    },
                    {
                        "id": "answer",
                        "type": "answerNode",
                        "position": {"x": 400, "y": 120},
                        "data": {"title": "응답"},
                    },
                ],
                "edges": [
                    {"id": "e1", "source": "llm-triage", "target": "answer"},
                ],
                "viewport": {"x": 0, "y": 0, "zoom": 1},
            },
        )

        _configure_workflow_lock_query(db, workflow)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with (
            patch(
                "apps.gateway.api.v1.endpoints.workflow.ensure_workflow_permission",
                return_value=workflow,
            ) as ensure_builder,
            patch(
                "apps.gateway.api.v1.endpoints.workflow.has_knowledge_base_permission",
                return_value=True,
                create=True,
            ),
            patch(
                "apps.gateway.api.v1.endpoints.workflow.LLMService.get_my_available_models",
                return_value=_available_model_options("gpt-4.1-mini", "gpt-4.1"),
            ),
            patch.object(
                WorkflowService,
                "validate_knowledge_references",
            ) as validate_knowledge_references,
        ):
            response = self.client.patch(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage"
                "/cost-optimizer/apply",
                json=_cas_payload(
                    workflow,
                    {
                        "comparison_id": str(comparison_id),
                        "candidate_settings": candidate_settings,
                        "acknowledge_downstream_warning": True,
                    },
                ),
            )

        assert response.status_code == 200
        validate_knowledge_references.assert_called_once()
        assert ensure_builder.call_count == 2
        target_data = workflow.graph["nodes"][0]["data"]
        assert target_data["model_id"] == "gpt-4.1-mini"
        assert target_data["fallback_model_id"] == "gpt-4.1"
        assert target_data["task_type"] == "classify"
        assert target_data["system_prompt"] == "new system"
        assert target_data["user_prompt"] == "new user"
        assert target_data["parameters"]["max_tokens"] == 800
        assert target_data["parameters"]["temperature"] == 0.1
        assert target_data["parameters"]["top_p"] == 0.8
        assert target_data["parameters"]["presence_penalty"] == 0.2
        assert target_data["parameters"]["frequency_penalty"] == -0.1
        assert target_data["parameters"]["stop"] == ["END"]
        assert target_data["output_format"] == {"type": "json", "schema": {}}
        assert target_data["knowledgeBases"] == [{"id": str(knowledge_base_id), "name": ""}]
        assert target_data["topK"] == 5
        assert target_data["scoreThreshold"] == 0.65
        assert target_data["dedupeRetrievedContext"] is True
        assert target_data["retrievedContextMaxChars"] == 6000
        assert target_data["retrievedContextCompression"] == "light"
        assert target_data["answerGroundingCheck"] == "basic"
        assert target_data["citationDisplayMode"] == "detailed"
        assert db.commit.called
        assert response.json()["applied"] is True
        assert response.json()["graph_hash"] == canonical_graph_hash(workflow.graph)
        assert response.json()["updated_at"] == workflow.updated_at.isoformat()
        assert "updated_draft_revision" not in response.json()

    def test_fr8_apply_candidate_rejects_stale_acknowledged_graph(self):
        workflow_id = uuid4()
        organization_id = uuid4()
        app_id = uuid4()
        user_id = uuid4()
        comparison_id = uuid4()
        db = MagicMock()
        candidate_settings = {
            "label": "B",
            "model_id": "gpt-4.1-mini",
            "parameters": {"max_tokens": 800, "temperature": 0.1},
        }
        request_candidate = workflow_endpoint.CostOptimizerCandidateRequest(
            **candidate_settings
        )
        stored_candidate = SimpleNamespace(
            experiment_id=comparison_id,
            candidate_settings=workflow_endpoint._safe_cost_optimizer_candidate_settings(
                request_candidate
            ),
            status="success",
            schema_status="pass",
            downstream_compatibility={"state": "compatible"},
            is_applied=False,
            applied_at=None,
            applied_by=None,
        )
        _configure_cost_optimizer_experiment_query(
            db,
            SimpleNamespace(
                id=comparison_id,
                workflow_id=workflow_id,
                node_id="llm-triage",
                organization_id=organization_id,
                candidates=[stored_candidate],
            ),
        )
        stale_workflow = SimpleNamespace(
            id=workflow_id,
            organization_id=organization_id,
            app_id=app_id,
            updated_at=_TEST_UPDATED_AT,
            graph={
                "nodes": [
                    {
                        "id": "llm-triage",
                        "type": "llmNode",
                        "position": {"x": 100, "y": 120},
                        "data": {"model_id": "gpt-4.1"},
                    }
                ],
                "edges": [],
            },
        )
        acknowledged_workflow = SimpleNamespace(
            id=workflow_id,
            organization_id=organization_id,
            app_id=app_id,
            updated_at=datetime(2026, 7, 14, 12, 5, tzinfo=timezone.utc),
            graph={
                "nodes": [
                    {
                        "id": "llm-triage",
                        "type": "llmNode",
                        "position": {"x": 100, "y": 120},
                        "data": {
                            "model_id": "gpt-4.1",
                            "system_prompt": "agent builder acknowledged prompt",
                            "parameters": {"max_tokens": 1600},
                        },
                    }
                ],
                "edges": [],
            },
        )
        lock_query = _configure_workflow_lock_query(db, acknowledged_workflow)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with (
            patch(
                "apps.gateway.api.v1.endpoints.workflow.ensure_workflow_permission",
                return_value=stale_workflow,
            ),
            patch(
                "apps.gateway.api.v1.endpoints.workflow.LLMService.get_my_available_models",
                return_value=_available_model_options("gpt-4.1-mini"),
            ),
        ):
            response = self.client.patch(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage"
                "/cost-optimizer/apply",
                json=_cas_payload(
                    stale_workflow,
                    {
                        "comparison_id": str(comparison_id),
                        "candidate_settings": candidate_settings,
                        "acknowledge_downstream_warning": True,
                    },
                ),
            )

        assert response.status_code == 409
        assert response.json()["detail"] == "stale_graph"
        assert lock_query.locked is True
        assert acknowledged_workflow.graph["nodes"][0]["data"] == {
            "model_id": "gpt-4.1",
            "system_prompt": "agent builder acknowledged prompt",
            "parameters": {"max_tokens": 1600},
        }
        assert stored_candidate.is_applied is False
        assert stored_candidate.applied_at is None
        assert stored_candidate.applied_by is None
        db.commit.assert_not_called()

    def test_fr8_apply_candidate_without_knowledge_preserves_existing_rag_selection(
        self,
    ):
        workflow_id = uuid4()
        organization_id = uuid4()
        app_id = uuid4()
        user_id = uuid4()
        knowledge_base_id = uuid4()
        comparison_id = uuid4()
        db = MagicMock()
        candidate_settings = {
            "label": "B",
            "model_id": "gpt-4.1-mini",
            "parameters": {"max_tokens": 800, "temperature": 0.2},
        }
        request_candidate = workflow_endpoint.CostOptimizerCandidateRequest(
            **candidate_settings
        )
        _configure_cost_optimizer_experiment_query(
            db,
            SimpleNamespace(
                id=comparison_id,
                workflow_id=workflow_id,
                node_id="llm-triage",
                organization_id=organization_id,
                candidates=[
                    SimpleNamespace(
                        experiment_id=comparison_id,
                        candidate_settings=workflow_endpoint._safe_cost_optimizer_candidate_settings(
                            request_candidate
                        ),
                        status="success",
                        schema_status="pass",
                        downstream_compatibility={"state": "compatible"},
                    )
                ],
            ),
        )
        workflow = SimpleNamespace(
            id=workflow_id,
            organization_id=organization_id,
            app_id=app_id,
            graph={
                "nodes": [
                    {
                        "id": "llm-triage",
                        "type": "llmNode",
                        "position": {"x": 100, "y": 120},
                        "data": {
                            "title": "티켓 처리 판단",
                            "model_id": "gpt-4.1",
                            "parameters": {"temperature": 0.7, "max_tokens": 1200},
                            "knowledgeBases": [
                                {"id": str(knowledge_base_id), "name": "제품 정책"}
                            ],
                            "topK": 4,
                            "scoreThreshold": 0.6,
                            "dedupeRetrievedContext": True,
                            "retrievedContextMaxChars": 4000,
                            "retrievedContextCompression": "light",
                            "answerGroundingCheck": "basic",
                        },
                    }
                ],
                "edges": [],
                "viewport": {"x": 0, "y": 0, "zoom": 1},
            },
        )

        _configure_workflow_lock_query(db, workflow)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with (
            patch(
                "apps.gateway.api.v1.endpoints.workflow.ensure_workflow_permission",
                return_value=workflow,
            ),
            patch(
                "apps.gateway.api.v1.endpoints.workflow.LLMService.get_my_available_models",
                return_value=_available_model_options("gpt-4.1-mini"),
            ),
            patch.object(
                WorkflowService,
                "validate_knowledge_references",
            ) as validate_knowledge_references,
        ):
            response = self.client.patch(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage"
                "/cost-optimizer/apply",
                json=_cas_payload(
                    workflow,
                    {
                        "comparison_id": str(comparison_id),
                        "candidate_settings": candidate_settings,
                    },
                ),
            )

        assert response.status_code == 200, response.json()
        validate_knowledge_references.assert_called_once()
        target_data = workflow.graph["nodes"][0]["data"]
        assert target_data["model_id"] == "gpt-4.1-mini"
        assert target_data["parameters"] == {
            "temperature": 0.2,
            "max_tokens": 800,
        }
        assert target_data["knowledgeBases"] == [
            {"id": str(knowledge_base_id), "name": "제품 정책"}
        ]
        assert target_data["topK"] == 4
        assert target_data["scoreThreshold"] == 0.6
        assert target_data["dedupeRetrievedContext"] is True
        assert target_data["retrievedContextMaxChars"] == 4000
        assert target_data["retrievedContextCompression"] == "light"
        assert target_data["answerGroundingCheck"] == "basic"
        assert db.commit.called

    def test_fr8_fr9_apply_marks_matching_candidate_as_applied(self):
        workflow_id = uuid4()
        organization_id = uuid4()
        app_id = uuid4()
        user_id = uuid4()
        comparison_id = uuid4()
        db = MagicMock()
        candidate_settings = {
            "label": "B",
            "model_id": "gpt-4.1-mini",
            "parameters": {"max_tokens": 800, "temperature": 0.1},
        }
        request_candidate = workflow_endpoint.CostOptimizerCandidateRequest(
            **candidate_settings
        )
        stored_candidate = SimpleNamespace(
            experiment_id=comparison_id,
            candidate_settings=workflow_endpoint._safe_cost_optimizer_candidate_settings(
                request_candidate
            ),
            status="success",
            schema_status="pass",
            downstream_compatibility={"state": "compatible"},
            is_applied=False,
            applied_at=None,
            applied_by=None,
        )
        _configure_cost_optimizer_experiment_query(
            db,
            SimpleNamespace(
                id=comparison_id,
                workflow_id=workflow_id,
                node_id="llm-triage",
                organization_id=organization_id,
                candidates=[stored_candidate],
            ),
        )
        workflow = SimpleNamespace(
            id=workflow_id,
            organization_id=organization_id,
            app_id=app_id,
            graph={
                "nodes": [
                    {
                        "id": "llm-triage",
                        "type": "llmNode",
                        "position": {"x": 100, "y": 120},
                        "data": {"model_id": "gpt-4.1"},
                    }
                ],
                "edges": [],
            },
        )

        _configure_workflow_lock_query(db, workflow)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with (
            patch(
                "apps.gateway.api.v1.endpoints.workflow.ensure_workflow_permission",
                return_value=workflow,
            ),
            patch(
                "apps.gateway.api.v1.endpoints.workflow.LLMService.get_my_available_models",
                return_value=_available_model_options("gpt-4.1-mini"),
            ),
        ):
            response = self.client.patch(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage"
                "/cost-optimizer/apply",
                json=_cas_payload(
                    workflow,
                    {
                        "comparison_id": str(comparison_id),
                        "candidate_settings": candidate_settings,
                        "acknowledge_downstream_warning": True,
                    },
                ),
            )

        assert response.status_code == 200
        assert stored_candidate.is_applied is True
        assert stored_candidate.applied_by == user_id
        assert stored_candidate.applied_at is not None

    def test_fr8_apply_rejects_missing_comparison_id(self):
        workflow_id = uuid4()
        organization_id = uuid4()
        app_id = uuid4()
        user_id = uuid4()
        db = MagicMock()
        candidate_settings = {
            "label": "B",
            "model_id": "gpt-4.1-mini",
            "parameters": {"max_tokens": 800, "temperature": 0.1},
        }
        workflow = SimpleNamespace(
            id=workflow_id,
            organization_id=organization_id,
            app_id=app_id,
            graph={
                "nodes": [
                    {
                        "id": "llm-triage",
                        "type": "llmNode",
                        "position": {"x": 100, "y": 120},
                        "data": {"model_id": "gpt-4.1"},
                    }
                ],
                "edges": [],
            },
        )

        _configure_workflow_lock_query(db, workflow)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with (
            patch(
                "apps.gateway.api.v1.endpoints.workflow.ensure_workflow_permission",
                return_value=workflow,
            ),
            patch(
                "apps.gateway.api.v1.endpoints.workflow.LLMService.get_my_available_models",
                return_value=_available_model_options("gpt-4.1-mini"),
            ),
        ):
            response = self.client.patch(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage"
                "/cost-optimizer/apply",
                json=_cas_payload(
                    workflow,
                    {
                        "candidate_settings": candidate_settings,
                        "acknowledge_downstream_warning": True,
                    },
                ),
            )

        assert response.status_code == 400
        assert response.json()["detail"] == "cost_optimizer.candidate_not_found"
        db.commit.assert_not_called()

    def test_fr8_apply_rejects_comparison_from_other_workflow_scope(self):
        workflow_id = uuid4()
        organization_id = uuid4()
        app_id = uuid4()
        user_id = uuid4()
        comparison_id = uuid4()
        db = MagicMock()
        candidate_settings = {
            "label": "B",
            "model_id": "gpt-4.1-mini",
            "parameters": {"max_tokens": 800, "temperature": 0.1},
        }
        request_candidate = workflow_endpoint.CostOptimizerCandidateRequest(
            **candidate_settings
        )
        external_candidate = SimpleNamespace(
            experiment_id=comparison_id,
            candidate_settings=workflow_endpoint._safe_cost_optimizer_candidate_settings(
                request_candidate
            ),
            status="success",
            schema_status="pass",
            downstream_compatibility={"state": "compatible"},
            is_applied=False,
            applied_at=None,
            applied_by=None,
        )
        _configure_cost_optimizer_experiment_query(db, None)
        workflow = SimpleNamespace(
            id=workflow_id,
            organization_id=organization_id,
            app_id=app_id,
            graph={
                "nodes": [
                    {
                        "id": "llm-triage",
                        "type": "llmNode",
                        "position": {"x": 100, "y": 120},
                        "data": {"model_id": "gpt-4.1"},
                    }
                ],
                "edges": [],
            },
        )

        _configure_workflow_lock_query(db, workflow)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with (
            patch(
                "apps.gateway.api.v1.endpoints.workflow.ensure_workflow_permission",
                return_value=workflow,
            ),
            patch(
                "apps.gateway.api.v1.endpoints.workflow.LLMService.get_my_available_models",
                return_value=_available_model_options("gpt-4.1-mini"),
            ),
        ):
            response = self.client.patch(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage"
                "/cost-optimizer/apply",
                json=_cas_payload(
                    workflow,
                    {
                        "comparison_id": str(comparison_id),
                        "candidate_settings": candidate_settings,
                        "acknowledge_downstream_warning": True,
                    },
                ),
            )

        assert response.status_code == 400
        assert response.json()["detail"] == "cost_optimizer.candidate_not_found"
        assert external_candidate.is_applied is False
        assert external_candidate.applied_by is None
        assert external_candidate.applied_at is None
        db.commit.assert_not_called()

    def test_fr8_apply_rejects_candidate_settings_not_in_comparison(self):
        workflow_id = uuid4()
        organization_id = uuid4()
        app_id = uuid4()
        user_id = uuid4()
        comparison_id = uuid4()
        db = MagicMock()
        stored_candidate = workflow_endpoint.CostOptimizerCandidateRequest(
            label="B",
            model_id="gpt-4.1",
            parameters={"max_tokens": 800, "temperature": 0.1},
        )
        requested_candidate = {
            "label": "B",
            "model_id": "gpt-4.1-mini",
            "parameters": {"max_tokens": 800, "temperature": 0.1},
        }
        stored_candidate_row = SimpleNamespace(
            experiment_id=comparison_id,
            candidate_settings=workflow_endpoint._safe_cost_optimizer_candidate_settings(
                stored_candidate
            ),
            status="success",
            schema_status="pass",
            downstream_compatibility={"state": "compatible"},
            is_applied=False,
            applied_at=None,
            applied_by=None,
        )
        _configure_cost_optimizer_experiment_query(
            db,
            SimpleNamespace(
                id=comparison_id,
                workflow_id=workflow_id,
                node_id="llm-triage",
                organization_id=organization_id,
                candidates=[stored_candidate_row],
            ),
        )
        workflow = SimpleNamespace(
            id=workflow_id,
            organization_id=organization_id,
            app_id=app_id,
            graph={
                "nodes": [
                    {
                        "id": "llm-triage",
                        "type": "llmNode",
                        "position": {"x": 100, "y": 120},
                        "data": {"model_id": "gpt-4.1"},
                    }
                ],
                "edges": [],
            },
        )

        _configure_workflow_lock_query(db, workflow)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with (
            patch(
                "apps.gateway.api.v1.endpoints.workflow.ensure_workflow_permission",
                return_value=workflow,
            ),
            patch(
                "apps.gateway.api.v1.endpoints.workflow.LLMService.get_my_available_models",
                return_value=_available_model_options("gpt-4.1-mini", "gpt-4.1"),
            ),
        ):
            response = self.client.patch(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage"
                "/cost-optimizer/apply",
                json=_cas_payload(
                    workflow,
                    {
                        "comparison_id": str(comparison_id),
                        "candidate_settings": requested_candidate,
                        "acknowledge_downstream_warning": True,
                    },
                ),
            )

        assert response.status_code == 400
        assert response.json()["detail"] == "cost_optimizer.candidate_not_found"
        assert stored_candidate_row.is_applied is False
        db.commit.assert_not_called()

    def test_fr3_fr8_apply_rejects_schema_failed_candidate_from_comparison(self):
        workflow_id = uuid4()
        organization_id = uuid4()
        app_id = uuid4()
        user_id = uuid4()
        comparison_id = uuid4()
        db = MagicMock()
        candidate_settings = {
            "label": "B",
            "model_id": "gpt-4.1-mini",
            "parameters": {"max_tokens": 800, "temperature": 0.1},
        }
        request_candidate = workflow_endpoint.CostOptimizerCandidateRequest(
            **candidate_settings
        )
        _configure_cost_optimizer_experiment_query(
            db,
            SimpleNamespace(
                id=comparison_id,
                workflow_id=workflow_id,
                node_id="llm-triage",
                organization_id=organization_id,
                candidates=[
                    SimpleNamespace(
                        experiment_id=comparison_id,
                        candidate_settings=workflow_endpoint._safe_cost_optimizer_candidate_settings(
                            request_candidate
                        ),
                        status="schema_failed",
                        schema_status="failed",
                    )
                ],
            ),
        )
        workflow = SimpleNamespace(
            id=workflow_id,
            organization_id=organization_id,
            app_id=app_id,
            graph={
                "nodes": [
                    {
                        "id": "llm-triage",
                        "type": "llmNode",
                        "position": {"x": 100, "y": 120},
                        "data": {"model_id": "gpt-4.1"},
                    }
                ],
                "edges": [],
            },
        )

        _configure_workflow_lock_query(db, workflow)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with (
            patch(
                "apps.gateway.api.v1.endpoints.workflow.ensure_workflow_permission",
                return_value=workflow,
            ),
            patch(
                "apps.gateway.api.v1.endpoints.workflow.LLMService.get_my_available_models",
                return_value=_available_model_options("gpt-4.1-mini"),
            ),
        ):
            response = self.client.patch(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage"
                "/cost-optimizer/apply",
                json=_cas_payload(
                    workflow,
                    {
                        "comparison_id": str(comparison_id),
                        "candidate_settings": candidate_settings,
                        "acknowledge_downstream_warning": True,
                    },
                ),
            )

        assert response.status_code == 400
        assert response.json()["detail"] == "cost_optimizer.schema_failed_candidate"
        db.commit.assert_not_called()

    def test_fr8_apply_matches_redacted_candidate_settings_by_fingerprint(self):
        workflow_id = uuid4()
        organization_id = uuid4()
        app_id = uuid4()
        user_id = uuid4()
        comparison_id = uuid4()
        db = MagicMock()
        candidate_settings = {
            "label": "B",
            "model_id": "gpt-4.1-mini",
            "system_prompt": "raw system prompt for apply",
            "user_prompt": "raw user prompt for apply",
            "assistant_prompt": "raw assistant prompt for apply",
            "parameters": {"max_tokens": 800, "temperature": 0.1},
        }
        target_candidate = workflow_endpoint.CostOptimizerCandidateRequest(
            **candidate_settings
        )
        other_candidate = workflow_endpoint.CostOptimizerCandidateRequest(
            label="다른 후보",
            model_id="gpt-4.1-mini",
            system_prompt="other system prompt",
            user_prompt="other user prompt",
            parameters={"max_tokens": 800, "temperature": 0.1},
        )
        _configure_cost_optimizer_experiment_query(
            db,
            SimpleNamespace(
                id=comparison_id,
                workflow_id=workflow_id,
                node_id="llm-triage",
                organization_id=organization_id,
                candidates=[
                    SimpleNamespace(
                        experiment_id=comparison_id,
                        candidate_settings=workflow_endpoint._safe_cost_optimizer_candidate_settings(
                            other_candidate
                        ),
                        status="success",
                        schema_status="pass",
                        downstream_compatibility={"state": "compatible"},
                    ),
                    SimpleNamespace(
                        experiment_id=comparison_id,
                        candidate_settings=workflow_endpoint._safe_cost_optimizer_candidate_settings(
                            target_candidate
                        ),
                        status="schema_failed",
                        schema_status="failed",
                    ),
                ],
            ),
        )
        workflow = SimpleNamespace(
            id=workflow_id,
            organization_id=organization_id,
            app_id=app_id,
            graph={
                "nodes": [
                    {
                        "id": "llm-triage",
                        "type": "llmNode",
                        "position": {"x": 100, "y": 120},
                        "data": {"model_id": "gpt-4.1"},
                    }
                ],
                "edges": [],
            },
        )

        _configure_workflow_lock_query(db, workflow)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with (
            patch(
                "apps.gateway.api.v1.endpoints.workflow.ensure_workflow_permission",
                return_value=workflow,
            ),
            patch(
                "apps.gateway.api.v1.endpoints.workflow.LLMService.get_my_available_models",
                return_value=_available_model_options("gpt-4.1-mini"),
            ),
        ):
            response = self.client.patch(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage"
                "/cost-optimizer/apply",
                json=_cas_payload(
                    workflow,
                    {
                        "comparison_id": str(comparison_id),
                        "candidate_settings": candidate_settings,
                        "acknowledge_downstream_warning": True,
                    },
                ),
            )

        assert response.status_code == 400
        assert response.json()["detail"] == "cost_optimizer.schema_failed_candidate"
        db.commit.assert_not_called()

    def test_fr7_fr8_apply_requires_ack_for_downstream_warning_candidate(self):
        workflow_id = uuid4()
        organization_id = uuid4()
        app_id = uuid4()
        user_id = uuid4()
        comparison_id = uuid4()
        db = MagicMock()
        candidate_settings = {
            "label": "B",
            "model_id": "gpt-4.1-mini",
            "parameters": {"max_tokens": 800, "temperature": 0.1},
        }
        request_candidate = workflow_endpoint.CostOptimizerCandidateRequest(
            **candidate_settings
        )
        _configure_cost_optimizer_experiment_query(
            db,
            SimpleNamespace(
                id=comparison_id,
                workflow_id=workflow_id,
                node_id="llm-triage",
                organization_id=organization_id,
                candidates=[
                    SimpleNamespace(
                        experiment_id=comparison_id,
                        candidate_settings=workflow_endpoint._safe_cost_optimizer_candidate_settings(
                            request_candidate
                        ),
                        status="success",
                        schema_status="pass",
                        downstream_compatibility={"state": "warning"},
                    )
                ],
            ),
        )
        workflow = SimpleNamespace(
            id=workflow_id,
            organization_id=organization_id,
            app_id=app_id,
            graph={
                "nodes": [
                    {
                        "id": "llm-triage",
                        "type": "llmNode",
                        "position": {"x": 100, "y": 120},
                        "data": {"model_id": "gpt-4.1"},
                    }
                ],
                "edges": [],
            },
        )

        _configure_workflow_lock_query(db, workflow)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with (
            patch(
                "apps.gateway.api.v1.endpoints.workflow.ensure_workflow_permission",
                return_value=workflow,
            ),
            patch(
                "apps.gateway.api.v1.endpoints.workflow.LLMService.get_my_available_models",
                return_value=_available_model_options("gpt-4.1-mini"),
            ),
        ):
            response = self.client.patch(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage"
                "/cost-optimizer/apply",
                json=_cas_payload(
                    workflow,
                    {
                        "comparison_id": str(comparison_id),
                        "candidate_settings": candidate_settings,
                        "acknowledge_downstream_warning": False,
                    },
                ),
            )

        assert response.status_code == 400
        assert response.json()["detail"] == "cost_optimizer.downstream_ack_required"
        db.commit.assert_not_called()


class TestCostOptimizerExperimentHistoryApi:
    def setup_method(self):
        self.client = TestClient(app)

    def teardown_method(self):
        app.dependency_overrides = {}

    def test_fr9_lists_experiments_with_candidate_summaries(self):
        workflow_id = uuid4()
        organization_id = uuid4()
        app_id = uuid4()
        user_id = uuid4()
        baseline_id = uuid4()
        experiment_id = uuid4()
        candidate_id = uuid4()
        created_at = datetime(2026, 7, 5, 1, 30, tzinfo=timezone.utc)
        workflow = _workflow_with_nodes(
            workflow_id,
            organization_id,
            [
                {
                    "id": "llm-triage",
                    "type": "llmNode",
                    "position": {"x": 0, "y": 0},
                    "data": {"model_id": "gpt-4.1"},
                }
            ],
        )
        workflow.app_id = app_id
        candidate = SimpleNamespace(
            id=candidate_id,
            name="비용 절감 후보",
            status="success",
            model_id="gpt-4.1-mini",
            fallback_model_id=None,
            task_type="generate",
            total_cost=0.0006,
            total_tokens=240,
            latency_ms=1200,
            schema_status="pass",
            downstream_state="compatible",
            usage_summary={"quality_judge_cost": 0.00008},
            diff_summary={
                "quality_evaluation": {
                    "status": "completed",
                    "baseline": {"score": 86},
                    "candidate": {"score": 82},
                    "delta": -4,
                    "dimensions": {},
                    "confidence": "medium",
                    "safe_summary": "후보 출력의 품질 점수가 기준 출력보다 낮게 평가되었습니다.",
                    "judge_cost": 0.00008,
                    "judge_usage_log_id": "internal-usage-log-id",
                }
            },
            is_applied=False,
            created_at=created_at,
        )
        failed_candidate = SimpleNamespace(
            id=uuid4(),
            name="실패 후보",
            status="schema_failed",
            model_id="gpt-4.1-mini",
            fallback_model_id=None,
            task_type="generate",
            total_cost=0.0004,
            total_tokens=200,
            latency_ms=1100,
            schema_status="failed",
            downstream_state="incompatible",
            usage_summary={},
            diff_summary={},
            is_applied=False,
            created_at=created_at,
        )
        experiment = SimpleNamespace(
            id=experiment_id,
            workflow_id=workflow_id,
            app_id=app_id,
            node_id="llm-triage",
            baseline_node_run_id=baseline_id,
            baseline_workflow_run_id=uuid4(),
            status="completed",
            created_by=user_id,
            created_at=created_at,
            usage_summary={"total_tokens": 240, "cost": 0.0006},
            candidates=[candidate, failed_candidate],
        )

        class FakeQuery:
            def __init__(self, rows):
                self.rows = rows

            def options(self, *args, **kwargs):
                return self

            def filter(self, *args, **kwargs):
                return self

            def join(self, *args, **kwargs):
                return self

            def distinct(self):
                return self

            def order_by(self, *args, **kwargs):
                return self

            def offset(self, _offset):
                return self

            def limit(self, _limit):
                return self

            def count(self):
                return len(self.rows)

            def all(self):
                return self.rows

        db = MagicMock()
        db.query.return_value = FakeQuery([experiment])
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with patch(
            "apps.gateway.api.v1.endpoints.workflow.ensure_workflow_permission",
            return_value=workflow,
        ) as ensure_builder:
            response = self.client.get(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage"
                f"/cost-optimizer/experiments?baseline_id={baseline_id}"
                "&candidate_status=success"
            )

        assert response.status_code == 200
        ensure_builder.assert_called_once_with(
            db, SimpleNamespace(id=user_id), str(workflow_id), "write"
        )
        payload = response.json()
        assert payload["total"] == 1
        assert payload["items"][0]["experiment_id"] == str(experiment_id)
        assert payload["items"][0]["baseline_node_run_id"] == str(baseline_id)
        assert len(payload["items"][0]["candidates"]) == 1
        assert payload["items"][0]["candidates"][0] == {
            "candidate_id": str(candidate_id),
            "name": "비용 절감 후보",
            "status": "success",
            "model_id": "gpt-4.1-mini",
            "fallback_model_id": None,
            "task_type": "generate",
            "total_cost": 0.0006,
            "total_tokens": 240,
            "latency_ms": 1200,
            "schema_status": "pass",
            "downstream_state": "compatible",
            "quality_evaluation": {
                "status": "completed",
                "baseline": {"score": 86},
                "candidate": {"score": 82},
                "delta": -4,
                "dimensions": {},
                "confidence": "medium",
                "safe_summary": "후보 출력의 품질 점수가 기준 출력보다 낮게 평가되었습니다.",
                "judge_cost": 0.00008,
            },
            "is_applied": False,
            "created_at": "2026-07-05T01:30:00+00:00",
        }

    def test_fr13_gets_exact_experiment_candidate_for_detail_deep_link(self):
        workflow_id = uuid4()
        organization_id = uuid4()
        user_id = uuid4()
        experiment_id = uuid4()
        candidate_id = uuid4()
        db = MagicMock()
        workflow = _workflow_with_nodes(
            workflow_id,
            organization_id,
            [
                {
                    "id": "llm-triage",
                    "type": "llmNode",
                    "position": {"x": 0, "y": 0},
                    "data": {"model_id": "gpt-4.1"},
                }
            ],
        )
        detail = {
            "experiment_id": str(experiment_id),
            "workflow_id": str(workflow_id),
            "node_id": "llm-triage",
            "baseline_summary": {"baseline_id": str(uuid4())},
            "candidate": {
                "candidate_id": str(candidate_id),
                "status": "schema_failed",
            },
        }
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with (
            patch(
                "apps.gateway.api.v1.endpoints.workflow.ensure_workflow_permission",
                return_value=workflow,
            ) as ensure_builder,
            patch.object(
                workflow_endpoint,
                "get_cost_optimizer_experiment_candidate",
                return_value=detail,
            ) as get_detail,
        ):
            response = self.client.get(
                f"/api/v1/workflows/{workflow_id}/llm-nodes/llm-triage"
                f"/cost-optimizer/experiments/{experiment_id}/candidates/{candidate_id}"
            )

        assert response.status_code == 200
        assert response.json() == detail
        ensure_builder.assert_called_once_with(
            db, SimpleNamespace(id=user_id), str(workflow_id), "write"
        )
        get_detail.assert_called_once_with(
            db,
            workflow,
            "llm-triage",
            str(experiment_id),
            str(candidate_id),
        )

    def test_fr13_detail_helper_returns_only_requested_candidate(self):
        workflow_id = uuid4()
        organization_id = uuid4()
        experiment_id = uuid4()
        candidate_id = uuid4()
        other_candidate_id = uuid4()
        created_at = datetime(2026, 7, 5, 1, 30, tzinfo=timezone.utc)
        workflow = SimpleNamespace(
            id=workflow_id,
            organization_id=organization_id,
        )

        def candidate(candidate_row_id, status):
            return SimpleNamespace(
                id=candidate_row_id,
                name="상세 후보",
                status=status,
                model_id="gpt-4.1-mini",
                fallback_model_id=None,
                task_type="generate",
                total_cost=0.0006,
                total_tokens=240,
                latency_ms=1200,
                schema_status="failed" if status == "schema_failed" else "pass",
                downstream_state="incompatible",
                usage_summary={},
                diff_summary={},
                candidate_node_run_id=None,
                candidate_workflow_run_id=None,
                is_applied=False,
                created_at=created_at,
            )

        experiment = SimpleNamespace(
            id=experiment_id,
            organization_id=organization_id,
            workflow_id=workflow_id,
            app_id=None,
            node_id="llm-triage",
            baseline_node_run_id=uuid4(),
            baseline_workflow_run_id=uuid4(),
            baseline_usage_summary={
                "model": "gpt-4.1",
                "cost": 0.001,
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
                "latency_ms": 1000,
            },
            baseline_node_options={},
            status="completed",
            created_by=uuid4(),
            created_at=created_at,
            usage_summary={},
            candidates=[
                candidate(other_candidate_id, "success"),
                candidate(candidate_id, "schema_failed"),
            ],
        )
        db = MagicMock()
        _configure_cost_optimizer_experiment_query(db, experiment)

        result = workflow_endpoint.get_cost_optimizer_experiment_candidate(
            db,
            workflow,
            "llm-triage",
            str(experiment_id),
            str(candidate_id),
        )

        assert result["experiment_id"] == str(experiment_id)
        assert result["candidate"]["candidate_id"] == str(candidate_id)
        assert result["candidate"]["status"] == "schema_failed"
        assert "candidates" not in result

    def test_fr13_detail_helper_rejects_candidate_outside_experiment(self):
        workflow_id = uuid4()
        organization_id = uuid4()
        experiment_id = uuid4()
        candidate_id = uuid4()
        workflow = SimpleNamespace(
            id=workflow_id,
            organization_id=organization_id,
        )
        experiment = SimpleNamespace(
            id=experiment_id,
            candidates=[SimpleNamespace(id=uuid4())],
        )
        db = MagicMock()
        _configure_cost_optimizer_experiment_query(db, experiment)

        with pytest.raises(HTTPException) as exc_info:
            workflow_endpoint.get_cost_optimizer_experiment_candidate(
                db,
                workflow,
                "llm-triage",
                str(experiment_id),
                str(candidate_id),
            )

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "resource.not_found"


class TestCostOptimizerBaselineHelpers:
    def test_candidate_data_defaults_answer_grounding_check_to_basic(self):
        candidate = workflow_endpoint._cost_optimizer_candidate_data_from_node_data(
            {"model_id": "gpt-4.1-mini"}
        )

        assert candidate["knowledge"]["answer_grounding_check"] == "basic"
        assert candidate["knowledge"]["score_threshold"] == 0.3
        assert candidate["knowledge"]["top_k"] == 5

    def test_fr7_downstream_compatibility_is_compatible_when_snapshot_matches(self):
        graph = {
            "nodes": [
                {"id": "llm-triage", "type": "llmNode"},
                {"id": "extract-result", "type": "variableExtractionNode"},
                {"id": "answer", "type": "answerNode"},
            ],
            "edges": [
                {"id": "e1", "source": "llm-triage", "target": "extract-result"},
                {"id": "e2", "source": "extract-result", "target": "answer"},
            ],
        }
        baseline_snapshot = workflow_endpoint.build_cost_optimizer_downstream_snapshot(
            graph,
            "llm-triage",
        )

        result = workflow_endpoint.build_cost_optimizer_downstream_compatibility(
            baseline_snapshot,
            graph,
            "llm-triage",
        )

        assert result["state"] == "compatible"
        assert result["label"] == "검증 가능"
        assert result["first_consumer_status"] == "same"
        assert result["contract_check"]["status"] == "pass"

    def test_fr7_downstream_compatibility_warns_when_first_consumer_is_same(self):
        baseline_graph = {
            "nodes": [
                {"id": "llm-triage", "type": "llmNode"},
                {"id": "extract-result", "type": "variableExtractionNode"},
            ],
            "edges": [
                {"id": "e1", "source": "llm-triage", "target": "extract-result"},
            ],
        }
        current_graph = {
            "nodes": [
                {"id": "llm-triage", "type": "llmNode"},
                {"id": "extract-result", "type": "variableExtractionNode"},
                {"id": "answer", "type": "answerNode"},
            ],
            "edges": [
                {"id": "e1", "source": "llm-triage", "target": "extract-result"},
                {"id": "e2", "source": "extract-result", "target": "answer"},
            ],
        }
        baseline_snapshot = workflow_endpoint.build_cost_optimizer_downstream_snapshot(
            baseline_graph,
            "llm-triage",
        )

        result = workflow_endpoint.build_cost_optimizer_downstream_compatibility(
            baseline_snapshot,
            current_graph,
            "llm-triage",
        )

        assert result["state"] == "warning"
        assert result["label"] == "주의 필요"
        assert result["first_consumer_status"] == "same"
        assert result["contract_check"]["status"] == "warning"

    def test_fr7_downstream_compatibility_is_incompatible_when_consumer_changes(self):
        baseline_graph = {
            "nodes": [
                {"id": "llm-triage", "type": "llmNode"},
                {"id": "extract-result", "type": "variableExtractionNode"},
            ],
            "edges": [
                {"id": "e1", "source": "llm-triage", "target": "extract-result"},
            ],
        }
        current_graph = {
            "nodes": [
                {"id": "llm-triage", "type": "llmNode"},
                {"id": "answer", "type": "answerNode"},
            ],
            "edges": [
                {"id": "e1", "source": "llm-triage", "target": "answer"},
            ],
        }
        baseline_snapshot = workflow_endpoint.build_cost_optimizer_downstream_snapshot(
            baseline_graph,
            "llm-triage",
        )

        result = workflow_endpoint.build_cost_optimizer_downstream_compatibility(
            baseline_snapshot,
            current_graph,
            "llm-triage",
        )

        assert result["state"] == "incompatible"
        assert result["label"] == "검증 불가"
        assert result["first_consumer_status"] == "changed"
        assert result["contract_check"]["status"] == "failed"

    def test_fr7_downstream_contract_fails_when_candidate_output_misses_required_key(
        self,
    ):
        graph = {
            "nodes": [
                {"id": "llm-triage", "type": "llmNode"},
                {
                    "id": "extract-result",
                    "type": "variableExtractionNode",
                    "data": {
                        "source_selector": ["llm-triage", "text"],
                        "mappings": [
                            {
                                "name": "승인 필요",
                                "json_path": "approvalRequired",
                            },
                            {
                                "name": "답변 초안",
                                "json_path": "mailDraft",
                            },
                        ],
                    },
                },
            ],
            "edges": [
                {"id": "e1", "source": "llm-triage", "target": "extract-result"},
            ],
        }
        baseline_snapshot = workflow_endpoint.build_cost_optimizer_downstream_snapshot(
            graph,
            "llm-triage",
        )

        result = workflow_endpoint.build_cost_optimizer_downstream_compatibility(
            baseline_snapshot,
            graph,
            "llm-triage",
            candidate_output={"text": '{"approvalRequired": true}'},
        )

        assert result["state"] == "incompatible"
        assert result["contract_check"]["status"] == "failed"
        assert result["contract_check"]["checked_node_ids"] == ["extract-result"]
        assert "mailDraft" in result["contract_check"]["warnings"][0]

    def test_fr7_downstream_contract_passes_when_candidate_output_has_required_keys(
        self,
    ):
        graph = {
            "nodes": [
                {"id": "llm-triage", "type": "llmNode"},
                {
                    "id": "extract-result",
                    "type": "variableExtractionNode",
                    "data": {
                        "source_selector": ["llm-triage", "text"],
                        "mappings": [
                            {
                                "name": "승인 필요",
                                "json_path": "approvalRequired",
                            },
                            {
                                "name": "답변 초안",
                                "json_path": "mailDraft",
                            },
                        ],
                    },
                },
            ],
            "edges": [
                {"id": "e1", "source": "llm-triage", "target": "extract-result"},
            ],
        }
        baseline_snapshot = workflow_endpoint.build_cost_optimizer_downstream_snapshot(
            graph,
            "llm-triage",
        )

        result = workflow_endpoint.build_cost_optimizer_downstream_compatibility(
            baseline_snapshot,
            graph,
            "llm-triage",
            candidate_output={
                "text": '{"approvalRequired": true, "mailDraft": "안내드립니다."}'
            },
        )

        assert result["state"] == "compatible"
        assert result["contract_check"]["status"] == "pass"
        assert result["contract_check"]["warnings"] == []

    def test_fr7_downstream_contract_uses_baseline_snapshot_contracts(self):
        baseline_graph = {
            "nodes": [
                {"id": "llm-triage", "type": "llmNode"},
                {
                    "id": "extract-result",
                    "type": "variableExtractionNode",
                    "data": {
                        "source_selector": ["llm-triage", "text"],
                        "mappings": [
                            {
                                "name": "답변 초안",
                                "json_path": "mailDraft",
                            }
                        ],
                    },
                },
            ],
            "edges": [
                {"id": "e1", "source": "llm-triage", "target": "extract-result"},
            ],
        }
        current_graph_without_contract_data = {
            "nodes": [
                {"id": "llm-triage", "type": "llmNode"},
                {
                    "id": "extract-result",
                    "type": "variableExtractionNode",
                    "data": {},
                },
            ],
            "edges": [
                {"id": "e1", "source": "llm-triage", "target": "extract-result"},
            ],
        }
        baseline_snapshot = workflow_endpoint.build_cost_optimizer_downstream_snapshot(
            baseline_graph,
            "llm-triage",
        )

        result = workflow_endpoint.build_cost_optimizer_downstream_compatibility(
            baseline_snapshot,
            current_graph_without_contract_data,
            "llm-triage",
            candidate_output={"text": '{"approvalRequired": true}'},
        )

        assert result["state"] == "incompatible"
        assert result["contract_check"]["status"] == "failed"
        assert result["contract_check"]["checked_node_ids"] == ["extract-result"]
        assert "mailDraft" in result["contract_check"]["warnings"][0]

    def test_fr7_downstream_missing_snapshot_returns_skipped_fallback(self):
        result = workflow_endpoint.build_cost_optimizer_downstream_compatibility(
            None,
            {"nodes": [], "edges": []},
            "llm-triage",
            candidate_output={"text": "{}"},
        )

        assert result["state"] == "unknown"
        assert result["contract_check"]["status"] == "skipped"
        assert "baseline_downstream_snapshot_unavailable" in result["contract_check"]["warnings"]

    @pytest.mark.parametrize(
        ("consumer", "warning_key"),
        [
            (
                {
                    "id": "condition-check",
                    "type": "conditionNode",
                    "data": {
                        "cases": [
                            {
                                "conditions": [
                                    {
                                        "variable_selector": [
                                            "llm-triage",
                                            "approvalRequired",
                                        ]
                                    }
                                ]
                            }
                        ]
                    },
                },
                "approvalRequired",
            ),
            (
                {
                    "id": "answer",
                    "type": "answerNode",
                    "data": {
                        "outputs": [
                            {"value_selector": ["llm-triage", "mailDraft"]}
                        ]
                    },
                },
                "mailDraft",
            ),
            (
                {
                    "id": "slack-notify",
                    "type": "slackPostNode",
                    "data": {
                        "referenced_variables": [
                            {"value_selector": ["llm-triage", "summary"]}
                        ]
                    },
                },
                "summary",
            ),
        ],
    )
    def test_fr7_downstream_contract_fails_when_direct_consumer_selector_is_missing(
        self,
        consumer,
        warning_key,
    ):
        graph = {
            "nodes": [
                {"id": "llm-triage", "type": "llmNode"},
                consumer,
            ],
            "edges": [
                {"id": "e1", "source": "llm-triage", "target": consumer["id"]},
            ],
        }
        baseline_snapshot = workflow_endpoint.build_cost_optimizer_downstream_snapshot(
            graph,
            "llm-triage",
        )

        result = workflow_endpoint.build_cost_optimizer_downstream_compatibility(
            baseline_snapshot,
            graph,
            "llm-triage",
            candidate_output={"text": '{"other": true}'},
        )

        assert result["state"] == "incompatible"
        assert result["contract_check"]["status"] == "failed"
        assert result["contract_check"]["checked_node_ids"] == [consumer["id"]]
        assert warning_key in result["contract_check"]["warnings"][0]

    def test_fr2_latest_baseline_ignores_non_comparable_rows(self):
        workflow = SimpleNamespace(id=uuid4())
        comparable = _baseline_row(
            baseline_id=uuid4(),
            workflow_run_id=uuid4(),
            input_available=True,
            compare_available=True,
        )
        comparable["run_started_at"] = "2026-07-03T00:00:00+00:00"
        non_comparable_newer = _baseline_row(
            baseline_id=uuid4(),
            workflow_run_id=uuid4(),
            input_available=False,
            compare_available=False,
        )
        non_comparable_newer["run_started_at"] = "2026-07-04T00:00:00+00:00"

        with patch(
            "apps.gateway.api.v1.endpoints.workflow._cost_optimizer_baseline_rows",
            return_value=[comparable, non_comparable_newer],
        ):
            result = workflow_endpoint.get_cost_optimizer_latest_baseline(
                MagicMock(),
                workflow,
                "llm-triage",
            )

        assert result["baseline_id"] == comparable["baseline_id"]
        assert result["compare_available"] is True

    def test_fr2_latest_baseline_raises_when_only_non_comparable_rows_exist(self):
        workflow = SimpleNamespace(id=uuid4())
        non_comparable = _baseline_row(input_available=False, compare_available=False)

        with (
            patch(
                "apps.gateway.api.v1.endpoints.workflow._cost_optimizer_baseline_rows",
                return_value=[non_comparable],
            ),
        ):
            try:
                workflow_endpoint.get_cost_optimizer_latest_baseline(
                    MagicMock(),
                    workflow,
                    "llm-triage",
                )
            except HTTPException as exc:
                assert exc.status_code == 400
                assert exc.detail == "cost_optimizer.no_baseline"
            else:
                raise AssertionError("expected cost_optimizer.no_baseline")

    def test_fr2_baseline_list_filters_by_search_model_date_and_compare_available(self):
        workflow = SimpleNamespace(id=uuid4())
        matched = _baseline_row()
        matched.update(
            {
                "baseline_id": "matched",
                "run_started_at": "2026-07-03T00:00:00+00:00",
                "model": "gpt-4.1-mini",
                "input_preview": "billing escalation",
                "output_preview": "enterprise response",
                "compare_available": True,
            }
        )
        wrong_query = _baseline_row()
        wrong_query.update(
            {
                "baseline_id": "wrong-query",
                "run_started_at": "2026-07-03T00:00:00+00:00",
                "model": "gpt-4.1-mini",
                "input_preview": "refund request",
                "output_preview": "consumer response",
                "compare_available": True,
            }
        )
        wrong_model = _baseline_row()
        wrong_model.update(
            {
                "baseline_id": "wrong-model",
                "run_started_at": "2026-07-03T00:00:00+00:00",
                "model": "claude-3-haiku",
                "input_preview": "billing escalation",
                "output_preview": "enterprise response",
                "compare_available": True,
            }
        )
        wrong_compare_state = _baseline_row(input_available=False, compare_available=False)
        wrong_compare_state.update(
            {
                "baseline_id": "wrong-compare",
                "run_started_at": "2026-07-03T00:00:00+00:00",
                "model": "gpt-4.1-mini",
                "input_preview": "billing escalation",
                "output_preview": "enterprise response",
            }
        )
        outside_date = _baseline_row()
        outside_date.update(
            {
                "baseline_id": "outside-date",
                "run_started_at": "2026-06-30T00:00:00+00:00",
                "model": "gpt-4.1-mini",
                "input_preview": "billing escalation",
                "output_preview": "enterprise response",
                "compare_available": True,
            }
        )

        with patch(
            "apps.gateway.api.v1.endpoints.workflow._cost_optimizer_baseline_rows",
            return_value=[
                matched,
                wrong_query,
                wrong_model,
                wrong_compare_state,
                outside_date,
            ],
        ):
            result = workflow_endpoint.list_cost_optimizer_baselines(
                MagicMock(),
                workflow,
                "llm-triage",
                q="billing",
                model="gpt-4.1-mini",
                date_from=datetime(2026, 7, 1, tzinfo=timezone.utc),
                date_to=datetime(2026, 7, 4, tzinfo=timezone.utc),
                compare_available=True,
            )

        assert result["total"] == 1
        assert [row["baseline_id"] for row in result["items"]] == ["matched"]

    def test_fr2_baseline_list_excludes_output_unavailable_rows(self):
        workflow = SimpleNamespace(id=uuid4())
        matched = _baseline_row()
        matched.update({"baseline_id": "matched", "output_available": True})
        missing_output = _baseline_row()
        missing_output.update(
            {
                "baseline_id": "missing-output",
                "output_available": False,
                "compare_available": False,
                "unavailable_reason": "output_payload_unavailable",
            }
        )

        with patch(
            "apps.gateway.api.v1.endpoints.workflow._cost_optimizer_baseline_rows",
            return_value=[matched, missing_output],
        ):
            result = workflow_endpoint.list_cost_optimizer_baselines(
                MagicMock(),
                workflow,
                "llm-triage",
            )

        assert result["total"] == 1
        assert [row["baseline_id"] for row in result["items"]] == ["matched"]

    def test_fr2_baseline_list_excludes_usage_unavailable_rows(self):
        workflow = SimpleNamespace(id=uuid4())
        matched = _baseline_row()
        matched.update({"baseline_id": "matched", "usage_available": True})
        missing_usage = _baseline_row()
        missing_usage.update(
            {
                "baseline_id": "missing-usage",
                "usage_available": False,
                "compare_available": False,
                "unavailable_reason": "usage_summary_unavailable",
            }
        )

        with patch(
            "apps.gateway.api.v1.endpoints.workflow._cost_optimizer_baseline_rows",
            return_value=[matched, missing_usage],
        ):
            result = workflow_endpoint.list_cost_optimizer_baselines(
                MagicMock(),
                workflow,
                "llm-triage",
            )

        assert result["total"] == 1
        assert [row["baseline_id"] for row in result["items"]] == ["matched"]

    def test_fr2_baseline_list_applies_sort_and_offset_limit(self):
        workflow = SimpleNamespace(id=uuid4())
        cheap = _baseline_row()
        cheap.update({"baseline_id": "cheap", "cost": 0.1, "total_tokens": 100})
        medium = _baseline_row()
        medium.update({"baseline_id": "medium", "cost": 0.5, "total_tokens": 300})
        expensive = _baseline_row()
        expensive.update({"baseline_id": "expensive", "cost": 0.9, "total_tokens": 900})

        with patch(
            "apps.gateway.api.v1.endpoints.workflow._cost_optimizer_baseline_rows",
            return_value=[cheap, expensive, medium],
        ):
            result = workflow_endpoint.list_cost_optimizer_baselines(
                MagicMock(),
                workflow,
                "llm-triage",
                sort="cost_desc",
                limit=1,
                offset=1,
            )

        assert result["total"] == 3
        assert result["limit"] == 1
        assert result["offset"] == 1
        assert [row["baseline_id"] for row in result["items"]] == ["medium"]

    def test_fr2_baseline_row_redacts_secret_values_from_previews_and_payload(self):
        workflow = SimpleNamespace(id=uuid4())
        run = SimpleNamespace(
            id=uuid4(),
            workflow_id=workflow.id,
            status=SimpleNamespace(value="success"),
            started_at=datetime(2026, 7, 4, tzinfo=timezone.utc),
        )
        node_run = SimpleNamespace(
            id=uuid4(),
            workflow_run_id=run.id,
            node_id="llm-triage",
            node_type="llmNode",
            status=SimpleNamespace(value="success"),
            inputs={"message": "hello", "api_key": "sk-secret"},
            outputs={"answer": "done", "encrypted_config": "ciphertext"},
            process_data={
                "node_options": {
                    "model_id": "gpt-4.1-mini",
                    "api_key": "sk-process-secret",
                    "parameters": {"temperature": 0.2},
                }
            },
            error_message=None,
            trace_metadata={"trace_id": "trace-1"},
        )
        usage = SimpleNamespace(
            model=SimpleNamespace(model_id_for_api_call="gpt-4.1-mini"),
            model_id=uuid4(),
            prompt_tokens=10,
            completion_tokens=5,
            total_cost=0.0001,
            latency_ms=300,
            status="success",
        )

        row = workflow_endpoint._baseline_row_from_records(
            workflow=workflow,
            run=run,
            node_run=node_run,
            usage=usage,
        )

        serialized = str(row)
        assert "sk-secret" not in serialized
        assert "sk-process-secret" not in serialized
        assert "ciphertext" not in serialized
        assert row["input"]["api_key"] == "[REDACTED]"
        assert row["output"]["encrypted_config"] == "[REDACTED]"
        assert row["node_options"]["api_key"] == "[REDACTED]"
        assert row["node_options"]["model_id"] == "gpt-4.1-mini"

    def test_fr11_baseline_row_preserves_safe_current_routing_summary(self):
        workflow = SimpleNamespace(id=uuid4())
        run = SimpleNamespace(
            id=uuid4(),
            workflow_id=workflow.id,
            status=SimpleNamespace(value="success"),
            started_at=datetime(2026, 7, 4, tzinfo=timezone.utc),
        )
        node_run = SimpleNamespace(
            id=uuid4(),
            workflow_run_id=run.id,
            node_id="llm-triage",
            node_type="llmNode",
            status=SimpleNamespace(value="success"),
            inputs={"message": "safe input"},
            outputs={"answer": "done"},
            process_data={"node_options": {"model_id": "gpt-4o-mini"}},
            error_message=None,
            trace_metadata={
                "llm": {
                    "selected_model": "gpt-4o-mini",
                    "fallback_model": "gpt-4.1-mini",
                    "strategy_id": "judge_bootstrap_incremental_v1",
                    "matched_rule_id": "difficulty-balanced",
                    "policy_version": "routing-policy-v3",
                    "reason_code": "validated_quality_floor_positive_net_saving",
                    "query_vector": [0.1, 0.2, 0.3],
                    "raw_input": "must-not-leak",
                }
            },
        )
        usage = SimpleNamespace(
            model=SimpleNamespace(model_id_for_api_call="gpt-4o-mini"),
            model_id=uuid4(),
            prompt_tokens=10,
            completion_tokens=5,
            total_cost=0.0001,
            latency_ms=300,
            status="success",
        )

        row = workflow_endpoint._baseline_row_from_records(
            workflow=workflow,
            run=run,
            node_run=node_run,
            usage=usage,
        )

        assert row["trace"]["model_routing"] == {
            "selected_model": "gpt-4o-mini",
            "fallback_model": "gpt-4.1-mini",
            "strategy_id": "judge_bootstrap_incremental_v1",
            "matched_rule_id": "difficulty-balanced",
            "policy_version": "routing-policy-v3",
            "reason_code": "validated_quality_floor_positive_net_saving",
        }
        assert "must-not-leak" not in str(row)
        assert "query_vector" not in str(row)

    def test_fr2_baseline_row_uses_node_run_duration_when_usage_latency_is_zero(self):
        workflow = SimpleNamespace(id=uuid4())
        run = SimpleNamespace(
            id=uuid4(),
            workflow_id=workflow.id,
            status=SimpleNamespace(value="success"),
            started_at=datetime(2026, 7, 4, tzinfo=timezone.utc),
        )
        node_run = SimpleNamespace(
            id=uuid4(),
            workflow_run_id=run.id,
            node_id="llm-triage",
            node_type="llmNode",
            status=SimpleNamespace(value="success"),
            inputs={"message": "hello"},
            outputs={"answer": "done"},
            process_data={},
            error_message=None,
            trace_metadata={},
            duration=2.395438,
        )
        usage = SimpleNamespace(
            model=SimpleNamespace(model_id_for_api_call="gpt-4.1-mini"),
            model_id=uuid4(),
            prompt_tokens=10,
            completion_tokens=5,
            total_cost=0.0001,
            latency_ms=0,
            status="success",
        )

        row = workflow_endpoint._baseline_row_from_records(
            workflow=workflow,
            run=run,
            node_run=node_run,
            usage=usage,
        )

        assert row["latency_ms"] == 2395
        assert row["usage"]["latency_ms"] == 2395

    def test_fr2_baseline_query_filters_failed_node_runs_at_db_boundary(self):
        db = MagicMock()
        query = db.query.return_value
        workflow = SimpleNamespace(id=uuid4())

        workflow_endpoint._cost_optimizer_baseline_rows(db, workflow, "llm-triage")

        filter_args = query.join.return_value.join.return_value.filter.call_args.args
        assert any(
            str(arg).endswith("workflow_node_runs.status = :status_1")
            for arg in filter_args
        )
        assert any(
            getattr(arg, "right", None).value == NodeRunStatus.SUCCESS
            for arg in filter_args
            if hasattr(getattr(arg, "right", None), "value")
        )

    def test_fr2_baseline_query_does_not_require_node_run_outputs_at_db_boundary(self):
        db = MagicMock()
        query = db.query.return_value
        workflow = SimpleNamespace(id=uuid4())

        workflow_endpoint._cost_optimizer_baseline_rows(db, workflow, "llm-triage")

        filter_args = query.join.return_value.join.return_value.filter.call_args.args
        assert not any(
            "workflow_node_runs.outputs IS NOT NULL" in str(arg)
            for arg in filter_args
        )

    def test_fr2_baseline_query_excludes_cost_optimizer_candidate_runs(self):
        db = MagicMock()
        query = db.query.return_value
        workflow = SimpleNamespace(id=uuid4())

        workflow_endpoint._cost_optimizer_baseline_rows(db, workflow, "llm-triage")

        filter_args = query.join.return_value.join.return_value.filter.call_args.args
        assert any(
            "llm_usage_logs.cost_optimizer_candidate_id IS NULL" in str(arg)
            for arg in filter_args
        )
        assert any(
            len(call.args) == 1 and call.args[0] is CostOptimizerCandidate.id
            for call in db.query.call_args_list
        )

    def test_fr2_trace_payload_redacted_input_should_drive_preview_and_availability(self):
        workflow = SimpleNamespace(id=uuid4())
        run = SimpleNamespace(
            id=uuid4(),
            workflow_id=workflow.id,
            status=SimpleNamespace(value="success"),
            started_at=datetime(2026, 7, 4, tzinfo=timezone.utc),
        )
        node_run = SimpleNamespace(
            id=uuid4(),
            workflow_run_id=run.id,
            node_id="llm-triage",
            node_type="llmNode",
            status=SimpleNamespace(value="success"),
            inputs=None,
            outputs={"fallback": "node output should not be preview source"},
            error_message=None,
            trace_metadata={"trace_id": "trace-1"},
            trace_payloads=[
                SimpleNamespace(
                    payload_kind="input",
                    retention_purged_at=None,
                    redacted_payload={"message": "trace safe input"},
                ),
                SimpleNamespace(
                    payload_kind="output",
                    retention_purged_at=None,
                    redacted_payload={"answer": "trace safe output"},
                ),
            ],
        )
        usage = SimpleNamespace(
            model=SimpleNamespace(model_id_for_api_call="gpt-4.1-mini"),
            model_id=uuid4(),
            prompt_tokens=10,
            completion_tokens=5,
            total_cost=0.0001,
            latency_ms=300,
            status="success",
        )

        row = workflow_endpoint._baseline_row_from_records(
            workflow=workflow,
            run=run,
            node_run=node_run,
            usage=usage,
        )

        assert row["input_available"] is True
        assert row["compare_available"] is True
        assert "trace safe input" in row["input_preview"]
        assert "trace safe output" in row["output_preview"]

    def test_fr2_purged_trace_input_marks_row_not_comparable(self):
        workflow = SimpleNamespace(id=uuid4())
        run = SimpleNamespace(
            id=uuid4(),
            workflow_id=workflow.id,
            status=SimpleNamespace(value="success"),
            started_at=datetime(2026, 7, 4, tzinfo=timezone.utc),
        )
        node_run = SimpleNamespace(
            id=uuid4(),
            workflow_run_id=run.id,
            node_id="llm-triage",
            node_type="llmNode",
            status=SimpleNamespace(value="success"),
            inputs={"message": "node input should be blocked by trace retention"},
            outputs={"answer": "ok"},
            error_message=None,
            trace_metadata={"trace_id": "trace-1"},
            trace_payloads=[
                SimpleNamespace(
                    payload_kind="input",
                    retention_purged_at=datetime(2026, 7, 5, tzinfo=timezone.utc),
                    redacted_payload={"message": "purged input"},
                ),
                SimpleNamespace(
                    payload_kind="output",
                    retention_purged_at=None,
                    redacted_payload={"answer": "safe output"},
                ),
            ],
        )
        usage = SimpleNamespace(
            model=SimpleNamespace(model_id_for_api_call="gpt-4.1-mini"),
            model_id=uuid4(),
            prompt_tokens=10,
            completion_tokens=5,
            total_cost=0.0001,
            latency_ms=300,
            status="success",
        )

        row = workflow_endpoint._baseline_row_from_records(
            workflow=workflow,
            run=run,
            node_run=node_run,
            usage=usage,
        )

        assert row["input_available"] is False
        assert row["compare_available"] is False
        assert row["unavailable_reason"] == "input_payload_unavailable"
