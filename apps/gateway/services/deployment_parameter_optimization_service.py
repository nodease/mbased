"""Deployment-scoped automatic LLM parameter optimization configuration."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session

from apps.shared.db.models.deployment_parameter_optimization import (
    DeploymentParameterOptimizationPlan,
)
from apps.shared.db.models.workflow_run import (
    NodeRunStatus,
    RunStatus,
    RunTriggerMode,
    WorkflowNodeRun,
    WorkflowRun,
)
from apps.shared.schemas.deployment import DeploymentParameterOptimizationConfig


OPERATION_TRIGGER_MODES = (
    RunTriggerMode.API,
    RunTriggerMode.WEBHOOK,
    RunTriggerMode.SCHEDULER,
    RunTriggerMode.APP,
)


class DeploymentParameterOptimizationConfigurationError(ValueError):
    """Raised when a deployment optimization setting cannot target its graph."""


class DeploymentParameterOptimizationBudgetExceeded(ValueError):
    """Raised before a plan-scoped recommendation verification exceeds its budget."""


class DeploymentParameterOptimizationService:
    """Persists deployment settings and builds safe list summaries.

    This service intentionally does not manage automatic model routing. The
    plan only describes evidence collection and validation budget controls for
    LLM parameter optimization.
    """

    @classmethod
    def configure_for_deployment(
        cls,
        db: Session,
        *,
        deployment: Any,
        workflow_id: Any,
        graph_snapshot: dict[str, Any],
        config: DeploymentParameterOptimizationConfig | None,
    ) -> DeploymentParameterOptimizationPlan | None:
        if config is None:
            return None

        llm_node_ids = cls._llm_node_ids(graph_snapshot)
        target_node_ids = (
            cls._target_node_ids(config, llm_node_ids)
            if config.enabled
            else llm_node_ids
        )
        existing = (
            db.query(DeploymentParameterOptimizationPlan)
            .filter(
                DeploymentParameterOptimizationPlan.deployment_id == deployment.id
            )
            .first()
        )
        if existing is None:
            existing = DeploymentParameterOptimizationPlan(
                deployment_id=deployment.id,
                app_id=deployment.app_id,
                workflow_id=workflow_id,
                node_ids=target_node_ids,
                enabled=config.enabled,
                check_every_runs=config.check_every_runs,
                monthly_validation_budget_usd=config.monthly_validation_budget_usd,
                validation_spend_usd=0.0,
                validation_spend_month=cls._current_month_key(),
                status="collecting" if config.enabled else "disabled",
                active_parameter_patch={},
            )
            db.add(existing)
            return existing

        existing.node_ids = target_node_ids
        existing.enabled = config.enabled
        existing.check_every_runs = config.check_every_runs
        existing.monthly_validation_budget_usd = config.monthly_validation_budget_usd
        if not config.enabled:
            existing.status = "disabled"
        elif existing.status in {"disabled", "paused", "failed"}:
            existing.status = "collecting"
        cls._reset_monthly_spend_if_needed(existing)
        return existing

    @classmethod
    def update_plan(
        cls,
        db: Session,
        *,
        deployment: Any,
        workflow_id: Any,
        config: DeploymentParameterOptimizationConfig,
    ) -> DeploymentParameterOptimizationPlan | None:
        return cls.configure_for_deployment(
            db,
            deployment=deployment,
            workflow_id=workflow_id,
            graph_snapshot=deployment.graph_snapshot or {},
            config=config,
        )

    @classmethod
    def disable_plan(cls, db: Session, *, deployment_id: Any) -> None:
        plan = cls._plan_for_deployment(db, deployment_id)
        if plan is None:
            return
        plan.enabled = False
        plan.status = "disabled"

    @classmethod
    def ensure_validation_budget_available(
        cls,
        db: Session,
        *,
        deployment_id: Any | None,
        node_id: str,
    ) -> None:
        """Block a new recommendation verification only for its owning plan.

        A manual comparison outside an enabled deployment plan must remain
        independent from this budget. The actual cost is unknown until the
        candidate finishes, so this check prevents a new run once the already
        recorded monthly spend has reached the configured cap.
        """
        plan = cls._plan_for_deployment(db, deployment_id)
        if not cls._is_validation_target(plan, node_id):
            return

        cls._reset_monthly_spend_if_needed(plan)
        budget = float(plan.monthly_validation_budget_usd or 0.0)
        spend = float(plan.validation_spend_usd or 0.0)
        if budget > 0 and spend >= budget:
            raise DeploymentParameterOptimizationBudgetExceeded(
                "deployment.parameter_optimization_validation_budget_exhausted"
            )

    @classmethod
    def record_validation_spend(
        cls,
        db: Session,
        *,
        deployment_id: Any | None,
        node_id: str,
        amount_usd: float | None,
    ) -> None:
        """Add a completed recommendation verification's known new cost.

        Only the candidate execution and optional quality-judge execution are
        included. Baseline reads and normal deployment traffic are not
        validation spend, and plans that do not target this node are untouched.
        """
        if amount_usd is None or amount_usd <= 0:
            return

        plan = cls._plan_for_deployment(db, deployment_id)
        if not cls._is_validation_target(plan, node_id):
            return

        cls._reset_monthly_spend_if_needed(plan)
        plan.validation_spend_usd = float(plan.validation_spend_usd or 0.0) + float(
            amount_usd
        )
        plan.last_evaluated_at = datetime.now(timezone.utc)

    @classmethod
    def summary_for_deployment(
        cls,
        db: Session,
        deployment_id: Any,
    ) -> dict[str, Any]:
        return cls.summaries_by_deployment_id(db, [deployment_id]).get(
            deployment_id,
            cls._disabled_summary(),
        )

    @classmethod
    def summaries_by_deployment_id(
        cls,
        db: Session,
        deployment_ids: list[Any],
    ) -> dict[Any, dict[str, Any]]:
        ids = [deployment_id for deployment_id in deployment_ids if deployment_id]
        if not ids or not hasattr(db, "query"):
            return {}

        plans = (
            db.query(DeploymentParameterOptimizationPlan)
            .filter(DeploymentParameterOptimizationPlan.deployment_id.in_(ids))
            .all()
        )
        plans = [
            plan
            for plan in plans
            if isinstance(plan, DeploymentParameterOptimizationPlan)
        ]
        summaries = {deployment_id: cls._disabled_summary() for deployment_id in ids}
        if not plans:
            return summaries

        for plan in plans:
            cls._reset_monthly_spend_if_needed(plan)

        successful_runs_by_node = cls._successful_run_counts(db, plans)
        for plan in plans:
            node_ids = [str(node_id) for node_id in plan.node_ids if node_id]
            counts = [
                successful_runs_by_node.get((plan.deployment_id, node_id), 0)
                for node_id in node_ids
            ]
            collected_runs = min(counts) if counts else 0
            budget = float(plan.monthly_validation_budget_usd or 0.0)
            spend = float(plan.validation_spend_usd or 0.0)
            status = cls._summary_status(plan, collected_runs, spend, budget)
            summaries[plan.deployment_id] = {
                "enabled": bool(plan.enabled),
                "status": status,
                "node_ids": node_ids,
                "node_count": len(node_ids),
                "collected_runs": collected_runs,
                "check_every_runs": int(plan.check_every_runs or 50),
                "validation_spend_usd": round(spend, 6),
                "monthly_validation_budget_usd": round(budget, 6),
            }
        return summaries

    @staticmethod
    def _llm_node_ids(graph_snapshot: dict[str, Any]) -> list[str]:
        nodes = graph_snapshot.get("nodes") if isinstance(graph_snapshot, dict) else []
        if not isinstance(nodes, list):
            return []
        return [
            str(node.get("id"))
            for node in nodes
            if isinstance(node, dict)
            and node.get("type") == "llmNode"
            and node.get("id")
        ]

    @classmethod
    def _target_node_ids(
        cls,
        config: DeploymentParameterOptimizationConfig,
        llm_node_ids: list[str],
    ) -> list[str]:
        if not llm_node_ids:
            raise DeploymentParameterOptimizationConfigurationError(
                "deployment.parameter_optimization_no_llm_node"
            )
        requested = [str(node_id) for node_id in config.node_ids if node_id]
        target_node_ids = requested or llm_node_ids
        invalid = set(target_node_ids).difference(llm_node_ids)
        if invalid:
            raise DeploymentParameterOptimizationConfigurationError(
                "deployment.parameter_optimization_invalid_node"
            )
        return list(dict.fromkeys(target_node_ids))

    @classmethod
    def _successful_run_counts(
        cls,
        db: Session,
        plans: list[DeploymentParameterOptimizationPlan],
    ) -> dict[tuple[Any, str], int]:
        deployment_ids = [plan.deployment_id for plan in plans]
        target_node_ids = {
            str(node_id)
            for plan in plans
            for node_id in plan.node_ids
            if node_id
        }
        if not deployment_ids or not target_node_ids:
            return {}
        rows = (
            db.query(
                WorkflowRun.deployment_id,
                WorkflowNodeRun.node_id,
                func.count(func.distinct(WorkflowRun.id)),
            )
            .join(WorkflowNodeRun, WorkflowNodeRun.workflow_run_id == WorkflowRun.id)
            .filter(WorkflowRun.deployment_id.in_(deployment_ids))
            .filter(WorkflowRun.status == RunStatus.SUCCESS)
            .filter(WorkflowRun.trigger_mode.in_(OPERATION_TRIGGER_MODES))
            .filter(WorkflowNodeRun.node_type == "llmNode")
            .filter(WorkflowNodeRun.status == NodeRunStatus.SUCCESS)
            .filter(WorkflowNodeRun.node_id.in_(target_node_ids))
            .group_by(WorkflowRun.deployment_id, WorkflowNodeRun.node_id)
            .all()
        )
        return {
            (deployment_id, str(node_id)): int(run_count or 0)
            for deployment_id, node_id, run_count in rows
        }

    @staticmethod
    def _summary_status(
        plan: DeploymentParameterOptimizationPlan,
        collected_runs: int,
        spend: float,
        budget: float,
    ) -> str:
        if not plan.enabled:
            return "disabled"
        if plan.status in {"paused", "failed"}:
            return plan.status
        if budget > 0 and spend >= budget:
            return "budget_exhausted"
        if collected_runs >= int(plan.check_every_runs or 50):
            return "ready"
        return "collecting"

    @staticmethod
    def _disabled_summary() -> dict[str, Any]:
        return {
            "enabled": False,
            "status": "disabled",
            "node_ids": [],
            "node_count": 0,
            "collected_runs": 0,
            "check_every_runs": 50,
            "validation_spend_usd": 0.0,
            "monthly_validation_budget_usd": 3.0,
        }

    @staticmethod
    def _plan_for_deployment(db: Session, deployment_id: Any):
        if not deployment_id or not hasattr(db, "query"):
            return None
        return (
            db.query(DeploymentParameterOptimizationPlan)
            .filter(DeploymentParameterOptimizationPlan.deployment_id == deployment_id)
            .first()
        )

    @staticmethod
    def _is_validation_target(
        plan: DeploymentParameterOptimizationPlan | None,
        node_id: str,
    ) -> bool:
        if plan is None or not plan.enabled:
            return False
        target_node_ids = {
            str(target_node_id)
            for target_node_id in (plan.node_ids or [])
            if target_node_id
        }
        return node_id in target_node_ids

    @staticmethod
    def _current_month_key() -> str:
        return datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m")

    @classmethod
    def _reset_monthly_spend_if_needed(
        cls,
        plan: DeploymentParameterOptimizationPlan,
    ) -> None:
        current_month = cls._current_month_key()
        if plan.validation_spend_month != current_month:
            plan.validation_spend_month = current_month
            plan.validation_spend_usd = 0.0
