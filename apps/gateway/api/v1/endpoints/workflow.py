import copy
import hashlib
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, List, Literal, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from celery.exceptions import TimeoutError as CeleryTimeoutError
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import Date, Integer, cast, func, or_
from sqlalchemy.orm import Session, noload, selectinload

# from sqlalchemy.orm import Session, noload, selectinload
from starlette.requests import Request

from apps.gateway.auth.dependencies import get_current_user
from apps.gateway.auth.permissions import ensure_workflow_permission
from apps.gateway.application.agent_builder.graph_mutation_builder import (
    canonical_graph_hash,
)
from apps.gateway.application.agent_builder.workflow_cas import (
    WorkflowDraftCASService,
    WorkflowMutationConflict,
)
from apps.gateway.services.organization_context import resolve_active_organization_id
from apps.gateway.utils.audit import audit
from apps.gateway.services.app_service import AppService
from apps.gateway.services.cost_optimizer_parameter_recommendation_service import (
    CostOptimizerParameterRecommendationService,
    _node_config_fingerprint,
    _resolve_operation_cohort,
)
from apps.gateway.services.cost_optimizer_output_quality_service import (
    CostOptimizerOutputQualityService,
)
from apps.gateway.services.cost_optimizer_recommendation_verification_service import (
    CostOptimizerRecommendationVerificationService,
)
from apps.gateway.services.model_routing_preview_service import (
    ModelRoutingPreviewBlockedError,
    ModelRoutingPreviewService,
)
from apps.gateway.services.llm_service import LLMService
from apps.gateway.services.workflow_budget_service import WorkflowBudgetService
from apps.gateway.services.deployment_service import DeploymentService
from apps.gateway.services.deployment_parameter_optimization_service import (
    DeploymentParameterOptimizationBudgetExceeded,
    DeploymentParameterOptimizationService,
)
from apps.gateway.services.workflow_service import WorkflowService
from apps.shared.audit.actions import AuditAction
from apps.shared.celery_app import celery_app
from apps.shared.db.models.app import App
from apps.shared.db.models.cost_optimizer import (
    CostOptimizerCandidate,
    CostOptimizerExperiment,
)
from apps.shared.db.models.llm import LLMUsageLog
from apps.shared.db.models.model_routing_policy import LLMNodeModelRoutingPolicy
from apps.shared.db.models.workflow_deployment import WorkflowDeployment
from apps.shared.db.models.user import User
from apps.shared.db.models.workflow import Workflow
from apps.shared.domain.external_effect_error import (
    safe_external_effect_error_payload,
)
from apps.shared.domain.workflow_knowledge_references import (
    WorkflowKnowledgeReferenceError,
    parse_workflow_knowledge_references,
)
from apps.shared.domain.workflow_graph import (
    WorkflowGraphValidationError,
    validate_workflow_graph,
)
from apps.shared.services.model_routing_global_profile_catalog import (
    is_workflow_execution_model_excluded,
)
from apps.shared.permissions import workflow_auth_state_allows
from apps.workflow_engine.services.llm_service import (
    LLMService as WorkflowRuntimeLLMService,
)
from apps.workflow_engine.services.model_router import ModelCandidate, ModelRouter
from apps.workflow_engine.services.model_routing_judge_first_policy import (
    build_judge_first_active_policy,
)
from apps.workflow_engine.services.model_routing_bootstrap import (
    PersistedModelRoutingBootstrapStore,
    downstream_contract_from_graph,
)

# [NEW] 로깅 모델 및 스키마
from apps.shared.db.models.workflow_run import (
    NodeRunStatus,
    RunStatus,
    RunTriggerMode,
    WorkflowNodeRun,
    WorkflowRun,
)
from apps.shared.db.session import get_db
from apps.shared.schemas.log import (
    DashboardStatsResponse,
    WorkflowRunListResponse,
    WorkflowRunSchema,
)
from apps.shared.schemas.llm import LLMTraceListResponse
from apps.shared.schemas.permission import WorkflowPermissionResponse
from apps.shared.schemas.workflow import (
    WorkflowCreateRequest,
    WorkflowDraftRequest,
    WorkflowNodeSecretWriteRequest,
    WorkflowNodeSecretWriteResponse,
    WorkflowResponse,
)
from apps.shared.services.permissions import (
    get_effective_workflow_auth_state,
    get_workflow_permission_sources,
    has_knowledge_base_permission,
)
from apps.shared.services.cost_optimizer_retention import CostOptimizerRetentionService
from apps.shared.services.tracing.metadata import TraceMetadataSanitizer
from apps.shared.services.workflow_task_publisher import send_workflow_task

logger = logging.getLogger(__name__)
router = APIRouter()

COST_OPTIMIZER_ALLOWED_TASK_TYPES = {
    "classify",
    "extract",
    "summarize",
    "generate",
    "reason",
}
COST_OPTIMIZER_BASELINE_INPUT_NODE_ID = "__cost_optimizer_baseline_input__"


