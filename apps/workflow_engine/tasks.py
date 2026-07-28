"""
Workflow-Engine Celery 태스크 정의
워크플로우 실행을 비동기적으로 처리

[GEVENT] WorkflowEngine이 동기화되어 asyncio가 더 이상 필요하지 않음.
"""

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict

from celery.exceptions import Retry

from apps.shared.celery_app import celery_app
from apps.shared.db.session import SessionLocal
from apps.shared.domain.deployment_runtime_policy import (
    DeploymentRuntimePolicy,
    is_deployment_type_allowed_for_trigger,
)
from apps.shared.domain.public_chat_conversation import (
    PublicChatConversationContractError,
    resolve_public_chat_conversation_contract,
)
from apps.shared.domain.public_chat_history import (
    PublicChatHistoryError,
    remaining_public_chat_history_tokens,
)
from apps.shared.domain.schedule_dispatch import (
    REASON_EXECUTION_FAILED_AFTER_ADMISSION,
    REASON_EXECUTION_OUTCOME_UNKNOWN,
)
from apps.shared.domain.workflow_node_binding import (
    canonical_snapshot_sha256,
    graph_has_external_effect,
    workflow_node_references,
)
from apps.shared.services.schedule_dispatch_observability import (
    emit_schedule_dispatch_signal,
)
from apps.shared.services.workflow_task_publisher import (
    RedactedWorkflowTask,
    send_workflow_task,
)
from apps.shared.services.public_chat_history_transient_store import (
    PublicChatHistoryTransientStoreError,
    consume_public_chat_history,
)
from apps.shared.services.workflow_node_catalog import node_side_effect_mapping
from apps.shared.services.workflow_configuration_preflight import (
    WorkflowConfigurationPreflightError,
    enforce_workflow_configuration_preflight,
)
from apps.workflow_engine import mail_credential_startup  # noqa: F401
from apps.workflow_engine import llm_credential_startup  # noqa: F401
from apps.workflow_engine import outbound_proxy_startup  # noqa: F401
from apps.workflow_engine.runtime_policy import get_deployment_runtime_policy
from apps.workflow_engine.schedule_dispatch_settings import (
    get_schedule_dispatch_settings,
)
from apps.workflow_engine.domain.external_effect import (
    ExternalEffectError,
    ExternalEffectRetrySignal,
)
from apps.workflow_engine.workflow.errors import NonRetryableWorkflowError

logger = logging.getLogger(__name__)

_SCHEDULE_FINALIZATION_MAX_ATTEMPTS = 3

_DEPLOYMENT_CONTEXT_PASSTHROUGH_KEYS = frozenset(
    {
        "conversation_id",
        "correlation_id",
        "request_id",
        "trace_metadata",
        "trigger_mode",
        "workflow_task_id",
        "execution_id",
        "deployment_version",
        "snapshot_sha256",
    }
)


def _external_effect_error_result(error: ExternalEffectError) -> Dict[str, Any]:
    return {"status": "error", "error": error.to_payload()}


def _safe_retry(self, error: Exception):
    code = getattr(error, "code", "workflow.execution_failed")
    raise self.retry(
        exc=Exception(str(code)),
        countdown=2**self.request.retries,
    )


def _workflow_task_deadline(
    public_request_deadline: datetime | None = None,
    *,
    now: datetime | None = None,
    monotonic_now: float | None = None,
) -> float:
    hard_limit = celery_app.conf.task_time_limit
    if not isinstance(hard_limit, (int, float)) or hard_limit <= 0:
        raise RuntimeError("workflow task hard time limit is invalid")
    safety_margin = min(1.0, float(hard_limit) * 0.1)
    current_monotonic = time.monotonic() if monotonic_now is None else monotonic_now
    task_deadline = current_monotonic + float(hard_limit) - safety_margin
    if public_request_deadline is None:
        return task_deadline

    current = now or datetime.now(timezone.utc)
    remaining_seconds = (
        public_request_deadline.astimezone(timezone.utc)
        - current.astimezone(timezone.utc)
    ).total_seconds()
    return min(task_deadline, current_monotonic + max(0.0, remaining_seconds))


def _enforce_public_request_deadline(
    execution_context: Dict[str, Any],
    *,
    now: datetime | None = None,
) -> datetime | None:
    is_public_transient = (
        "public_chat_history_ref" in execution_context
        or execution_context.get("public_chat_stateless_compatibility") is True
        or execution_context.get("execution_actor") == {"type": "public"}
    )
    if not is_public_transient:
        return None
    raw_deadline = execution_context.get("public_request_deadline_at")
    if not isinstance(raw_deadline, str):
        raise NonRetryableWorkflowError("conversation.request_deadline_invalid")
    try:
        deadline = datetime.fromisoformat(raw_deadline)
    except ValueError:
        raise NonRetryableWorkflowError(
            "conversation.request_deadline_invalid"
        ) from None
    if deadline.tzinfo is None or deadline.utcoffset() is None:
        raise NonRetryableWorkflowError("conversation.request_deadline_invalid")
    current = now or datetime.now(timezone.utc)
    if deadline.astimezone(timezone.utc) <= current.astimezone(timezone.utc):
        raise NonRetryableWorkflowError("conversation.request_expired")
    return deadline.astimezone(timezone.utc)


