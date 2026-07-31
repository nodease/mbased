import uuid
from types import SimpleNamespace

import pytest

from apps.gateway.services.deployment_parameter_optimization_service import (
    DeploymentParameterOptimizationBudgetExceeded,
    DeploymentParameterOptimizationConfigurationError,
    DeploymentParameterOptimizationService,
)
from apps.shared.db.models.deployment_parameter_optimization import (
    DeploymentParameterOptimizationPlan,
)
from apps.shared.schemas.deployment import DeploymentParameterOptimizationConfig


class _Query:
    def filter(self, *_args):
        return self

    def first(self):
        return None


class _Db:
    def __init__(self):
        self.added = []

    def query(self, *_args):
        return _Query()

    def add(self, value):
        self.added.append(value)


class _PlanQuery:
    def __init__(self, plan):
        self.plan = plan

    def filter(self, *_args):
        return self

    def first(self):
        return self.plan

    def all(self):
        return [self.plan] if self.plan is not None else []


class _PlanDb:
    def __init__(self, plan):
        self.plan = plan

    def query(self, *_args):
        return _PlanQuery(self.plan)


def _deployment():
    return SimpleNamespace(
        id=uuid.uuid4(),
        app_id=uuid.uuid4(),
        graph_snapshot=_graph(),
    )


def _graph():
    return {
        "nodes": [
            {"id": "llm-triage", "type": "llmNode"},
            {"id": "answer", "type": "answerNode"},
        ]
    }


def test_configure_for_deployment_creates_a_parameter_only_plan():
    db = _Db()
    deployment = _deployment()
    config = DeploymentParameterOptimizationConfig(
        enabled=True,
        node_ids=["llm-triage"],
        check_every_runs=50,
        monthly_validation_budget_usd=3,
    )

    plan = DeploymentParameterOptimizationService.configure_for_deployment(
        db,
        deployment=deployment,
        workflow_id=uuid.uuid4(),
        graph_snapshot=_graph(),
        config=config,
    )

    assert plan is db.added[0]
    assert plan.node_ids == ["llm-triage"]
    assert plan.check_every_runs == 50
    assert float(plan.monthly_validation_budget_usd) == 3
    assert plan.active_parameter_patch == {}
    assert plan.status == "collecting"


def test_configure_for_deployment_preserves_targets_for_disabled_management(
    monkeypatch,
):
    db = _Db()
    deployment = _deployment()
    config = DeploymentParameterOptimizationConfig(
        enabled=False,
        node_ids=["llm-triage"],
        check_every_runs=50,
        monthly_validation_budget_usd=3,
    )

    plan = DeploymentParameterOptimizationService.configure_for_deployment(
        db,
        deployment=deployment,
        workflow_id=uuid.uuid4(),
        graph_snapshot=_graph(),
        config=config,
    )

    assert plan is db.added[0]
    assert plan.enabled is False
    assert plan.status == "disabled"
    assert plan.node_ids == ["llm-triage"]
    assert plan.check_every_runs == 50
    assert float(plan.monthly_validation_budget_usd) == 3

    monkeypatch.setattr(
        DeploymentParameterOptimizationService,
        "_successful_run_counts",
        classmethod(lambda _cls, _db, _plans: {}),
    )
    summary = DeploymentParameterOptimizationService.summary_for_deployment(
        _PlanDb(plan),
        deployment.id,
    )
    assert summary["enabled"] is False
    assert summary["node_ids"] == ["llm-triage"]
    assert summary["node_count"] == 1

    updated = DeploymentParameterOptimizationService.update_plan(
        _PlanDb(plan),
        deployment=deployment,
        workflow_id=plan.workflow_id,
        config=DeploymentParameterOptimizationConfig(enabled=True),
    )
    assert updated is plan
    assert updated.enabled is True
    assert updated.status == "collecting"
    assert updated.node_ids == ["llm-triage"]


def test_disabled_deployment_without_llm_nodes_keeps_deployment_available():
    db = _Db()

    plan = DeploymentParameterOptimizationService.configure_for_deployment(
        db,
        deployment=_deployment(),
        workflow_id=uuid.uuid4(),
        graph_snapshot={"nodes": [{"id": "answer", "type": "answerNode"}]},
        config=DeploymentParameterOptimizationConfig(enabled=False),
    )

    assert plan is db.added[0]
    assert plan.enabled is False
    assert plan.status == "disabled"
    assert plan.node_ids == []


def test_configure_for_deployment_rejects_nodes_outside_the_snapshot():
    db = _Db()
    config = DeploymentParameterOptimizationConfig(
        enabled=True,
        node_ids=["missing-node"],
    )

    with pytest.raises(DeploymentParameterOptimizationConfigurationError):
        DeploymentParameterOptimizationService.configure_for_deployment(
            db,
            deployment=_deployment(),
            workflow_id=uuid.uuid4(),
            graph_snapshot=_graph(),
            config=config,
        )


def test_record_validation_spend_tracks_selected_node_and_blocks_at_budget():
    deployment = _deployment()
    plan = DeploymentParameterOptimizationPlan(
        deployment_id=deployment.id,
        app_id=deployment.app_id,
        workflow_id=uuid.uuid4(),
        node_ids=["llm-triage"],
        enabled=True,
        check_every_runs=50,
        monthly_validation_budget_usd=0.18,
        validation_spend_usd=0.0,
        validation_spend_month=DeploymentParameterOptimizationService._current_month_key(),
        status="collecting",
        active_parameter_patch={},
    )
    db = _PlanDb(plan)

    DeploymentParameterOptimizationService.record_validation_spend(
        db,
        deployment_id=deployment.id,
        node_id="llm-triage",
        amount_usd=0.18,
    )

    assert float(plan.validation_spend_usd) == 0.18
    assert plan.last_evaluated_at is not None
    with pytest.raises(DeploymentParameterOptimizationBudgetExceeded):
        DeploymentParameterOptimizationService.ensure_validation_budget_available(
            db,
            deployment_id=deployment.id,
            node_id="llm-triage",
        )


def test_record_validation_spend_ignores_unselected_node():
    deployment = _deployment()
    plan = DeploymentParameterOptimizationPlan(
        deployment_id=deployment.id,
        app_id=deployment.app_id,
        workflow_id=uuid.uuid4(),
        node_ids=["llm-triage"],
        enabled=True,
        check_every_runs=50,
        monthly_validation_budget_usd=3.0,
        validation_spend_usd=0.0,
        validation_spend_month=DeploymentParameterOptimizationService._current_month_key(),
        status="collecting",
        active_parameter_patch={},
    )

    DeploymentParameterOptimizationService.record_validation_spend(
        _PlanDb(plan),
        deployment_id=deployment.id,
        node_id="another-llm-node",
        amount_usd=0.18,
    )

    assert float(plan.validation_spend_usd) == 0.0