def _safe_task_error(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    code = value.get("code")
    retryable = value.get("retryable")
    if not isinstance(code, str) or not isinstance(retryable, bool):
        return None
    is_external_effect_error = code.startswith("external_effect.")
    if is_external_effect_error:
        return safe_external_effect_error_payload(value)
    payload: dict[str, Any] = {
        "code": code,
        "message": str(value.get("message") or code),
        "retryable": retryable,
    }
    if isinstance(value.get("node_id"), str):
        payload["node_id"] = value["node_id"]
    return payload


def _safe_stream_event(value: Any) -> dict[str, Any]:
    generic_error = {
        "type": "error",
        "data": {"message": "workflow.execution_failed"},
    }
    if not isinstance(value, dict) or not isinstance(value.get("type"), str):
        return generic_error
    if value["type"] != "error":
        return value
    data = value.get("data")
    if not isinstance(data, dict):
        return generic_error
    code = data.get("code")
    if isinstance(code, str) and code.startswith("external_effect."):
        safe_error = _safe_task_error(data)
        if safe_error is None:
            return generic_error
        return {"type": "error", "data": safe_error}
    return value


def _workflow_stream_started_event(run_id: str) -> dict[str, Any]:
    """클라이언트가 테스트 실행 기록을 다시 조회할 수 있게 식별자만 전달한다."""
    return {"type": "workflow_start", "data": {"run_id": run_id}}


def _serialize_workflow_sse_event(event: dict[str, Any]) -> str:
    """하나의 workflow 이벤트를 실제 빈 줄로 끝나는 SSE record로 직렬화한다."""
    return f"data: {json.dumps(event)}\n\n"


def _stream_workflow_events(
    *,
    external_run_id: str,
    celery: Any,
    graph: dict[str, Any],
    user_input: dict[str, Any],
    execution_context: dict[str, Any],
):
    """Redis 구독과 task 발행을 완료한 뒤 workflow SSE 이벤트를 전달한다."""
    from apps.shared.pubsub import get_redis_client

    client = get_redis_client()
    pubsub = client.pubsub()
    channel = f"workflow:{external_run_id}"

    try:
        # 구독을 먼저 완료해야 Worker가 즉시 발행한 첫 이벤트를 놓치지 않는다.
        pubsub.subscribe(channel)
        # 복원용 run_id를 브라우저에 알리기 전에 task를 큐에 올린다.
        send_workflow_task(
            celery,
            "workflow.stream",
            args=[graph, user_input, execution_context, external_run_id],
        )
        logger.info("[Gateway] Celery 태스크 시작됨")

        yield _serialize_workflow_sse_event(
            _workflow_stream_started_event(external_run_id)
        )

        for message in pubsub.listen():
            if message["type"] == "message":
                event = _safe_stream_event(json.loads(message["data"]))
                yield _serialize_workflow_sse_event(event)

                if event.get("type") in ("workflow_finish", "error"):
                    logger.info(f"[Gateway] 스트리밍 종료 - type: {event.get('type')}")
                    break
    except Exception:
        error_event = {
            "type": "error",
            "data": {"message": "workflow.stream_unavailable"},
        }
        yield _serialize_workflow_sse_event(error_event)
    finally:
        pubsub.unsubscribe(channel)
        pubsub.close()


class WorkflowCompareRequest(BaseModel):
    node_id: str
    compare_type: Literal["model", "prompt"]
    inputs: dict[str, Any] = Field(default_factory=dict)
    left: str
    right: str


class CostOptimizerPermissionResponse(BaseModel):
    can_compare: bool
    can_apply: bool
    required_auth_state: str


class CostOptimizerAvailabilityResponse(BaseModel):
    available: bool
    reason: str | None = None
    workflow_id: str
    node_id: str
    node_type: str
    permission: CostOptimizerPermissionResponse


class CostOptimizerLatestBaselineResponse(BaseModel):
    baseline: dict[str, Any]


class CostOptimizerBaselineListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[dict[str, Any]]


class CostOptimizerCandidateRequest(BaseModel):
    label: str = "B"
    model_id: str
    fallback_model_id: str | None = None
    auto_model_routing: bool | None = None
    model_routing_policy: dict[str, Any] | None = None
    task_type: str | None = None
    system_prompt: str | None = None
    user_prompt: str | None = None
    assistant_prompt: str | None = None
    referenced_variables: list[dict[str, Any]] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    output_format: dict[str, Any] | None = None
    knowledge: dict[str, Any] | None = None


class CostOptimizerCompareRequest(BaseModel):
    baseline_id: str
    candidate: CostOptimizerCandidateRequest


class CostOptimizerApplyRequest(BaseModel):
    comparison_id: str | None = None
    candidate_settings: CostOptimizerCandidateRequest
    acknowledge_downstream_warning: bool = False
    expected_graph_hash: str = Field(min_length=64, max_length=64)
    expected_updated_at: datetime


class CostOptimizerRecommendationApplyRequest(BaseModel):
    recommendation_ids: list[str] = Field(default_factory=list)
    expected_graph_hash: str = Field(min_length=64, max_length=64)
    expected_updated_at: datetime


class CostOptimizerRecommendationVerifyRequest(BaseModel):
    recommendation_ids: list[str] = Field(default_factory=list)
    baseline_mode: Literal["latest_success"]
    # 프론트가 추천 목록을 조회한 시점의 값을 함께 보내면 이전 modal state를
    # 재사용한 요청을 stale로 막을 수 있다. 기존 클라이언트 호환을 위해 선택값이다.
    recommendation_policy_version: str | None = None
    recommendation_fingerprint: str | None = None
    node_config_fingerprint: str | None = None


class ModelRoutingPolicyPatchRequest(BaseModel):
    enabled: bool
    refresh_every_runs: int = Field(default=20, ge=5, le=100)
    default_model_id: str | None = Field(default=None, min_length=1, max_length=255)
    fallback_model_id: str | None = Field(default=None, max_length=255)
    expected_graph_hash: str = Field(min_length=64, max_length=64)
    expected_updated_at: datetime


class ModelRoutingBootstrapRequest(BaseModel):
    """초안 LLM 노드에서 Judge-first 초기 정책을 만드는 요청."""

    task_description: str = Field(min_length=10, max_length=4000)
    default_model_id: str = Field(min_length=1, max_length=255)
    fallback_model_id: str | None = Field(default=None, max_length=255)
    expected_graph_hash: str = Field(min_length=64, max_length=64)
    expected_updated_at: datetime


class ModelRoutingPolicyRefreshRequest(BaseModel):
    pass


class ModelRoutingPreviewRequest(BaseModel):
    """편집 화면에서 입력만 받아 배포 policy를 read-only로 평가한다."""

    inputs: dict[str, Any] = Field(default_factory=dict)


class ModelRoutingPreviewResponse(BaseModel):
    """미리보기 endpoint가 raw input 없이 내보내는 고정 safe summary."""

    deployment_version: int
    policy_version: str | None = None
    decision_source: Literal["matched_rule", "default_model", "fallback_model"]
    selected_model_id: str
    fallback_model_id: str | None = None
    default_model_id: str | None = None
    configured_fallback_model_id: str | None = None
    matched_rule_id: str | None = None
    reason_code: str
    strategy_id: str | None = None
    decision_factors: dict[str, Any] = Field(default_factory=dict)
    runtime_context: dict[str, Any] = Field(default_factory=dict)
    availability: Literal["available", "fallback"]
    draft_matches_deployment: bool


def _raise_invalid_cost_optimizer_candidate() -> None:
    raise HTTPException(status_code=400, detail="cost_optimizer.invalid_candidate")


def _lock_workflow_for_cas_graph_write(
    db: Session,
    current_user: User,
    workflow_id: str,
    request_body: Any,
    authorized_workflow: Workflow,
) -> Workflow:
    workflow = (
        db.query(Workflow)
        .filter(Workflow.id == workflow_id)
        .with_for_update()
        .first()
    )
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    if str(workflow.id) != str(authorized_workflow.id) or str(
        workflow.organization_id
    ) != str(authorized_workflow.organization_id):
        raise HTTPException(status_code=404, detail="Workflow not found")

    rechecked_workflow = ensure_workflow_permission(
        db, current_user, workflow_id, "write"
    )
    if str(rechecked_workflow.id) != str(workflow.id) or str(
        rechecked_workflow.organization_id
    ) != str(workflow.organization_id):
        raise HTTPException(status_code=404, detail="Workflow not found")

    try:
        WorkflowDraftCASService.validate_expected_draft_state(
            workflow=workflow,
            request=request_body,
        )
    except WorkflowMutationConflict as exc:
        raise HTTPException(status_code=409, detail=exc.code) from exc
    return workflow


def _commit_graph_write_with_canonical_metadata(
    db: Session,
    workflow: Workflow,
) -> dict[str, str]:
    try:
        db.flush()
        db.refresh(workflow)
        updated_at = workflow.updated_at
        if updated_at is None:
            raise RuntimeError("workflow_updated_at_unavailable")
        metadata = {
            "graph_hash": canonical_graph_hash(workflow.graph),
            "updated_at": updated_at.isoformat(),
        }
        db.commit()
        return metadata
    except Exception:
        db.rollback()
        raise


def _request_id_from_request(request: Request) -> str | None:
    return getattr(request.state, "request_id", None) or request.headers.get(
        "x-request-id"
    )


def _ensure_workflow_matches_active_organization(
    db: Session,
    request: Request,
    current_user: User,
    workflow: Workflow,
    raw_organization_id: str | None,
) -> None:
    if not isinstance(raw_organization_id, str):
        return

    organization_id = resolve_active_organization_id(
        db,
        request,
        raw_organization_id,
        current_user.id,
    )
    workflow_organization_id = getattr(workflow, "organization_id", None)
    if workflow_organization_id is not None and str(workflow_organization_id) != str(
        organization_id
    ):
        raise HTTPException(status_code=404, detail="Workflow not found")


def _bind_and_preflight_authenticated_graph(
    db: Session,
    *,
    workflow: Workflow,
    graph: dict[str, Any],
    principal_id: UUID,
) -> dict[str, Any]:
    try:
        validate_workflow_graph(graph)
    except WorkflowGraphValidationError:
        DeploymentService.enforce_authenticated_configuration_preflight(
            db,
            graph_snapshot=graph,
            organization_id=workflow.organization_id,
            principal_id=principal_id,
        )
    try:
        bound_graph = DeploymentService.bind_workflow_node_targets(
            db,
            graph,
            app=SimpleNamespace(
                id=getattr(workflow, "app_id", None) or workflow.id,
                organization_id=workflow.organization_id,
            ),
        )
    except HTTPException:
        DeploymentService.enforce_authenticated_configuration_preflight(
            db,
            graph_snapshot=graph,
            organization_id=workflow.organization_id,
            principal_id=principal_id,
        )
        raise
    DeploymentService.enforce_authenticated_configuration_preflight(
        db,
        graph_snapshot=bound_graph,
        organization_id=workflow.organization_id,
        principal_id=principal_id,
    )
    return bound_graph


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_number_range(
    value: Any,
    *,
    minimum: float,
    maximum: float,
) -> None:
    if not _is_number(value) or value < minimum or value > maximum:
        _raise_invalid_cost_optimizer_candidate()


def _validate_cost_optimizer_candidate_shape(
    candidate: CostOptimizerCandidateRequest,
) -> None:
    model_id = (candidate.model_id or "").strip()
    if not model_id and not _candidate_has_active_model_routing_policy(candidate):
        _raise_invalid_cost_optimizer_candidate()

    fallback_model_id = (
        candidate.fallback_model_id.strip()
        if isinstance(candidate.fallback_model_id, str)
        else None
    )
    if model_id and fallback_model_id and fallback_model_id == model_id:
        _raise_invalid_cost_optimizer_candidate()

    if candidate.task_type is not None:
        if (
            not isinstance(candidate.task_type, str)
            or candidate.task_type not in COST_OPTIMIZER_ALLOWED_TASK_TYPES
        ):
            _raise_invalid_cost_optimizer_candidate()

    prompts = (
        candidate.system_prompt,
        candidate.user_prompt,
        candidate.assistant_prompt,
    )
    if all(prompt is not None for prompt in prompts) and not any(
        isinstance(prompt, str) and prompt.strip() for prompt in prompts
    ):
        _raise_invalid_cost_optimizer_candidate()

    for variable in candidate.referenced_variables or []:
        if not isinstance(variable, dict):
            _raise_invalid_cost_optimizer_candidate()
        name = variable.get("name")
        selector = variable.get("value_selector")
        if (
            not isinstance(name, str)
            or not name.strip()
            or not isinstance(selector, list)
            or len(selector) < 2
            or any(not isinstance(item, str) or not item.strip() for item in selector)
        ):
            _raise_invalid_cost_optimizer_candidate()

    parameters = candidate.parameters or {}
    max_tokens = parameters.get("max_tokens")
    if not isinstance(max_tokens, int) or isinstance(max_tokens, bool):
        _raise_invalid_cost_optimizer_candidate()
    _validate_number_range(max_tokens, minimum=1, maximum=8192)

    temperature = parameters.get("temperature")
    _validate_number_range(temperature, minimum=0, maximum=2)

    if "top_p" in parameters and parameters.get("top_p") is not None:
        _validate_number_range(parameters.get("top_p"), minimum=0, maximum=1)
    if (
        "presence_penalty" in parameters
        and parameters.get("presence_penalty") is not None
    ):
        _validate_number_range(
            parameters.get("presence_penalty"), minimum=-2, maximum=2
        )
    if (
        "frequency_penalty" in parameters
        and parameters.get("frequency_penalty") is not None
    ):
        _validate_number_range(
            parameters.get("frequency_penalty"), minimum=-2, maximum=2
        )
    if "stop" in parameters and parameters.get("stop") is not None:
        stop = parameters.get("stop")
        if (
            not isinstance(stop, list)
            or len(stop) > 4
            or any(not isinstance(item, str) for item in stop)
        ):
            _raise_invalid_cost_optimizer_candidate()

    output_format = candidate.output_format
    if output_format is not None:
        if not isinstance(output_format, dict):
            _raise_invalid_cost_optimizer_candidate()
        if output_format.get("type") not in {"text", "json"}:
            _raise_invalid_cost_optimizer_candidate()
        schema = output_format.get("schema")
        if schema is not None and not isinstance(schema, dict):
            _raise_invalid_cost_optimizer_candidate()
        if isinstance(schema, dict):
            _validate_cost_optimizer_json_schema_shape(schema)

    knowledge = candidate.knowledge
    if knowledge is None:
        return
    if not isinstance(knowledge, dict):
        _raise_invalid_cost_optimizer_candidate()

    if "knowledge_base_ids" in knowledge:
        knowledge_base_ids = knowledge.get("knowledge_base_ids")
        if not isinstance(knowledge_base_ids, list) or any(
            not isinstance(knowledge_base_id, str) or not knowledge_base_id.strip()
            for knowledge_base_id in knowledge_base_ids
        ):
            _raise_invalid_cost_optimizer_candidate()
    if "top_k" in knowledge:
        top_k = knowledge.get("top_k")
        if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k < 1:
            _raise_invalid_cost_optimizer_candidate()
    if "score_threshold" in knowledge:
        _validate_number_range(knowledge.get("score_threshold"), minimum=0, maximum=1)
    if "dedupe_retrieved_context" in knowledge and not isinstance(
        knowledge.get("dedupe_retrieved_context"), bool
    ):
        _raise_invalid_cost_optimizer_candidate()
    if "retrieved_context_max_chars" in knowledge:
        retrieved_context_max_chars = knowledge.get("retrieved_context_max_chars")
        if retrieved_context_max_chars is not None:
            if (
                not isinstance(retrieved_context_max_chars, int)
                or isinstance(retrieved_context_max_chars, bool)
                or retrieved_context_max_chars < 1
            ):
                _raise_invalid_cost_optimizer_candidate()
    if knowledge.get("retrieved_context_compression") not in {
        None,
        "off",
        "light",
        "strong",
    }:
        _raise_invalid_cost_optimizer_candidate()
    if knowledge.get("answer_grounding_check") not in {None, "off", "basic", "strict"}:
        _raise_invalid_cost_optimizer_candidate()


def _validate_cost_optimizer_json_schema_shape(schema: dict[str, Any]) -> None:
    allowed_types = {"object", "array", "string", "number", "boolean"}
    schema_type = schema.get("type")
    if schema_type is not None and schema_type not in allowed_types:
        _raise_invalid_cost_optimizer_candidate()

    properties = schema.get("properties")
    if properties is not None and not isinstance(properties, dict):
        _raise_invalid_cost_optimizer_candidate()
    if isinstance(properties, dict):
        for property_schema in properties.values():
            if not isinstance(property_schema, dict):
                _raise_invalid_cost_optimizer_candidate()
            property_type = property_schema.get("type")
            if property_type is not None and property_type not in allowed_types:
                _raise_invalid_cost_optimizer_candidate()

    required = schema.get("required")
    if required is not None and (
        not isinstance(required, list)
        or any(not isinstance(item, str) or not item.strip() for item in required)
    ):
        _raise_invalid_cost_optimizer_candidate()
    if isinstance(required, list) and isinstance(properties, dict):
        property_keys = set(properties.keys())
        if any(item not in property_keys for item in required):
            _raise_invalid_cost_optimizer_candidate()


def _ensure_cost_optimizer_candidate_knowledge_available(
    db: Session,
    current_user: User,
    workflow: Workflow,
    candidate: CostOptimizerCandidateRequest,
) -> None:
    knowledge = candidate.knowledge if isinstance(candidate.knowledge, dict) else {}
    knowledge_base_ids = knowledge.get("knowledge_base_ids") or []
    for knowledge_base_id in knowledge_base_ids:
        if not has_knowledge_base_permission(
            db,
            current_user.id,
            str(knowledge_base_id),
            "use",
            workflow.organization_id,
        ):
            raise HTTPException(
                status_code=422,
                detail="cost_optimizer.knowledge_unavailable",
            )


def _cost_optimizer_model_id_from_option(model: Any) -> str | None:
    if isinstance(model, dict):
        value = model.get("model_id_for_api_call") or model.get("model_id")
    else:
        value = getattr(model, "model_id_for_api_call", None) or getattr(
            model,
            "model_id",
            None,
        )
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _cost_optimizer_available_model_ids(db: Session, current_user: User) -> list[str]:
    available_model_ids: list[str] = []
    seen_model_ids: set[str] = set()
    for model in LLMService.get_my_available_models(db, current_user.id):
        model_id = _cost_optimizer_model_id_from_option(model)
        if not model_id or model_id in seen_model_ids:
            continue
        available_model_ids.append(model_id)
        seen_model_ids.add(model_id)
    return available_model_ids


def _cost_optimizer_available_model_candidates(
    db: Session,
    current_user: User,
) -> list[ModelCandidate]:
    candidates_by_id: dict[str, ModelCandidate] = {}
    fallback_models: list[Any] = []
    for model in LLMService.get_my_available_models(db, current_user.id):
        model_id = _cost_optimizer_model_id_from_option(model)
        if not model_id or is_workflow_execution_model_excluded(model_id):
            continue
        if ModelRouter.is_workflow_chat_model(model):
            candidates_by_id[model_id] = ModelCandidate.from_model(model)
        else:
            fallback_models.append(model)

    if candidates_by_id:
        return list(candidates_by_id.values())

    # 테스트 fixture나 오래된 DB row에 type/name metadata가 비어 있어도,
    # 권한이 확인된 모델이면 cold-start 기본 정책 생성에는 사용할 수 있게 한다.
    for model in fallback_models:
        model_id = _cost_optimizer_model_id_from_option(model)
        if not model_id or is_workflow_execution_model_excluded(model_id):
            continue
        if not model_id:
            continue
        candidates_by_id[model_id] = ModelCandidate.from_model(model)
    return list(candidates_by_id.values())


def _candidate_model_routing_active_policy(
    candidate: CostOptimizerCandidateRequest,
) -> dict[str, Any] | None:
    if candidate.auto_model_routing is not True:
        return None
    policy = candidate.model_routing_policy
    if not isinstance(policy, dict):
        return None
    active_policy = policy.get("active_policy")
    return active_policy if isinstance(active_policy, dict) else None


def _candidate_has_active_model_routing_policy(
    candidate: CostOptimizerCandidateRequest,
) -> bool:
    active_policy = _candidate_model_routing_active_policy(candidate)
    if active_policy is None:
        return False
    default_model_id = active_policy.get("default_model_id")
    return isinstance(default_model_id, str) and bool(default_model_id.strip())


def _candidate_requested_model_ids(
    candidate: CostOptimizerCandidateRequest,
) -> set[str]:
    requested_model_ids: set[str] = set()
    active_policy = _candidate_model_routing_active_policy(candidate)
    if active_policy is not None:
        for key in ("default_model_id", "fallback_model_id"):
            value = active_policy.get(key)
            if isinstance(value, str) and value.strip():
                requested_model_ids.add(value.strip())

        rules = active_policy.get("rules")
        if isinstance(rules, list):
            for rule in rules:
                if not isinstance(rule, dict):
                    continue
                for key in ("selected_model_id", "fallback_model_id"):
                    value = rule.get(key)
                    if isinstance(value, str) and value.strip():
                        requested_model_ids.add(value.strip())

        return requested_model_ids

    model_id = (candidate.model_id or "").strip()
    if model_id:
        requested_model_ids.add(model_id)
    if candidate.fallback_model_id:
        fallback_model_id = candidate.fallback_model_id.strip()
        if fallback_model_id:
            requested_model_ids.add(fallback_model_id)

    return requested_model_ids


def _candidate_refresh_every_runs(candidate: CostOptimizerCandidateRequest) -> int:
    policy = candidate.model_routing_policy
    if not isinstance(policy, dict):
        return 20
    refresh = policy.get("refresh")
    if not isinstance(refresh, dict):
        return 20
    try:
        parsed = int(refresh.get("refresh_every_runs"))
    except (TypeError, ValueError):
        parsed = 20
    return max(5, min(100, parsed))


def _default_cost_optimizer_model_routing_policy(
    candidate: CostOptimizerCandidateRequest,
    candidate_models: list[ModelCandidate],
) -> dict[str, Any]:
    if not candidate_models:
        raise HTTPException(status_code=422, detail="cost_optimizer.model_unavailable")

    available_model_ids = {model.model_id for model in candidate_models}
    requested_model_id = str(candidate.model_id or "").strip()
    default_model_id = (
        requested_model_id
        if requested_model_id in available_model_ids
        else candidate_models[0].model_id
    )
    requested_fallback_id = str(candidate.fallback_model_id or "").strip()
    fallback_model_id = (
        requested_fallback_id
        if requested_fallback_id in available_model_ids
        and requested_fallback_id != default_model_id
        else None
    )

    policy_version = "gateway-judge-first-v1"
    active_policy = build_judge_first_active_policy(
        policy_version=policy_version,
        default_model_id=default_model_id,
        fallback_model_id=fallback_model_id,
        candidate_model_ids=[model.model_id for model in candidate_models],
    )
    return {
        "status": "collecting",
        "policy_id": "cost-optimizer-judge-first-default",
        "policy_version": policy_version,
        "active_policy": active_policy,
        "refresh": {
            "refresh_every_runs": _candidate_refresh_every_runs(candidate),
            "last_refresh_result": "judge_first_ready",
            "last_refresh_trigger": "cost_optimizer_candidate",
        },
    }


def _materialize_cost_optimizer_candidate_model_routing_policy(
    db: Session,
    current_user: User,
    candidate: CostOptimizerCandidateRequest,
) -> CostOptimizerCandidateRequest:
    if candidate.auto_model_routing is not True:
        return candidate
    if _candidate_has_active_model_routing_policy(candidate):
        return candidate

    candidate_models = _cost_optimizer_available_model_candidates(db, current_user)
    default_policy = _default_cost_optimizer_model_routing_policy(
        candidate,
        candidate_models,
    )
    existing_policy = (
        candidate.model_routing_policy
        if isinstance(candidate.model_routing_policy, dict)
        else {}
    )
    candidate_data = candidate.model_dump(mode="python")
    candidate_data["model_routing_policy"] = _deep_merge_dict(
        existing_policy,
        default_policy,
    )
    active_policy = default_policy["active_policy"]
    if not str(candidate_data.get("model_id") or "").strip():
        candidate_data["model_id"] = active_policy["default_model_id"]
    if not str(candidate_data.get("fallback_model_id") or "").strip():
        candidate_data["fallback_model_id"] = active_policy.get("fallback_model_id")
    return CostOptimizerCandidateRequest(**candidate_data)


def _ensure_cost_optimizer_candidate_models_available(
    db: Session,
    current_user: User,
    candidate: CostOptimizerCandidateRequest,
) -> None:
    available_model_ids = set(_cost_optimizer_available_model_ids(db, current_user))
    requested_model_ids = _candidate_requested_model_ids(candidate)
    if not requested_model_ids:
        _raise_invalid_cost_optimizer_candidate()
    if not requested_model_ids.issubset(available_model_ids):
        raise HTTPException(
            status_code=422,
            detail="cost_optimizer.model_unavailable",
        )


def _find_workflow_node(
    graph: dict[str, Any] | None, node_id: str
) -> dict[str, Any] | None:
    if not isinstance(graph, dict):
        return None
    nodes = graph.get("nodes") or []
    for node in nodes:
        if isinstance(node, dict) and str(node.get("id")) == node_id:
            return node
    return None


def _ensure_cost_optimizer_llm_node(workflow: Workflow, node_id: str) -> dict[str, Any]:
    node = _find_workflow_node(workflow.graph, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="resource.not_found")

    if str(node.get("type") or "") != "llmNode":
        raise HTTPException(status_code=400, detail="cost_optimizer.not_llm_node")

    return node


def _active_deployment_for_workflow(
    db: Session, workflow: Workflow
) -> WorkflowDeployment | None:
    deployments = _active_deployments_for_workflow(db, workflow)
    return deployments[0] if deployments else None


def _active_deployments_for_workflow(
    db: Session, workflow: Workflow
) -> list[WorkflowDeployment]:
    return (
        db.query(WorkflowDeployment)
        .join(App, App.id == WorkflowDeployment.app_id)
        .filter(App.workflow_id == workflow.id)
        .filter(WorkflowDeployment.is_active.is_(True))
        .order_by(WorkflowDeployment.created_at.desc())
        .all()
    )


def _test_routing_policy_context(
    db: Session,
    *,
    workflow: Workflow,
    graph: dict[str, Any],
) -> dict[str, Any]:
    """자동 라우팅 LLM node를 테스트하되 일치하는 배포 정책만 재사용한다.

    테스트 실행은 deployment run이 아니므로 ``deployment_id``를 넣지 않는다.
    현재 draft와 활성 배포 설정이 같은 node는 저장 정책을 조회하고, 나머지는
    runtime에서 임시 Judge-first 정책을 만든다. 어느 경로도 운영 학습/갱신에는
    포함하지 않는다.
    """
    preview_node_ids = [
        str(node.get("id"))
        for node in graph.get("nodes", [])
        if isinstance(node, dict)
        and node.get("type") == "llmNode"
        and isinstance(node.get("data"), dict)
        and node["data"].get("auto_model_routing")
        and str(node.get("id") or "")
    ]
    if not preview_node_ids:
        return {}

    deployments = _active_deployments_for_workflow(db, workflow)
    matching_deployment_ids_by_node: dict[str, str] = {}
    ambiguous_node_ids: list[str] = []
    for node in graph.get("nodes", []):
        if not isinstance(node, dict) or node.get("type") != "llmNode":
            continue
        node_id = str(node.get("id") or "")
        node_data = node.get("data")
        if not node_id or not isinstance(node_data, dict) or not node_data.get("auto_model_routing"):
            continue
        matching_deployments = []
        for deployment in deployments:
            snapshot = deployment.graph_snapshot
            if not isinstance(snapshot, dict):
                continue
            deployed_node = next(
                (
                    item
                    for item in snapshot.get("nodes", [])
                    if isinstance(item, dict)
                    and item.get("type") == "llmNode"
                    and str(item.get("id") or "") == node_id
                ),
                None,
            )
            deployed_data = deployed_node.get("data") if deployed_node else None
            if (
                isinstance(deployed_data, dict)
                and deployed_data.get("auto_model_routing")
                and _node_config_fingerprint(node_data)
                == _node_config_fingerprint(deployed_data)
            ):
                matching_deployments.append(deployment)
        if len(matching_deployments) == 1:
            matching_deployment_ids_by_node[node_id] = str(matching_deployments[0].id)
        elif len(matching_deployments) > 1:
            ambiguous_node_ids.append(node_id)

    context = {
        "routing_policy_preview_node_ids": preview_node_ids,
        "routing_policy_deployment_node_ids": list(matching_deployment_ids_by_node),
        "routing_policy_deployment_ids_by_node": matching_deployment_ids_by_node,
        "routing_policy_ambiguous_node_ids": ambiguous_node_ids,
        "routing_policy_preview": True,
        # Test Sidebar는 실제 workflow를 실행하므로 배포 runtime과 같은 Judge 선택을
        # 수행한다. read-only routing preview API는 이 flag를 전달하지 않는다.
        "routing_policy_execute_judge": True,
    }
    return context


def _model_routing_policy_response(
    policy: LLMNodeModelRoutingPolicy | None,
    *,
    enabled: bool,
    db: Session | None = None,
    decision_deployment_id: UUID | None = None,
    decision_node_id: str | None = None,
) -> dict[str, Any]:
    last_decision = _model_routing_latest_decision_summary(
        db,
        policy,
        deployment_id=decision_deployment_id,
        node_id=decision_node_id,
    )
    if policy is None:
        return {
            "enabled": enabled,
            "status": "collecting" if enabled else "off",
            "policy_id": None,
            "bootstrap_id": None,
            "policy_version": None,
            "active_policy": None,
            "learner": None,
            "pending_policy": None,
            "refresh": {
                "refresh_every_runs": 20,
                "eligible_runs_since_last_refresh": 0,
                "next_refresh_after_runs": 20,
                "last_refresh_result": None,
                "last_refresh_at": None,
            },
            "last_decision": last_decision,
        }

    refresh_every_runs = policy.refresh_every_runs
    eligible = policy.eligible_runs_since_last_refresh
    bootstrap_id = getattr(policy, "bootstrap_id", None)
    from apps.workflow_engine.services.model_routing_learner_store import (
        ModelRoutingLearnerStore,
    )

    learner = (
        ModelRoutingLearnerStore.public_summary(
            db,
            learner_id=getattr(policy, "learner_id", None),
            version_id=getattr(policy, "active_learner_version_id", None),
        )
        if db is not None
        else None
    )
    active_policy = dict(policy.active_policy or {})
    active_policy.pop("learning", None)
    return {
        "enabled": policy.enabled,
        "status": policy.status,
        "policy_id": str(policy.id),
        "bootstrap_id": str(bootstrap_id) if bootstrap_id else None,
        "policy_version": policy.policy_version,
        "active_policy": active_policy or None,
        "learner": learner,
        "pending_policy": policy.pending_policy,
        "refresh": {
            "refresh_every_runs": refresh_every_runs,
            "eligible_runs_since_last_refresh": eligible,
            "next_refresh_after_runs": max(0, refresh_every_runs - eligible),
            "last_refresh_result": policy.last_refresh_result,
            "last_refresh_at": policy.last_refreshed_at.isoformat()
            if policy.last_refreshed_at
            else None,
        },
        "last_decision": last_decision,
    }


_MODEL_ROUTING_REASON_LABELS = {
    "simple_response": "간단한 응답 처리",
    "multi_constraint": "여러 조건 종합",
    "evidence_synthesis": "근거 종합 필요",
    "structured_precision": "정확한 형식 필요",
    "high_risk_reasoning": "고위험 판단 필요",
    "ambiguous_request": "모호한 요청 판단",
    "long_context": "긴 문맥 종합",
    "local_router_confident": "학습된 선택 기준",
    "local_router_uncertain": "확신 부족 기본 선택",
    "runtime_judge_unavailable": "Judge를 사용할 수 없음",
}


def _model_routing_latest_decision_summary(
    db: Session | None,
    policy: LLMNodeModelRoutingPolicy | None,
    *,
    deployment_id: UUID | None = None,
    node_id: str | None = None,
) -> dict[str, Any] | None:
    """Return the latest safe routing decision without exposing request payloads."""

    if db is None:
        return None
    deployment_id = deployment_id or getattr(policy, "deployment_id", None)
    node_id = str(node_id or getattr(policy, "node_id", "") or "").strip()
    if deployment_id is None or not node_id:
        return None

    try:
        node_run = (
            db.query(WorkflowNodeRun)
            .join(WorkflowRun, WorkflowNodeRun.workflow_run_id == WorkflowRun.id)
            .filter(WorkflowNodeRun.node_id == node_id)
            .filter(WorkflowRun.deployment_id == deployment_id)
            .order_by(
                WorkflowNodeRun.finished_at.desc(),
                WorkflowNodeRun.started_at.desc(),
            )
            .first()
        )
    except Exception:
        return None

    outputs = getattr(node_run, "outputs", None)
    metadata = outputs.get("metadata") if isinstance(outputs, dict) else None
    routing = metadata.get("model_routing") if isinstance(metadata, dict) else None
    if not isinstance(routing, dict):
        return None

    selected_model_id = str(routing.get("selected_model") or "").strip()
    if not selected_model_id:
        return None
    judge = routing.get("judge") if isinstance(routing.get("judge"), dict) else {}
    reason_code = str(routing.get("reason_code") or "").strip() or None
    reason_short = str(judge.get("reason_short") or "").strip() or None
    return {
        "selected_model_id": selected_model_id,
        "fallback_model_id": str(routing.get("fallback_model") or "").strip()
        or None,
        "fallback_used": bool(routing.get("fallback_used")),
        "decision_source": str(routing.get("decision_source") or "").strip()
        or None,
        "reason_code": reason_code,
        "reason_label": reason_short
        or _MODEL_ROUTING_REASON_LABELS.get(reason_code or "", "선택 근거 기록 없음"),
        "created_at": node_run.finished_at.isoformat()
        if getattr(node_run, "finished_at", None)
        else None,
    }


def _get_model_routing_policy_for_workflow(
    db: Session,
    workflow: Workflow,
    node_id: str,
) -> LLMNodeModelRoutingPolicy | None:
    deployment = _active_deployment_for_workflow(db, workflow)
    if deployment is None:
        return None
    return (
        db.query(LLMNodeModelRoutingPolicy)
        .filter(LLMNodeModelRoutingPolicy.workflow_id == workflow.id)
        .filter(LLMNodeModelRoutingPolicy.deployment_id == deployment.id)
        .filter(LLMNodeModelRoutingPolicy.node_id == node_id)
        .first()
    )


def _canonical_json_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cost_optimizer_downstream_contracts_for_consumer(
    target_node_id: str,
    consumer: dict[str, Any],
) -> list[dict[str, Any]]:
    consumer_type = str(consumer.get("type") or "")
    data = consumer.get("data") if isinstance(consumer.get("data"), dict) else {}
    contracts: list[dict[str, Any]] = []

    if consumer_type == "variableExtractionNode":
        source_selector = data.get("source_selector") or []
        if not source_selector or source_selector[0] != target_node_id:
            return contracts
        selector = source_selector[1] if len(source_selector) > 1 else "text"
        for mapping in data.get("mappings") or []:
            if not isinstance(mapping, dict):
                continue
            json_path = str(mapping.get("json_path") or "").strip()
            if not json_path:
                continue
            contracts.append(
                {
                    "kind": "json_path",
                    "selector": selector,
                    "path": json_path,
                    "required": True,
                }
            )

    elif consumer_type == "conditionNode":
        for case in data.get("cases") or []:
            if not isinstance(case, dict):
                continue
            for condition in case.get("conditions") or []:
                if not isinstance(condition, dict):
                    continue
                selector = condition.get("variable_selector") or []
                if selector and selector[0] == target_node_id and len(selector) > 1:
                    contracts.append(
                        {
                            "kind": "selector",
                            "key": str(selector[1]),
                            "required": True,
                        }
                    )

    elif consumer_type == "answerNode":
        for output in data.get("outputs") or []:
            if not isinstance(output, dict):
                continue
            selector = output.get("value_selector") or []
            if selector and selector[0] == target_node_id and len(selector) > 1:
                contracts.append(
                    {
                        "kind": "selector",
                        "key": str(selector[1]),
                        "required": True,
                    }
                )

    elif consumer_type == "slackPostNode":
        for variable in data.get("referenced_variables") or []:
            if not isinstance(variable, dict):
                continue
            selector = variable.get("value_selector") or []
            if selector and selector[0] == target_node_id and len(selector) > 1:
                contracts.append(
                    {
                        "kind": "selector",
                        "key": str(selector[1]),
                        "required": True,
                    }
                )

    return contracts


def _cost_optimizer_downstream_has_side_effect(node_type: str) -> bool:
    return node_type in {"slackPostNode", "httpRequestNode", "databaseNode"}


def build_cost_optimizer_downstream_snapshot(
    graph: dict[str, Any] | None,
    node_id: str,
) -> dict[str, Any]:
    nodes = graph.get("nodes") if isinstance(graph, dict) else []
    edges = graph.get("edges") if isinstance(graph, dict) else []
    nodes = nodes if isinstance(nodes, list) else []
    edges = edges if isinstance(edges, list) else []
    node_by_id = {
        str(node.get("id")): node
        for node in nodes
        if isinstance(node, dict) and node.get("id")
    }
    node_types = {
        str(node.get("id")): str(node.get("type") or "")
        for node in nodes
        if isinstance(node, dict) and node.get("id")
    }
    adjacency: dict[str, list[str]] = {}
    normalized_edges = []

    for edge in edges:
        if not isinstance(edge, dict):
            continue
        source = edge.get("source")
        target = edge.get("target")
        if source is None or target is None:
            continue
        source_id = str(source)
        target_id = str(target)
        adjacency.setdefault(source_id, []).append(target_id)
        normalized_edges.append(
            {
                "source": source_id,
                "target": target_id,
                "source_handle": edge.get("sourceHandle"),
                "target_handle": edge.get("targetHandle"),
            }
        )

    direct_consumer_ids = sorted(set(adjacency.get(node_id, [])))
    visited: set[str] = set()
    queue = list(direct_consumer_ids)
    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        queue.extend(adjacency.get(current, []))

    downstream_edges = [
        edge
        for edge in normalized_edges
        if edge["source"] == node_id
        or edge["source"] in visited
        or edge["target"] in visited
    ]
    comparable = {
        "direct_consumers": [
            {
                "id": consumer_id,
                "type": node_types.get(consumer_id, ""),
                "contracts": _cost_optimizer_downstream_contracts_for_consumer(
                    node_id,
                    node_by_id.get(consumer_id, {}),
                ),
                "has_side_effect": _cost_optimizer_downstream_has_side_effect(
                    node_types.get(consumer_id, "")
                ),
            }
            for consumer_id in direct_consumer_ids
        ],
        "reachable_nodes": [
            {
                "id": downstream_id,
                "type": node_types.get(downstream_id, ""),
                "has_side_effect": _cost_optimizer_downstream_has_side_effect(
                    node_types.get(downstream_id, "")
                ),
            }
            for downstream_id in sorted(visited)
        ],
        "edges": sorted(
            downstream_edges,
            key=lambda item: (
                item["source"],
                item["target"],
                str(item.get("source_handle") or ""),
                str(item.get("target_handle") or ""),
            ),
        ),
    }
    return {
        **comparable,
        "hash": _canonical_json_hash(comparable),
    }


def _cost_optimizer_json_path_exists(payload: Any, json_path: str) -> bool:
    path = str(json_path or "").strip()
    if not path:
        return True
    current = payload
    for segment in path.split("."):
        if isinstance(current, dict) and segment in current:
            current = current[segment]
            continue
        return False
    return True


def _cost_optimizer_contract_payload(
    candidate_output: Any, selector: str | None
) -> Any:
    if not isinstance(candidate_output, dict):
        return candidate_output
    selected = candidate_output.get(selector or "text")
    if selected is None and selector is not None:
        return None
    if selected is None:
        selected = candidate_output
    if isinstance(selected, str):
        try:
            return json.loads(selected)
        except json.JSONDecodeError:
            return selected
    return selected


def _cost_optimizer_selector_exists(candidate_output: Any, selector: list[Any]) -> bool:
    if len(selector) < 2:
        return True
    key = selector[1]
    if not isinstance(key, str) or not key:
        return True
    if isinstance(candidate_output, dict):
        if key in candidate_output:
            return True
        text_payload = _cost_optimizer_contract_payload(candidate_output, "text")
        return _cost_optimizer_json_path_exists(text_payload, key)
    return False


def _cost_optimizer_downstream_contract_check(
    baseline_snapshot: dict[str, Any],
    candidate_output: Any,
) -> dict[str, Any]:
    checked_node_ids: list[str] = []
    warnings: list[str] = []

    direct_consumers = baseline_snapshot.get("direct_consumers")
    direct_consumers = direct_consumers if isinstance(direct_consumers, list) else []
    for consumer in direct_consumers:
        if not isinstance(consumer, dict):
            continue
        consumer_id = str(consumer.get("id"))
        checked_node_ids.append(consumer_id)
        contracts = consumer.get("contracts")
        contracts = contracts if isinstance(contracts, list) else []
        for contract in contracts:
            if not isinstance(contract, dict) or not contract.get("required", True):
                continue
            if contract.get("kind") == "json_path":
                json_path = contract.get("path")
                payload = _cost_optimizer_contract_payload(
                    candidate_output,
                    str(contract.get("selector") or "text"),
                )
                if not _cost_optimizer_json_path_exists(payload, str(json_path or "")):
                    warnings.append(
                        f"{consumer_id} requires candidate output path '{json_path}'"
                    )
            elif contract.get("kind") == "selector":
                key = str(contract.get("key") or "")
                if key and not _cost_optimizer_selector_exists(
                    candidate_output,
                    ["", key],
                ):
                    warnings.append(
                        f"{consumer_id} requires candidate output selector '{key}'"
                    )

    return {
        "status": "failed" if warnings else "pass",
        "checked_node_ids": checked_node_ids,
        "warnings": warnings,
    }


def build_cost_optimizer_downstream_compatibility(
    baseline_snapshot: dict[str, Any] | None,
    current_graph: dict[str, Any] | None,
    node_id: str,
    candidate_output: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(baseline_snapshot, dict):
        return {
            "state": "unknown",
            "label": "판정 전",
            "message": "baseline 실행 시점의 downstream snapshot이 없어 판정할 수 없습니다.",
            "first_consumer_status": "unknown",
            "contract_check": {
                "status": "skipped",
                "checked_node_ids": [],
                "warnings": ["baseline_downstream_snapshot_unavailable"],
            },
        }

    current_snapshot = build_cost_optimizer_downstream_snapshot(
        current_graph,
        node_id,
    )
    output_contract_check = (
        _cost_optimizer_downstream_contract_check(
            baseline_snapshot,
            candidate_output,
        )
        if candidate_output is not None
        else None
    )
    baseline_hash = baseline_snapshot.get("hash")
    current_hash = current_snapshot.get("hash")
    baseline_consumers = [
        consumer.get("id")
        for consumer in baseline_snapshot.get("direct_consumers", [])
        if isinstance(consumer, dict) and consumer.get("id")
    ]
    current_consumers = [
        consumer.get("id")
        for consumer in current_snapshot.get("direct_consumers", [])
        if isinstance(consumer, dict) and consumer.get("id")
    ]
    checked_node_ids = list(current_consumers)

    if output_contract_check and output_contract_check["status"] == "failed":
        return {
            "state": "incompatible",
            "label": "검증 불가",
            "message": "후보 출력이 현재 downstream 소비 노드의 필수 입력 계약을 만족하지 못합니다.",
            "baseline_downstream_hash": baseline_hash,
            "current_downstream_hash": current_hash,
            "first_consumer_status": "same"
            if baseline_consumers == current_consumers
            else "changed",
            "contract_check": output_contract_check,
        }

    if baseline_hash == current_hash:
        return {
            "state": "compatible",
            "label": "검증 가능",
            "message": "baseline 실행 시점의 downstream과 현재 downstream이 호환됩니다.",
            "baseline_downstream_hash": baseline_hash,
            "current_downstream_hash": current_hash,
            "first_consumer_status": "same",
            "contract_check": output_contract_check
            or {
                "status": "pass",
                "checked_node_ids": checked_node_ids,
                "warnings": [],
            },
        }

    if baseline_consumers and baseline_consumers == current_consumers:
        warnings = ["downstream structure changed after first consumer"]
        if output_contract_check:
            warnings.extend(output_contract_check.get("warnings") or [])
        return {
            "state": "warning",
            "label": "주의 필요",
            "message": "downstream 구조가 일부 달라졌지만 첫 소비 노드는 동일합니다. 적용 전 현재 workflow 테스트 실행으로 확인해야 합니다.",
            "baseline_downstream_hash": baseline_hash,
            "current_downstream_hash": current_hash,
            "first_consumer_status": "same",
            "contract_check": {
                "status": "warning",
                "checked_node_ids": output_contract_check.get(
                    "checked_node_ids", checked_node_ids
                )
                if output_contract_check
                else checked_node_ids,
                "warnings": warnings,
            },
        }

    return {
        "state": "incompatible",
        "label": "검증 불가",
        "message": "target LLM node 이후 소비 노드가 바뀌어 현재 workflow에서 downstream 성공 여부를 별도로 검증해야 합니다.",
        "baseline_downstream_hash": baseline_hash,
        "current_downstream_hash": current_hash,
        "first_consumer_status": "changed",
        "contract_check": {
            "status": "failed",
            "checked_node_ids": checked_node_ids,
            "warnings": ["first consumer changed or missing"],
        },
    }


SENSITIVE_BASELINE_KEYS = {"api_key", "authorization", "encrypted_config", "secret"}
SENSITIVE_CANDIDATE_PROMPT_FIELDS = (
    "system_prompt",
    "user_prompt",
    "assistant_prompt",
)
SAFE_COST_OPTIMIZER_RAG_SUMMARY_KEYS = {
    "retrieved_chunk_count",
    "knowledge_base_count",
    "context_token_estimate",
    "evidence_sufficient",
    "source_summary",
    "score_summary",
    "hierarchy_fallback",
}
COST_OPTIMIZER_SAFE_SUMMARY_MAX_LIST_ITEMS = 20
COST_OPTIMIZER_SAFE_SUMMARY_MAX_STRING_CHARS = 200


def _redact_baseline_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if str(key).lower() in SENSITIVE_BASELINE_KEYS
            else _redact_baseline_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_baseline_value(item) for item in value]
    return value


def _safe_cost_optimizer_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _safe_cost_optimizer_value(item)
            for key, item in value.items()
            if str(key).lower() not in SENSITIVE_BASELINE_KEYS
        }
    if isinstance(value, list):
        return [_safe_cost_optimizer_value(item) for item in value]
    return value