def _cleanup_execution_resources(engine, session, *, label: str) -> None:
    if engine is not None:
        try:
            engine.cleanup()
        except Exception as exc:
            logger.warning(
                "%s engine cleanup failed: error_type=%s",
                label,
                type(exc).__name__,
            )
    if session is not None:
        try:
            session.close()
        except Exception as exc:
            logger.warning(
                "%s session close failed: error_type=%s",
                label,
                type(exc).__name__,
            )


def _enforce_runtime_configuration(graph: Dict[str, Any], *, surface: str) -> None:
    try:
        enforce_workflow_configuration_preflight(graph, surface=surface)
    except WorkflowConfigurationPreflightError as exc:
        raise NonRetryableWorkflowError(str(exc)) from exc


def _engine_workflow_run_id(engine) -> str | None:
    """API 실험 도구가 결과를 정확한 실행 로그와 연결할 safe 식별자를 반환한다."""
    run_id = getattr(getattr(engine, "logger", None), "workflow_run_id", None)
    return str(run_id) if run_id is not None else None


@celery_app.task(
    name="workflow.model_routing.bootstrap_policy",
    bind=True,
    max_retries=3,
    base=RedactedWorkflowTask,
)
def bootstrap_model_routing_policy(self, policy_id: str):
    """배포 직후 Judge-first 정책 상태와 로컬 학습 조건을 정합화한다."""
    from apps.workflow_engine.services.model_routing_policy_refresh_task import (
        PersistedModelRoutingPolicyRefreshService,
    )

    session = SessionLocal()
    try:
        update = PersistedModelRoutingPolicyRefreshService.refresh(
            session,
            policy_id=policy_id,
            trigger="deployment_bootstrap",
        )
        session.commit()
        return {
            "status": update.status if update is not None else "not_found",
            "policy_id": policy_id,
            "update_id": str(update.id) if update is not None else None,
        }
    except Exception as exc:
        session.rollback()
        logger.error("[Model-Routing] deployment bootstrap failed: %s", exc)
        raise self.retry(exc=exc, countdown=min(2 ** (self.request.retries + 1), 30))
    finally:
        session.close()


@celery_app.task(
    name="workflow.model_routing.record_run",
    bind=True,
    max_retries=3,
    base=RedactedWorkflowTask,
)
def record_model_routing_operational_run(self, workflow_run_id: str):
    """완료된 LLM node run을 정책 갱신 카운터에 반영하고 필요할 때만 refresh task를 예약한다."""
    from apps.workflow_engine.services.model_routing_policy_store import (
        ModelRoutingPolicyStore,
    )

    session = SessionLocal()
    try:
        policy_ids = ModelRoutingPolicyStore.record_completed_deployed_run(
            session,
            workflow_run_id=workflow_run_id,
        )
        learning_learner_ids = (
            ModelRoutingPolicyStore.pending_learning_learner_ids_for_run(
                session,
                workflow_run_id=workflow_run_id,
            )
        )
        session.commit()
        for learner_id in learning_learner_ids:
            send_workflow_task(
                celery_app,
                "workflow.model_routing.train_local_router",
                args=[str(learner_id), False],
            )
        for policy_id in policy_ids:
            send_workflow_task(
                celery_app,
                "workflow.model_routing.refresh_policy",
                args=[str(policy_id), "score_change"],
            )
        return {
            "status": "success",
            "scheduled_policy_ids": [str(item) for item in policy_ids],
        }
    except Exception as exc:
        session.rollback()
        logger.error("[Model-Routing] run event record failed: %s", exc)
        raise self.retry(exc=exc, countdown=min(2 ** (self.request.retries + 1), 30))
    finally:
        session.close()


@celery_app.task(
    name="workflow.model_routing.train_local_router",
    bind=True,
    max_retries=3,
    base=RedactedWorkflowTask,
)
def train_model_routing_local_router(
    self,
    learner_id: str,
    force: bool = False,
):
    """Judge label을 요청 처리와 분리해 작은 batch로 학습한다."""

    from apps.workflow_engine.services.model_routing_learning_batch import (
        ModelRoutingLearningBatchService,
    )

    session = SessionLocal()
    try:
        result = ModelRoutingLearningBatchService.train_pending(
            session,
            learner_id=learner_id,
            force=force,
        )
        session.commit()
        if result.deferred_seconds is not None:
            send_workflow_task(
                celery_app,
                "workflow.model_routing.train_local_router",
                args=[learner_id, True],
                countdown=result.deferred_seconds,
            )
            status = "deferred"
        elif result.remaining_count > 0:
            send_workflow_task(
                celery_app,
                "workflow.model_routing.train_local_router",
                args=[learner_id, True],
            )
            status = "continued"
        else:
            status = "success"
        return {
            "status": status,
            "processed_count": result.processed_count,
            "remaining_count": result.remaining_count,
        }
    except Exception as exc:
        session.rollback()
        logger.error("[Model-Routing] local training failed: %s", exc)
        raise self.retry(exc=exc, countdown=min(2 ** (self.request.retries + 1), 30))
    finally:
        session.close()


