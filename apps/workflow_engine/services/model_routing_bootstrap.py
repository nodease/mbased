"""Judge-first 초기 정책 artifact의 DB 저장과 작업 지문 계산."""

from __future__ import annotations

import hashlib
import json
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from apps.shared.db.models.model_routing_policy import (
    LLMNodeModelRoutingBootstrap,
    LLMNodeModelRoutingBootstrapSample,
)
from apps.shared.db.models.workflow_deployment import WorkflowDeployment
from apps.shared.db.models.workflow_run import (
    NodeRunStatus,
    RunStatus,
    RunTriggerMode,
    WorkflowNodeRun,
    WorkflowRun,
)
from apps.workflow_engine.services.model_router import ModelCandidate
from apps.workflow_engine.services.model_routing_judge_first_policy import (
    JUDGE_FIRST_STRATEGY_ID,
    build_judge_first_active_policy,
)


OPERATIONAL_TRIGGER_MODES = {
    RunTriggerMode.API,
    RunTriggerMode.WEBHOOK,
    RunTriggerMode.SCHEDULER,
    RunTriggerMode.APP,
}


class PersistedModelRoutingBootstrapStore:
    """초안에서 Judge-first 정책을 즉시 준비하는 저장 경계."""

    @classmethod
    def preview(
        cls,
        db: Session,
        *,
        workflow_id: uuid.UUID,
        organization_id: uuid.UUID | None,
        node_id: str,
        node_data: dict[str, Any],
        downstream_contract: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        del organization_id
        fingerprint = task_fingerprint(
            node_data,
            downstream_contract=downstream_contract,
        )
        history_count, excluded_count = cls._history_counts(
            db,
            workflow_id=workflow_id,
            node_id=node_id,
            task_fingerprint_value=fingerprint,
        )
        existing = cls._find_exact_bootstrap(
            db,
            workflow_id=workflow_id,
            node_id=node_id,
            task_fingerprint_value=fingerprint,
        )
        return {
            "task_fingerprint": fingerprint,
            "history_mode": "history" if history_count else "judge_first",
            "available_history_count": history_count,
            "excluded_history_count": excluded_count,
            "excluded_reason_summary": (
                {"task_fingerprint_mismatch": excluded_count}
                if excluded_count
                else {}
            ),
            "bootstrap": cls.public_summary(existing, include_samples=True, db=db),
        }

    @classmethod
    def create_ready(
        cls,
        db: Session,
        *,
        workflow_id: uuid.UUID,
        organization_id: uuid.UUID | None,
        node_id: str,
        node_data: dict[str, Any],
        task_description: str,
        default_model_id: str,
        fallback_model_id: str | None,
        created_by: uuid.UUID,
        available_candidates: list[ModelCandidate],
        downstream_contract: dict[str, Any] | None = None,
    ) -> LLMNodeModelRoutingBootstrap:
        """Planner 호출 없이 Judge-first 실행에 필요한 최소 artifact를 만든다."""

        description = str(task_description or "").strip()
        if not description:
            raise ValueError("자동 라우팅 작업 설명을 입력하세요.")
        default_model = str(default_model_id or "").strip()
        fallback_model = str(fallback_model_id or "").strip() or None
        candidate_ids = [candidate.model_id for candidate in available_candidates]
        if default_model not in candidate_ids:
            raise ValueError("기본 모델은 현재 실행 주체가 사용할 수 있어야 합니다.")
        if fallback_model and fallback_model not in candidate_ids:
            raise ValueError("대체 모델은 현재 실행 주체가 사용할 수 있어야 합니다.")
        if fallback_model == default_model:
            raise ValueError("기본 모델과 대체 모델은 서로 달라야 합니다.")

        fingerprint = task_fingerprint(
            node_data,
            downstream_contract=downstream_contract,
            task_description=description,
        )
        existing = cls._find_exact_bootstrap(
            db,
            workflow_id=workflow_id,
            node_id=node_id,
            task_fingerprint_value=fingerprint,
        )
        for stale in (
            db.query(LLMNodeModelRoutingBootstrap)
            .filter(LLMNodeModelRoutingBootstrap.workflow_id == workflow_id)
            .filter(LLMNodeModelRoutingBootstrap.node_id == node_id)
            .filter(LLMNodeModelRoutingBootstrap.task_fingerprint != fingerprint)
            .filter(LLMNodeModelRoutingBootstrap.status == "ready")
            .all()
        ):
            stale.status = "stale"
            stale.stale_reason = "task_fingerprint_changed"

        bootstrap = existing or LLMNodeModelRoutingBootstrap(
            organization_id=organization_id,
            workflow_id=workflow_id,
            node_id=node_id,
            task_fingerprint=fingerprint,
        )
        if existing is not None:
            for sample in (
                db.query(LLMNodeModelRoutingBootstrapSample)
                .filter(LLMNodeModelRoutingBootstrapSample.bootstrap_id == existing.id)
                .all()
            ):
                db.delete(sample)

        history_count, excluded_count = cls._history_counts(
            db,
            workflow_id=workflow_id,
            node_id=node_id,
            task_fingerprint_value=fingerprint,
        )
        bootstrap.organization_id = organization_id
        bootstrap.task_description = description
        bootstrap.status = "ready"
        bootstrap.source = "history" if history_count else "judge_first"
        bootstrap.default_model_id = default_model
        bootstrap.fallback_model_id = fallback_model
        bootstrap.initial_budget_usd = Decimal("0")
        bootstrap.planner_model_id = None
        bootstrap.planner_cost_usd = None
        bootstrap.classifier_artifact = {}
        bootstrap.generation_summary = {
            "strategy_id": JUDGE_FIRST_STRATEGY_ID,
            "candidate_model_ids": candidate_ids,
            "history_sample_count": history_count,
            "excluded_history_count": excluded_count,
            "generation_status": "ready",
            "planner_called": False,
        }
        bootstrap.stale_reason = None
        bootstrap.created_by = created_by
        if existing is None:
            db.add(bootstrap)
        db.flush()
        return bootstrap

    @classmethod
    def active_policy_for_bootstrap(
        cls,
        bootstrap: LLMNodeModelRoutingBootstrap,
    ) -> dict[str, Any]:
        summary = (
            bootstrap.generation_summary
            if isinstance(bootstrap.generation_summary, dict)
            else {}
        )
        policy = build_judge_first_active_policy(
            policy_version=f"bootstrap-{str(bootstrap.id)[:8]}",
            default_model_id=bootstrap.default_model_id,
            fallback_model_id=bootstrap.fallback_model_id,
            candidate_model_ids=summary.get("candidate_model_ids") or [
                bootstrap.default_model_id,
                bootstrap.fallback_model_id or "",
            ],
        )
        policy.update(
            {
                "bootstrap_id": str(bootstrap.id),
                "task_fingerprint": bootstrap.task_fingerprint,
            }
        )
        return policy

    @classmethod
    def public_summary(
        cls,
        bootstrap: LLMNodeModelRoutingBootstrap | None,
        *,
        include_samples: bool = False,
        db: Session | None = None,
    ) -> dict[str, Any] | None:
        del include_samples, db
        if bootstrap is None:
            return None
        summary = (
            bootstrap.generation_summary
            if isinstance(bootstrap.generation_summary, dict)
            else {}
        )
        return {
            "id": str(bootstrap.id),
            "status": bootstrap.status,
            "source": bootstrap.source,
            "task_fingerprint": bootstrap.task_fingerprint,
            "task_description": bootstrap.task_description,
            "default_model_id": bootstrap.default_model_id,
            "fallback_model_id": bootstrap.fallback_model_id,
            "generation_summary": summary,
            "stale_reason": bootstrap.stale_reason,
            "created_at": bootstrap.created_at.isoformat()
            if bootstrap.created_at
            else None,
        }

    @classmethod
    def _find_exact_bootstrap(
        cls,
        db: Session,
        *,
        workflow_id: uuid.UUID,
        node_id: str,
        task_fingerprint_value: str,
    ) -> LLMNodeModelRoutingBootstrap | None:
        return (
            db.query(LLMNodeModelRoutingBootstrap)
            .filter(LLMNodeModelRoutingBootstrap.workflow_id == workflow_id)
            .filter(LLMNodeModelRoutingBootstrap.node_id == node_id)
            .filter(
                LLMNodeModelRoutingBootstrap.task_fingerprint
                == task_fingerprint_value
            )
            .order_by(LLMNodeModelRoutingBootstrap.created_at.desc())
            .first()
        )

    @staticmethod
    def _history_counts(
        db: Session,
        *,
        workflow_id: uuid.UUID,
        node_id: str,
        task_fingerprint_value: str,
    ) -> tuple[int, int]:
        rows = (
            db.query(WorkflowDeployment)
            .join(WorkflowRun, WorkflowRun.deployment_id == WorkflowDeployment.id)
            .join(WorkflowNodeRun, WorkflowNodeRun.workflow_run_id == WorkflowRun.id)
            .filter(WorkflowRun.workflow_id == workflow_id)
            .filter(WorkflowRun.status == RunStatus.SUCCESS)
            .filter(WorkflowRun.trigger_mode.in_(OPERATIONAL_TRIGGER_MODES))
            .filter(WorkflowNodeRun.node_id == node_id)
            .filter(WorkflowNodeRun.node_type == "llmNode")
            .filter(WorkflowNodeRun.status == NodeRunStatus.SUCCESS)
            .order_by(WorkflowRun.finished_at.desc(), WorkflowRun.id.desc())
            .limit(200)
            .all()
        )
        matched = 0
        excluded = 0
        for deployment in rows:
            node = _graph_node(deployment.graph_snapshot, node_id)
            node_data = node.get("data") if isinstance(node, dict) else None
            if not isinstance(node_data, dict):
                excluded += 1
                continue
            contract = downstream_contract_from_graph(deployment.graph_snapshot, node_id)
            if (
                task_fingerprint(node_data, downstream_contract=contract)
                == task_fingerprint_value
            ):
                matched += 1
            else:
                excluded += 1
        return matched, excluded


def task_fingerprint(
    node_data: Any,
    *,
    downstream_contract: dict[str, Any] | None = None,
    task_description: str | None = None,
) -> str:
    payload = safe_task_summary(
        node_data,
        task_description=(
            task_description
            if task_description is not None
            else str(_node_value(node_data, "model_routing_task_description", "") or "")
        ),
        downstream_contract=downstream_contract,
    )
    payload.pop("task_description", None)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def safe_task_summary(
    node_data: Any,
    *,
    task_description: str,
    downstream_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "task_description": str(task_description or "").strip(),
        "prompts": {
            "system": str(_node_value(node_data, "system_prompt", "") or ""),
            "user": str(_node_value(node_data, "user_prompt", "") or ""),
            "assistant": str(_node_value(node_data, "assistant_prompt", "") or ""),
        },
        "input_variables": _json_safe(
            _node_value(node_data, "referenced_variables", []) or []
        ),
        "output_format": _json_safe(_node_value(node_data, "output_format", None)),
        "knowledge_enabled": bool(
            _node_value(node_data, "knowledgeBases", None)
            or _node_value(node_data, "knowledgeCollections", None)
        ),
        "knowledge": {
            "knowledge_bases": _json_safe(
                _node_value(node_data, "knowledgeBases", []) or []
            ),
            "knowledge_collections": _json_safe(
                _node_value(node_data, "knowledgeCollections", []) or []
            ),
            "top_k": _node_value(node_data, "topK", None),
            "score_threshold": _node_value(node_data, "scoreThreshold", None),
            "retrieved_context_max_chars": _node_value(
                node_data,
                "retrievedContextMaxChars",
                None,
            ),
        },
        "downstream_contract": _json_safe(downstream_contract or {}),
    }


def downstream_contract_from_graph(graph: Any, node_id: str) -> dict[str, Any]:
    if not isinstance(graph, dict):
        return {"consumers": []}
    edges = graph.get("edges") if isinstance(graph.get("edges"), list) else []
    consumers: list[dict[str, Any]] = []
    for edge in edges:
        if not isinstance(edge, dict) or str(edge.get("source")) != str(node_id):
            continue
        target = _graph_node(graph, str(edge.get("target") or ""))
        data = target.get("data") if isinstance(target, dict) else {}
        data = data if isinstance(data, dict) else {}
        consumers.append(
            {
                "node_type": str(target.get("type") or "") if target else "",
                "node_id": str(target.get("id") or "") if target else "",
                "referenced_variables": _json_safe(
                    data.get("referenced_variables") or []
                ),
                "variable_mappings": _json_safe(
                    data.get("variableMappings") or data.get("mappings") or []
                ),
                "output_format": _json_safe(data.get("output_format")),
            }
        )
    return {"consumers": consumers}


def _graph_node(graph: Any, node_id: str) -> dict[str, Any] | None:
    nodes = graph.get("nodes") if isinstance(graph, dict) else None
    for node in nodes if isinstance(nodes, list) else []:
        if isinstance(node, dict) and str(node.get("id")) == str(node_id):
            return node
    return None


def _json_safe(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {str(key): _json_safe(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(child) for child in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _node_value(node_data: Any, key: str, default: Any = None) -> Any:
    if isinstance(node_data, dict):
        return node_data.get(key, default)
    return getattr(node_data, key, default)
