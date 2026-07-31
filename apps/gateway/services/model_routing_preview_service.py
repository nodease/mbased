"""배포된 자동 모델 라우팅 정책을 기록 없이 평가하는 read-only service."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from apps.shared.db.models.model_routing_policy import LLMNodeModelRoutingPolicy
from apps.shared.db.models.workflow import Workflow
from apps.shared.db.models.workflow_deployment import WorkflowDeployment
from apps.shared.services.node_config_fingerprint import llm_node_config_fingerprint
from apps.workflow_engine.services.llm_service import LLMService as WorkflowRuntimeLLMService
from apps.workflow_engine.services.model_router import (
    ModelRouter,
    ModelRoutingPromptRenderError,
    ModelRoutingUnavailableError,
)
from apps.workflow_engine.workflow.nodes.llm.entities import LLMNodeData


class ModelRoutingPreviewBlockedError(ValueError):
    """미리보기는 요청을 처리했지만, 현재 정책으로 모델을 고를 수 없는 상태."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class ModelRoutingPreviewService:
    """실제 deployment runtime과 같은 policy evaluator를 재사용한다.

    이 service는 policy/run/usage를 변경하거나 Celery task를 발행하지 않는다.
    embedding이나 LLM completion을 실행하지 않고 raw input도 저장하지 않는다.
    """

    @classmethod
    def preview(
        cls,
        db: Session,
        *,
        workflow: Workflow,
        deployment: WorkflowDeployment,
        node_id: str,
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        deployed_node = cls._find_llm_node(deployment.graph_snapshot, node_id)
        deployed_node_data = cls._node_data(deployed_node)
        if not bool(deployed_node_data.get("auto_model_routing")):
            raise ModelRoutingPreviewBlockedError("model_routing.disabled")

        policy = cls._load_policy(
            db,
            workflow_id=workflow.id,
            deployment_id=deployment.id,
            node_id=node_id,
        )
        if (
            policy is None
            or not policy.enabled
            or not isinstance(policy.active_policy, dict)
            or not policy.active_policy
        ):
            raise ModelRoutingPreviewBlockedError("model_routing.policy_not_ready")

        subject_id = policy.execution_subject_user_id
        organization_id = policy.organization_id or getattr(workflow, "organization_id", None)
        if subject_id is None or organization_id is None:
            raise ModelRoutingPreviewBlockedError(
                "model_routing.execution_subject_unavailable"
            )

        # 배포 runtime과 같은 execution subject 권한으로 제한한다. 현재 로그인
        # 사용자의 권한을 쓰면 webhook/app 실행의 실제 결과와 달라질 수 있다.
        available_model_ids = (
            WorkflowRuntimeLLMService.get_runtime_available_model_ids_for_user(
                db,
                user_id=subject_id,
                organization_id=organization_id,
            )
        )
        node_data = LLMNodeData.model_validate(deployed_node_data)
        policy_payload = {
            "policy_id": str(policy.id),
            "policy_version": policy.policy_version,
            "active_policy": policy.active_policy,
        }
        try:
            routing_feature_text = ModelRouter.routing_feature_text(inputs, node_data)
            decision = ModelRouter.resolve_policy(
                policy_payload,
                inputs=inputs,
                node_data=node_data,
                available_model_ids=available_model_ids,
                routing_feature_text=routing_feature_text,
            )
        except ModelRoutingPromptRenderError as exc:
            raise ModelRoutingPreviewBlockedError(exc.args[0]) from exc
        except ModelRoutingUnavailableError as exc:
            raise ModelRoutingPreviewBlockedError(
                "model_routing.no_available_model"
            ) from exc

        active_policy = policy.active_policy
        configured_default_model = cls._model_id(active_policy.get("default_model_id"))
        configured_fallback_model = cls._model_id(active_policy.get("fallback_model_id"))
        decision_source = "matched_rule" if decision.matched_rule_id else "default_model"
        availability = "available"
        if (
            decision.matched_rule_id is None
            and configured_default_model
            and ModelRouter.normalize_model_id(decision.selected_model_id)
            != ModelRouter.normalize_model_id(configured_default_model)
        ):
            decision_source = "fallback_model"
            availability = "fallback"

        return {
            "deployment_version": deployment.version,
            "policy_version": policy.policy_version,
            "decision_source": decision_source,
            "selected_model_id": decision.selected_model_id,
            "fallback_model_id": decision.fallback_model_id,
            "default_model_id": configured_default_model,
            "configured_fallback_model_id": configured_fallback_model,
            "matched_rule_id": decision.matched_rule_id,
            "reason_code": decision.reason_code,
            "strategy_id": decision.strategy_id,
            "decision_factors": decision.decision_factors,
            "runtime_context": decision.runtime_context.as_metadata(),
            "availability": availability,
            "draft_matches_deployment": cls._draft_matches_deployment(
                workflow, node_id, deployed_node_data
            ),
        }

    @staticmethod
    def _load_policy(
        db: Session,
        *,
        workflow_id: Any,
        deployment_id: Any,
        node_id: str,
    ) -> LLMNodeModelRoutingPolicy | None:
        return (
            db.query(LLMNodeModelRoutingPolicy)
            .filter(LLMNodeModelRoutingPolicy.workflow_id == workflow_id)
            .filter(LLMNodeModelRoutingPolicy.deployment_id == deployment_id)
            .filter(LLMNodeModelRoutingPolicy.node_id == node_id)
            .first()
        )

    @staticmethod
    def _find_llm_node(graph: Any, node_id: str) -> dict[str, Any]:
        nodes = graph.get("nodes") if isinstance(graph, dict) else None
        for node in nodes if isinstance(nodes, list) else []:
            if isinstance(node, dict) and node.get("id") == node_id:
                if node.get("type") != "llmNode":
                    break
                return node
        raise ModelRoutingPreviewBlockedError("model_routing.deployed_node_not_found")

    @staticmethod
    def _node_data(node: dict[str, Any]) -> dict[str, Any]:
        data = node.get("data")
        if not isinstance(data, dict):
            raise ModelRoutingPreviewBlockedError("model_routing.deployed_node_invalid")
        return data

    @classmethod
    def _draft_matches_deployment(
        cls,
        workflow: Workflow,
        node_id: str,
        deployed_node_data: dict[str, Any],
    ) -> bool:
        draft_graph = getattr(workflow, "graph", None)
        try:
            draft_node = cls._find_llm_node(draft_graph, node_id)
            draft_node_data = cls._node_data(draft_node)
        except ModelRoutingPreviewBlockedError:
            return False
        return llm_node_config_fingerprint(
            draft_node_data
        ) == llm_node_config_fingerprint(deployed_node_data)

    @staticmethod
    def _model_id(value: Any) -> str | None:
        normalized = str(value or "").strip()
        return normalized or None