@celery_app.task(
    name="workflow.model_routing.refresh_policy",
    bind=True,
    max_retries=2,
    base=RedactedWorkflowTask,
)
def refresh_model_routing_policy(self, policy_id: str, trigger: str = "manual_refresh"):
    """누적된 Judge 선택과 운영 품질로 local-first 전환 여부를 갱신한다."""
    from apps.workflow_engine.services.model_routing_policy_refresh_task import (
        PersistedModelRoutingPolicyRefreshService,
    )

    session = SessionLocal()
    try:
        if trigger in {"auto_n_runs", "score_change"}:
            from apps.workflow_engine.services.model_routing_policy_store import (
                ModelRoutingPolicyStore,
            )

            if (
                ModelRoutingPolicyStore.claim_pending_auto_refresh(
                    session,
                    policy_id=policy_id,
                )
                is None
            ):
                session.rollback()
                return {"status": "skipped", "update_id": None, "result": None}
        update = PersistedModelRoutingPolicyRefreshService.refresh(
            session,
            policy_id=policy_id,
            trigger=trigger,
        )
        session.commit()
        return {
            "status": "success" if update is not None else "not_found",
            "update_id": str(update.id) if update is not None else None,
            "result": update.status if update is not None else None,
            "validation_batch_id": None,
        }
    except Exception as exc:
        session.rollback()
        logger.error("[Model-Routing] policy refresh failed: %s", exc)
        raise self.retry(exc=exc, countdown=min(2 ** (self.request.retries + 1), 30))
    finally:
        session.close()


def _sync_skipped_result(reason: str) -> Dict[str, Any]:
    """Knowledge sync를 실행하지 않았음을 task 응답에 안전하게 표시한다."""
    return {
        "synced_count": 0,
        "failed": [],
        "skipped": True,
        "reason": reason,
    }


def _resolve_user_execution_subject_id(
    execution_context: Dict[str, Any],
) -> uuid.UUID | None:
    """Private/source-backed Knowledge sync에 사용할 user execution_subject만 해석한다."""
    subject = execution_context.get("execution_subject")
    if not isinstance(subject, dict):
        return None

    subject_type = subject.get("subject_type") or subject.get("type") or "user"
    if subject_type != "user":
        return None

    subject_id = subject.get("subject_id") or subject.get("id")
    try:
        return uuid.UUID(str(subject_id))
    except (TypeError, ValueError):
        return None


def _sync_knowledge_bases_for_execution_subject(
    session,
    graph: Dict[str, Any],
    execution_context: Dict[str, Any],
) -> Dict[str, Any]:
    # Anonymous public-only RAG는 이미 색인된 public KB만 검색한다.
    # Private/source-backed sync에는 명시적인 user execution_subject가 필요하며,
    # workflow owner/app creator/user_id를 데이터 접근 주체로 대체하지 않는다.
    subject_id = _resolve_user_execution_subject_id(execution_context)
    if subject_id is None:
        return _sync_skipped_result("anonymous_public_only")

    from apps.workflow_engine.services.sync_service import SyncService

    syncer = SyncService(
        db=session,
        user_id=subject_id,
        organization_id=execution_context.get("organization_id"),
    )
    return syncer.sync_knowledge_bases(graph)


class PermanentDeploymentExecutionError(ValueError):
    """Non-retryable deployment runtime contract violation."""


def _deployment_type_allowed_for_trigger(
    deployment_type: Any,
    trigger_mode: Any,
    *,
    runtime_policy: DeploymentRuntimePolicy,
) -> bool:
    return is_deployment_type_allowed_for_trigger(
        deployment_type,
        trigger_mode,
        policy=runtime_policy,
    )


def _canonical_deployment_execution_context(
    queued_context: Dict[str, Any],
    *,
    deployment: Any,
    app: Any,
    require_execution_id: bool = True,
) -> Dict[str, Any]:
    """Rebuild tenant/resource identity from canonical deployment rows."""
    context = {
        key: queued_context[key]
        for key in _DEPLOYMENT_CONTEXT_PASSTHROUGH_KEYS
        if key in queued_context
    }
    canonical_user_id = getattr(app, "created_by", None) or getattr(
        deployment, "created_by", None
    )
    context.update(
        {
            "user_id": str(canonical_user_id) if canonical_user_id else None,
            "workflow_id": str(app.workflow_id) if app.workflow_id else None,
            "organization_id": (
                str(app.organization_id) if app.organization_id else None
            ),
            "app_id": str(deployment.app_id),
            "deployment_id": str(deployment.id),
            "workflow_version": deployment.version,
        }
    )
    if require_execution_id:
        context["execution_id"] = _canonical_execution_id(context)
    return context


def _canonical_execution_id(context: Dict[str, Any]) -> str:
    try:
        return str(uuid.UUID(str(context.get("execution_id"))))
    except (TypeError, ValueError, AttributeError):
        raise PermanentDeploymentExecutionError(
            "workflow execution identity is invalid"
        ) from None


def _canonical_workflow_execution_context(
    session,
    queued_context: Dict[str, Any],
) -> Dict[str, Any]:
    from apps.shared.db.models.app import App
    from apps.shared.db.models.workflow import Workflow

    try:
        workflow_id = uuid.UUID(str(queued_context.get("workflow_id")))
    except (TypeError, ValueError):
        raise PermanentDeploymentExecutionError(
            "workflow execution identity is invalid"
        ) from None
    workflow = session.query(Workflow).filter(Workflow.id == workflow_id).first()
    if workflow is None:
        raise PermanentDeploymentExecutionError(
            "workflow execution identity is invalid"
        )
    app = session.query(App).filter(App.id == workflow.app_id).first()
    if app is None or app.workflow_id != workflow.id:
        raise PermanentDeploymentExecutionError(
            "workflow execution identity is invalid"
        )
    context = dict(queued_context)
    context.update(
        {
            "workflow_id": str(workflow.id),
            "organization_id": (
                str(workflow.organization_id) if workflow.organization_id else None
            ),
            "app_id": str(app.id),
        }
    )
    context["execution_id"] = _canonical_execution_id(context)
    return context