def _cost_optimizer_stable_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _cost_optimizer_candidate_settings_fingerprint(settings: dict[str, Any]) -> str:
    return hashlib.sha256(
        _cost_optimizer_stable_json(settings).encode("utf-8")
    ).hexdigest()


def _safe_cost_optimizer_candidate_settings(
    candidate: CostOptimizerCandidateRequest,
) -> dict[str, Any]:
    raw_settings = candidate.model_dump(mode="json")
    safe_settings = _safe_cost_optimizer_value(raw_settings)
    if not isinstance(safe_settings, dict):
        safe_settings = {}
    safe_settings["_settings_fingerprint"] = (
        _cost_optimizer_candidate_settings_fingerprint(raw_settings)
    )

    for field in SENSITIVE_CANDIDATE_PROMPT_FIELDS:
        value = raw_settings.get(field)
        if isinstance(value, str):
            safe_settings[field] = {
                "redacted": True,
                "present": bool(value),
                "length": len(value),
            }
        else:
            safe_settings[field] = None

    return safe_settings


def _safe_cost_optimizer_rag_summary(value: Any) -> Any:
    safe_value = _safe_cost_optimizer_value(value)
    if isinstance(safe_value, dict):
        return {
            key: _safe_cost_optimizer_rag_summary_value(item)
            for key, item in safe_value.items()
            if str(key) in SAFE_COST_OPTIMIZER_RAG_SUMMARY_KEYS
        }
    return None


def _safe_cost_optimizer_rag_summary_value(value: Any) -> Any:
    """허용된 RAG 집계값 안에서도 원문 객체가 다시 섞이지 않게 제한한다."""
    if isinstance(value, dict):
        return {
            str(key)[:COST_OPTIMIZER_SAFE_SUMMARY_MAX_STRING_CHARS]: (
                _safe_cost_optimizer_rag_summary_value(item)
            )
            for key, item in list(value.items())[
                :COST_OPTIMIZER_SAFE_SUMMARY_MAX_LIST_ITEMS
            ]
            if isinstance(item, (str, int, float, bool)) or item is None
        }
    if isinstance(value, list):
        return [
            _safe_cost_optimizer_rag_summary_value(item)
            for item in value[:COST_OPTIMIZER_SAFE_SUMMARY_MAX_LIST_ITEMS]
            if isinstance(item, (str, int, float, bool)) or item is None
        ]
    if isinstance(value, str):
        return value[:COST_OPTIMIZER_SAFE_SUMMARY_MAX_STRING_CHARS]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return None


def _preview_baseline_payload(value: Any) -> str:
    if value is None:
        return ""
    redacted = _redact_baseline_value(value)
    if isinstance(redacted, str):
        text = redacted
    else:
        text = json.dumps(redacted, ensure_ascii=False, default=str)
    return text[:300]


def _trace_payload_value(
    node_run: WorkflowNodeRun,
    payload_kind: str,
) -> tuple[bool, bool, Any]:
    trace_payloads = getattr(node_run, "trace_payloads", None) or []
    for payload in trace_payloads:
        if getattr(payload, "payload_kind", None) != payload_kind:
            continue
        if getattr(payload, "retention_purged_at", None) is not None:
            return True, False, None
        return True, True, getattr(payload, "redacted_payload", None)
    return False, False, None


def _usage_model_name(usage: LLMUsageLog) -> str:
    model = getattr(usage, "model", None)
    for attr in ("model_id_for_api_call", "name"):
        value = getattr(model, attr, None)
        if value:
            return str(value)
    return str(usage.model_id)


def _decimal_to_float(value: Any) -> float:
    if isinstance(value, Decimal):
        return float(value)
    return float(value or 0)


def _cost_optimizer_baseline_latency_ms(
    node_run: WorkflowNodeRun,
    usage: LLMUsageLog,
) -> int:
    usage_latency = getattr(usage, "latency_ms", None)
    if isinstance(usage_latency, (int, float)) and usage_latency > 0:
        return int(usage_latency)

    node_duration = getattr(node_run, "duration", None)
    if isinstance(node_duration, (int, float)) and node_duration > 0:
        return int(node_duration * 1000)

    return int(usage_latency or 0)


def _baseline_row_from_records(
    *,
    workflow: Workflow,
    run: WorkflowRun,
    node_run: WorkflowNodeRun,
    usage: LLMUsageLog,
) -> dict[str, Any]:
    has_trace_input, trace_input_available, trace_input = _trace_payload_value(
        node_run,
        "input",
    )
    has_trace_output, trace_output_available, trace_output = _trace_payload_value(
        node_run,
        "output",
    )
    input_payload = trace_input if has_trace_input else node_run.inputs
    output_payload = trace_output if has_trace_output else node_run.outputs
    input_available = (
        trace_input_available if has_trace_input else node_run.inputs is not None
    )
    output_available = (
        trace_output_available if has_trace_output else node_run.outputs is not None
    )
    usage_available = usage is not None
    compare_available = input_available and output_available and usage_available
    total_tokens = int((usage.prompt_tokens or 0) + (usage.completion_tokens or 0))
    cost = _decimal_to_float(usage.total_cost)
    latency_ms = _cost_optimizer_baseline_latency_ms(node_run, usage)
    model = _usage_model_name(usage)
    input_preview = _preview_baseline_payload(input_payload)
    output_preview = _preview_baseline_payload(output_payload)
    trace_available = (
        bool(node_run.trace_metadata) or has_trace_input or has_trace_output
    )
    trace_metadata = (
        node_run.trace_metadata if isinstance(node_run.trace_metadata, dict) else {}
    )
    llm_trace_summary = TraceMetadataSanitizer.sanitize_span_metadata(
        "llmNode",
        {"llm": trace_metadata.get("llm")},
    ).get("llm")
    rag_summary = trace_metadata.get("rag") or trace_metadata.get("rag_summary")
    rag_summary = _safe_cost_optimizer_rag_summary(rag_summary)
    rag_summary = rag_summary if isinstance(rag_summary, dict) else None
    process_data = getattr(node_run, "process_data", None) or {}
    node_options = (
        process_data.get("node_options") if isinstance(process_data, dict) else {}
    )
    node_options = node_options if isinstance(node_options, dict) else {}
    workflow_graph = getattr(workflow, "graph", None)
    downstream_snapshot = build_cost_optimizer_downstream_snapshot(
        workflow_graph,
        node_run.node_id,
    )
    downstream_compatibility = build_cost_optimizer_downstream_compatibility(
        downstream_snapshot,
        workflow_graph,
        node_run.node_id,
    )

    return {
        "baseline_id": str(node_run.id),
        "baseline_source": "workflow_node_run",
        "source_workflow_node_run_id": str(node_run.id),
        "workflow_run_id": str(run.id),
        "deployment_id": (
            str(getattr(run, "deployment_id", None))
            if getattr(run, "deployment_id", None)
            else None
        ),
        "workflow_id": str(workflow.id),
        "node_id": node_run.node_id,
        "run_started_at": run.started_at.isoformat(),
        "workflow_run_status": run.status.value
        if hasattr(run.status, "value")
        else str(run.status),
        "node_status": node_run.status.value
        if hasattr(node_run.status, "value")
        else str(node_run.status),
        "model": model,
        "cost": cost,
        "total_tokens": total_tokens,
        "latency_ms": latency_ms,
        "input_available": input_available,
        "output_available": output_available,
        "usage_available": usage_available,
        "trace_available": trace_available,
        "compare_available": compare_available,
        "unavailable_reason": None
        if compare_available
        else "input_payload_unavailable",
        "input_preview": input_preview,
        "output_preview": output_preview,
        "has_trace": trace_available,
        "input": _redact_baseline_value(input_payload),
        "output": _redact_baseline_value(output_payload),
        "node_options": _redact_baseline_value(node_options),
        "node_config_fingerprint": _node_config_fingerprint(node_options),
        "downstream_snapshot": downstream_snapshot,
        "usage": {
            "model": model,
            "prompt_tokens": int(usage.prompt_tokens or 0),
            "completion_tokens": int(usage.completion_tokens or 0),
            "total_tokens": total_tokens,
            "cost": cost,
            "latency_ms": latency_ms,
            "status": usage.status,
        },
        "trace": {
            "input_preview": input_preview,
            "output_preview": output_preview,
            "messages_preview": [],
            "rag_summary": rag_summary,
            **({"model_routing": llm_trace_summary} if llm_trace_summary else {}),
            "error_message": node_run.error_message,
        },
        "downstream_compatibility": downstream_compatibility,
    }


def _cost_optimizer_baseline_rows(
    db: Session,
    workflow: Workflow,
    node_id: str,
) -> list[dict[str, Any]]:
    rows = (
        db.query(WorkflowNodeRun, WorkflowRun, LLMUsageLog)
        .join(WorkflowRun, WorkflowRun.id == WorkflowNodeRun.workflow_run_id)
        .join(
            LLMUsageLog,
            (LLMUsageLog.workflow_run_id == WorkflowRun.id)
            & (LLMUsageLog.workflow_id == workflow.id)
            & (LLMUsageLog.node_id == WorkflowNodeRun.node_id),
        )
        .filter(
            WorkflowRun.workflow_id == workflow.id,
            WorkflowRun.status == RunStatus.SUCCESS,
            WorkflowNodeRun.node_id == node_id,
            WorkflowNodeRun.node_type == "llmNode",
            WorkflowNodeRun.status == NodeRunStatus.SUCCESS,
            LLMUsageLog.status == "success",
            LLMUsageLog.cost_optimizer_candidate_id.is_(None),
            ~_cost_optimizer_candidate_run_exists(db),
            _not_cost_optimizer_trace_run(),
        )
        .all()
    )

    return [
        _baseline_row_from_records(
            workflow=workflow,
            run=run,
            node_run=node_run,
            usage=usage,
        )
        for node_run, run, usage in rows
    ]


def _cost_optimizer_candidate_run_exists(db: Session):
    return (
        db.query(CostOptimizerCandidate.id)
        .filter(CostOptimizerCandidate.candidate_workflow_run_id == WorkflowRun.id)
        .exists()
    )


def _not_cost_optimizer_trace_run():
    return or_(
        WorkflowRun.trace_metadata.is_(None),
        ~WorkflowRun.trace_metadata.has_key("cost_optimizer"),  # noqa: W601
    )


def _has_cost_optimizer_baseline_result(row: dict[str, Any]) -> bool:
    return bool(row.get("output_available")) and bool(row.get("usage_available"))


def get_cost_optimizer_latest_baseline(
    db: Session,
    workflow: Workflow,
    node_id: str,
) -> dict[str, Any]:
    candidates = [
        row
        for row in _cost_optimizer_baseline_rows(db, workflow, node_id)
        if _has_cost_optimizer_baseline_result(row) and row["compare_available"]
    ]
    if not candidates:
        raise HTTPException(status_code=400, detail="cost_optimizer.no_baseline")
    return sorted(candidates, key=lambda row: row["run_started_at"], reverse=True)[0]


def get_cost_optimizer_latest_operation_baseline(
    db: Session,
    workflow: Workflow,
    node_id: str,
) -> dict[str, Any]:
    """현재 활성 deployment/config cohort의 최신 성공 운영 baseline을 고정한다."""
    node = _ensure_cost_optimizer_llm_node(workflow, node_id)
    node_data = node.get("data") if isinstance(node.get("data"), dict) else {}
    node_data = node_data if isinstance(node_data, dict) else {}
    cohort = _resolve_operation_cohort(
        db,
        workflow=workflow,
        node_id=node_id,
        current_node_data=node_data,
    )
    if cohort.get("status") != "active_deployment":
        raise HTTPException(
            status_code=409, detail="cost_optimizer.recommendation_stale"
        )

    expected_deployment_id = str(cohort.get("deployment_id"))
    # 현재 draft와 활성 배포 snapshot의 설정 일치는 cohort 해석에서 이미
    # 확인한다. 실행 로그의 process_data는 보안 마스킹된 값일 수 있으므로,
    # 같은 불변 배포 ID를 가진 운영 실행을 다시 fingerprint로 제외하지 않는다.
    candidates = [
        row
        for row in _cost_optimizer_baseline_rows(db, workflow, node_id)
        if _has_cost_optimizer_baseline_result(row)
        and row.get("compare_available")
        and row.get("deployment_id") == expected_deployment_id
    ]
    if not candidates:
        raise HTTPException(status_code=400, detail="cost_optimizer.no_baseline")
    return sorted(candidates, key=lambda row: row["run_started_at"], reverse=True)[0]


def list_cost_optimizer_baselines(
    db: Session,
    workflow: Workflow,
    node_id: str,
    *,
    q: str | None = None,
    model: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    sort: str = "started_at_desc",
    compare_available: bool | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    rows = [
        row
        for row in _cost_optimizer_baseline_rows(db, workflow, node_id)
        if _has_cost_optimizer_baseline_result(row)
    ]

    if q:
        query_text = q.lower()
        rows = [
            row
            for row in rows
            if query_text in (row.get("input_preview") or "").lower()
            or query_text in (row.get("output_preview") or "").lower()
        ]
    if model:
        rows = [row for row in rows if row.get("model") == model]
    if date_from:
        rows = [
            row
            for row in rows
            if datetime.fromisoformat(row["run_started_at"]) >= date_from
        ]
    if date_to:
        rows = [
            row
            for row in rows
            if datetime.fromisoformat(row["run_started_at"]) <= date_to
        ]
    if compare_available is not None:
        rows = [
            row for row in rows if row.get("compare_available") is compare_available
        ]

    sort_key = {
        "cost_desc": lambda row: row.get("cost") or 0,
        "cost_asc": lambda row: row.get("cost") or 0,
        "tokens_desc": lambda row: row.get("total_tokens") or 0,
        "latency_desc": lambda row: row.get("latency_ms") or 0,
        "started_at_desc": lambda row: row.get("run_started_at") or "",
    }.get(sort, lambda row: row.get("run_started_at") or "")
    rows = sorted(rows, key=sort_key, reverse=sort != "cost_asc")

    total = len(rows)
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": rows[offset : offset + limit],
    }


def get_cost_optimizer_baseline_by_id(
    db: Session,
    workflow: Workflow,
    node_id: str,
    baseline_id: str,
) -> dict[str, Any]:
    for row in _cost_optimizer_baseline_rows(db, workflow, node_id):
        if not _has_cost_optimizer_baseline_result(row):
            continue
        if row.get("baseline_id") == baseline_id:
            return row
        if row.get("source_workflow_node_run_id") == baseline_id:
            return row
    raise HTTPException(status_code=404, detail="resource.not_found")


def _public_cost_optimizer_baseline_row(row: dict[str, Any]) -> dict[str, Any]:
    public_row = copy.deepcopy(row)
    public_row.pop("downstream_snapshot", None)
    return public_row