def _canonical_deployed_graph_execution_context(
    session,
    queued_context: Dict[str, Any],
    graph: Dict[str, Any],
) -> Dict[str, Any]:
    from apps.shared.db.models.workflow_deployment import WorkflowDeployment

    context = _canonical_workflow_execution_context(session, queued_context)
    try:
        deployment_id = uuid.UUID(str(queued_context.get("deployment_id")))
    except (TypeError, ValueError):
        raise PermanentDeploymentExecutionError(
            "deployed workflow identity is not frozen"
        ) from None
    deployment = (
        session.query(WorkflowDeployment)
        .filter(WorkflowDeployment.id == deployment_id)
        .first()
    )
    if (
        deployment is None
        or str(deployment.app_id) != str(context["app_id"])
        or str(queued_context.get("workflow_version")) != str(deployment.version)
        or not isinstance(deployment.graph_snapshot, dict)
        or canonical_snapshot_sha256(graph)
        != canonical_snapshot_sha256(deployment.graph_snapshot)
    ):
        raise PermanentDeploymentExecutionError(
            "deployed workflow identity is not frozen"
        )
    context["deployment_id"] = str(deployment.id)
    context["workflow_version"] = deployment.version
    for key in (
        "execution_actor",
        "public_chat_history_consumer_ref",
        "public_chat_history_token_budget",
        "public_chat_stateless_compatibility",
        "suppress_content_persistence",
    ):
        context.pop(key, None)

    has_public_history = (
        "public_chat_history_ref" in queued_context
        or "public_chat_history" in queued_context
    )
    stateless_compatibility = (
        queued_context.get("public_chat_stateless_compatibility") is True
    )
    if has_public_history or stateless_compatibility:
        deployment_type = getattr(deployment.type, "value", deployment.type)
        if (
            deployment_type != "chatbot"
            or queued_context.get("trigger_mode") != "app"
            or queued_context.get("execution_subject") is not None
        ):
            raise PermanentDeploymentExecutionError(
                "public conversation execution boundary is invalid"
            )
        context["execution_actor"] = {"type": "public"}
        context["suppress_content_persistence"] = True
        if has_public_history:
            try:
                contract = resolve_public_chat_conversation_contract(
                    getattr(deployment, "config", None),
                    deployment.graph_snapshot,
                    required=True,
                )
            except PublicChatConversationContractError:
                raise PermanentDeploymentExecutionError(
                    "public conversation consumer mapping is invalid"
                ) from None
            context["public_chat_history_consumer_ref"] = contract.history_consumer_ref
        else:
            context["public_chat_stateless_compatibility"] = True
    return context


def _graph_requires_frozen_deployment(graph: Dict[str, Any]) -> bool:
    return bool(workflow_node_references(graph)) or graph_has_external_effect(
        graph,
        node_side_effect_mapping(),
    )


@celery_app.task(
    name="workflow.execute", bind=True, max_retries=3, base=RedactedWorkflowTask
)
def execute_workflow(
    self,
    graph: Dict[str, Any],
    user_input: Dict[str, Any],
    execution_context: Dict[str, Any],
    is_deployed: bool = False,
):
    """
    워크플로우 비동기 실행

    [GEVENT] WorkflowEngine이 동기화되어 단순화됨.

    Args:
        graph: 워크플로우 그래프 데이터
        user_input: 사용자 입력
        execution_context: 실행 컨텍스트
        is_deployed: 배포 모드 여부

    Returns:
        워크플로우 실행 결과
    """
    from apps.workflow_engine.workflow.core.workflow_engine import WorkflowEngine

    queued_context = dict(execution_context or {})
    public_request_deadline = _enforce_public_request_deadline(queued_context)
    task_deadline = _workflow_task_deadline(public_request_deadline)
    session = None
    engine = None
    sync_result = {}
    public_history_consumed = False

    try:
        session = SessionLocal()
        execution_context = (
            _canonical_deployed_graph_execution_context(
                session,
                queued_context,
                graph,
            )
            if is_deployed
            else _canonical_workflow_execution_context(session, queued_context)
        )
        _enforce_runtime_configuration(graph, surface="workflow_engine_run")

        # Knowledge Base 동기화
        try:
            sync_result = _sync_knowledge_bases_for_execution_subject(
                session,
                graph,
                execution_context,
            )
        except Exception as e:
            logger.error(
                "Workflow knowledge sync failed: error_type=%s",
                type(e).__name__,
            )

        history_reference = execution_context.pop("public_chat_history_ref", None)
        if history_reference is not None:
            try:
                history_token_budget = remaining_public_chat_history_tokens(user_input)
            except PublicChatHistoryError as error:
                raise NonRetryableWorkflowError(error.code) from None
            _enforce_public_request_deadline(execution_context)
            try:
                public_chat_history = consume_public_chat_history(history_reference)
            except PublicChatHistoryTransientStoreError:
                raise NonRetryableWorkflowError(
                    "conversation.history_unavailable"
                ) from None
            if public_chat_history is None:
                raise NonRetryableWorkflowError("conversation.history_unavailable")
            public_history_consumed = True
            execution_context["public_chat_history"] = [
                dict(message) for message in public_chat_history
            ]
            execution_context["public_chat_history_token_budget"] = history_token_budget

        _enforce_public_request_deadline(execution_context)

        engine = WorkflowEngine(
            graph=graph,
            user_input=user_input,
            execution_context=execution_context,
            is_deployed=is_deployed,
            db=session,
            task_deadline=task_deadline,
        )

        # [GEVENT] 직접 동기 호출 - asyncio 불필요
        result = engine.execute()
        return {
            "status": "success",
            "result": result,
            "sync_status": sync_result,
            "run_id": _engine_workflow_run_id(engine),
        }

    except ExternalEffectRetrySignal as e:
        logger.warning("Workflow external effect retry requested: code=%s", e.code)
        if public_history_consumed:
            raise NonRetryableWorkflowError(
                "conversation.history_replay_required"
            ) from e
        _safe_retry(self, e)
    except ExternalEffectError as e:
        logger.warning("Workflow external effect stopped: code=%s", e.code)
        return _external_effect_error_result(e)
    except (PermanentDeploymentExecutionError, NonRetryableWorkflowError) as e:
        logger.error(
            "Workflow execution blocked: error_type=%s",
            type(e).__name__,
        )
        raise
    except Exception as e:
        logger.error("Workflow execution failed: error_type=%s", type(e).__name__)
        if public_history_consumed:
            raise NonRetryableWorkflowError(
                "conversation.history_replay_required"
            ) from e
        _safe_retry(self, e)
    finally:
        _cleanup_execution_resources(engine, session, label="workflow")


@celery_app.task(
    name="workflow.execute_deployed",
    bind=True,
    max_retries=3,
    base=RedactedWorkflowTask,
)
def execute_deployed_workflow(
    self,
    workflow_id: str,
    user_input: Dict[str, Any],
    execution_context: Dict[str, Any],
):
    """
    배포된 워크플로우 실행

    [GEVENT] WorkflowEngine이 동기화되어 단순화됨.
    """
    from apps.shared.db.models.app import App
    from apps.shared.db.models.workflow_deployment import WorkflowDeployment
    from apps.workflow_engine.workflow.core.workflow_engine import WorkflowEngine

    task_deadline = _workflow_task_deadline()
    session = SessionLocal()
    engine = None

    try:
        queued_context = dict(execution_context or {})
        app = session.query(App).filter(App.workflow_id == workflow_id).first()
        if not app:
            raise PermanentDeploymentExecutionError("deployed workflow is unavailable")
        active_deployment = (
            session.query(WorkflowDeployment)
            .filter(
                WorkflowDeployment.id == app.active_deployment_id,
                WorkflowDeployment.app_id == app.id,
                WorkflowDeployment.is_active.is_(True),
            )
            .first()
        )
        if active_deployment is None:
            raise PermanentDeploymentExecutionError("deployed workflow is unavailable")
        queued_deployment_id = queued_context.get("deployment_id")
        if queued_deployment_id is None:
            deployment = active_deployment
        else:
            try:
                frozen_deployment_id = uuid.UUID(str(queued_deployment_id))
            except (TypeError, ValueError):
                raise PermanentDeploymentExecutionError(
                    "deployed workflow identity is not frozen"
                ) from None
            deployment = (
                session.query(WorkflowDeployment)
                .filter(
                    WorkflowDeployment.id == frozen_deployment_id,
                    WorkflowDeployment.app_id == app.id,
                )
                .first()
            )
        if not deployment or not deployment.graph_snapshot:
            raise PermanentDeploymentExecutionError("deployed workflow is unavailable")
        graph = deployment.graph_snapshot
        if _graph_requires_frozen_deployment(graph) and (
            str(queued_context.get("deployment_id")) != str(deployment.id)
            or str(queued_context.get("deployment_version")) != str(deployment.version)
            or queued_context.get("snapshot_sha256") != canonical_snapshot_sha256(graph)
        ):
            raise PermanentDeploymentExecutionError(
                "deployed workflow identity is not frozen"
            )
        execution_context = _canonical_deployment_execution_context(
            queued_context,
            deployment=deployment,
            app=app,
            require_execution_id=_graph_requires_frozen_deployment(graph),
        )
        _enforce_runtime_configuration(
            graph,
            surface="workflow_engine_deployed_run",
        )

        sync_result = {}
        try:
            sync_result = _sync_knowledge_bases_for_execution_subject(
                session,
                graph,
                execution_context,
            )
        except Exception as e:
            logger.error(
                "Workflow knowledge sync failed: error_type=%s",
                type(e).__name__,
            )

        engine = WorkflowEngine(
            graph=graph,
            user_input=user_input,
            execution_context=execution_context,
            is_deployed=True,
            db=session,
            task_deadline=task_deadline,
        )

        # [GEVENT] 직접 동기 호출
        result = engine.execute()
        return {
            "status": "success",
            "result": result,
            "sync_status": sync_result,
            "run_id": _engine_workflow_run_id(engine),
        }

    except ExternalEffectRetrySignal as e:
        logger.warning("Workflow external effect retry requested: code=%s", e.code)
        _safe_retry(self, e)
    except ExternalEffectError as e:
        logger.warning("Workflow external effect stopped: code=%s", e.code)
        return _external_effect_error_result(e)
    except (PermanentDeploymentExecutionError, NonRetryableWorkflowError) as e:
        logger.error(
            "Deployed workflow execution blocked: error_type=%s",
            type(e).__name__,
        )
        raise
    except Exception as e:
        logger.error(
            "Deployed workflow execution failed: error_type=%s",
            type(e).__name__,
        )
        _safe_retry(self, e)
    finally:
        _cleanup_execution_resources(engine, session, label="deployed workflow")