def _deep_merge_dict(
    base: dict[str, Any],
    patch: dict[str, Any],
) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dict(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _patch_cost_optimizer_candidate_graph(
    graph: dict[str, Any],
    node_id: str,
    candidate: CostOptimizerCandidateRequest,
) -> dict[str, Any]:
    patched_graph = copy.deepcopy(graph)
    target_node = _find_workflow_node(patched_graph, node_id)
    if target_node is None:
        raise HTTPException(status_code=404, detail="resource.not_found")

    data = target_node.setdefault("data", {})
    if not isinstance(data, dict):
        data = {}
        target_node["data"] = data

    data["model_id"] = candidate.model_id
    data["fallback_model_id"] = candidate.fallback_model_id
    if candidate.auto_model_routing is not None:
        data["auto_model_routing"] = candidate.auto_model_routing
    if candidate.model_routing_policy is not None:
        current_policy = data.get("model_routing_policy")
        current_policy = current_policy if isinstance(current_policy, dict) else {}
        data["model_routing_policy"] = _deep_merge_dict(
            current_policy,
            candidate.model_routing_policy,
        )
    if candidate.task_type is not None:
        data["task_type"] = candidate.task_type
    if candidate.system_prompt is not None:
        data["system_prompt"] = candidate.system_prompt
    if candidate.user_prompt is not None:
        data["user_prompt"] = candidate.user_prompt
    if candidate.assistant_prompt is not None:
        data["assistant_prompt"] = candidate.assistant_prompt
    if candidate.referenced_variables is not None:
        data["referenced_variables"] = candidate.referenced_variables
    if candidate.parameters:
        current_parameters = (
            data.get("parameters") if isinstance(data.get("parameters"), dict) else {}
        )
        next_parameters = {**current_parameters}
        for key, value in candidate.parameters.items():
            if value is None:
                next_parameters.pop(key, None)
            else:
                next_parameters[key] = value
        data["parameters"] = next_parameters
    if candidate.output_format is not None:
        data["output_format"] = candidate.output_format

    knowledge = candidate.knowledge if isinstance(candidate.knowledge, dict) else {}
    if knowledge:
        if "knowledge_base_ids" in knowledge:
            data["knowledgeBases"] = [
                {"id": str(knowledge_base_id), "name": ""}
                for knowledge_base_id in knowledge.get("knowledge_base_ids") or []
            ]
        if "top_k" in knowledge:
            data["topK"] = knowledge.get("top_k")
        if "score_threshold" in knowledge:
            data["scoreThreshold"] = knowledge.get("score_threshold")
        if "dedupe_retrieved_context" in knowledge:
            data["dedupeRetrievedContext"] = knowledge.get("dedupe_retrieved_context")
        if "retrieved_context_max_chars" in knowledge:
            data["retrievedContextMaxChars"] = knowledge.get(
                "retrieved_context_max_chars"
            )
        if "retrieved_context_compression" in knowledge:
            data["retrievedContextCompression"] = knowledge.get(
                "retrieved_context_compression"
            )
        if "answer_grounding_check" in knowledge:
            data["answerGroundingCheck"] = knowledge.get("answer_grounding_check")

    return patched_graph


def _cost_optimizer_candidate_data_from_node_data(
    node_data: dict[str, Any],
) -> dict[str, Any]:
    parameters = (
        copy.deepcopy(node_data.get("parameters"))
        if isinstance(node_data.get("parameters"), dict)
        else {}
    )
    parameters.setdefault("max_tokens", 4096)
    parameters.setdefault("temperature", 0.7)

    knowledge_bases = node_data.get("knowledgeBases")
    knowledge_base_ids = []
    if isinstance(knowledge_bases, list):
        knowledge_base_ids = [
            str(item.get("id")).strip()
            for item in knowledge_bases
            if isinstance(item, dict)
            and isinstance(item.get("id"), str)
            and item.get("id").strip()
        ]

    output_format = node_data.get("output_format")
    if isinstance(output_format, str):
        output_format = {"type": output_format}
    elif not isinstance(output_format, dict):
        output_format = {"type": "text"}

    referenced_variables = node_data.get("referenced_variables")
    if not isinstance(referenced_variables, list):
        referenced_variables = []

    model_id = str(node_data.get("model_id") or "").strip()
    fallback_model_id = (
        str(node_data.get("fallback_model_id")).strip()
        if node_data.get("fallback_model_id") is not None
        else None
    )
    if bool(node_data.get("auto_model_routing")):
        routing_model_id = _active_model_routing_policy_model_id(node_data)
        if routing_model_id:
            model_id = model_id or routing_model_id
        routing_fallback_model_id = _active_model_routing_policy_fallback_model_id(
            node_data
        )
        if routing_fallback_model_id:
            fallback_model_id = fallback_model_id or routing_fallback_model_id
    if fallback_model_id == model_id:
        fallback_model_id = None

    return {
        "label": "추천 적용",
        "model_id": model_id,
        "fallback_model_id": fallback_model_id,
        "auto_model_routing": node_data.get("auto_model_routing")
        if isinstance(node_data.get("auto_model_routing"), bool)
        else None,
        "model_routing_policy": copy.deepcopy(node_data.get("model_routing_policy"))
        if isinstance(node_data.get("model_routing_policy"), dict)
        else None,
        "task_type": node_data.get("task_type") or "generate",
        "system_prompt": node_data.get("system_prompt") or None,
        "user_prompt": node_data.get("user_prompt") or None,
        "assistant_prompt": node_data.get("assistant_prompt") or None,
        "referenced_variables": copy.deepcopy(referenced_variables),
        "parameters": parameters,
        "output_format": copy.deepcopy(output_format),
        "knowledge": {
            "knowledge_base_ids": knowledge_base_ids,
            "top_k": (
                node_data.get("topK") if node_data.get("topK") is not None else 5
            ),
            "score_threshold": node_data.get("scoreThreshold")
            if node_data.get("scoreThreshold") is not None
            else 0.3,
            "dedupe_retrieved_context": bool(node_data.get("dedupeRetrievedContext")),
            "retrieved_context_max_chars": node_data.get("retrievedContextMaxChars"),
            "retrieved_context_compression": node_data.get(
                "retrievedContextCompression"
            )
            or "off",
            "answer_grounding_check": node_data.get("answerGroundingCheck") or "basic",
        },
    }


def _active_model_routing_policy_model_id(node_data: dict[str, Any]) -> str | None:
    policy = node_data.get("model_routing_policy")
    policy = policy if isinstance(policy, dict) else {}
    active_policy = policy.get("active_policy")
    active_policy = active_policy if isinstance(active_policy, dict) else {}
    default_model_id = active_policy.get("default_model_id")
    if isinstance(default_model_id, str) and default_model_id.strip():
        return default_model_id.strip()
    rules = active_policy.get("rules")
    for rule in rules if isinstance(rules, list) else []:
        if not isinstance(rule, dict):
            continue
        selected_model_id = rule.get("selected_model_id")
        if isinstance(selected_model_id, str) and selected_model_id.strip():
            return selected_model_id.strip()
    return None


def _active_model_routing_policy_fallback_model_id(
    node_data: dict[str, Any],
) -> str | None:
    policy = node_data.get("model_routing_policy")
    policy = policy if isinstance(policy, dict) else {}
    active_policy = policy.get("active_policy")
    active_policy = active_policy if isinstance(active_policy, dict) else {}
    fallback_model_id = active_policy.get("fallback_model_id")
    if isinstance(fallback_model_id, str) and fallback_model_id.strip():
        return fallback_model_id.strip()
    return None


def _apply_cost_optimizer_recommendation_patch(
    candidate_data: dict[str, Any],
    patch: dict[str, Any],
) -> dict[str, Any]:
    next_candidate = copy.deepcopy(candidate_data)
    parameters = patch.get("parameters") if isinstance(patch, dict) else None
    if isinstance(parameters, dict):
        current_parameters = next_candidate.setdefault("parameters", {})
        if not isinstance(current_parameters, dict):
            current_parameters = {}
            next_candidate["parameters"] = current_parameters
        for key, value in parameters.items():
            if value is None:
                current_parameters.pop(key, None)
            else:
                current_parameters[key] = value

    knowledge = patch.get("knowledge") if isinstance(patch, dict) else None
    if isinstance(knowledge, dict):
        current_knowledge = next_candidate.setdefault("knowledge", {})
        if not isinstance(current_knowledge, dict):
            current_knowledge = {}
            next_candidate["knowledge"] = current_knowledge
        for key, value in knowledge.items():
            current_knowledge[key] = value

    if "auto_model_routing" in patch:
        next_candidate["auto_model_routing"] = bool(patch.get("auto_model_routing"))

    model_routing_policy = (
        patch.get("model_routing_policy") if isinstance(patch, dict) else None
    )
    if isinstance(model_routing_policy, dict):
        current_policy = next_candidate.get("model_routing_policy")
        current_policy = current_policy if isinstance(current_policy, dict) else {}
        next_candidate["model_routing_policy"] = _deep_merge_dict(
            current_policy,
            model_routing_policy,
        )

    return next_candidate


def _cost_optimizer_candidate_from_recommendations(
    workflow: Workflow,
    node_id: str,
    recommendations_payload: dict[str, Any],
    recommendation_ids: list[str],
    *,
    allowed_apply_modes: set[str] | None = None,
) -> tuple[CostOptimizerCandidateRequest, list[str]]:
    selected_ids = [
        item for item in recommendation_ids if isinstance(item, str) and item
    ]
    if not selected_ids:
        raise HTTPException(
            status_code=400,
            detail="cost_optimizer.recommendation_required",
        )

    node = _ensure_cost_optimizer_llm_node(workflow, node_id)
    node_data = node.get("data") if isinstance(node.get("data"), dict) else {}
    candidate_data = _cost_optimizer_candidate_data_from_node_data(node_data)

    recommendations = recommendations_payload.get("recommendations")
    recommendations = recommendations if isinstance(recommendations, list) else []
    recommendation_by_id = {
        recommendation.get("parameter_key"): recommendation
        for recommendation in recommendations
        if isinstance(recommendation, dict)
        and isinstance(recommendation.get("parameter_key"), str)
    }

    applied_ids: list[str] = []
    for recommendation_id in selected_ids:
        recommendation = recommendation_by_id.get(recommendation_id)
        patch = (
            recommendation.get("candidate_patch")
            if isinstance(recommendation, dict)
            else None
        )
        if not isinstance(patch, dict):
            raise HTTPException(
                status_code=400,
                detail="cost_optimizer.recommendation_not_found",
            )
        apply_mode = recommendation.get("apply_mode")
        apply_mode = (
            apply_mode if isinstance(apply_mode, str) else "experiment_required"
        )
        if allowed_apply_modes is not None and apply_mode not in allowed_apply_modes:
            raise HTTPException(
                status_code=400,
                detail="cost_optimizer.recommendation_requires_experiment",
            )
        candidate_data = _apply_cost_optimizer_recommendation_patch(
            candidate_data,
            patch,
        )
        applied_ids.append(recommendation_id)

    return CostOptimizerCandidateRequest(**candidate_data), applied_ids


def _cost_optimizer_baseline_input_variables(
    baseline_input: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        {
            "id": key,
            "name": key,
            "label": key,
            "type": "text",
            "required": False,
        }
        for key in baseline_input
        if isinstance(key, str) and key
    ]


def _cost_optimizer_retarget_referenced_variables(
    value: Any,
) -> Any:
    if isinstance(value, list):
        return [_cost_optimizer_retarget_referenced_variables(item) for item in value]
    if isinstance(value, dict):
        updated = {
            key: _cost_optimizer_retarget_referenced_variables(item)
            for key, item in value.items()
        }
        selector = updated.get("value_selector")
        if isinstance(selector, list) and selector:
            updated["value_selector"] = [
                COST_OPTIMIZER_BASELINE_INPUT_NODE_ID,
                *selector,
            ]
        return updated
    return value


def _build_cost_optimizer_candidate_execution_graph(
    graph: dict[str, Any],
    node_id: str,
    candidate: CostOptimizerCandidateRequest,
    baseline_input: dict[str, Any],
) -> dict[str, Any]:
    patched_graph = _patch_cost_optimizer_candidate_graph(graph, node_id, candidate)
    target_node = _find_workflow_node(patched_graph, node_id)
    if target_node is None:
        raise HTTPException(status_code=404, detail="resource.not_found")

    target_node = copy.deepcopy(target_node)
    data = target_node.get("data") if isinstance(target_node.get("data"), dict) else {}
    data = copy.deepcopy(data)
    data["referenced_variables"] = _cost_optimizer_retarget_referenced_variables(
        data.get("referenced_variables") or []
    )
    target_node["data"] = data

    input_node = {
        "id": COST_OPTIMIZER_BASELINE_INPUT_NODE_ID,
        "type": "startNode",
        "position": {"x": 0, "y": 0},
        "data": {
            "title": "Cost Optimizer Baseline Input",
            "trigger_type": "manual",
            "variables": _cost_optimizer_baseline_input_variables(baseline_input),
        },
    }

    return {
        "nodes": [input_node, target_node],
        "edges": [
            {
                "id": f"cost-optimizer-input-to-{node_id}",
                "source": COST_OPTIMIZER_BASELINE_INPUT_NODE_ID,
                "target": node_id,
            }
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }


def _extract_cost_optimizer_node_output(
    task_result: dict[str, Any],
    node_id: str,
) -> dict[str, Any]:
    result = task_result.get("result") if isinstance(task_result, dict) else {}
    if isinstance(result, dict) and isinstance(result.get(node_id), dict):
        return result[node_id]
    return result if isinstance(result, dict) else {}


def _cost_optimizer_usage_number(
    usage: dict[str, Any] | None,
    *keys: str,
) -> float | None:
    if not isinstance(usage, dict):
        return None
    for key in keys:
        value = usage.get(key)
        if isinstance(value, (int, float, Decimal)):
            return float(value)
    return None


def _cost_optimizer_total_tokens(usage: dict[str, Any] | None) -> int | None:
    total = _cost_optimizer_usage_number(usage, "total_tokens", "totalTokens")
    if total is not None:
        return int(total)
    prompt = _cost_optimizer_usage_number(usage, "prompt_tokens", "promptTokens")
    completion = _cost_optimizer_usage_number(
        usage,
        "completion_tokens",
        "completionTokens",
    )
    if prompt is None and completion is None:
        return None
    return int(prompt or 0) + int(completion or 0)


def _normalize_cost_optimizer_candidate_usage(
    usage: dict[str, Any] | None,
    output: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(usage, dict):
        return {}

    normalized = dict(usage)
    cost = _cost_optimizer_usage_number(normalized, "cost", "total_cost")
    if cost is None and isinstance(output, dict):
        cost = _cost_optimizer_usage_number(output, "cost", "total_cost")
    if cost is None:
        normalized["cost"] = None
        normalized["cost_unavailable"] = True
    else:
        normalized["cost"] = cost
        normalized["cost_unavailable"] = False
    return normalized


def _build_cost_optimizer_candidate_trace(output: dict[str, Any]) -> dict[str, Any]:
    safe_output = _safe_cost_optimizer_value(output)
    metadata = safe_output.get("metadata") if isinstance(safe_output, dict) else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    rag_summary = metadata.get("rag_summary") or metadata.get("retrieval_summary")
    rag_summary = rag_summary if isinstance(rag_summary, dict) else None
    rag_summary = _safe_cost_optimizer_rag_summary(rag_summary)
    rag_summary = rag_summary if isinstance(rag_summary, dict) else None
    model_routing = metadata.get("model_routing") or metadata.get(
        "model_routing_metadata"
    )
    model_routing = (
        _safe_cost_optimizer_value(model_routing)
        if isinstance(model_routing, dict)
        else None
    )
    return {
        "input_preview": "",
        "output_preview": _preview_baseline_payload(
            safe_output.get("text", safe_output)
        ),
        "messages_preview": [],
        "rag_summary": rag_summary,
        "model_routing": model_routing,
        "error_message": None,
    }


def _cost_optimizer_json_value_type_matches(value: Any, expected_type: str) -> bool:
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    return True


def _cost_optimizer_output_payload_for_schema(output: dict[str, Any]) -> Any:
    payload = (
        output.get("text") if isinstance(output, dict) and "text" in output else output
    )
    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return None
    return payload


def _validate_cost_optimizer_candidate_output_schema(
    output: dict[str, Any],
    candidate: CostOptimizerCandidateRequest,
) -> dict[str, Any]:
    output_format = (
        candidate.output_format if isinstance(candidate.output_format, dict) else {}
    )
    if output_format.get("type") != "json":
        return {"status": "skipped", "errors": []}

    schema = output_format.get("schema")
    if not isinstance(schema, dict) or not schema:
        return {"status": "valid", "errors": []}

    payload = _cost_optimizer_output_payload_for_schema(output)
    if payload is None:
        return {
            "status": "schema_failed",
            "errors": ["output must be valid JSON"],
        }

    errors: list[str] = []
    expected_type = schema.get("type")
    if expected_type and not _cost_optimizer_json_value_type_matches(
        payload,
        expected_type,
    ):
        errors.append(f"output must be {expected_type}")

    if isinstance(payload, dict):
        required = (
            schema.get("required") if isinstance(schema.get("required"), list) else []
        )
        for field in required:
            if isinstance(field, str) and field not in payload:
                errors.append(f"{field} is required")

        properties = schema.get("properties")
        if isinstance(properties, dict):
            for field, field_schema in properties.items():
                if field not in payload or not isinstance(field_schema, dict):
                    continue
                field_type = field_schema.get("type")
                if isinstance(
                    field_type, str
                ) and not _cost_optimizer_json_value_type_matches(
                    payload[field],
                    field_type,
                ):
                    errors.append(f"{field} must be {field_type}")

    if errors:
        return {"status": "schema_failed", "errors": errors}
    return {"status": "valid", "errors": []}


def _build_cost_optimizer_diff(
    baseline: dict[str, Any],
    candidate_result: dict[str, Any],
) -> dict[str, Any]:
    baseline_usage = baseline.get("usage") if isinstance(baseline, dict) else {}
    candidate_usage = (
        candidate_result.get("usage") if isinstance(candidate_result, dict) else {}
    )
    baseline_usage = baseline_usage if isinstance(baseline_usage, dict) else {}
    candidate_usage = candidate_usage if isinstance(candidate_usage, dict) else {}

    baseline_cost = _cost_optimizer_usage_number(baseline_usage, "cost", "total_cost")
    candidate_cost = _cost_optimizer_usage_number(candidate_usage, "cost", "total_cost")
    baseline_tokens = _cost_optimizer_total_tokens(baseline_usage)
    candidate_tokens = _cost_optimizer_total_tokens(candidate_usage)
    baseline_latency = _cost_optimizer_usage_number(baseline_usage, "latency_ms")
    candidate_latency = _cost_optimizer_usage_number(
        candidate_usage,
        "latency_ms",
    )
    if candidate_latency is None:
        candidate_latency = _cost_optimizer_usage_number(
            candidate_result,
            "latency_ms",
        )

    diff: dict[str, Any] = {}
    if baseline_cost is not None and candidate_cost is not None:
        cost_delta = candidate_cost - baseline_cost
        diff["cost_delta"] = round(cost_delta, 10)
        diff["cost_delta_percent"] = (
            round((cost_delta / baseline_cost) * 100, 2) if baseline_cost else None
        )
    if baseline_tokens is not None and candidate_tokens is not None:
        diff["token_delta"] = candidate_tokens - baseline_tokens
    if baseline_latency is not None and candidate_latency is not None:
        diff["latency_delta_ms"] = int(candidate_latency - baseline_latency)
    return diff


def _resolve_cost_optimizer_downstream_compatibility(
    baseline: dict[str, Any],
    workflow: Workflow,
    node_id: str,
    candidate_output: dict[str, Any] | None = None,
) -> dict[str, Any]:
    baseline_snapshot = baseline.get("downstream_snapshot")
    if isinstance(baseline_snapshot, dict):
        return build_cost_optimizer_downstream_compatibility(
            baseline_snapshot,
            workflow.graph,
            node_id,
            candidate_output=candidate_output,
        )
    existing = baseline.get("downstream_compatibility")
    if isinstance(existing, dict):
        return existing
    return {
        "state": "unknown",
        "label": "판정 전",
        "message": "downstream compatibility is not evaluated yet",
    }


def _uuid_or_none(value: Any) -> UUID | None:
    if value is None:
        return None
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError):
        return None


def _cost_optimizer_schema_status(
    schema_validation: dict[str, Any] | None,
) -> str:
    if not isinstance(schema_validation, dict):
        return "not_checked"
    status = schema_validation.get("status")
    if status == "schema_failed":
        return "failed"
    if status in {"valid", "pass"}:
        return "pass"
    return "not_checked"


def _cost_optimizer_routing_evidence_summary(
    *,
    trace: Any,
    node_options: Any,
) -> dict[str, Any]:
    trace = trace if isinstance(trace, dict) else {}
    routing = trace.get("model_routing")
    routing = routing if isinstance(routing, dict) else {}
    node_options = node_options if isinstance(node_options, dict) else {}
    output_format = node_options.get("output_format")
    output_format = output_format if isinstance(output_format, dict) else {}

    return {
        "strategy_id": str(routing.get("strategy_id") or "") or None,
        "runtime_context": (
            routing.get("runtime_context")
            if isinstance(routing.get("runtime_context"), dict)
            else {}
        ),
        "schema_required": (
            str(output_format.get("type") or "").lower() == "json"
            and isinstance(output_format.get("schema"), dict)
            and bool(output_format["schema"])
        ),
    }


def _create_cost_optimizer_comparison(
    *,
    db: Session,
    workflow: Workflow,
    node_id: str,
    baseline: dict[str, Any],
    candidate: CostOptimizerCandidateRequest,
    current_user: User,
) -> tuple[CostOptimizerExperiment, CostOptimizerCandidate]:
    candidate_settings = _safe_cost_optimizer_candidate_settings(candidate)
    baseline_node_fingerprint = str(
        baseline.get("node_config_fingerprint") or ""
    ).strip()
    if baseline_node_fingerprint:
        candidate_settings["_baseline_node_config_fingerprint"] = (
            baseline_node_fingerprint
        )
    baseline_usage_summary = _cost_optimizer_baseline_usage_summary(baseline)
    created_at = datetime.now(timezone.utc)
    retention_expires_at = CostOptimizerRetentionService.expires_at(
        db,
        app_id=getattr(workflow, "app_id", None),
        organization_id=getattr(workflow, "organization_id", None),
        created_at=created_at,
    )
    experiment = CostOptimizerExperiment(
        id=uuid4(),
        organization_id=getattr(workflow, "organization_id", None),
        workflow_id=workflow.id,
        app_id=getattr(workflow, "app_id", None),
        node_id=node_id,
        baseline_node_run_id=_uuid_or_none(
            baseline.get("source_workflow_node_run_id") or baseline.get("baseline_id")
        ),
        baseline_workflow_run_id=_uuid_or_none(baseline.get("workflow_run_id")),
        baseline_node_options=baseline.get("node_options") or {},
        baseline_usage_summary=baseline_usage_summary,
        baseline_trace_summary=baseline.get("trace") or {},
        baseline_downstream_snapshot=baseline.get("downstream_snapshot") or {},
        usage_summary={},
        status="running",
        created_by=current_user.id,
        created_at=created_at,
        updated_at=created_at,
        retention_expires_at=retention_expires_at,
    )
    candidate_row = CostOptimizerCandidate(
        id=uuid4(),
        experiment_id=experiment.id,
        name=candidate.label,
        model_id=candidate.model_id,
        fallback_model_id=candidate.fallback_model_id,
        task_type=candidate.task_type,
        candidate_settings=candidate_settings,
        usage_summary={},
        schema_validation={"status": "skipped", "errors": []},
        downstream_compatibility={},
        diff_summary={
            "routing_evidence": _cost_optimizer_routing_evidence_summary(
                trace=baseline.get("trace"),
                node_options=baseline.get("node_options"),
            )
        },
        status="running",
        created_at=created_at,
        updated_at=created_at,
    )
    db.add(experiment)
    db.add(candidate_row)
    db.commit()
    return experiment, candidate_row


def _link_cost_optimizer_candidate_run(
    db: Session,
    *,
    experiment: CostOptimizerExperiment,
    candidate_row: CostOptimizerCandidate,
    workflow_run_id: UUID | None,
) -> None:
    if workflow_run_id is None:
        return

    candidate_row.candidate_workflow_run_id = workflow_run_id
    candidate_node_run = (
        db.query(WorkflowNodeRun)
        .filter(
            WorkflowNodeRun.workflow_run_id == workflow_run_id,
            WorkflowNodeRun.node_id == experiment.node_id,
        )
        .order_by(WorkflowNodeRun.started_at.desc())
        .first()
    )
    if candidate_node_run:
        candidate_row.candidate_node_run_id = candidate_node_run.id

    (
        db.query(LLMUsageLog)
        .filter(
            LLMUsageLog.workflow_run_id == workflow_run_id,
            LLMUsageLog.node_id == experiment.node_id,
            LLMUsageLog.cost_optimizer_candidate_id.is_(None),
        )
        .update(
            {LLMUsageLog.cost_optimizer_candidate_id: candidate_row.id},
            synchronize_session=False,
        )
    )


def _persist_cost_optimizer_comparison(
    *,
    db: Session,
    experiment: CostOptimizerExperiment,
    candidate_row: CostOptimizerCandidate,
    candidate: CostOptimizerCandidateRequest,
    candidate_result: dict[str, Any],
    diff: dict[str, Any],
    downstream_compatibility: dict[str, Any],
    quality_evaluation: dict[str, Any] | None = None,
) -> CostOptimizerExperiment:
    usage = candidate_result.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    trace = candidate_result.get("trace")
    trace = trace if isinstance(trace, dict) else {}
    retrieval_summary = trace.get("rag_summary")
    schema_validation = candidate_result.get("schema_validation")
    schema_validation = schema_validation if isinstance(schema_validation, dict) else {}
    total_cost = _cost_optimizer_usage_number(usage, "cost", "total_cost")
    latency_ms = _cost_optimizer_usage_number(usage, "latency_ms")
    if latency_ms is None:
        latency_ms = candidate_result.get("latency_ms")
    downstream_state = downstream_compatibility.get("state")
    status = str(candidate_result.get("status") or "failed")
    candidate_workflow_run_id = _uuid_or_none(
        candidate_result.get("candidate_workflow_run_id")
    )

    quality_evaluation = (
        quality_evaluation if isinstance(quality_evaluation, dict) else {}
    )
    quality_judge = quality_evaluation.get("judge")
    quality_judge = quality_judge if isinstance(quality_judge, dict) else {}
    experiment.usage_summary = {
        **usage,
        "quality_judge": quality_judge,
        "quality_judge_cost": quality_evaluation.get("judge_cost"),
    }
    experiment.status = "failed" if status == "failed" else "completed"
    candidate_row.name = candidate.label
    candidate_row.model_id = candidate.model_id
    candidate_row.fallback_model_id = candidate.fallback_model_id
    candidate_row.task_type = candidate.task_type
    baseline_node_fingerprint = str(
        (candidate_row.candidate_settings or {}).get(
            "_baseline_node_config_fingerprint"
        )
        or ""
    ).strip()
    candidate_settings = _safe_cost_optimizer_candidate_settings(candidate)
    if baseline_node_fingerprint:
        candidate_settings["_baseline_node_config_fingerprint"] = (
            baseline_node_fingerprint
        )
    candidate_row.candidate_settings = candidate_settings
    candidate_row.total_cost = (
        Decimal(str(total_cost)) if total_cost is not None else None
    )
    candidate_row.total_tokens = _cost_optimizer_total_tokens(usage)
    candidate_row.latency_ms = (
        int(latency_ms) if isinstance(latency_ms, (int, float)) else None
    )
    candidate_row.schema_status = _cost_optimizer_schema_status(schema_validation)
    candidate_row.downstream_state = str(downstream_state) if downstream_state else None
    candidate_row.usage_summary = {
        **usage,
        "quality_judge": quality_judge,
        "quality_judge_cost": quality_evaluation.get("judge_cost"),
    }
    candidate_row.schema_validation = schema_validation
    candidate_row.retrieval_summary = (
        retrieval_summary if isinstance(retrieval_summary, dict) else None
    )
    _link_cost_optimizer_candidate_run(
        db,
        experiment=experiment,
        candidate_row=candidate_row,
        workflow_run_id=candidate_workflow_run_id,
    )
    candidate_row.downstream_compatibility = downstream_compatibility
    candidate_row.diff_summary = {
        **diff,
        "quality_evaluation": quality_evaluation,
        "routing_evidence": _cost_optimizer_routing_evidence_summary(
            trace=experiment.baseline_trace_summary,
            node_options=experiment.baseline_node_options,
        ),
    }
    candidate_row.status = status
    db.commit()
    return experiment


def _cost_optimizer_candidate_settings_match(
    stored_settings: Any,
    request_settings: dict[str, Any],
) -> bool:
    if not isinstance(stored_settings, dict):
        return False
    fingerprint = stored_settings.get("_settings_fingerprint")
    if isinstance(fingerprint, str):
        return fingerprint == _cost_optimizer_candidate_settings_fingerprint(
            request_settings
        )
    return stored_settings == request_settings


def _ensure_cost_optimizer_candidate_apply_allowed(
    db: Session,
    workflow: Workflow,
    node_id: str,
    comparison_id: str | None,
    candidate_settings: CostOptimizerCandidateRequest,
    acknowledge_downstream_warning: bool,
) -> tuple[Any | None, list[Any]]:
    experiment_id = _uuid_or_none(comparison_id)
    if experiment_id is None:
        raise HTTPException(
            status_code=400,
            detail="cost_optimizer.candidate_not_found",
        )

    experiment = (
        db.query(CostOptimizerExperiment)
        .options(selectinload(CostOptimizerExperiment.candidates))
        .filter(
            CostOptimizerExperiment.id == experiment_id,
            CostOptimizerExperiment.workflow_id == workflow.id,
            CostOptimizerExperiment.node_id == node_id,
            CostOptimizerExperiment.organization_id == workflow.organization_id,
        )
        .first()
    )
    rows = list(getattr(experiment, "candidates", []) or []) if experiment else []
    if not rows:
        raise HTTPException(
            status_code=400,
            detail="cost_optimizer.candidate_not_found",
        )

    requested = candidate_settings.model_dump(mode="json")
    for row in rows:
        is_target_candidate = _cost_optimizer_candidate_settings_match(
            getattr(row, "candidate_settings", None),
            requested,
        )
        if not is_target_candidate:
            continue

        status = getattr(row, "status", None)
        schema_status = getattr(row, "schema_status", None)
        if status == "schema_failed" or schema_status == "failed":
            raise HTTPException(
                status_code=400,
                detail="cost_optimizer.schema_failed_candidate",
            )

        downstream_compatibility = getattr(row, "downstream_compatibility", None)
        downstream_state = (
            downstream_compatibility.get("state")
            if isinstance(downstream_compatibility, dict)
            else None
        )
        if (
            downstream_state in {"warning", "incompatible"}
            and not acknowledge_downstream_warning
        ):
            raise HTTPException(
                status_code=400,
                detail="cost_optimizer.downstream_ack_required",
            )
        return row, rows

    raise HTTPException(
        status_code=400,
        detail="cost_optimizer.candidate_not_found",
    )


def _cost_optimizer_datetime(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _cost_optimizer_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _cost_optimizer_summary_value(
    summary: dict[str, Any],
    *keys: str,
) -> Any:
    for key in keys:
        value = summary.get(key)
        if value is not None:
            return value
    return None


def _cost_optimizer_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _cost_optimizer_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _cost_optimizer_baseline_usage_summary(baseline: dict[str, Any]) -> dict[str, Any]:
    usage_summary = dict(_cost_optimizer_dict(baseline.get("usage")))
    for source_key, summary_key in (
        ("model", "model"),
        ("cost", "cost"),
        ("total_tokens", "total_tokens"),
        ("latency_ms", "latency_ms"),
    ):
        if (
            usage_summary.get(summary_key) is None
            and baseline.get(source_key) is not None
        ):
            usage_summary[summary_key] = baseline.get(source_key)
    return usage_summary


def _cost_optimizer_summary_missing_any(
    summary: dict[str, Any],
    key_groups: tuple[tuple[str, ...], ...],
) -> bool:
    return any(
        _cost_optimizer_summary_value(summary, *keys) is None for keys in key_groups
    )


def _cost_optimizer_node_run_output_summary(
    node_run: WorkflowNodeRun,
) -> dict[str, Any]:
    has_trace_output, trace_output_available, trace_output = _trace_payload_value(
        node_run,
        "output",
    )
    output_payload = trace_output if has_trace_output else node_run.outputs
    output_available = (
        trace_output_available if has_trace_output else node_run.outputs is not None
    )
    if not output_available:
        return {"output_available": False}

    return {
        "output_available": True,
        "output": _redact_baseline_value(output_payload),
        "output_preview": _preview_baseline_payload(output_payload),
    }


def _cost_optimizer_node_run_output_summary_from_source(
    db: Session,
    *,
    node_run_id: Any = None,
    workflow_run_id: Any = None,
    node_id: str | None = None,
) -> dict[str, Any]:
    if node_run_id is None and (workflow_run_id is None or node_id is None):
        return {}

    query = db.query(WorkflowNodeRun)
    if node_run_id is not None:
        query = query.filter(WorkflowNodeRun.id == node_run_id)
    else:
        query = query.filter(
            WorkflowNodeRun.workflow_run_id == workflow_run_id,
            WorkflowNodeRun.node_id == node_id,
        ).order_by(WorkflowNodeRun.started_at.desc())

    if not hasattr(query, "first"):
        return {}
    node_run = query.first()
    if node_run is None:
        return {}
    return _cost_optimizer_node_run_output_summary(node_run)


def _cost_optimizer_candidate_summary(
    db: Session,
    row: Any,
    node_id: str,
) -> dict[str, Any]:
    usage_summary = _cost_optimizer_dict(getattr(row, "usage_summary", None))
    prompt_tokens = _cost_optimizer_usage_number(
        usage_summary,
        "prompt_tokens",
        "promptTokens",
    )
    completion_tokens = _cost_optimizer_usage_number(
        usage_summary,
        "completion_tokens",
        "completionTokens",
    )
    summary = {
        "candidate_id": str(row.id),
        "name": getattr(row, "name", None),
        "status": getattr(row, "status", None),
        "model_id": getattr(row, "model_id", None),
        "fallback_model_id": getattr(row, "fallback_model_id", None),
        "task_type": getattr(row, "task_type", None),
        "total_cost": _cost_optimizer_float(getattr(row, "total_cost", None)),
        "total_tokens": getattr(row, "total_tokens", None),
        "latency_ms": getattr(row, "latency_ms", None),
        "schema_status": getattr(row, "schema_status", None),
        "downstream_state": getattr(row, "downstream_state", None),
        "is_applied": bool(getattr(row, "is_applied", False)),
        "created_at": _cost_optimizer_datetime(getattr(row, "created_at", None)),
    }
    if prompt_tokens is not None:
        summary["prompt_tokens"] = int(prompt_tokens)
    if completion_tokens is not None:
        summary["completion_tokens"] = int(completion_tokens)
    diff_summary = _cost_optimizer_dict(getattr(row, "diff_summary", None))
    quality_evaluation = _cost_optimizer_quality_evaluation_summary(
        diff_summary.get("quality_evaluation")
    )
    if quality_evaluation:
        summary["quality_evaluation"] = quality_evaluation
    output_summary = _cost_optimizer_node_run_output_summary_from_source(
        db,
        node_run_id=getattr(row, "candidate_node_run_id", None),
        workflow_run_id=getattr(row, "candidate_workflow_run_id", None),
        node_id=node_id,
    )
    if output_summary.get("output_available"):
        summary.update(output_summary)
    return summary


def _cost_optimizer_quality_evaluation_summary(value: Any) -> dict[str, Any]:
    """실험 이력에 필요한 품질 평가 safe summary만 반환한다."""
    evaluation = _cost_optimizer_dict(value)
    if not evaluation:
        return {}

    summary = {
        key: evaluation.get(key)
        for key in (
            "status",
            "delta",
            "confidence",
            "confidence_score",
            "safe_summary",
            "judge_cost",
        )
        if key in evaluation
    }
    for variant in ("baseline", "candidate"):
        variant_summary = _cost_optimizer_dict(evaluation.get(variant))
        if "score" in variant_summary:
            summary[variant] = {"score": variant_summary.get("score")}

    dimensions = _cost_optimizer_dict(evaluation.get("dimensions"))
    if "dimensions" in evaluation:
        safe_dimensions: dict[str, Any] = {}
        for name, raw_dimension in dimensions.items():
            dimension = _cost_optimizer_dict(raw_dimension)
            if not dimension:
                continue
            safe_dimensions[str(name)] = {
                key: dimension.get(key)
                for key in ("baseline", "candidate", "delta")
                if key in dimension
            }
        summary["dimensions"] = safe_dimensions
    return summary


def _cost_optimizer_source_baseline_usage_summary(
    db: Session,
    row: Any,
) -> dict[str, Any]:
    baseline_node_run_id = getattr(row, "baseline_node_run_id", None)
    if baseline_node_run_id is None:
        return {}

    node_run_query = db.query(WorkflowNodeRun).filter(
        WorkflowNodeRun.id == baseline_node_run_id
    )
    if not hasattr(node_run_query, "first"):
        return {}
    node_run = node_run_query.first()
    if node_run is None:
        return {}

    usage_query = (
        db.query(LLMUsageLog)
        .filter(
            LLMUsageLog.workflow_run_id == node_run.workflow_run_id,
            LLMUsageLog.node_id == node_run.node_id,
            LLMUsageLog.cost_optimizer_candidate_id.is_(None),
        )
        .order_by(LLMUsageLog.created_at.desc())
    )
    if not hasattr(usage_query, "first"):
        return {}
    usage = usage_query.first()
    if usage is None:
        return {}

    prompt_tokens = int(usage.prompt_tokens or 0)
    completion_tokens = int(usage.completion_tokens or 0)
    return {
        "model": _usage_model_name(usage),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "cost": _decimal_to_float(usage.total_cost),
        "latency_ms": _cost_optimizer_baseline_latency_ms(node_run, usage),
    }


def _cost_optimizer_baseline_summary(db: Session, row: Any) -> dict[str, Any]:
    usage_summary = _cost_optimizer_dict(getattr(row, "baseline_usage_summary", None))
    fallback_key_groups = (
        ("model", "model_id"),
        ("cost", "total_cost"),
        ("prompt_tokens", "promptTokens"),
        ("completion_tokens", "completionTokens"),
        ("total_tokens", "totalTokens"),
        ("latency_ms", "latencyMs"),
    )
    source_usage_summary = (
        _cost_optimizer_source_baseline_usage_summary(db, row)
        if _cost_optimizer_summary_missing_any(usage_summary, fallback_key_groups)
        else {}
    )
    node_options = _cost_optimizer_dict(getattr(row, "baseline_node_options", None))
    parameters = (
        node_options.get("parameters")
        if isinstance(node_options.get("parameters"), dict)
        else {}
    )

    def summary_value(*keys: str) -> Any:
        primary = _cost_optimizer_summary_value(usage_summary, *keys)
        if primary is not None:
            return primary
        return _cost_optimizer_summary_value(source_usage_summary, *keys)

    model = summary_value("model", "model_id") or node_options.get("model_id")
    prompt_tokens = summary_value("prompt_tokens", "promptTokens")
    completion_tokens = summary_value("completion_tokens", "completionTokens")
    total_tokens = summary_value("total_tokens", "totalTokens")
    cost = summary_value("total_cost", "cost")
    latency_ms = summary_value("latency_ms", "latencyMs")

    summary = {
        "baseline_id": str(row.baseline_node_run_id)
        if getattr(row, "baseline_node_run_id", None)
        else None,
        "workflow_run_id": str(row.baseline_workflow_run_id)
        if getattr(row, "baseline_workflow_run_id", None)
        else None,
        "model": model,
        "cost": _cost_optimizer_float(cost),
        "prompt_tokens": _cost_optimizer_int(prompt_tokens),
        "completion_tokens": _cost_optimizer_int(completion_tokens),
        "total_tokens": _cost_optimizer_int(total_tokens),
        "latency_ms": latency_ms,
        "output_format": node_options.get("output_format"),
        "max_tokens": parameters.get("max_tokens"),
        "temperature": parameters.get("temperature"),
    }
    output_summary = _cost_optimizer_node_run_output_summary_from_source(
        db,
        node_run_id=getattr(row, "baseline_node_run_id", None),
        workflow_run_id=getattr(row, "baseline_workflow_run_id", None),
        node_id=getattr(row, "node_id", None),
    )
    if output_summary.get("output_available"):
        summary.update(output_summary)
    return summary


def _cost_optimizer_experiment_summary(
    db: Session,
    row: Any,
    *,
    candidates: list[Any] | None = None,
) -> dict[str, Any]:
    summary_candidates = (
        candidates
        if candidates is not None
        else list(getattr(row, "candidates", None) or [])
    )
    return {
        "experiment_id": str(row.id),
        "workflow_id": str(row.workflow_id),
        "app_id": str(row.app_id) if getattr(row, "app_id", None) else None,
        "node_id": row.node_id,
        "baseline_node_run_id": str(row.baseline_node_run_id)
        if getattr(row, "baseline_node_run_id", None)
        else None,
        "baseline_workflow_run_id": str(row.baseline_workflow_run_id)
        if getattr(row, "baseline_workflow_run_id", None)
        else None,
        "status": getattr(row, "status", None),
        "created_by": str(row.created_by) if getattr(row, "created_by", None) else None,
        "created_at": _cost_optimizer_datetime(getattr(row, "created_at", None)),
        "baseline_summary": _cost_optimizer_baseline_summary(db, row),
        "usage_summary": getattr(row, "usage_summary", None) or {},
        "candidates": [
            _cost_optimizer_candidate_summary(db, candidate, row.node_id)
            for candidate in summary_candidates
        ],
    }


def _cost_optimizer_candidate_matches_filters(
    candidate: Any,
    *,
    candidate_status: str | None,
    model: str | None,
    is_applied: bool | None,
    schema_status: str | None,
    downstream_state: str | None,
) -> bool:
    """목록 query에 적용한 후보 조건을 experiment 내부 후보에도 동일하게 적용한다."""
    return all(
        (
            candidate_status is None
            or getattr(candidate, "status", None) == candidate_status,
            model is None or getattr(candidate, "model_id", None) == model,
            is_applied is None
            or bool(getattr(candidate, "is_applied", False)) == is_applied,
            schema_status is None
            or getattr(candidate, "schema_status", None) == schema_status,
            downstream_state is None
            or getattr(candidate, "downstream_state", None) == downstream_state,
        )
    )


def list_cost_optimizer_experiments(
    db: Session,
    workflow: Workflow,
    node_id: str,
    *,
    baseline_id: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    created_by: str | None = None,
    candidate_status: str | None = None,
    model: str | None = None,
    is_applied: bool | None = None,
    schema_status: str | None = None,
    downstream_state: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    query = (
        db.query(CostOptimizerExperiment)
        .options(selectinload(CostOptimizerExperiment.candidates))
        .filter(
            CostOptimizerExperiment.workflow_id == workflow.id,
            CostOptimizerExperiment.node_id == node_id,
        )
    )
    if baseline_id:
        baseline_uuid = _uuid_or_none(baseline_id)
        if baseline_uuid is None:
            raise HTTPException(
                status_code=400,
                detail="cost_optimizer.invalid_filter",
            )
        query = query.filter(
            CostOptimizerExperiment.baseline_node_run_id == baseline_uuid
        )
    if date_from:
        query = query.filter(CostOptimizerExperiment.created_at >= date_from)
    if date_to:
        query = query.filter(CostOptimizerExperiment.created_at <= date_to)
    if created_by:
        created_by_uuid = _uuid_or_none(created_by)
        if created_by_uuid is None:
            raise HTTPException(
                status_code=400,
                detail="cost_optimizer.invalid_filter",
            )
        query = query.filter(CostOptimizerExperiment.created_by == created_by_uuid)

    candidate_filters = [
        candidate_status,
        model,
        is_applied is not None,
        schema_status,
        downstream_state,
    ]
    has_candidate_filters = any(candidate_filters)
    if has_candidate_filters:
        query = query.join(CostOptimizerCandidate).distinct()
        if candidate_status:
            query = query.filter(CostOptimizerCandidate.status == candidate_status)
        if model:
            query = query.filter(CostOptimizerCandidate.model_id == model)
        if is_applied is not None:
            query = query.filter(CostOptimizerCandidate.is_applied == is_applied)
        if schema_status:
            query = query.filter(CostOptimizerCandidate.schema_status == schema_status)
        if downstream_state:
            query = query.filter(
                CostOptimizerCandidate.downstream_state == downstream_state
            )

    total = query.count()
    rows = (
        query.order_by(
            CostOptimizerExperiment.created_at.desc(),
            CostOptimizerExperiment.id.desc(),
        )
        .offset(offset)
        .limit(limit)
        .all()
    )
    items = []
    for row in rows:
        matching_candidates = None
        if has_candidate_filters:
            matching_candidates = [
                candidate
                for candidate in list(getattr(row, "candidates", None) or [])
                if _cost_optimizer_candidate_matches_filters(
                    candidate,
                    candidate_status=candidate_status,
                    model=model,
                    is_applied=is_applied,
                    schema_status=schema_status,
                    downstream_state=downstream_state,
                )
            ]
        items.append(
            _cost_optimizer_experiment_summary(
                db,
                row,
                candidates=matching_candidates,
            )
        )

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": items,
    }


def get_cost_optimizer_experiment_candidate(
    db: Session,
    workflow: Workflow,
    node_id: str,
    experiment_id: str,
    candidate_id: str,
) -> dict[str, Any]:
    """목록 pagination과 무관하게 실험 후보 한 건의 safe summary를 반환한다."""
    experiment_uuid = _uuid_or_none(experiment_id)
    candidate_uuid = _uuid_or_none(candidate_id)
    if experiment_uuid is None or candidate_uuid is None:
        raise HTTPException(status_code=404, detail="resource.not_found")

    experiment = (
        db.query(CostOptimizerExperiment)
        .options(selectinload(CostOptimizerExperiment.candidates))
        .filter(
            CostOptimizerExperiment.id == experiment_uuid,
            CostOptimizerExperiment.workflow_id == workflow.id,
            CostOptimizerExperiment.node_id == node_id,
            CostOptimizerExperiment.organization_id == workflow.organization_id,
        )
        .first()
    )
    if experiment is None:
        raise HTTPException(status_code=404, detail="resource.not_found")

    candidate = next(
        (
            row
            for row in list(getattr(experiment, "candidates", None) or [])
            if getattr(row, "id", None) == candidate_uuid
        ),
        None,
    )
    if candidate is None:
        raise HTTPException(status_code=404, detail="resource.not_found")

    experiment_summary = _cost_optimizer_experiment_summary(db, experiment)
    detail = {
        key: value for key, value in experiment_summary.items() if key != "candidates"
    }
    detail["candidate"] = _cost_optimizer_candidate_summary(
        db,
        candidate,
        node_id,
    )
    return detail


def _cost_optimizer_candidate_execution_context(
    *,
    workflow: Workflow,
    node_id: str,
    baseline: dict[str, Any],
    candidate: CostOptimizerCandidateRequest,
    candidate_id: UUID,
    workflow_run_id: UUID,
    current_user: User,
    request: Request,
) -> dict[str, Any]:
    return {
        "user_id": str(current_user.id),
        "execution_subject": {
            "type": "user",
            "id": str(current_user.id),
        },
        "workflow_id": str(workflow.id),
        "workflow_run_id": str(workflow_run_id),
        "organization_id": (
            str(workflow.organization_id) if workflow.organization_id else None
        ),
        "app_id": str(workflow.app_id) if getattr(workflow, "app_id", None) else None,
        "trigger_mode": "cost_optimizer_compare",
        "cost_optimizer_candidate_id": str(candidate_id),
        "request_id": request.headers.get("x-request-id"),
        "correlation_id": request.headers.get("x-correlation-id"),
        "trace_metadata": {
            "cost_optimizer": {
                "node_id": node_id,
                "baseline_id": baseline["baseline_id"],
                "candidate_id": str(candidate_id),
            },
        },
        "cost_optimizer": {
            "node_id": node_id,
            "baseline_id": baseline["baseline_id"],
            "candidate_id": str(candidate_id),
            "candidate_label": candidate.label,
        },
    }


def _run_cost_optimizer_candidate(
    *,
    workflow: Workflow,
    execution_graph: dict[str, Any],
    node_id: str,
    baseline: dict[str, Any],
    candidate: CostOptimizerCandidateRequest,
    cost_optimizer_candidate_id: UUID,
    current_user: User,
    request: Request,
) -> dict[str, Any]:
    started = time.perf_counter()
    candidate_workflow_run_id = uuid4()
    context = _cost_optimizer_candidate_execution_context(
        workflow=workflow,
        node_id=node_id,
        baseline=baseline,
        candidate=candidate,
        candidate_id=cost_optimizer_candidate_id,
        workflow_run_id=candidate_workflow_run_id,
        current_user=current_user,
        request=request,
    )
    patched_graph = _build_cost_optimizer_candidate_execution_graph(
        execution_graph,
        node_id,
        candidate,
        baseline["input"],
    )

    try:
        task = send_workflow_task(
            celery_app,
            "workflow.execute",
            args=[patched_graph, baseline["input"], context],
            kwargs={"is_deployed": False},
        )
        task_result = task.get(timeout=600)
    except Exception as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        return {
            "label": candidate.label,
            "settings": candidate.model_dump(mode="json"),
            "candidate_workflow_run_id": str(candidate_workflow_run_id),
            "status": "failed",
            "output": {},
            "usage": {},
            "schema_validation": {"status": "skipped", "errors": []},
            "latency_ms": latency_ms,
            "trace": {},
            "error_message": getattr(exc, "code", "workflow.execution_failed"),
            "error_detail": None,
        }

    latency_ms = int((time.perf_counter() - started) * 1000)

    if task_result.get("status") == "success":
        output = _extract_cost_optimizer_node_output(task_result, node_id)
        safe_output = _safe_cost_optimizer_value(output)
        usage = safe_output.get("usage", {}) if isinstance(safe_output, dict) else {}
        usage = _normalize_cost_optimizer_candidate_usage(
            usage if isinstance(usage, dict) else {},
            safe_output if isinstance(safe_output, dict) else None,
        )
        schema_validation = (
            _validate_cost_optimizer_candidate_output_schema(safe_output, candidate)
            if isinstance(safe_output, dict)
            else {"status": "skipped", "errors": []}
        )
        candidate_status = (
            "schema_failed"
            if schema_validation.get("status") == "schema_failed"
            else "success"
        )
        if isinstance(usage, dict) and candidate_status == "schema_failed":
            usage = {**usage, "status": "schema_failed"}
        return {
            "label": candidate.label,
            "settings": candidate.model_dump(mode="json"),
            "candidate_workflow_run_id": str(candidate_workflow_run_id),
            "status": candidate_status,
            "output": safe_output,
            "usage": usage,
            "schema_validation": schema_validation,
            "latency_ms": latency_ms,
            "trace": _build_cost_optimizer_candidate_trace(safe_output)
            if isinstance(safe_output, dict)
            else {},
            "error_message": None,
            "error_detail": None,
        }

    failed_output = _safe_cost_optimizer_value(
        _extract_cost_optimizer_node_output(task_result, node_id)
    )
    error_detail = _safe_task_error(task_result.get("error"))
    return {
        "label": candidate.label,
        "settings": candidate.model_dump(mode="json"),
        "candidate_workflow_run_id": str(candidate_workflow_run_id),
        "status": "failed",
        "output": failed_output,
        "usage": {},
        "schema_validation": {"status": "skipped", "errors": []},
        "latency_ms": latency_ms,
        "trace": _build_cost_optimizer_candidate_trace(failed_output)
        if isinstance(failed_output, dict)
        else {},
        "error_message": (
            error_detail["code"] if error_detail else "Workflow execution failed"
        ),
        "error_detail": error_detail,
    }


def validate_execution_graph(graph: dict):
    try:
        validate_workflow_graph(graph)
    except WorkflowGraphValidationError as exc:
        detail_by_code = {
            "workflow_edge_invalid": "Invalid edge format",
            "workflow_edge_targets_source": "입력/트리거 노드에는 다른 노드를 연결할 수 없습니다.",
            "workflow_edge_from_terminal": "종료 노드에서는 다른 노드로 연결할 수 없습니다.",
        }
        if exc.code == "workflow_edge_source_missing":
            detail = (
                f"존재하지 않는 노드에서 시작하는 연결입니다. edge: {exc.reference}"
            )
        elif exc.code == "workflow_edge_target_missing":
            detail = f"존재하지 않는 노드로 향하는 연결입니다. edge: {exc.reference}"
        elif exc.code == "workflow_cycle_detected":
            detail = f"워크플로우에 순환 연결이 있습니다. node: {exc.reference}"
        else:
            detail = detail_by_code.get(exc.code, "Workflow graph is invalid")
        raise HTTPException(status_code=400, detail=detail) from None

    try:
        parse_workflow_knowledge_references(graph)
    except WorkflowKnowledgeReferenceError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": exc.reason_code, "field": exc.field_path},
        ) from exc


def _patch_compare_graph(
    graph: dict[str, Any],
    node_id: str,
    compare_type: Literal["model", "prompt"],
    value: str,
) -> dict[str, Any]:
    patched = copy.deepcopy(graph)
    for node in patched.get("nodes", []):
        if str(node.get("id")) != node_id:
            continue
        data = node.setdefault("data", {})
        if compare_type == "model":
            data["model_id"] = value
        else:
            data["user_prompt"] = value
        validate_execution_graph(patched)
        return patched
    raise HTTPException(status_code=400, detail="Compare target node not found")


def _extract_node_result(outputs: Any, node_id: str) -> Any:
    if not isinstance(outputs, dict):
        return None
    if node_id in outputs:
        return outputs[node_id]
    nested_result = outputs.get("result")
    if isinstance(nested_result, dict):
        return nested_result.get(node_id)
    return None


def _format_compare_variant(
    *,
    label: str,
    value: str,
    node_id: str,
    status: str,
    outputs: Any = None,
    error: str | None = None,
    error_detail: dict[str, Any] | None = None,
    latency_ms: int | None = None,
) -> dict[str, Any]:
    node_output = _extract_node_result(outputs, node_id)
    usage = node_output.get("usage") if isinstance(node_output, dict) else {}
    usage = usage if isinstance(usage, dict) else {}
    return {
        "label": label,
        "value": value,
        "status": status,
        "error": error,
        "error_detail": error_detail,
        "outputs": outputs,
        "node_output": node_output,
        "model": node_output.get("model") if isinstance(node_output, dict) else None,
        "total_tokens": usage.get("total_tokens")
        or usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0),
        "total_cost": node_output.get("cost", 0.0)
        if isinstance(node_output, dict)
        else 0.0,
        "latency_ms": usage.get("latency_ms") or latency_ms,
    }


def _model_routing_candidates_for_user(
    db: Session,
    *,
    current_user: User,
    organization_id: UUID | None,
    node_data: dict[str, Any],
) -> list[ModelCandidate]:
    """초기 정책에는 현재 편집자가 실제 호출할 수 있는 모델만 넣는다."""
    if organization_id is None:
        raise HTTPException(
            status_code=409, detail="model_routing.organization_required"
        )
    available_ids = set(
        WorkflowRuntimeLLMService.get_runtime_available_model_ids_for_user(
            db,
            user_id=current_user.id,
            organization_id=organization_id,
        )
    )
    candidates = [
        candidate
        for candidate in ModelRouter.collect_candidates(
            db, organization_id=organization_id
        )
        if candidate.model_id in available_ids
        and ModelRouter.is_workflow_chat_model(candidate.model_id)
    ]
    if not candidates:
        raise HTTPException(
            status_code=422, detail="model_routing.no_available_chat_model"
        )
    return candidates


@router.get("/{workflow_id}/llm-nodes/{node_id}/model-routing/bootstrap-preview")
def preview_model_routing_bootstrap_endpoint(
    workflow_id: str,
    node_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """초안 노드의 Judge-first 실행 후보와 준비 상태를 미리 보여준다."""
    workflow = ensure_workflow_permission(db, current_user, workflow_id, "write")
    node = _ensure_cost_optimizer_llm_node(workflow, node_id)
    node_data = node.get("data") if isinstance(node.get("data"), dict) else {}
    return PersistedModelRoutingBootstrapStore.preview(
        db,
        workflow_id=workflow.id,
        organization_id=workflow.organization_id,
        node_id=node_id,
        node_data=node_data,
        downstream_contract=downstream_contract_from_graph(workflow.graph, node_id),
    )


@router.get("/{workflow_id}/llm-nodes/{node_id}/model-routing/bootstrap")
def get_model_routing_bootstrap_endpoint(
    workflow_id: str,
    node_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    workflow = ensure_workflow_permission(db, current_user, workflow_id, "write")
    node = _ensure_cost_optimizer_llm_node(workflow, node_id)
    node_data = node.get("data") if isinstance(node.get("data"), dict) else {}
    preview = PersistedModelRoutingBootstrapStore.preview(
        db,
        workflow_id=workflow.id,
        organization_id=workflow.organization_id,
        node_id=node_id,
        node_data=node_data,
        downstream_contract=downstream_contract_from_graph(workflow.graph, node_id),
    )
    return preview.get("bootstrap")


@router.post("/{workflow_id}/llm-nodes/{node_id}/model-routing/bootstrap")
def create_model_routing_bootstrap_endpoint(
    workflow_id: str,
    node_id: str,
    request_body: ModelRoutingBootstrapRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Planner 호출 없이 초안 단계의 Judge-first 정책을 준비한다."""
    # 초기 기준 생성은 초안 편집 단계의 작업이다. 실제 배포 권한은 이후 배포 API에서
    # 별도로 검사하므로, 여기서는 workflow 수정 권한만 요구한다.
    authorized_workflow = ensure_workflow_permission(
        db, current_user, workflow_id, "write"
    )
    workflow = _lock_workflow_for_cas_graph_write(
        db,
        current_user,
        workflow_id,
        request_body,
        authorized_workflow,
    )
    next_graph = copy.deepcopy(workflow.graph or {})
    node = _ensure_cost_optimizer_llm_node(
        SimpleNamespace(id=workflow.id, graph=next_graph), node_id
    )
    node_data = node.get("data") if isinstance(node.get("data"), dict) else {}
    candidates = _model_routing_candidates_for_user(
        db,
        current_user=current_user,
        organization_id=workflow.organization_id,
        node_data=node_data,
    )
    candidate_ids = {candidate.model_id for candidate in candidates}
    if request_body.default_model_id not in candidate_ids:
        raise HTTPException(status_code=422, detail="model_routing.policy_model_unavailable")
    if (
        request_body.fallback_model_id
        and request_body.fallback_model_id not in candidate_ids
    ):
        raise HTTPException(status_code=422, detail="model_routing.policy_model_unavailable")
    if request_body.fallback_model_id == request_body.default_model_id:
        raise HTTPException(status_code=422, detail="model_routing.fallback_must_differ")

    try:
        bootstrap = PersistedModelRoutingBootstrapStore.create_ready(
            db,
            workflow_id=workflow.id,
            organization_id=workflow.organization_id,
            node_id=node_id,
            node_data=node_data,
            task_description=request_body.task_description,
            default_model_id=request_body.default_model_id,
            fallback_model_id=request_body.fallback_model_id,
            created_by=current_user.id,
            available_candidates=candidates,
            downstream_contract=downstream_contract_from_graph(next_graph, node_id),
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        logger.exception("[Model-Routing] bootstrap creation failed")
        raise HTTPException(
            status_code=502, detail="model_routing.bootstrap_creation_failed"
        ) from exc

    # Judge-first artifact와 draft 참조를 같은 commit으로 저장한다.
    node_data.update(
        {
            "auto_model_routing": True,
            "model_id": request_body.default_model_id,
            "fallback_model_id": request_body.fallback_model_id or "",
            "model_routing_bootstrap_id": str(bootstrap.id),
            "model_routing_bootstrap_fingerprint": bootstrap.task_fingerprint,
            "model_routing_task_description": request_body.task_description,
            "model_routing_strategy": "judge_bootstrap_incremental_v1",
        }
    )
    node["data"] = node_data
    workflow.graph = next_graph
    summary = PersistedModelRoutingBootstrapStore.public_summary(
        bootstrap, include_samples=True, db=db
    )
    graph_metadata = _commit_graph_write_with_canonical_metadata(db, workflow)
    return {**(summary or {}), **graph_metadata}


@router.get("/{workflow_id}/llm-nodes/{node_id}/model-routing/policy")
def get_model_routing_policy_endpoint(
    workflow_id: str,
    node_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """현재 배포에서 사용 중인 LLM node routing policy 상태를 조회한다."""
    workflow = ensure_workflow_permission(db, current_user, workflow_id, "read")
    node = _ensure_cost_optimizer_llm_node(workflow, node_id)
    node_data = node.get("data") if isinstance(node.get("data"), dict) else {}
    policy = _get_model_routing_policy_for_workflow(db, workflow, node_id)
    deployment = _active_deployment_for_workflow(db, workflow)
    return _model_routing_policy_response(
        policy,
        enabled=bool(node_data.get("auto_model_routing")),
        db=db,
        decision_deployment_id=deployment.id if deployment else None,
        decision_node_id=node_id,
    )


@router.post(
    "/{workflow_id}/llm-nodes/{node_id}/model-routing/preview",
    response_model=ModelRoutingPreviewResponse,
)
def preview_model_routing_policy_endpoint(
    workflow_id: str,
    node_id: str,
    request_body: ModelRoutingPreviewRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """현재 active deployment policy가 고를 모델을 실행 없이 보여준다."""
    workflow = ensure_workflow_permission(db, current_user, workflow_id, "execute")
    deployment = _active_deployment_for_workflow(db, workflow)
    if deployment is None:
        raise HTTPException(status_code=409, detail="model_routing.policy_not_ready")
    try:
        return ModelRoutingPreviewService.preview(
            db,
            workflow=workflow,
            deployment=deployment,
            node_id=node_id,
            inputs=request_body.inputs,
        )
    except ModelRoutingPreviewBlockedError as exc:
        raise HTTPException(status_code=409, detail=exc.code) from exc


@router.patch("/{workflow_id}/llm-nodes/{node_id}/model-routing/policy")
def patch_model_routing_policy_endpoint(
    workflow_id: str,
    node_id: str,
    request_body: ModelRoutingPolicyPatchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """draft의 자동 라우팅 설정과 현재 배포 policy의 갱신 기준을 함께 갱신한다."""
    authorized_workflow = ensure_workflow_permission(
        db, current_user, workflow_id, "deploy"
    )
    workflow = _lock_workflow_for_cas_graph_write(
        db,
        current_user,
        workflow_id,
        request_body,
        authorized_workflow,
    )
    next_graph = copy.deepcopy(workflow.graph or {})
    node = _ensure_cost_optimizer_llm_node(
        SimpleNamespace(id=workflow.id, graph=next_graph), node_id
    )
    node_data = node.get("data") if isinstance(node.get("data"), dict) else {}
    node_data["auto_model_routing"] = request_body.enabled
    request_fields = request_body.model_fields_set
    if "default_model_id" in request_fields and request_body.default_model_id is None:
        raise HTTPException(
            status_code=422, detail="model_routing.default_model_required"
        )
    configured_model_id = str(
        request_body.default_model_id
        if "default_model_id" in request_fields
        else node_data.get("model_id") or ""
    ).strip()
    fallback_model_id = (
        request_body.fallback_model_id
        if "fallback_model_id" in request_fields
        else node_data.get("fallback_model_id")
    )
    fallback_model_id = str(fallback_model_id or "").strip() or None
    if request_body.enabled and not configured_model_id:
        raise HTTPException(
            status_code=422, detail="model_routing.default_model_required"
        )
    if fallback_model_id == configured_model_id:
        raise HTTPException(
            status_code=422, detail="model_routing.fallback_must_differ"
        )
    if "default_model_id" in request_fields:
        node_data["model_id"] = configured_model_id
    if "fallback_model_id" in request_fields:
        node_data["fallback_model_id"] = fallback_model_id or ""
    legacy_policy = node_data.get("model_routing_policy")
    if not isinstance(legacy_policy, dict):
        legacy_policy = {}
    legacy_refresh = legacy_policy.get("refresh")
    if not isinstance(legacy_refresh, dict):
        legacy_refresh = {}
    legacy_refresh["refresh_every_runs"] = request_body.refresh_every_runs
    legacy_policy["refresh"] = legacy_refresh
    node_data["model_routing_policy"] = legacy_policy
    node["data"] = node_data
    WorkflowService.validate_knowledge_references(
        db,
        next_graph,
        user_id=current_user.id,
        organization_id=workflow.organization_id,
    )

    policy = _get_model_routing_policy_for_workflow(db, workflow, node_id)
    if policy is not None:
        if (
            "default_model_id" in request_fields
            or "fallback_model_id" in request_fields
        ):
            execution_subject_id = policy.execution_subject_user_id
            organization_id = policy.organization_id or workflow.organization_id
            if execution_subject_id is None or organization_id is None:
                raise HTTPException(
                    status_code=409,
                    detail="model_routing.execution_subject_unavailable",
                )
            available_model_ids = set(
                WorkflowRuntimeLLMService.get_runtime_available_model_ids_for_user(
                    db,
                    user_id=execution_subject_id,
                    organization_id=organization_id,
                )
            )
            policy_model_ids = {configured_model_id}
            if fallback_model_id is not None:
                policy_model_ids.add(fallback_model_id)
            if not policy_model_ids.issubset(available_model_ids):
                raise HTTPException(
                    status_code=422,
                    detail="model_routing.policy_model_unavailable",
                )
        policy.enabled = request_body.enabled
        policy.refresh_every_runs = request_body.refresh_every_runs
        policy.judge_user_id = current_user.id
        if policy.active_policy and (
            "default_model_id" in request_fields
            or "fallback_model_id" in request_fields
        ):
            active_policy = copy.deepcopy(policy.active_policy)
            active_policy["default_model_id"] = configured_model_id
            active_policy["fallback_model_id"] = fallback_model_id
            policy.active_policy = active_policy
        if not request_body.enabled:
            policy.status = "off"
            policy.refresh_requested_at = None
        elif policy.active_policy:
            policy.status = "active"
        else:
            policy.status = "collecting"
    workflow.graph = next_graph
    metadata = _commit_graph_write_with_canonical_metadata(db, workflow)
    response = _model_routing_policy_response(
        policy,
        enabled=request_body.enabled,
        db=db,
    )
    return {**response, **metadata}


@router.post("/{workflow_id}/llm-nodes/{node_id}/model-routing/policy/refresh")
def refresh_model_routing_policy_endpoint(
    workflow_id: str,
    node_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """사용자 요청으로 policy judge refresh를 한 번 예약한다."""
    workflow = ensure_workflow_permission(db, current_user, workflow_id, "deploy")
    _ensure_cost_optimizer_llm_node(workflow, node_id)
    policy = _get_model_routing_policy_for_workflow(db, workflow, node_id)
    if policy is None or not policy.enabled:
        raise HTTPException(status_code=409, detail="model_routing.policy_not_ready")
    if policy.refresh_requested_at is not None:
        try:
            # DB에 남은 pending 요청은 이전 publish 실패 또는 broker 재시도의
            # 복구 경로일 수 있으므로 manual 요청으로 다시 발행한다.
            send_workflow_task(
                celery_app,
                "workflow.model_routing.refresh_policy",
                args=[str(policy.id), "manual_refresh"],
            )
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail="model_routing.refresh_schedule_failed",
            ) from exc
        return {
            "policy_id": str(policy.id),
            "status": "refreshing",
            "trigger": "manual_refresh",
            "scheduled": True,
        }

    previous_status = policy.status
    previous_refresh_requested_at = policy.refresh_requested_at
    previous_judge_user_id = policy.judge_user_id
    policy.status = "refreshing"
    policy.refresh_requested_at = datetime.now(timezone.utc)
    policy.judge_user_id = current_user.id
    db.commit()
    try:
        send_workflow_task(
            celery_app,
            "workflow.model_routing.refresh_policy",
            args=[str(policy.id), "manual_refresh"],
        )
    except Exception as exc:
        policy.status = previous_status
        policy.refresh_requested_at = previous_refresh_requested_at
        policy.judge_user_id = previous_judge_user_id
        try:
            db.commit()
        except Exception:
            db.rollback()
        raise HTTPException(
            status_code=503,
            detail="model_routing.refresh_schedule_failed",
        ) from exc
    return {
        "policy_id": str(policy.id),
        "status": "refreshing",
        "trigger": "manual_refresh",
        "scheduled": True,
    }


@router.get(
    "/{workflow_id}/llm-nodes/{node_id}/cost-optimizer/availability",
    response_model=CostOptimizerAvailabilityResponse,
)
def get_cost_optimizer_availability(
    workflow_id: str,
    node_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    특정 LLM 노드가 Cost Optimizer A/B 테스트 진입 대상인지 확인합니다.
    """
    workflow = ensure_workflow_permission(db, current_user, workflow_id, "write")
    node = _ensure_cost_optimizer_llm_node(workflow, node_id)
    node_type = str(node.get("type") or "")

    return {
        "available": True,
        "reason": None,
        "workflow_id": str(workflow.id),
        "node_id": node_id,
        "node_type": node_type,
        "permission": {
            "can_compare": True,
            "can_apply": True,
            "required_auth_state": "builder",
        },
    }


@router.get(
    "/{workflow_id}/llm-nodes/{node_id}/cost-optimizer/baselines/latest",
    response_model=CostOptimizerLatestBaselineResponse,
)
def get_cost_optimizer_latest_baseline_endpoint(
    workflow_id: str,
    node_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    특정 LLM 노드의 가장 최근 비교 가능 baseline을 조회합니다.
    """
    workflow = ensure_workflow_permission(db, current_user, workflow_id, "write")
    _ensure_cost_optimizer_llm_node(workflow, node_id)
    baseline = get_cost_optimizer_latest_baseline(db, workflow, node_id)
    return {"baseline": _public_cost_optimizer_baseline_row(baseline)}


@router.get(
    "/{workflow_id}/llm-nodes/{node_id}/cost-optimizer/baselines",
    response_model=CostOptimizerBaselineListResponse,
)
def list_cost_optimizer_baselines_endpoint(
    workflow_id: str,
    node_id: str,
    q: str | None = None,
    model: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    sort: str = "started_at_desc",
    compare_available: bool | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    특정 LLM 노드의 baseline 후보 목록을 조회합니다.
    """
    workflow = ensure_workflow_permission(db, current_user, workflow_id, "write")
    _ensure_cost_optimizer_llm_node(workflow, node_id)
    result = list_cost_optimizer_baselines(
        db,
        workflow,
        node_id,
        q=q,
        model=model,
        date_from=date_from,
        date_to=date_to,
        sort=sort,
        compare_available=compare_available,
        limit=limit,
        offset=offset,
    )
    return {
        **result,
        "items": [
            _public_cost_optimizer_baseline_row(row)
            for row in result.get("items", [])
            if isinstance(row, dict)
        ],
    }


@router.get("/{workflow_id}/llm-nodes/{node_id}/cost-optimizer/experiments")
def list_cost_optimizer_experiments_endpoint(
    workflow_id: str,
    node_id: str,
    baseline_id: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    created_by: str | None = None,
    candidate_status: str | None = None,
    model: str | None = None,
    is_applied: bool | None = None,
    schema_status: str | None = None,
    downstream_state: str | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    특정 LLM 노드의 Cost Optimizer experiment/candidate 이력을 조회합니다.
    """
    workflow = ensure_workflow_permission(db, current_user, workflow_id, "write")
    _ensure_cost_optimizer_llm_node(workflow, node_id)
    return list_cost_optimizer_experiments(
        db,
        workflow,
        node_id,
        baseline_id=baseline_id,
        date_from=date_from,
        date_to=date_to,
        created_by=created_by,
        candidate_status=candidate_status,
        model=model,
        is_applied=is_applied,
        schema_status=schema_status,
        downstream_state=downstream_state,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{workflow_id}/llm-nodes/{node_id}/cost-optimizer/experiments/"
    "{experiment_id}/candidates/{candidate_id}"
)
def get_cost_optimizer_experiment_candidate_endpoint(
    workflow_id: str,
    node_id: str,
    experiment_id: str,
    candidate_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """URL로 지정한 Cost Optimizer 실험 후보의 상세 summary를 조회합니다."""
    workflow = ensure_workflow_permission(db, current_user, workflow_id, "write")
    _ensure_cost_optimizer_llm_node(workflow, node_id)
    return get_cost_optimizer_experiment_candidate(
        db,
        workflow,
        node_id,
        experiment_id,
        candidate_id,
    )


@router.get(
    "/{workflow_id}/llm-nodes/{node_id}/cost-optimizer/parameter-recommendations"
)
def get_cost_optimizer_parameter_recommendations_endpoint(
    workflow_id: str,
    node_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    배포 후 운영 로그를 기준으로 LLM 노드 파라미터 추천 후보를 조회합니다.
    """
    workflow = ensure_workflow_permission(db, current_user, workflow_id, "write")
    _ensure_cost_optimizer_llm_node(workflow, node_id)
    return CostOptimizerParameterRecommendationService.recommend(
        db,
        workflow=workflow,
        node_id=node_id,
    )


@router.patch("/{workflow_id}/llm-nodes/{node_id}/cost-optimizer/apply-recommendations")
def apply_cost_optimizer_recommendations(
    workflow_id: str,
    node_id: str,
    request_body: CostOptimizerRecommendationApplyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    서버가 다시 계산한 파라미터 추천 중 선택된 항목만 현재 draft LLM node에 적용합니다.
    """
    authorized_workflow = ensure_workflow_permission(
        db, current_user, workflow_id, "write"
    )
    workflow = _lock_workflow_for_cas_graph_write(
        db,
        current_user,
        workflow_id,
        request_body,
        authorized_workflow,
    )
    _ensure_cost_optimizer_llm_node(workflow, node_id)
    recommendations_payload = CostOptimizerParameterRecommendationService.recommend(
        db,
        workflow=workflow,
        node_id=node_id,
    )
    candidate_settings, applied_recommendation_ids = (
        _cost_optimizer_candidate_from_recommendations(
            workflow,
            node_id,
            recommendations_payload,
            request_body.recommendation_ids,
            allowed_apply_modes={"direct_policy_update"},
        )
    )
    candidate_settings = _materialize_cost_optimizer_candidate_model_routing_policy(
        db,
        current_user,
        candidate_settings,
    )
    _validate_cost_optimizer_candidate_shape(candidate_settings)
    _ensure_cost_optimizer_candidate_knowledge_available(
        db,
        current_user,
        workflow,
        candidate_settings,
    )
    _ensure_cost_optimizer_candidate_models_available(
        db,
        current_user,
        candidate_settings,
    )

    current_graph = copy.deepcopy(workflow.graph or {})
    _ensure_cost_optimizer_llm_node(
        SimpleNamespace(id=workflow.id, graph=current_graph),
        node_id,
    )
    next_graph = _patch_cost_optimizer_candidate_graph(
        current_graph,
        node_id,
        candidate_settings,
    )
    WorkflowService.validate_knowledge_references(
        db,
        next_graph,
        user_id=current_user.id,
        organization_id=workflow.organization_id,
    )
    workflow.graph = next_graph
    metadata = _commit_graph_write_with_canonical_metadata(db, workflow)

    return {
        "workflow_id": str(workflow.id),
        "node_id": node_id,
        "applied": True,
        "applied_recommendation_ids": applied_recommendation_ids,
        "downstream_compatibility": {
            "state": "unknown",
            "label": "판정 전",
            "message": "추천 설정이 적용되었습니다. 현재 workflow 테스트 실행으로 downstream 결과를 확인하세요.",
        },
        **metadata,
    }


def _cost_optimizer_inline_schema_validation(
    candidate: CostOptimizerCandidateRequest,
    candidate_result: dict[str, Any],
) -> dict[str, Any]:
    output_format = (
        candidate.output_format if isinstance(candidate.output_format, dict) else {}
    )
    if output_format.get("type") != "json":
        return {"status": "not_applicable", "issues": []}
    schema = output_format.get("schema")
    if not isinstance(schema, dict) or not schema:
        return {"status": "not_configured", "issues": []}
    validation = candidate_result.get("schema_validation")
    validation = validation if isinstance(validation, dict) else {}
    if validation.get("status") in {"valid", "pass", "passed"}:
        return {"status": "passed", "issues": []}
    errors = (
        validation.get("errors") if isinstance(validation.get("errors"), list) else []
    )
    return {
        "status": "failed",
        "issues": [str(error)[:200] for error in errors[:10]],
    }


def _cost_optimizer_metric_comparison(
    baseline_value: Any,
    candidate_value: Any,
) -> dict[str, Any]:
    baseline_number = (
        float(baseline_value)
        if isinstance(baseline_value, (int, float, Decimal))
        else None
    )
    candidate_number = (
        float(candidate_value)
        if isinstance(candidate_value, (int, float, Decimal))
        else None
    )
    delta = (
        candidate_number - baseline_number
        if baseline_number is not None and candidate_number is not None
        else None
    )
    return {
        "baseline": baseline_number,
        "candidate": candidate_number,
        "delta": delta,
        "change_rate": (
            delta / baseline_number
            if delta is not None and baseline_number not in {None, 0}
            else None
        ),
    }


def _evaluate_cost_optimizer_output_quality(
    *,
    db: Session,
    workflow: Any,
    current_user: User,
    node_id: str,
    candidate_row: CostOptimizerCandidate,
    baseline: dict[str, Any],
    candidate_result: dict[str, Any],
) -> dict[str, Any]:
    """성공한 B 출력만 평가하고 실패 후보는 평가 불가로 정규화한다."""
    if candidate_result.get("status") == "failed":
        return {
            "status": "unavailable",
            "baseline": {"score": None},
            "candidate": {"score": None},
            "delta": None,
            "dimensions": {},
            "confidence": "unavailable",
            "safe_summary": "candidate 실행 실패로 품질 평가를 수행하지 않았습니다.",
            "judge_cost": None,
            "judge_usage_log_id": None,
        }

    return CostOptimizerOutputQualityService.evaluate(
        db=db,
        workflow=workflow,
        current_user=current_user,
        node_id=node_id,
        candidate_row=candidate_row,
        baseline=baseline,
        candidate_result={**candidate_result, "input": baseline.get("input")},
    )


def _build_cost_optimizer_verification_apply_decision(
    *,
    candidate_result: dict[str, Any],
    schema_validation: dict[str, Any],
    downstream_compatibility: dict[str, Any],
    quality_evaluation: dict[str, Any],
) -> dict[str, Any]:
    reasons: list[str] = []
    candidate_status = str(candidate_result.get("status") or "failed")
    if candidate_status == "failed":
        reasons.append("candidate_execution_failed")
    if schema_validation.get("status") == "failed":
        reasons.append("schema_failed")
    if downstream_compatibility.get("state") == "incompatible":
        reasons.append("downstream_incompatible")

    allowed = not reasons
    confirmation_reasons: list[str] = []
    if quality_evaluation.get("status") != "completed":
        confirmation_reasons.append("quality_evaluation_unavailable")
    elif (
        quality_evaluation.get("delta") is not None and quality_evaluation["delta"] < 0
    ):
        confirmation_reasons.append("quality_score_decreased")
    if quality_evaluation.get("confidence") == "low":
        confirmation_reasons.append("quality_confidence_low")
    if downstream_compatibility.get("state") == "warning":
        confirmation_reasons.append("downstream_warning")

    return {
        "allowed": allowed,
        "requires_confirmation": allowed and bool(confirmation_reasons),
        "reasons": reasons + confirmation_reasons,
    }


def _cost_optimizer_stale_verification_response(reason: str) -> dict[str, Any]:
    return {
        "verification_status": "stale",
        "comparison_id": None,
        "candidate_id": None,
        "baseline": None,
        "candidate": None,
        "metrics": {},
        "quality_evaluation": {
            "status": "unavailable",
            "baseline": {"score": None},
            "candidate": {"score": None},
            "delta": None,
            "dimensions": {},
            "confidence": "unavailable",
            "safe_summary": "추천 설정이 최신 node 설정과 일치하지 않습니다.",
        },
        "schema_validation": {"status": "not_applicable", "issues": []},
        "downstream_compatibility": {"state": "unknown"},
        "incurred_cost": {
            "candidate_execution_cost": None,
            "quality_judge_cost": None,
            "total_new_cost": None,
            "currency": "USD",
        },
        "apply": {
            "allowed": False,
            "requires_confirmation": False,
            "reasons": [reason],
        },
    }


def _cost_optimizer_recommendation_request_fingerprint(
    *,
    workflow: Workflow,
    node_id: str,
    request_body: CostOptimizerRecommendationVerifyRequest,
) -> str:
    return CostOptimizerRecommendationVerificationService.request_fingerprint(
        workflow_id=workflow.id,
        node_id=node_id,
        recommendation_ids=request_body.recommendation_ids,
        baseline_mode=request_body.baseline_mode,
        recommendation_policy_version=request_body.recommendation_policy_version,
        recommendation_fingerprint=request_body.recommendation_fingerprint,
        node_config_fingerprint=request_body.node_config_fingerprint,
    )


def _verify_cost_optimizer_recommendations(
    *,
    db: Session,
    workflow: Workflow,
    execution_graph: dict[str, Any],
    node_id: str,
    current_user: User,
    request: Request,
    request_body: CostOptimizerRecommendationVerifyRequest,
    idempotency_key: str,
) -> dict[str, Any]:
    """추천 candidate 실행과 quality judge를 하나의 멱등 검증으로 묶는다."""
    idempotency_key = idempotency_key.strip()
    if not idempotency_key:
        raise HTTPException(
            status_code=422, detail="cost_optimizer.idempotency_key_required"
        )
    request_fingerprint = _cost_optimizer_recommendation_request_fingerprint(
        workflow=workflow,
        node_id=node_id,
        request_body=request_body,
    )
    claim = CostOptimizerRecommendationVerificationService.claim(
        db,
        workflow_id=workflow.id,
        node_id=node_id,
        user_id=current_user.id,
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
    )
    if claim.replay_response is not None:
        return claim.replay_response

    verification = claim.record
    try:
        recommendations_payload = CostOptimizerParameterRecommendationService.recommend(
            db,
            workflow=workflow,
            node_id=node_id,
        )
        node = _ensure_cost_optimizer_llm_node(workflow, node_id)
        node_data = node.get("data") if isinstance(node.get("data"), dict) else {}
        node_data = node_data if isinstance(node_data, dict) else {}
        current_fingerprint = _node_config_fingerprint(node_data)
        current_policy_version = str(
            recommendations_payload.get("policy_version") or ""
        )
        current_recommendation_fingerprint = str(
            recommendations_payload.get("recommendation_fingerprint") or ""
        )
        if (
            (
                request_body.node_config_fingerprint
                and request_body.node_config_fingerprint != current_fingerprint
            )
            or (
                request_body.recommendation_policy_version
                and request_body.recommendation_policy_version != current_policy_version
            )
            or (
                request_body.recommendation_fingerprint
                and request_body.recommendation_fingerprint
                != current_recommendation_fingerprint
            )
        ):
            response = _cost_optimizer_stale_verification_response(
                "recommendation_stale"
            )
            CostOptimizerRecommendationVerificationService.complete(
                db,
                record=verification,
                response=response,
                experiment_id=None,
                candidate_id=None,
            )
            return response

        candidate, applied_recommendation_ids = (
            _cost_optimizer_candidate_from_recommendations(
                workflow,
                node_id,
                recommendations_payload,
                request_body.recommendation_ids,
            )
        )
        candidate = _materialize_cost_optimizer_candidate_model_routing_policy(
            db,
            current_user,
            candidate,
        )
        _validate_cost_optimizer_candidate_shape(candidate)
        _ensure_cost_optimizer_candidate_knowledge_available(
            db,
            current_user,
            workflow,
            candidate,
        )
        _ensure_cost_optimizer_candidate_models_available(
            db,
            current_user,
            candidate,
        )
        baseline = get_cost_optimizer_latest_operation_baseline(db, workflow, node_id)
        try:
            DeploymentParameterOptimizationService.ensure_validation_budget_available(
                db,
                deployment_id=baseline.get("deployment_id"),
                node_id=node_id,
            )
        except DeploymentParameterOptimizationBudgetExceeded as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    except HTTPException as exc:
        if exc.detail == "cost_optimizer.recommendation_stale":
            response = _cost_optimizer_stale_verification_response(
                "recommendation_stale"
            )
            CostOptimizerRecommendationVerificationService.complete(
                db,
                record=verification,
                response=response,
                experiment_id=None,
                candidate_id=None,
            )
            return response
        CostOptimizerRecommendationVerificationService.fail(db, record=verification)
        raise
    except Exception:
        CostOptimizerRecommendationVerificationService.fail(db, record=verification)
        raise

    try:
        experiment, candidate_row = _create_cost_optimizer_comparison(
            db=db,
            workflow=workflow,
            node_id=node_id,
            baseline=baseline,
            candidate=candidate,
            current_user=current_user,
        )
        candidate_result = _run_cost_optimizer_candidate(
            workflow=workflow,
            execution_graph=execution_graph,
            node_id=node_id,
            baseline=baseline,
            candidate=candidate,
            cost_optimizer_candidate_id=candidate_row.id,
            current_user=current_user,
            request=request,
        )
        downstream_compatibility = _resolve_cost_optimizer_downstream_compatibility(
            baseline,
            workflow,
            node_id,
            candidate_output=candidate_result.get("output")
            if isinstance(candidate_result, dict)
            else None,
        )
        schema_validation = _cost_optimizer_inline_schema_validation(
            candidate,
            candidate_result,
        )
        quality_evaluation = _evaluate_cost_optimizer_output_quality(
            db=db,
            workflow=workflow,
            current_user=current_user,
            node_id=node_id,
            candidate_row=candidate_row,
            baseline=baseline,
            candidate_result=candidate_result,
        )
        diff = _build_cost_optimizer_diff(baseline, candidate_result)
        experiment = _persist_cost_optimizer_comparison(
            db=db,
            experiment=experiment,
            candidate_row=candidate_row,
            candidate=candidate,
            candidate_result=candidate_result,
            diff=diff,
            downstream_compatibility=downstream_compatibility,
            quality_evaluation=quality_evaluation,
        )
        baseline_usage = (
            baseline.get("usage") if isinstance(baseline.get("usage"), dict) else {}
        )
        candidate_usage = (
            candidate_result.get("usage")
            if isinstance(candidate_result.get("usage"), dict)
            else {}
        )
        candidate_cost = _cost_optimizer_usage_number(
            candidate_usage, "cost", "total_cost"
        )
        judge_cost = quality_evaluation.get("judge_cost")
        judge_cost = float(judge_cost) if isinstance(judge_cost, (int, float)) else None
        known_new_costs = [
            cost for cost in (candidate_cost, judge_cost) if cost is not None
        ]
        total_new_cost = sum(known_new_costs) if known_new_costs else None
        metrics = {
            "cost": _cost_optimizer_metric_comparison(
                _cost_optimizer_usage_number(baseline_usage, "cost", "total_cost"),
                candidate_cost,
            ),
            "latency_ms": _cost_optimizer_metric_comparison(
                _cost_optimizer_usage_number(baseline_usage, "latency_ms"),
                _cost_optimizer_usage_number(candidate_usage, "latency_ms")
                or candidate_result.get("latency_ms"),
            ),
            "input_tokens": _cost_optimizer_metric_comparison(
                _cost_optimizer_usage_number(baseline_usage, "prompt_tokens"),
                _cost_optimizer_usage_number(candidate_usage, "prompt_tokens"),
            ),
            "output_tokens": _cost_optimizer_metric_comparison(
                _cost_optimizer_usage_number(baseline_usage, "completion_tokens"),
                _cost_optimizer_usage_number(candidate_usage, "completion_tokens"),
            ),
            "total_tokens": _cost_optimizer_metric_comparison(
                _cost_optimizer_total_tokens(baseline_usage),
                _cost_optimizer_total_tokens(candidate_usage),
            ),
        }
        verification_status = (
            "failed"
            if candidate_result.get("status") == "failed"
            else "partial"
            if quality_evaluation.get("status") != "completed"
            else "completed"
        )
        apply = _build_cost_optimizer_verification_apply_decision(
            candidate_result=candidate_result,
            schema_validation=schema_validation,
            downstream_compatibility=downstream_compatibility,
            quality_evaluation=quality_evaluation,
        )
        response = {
            "verification_status": verification_status,
            "comparison_id": str(experiment.id),
            "candidate_id": str(candidate_row.id),
            "applied_recommendation_ids": applied_recommendation_ids,
            "baseline": {
                "label": "최신 비교 가능한 성공 기록",
                "workflow_node_run_id": baseline.get("baseline_id"),
                "executed_at": baseline.get("run_started_at"),
                "model": baseline.get("model"),
                "deployment_id": baseline.get("deployment_id"),
                "metrics": {
                    "cost": _cost_optimizer_usage_number(
                        baseline_usage, "cost", "total_cost"
                    ),
                    "latency_ms": _cost_optimizer_usage_number(
                        baseline_usage, "latency_ms"
                    ),
                    "input_tokens": _cost_optimizer_usage_number(
                        baseline_usage, "prompt_tokens"
                    ),
                    "output_tokens": _cost_optimizer_usage_number(
                        baseline_usage, "completion_tokens"
                    ),
                    "total_tokens": _cost_optimizer_total_tokens(baseline_usage),
                },
            },
            "candidate": {
                "status": candidate_result.get("status"),
                "model": (
                    candidate_result.get("output", {}).get("model")
                    if isinstance(candidate_result.get("output"), dict)
                    else None
                )
                or candidate.model_id,
                "metrics": {
                    "cost": candidate_cost,
                    "latency_ms": metrics["latency_ms"]["candidate"],
                    "input_tokens": metrics["input_tokens"]["candidate"],
                    "output_tokens": metrics["output_tokens"]["candidate"],
                    "total_tokens": metrics["total_tokens"]["candidate"],
                },
            },
            "metrics": metrics,
            "quality_evaluation": quality_evaluation,
            "schema_validation": schema_validation,
            "downstream_compatibility": downstream_compatibility,
            "incurred_cost": {
                "candidate_execution_cost": candidate_cost,
                "quality_judge_cost": judge_cost,
                "total_new_cost": total_new_cost,
                "currency": "USD",
            },
            "apply": apply,
            "verification_context": {
                "node_config_fingerprint": current_fingerprint,
                "recommendation_policy_version": current_policy_version,
                "recommendation_fingerprint": current_recommendation_fingerprint,
            },
        }
        DeploymentParameterOptimizationService.record_validation_spend(
            db,
            deployment_id=baseline.get("deployment_id"),
            node_id=node_id,
            amount_usd=total_new_cost,
        )
        CostOptimizerRecommendationVerificationService.complete(
            db,
            record=verification,
            response=response,
            experiment_id=experiment.id,
            candidate_id=candidate_row.id,
        )
        return response
    except Exception:
        CostOptimizerRecommendationVerificationService.fail(db, record=verification)
        raise


@router.post("/{workflow_id}/llm-nodes/{node_id}/cost-optimizer/recommendations/verify")
def verify_cost_optimizer_recommendations(
    workflow_id: str,
    node_id: str,
    request_body: CostOptimizerRecommendationVerifyRequest,
    request: Request,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1),
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """추천 설정을 최신 성공 운영 baseline에 한 번만 실행해 빠르게 검증한다."""
    workflow = ensure_workflow_permission(db, current_user, workflow_id, "write")
    _ensure_workflow_matches_active_organization(
        db,
        request,
        current_user,
        workflow,
        x_organization_id,
    )
    normalized_idempotency_key = idempotency_key.strip()
    if not normalized_idempotency_key:
        raise HTTPException(
            status_code=422,
            detail="cost_optimizer.idempotency_key_required",
        )
    request_fingerprint = _cost_optimizer_recommendation_request_fingerprint(
        workflow=workflow,
        node_id=node_id,
        request_body=request_body,
    )
    replay_response = CostOptimizerRecommendationVerificationService.replay_existing(
        db,
        user_id=current_user.id,
        idempotency_key=normalized_idempotency_key,
        request_fingerprint=request_fingerprint,
    )
    if replay_response is not None:
        return replay_response
    _ensure_cost_optimizer_llm_node(workflow, node_id)
    execution_graph = _bind_and_preflight_authenticated_graph(
        db,
        workflow=workflow,
        graph=workflow.graph or {},
        principal_id=current_user.id,
    )
    return _verify_cost_optimizer_recommendations(
        db=db,
        workflow=workflow,
        execution_graph=execution_graph,
        node_id=node_id,
        current_user=current_user,
        request=request,
        request_body=request_body,
        idempotency_key=normalized_idempotency_key,
    )


@router.post("/{workflow_id}/llm-nodes/{node_id}/cost-optimizer/compare")
def compare_cost_optimizer_candidate(
    workflow_id: str,
    node_id: str,
    request_body: CostOptimizerCompareRequest,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    A baseline input을 고정하고 B candidate만 새 설정으로 실행합니다.
    """
    workflow = ensure_workflow_permission(db, current_user, workflow_id, "write")
    _ensure_workflow_matches_active_organization(
        db,
        request,
        current_user,
        workflow,
        x_organization_id,
    )
    _ensure_cost_optimizer_llm_node(workflow, node_id)
    execution_graph = _bind_and_preflight_authenticated_graph(
        db,
        workflow=workflow,
        graph=workflow.graph or {},
        principal_id=current_user.id,
    )
    baseline = get_cost_optimizer_baseline_by_id(
        db,
        workflow,
        node_id,
        request_body.baseline_id,
    )
    if not baseline.get("compare_available") or not baseline.get("input_available"):
        raise HTTPException(
            status_code=400,
            detail="cost_optimizer.baseline_input_unavailable",
        )

    candidate = _materialize_cost_optimizer_candidate_model_routing_policy(
        db,
        current_user,
        request_body.candidate,
    )
    _validate_cost_optimizer_candidate_shape(candidate)
    _ensure_cost_optimizer_candidate_knowledge_available(
        db,
        current_user,
        workflow,
        candidate,
    )
    _ensure_cost_optimizer_candidate_models_available(
        db,
        current_user,
        candidate,
    )
    experiment, candidate_row = _create_cost_optimizer_comparison(
        db=db,
        workflow=workflow,
        node_id=node_id,
        baseline=baseline,
        candidate=candidate,
        current_user=current_user,
    )

    candidate_result = _run_cost_optimizer_candidate(
        workflow=workflow,
        execution_graph=execution_graph,
        node_id=node_id,
        baseline=baseline,
        candidate=candidate,
        cost_optimizer_candidate_id=candidate_row.id,
        current_user=current_user,
        request=request,
    )
    diff = _build_cost_optimizer_diff(baseline, candidate_result)
    downstream_compatibility = _resolve_cost_optimizer_downstream_compatibility(
        baseline,
        workflow,
        node_id,
        candidate_output=candidate_result.get("output")
        if isinstance(candidate_result, dict)
        else None,
    )
    quality_evaluation = _evaluate_cost_optimizer_output_quality(
        db=db,
        workflow=workflow,
        current_user=current_user,
        node_id=node_id,
        candidate_row=candidate_row,
        baseline=baseline,
        candidate_result=candidate_result,
    )
    experiment = _persist_cost_optimizer_comparison(
        db=db,
        experiment=experiment,
        candidate_row=candidate_row,
        candidate=candidate,
        candidate_result=candidate_result,
        diff=diff,
        downstream_compatibility=downstream_compatibility,
        quality_evaluation=quality_evaluation,
    )

    return {
        "comparison_id": str(experiment.id),
        "workflow_id": str(workflow.id),
        "node_id": node_id,
        "baseline": {
            "baseline_id": baseline["baseline_id"],
            "label": "A",
            "settings": baseline.get("node_options") or {},
            "input": baseline.get("input"),
            "output": baseline.get("output"),
            "usage": baseline.get("usage") or {},
            "trace": baseline.get("trace") or {},
        },
        "candidate": candidate_result,
        "diff": diff,
        "quality_evaluation": quality_evaluation,
        "downstream_compatibility": downstream_compatibility,
    }


@router.patch("/{workflow_id}/llm-nodes/{node_id}/cost-optimizer/apply")
def apply_cost_optimizer_candidate(
    workflow_id: str,
    node_id: str,
    request_body: CostOptimizerApplyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    B candidate 설정 전체를 현재 draft의 target LLM node에 적용합니다.
    """
    authorized_workflow = ensure_workflow_permission(
        db, current_user, workflow_id, "write"
    )
    workflow = _lock_workflow_for_cas_graph_write(
        db,
        current_user,
        workflow_id,
        request_body,
        authorized_workflow,
    )
    _ensure_cost_optimizer_llm_node(workflow, node_id)
    candidate_settings = _materialize_cost_optimizer_candidate_model_routing_policy(
        db,
        current_user,
        request_body.candidate_settings,
    )
    _validate_cost_optimizer_candidate_shape(candidate_settings)
    _ensure_cost_optimizer_candidate_knowledge_available(
        db,
        current_user,
        workflow,
        candidate_settings,
    )
    _ensure_cost_optimizer_candidate_models_available(
        db,
        current_user,
        candidate_settings,
    )
    applied_candidate_row, comparison_candidate_rows = (
        _ensure_cost_optimizer_candidate_apply_allowed(
            db,
            workflow,
            node_id,
            request_body.comparison_id,
            candidate_settings,
            request_body.acknowledge_downstream_warning,
        )
    )

    current_graph = copy.deepcopy(workflow.graph or {})
    _ensure_cost_optimizer_llm_node(
        SimpleNamespace(id=workflow.id, graph=current_graph),
        node_id,
    )
    next_graph = _patch_cost_optimizer_candidate_graph(
        current_graph,
        node_id,
        candidate_settings,
    )
    WorkflowService.validate_knowledge_references(
        db,
        next_graph,
        user_id=current_user.id,
        organization_id=workflow.organization_id,
    )
    workflow.graph = next_graph
    if applied_candidate_row is not None:
        applied_at = datetime.now(timezone.utc)
        for candidate_row in comparison_candidate_rows:
            is_applied_candidate = candidate_row is applied_candidate_row
            candidate_row.is_applied = is_applied_candidate
            candidate_row.applied_at = applied_at if is_applied_candidate else None
            candidate_row.applied_by = current_user.id if is_applied_candidate else None
    metadata = _commit_graph_write_with_canonical_metadata(db, workflow)

    return {
        "workflow_id": str(workflow.id),
        "node_id": node_id,
        "applied": True,
        "downstream_compatibility": {
            "state": "unknown",
            "label": "판정 전",
            "message": "후보 설정이 적용되었습니다. 현재 workflow 테스트 실행으로 downstream 결과를 확인하세요.",
        },
        **metadata,
    }


# [NEW] 로그 조회 API
@router.get("/{workflow_id}/runs", response_model=WorkflowRunListResponse)
def get_workflow_runs(
    workflow_id: str,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    status: Optional[RunStatus] = Query(None),
    trigger_mode: Optional[RunTriggerMode] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    특정 워크플로우의 실행 이력 조회
    """
    skip = (page - 1) * limit

    ensure_workflow_permission(db, current_user, workflow_id, "read")

    query = (
        db.query(WorkflowRun)
        .options(noload(WorkflowRun.node_runs))
        .filter(WorkflowRun.workflow_id == workflow_id)
    )
    if status is not None:
        query = query.filter(WorkflowRun.status == status)
    if trigger_mode is not None:
        query = query.filter(WorkflowRun.trigger_mode == trigger_mode)

    total = query.count()
    runs = query.order_by(WorkflowRun.started_at.desc()).offset(skip).limit(limit).all()

    return {"total": total, "items": runs}


@router.get("/{workflow_id}/runs/{run_id}", response_model=WorkflowRunSchema)
def get_workflow_run_detail(
    workflow_id: str,
    run_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    특정 워크플로우 실행 이력 상세 조회
    """
    ensure_workflow_permission(db, current_user, workflow_id, "read")

    run = (
        db.query(WorkflowRun)
        .options(selectinload(WorkflowRun.node_runs))
        .filter(WorkflowRun.id == run_id, WorkflowRun.workflow_id == workflow_id)
        .first()
    )

    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    # [FIX] 중복 실행 로그 정리 (Celery Retry 등으로 인한 중복 제거)
    # node_id별로 가장 최신(started_at 기준) 로그만 필터링하여 반환
    if run.node_runs:
        latest_logs = {}
        for node_run in run.node_runs:
            node_id = node_run.node_id
            # 기존에 저장된 로그가 없거나, 현재 로그가 더 최신이면 업데이트
            if node_id not in latest_logs:
                latest_logs[node_id] = node_run
            else:
                existing = latest_logs[node_id]
                # started_at 비교 (None일 수 있으므로 안전하게 처리)
                current_start = node_run.started_at
                existing_start = existing.started_at

                if current_start and existing_start:
                    if current_start > existing_start:
                        latest_logs[node_id] = node_run
                elif current_start and not existing_start:
                    latest_logs[node_id] = node_run
                # 둘 다 없거나 기존만 있는 경우는 유지

        # 필터링된 로그 리스트로 교체 (started_at 순으로 정렬)
        run.node_runs = sorted(
            latest_logs.values(),
            key=lambda x: (
                x.started_at
                if x.started_at
                else datetime.min.replace(tzinfo=timezone.utc)
            ),
        )

    return run


@router.get(
    "/{workflow_id}/runs/{run_id}/llm-traces", response_model=LLMTraceListResponse
)
def get_workflow_run_llm_traces(
    workflow_id: str,
    run_id: str,
    node_id: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    특정 workflow run에 연결된 LLM usage trace를 조회합니다.
    """
    ensure_workflow_permission(db, current_user, workflow_id, "read")
    try:
        workflow_uuid = UUID(str(workflow_id))
        run_uuid = UUID(str(run_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Run not found")

    result = LLMService.list_workflow_run_llm_traces(
        db,
        workflow_uuid,
        run_uuid,
        node_id=node_id,
        limit=limit,
        offset=offset,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return result


# [NEW] 모니터링 대시보드 통계 API


@router.get("/{workflow_id}/stats", response_model=DashboardStatsResponse)
def get_workflow_stats(
    workflow_id: str,
    days: int = 30,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    import traceback

    try:
        from apps.shared.db.models.workflow_run import WorkflowNodeRun, WorkflowRun
        from apps.shared.schemas.log import (
            DailyRunStat,
            DashboardStatsResponse,
            FailureStat,
            RecentFailure,
            RunCostStat,
            StatsSummary,
        )

        # 1. 권한 체크
        ensure_workflow_permission(db, current_user, workflow_id, "read")

        # 기간 필터 (기본 30일)
        cutoff_date = datetime.now() - timedelta(days=days)

        # base_query는 쿼리 통합으로 더 이상 사용하지 않음

        # === 1. Summary Stats (쿼리 통합 최적화) ===
        # [OPTIMIZATION] 4개의 개별 쿼리를 1개로 통합
        summary_stats = (
            db.query(
                func.count(WorkflowRun.id).label("total_runs"),
                func.sum(
                    func.cast(WorkflowRun.status == RunStatus.SUCCESS, Integer)
                ).label("success_count"),
                func.avg(WorkflowRun.duration).label("avg_duration"),
                func.sum(WorkflowRun.total_cost).label("total_cost"),
                func.sum(WorkflowRun.total_tokens).label("total_tokens"),
            )
            .filter(
                WorkflowRun.workflow_id == workflow_id,
                WorkflowRun.started_at >= cutoff_date,
            )
            .first()
        )

        total_runs = summary_stats.total_runs or 0
        success_count = summary_stats.success_count or 0
        avg_duration = float(summary_stats.avg_duration or 0.0)
        total_cost = float(summary_stats.total_cost or 0.0)
        total_tokens = int(summary_stats.total_tokens or 0)

        summary = StatsSummary(
            totalRuns=total_runs,
            successRate=round((success_count / total_runs * 100), 1)
            if total_runs > 0
            else 0.0,
            avgDuration=round(avg_duration, 2),
            totalCost=round(total_cost, 8),
            avgTokenPerRun=round(total_tokens / total_runs, 1)
            if total_runs > 0
            else 0.0,
            avgCostPerRun=round(total_cost / total_runs, 8) if total_runs > 0 else 0.0,
        )

        # === 2. Runs Over Time (Extended) ===
        runs_over_time = []
        daily_stats = (
            db.query(
                cast(WorkflowRun.started_at, Date).label("date"),
                func.count(WorkflowRun.id),
                func.sum(WorkflowRun.total_cost),
                func.sum(WorkflowRun.total_tokens),
            )
            .filter(
                WorkflowRun.workflow_id == workflow_id,
                WorkflowRun.started_at >= cutoff_date,
            )
            .group_by(cast(WorkflowRun.started_at, Date))
            .order_by(cast(WorkflowRun.started_at, Date))
            .all()
        )

        for row in daily_stats:
            runs_over_time.append(
                DailyRunStat(
                    date=str(row[0]),
                    count=row[1],
                    total_cost=float(row[2] or 0.0),
                    total_tokens=int(row[3] or 0),
                )
            )

        # === 3. Cost Analysis (Min/Max Runs) ===
        # Top 3 Min Cost (Success only, Cost > 0 to avoid boring zeros if wanted, but user asked for min cost. 0 is valid min.)
        # Let's just do Success runs.
        min_cost_runs = []
        min_runs_query = (
            db.query(WorkflowRun)
            .filter(
                WorkflowRun.workflow_id == workflow_id,
                WorkflowRun.status == RunStatus.SUCCESS,
                WorkflowRun.started_at >= cutoff_date,
                # WorkflowRun.total_cost > 0 # Optional
            )
            .order_by(WorkflowRun.total_cost.asc())
            .limit(3)
            .all()
        )

        for run in min_runs_query:
            min_cost_runs.append(
                RunCostStat(
                    run_id=run.id,
                    started_at=run.started_at,
                    total_tokens=run.total_tokens or 0,
                    total_cost=run.total_cost or 0.0,
                )
            )

        # Top 3 Max Cost
        max_cost_runs = []
        max_runs_query = (
            db.query(WorkflowRun)
            .filter(
                WorkflowRun.workflow_id == workflow_id,
                WorkflowRun.status == RunStatus.SUCCESS,
                WorkflowRun.started_at >= cutoff_date,
            )
            .order_by(WorkflowRun.total_cost.desc())
            .limit(3)
            .all()
        )

        for run in max_runs_query:
            max_cost_runs.append(
                RunCostStat(
                    run_id=run.id,
                    started_at=run.started_at,
                    total_tokens=run.total_tokens or 0,
                    total_cost=run.total_cost or 0.0,
                )
            )

        # === 4. Failure Analysis ===
        failure_analysis = []
        failed_nodes = (
            db.query(
                WorkflowNodeRun.node_id,
                WorkflowNodeRun.node_type,
                WorkflowNodeRun.error_message,
                func.count(WorkflowNodeRun.id),
            )
            .join(WorkflowRun)
            .filter(
                WorkflowRun.workflow_id == workflow_id,
                WorkflowNodeRun.status == NodeRunStatus.FAILED,
                WorkflowRun.started_at >= cutoff_date,
            )
            .group_by(
                WorkflowNodeRun.node_id,
                WorkflowNodeRun.node_type,
                WorkflowNodeRun.error_message,
            )
            .order_by(func.count(WorkflowNodeRun.id).desc())
            .limit(5)
            .all()
        )

        for row in failed_nodes:
            failure_analysis.append(
                FailureStat(
                    node_id=row[0],
                    node_name=f"{row[1]} ({row[0]})",
                    count=row[3],
                    reason=str(row[2])[:50] + "..." if row[2] else "Unknown Error",
                    rate="-",
                )
            )

        # === 5. Recent Failures ===
        recent_failures = []
        failed_runs = (
            db.query(WorkflowRun)
            .options(
                selectinload(WorkflowRun.node_runs)
            )  # [FIX] N+1 문제 해결: node_runs 미리 로드
            .filter(
                WorkflowRun.workflow_id == workflow_id,
                WorkflowRun.status == RunStatus.FAILED,
            )
            .order_by(WorkflowRun.started_at.desc())
            .limit(5)
            .all()
        )

        for run in failed_runs:
            failed_node = next(
                (n for n in run.node_runs if n.status == NodeRunStatus.FAILED),
                None,
            )
            recent_failures.append(
                RecentFailure(
                    run_id=str(run.id),
                    failed_at=str(run.started_at),
                    node_id=failed_node.node_id if failed_node else "Unknown",
                    error_message=run.error_message
                    or (failed_node.error_message if failed_node else "Unknown error"),
                )
            )

        return DashboardStatsResponse(
            summary=summary,
            runsOverTime=runs_over_time,
            minCostRuns=min_cost_runs,
            maxCostRuns=max_cost_runs,
            failureAnalysis=failure_analysis,
            recentFailures=recent_failures,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[ERROR] Stats API Failed:\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("", response_model=WorkflowResponse)
@audit(AuditAction.WORKFLOW_CREATE)
def create_workflow(
    request: Request,
    payload: WorkflowCreateRequest,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    새 워크플로우 생성 (인증 필요)
    """
    organization_id = resolve_active_organization_id(
        db, request, x_organization_id, current_user.id
    )
    workflow = WorkflowService.create_workflow(
        db,
        payload,
        user_id=current_user.id,
        organization_id=organization_id,
    )

    return {
        "id": str(workflow.id),
        "app_id": workflow.app_id,
        "created_at": workflow.created_at.isoformat(),
        "updated_at": workflow.updated_at.isoformat(),
    }


@router.get("/{workflow_id}", response_model=WorkflowResponse)
def get_workflow(
    workflow_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    워크플로우 메타데이터 조회 (app_id 포함)
    """
    workflow = ensure_workflow_permission(db, current_user, workflow_id, "read")

    return {
        "id": str(workflow.id),
        "app_id": workflow.app_id,
        "created_at": workflow.created_at.isoformat(),
        "updated_at": workflow.updated_at.isoformat(),
    }


@router.get(
    "/{workflow_id}/permissions/me",
    response_model=WorkflowPermissionResponse,
    response_model_exclude_none=True,
)
def get_my_workflow_permission(
    workflow_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    workflow = ensure_workflow_permission(db, current_user, workflow_id, "read")
    auth_state = get_effective_workflow_auth_state(
        db,
        current_user.id,
        workflow.id,
        organization_id=workflow.organization_id,
    )
    sources = get_workflow_permission_sources(
        db,
        current_user.id,
        workflow.id,
        organization_id=workflow.organization_id,
    )
    return {
        "workflow_id": str(workflow.id),
        "organization_id": str(workflow.organization_id)
        if workflow.organization_id
        else None,
        "auth_state": auth_state,
        "can_read": workflow_auth_state_allows(auth_state, "read"),
        "can_write": workflow_auth_state_allows(auth_state, "write"),
        "can_execute": workflow_auth_state_allows(auth_state, "execute"),
        "can_deploy": workflow_auth_state_allows(auth_state, "deploy"),
        "can_manage": workflow_auth_state_allows(auth_state, "manage"),
        "sources": sources,
    }


@router.get("/app/{app_id}", response_model=List[WorkflowResponse])
def list_workflows_by_app(
    app_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    특정 App의 모든 워크플로우 조회
    """
    # App 권한 확인
    app = db.query(App).filter(App.id == app_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="App not found")

    denial_status = AppService.access_denial_status(db, app, current_user.id, "read")
    if denial_status is not None:
        detail = "Forbidden" if denial_status == 403 else "App not found"
        raise HTTPException(status_code=denial_status, detail=detail)

    # 워크플로우 목록 조회
    workflows = db.query(Workflow).filter(Workflow.app_id == app_id).all()

    return [
        {
            "id": str(w.id),
            "app_id": str(w.app_id),
            "created_at": w.created_at.isoformat(),
            "updated_at": w.updated_at.isoformat(),
        }
        for w in workflows
    ]


@router.post("/{workflow_id}/draft")
@audit(AuditAction.WORKFLOW_UPDATE, target_param="workflow_id")
def sync_draft_workflow(
    workflow_id: str,
    request: Request,
    payload: WorkflowDraftRequest,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    프론트엔드로부터 워크플로우 초안 데이터를 받아 PostgreSQL에 저장합니다. (인증 필요)

    Args:
        workflow_id: 워크플로우 ID (URL 경로에서 가져옴)
        payload: 워크플로우 데이터 (노드, 엣지, 뷰포트)
        db: 데이터베이스 세션 (의존성 주입)
        current_user: 현재 로그인한 사용자
    """
    active_organization_id = resolve_active_organization_id(
        db,
        request,
        x_organization_id,
        current_user.id,
    )
    workflow = ensure_workflow_permission(
        db,
        current_user,
        workflow_id,
        "write",
    )
    if workflow.organization_id != active_organization_id:
        raise HTTPException(status_code=404, detail="Workflow not found")

    return WorkflowService.save_draft(
        db, workflow_id, payload, user_id=str(current_user.id)
    )


@router.post(
    "/{workflow_id}/node-secrets",
    response_model=WorkflowNodeSecretWriteResponse,
)
@audit(AuditAction.WORKFLOW_UPDATE, target_param="workflow_id")
def store_workflow_node_secret(
    workflow_id: str,
    request: Request,
    payload: WorkflowNodeSecretWriteRequest,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    active_organization_id = resolve_active_organization_id(
        db,
        request,
        x_organization_id,
        current_user.id,
    )
    workflow = ensure_workflow_permission(
        db,
        current_user,
        workflow_id,
        "write",
    )
    if workflow.organization_id != active_organization_id:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return WorkflowService.store_node_secret(
        db,
        workflow_id=workflow_id,
        active_organization_id=active_organization_id,
        user_id=current_user.id,
        node_id=payload.node_id,
        node_type=payload.node_type,
        parameter_key=payload.parameter_key,
        secret_value=payload.secret_value.get_secret_value(),
    )


@router.get("/{workflow_id}/draft")
def get_draft_workflow(
    workflow_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    PostgreSQL에서 워크플로우 초안 데이터를 조회합니다. (인증 필요)
    """
    ensure_workflow_permission(db, current_user, workflow_id, "read")

    return WorkflowService.get_draft(db, workflow_id, include_metadata=True)


@router.post("/{workflow_id}/compare")
def compare_workflow_variants(
    workflow_id: str,
    request_body: WorkflowCompareRequest,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    workflow = ensure_workflow_permission(db, current_user, workflow_id, "execute")
    _ensure_workflow_matches_active_organization(
        db,
        request,
        current_user,
        workflow,
        x_organization_id,
    )
    graph = WorkflowService.get_draft(db, workflow_id)
    if not graph:
        raise HTTPException(
            status_code=404, detail=f"Workflow '{workflow_id}' draft not found"
        )

    graph = _bind_and_preflight_authenticated_graph(
        db,
        workflow=workflow,
        graph=graph,
        principal_id=current_user.id,
    )

    base_context = {
        "user_id": str(current_user.id),
        "execution_subject": {
            "type": "user",
            "id": str(current_user.id),
        },
        "workflow_id": workflow_id,
        "organization_id": (
            str(workflow.organization_id) if workflow.organization_id else None
        ),
        "app_id": str(workflow.app_id),
        "trigger_mode": "manual_compare",
        "request_id": request.headers.get("x-request-id"),
        "correlation_id": request.headers.get("x-correlation-id"),
        "compare": {
            "node_id": request_body.node_id,
            "compare_type": request_body.compare_type,
        },
    }

    def run_variant(label: str, value: str) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            patched_graph = _patch_compare_graph(
                graph, request_body.node_id, request_body.compare_type, value
            )
            task = send_workflow_task(
                celery_app,
                "workflow.execute",
                args=[patched_graph, request_body.inputs, base_context],
                kwargs={"is_deployed": False},
            )
            task_result = task.get(timeout=600)
            latency_ms = int((time.perf_counter() - started) * 1000)
            if task_result.get("status") == "success":
                return _format_compare_variant(
                    label=label,
                    value=value,
                    node_id=request_body.node_id,
                    status="success",
                    outputs=task_result.get("result", {}),
                    latency_ms=latency_ms,
                )
            error_detail = _safe_task_error(task_result.get("error"))
            return _format_compare_variant(
                label=label,
                value=value,
                node_id=request_body.node_id,
                status="failed",
                outputs=task_result.get("result", {}),
                error=(
                    error_detail["code"]
                    if error_detail
                    else "Workflow execution failed"
                ),
                error_detail=error_detail,
                latency_ms=latency_ms,
            )
        except Exception as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            return _format_compare_variant(
                label=label,
                value=value,
                node_id=request_body.node_id,
                status="failed",
                error=getattr(exc, "code", "workflow.execution_failed"),
                latency_ms=latency_ms,
            )

    return {
        "workflow_id": workflow_id,
        "node_id": request_body.node_id,
        "compare_type": request_body.compare_type,
        "variants": [
            run_variant("A", request_body.left),
            run_variant("B", request_body.right),
        ],
    }


@router.post("/{workflow_id}/execute")
async def execute_workflow(
    workflow_id: str,
    request: Request,
    user_input: dict = {},
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    PostgreSQL에서 워크플로우 초안 데이터를 조회하고, Celery 태스크로 실행합니다. (인증 필요)
    """
    # 1. 권한 확인
    workflow = ensure_workflow_permission(db, current_user, workflow_id, "execute")
    _ensure_workflow_matches_active_organization(
        db,
        request,
        current_user,
        workflow,
        x_organization_id,
    )
    from apps.gateway.services.deployment_service import DeploymentService
    from apps.shared.services.workflow_configuration_preflight import (
        WorkflowConfigurationPreflightError,
        enforce_workflow_configuration_preflight,
    )

    try:
        enforce_workflow_configuration_preflight(workflow.graph, surface="test")
    except WorkflowConfigurationPreflightError as exc:
        raise DeploymentService.workflow_configuration_preflight_blocked(exc) from exc

    # 1-1. 예산 초과 차단 — dispatch try 블록 밖이어야 429가 500으로 감싸이지 않는다.
    WorkflowBudgetService.ensure_workflow_budget_allows_execution(
        db,
        workflow_id=workflow_id,
        trigger_mode="test",
        actor_id=current_user.id,
    )

    memory_mode_enabled = False
    if isinstance(user_input, dict):
        # 프론트 토글 상태가 실행 입력에 섞여 올 수 있으므로 분리해서 컨텍스트에만 전달
        memory_mode_enabled = bool(user_input.pop("memory_mode", False))

    # 2. 데이터 조회 및 Celery 태스크 호출
    try:
        graph = WorkflowService.get_draft(db, workflow_id)
        if not graph:
            raise HTTPException(
                status_code=404, detail=f"Workflow '{workflow_id}' draft not found"
            )
        graph = _bind_and_preflight_authenticated_graph(
            db,
            workflow=workflow,
            graph=graph,
            principal_id=current_user.id,
        )

        # execution_context 구성
        execution_context = {
            "user_id": str(current_user.id),
            "execution_subject": {
                "type": "user",
                "id": str(current_user.id),
            },
            "workflow_id": workflow_id,
            "organization_id": (
                str(workflow.organization_id) if workflow.organization_id else None
            ),
            "app_id": str(workflow.app_id),
            "memory_mode": memory_mode_enabled,
            "request_id": _request_id_from_request(request),
            "correlation_id": request.headers.get("x-correlation-id"),
        }

        # Celery 태스크 호출 (workflow.execute)
        task = send_workflow_task(
            celery_app,
            "workflow.execute",
            args=[graph, user_input, execution_context],
            kwargs={"is_deployed": False},
        )

        # 결과 대기 (타임아웃 10분)
        result = task.get(timeout=600)

        if result.get("status") == "success":
            return result.get("result", {})
        else:
            raise HTTPException(
                status_code=500,
                detail=_safe_task_error(result.get("error"))
                or "Workflow execution failed",
            )

    except CeleryTimeoutError:
        raise HTTPException(status_code=504, detail="Workflow execution timed out")
    except HTTPException:
        raise
    except ValueError:
        # 노드 검증 실패 등의 입력 오류
        raise HTTPException(status_code=400, detail="Workflow validation failed")
    except NotImplementedError:
        # 미지원 노드 등
        raise HTTPException(status_code=501, detail="Workflow feature is not supported")
    except Exception:
        # 그 외 서버 에러
        raise HTTPException(status_code=500, detail="Workflow execution failed")


@router.post("/{workflow_id}/stream")
async def stream_workflow(
    workflow_id: str,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    워크플로우를 실행하고 실행 과정을 SSE(Server-Sent Events)로 스트리밍합니다.

    [완전 분리 버전] Gateway에서 run_id를 생성하고, Celery 태스크를 호출한 후
    Redis Pub/Sub 채널을 구독하여 이벤트를 SSE로 전달합니다.

    multipart/form-data 지원:
    - inputs: JSON 문자열 (일반 입력값)
    - file_변수명: 업로드된 파일들
    """
    import uuid

    memory_mode_enabled = False
    # 1. 권한 확인
    workflow = ensure_workflow_permission(db, current_user, workflow_id, "execute")
    _ensure_workflow_matches_active_organization(
        db,
        request,
        current_user,
        workflow,
        x_organization_id,
    )

    # 1-1. 예산 초과 차단 — SSE 스트림이 시작되기 전에 429로 끝낸다 (BGT-REQ-030).
    WorkflowBudgetService.ensure_workflow_budget_allows_execution(
        db,
        workflow_id=workflow_id,
        trigger_mode="test",
        actor_id=current_user.id,
    )

    # 2. Request에서 FormData 파싱
    content_type = request.headers.get("content-type", "")
    user_input = {}
    graph_snapshot = None
    use_active_deployment_routing_policy = False

    if "multipart/form-data" in content_type:
        # FormData 파싱
        form = await request.form()

        # inputs 필드에서 JSON 파싱
        inputs_str = form.get("inputs", "{}")
        try:
            user_input = json.loads(inputs_str) if isinstance(inputs_str, str) else {}
        except json.JSONDecodeError:
            user_input = {}

        graph_snapshot_str = form.get("graph_snapshot")
        if isinstance(graph_snapshot_str, str) and graph_snapshot_str.strip():
            try:
                graph_snapshot = json.loads(graph_snapshot_str)
            except json.JSONDecodeError:
                raise HTTPException(
                    status_code=400, detail="Invalid graph_snapshot JSON"
                )

        # 토글 값 분리 (문자열 true/false 허용)
        memory_mode_enabled = (
            str(form.get("memory_mode", user_input.pop("memory_mode", ""))).lower()
            == "true"
        )
        use_active_deployment_routing_policy = (
            str(form.get("use_active_deployment_routing_policy", "")).lower() == "true"
        )
    else:
        # JSON 방식 (기존)
        try:
            body = await request.json()
            if isinstance(body, dict) and (
                "inputs" in body or "graph_snapshot" in body
            ):
                raw_inputs = body.get("inputs", {})
                user_input = raw_inputs if isinstance(raw_inputs, dict) else {}
                graph_snapshot = body.get("graph_snapshot")
                use_active_deployment_routing_policy = bool(
                    body.get("use_active_deployment_routing_policy", False)
                )
            else:
                user_input = body if isinstance(body, dict) else {}

            if isinstance(user_input, dict):
                memory_mode_enabled = bool(user_input.pop("memory_mode", False))
        except Exception:
            user_input = {}

    if graph_snapshot is not None and not isinstance(graph_snapshot, dict):
        raise HTTPException(status_code=400, detail="graph_snapshot must be an object")

    # 3. 실행 그래프 결정
    graph = graph_snapshot or WorkflowService.get_draft(db, workflow_id)
    if not graph:
        raise HTTPException(
            status_code=404, detail=f"Workflow '{workflow_id}' draft not found"
        )
    graph = _bind_and_preflight_authenticated_graph(
        db,
        workflow=workflow,
        graph=graph,
        principal_id=current_user.id,
    )

    # 4. [NEW] Gateway에서 run_id 생성 (Celery 태스크에 전달)
    external_run_id = str(uuid.uuid4())

    # 5. 실행 컨텍스트 준비
    execution_context = {
        "user_id": str(current_user.id),
        "execution_subject": {
            "type": "user",
            "id": str(current_user.id),
        },
        "workflow_id": workflow_id,
        "organization_id": (
            str(workflow.organization_id) if workflow.organization_id else None
        ),
        "app_id": str(workflow.app_id),
        "memory_mode": memory_mode_enabled,
        "trigger_mode": "manual",  # 테스트 실행
        "request_id": _request_id_from_request(request),
        "correlation_id": request.headers.get("x-correlation-id"),
    }
    if use_active_deployment_routing_policy:
        execution_context.update(
            _test_routing_policy_context(db, workflow=workflow, graph=graph)
        )

    # 7. StreamingResponse 반환
    return StreamingResponse(
        _stream_workflow_events(
            external_run_id=external_run_id,
            celery=celery_app,
            graph=graph,
            user_input=user_input,
            execution_context=execution_context,
        ),
        media_type="text/event-stream",
    )