@celery_app.task(
    name="workflow.execute_by_deployment",
    bind=True,
    max_retries=3,
    base=RedactedWorkflowTask,
)
def execute_by_deployment(
    self,
    deployment_id: str,
    user_input: Dict[str, Any],
    execution_context: Dict[str, Any],
):
    """
    배포 ID를 기반으로 워크플로우 실행 (Webhook 등에서 사용)

    [GEVENT] WorkflowEngine이 동기화되어 단순화됨.
    """
    from apps.shared.db.models.app import App
    from apps.shared.db.models.workflow_deployment import (
        DeploymentType,
        WorkflowDeployment,
    )
    from apps.workflow_engine.workflow.core.workflow_engine import WorkflowEngine

    task_deadline = _workflow_task_deadline()
    session = SessionLocal()
    engine = None

    try:
        if not isinstance(execution_context, dict):
            raise PermanentDeploymentExecutionError(
                "실행 컨텍스트 형식이 올바르지 않습니다"
            )
        queued_context = dict(execution_context)
        if (
            str(queued_context.get("trigger_mode", "")).strip().lower() == "schedule"
            and get_schedule_dispatch_settings().mode != "disabled"
        ):
            raise PermanentDeploymentExecutionError(
                "claim mode requires the dedicated schedule task"
            )

        deployment = (
            session.query(WorkflowDeployment)
            .filter(WorkflowDeployment.id == deployment_id)
            .first()
        )

        if not deployment:
            raise PermanentDeploymentExecutionError(
                f"배포를 찾을 수 없습니다: {deployment_id}"
            )

        if not deployment.graph_snapshot:
            raise PermanentDeploymentExecutionError(
                f"배포 그래프 데이터가 없습니다: {deployment_id}"
            )

        app = session.query(App).filter(App.id == deployment.app_id).first()
        if not app:
            raise PermanentDeploymentExecutionError(
                f"배포 앱을 찾을 수 없습니다: {deployment_id}"
            )
        if not getattr(deployment, "is_active", False):
            raise PermanentDeploymentExecutionError(
                f"비활성 배포는 실행할 수 없습니다: {deployment_id}"
            )
        if str(getattr(app, "active_deployment_id", "")) != str(deployment.id):
            raise PermanentDeploymentExecutionError(
                f"현재 활성 배포가 아닙니다: {deployment_id}"
            )
        if deployment.type == DeploymentType.WORKFLOW_NODE:
            raise PermanentDeploymentExecutionError(
                f"workflow_node 배포는 직접 실행할 수 없습니다: {deployment_id}"
            )
        if not _deployment_type_allowed_for_trigger(
            deployment.type,
            queued_context.get("trigger_mode"),
            runtime_policy=get_deployment_runtime_policy(),
        ):
            raise PermanentDeploymentExecutionError(
                f"배포 타입과 실행 트리거가 일치하지 않습니다: {deployment_id}"
            )

        execution_context = _canonical_deployment_execution_context(
            queued_context,
            deployment=deployment,
            app=app,
        )

        _enforce_runtime_configuration(
            deployment.graph_snapshot,
            surface="workflow_engine_deployed_run",
        )

        sync_result = {}
        try:
            sync_result = _sync_knowledge_bases_for_execution_subject(
                session,
                deployment.graph_snapshot,
                execution_context,
            )
        except Exception as e:
            logger.error(
                "Workflow knowledge sync failed: error_type=%s",
                type(e).__name__,
            )

        engine = WorkflowEngine(
            graph=deployment.graph_snapshot,
            user_input=user_input,
            execution_context=execution_context,
            is_deployed=True,
            db=session,
            task_deadline=task_deadline,
        )

        # [GEVENT] 직접 동기 호출
        result = engine.execute()
        return {
            "status": "success",
            "result": result,
            "sync_status": sync_result,
            "run_id": _engine_workflow_run_id(engine),
        }

    except ExternalEffectRetrySignal as e:
        logger.warning("Workflow external effect retry requested: code=%s", e.code)
        _safe_retry(self, e)
    except ExternalEffectError as e:
        logger.warning("Workflow external effect stopped: code=%s", e.code)
        return _external_effect_error_result(e)
    except (PermanentDeploymentExecutionError, NonRetryableWorkflowError) as e:
        logger.error(
            "Deployment workflow execution blocked: error_type=%s",
            type(e).__name__,
        )
        raise
    except Exception as e:
        logger.error(
            "Deployment workflow execution failed: error_type=%s",
            type(e).__name__,
        )
        _safe_retry(self, e)
    finally:
        _cleanup_execution_resources(engine, session, label="deployment workflow")


@celery_app.task(
    name="workflow.execute_scheduled_deployment",
    bind=True,
    max_retries=0,
    ignore_result=True,
    base=RedactedWorkflowTask,
)
def execute_scheduled_deployment(self, schedule_dispatch_claim_id: str):
    """Execute one canonical schedule claim without Celery-level replay."""
    task_id = str(getattr(self.request, "id", "") or "")
    return _execute_scheduled_deployment_claim(
        schedule_dispatch_claim_id,
        task_id=task_id,
        task_deadline=_workflow_task_deadline(),
    )


def _finalize_scheduled_claim(
    *,
    use_case,
    plan,
    succeeded: bool,
    failure_reason: str = REASON_EXECUTION_FAILED_AFTER_ADMISSION,
) -> bool:
    """Retry only the terminal CAS write; never re-run the workflow engine."""
    from apps.workflow_engine.composition.schedule_dispatch import (
        build_schedule_admission_dependencies,
    )

    for attempt in range(1, _SCHEDULE_FINALIZATION_MAX_ATTEMPTS + 1):
        session = SessionLocal()
        try:
            dependencies = build_schedule_admission_dependencies(session)
            return use_case.finalize(
                repository=dependencies.repository,
                uow=dependencies.uow,
                plan=plan,
                succeeded=succeeded,
                failure_reason=failure_reason,
            )
        except Exception as exc:
            logger.error(
                "Schedule finalization unavailable: attempt=%s error_type=%s",
                attempt,
                type(exc).__name__,
            )
        finally:
            _cleanup_execution_resources(
                None,
                session,
                label="schedule finalization",
            )
    raise NonRetryableWorkflowError("scheduled workflow finalization is unavailable")


def _execute_scheduled_deployment_claim(
    schedule_dispatch_claim_id: str,
    *,
    task_id: str,
    task_deadline: float | None = None,
):
    from apps.workflow_engine.composition.schedule_dispatch import (
        build_schedule_admission_dependencies,
        build_scheduled_execution_use_case,
        build_scheduled_workflow_engine,
    )

    try:
        claim_id = uuid.UUID(str(schedule_dispatch_claim_id))
    except (TypeError, ValueError):
        raise PermanentDeploymentExecutionError(
            "invalid schedule claim locator"
        ) from None
    if not task_id.startswith("schedule:"):
        raise PermanentDeploymentExecutionError("invalid schedule task identity")
    if task_deadline is None:
        task_deadline = _workflow_task_deadline()

    try:
        settings = get_schedule_dispatch_settings()
    except Exception as exc:
        logger.error(
            "Schedule dispatch configuration is invalid: error_type=%s",
            type(exc).__name__,
        )
        raise PermanentDeploymentExecutionError(
            "schedule dispatch configuration is invalid"
        ) from None
    use_case = build_scheduled_execution_use_case(
        settings=settings,
        runtime_policy=get_deployment_runtime_policy(),
    )
    admission_owner = str(uuid.uuid4())
    admission_session = SessionLocal()
    try:
        try:
            dependencies = build_schedule_admission_dependencies(admission_session)
            admission = use_case.admit(
                repository=dependencies.repository,
                budget=dependencies.budget,
                configuration_preflight=dependencies.configuration_preflight,
                audit=dependencies.audit,
                uow=dependencies.uow,
                claim_id=claim_id,
                task_id=task_id,
                admission_owner=admission_owner,
            )
        except Exception as exc:
            logger.error(
                "Schedule admission unavailable: error_type=%s",
                type(exc).__name__,
            )
            raise PermanentDeploymentExecutionError(
                "schedule admission is unavailable"
            ) from None
    finally:
        _cleanup_execution_resources(
            None,
            admission_session,
            label="schedule admission",
        )

    if admission.status != "admitted" or admission.plan is None:
        if admission.dead_lettered_reason is not None:
            emit_schedule_dispatch_signal(
                logger,
                "schedule_claim_dead_letter_total",
                status="dead_lettered",
                reason=admission.dead_lettered_reason,
                mode=settings.mode,
            )
        if admission.status == "duplicate":
            emit_schedule_dispatch_signal(
                logger,
                "schedule_duplicate_delivery_suppressed_total",
                status="duplicate",
                reason=admission.reason,
                mode=settings.mode,
            )
        return {
            "status": admission.status,
            "reason": admission.reason,
            "claim_id": str(claim_id),
        }

    plan = admission.plan
    sync_session = None
    try:
        sync_session = SessionLocal()
        _sync_knowledge_bases_for_execution_subject(
            sync_session,
            plan.graph_snapshot,
            plan.execution_context,
        )
    except Exception as exc:
        logger.warning(
            "Scheduled knowledge sync skipped: error_type=%s",
            type(exc).__name__,
        )
    finally:
        if sync_session is not None:
            _cleanup_execution_resources(
                None,
                sync_session,
                label="scheduled knowledge sync",
            )

    engine_session = SessionLocal()
    engine = None
    execution_succeeded = False
    execution_failure_reason = REASON_EXECUTION_FAILED_AFTER_ADMISSION
    execution_error_code = "workflow.execution_failed"
    try:
        engine = build_scheduled_workflow_engine(
            graph=plan.graph_snapshot,
            user_input=plan.user_input,
            execution_context=plan.execution_context,
            is_deployed=True,
            db=engine_session,
            task_deadline=task_deadline,
        )
        engine.execute()
        execution_succeeded = True
    except ExternalEffectError as exc:
        execution_error_code = exc.code
        if exc.code == "external_effect.outcome_unknown":
            execution_failure_reason = REASON_EXECUTION_OUTCOME_UNKNOWN
        logger.error(
            "Scheduled workflow external effect stopped: code=%s",
            exc.code,
        )
    except Exception as exc:
        logger.error(
            "Scheduled workflow execution failed: error_type=%s",
            type(exc).__name__,
        )
    finally:
        if engine is not None:
            try:
                engine.cleanup()
            except Exception as exc:
                # Cleanup is best-effort after the workflow result is known. It must
                # not prevent the durable claim from reaching its terminal state.
                logger.warning(
                    "Scheduled workflow cleanup failed: error_type=%s",
                    type(exc).__name__,
                )
        try:
            engine_session.close()
        except Exception as exc:
            logger.warning(
                "Scheduled workflow session close failed: error_type=%s",
                type(exc).__name__,
            )

    finalized = _finalize_scheduled_claim(
        use_case=use_case,
        plan=plan,
        succeeded=execution_succeeded,
        failure_reason=execution_failure_reason,
    )
    if finalized and not execution_succeeded:
        emit_schedule_dispatch_signal(
            logger,
            "schedule_claim_dead_letter_total",
            status="dead_lettered",
            reason=execution_failure_reason,
            mode=settings.mode,
        )

    if not execution_succeeded:
        raise NonRetryableWorkflowError(execution_error_code)
    return {
        "status": "success",
        "claim_id": str(claim_id),
        "finalized": finalized,
    }


@celery_app.task(
    name="workflow.stream", bind=True, max_retries=3, base=RedactedWorkflowTask
)
def stream_workflow(
    self,
    graph: Dict[str, Any],
    user_input: Dict[str, Any],
    execution_context: Dict[str, Any],
    external_run_id: str,
):
    """
    워크플로우 스트리밍 실행 (외부에서 run_id 전달)

    [GEVENT] WorkflowEngine.execute_stream()이 이제 동기 제너레이터.
    """
    from apps.workflow_engine.workflow.core.workflow_engine import WorkflowEngine

    task_deadline = _workflow_task_deadline()
    session = SessionLocal()
    engine = None

    try:
        execution_context = _canonical_workflow_execution_context(
            session,
            dict(execution_context or {}),
        )
        execution_context["workflow_run_id"] = external_run_id

        _enforce_runtime_configuration(graph, surface="workflow_engine_stream")

        sync_result = {}
        try:
            sync_result = _sync_knowledge_bases_for_execution_subject(
                session,
                graph,
                execution_context,
            )

            if sync_result.get("failed"):
                from apps.shared.pubsub import publish_workflow_event

                publish_workflow_event(external_run_id, "sync_warning", sync_result)

        except Exception as e:
            logger.error(
                "Workflow knowledge sync failed: error_type=%s",
                type(e).__name__,
            )

        engine = WorkflowEngine(
            graph=graph,
            user_input=user_input,
            execution_context=execution_context,
            is_deployed=False,
            db=session,
            task_deadline=task_deadline,
        )

        # [GEVENT] 동기 제너레이터 사용
        final_result = {}
        for event in engine.execute_stream():
            if event.get("type") == "workflow_finish":
                final_result = event.get("data", {})
            elif event.get("type") == "error":
                event_data = event.get("data", {})
                error_message = event_data.get("message", "Unknown error")
                error_payload = (
                    event_data if event_data.get("code") else event_data.get("error")
                )
                if isinstance(error_payload, dict) and error_payload.get("code"):
                    raise ExternalEffectError(
                        str(error_payload["code"]),
                        retryable=bool(error_payload.get("retryable", False)),
                        node_id=error_payload.get("node_id"),
                    )
                if event_data.get("non_retryable"):
                    raise NonRetryableWorkflowError(error_message)
                raise ValueError(error_message)

        return {"status": "success", "result": final_result, "sync_status": sync_result}

    except ExternalEffectRetrySignal as e:
        logger.warning("Workflow external effect retry requested: code=%s", e.code)
        try:
            _safe_retry(self, e)
        except Retry:
            raise
        except Exception:
            from apps.shared.pubsub import publish_workflow_event

            terminal_error = ExternalEffectError(
                e.terminal_code,
                retryable=False,
                node_id=e.node_id,
            )
            publish_workflow_event(
                external_run_id,
                "error",
                terminal_error.to_payload(),
            )
            raise
    except ExternalEffectError as e:
        logger.warning("Workflow external effect stopped: code=%s", e.code)
        return _external_effect_error_result(e)
    except (PermanentDeploymentExecutionError, NonRetryableWorkflowError) as e:
        logger.error(
            "Streaming workflow execution blocked: error_type=%s",
            type(e).__name__,
        )
        from apps.shared.pubsub import publish_workflow_event

        publish_workflow_event(
            external_run_id,
            "error",
            {"message": "workflow.execution_blocked"},
        )
        raise
    except Exception as e:
        logger.error(
            "Streaming workflow execution failed: error_type=%s",
            type(e).__name__,
        )
        from apps.shared.pubsub import publish_workflow_event

        publish_workflow_event(
            external_run_id,
            "error",
            {"message": "workflow.execution_failed"},
        )
        _safe_retry(self, e)
    finally:
        _cleanup_execution_resources(engine, session, label="streaming workflow")
