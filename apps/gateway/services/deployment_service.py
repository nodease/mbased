"""Deployment Service - 배포 관련 비즈니스 로직"""

import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import and_, desc, func
from sqlalchemy.orm import Session

from apps.gateway.services.app_auth_secret_service import AppAuthSecretService
from apps.gateway.services.app_lifecycle_lock import lock_app_for_lifecycle
from apps.gateway.services.knowledge_deployment_preflight_service import (
    KnowledgeDeploymentPreflightService,
)
from apps.gateway.services.deployment_parameter_optimization_service import (
    DeploymentParameterOptimizationConfigurationError,
    DeploymentParameterOptimizationService,
)
from apps.gateway.services.workflow_budget_service import WorkflowBudgetService
from apps.gateway.services.workflow_service import WorkflowService
from apps.gateway.application.deployment.schedule_errors import (
    ScheduleConfigurationError,
)
from apps.gateway.application.deployment.browser_access_errors import (
    BrowserAccessPolicyError,
)
from apps.shared.celery_app import celery_app
from apps.shared.db.models.app import App
from apps.shared.db.models.schedule import Schedule
from apps.shared.db.models.workflow import Workflow
from apps.shared.db.models.workflow_deployment import DeploymentType, WorkflowDeployment
from apps.shared.domain.deployment_runtime_policy import (
    SURFACE_AUTHENTICATED_RUN,
    SURFACE_AUTHENTICATED_RUN_INFO,
    SURFACE_SCHEDULE_RUN,
    DeploymentRuntimePolicy,
    is_deployment_type_allowed_for_surface,
    is_deployment_type_allowed_for_trigger,
)
from apps.shared.domain.public_chat_conversation import (
    PUBLIC_CHAT_REQUEST_TTL_SECONDS,
    PublicChatConversationContractError,
    resolve_public_chat_conversation_contract,
)
from apps.shared.domain.external_effect_error import (
    safe_external_effect_error_payload,
)
from apps.shared.domain.workflow_graph import (
    WorkflowGraphValidationError,
    validate_workflow_graph,
)
from apps.shared.schemas.deployment import DeploymentCreate, DeploymentPreflightResponse
from apps.shared.services.permissions import has_workflow_permission
from apps.shared.services.workflow_configuration_preflight import (
    WorkflowConfigurationPreflightError,
    enforce_workflow_configuration_preflight,
    workflow_configuration_issues,
)
from apps.shared.services.workflow_node_secret_service import (
    WorkflowNodeSecretError,
    get_workflow_node_secret_encryption_service,
    migrate_legacy_workflow_graph_secrets,
    validate_workflow_node_secret_persistence_boundary,
    validate_workflow_node_secret_reference_ownership,
)
from apps.shared.services.credential_encryption import CredentialEncryptionError
from apps.shared.services.workflow_task_publisher import (
    PUBLIC_CHAT_WORKFLOW_TASK_NAME,
    send_workflow_task,
)
from apps.shared.services.public_chat_history_transient_store import (
    PUBLIC_CHAT_HISTORY_TRANSIENT_IO_TIMEOUT_SECONDS,
    PublicChatHistoryTransientStoreError,
    store_public_chat_history,
)
from apps.workflow_engine.services.model_routing_policy_store import (
    ModelRoutingPolicyStore,
)

logger = logging.getLogger(__name__)

_CHATBOT_DEPLOYMENT_TYPES = {
    DeploymentType.CHATBOT,
    DeploymentType.INTERNAL_CHATBOT,
}
_AUTH_SECRET_DEPLOYMENT_TYPES = {
    DeploymentType.API,
    DeploymentType.WEBHOOK,
}
_LEGACY_CONVERSATION_INPUT = "conversation_id"
_LEGACY_MEMORY_MODE_INPUT = "memory_mode"
_MAX_LEGACY_CONVERSATION_ID_LENGTH = 255


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _remaining_public_request_seconds(
    deadline: datetime | None,
    *,
    minimum_seconds: float = 0.0,
) -> float:
    if (
        not isinstance(deadline, datetime)
        or deadline.tzinfo is None
        or deadline.utcoffset() is None
    ):
        raise HTTPException(
            status_code=500,
            detail="Workflow execution failed",
        )
    remaining = (deadline.astimezone(timezone.utc) - _utc_now()).total_seconds()
    if remaining < minimum_seconds or remaining <= 0:
        raise HTTPException(
            status_code=504,
            detail="Workflow execution timed out",
        )
    return remaining


class DeploymentAuthSecretPreflightError(RuntimeError):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.message = message
        self.details = details or {}


def _safe_deployment_error_detail(value: Any) -> Any:
    if not isinstance(value, dict):
        return "Workflow execution failed"
    code = value.get("code")
    if isinstance(code, str) and code.startswith("external_effect."):
        return safe_external_effect_error_payload(value) or "Workflow execution failed"
    return value


def _deployment_type_allowed_for_slug_trigger(
    deployment_type: DeploymentType,
    trigger_mode: str,
    *,
    runtime_policy: DeploymentRuntimePolicy,
) -> bool:
    return is_deployment_type_allowed_for_trigger(
        deployment_type,
        trigger_mode,
        policy=runtime_policy,
    )


class DeploymentService:
    """배포 관련 비즈니스 로직을 담당하는 Service"""

    @staticmethod
    def create_deployment(
        db: Session,
        deployment_in: DeploymentCreate,
        user_id: uuid.UUID,
        *,
        observed_workflow_id: uuid.UUID,
        runtime_policy: DeploymentRuntimePolicy,
        auth_secret_lifecycle_mutations_enabled: bool = False,
        require_public_chat_conversation_contract: bool = False,
    ) -> WorkflowDeployment:
        """
        워크플로우를 배포합니다.

        Args:
            db: 데이터베이스 세션
            deployment_in: 배포 생성 요청 데이터
            user_id: 현재 사용자 ID

        Returns:
            생성된 WorkflowDeployment 객체

        Raises:
            HTTPException: 워크플로우를 찾을 수 없거나 권한이 없는 경우
        """
        # 1. App 조회 및 권한 확인
        app = lock_app_for_lifecycle(db, deployment_in.app_id)
        if not app:
            raise HTTPException(status_code=404, detail="App not found")

        if (
            deployment_in.graph_snapshot is not None
            and app.workflow_id != observed_workflow_id
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "deployment.graph_snapshot_stale",
                    "message": (
                        "The App primary Workflow changed. Refresh the deployment "
                        "snapshot and try again."
                    ),
                },
            )

        # 2. Workflow 조회 및 권한 체크
        workflow = db.query(Workflow).filter(Workflow.id == app.workflow_id).first()
        if not workflow:
            raise HTTPException(status_code=404, detail="Workflow not found")

        if not has_workflow_permission(
            db,
            user_id,
            workflow.id,
            "deploy",
            organization_id=workflow.organization_id,
        ):
            raise HTTPException(
                status_code=403,
                detail="You do not have permission to deploy this app.",
            )

        browser_access_policy = DeploymentService.normalize_browser_access_policy(
            deployment_in.type,
            deployment_in.browser_access_policy,
        )

        # 4. Draft 데이터(Snapshot) 가져오기
        graph_snapshot = DeploymentService._resolve_graph_snapshot(
            db,
            workflow.id,
            deployment_in.graph_snapshot,
        )
        DeploymentService.validate_public_chat_conversation_config(
            deployment_type=deployment_in.type,
            config=deployment_in.config,
            graph_snapshot=graph_snapshot,
            required=(require_public_chat_conversation_contract),
        )
        DeploymentService._enforce_graph_structure_before_binding(
            db,
            app=app,
            deployment_type=deployment_in.type,
            graph_snapshot=graph_snapshot,
            principal_id=user_id,
            is_active=deployment_in.is_active,
        )
        DeploymentService._enforce_node_secret_storage_boundary(
            db,
            graph_snapshot,
            workflow_id=workflow.id,
            organization_id=workflow.organization_id,
        )
        WorkflowService.validate_knowledge_references(
            db,
            graph_snapshot,
            user_id=user_id,
            organization_id=workflow.organization_id,
        )
        try:
            graph_snapshot = DeploymentService.bind_workflow_node_targets(
                db,
                graph_snapshot,
                app=app,
            )
        except HTTPException:
            # A transitive target can fail structural validation inside binding.
            # Preserve the common safe preflight envelope before exposing a
            # lower-level binding error.
            DeploymentService._enforce_deployment_configuration_preflight(
                db,
                app=app,
                deployment_type=deployment_in.type,
                graph_snapshot=graph_snapshot,
                principal_id=user_id,
                is_active=deployment_in.is_active,
            )
            raise
        try:
            WorkflowService.validate_mail_credential_references(
                db,
                graph_snapshot,
                user_id=str(user_id),
                organization_id=workflow.organization_id,
                require_resolved=False,
            )
        except HTTPException as exc:
            if exc.status_code == 403:
                raise
            reason_code = (
                "mail_credential_unavailable"
                if exc.detail == "resource.not_found"
                else "node_configuration_invalid"
            )
            raise DeploymentService.workflow_configuration_validation_blocked(
                graph_snapshot,
                surface="deployment",
                reason_code=reason_code,
            ) from exc
        if deployment_in.is_active:
            try:
                enforce_workflow_configuration_preflight(
                    graph_snapshot,
                    surface="deployment",
                )
            except WorkflowConfigurationPreflightError as exc:
                raise DeploymentService.workflow_configuration_preflight_blocked(
                    exc
                ) from exc

        DeploymentService._enforce_deployment_configuration_preflight(
            db,
            app=app,
            deployment_type=deployment_in.type,
            graph_snapshot=graph_snapshot,
            principal_id=user_id,
            is_active=deployment_in.is_active,
        )
        WorkflowService.validate_external_node_storage_boundaries(
            graph_snapshot,
            require_resolved=False,
        )
        DeploymentService.ensure_auth_secret_ready_for_activation(
            app,
            deployment_type=deployment_in.type,
            is_active=deployment_in.is_active,
            lifecycle_mutations_enabled=auth_secret_lifecycle_mutations_enabled,
        )

        # 5. 첫 배포 시 url_slug 생성. Secret은 lifecycle API에서 발급한다.
        # app 상태가 남지 않도록 active preflight 이후에 수행한다.
        if not app.url_slug:
            from apps.gateway.services.app_service import AppService

            app.url_slug = AppService._generate_url_slug(db, app.name)

        db.flush()

        # 6. 버전 번호 채번 (app_id 기준)
        max_version = (
            db.query(func.max(WorkflowDeployment.version))
            .filter(WorkflowDeployment.app_id == deployment_in.app_id)
            .scalar()
        ) or 0
        new_version = max_version + 1

        # 7. 입출력 스키마 자동 추출
        input_schema = DeploymentService._extract_input_schema(graph_snapshot)
        output_schema = DeploymentService._extract_output_schema(graph_snapshot)

        # 8. 배포 모델 생성
        deployment_config = dict(deployment_in.config or {})
        if deployment_in.parameter_optimization is not None:
            deployment_config["parameter_optimization"] = (
                deployment_in.parameter_optimization.model_dump(mode="json")
            )

        db_obj = WorkflowDeployment(
            app_id=deployment_in.app_id,
            version=new_version,
            type=deployment_in.type,
            # url_slug는 App 모델에서 관리한다.
            graph_snapshot=graph_snapshot,
            config=deployment_config,
            browser_access_policy=(
                browser_access_policy.to_dict()
                if browser_access_policy is not None
                else None
            ),
            input_schema=input_schema,
            output_schema=output_schema,
            description=deployment_in.description,
            created_by=user_id,
            is_active=deployment_in.is_active,
        )

        try:
            db.add(db_obj)
            db.flush()

            # 8.1. 같은 앱의 기존 배포를 모두 비활성화 (단일 활성화 정책)
            if db_obj.is_active:
                # bootstrap 정책은 현재 deployment snapshot의 작업 지문과 실행 주체
                # 권한을 기준으로 다시 만든다. 이전 입력군 기반 정책은 상속하지 않는다.
                ModelRoutingPolicyStore.ensure_policies_for_deployment(
                    db,
                    workflow_id=workflow.id,
                    deployment_id=db_obj.id,
                    organization_id=workflow.organization_id,
                    execution_subject_user_id=user_id,
                    graph_snapshot=graph_snapshot,
                )
                from apps.gateway.services.scheduler_service import (
                    get_scheduler_service,
                )

                scheduler_service = get_scheduler_service()
                DeploymentService._deactivate_other_deployments(
                    db, app.id, db_obj.id, scheduler_service
                )

                # 9. App의 active_deployment_id 업데이트
                app.active_deployment_id = db_obj.id

                # 10. ScheduleTrigger 노드가 있으면 스케줄 생성
                schedule = DeploymentService._ensure_schedule_record(
                    db,
                    db_obj,
                    runtime_policy=runtime_policy,
                )
                if schedule:
                    scheduler_service = get_scheduler_service()
                    scheduler_service.add_schedule(schedule, db)

            DeploymentParameterOptimizationService.configure_for_deployment(
                db,
                deployment=db_obj,
                workflow_id=workflow.id,
                graph_snapshot=graph_snapshot,
                config=deployment_in.parameter_optimization,
            )

            db.commit()
            db.refresh(db_obj)

            # 응답에는 public path를 구성하는 slug만 projection한다.
            db_obj.url_slug = app.url_slug

            return db_obj

        except DeploymentParameterOptimizationConfigurationError as exc:
            db.rollback()
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except ScheduleConfigurationError:
            db.rollback()
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "deployment.schedule_configuration_invalid",
                    "message": "Schedule configuration is invalid.",
                },
            ) from None
        except HTTPException:
            db.rollback()
            raise
        except Exception as exc:
            db.rollback()
            logger.error(
                "Deployment creation failed: error_type=%s",
                type(exc).__name__,
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "deployment.creation_failed",
                    "message": "Deployment could not be created.",
                },
            ) from None

    @staticmethod
    def preview_knowledge_preflight(
        db: Session,
        *,
        app: App,
        deployment_type: DeploymentType,
        graph_snapshot: dict,
        audience_hint=None,
        is_active: bool = True,
        principal_id: uuid.UUID | None = None,
        auth_secret_lifecycle_mutations_enabled: bool = False,
    ) -> DeploymentPreflightResponse:
        result = KnowledgeDeploymentPreflightService(
            db,
            organization_id=app.organization_id,
            principal_id=principal_id,
            candidate_graphs_by_app_id={app.id: graph_snapshot},
            candidate_deployment_types_by_app_id={app.id: deployment_type},
        ).preview(
            deployment_type=deployment_type,
            graph_snapshot=graph_snapshot,
            audience_hint=audience_hint,
            is_active=is_active,
        )
        if result.status != "blocked":
            DeploymentService.ensure_auth_secret_ready_for_activation(
                app,
                deployment_type=deployment_type,
                is_active=is_active,
                lifecycle_mutations_enabled=(auth_secret_lifecycle_mutations_enabled),
            )
        return result

    @staticmethod
    def ensure_auth_secret_ready_for_activation(
        app: App,
        *,
        deployment_type: DeploymentType,
        is_active: bool,
        lifecycle_mutations_enabled: bool,
    ) -> None:
        if (
            not is_active
            or deployment_type not in _AUTH_SECRET_DEPLOYMENT_TYPES
            or AppAuthSecretService.is_configured(app)
        ):
            return
        if not lifecycle_mutations_enabled:
            raise DeploymentAuthSecretPreflightError(
                code="app.auth_secret_lifecycle_unavailable",
                message=(
                    "App authentication secret lifecycle is temporarily unavailable."
                ),
            )
        raise DeploymentAuthSecretPreflightError(
            code="deployment.app_auth_secret_required",
            message=(
                "Issue an App authentication secret before activating this deployment."
            ),
            details={"required_actions": ["issue_app_auth_secret"]},
        )

    @staticmethod
    def normalize_browser_access_policy(deployment_type, policy):
        from apps.gateway.composition.deployment import (
            normalize_deployment_browser_access_policy,
        )

        try:
            return normalize_deployment_browser_access_policy(
                deployment_type,
                policy,
            )
        except BrowserAccessPolicyError as exc:
            detail = {
                "code": exc.code,
                "message": "Browser access policy is invalid.",
            }
            if exc.origin_index is not None:
                detail["field"] = (
                    "browser_access_policy.embedding.parent_origins"
                    f"[{exc.origin_index}]"
                )
            raise HTTPException(status_code=422, detail=detail) from None

    @staticmethod
    def _enforce_knowledge_preflight(
        db: Session,
        *,
        app: App,
        deployment_type: DeploymentType,
        graph_snapshot: dict,
        principal_id: uuid.UUID | None = None,
    ) -> DeploymentPreflightResponse:
        return KnowledgeDeploymentPreflightService(
            db,
            organization_id=app.organization_id,
            principal_id=principal_id,
            candidate_graphs_by_app_id={app.id: graph_snapshot},
            candidate_deployment_types_by_app_id={app.id: deployment_type},
        ).enforce_active_publish(
            deployment_type=deployment_type,
            graph_snapshot=graph_snapshot,
        )

    @staticmethod
    def _enforce_inactive_preflight(
        db: Session,
        *,
        app: App,
        deployment_type: DeploymentType,
        graph_snapshot: dict,
        principal_id: uuid.UUID | None = None,
    ) -> DeploymentPreflightResponse:
        return KnowledgeDeploymentPreflightService(
            db,
            organization_id=app.organization_id,
            principal_id=principal_id,
            candidate_graphs_by_app_id={app.id: graph_snapshot},
            candidate_deployment_types_by_app_id={app.id: deployment_type},
        ).enforce_inactive_save(
            deployment_type=deployment_type,
            graph_snapshot=graph_snapshot,
        )

    @staticmethod
    def _enforce_deployment_configuration_preflight(
        db: Session,
        *,
        app: App,
        deployment_type: DeploymentType,
        graph_snapshot: dict,
        principal_id: uuid.UUID,
        is_active: bool,
    ) -> DeploymentPreflightResponse:
        if is_active:
            return DeploymentService._enforce_knowledge_preflight(
                db,
                app=app,
                deployment_type=deployment_type,
                graph_snapshot=graph_snapshot,
                principal_id=principal_id,
            )
        return DeploymentService._enforce_inactive_preflight(
            db,
            app=app,
            deployment_type=deployment_type,
            graph_snapshot=graph_snapshot,
            principal_id=principal_id,
        )

    @staticmethod
    def enforce_authenticated_configuration_preflight(
        db: Session,
        *,
        graph_snapshot: dict,
        organization_id: uuid.UUID | None,
        principal_id: uuid.UUID,
    ) -> DeploymentPreflightResponse:
        return KnowledgeDeploymentPreflightService(
            db,
            organization_id=organization_id,
            principal_id=principal_id,
        ).enforce_authenticated_run(graph_snapshot=graph_snapshot)

    @staticmethod
    def workflow_configuration_preflight_blocked(
        error: WorkflowConfigurationPreflightError,
        *,
        reason_code: str = "node_configuration_unresolved",
        issues: list | None = None,
    ) -> HTTPException:
        return DeploymentService._workflow_configuration_preflight_response(
            surface=error.surface,
            reason_code=reason_code,
            issues=(issues if issues is not None else error.issues),
        )

    @staticmethod
    def workflow_configuration_validation_blocked(
        graph_snapshot: dict[str, Any],
        *,
        surface: str,
        reason_code: str,
    ) -> HTTPException:
        return DeploymentService._workflow_configuration_preflight_response(
            surface=surface,
            reason_code=reason_code,
            issues=workflow_configuration_issues(graph_snapshot),
        )

    @staticmethod
    def _workflow_configuration_preflight_response(
        *,
        surface: str,
        reason_code: str,
        issues: list,
    ) -> HTTPException:
        safe_issues = list(issues)
        return HTTPException(
            status_code=409,
            detail={
                "error": {
                    "code": "workflow.configuration_preflight.blocked",
                    "message": "Workflow configuration preflight blocked execution",
                    "reason_code": reason_code,
                    "required_actions": ["complete_node_configuration"],
                    "preflight": {
                        "status": "blocked",
                        "surface": surface,
                        "nodes": [
                            {
                                "node_type": issue.node_type,
                                "reason_codes": [reason_code],
                            }
                            for issue in safe_issues
                        ],
                    },
                }
            },
        )

    @staticmethod
    def _enforce_graph_structure_before_binding(
        db: Session,
        *,
        app: App,
        deployment_type: DeploymentType,
        graph_snapshot: dict,
        principal_id: uuid.UUID,
        is_active: bool,
    ) -> None:
        try:
            validate_workflow_graph(graph_snapshot)
        except WorkflowGraphValidationError:
            DeploymentService._enforce_deployment_configuration_preflight(
                db,
                app=app,
                deployment_type=deployment_type,
                graph_snapshot=graph_snapshot,
                principal_id=principal_id,
                is_active=is_active,
            )

    @staticmethod
    def _resolve_graph_snapshot(
        db: Session,
        workflow_id: uuid.UUID,
        graph_snapshot: dict | None,
    ) -> dict:
        if not graph_snapshot:
            graph_snapshot = WorkflowService.get_draft(db, str(workflow_id))

        if not graph_snapshot:
            raise HTTPException(
                status_code=400,
                detail="Cannot deploy workflow without graph data. Please save the workflow first.",
            )
        return graph_snapshot

    @staticmethod
    def _enforce_node_secret_storage_boundary(
        db: Session,
        graph_snapshot: dict,
        *,
        workflow_id: uuid.UUID,
        organization_id: uuid.UUID | None,
    ) -> None:
        try:
            validate_workflow_node_secret_persistence_boundary(
                graph_snapshot.get("nodes", [])
            )
        except WorkflowNodeSecretError as exc:
            raise HTTPException(
                status_code=422,
                detail="workflow.node_secret_reference_required",
            ) from exc
        try:
            validate_workflow_node_secret_reference_ownership(
                db,
                nodes=graph_snapshot.get("nodes", []),
                workflow_id=workflow_id,
                organization_id=organization_id,
            )
        except WorkflowNodeSecretError as exc:
            raise HTTPException(
                status_code=422,
                detail="workflow.node_secret_reference_invalid",
            ) from exc

    @staticmethod
    def migrate_legacy_node_secrets(
        db: Session,
        deployment: WorkflowDeployment,
        *,
        actor_id: uuid.UUID | None = None,
    ) -> WorkflowDeployment:
        graph_snapshot = getattr(deployment, "graph_snapshot", None)
        if not isinstance(graph_snapshot, dict):
            return deployment
        try:
            validate_workflow_node_secret_persistence_boundary(
                graph_snapshot.get("nodes", [])
            )
            return deployment
        except WorkflowNodeSecretError:
            pass

        app = db.query(App).filter(App.id == deployment.app_id).first()
        workflow = (
            db.query(Workflow).filter(Workflow.id == app.workflow_id).first()
            if app is not None
            else None
        )
        if workflow is None or workflow.organization_id is None:
            raise HTTPException(
                status_code=503,
                detail="workflow.node_secret_migration_unavailable",
            )
        migration_actor = actor_id or deployment.created_by
        try:
            migrated_graph, changed = migrate_legacy_workflow_graph_secrets(
                db,
                graph=graph_snapshot,
                encryption=get_workflow_node_secret_encryption_service(),
                workflow_id=workflow.id,
                organization_id=workflow.organization_id,
                user_id=migration_actor,
            )
        except (WorkflowNodeSecretError, CredentialEncryptionError) as exc:
            db.rollback()
            raise HTTPException(
                status_code=503,
                detail="workflow.node_secret_migration_unavailable",
            ) from exc
        if changed:
            deployment.graph_snapshot = migrated_graph
            db.commit()
            db.refresh(deployment)
        return deployment

    @staticmethod
    def bind_workflow_node_targets(
        db: Session,
        graph_snapshot: dict,
        *,
        app: App,
    ) -> dict:
        from apps.gateway.composition.deployment import (
            build_workflow_node_binding_use_case,
        )
        from apps.shared.domain.workflow_node_binding import WorkflowNodeBindingError

        try:
            return build_workflow_node_binding_use_case(
                db,
                organization_id=app.organization_id,
            ).bind_graph(graph_snapshot, root_app_id=app.id)
        except WorkflowNodeBindingError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": exc.code,
                    "message": "Workflow-node target could not be fixed for execution.",
                },
            ) from None

    @staticmethod
    def list_deployments(
        db: Session,
        app_id: uuid.UUID = None,
        workflow_id: uuid.UUID = None,
        skip: int = 0,
        limit: int = 100,
    ) -> List[WorkflowDeployment]:
        """
        특정 앱의 배포 이력을 조회합니다.

        Args:
            db: 데이터베이스 세션
            user_id: 요청 사용자 ID (권한 확인용)
            app_id: 앱 ID (Optional)
            workflow_id: 워크플로우 ID (Optional)
            skip: 페이지네이션 시작 위치
            limit: 조회할 최대 개수

        Returns:
            WorkflowDeployment 객체 리스트

        Raises:
            HTTPException: 권한이 없는 경우
        """
        if workflow_id and not app_id:
            # workflow_id로 app_id 찾기
            app = db.query(App).filter(App.workflow_id == workflow_id).first()
            if app:
                app_id = app.id

        if not app_id:
            return []

        # [SECURE] App 조회
        app = db.query(App).filter(App.id == app_id).first()
        if not app:
            # 앱이 없으면 빈 리스트 반환하거나 404 (여기선 빈 리스트 유지)
            return []

        deployments = (
            db.query(WorkflowDeployment)
            .filter(WorkflowDeployment.app_id == app_id)
            .order_by(desc(WorkflowDeployment.version))
            .offset(skip)
            .limit(limit)
            .all()
        )

        # The slug belongs to App but is part of the authenticated deployment
        # response contract used to construct share URLs.
        for deployment in deployments:
            DeploymentService.migrate_legacy_node_secrets(db, deployment)
            deployment.url_slug = app.url_slug

        return deployments

    @staticmethod
    def get_deployment(db: Session, deployment_id: uuid.UUID) -> WorkflowDeployment:
        """
        특정 배포 ID의 상세 정보를 조회합니다.

        Args:
            db: 데이터베이스 세션
            deployment_id: 배포 ID

        Returns:
            WorkflowDeployment 객체

        Raises:
            HTTPException: 배포를 찾을 수 없는 경우
        """
        deployment = (
            db.query(WorkflowDeployment)
            .filter(WorkflowDeployment.id == deployment_id)
            .first()
        )
        if not deployment:
            raise HTTPException(status_code=404, detail="Deployment not found")

        return DeploymentService.migrate_legacy_node_secrets(db, deployment)

    @staticmethod
    def list_workflow_node_deployments(
        db: Session, user_id: uuid.UUID, excluded_app_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        '워크플로우 노드'로 배포된 모든 앱의 목록을 조회합니다.
        다른 워크플로우에서 사용할 수 있는 컴포넌트 목록용입니다.

        Returns:
            [
                {
                    "deployment_id": "...",
                    "app_id": "...",
                    "name": "...",
                    "description": "...",
                    "input_schema": {...},
                    "output_schema": {...},
                    "version": 1
                }, ...
            ]
        """
        # 1. 활성 배포(Active Deployment)가 있고, 타입이 WORKFLOW_NODE인 App 조회
        # (WorkflowDeployment와 App을 조인하여 최신 정보 가져옴)
        query = (
            db.query(App, WorkflowDeployment)
            .join(
                WorkflowDeployment,
                and_(
                    App.active_deployment_id == WorkflowDeployment.id,
                    App.id == WorkflowDeployment.app_id,
                ),
            )
            .filter(WorkflowDeployment.type == DeploymentType.WORKFLOW_NODE)
            .filter(WorkflowDeployment.is_active.is_(True))
        )

        if excluded_app_id:
            query = query.filter(App.id != excluded_app_id)

        results = query.all()

        nodes = []
        for app, deployment in results:
            if not app.workflow_id or not has_workflow_permission(
                db,
                user_id,
                app.workflow_id,
                "read",
                organization_id=app.organization_id,
            ):
                continue
            nodes.append(
                {
                    "deployment_id": str(deployment.id),
                    "app_id": str(app.id),
                    "name": app.name,
                    "description": app.description,
                    "input_schema": deployment.input_schema,
                    "output_schema": deployment.output_schema,
                    "version": deployment.version,
                }
            )

        return nodes

    @staticmethod
    def validate_public_chat_conversation_config(
        *,
        deployment_type: DeploymentType,
        config: Dict[str, Any] | None,
        graph_snapshot: Dict[str, Any],
        required: bool,
    ) -> None:
        has_contract = isinstance(config, dict) and "public_conversation" in config
        if deployment_type is not DeploymentType.CHATBOT:
            if has_contract:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "conversation.consumer_mapping_invalid",
                        "message": (
                            "Public conversation configuration is only "
                            "supported for public chatbot deployments."
                        ),
                    },
                )
            return
        try:
            resolve_public_chat_conversation_contract(
                config,
                graph_snapshot,
                required=required,
            )
        except PublicChatConversationContractError as error:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": error.code,
                    "message": "The public conversation consumer mapping is invalid.",
                },
            ) from None

    @staticmethod
    async def run_deployment(
        db: Session,
        url_slug: str,
        user_inputs: Dict[str, Any],
        trigger_mode: str,
        runtime_policy: DeploymentRuntimePolicy,
        client_conversation_history: tuple[dict[str, str], ...] | None = None,
        allow_stateless_public_chatbot_compatibility: bool = False,
        expected_deployment_version: int | None = None,
        public_request_deadline_at: datetime | None = None,
        auth_token: Optional[str] = None,
        require_auth: bool = True,  # 인증 필요 여부 (기본값: 필요)
    ) -> Dict[str, Any]:
        """
        배포된 워크플로우를 실행합니다.

        Args:
            db: 데이터베이스 세션
            url_slug: 앱의 URL slug (예: "my-chat-app")
            user_inputs: 워크플로우 실행 시 사용자 입력 데이터
            auth_token: public API Bearer token candidate
            require_auth: 인증 검증 필요 여부 (True: REST API, False: 웹 앱)

        Returns:
            워크플로우 실행 결과 {"status": "success", "results": {...}}

        Raises:
            HTTPException: 배포를 찾을 수 없거나 권한이 없거나 실행 실패 시
        """
        # 1. url_slug와 일치하는 App 찾기 (App 중심 구조)
        app = db.query(App).filter(App.url_slug == url_slug).first()
        if not app:
            raise HTTPException(status_code=404, detail="App not found.")

        # 2. 활성 배포(Active Deployment) 조회
        if not app.active_deployment_id:
            raise HTTPException(
                status_code=404, detail="No active deployment found for this app."
            )

        deployment = (
            db.query(WorkflowDeployment)
            .filter(
                WorkflowDeployment.id == app.active_deployment_id,
                WorkflowDeployment.app_id == app.id,
            )
            .first()
        )

        if not deployment:
            raise HTTPException(status_code=404, detail="Deployment data not found.")


        # 3. 활성상태 체크
        if not deployment.is_active:
            raise HTTPException(status_code=404, detail="Deployment is inactive")
        if not _deployment_type_allowed_for_slug_trigger(
            deployment.type,
            trigger_mode,
            runtime_policy=runtime_policy,
        ):
            raise HTTPException(status_code=404, detail="Deployment not found.")
        if expected_deployment_version is not None:
            if (
                isinstance(expected_deployment_version, bool)
                or not isinstance(expected_deployment_version, int)
                or expected_deployment_version < 1
            ):
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "conversation.deployment_version_invalid",
                        "message": "The public deployment version is invalid.",
                    },
                )
            if deployment.version != expected_deployment_version:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "conversation.deployment_version_changed",
                        "message": "The active public deployment changed.",
                    },
                )

        # 4. 인증 검증은 verifier lifecycle 경계를 사용한다.
        if require_auth:
            if not AppAuthSecretService.authenticate(app, auth_token):
                raise HTTPException(
                    status_code=401, detail="Invalid authentication secret"
                )
        # require_auth가 False면 인증 스킵 (웹 앱/위젯)

        return await DeploymentService._execute_deployment_snapshot(
            db=db,
            app=app,
            deployment=deployment,
            user_inputs=user_inputs,
            trigger_mode=trigger_mode,
            actor_user_id=None,
            execution_subject_user_id=None,
            client_conversation_history=client_conversation_history,
            allow_stateless_public_chatbot_compatibility=(
                allow_stateless_public_chatbot_compatibility
            ),
            public_request_deadline_at=public_request_deadline_at,
        )

    @staticmethod
    async def run_authenticated_deployment(
        db: Session,
        deployment_id: uuid.UUID | str,
        user_inputs: Dict[str, Any],
        client_conversation_id: Optional[str],
        current_user_id: uuid.UUID | str,
        runtime_policy: DeploymentRuntimePolicy,
        request_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        로그인 사용자의 권한 주체로 활성 배포 snapshot을 실행합니다.

        공개 실행(`/run-public`)은 anonymous public-only RAG 경계를 유지해야 하므로
        이 메서드에서만 execution_subject를 주입합니다.
        """
        deployment, app = DeploymentService._get_active_deployment_and_app(
            db,
            deployment_id,
            surface=SURFACE_AUTHENTICATED_RUN,
            runtime_policy=runtime_policy,
        )

        if (
            client_conversation_id is not None
            and deployment.type not in _CHATBOT_DEPLOYMENT_TYPES
        ):
            raise HTTPException(
                status_code=400,
                detail="Conversation control is only supported for chatbot deployments",
            )

        return await DeploymentService._execute_deployment_snapshot(
            db=db,
            app=app,
            deployment=deployment,
            user_inputs=user_inputs,
            trigger_mode="app",
            actor_user_id=current_user_id,
            execution_subject_user_id=current_user_id,
            client_conversation_id=client_conversation_id,
            separate_conversation_control=True,
            request_id=request_id,
            correlation_id=correlation_id,
        )

    @staticmethod
    def get_deployment_run_info(
        db: Session,
        deployment_id: uuid.UUID | str,
        *,
        runtime_policy: DeploymentRuntimePolicy,
    ) -> Dict[str, Any]:
        deployment, app = DeploymentService._get_active_deployment_and_app(
            db,
            deployment_id,
            surface=SURFACE_AUTHENTICATED_RUN_INFO,
            runtime_policy=runtime_policy,
        )
        return {
            "deployment_id": deployment.id,
            "app_id": app.id,
            "workflow_id": app.workflow_id,
            "name": app.name,
            "description": app.description or deployment.description,
            "version": deployment.version,
            "type": deployment.type.value,
            "input_schema": deployment.input_schema,
            "output_schema": deployment.output_schema,
        }

    @staticmethod
    def _get_active_deployment_and_app(
        db: Session,
        deployment_id: uuid.UUID | str,
        *,
        surface: str,
        runtime_policy: DeploymentRuntimePolicy,
    ) -> tuple[WorkflowDeployment, App]:
        deployment = (
            db.query(WorkflowDeployment)
            .filter(WorkflowDeployment.id == deployment_id)
            .first()
        )
        if not deployment:
            raise HTTPException(status_code=404, detail="Deployment not found")

        app = db.query(App).filter(App.id == deployment.app_id).first()
        if not app or not app.workflow_id:
            raise HTTPException(status_code=404, detail="Workflow not found")

        if app.active_deployment_id != deployment.id or not deployment.is_active:
            raise HTTPException(status_code=404, detail="Deployment is inactive")
        if not is_deployment_type_allowed_for_surface(
            deployment.type,
            surface,
            policy=runtime_policy,
        ):
            raise HTTPException(status_code=404, detail="Deployment not found")
        return deployment, app

    @staticmethod
    async def _execute_deployment_snapshot(
        db: Session,
        app: App,
        deployment: WorkflowDeployment,
        user_inputs: Dict[str, Any],
        trigger_mode: str,
        actor_user_id: uuid.UUID | str | None,
        execution_subject_user_id: uuid.UUID | str | None,
        client_conversation_id: Optional[str] = None,
        client_conversation_history: tuple[dict[str, str], ...] | None = None,
        allow_stateless_public_chatbot_compatibility: bool = False,
        separate_conversation_control: bool = False,
        public_request_deadline_at: datetime | None = None,
        request_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        public_client_history_mode = client_conversation_history is not None
        public_stateless_compatibility_mode = False
        if public_client_history_mode and (
            deployment.type is not DeploymentType.CHATBOT
            or trigger_mode != "app"
            or execution_subject_user_id is not None
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Client-held conversation history is only supported for "
                    "public chatbot deployments"
                ),
            )
        is_public_chatbot = (
            deployment.type is DeploymentType.CHATBOT
            and trigger_mode == "app"
            and execution_subject_user_id is None
        )
        if is_public_chatbot and not public_client_history_mode:
            if allow_stateless_public_chatbot_compatibility:
                public_stateless_compatibility_mode = True
            else:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "conversation.history_required",
                        "message": (
                            "Public chatbot requests require a conversation "
                            "history envelope."
                        ),
                    },
                )

        public_transient_mode = (
            public_client_history_mode or public_stateless_compatibility_mode
        )
        if public_transient_mode:
            _remaining_public_request_seconds(public_request_deadline_at)
        dispatch_inputs = dict(user_inputs or {})
        if public_client_history_mode and (
            _LEGACY_MEMORY_MODE_INPUT in dispatch_inputs
            or _LEGACY_CONVERSATION_INPUT in dispatch_inputs
        ):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "conversation.legacy_control_forbidden",
                    "message": (
                        "Legacy public conversation controls are not supported."
                    ),
                },
            )
        if public_stateless_compatibility_mode:
            dispatch_inputs.pop(_LEGACY_MEMORY_MODE_INPUT, None)
            dispatch_inputs.pop(_LEGACY_CONVERSATION_INPUT, None)

        public_conversation_contract = None
        if public_client_history_mode:
            try:
                public_conversation_contract = (
                    resolve_public_chat_conversation_contract(
                        deployment.config,
                        deployment.graph_snapshot,
                        required=True,
                    )
                )
            except PublicChatConversationContractError as error:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": error.code,
                        "message": (
                            "The deployed public conversation consumer "
                            "mapping is unavailable."
                        ),
                    },
                ) from None

        if public_transient_mode:
            _remaining_public_request_seconds(public_request_deadline_at)

        # 예산 초과 차단 — 아래 dispatch try 블록 밖이어야 429가
        # "Engine Execution failed" 500으로 감싸이지 않는다 (BGT-REQ-030~031).
        WorkflowBudgetService.ensure_workflow_budget_allows_execution(
            db,
            workflow_id=app.workflow_id,
            trigger_mode=trigger_mode,
            actor_id=actor_user_id,
        )
        if public_transient_mode:
            _remaining_public_request_seconds(public_request_deadline_at)

        DeploymentService.migrate_legacy_node_secrets(
            db,
            deployment,
            actor_id=(
                uuid.UUID(str(actor_user_id)) if actor_user_id is not None else None
            ),
        )
        if public_transient_mode:
            _remaining_public_request_seconds(public_request_deadline_at)
        graph_data = deployment.graph_snapshot
        try:
            enforce_workflow_configuration_preflight(graph_data, surface="run")
        except WorkflowConfigurationPreflightError as exc:
            raise DeploymentService.workflow_configuration_preflight_blocked(
                exc
            ) from exc

        if public_transient_mode:
            _remaining_public_request_seconds(public_request_deadline_at)

        try:
            # 로깅을 위한 컨텍스트 주입
            # memory_mode 추가 (챗봇 기억 모드 지원)
            declared_inputs = DeploymentService._declared_input_names(deployment)
            legacy_memory_is_business_input = (
                separate_conversation_control
                and _LEGACY_MEMORY_MODE_INPUT in declared_inputs
            )
            legacy_conversation_is_business_input = (
                separate_conversation_control
                and _LEGACY_CONVERSATION_INPUT in declared_inputs
            )

            if (
                client_conversation_id is not None
                and not legacy_conversation_is_business_input
                and _LEGACY_CONVERSATION_INPUT in dispatch_inputs
            ):
                raise HTTPException(
                    status_code=400,
                    detail="Conflicting conversation controls",
                )

            memory_mode_enabled = (
                False
                if public_transient_mode or legacy_memory_is_business_input
                else dispatch_inputs.pop(_LEGACY_MEMORY_MODE_INPUT, False)
            )
            if isinstance(memory_mode_enabled, str):
                memory_mode_enabled = memory_mode_enabled.lower() == "true"

            # 방문자별 대화 격리용 conversation_id (챗봇 멀티턴 기억).
            # memory_mode와 동일하게 dispatch 전에 pop하여 워크플로우 입력 오염을 막는다.
            conversation_id = None if public_transient_mode else client_conversation_id
            if conversation_id is None and not legacy_conversation_is_business_input:
                conversation_id = dispatch_inputs.pop(
                    _LEGACY_CONVERSATION_INPUT,
                    None,
                )
            if conversation_id is not None:
                conversation_id = DeploymentService._validate_client_conversation_id(
                    conversation_id
                )
                if execution_subject_user_id:
                    conversation_id = DeploymentService._authenticated_conversation_id(
                        deployment_id=deployment.id,
                        subject_id=execution_subject_user_id,
                        client_conversation_id=conversation_id,
                    )

            # 챗봇 배포는 기억모드가 항상 켜져 있어야 한다 (클라이언트 값과 무관하게 서버가 강제).
            if (
                deployment.type in _CHATBOT_DEPLOYMENT_TYPES
                and not public_transient_mode
            ):
                memory_mode_enabled = True

            execution_context = {
                "user_id": str(actor_user_id or app.created_by),
                "workflow_id": str(app.workflow_id) if app.workflow_id else None,
                "organization_id": (
                    str(app.organization_id) if app.organization_id else None
                ),
                "app_id": str(app.id),
                "trigger_mode": trigger_mode,  # 실행 모드 (API/앱 배포 실행)
                "deployment_id": str(deployment.id),
                "workflow_version": deployment.version,
                "memory_mode": memory_mode_enabled,  # 기억 모드 추가
                "conversation_id": conversation_id,  # 방문자별 대화 격리 키
            }
            public_request_deadline = (
                public_request_deadline_at if public_transient_mode else None
            )
            if public_transient_mode:
                execution_context["execution_actor"] = {"type": "public"}
                execution_context["suppress_content_persistence"] = True
                execution_context["public_request_deadline_at"] = (
                    public_request_deadline.isoformat()
                )
            if public_client_history_mode:
                remaining_seconds = _remaining_public_request_seconds(
                    public_request_deadline,
                    minimum_seconds=1.0,
                )
                try:
                    history_reference = await store_public_chat_history(
                        client_conversation_history,
                        ttl_seconds=min(
                            PUBLIC_CHAT_REQUEST_TTL_SECONDS,
                            int(remaining_seconds),
                        ),
                        timeout_seconds=min(
                            PUBLIC_CHAT_HISTORY_TRANSIENT_IO_TIMEOUT_SECONDS,
                            remaining_seconds,
                        ),
                    )
                except PublicChatHistoryTransientStoreError:
                    raise HTTPException(
                        status_code=503,
                        detail={
                            "code": "conversation.history_store_unavailable",
                            "message": (
                                "The public conversation transport is unavailable."
                            ),
                        },
                    ) from None
                execution_context["public_chat_history_ref"] = history_reference
                execution_context["public_chat_history_consumer_ref"] = (
                    public_conversation_contract.history_consumer_ref
                )
            if public_stateless_compatibility_mode:
                execution_context["public_chat_stateless_compatibility"] = True
            if request_id:
                execution_context["request_id"] = request_id
            if correlation_id:
                execution_context["correlation_id"] = correlation_id
            if execution_subject_user_id:
                execution_context["execution_subject"] = {
                    "type": "user",
                    "id": str(execution_subject_user_id),
                }

            if public_transient_mode:
                _remaining_public_request_seconds(
                    public_request_deadline,
                    minimum_seconds=1.0,
                )
            workflow_task_name = (
                PUBLIC_CHAT_WORKFLOW_TASK_NAME
                if public_transient_mode
                else "workflow.execute"
            )
            task = send_workflow_task(
                celery_app,
                workflow_task_name,
                args=[graph_data, dispatch_inputs, execution_context],
                kwargs={"is_deployed": True},
                **(
                    {"expires": public_request_deadline}
                    if public_request_deadline is not None
                    else {}
                ),
            )

            # 비동기 폴링 패턴으로 결과 대기 (스레드 풀 고갈 방지)
            import asyncio
            import time

            from celery.result import AsyncResult

            async def wait_for_celery_result(task_id: str, timeout: float = 600):
                """비동기적으로 Celery 결과 대기 (폴링 방식)"""
                result = AsyncResult(task_id, app=celery_app)
                start_time = time.time()

                def forget_public_result() -> None:
                    if not public_transient_mode:
                        return
                    forget = getattr(result, "forget", None)
                    if not callable(forget):
                        return
                    try:
                        forget()
                    except Exception as error:
                        logger.warning(
                            "Public workflow result cleanup failed: error_type=%s",
                            type(error).__name__,
                        )

                while not result.ready():
                    elapsed = time.time() - start_time
                    if elapsed > timeout:
                        raise TimeoutError(
                            f"Workflow execution timed out after {timeout} seconds"
                        )
                    await asyncio.sleep(0.5)  # 비동기 대기 (이벤트 루프 블로킹 방지)

                if result.failed():
                    failure = result.result
                    forget_public_result()
                    raise failure
                payload = result.result
                forget_public_result()
                return payload

            result_timeout = (
                _remaining_public_request_seconds(public_request_deadline)
                if public_transient_mode
                else 600
            )
            result = await wait_for_celery_result(
                task.id,
                timeout=result_timeout,
            )

            if result.get("status") == "success":
                return {
                    "status": "success",
                    "results": result.get("result", {}),
                    "run_id": result.get("run_id"),
                }
            else:
                detail = _safe_deployment_error_detail(result.get("error"))
                raise HTTPException(status_code=500, detail=detail)

        except TimeoutError as e:
            logger.warning(
                "[Deployment] engine execution timed out: deployment_id=%s error_type=%s",
                deployment.id,
                type(e).__name__,
            )
            raise HTTPException(status_code=504, detail="Workflow execution timed out")

        except ValueError as e:
            logger.warning(
                "[Deployment] engine validation failed: deployment_id=%s error_type=%s",
                deployment.id,
                type(e).__name__,
            )
            raise HTTPException(status_code=400, detail="Workflow validation failed")

        except NotImplementedError as e:
            logger.warning(
                "[Deployment] unsupported engine feature: deployment_id=%s error_type=%s",
                deployment.id,
                type(e).__name__,
            )
            raise HTTPException(
                status_code=501, detail="Workflow feature is not supported"
            )

        except HTTPException:
            raise

        except Exception as e:
            logger.error(
                "[Deployment] engine execution failed: deployment_id=%s error_type=%s",
                deployment.id,
                type(e).__name__,
            )
            raise HTTPException(status_code=500, detail="Workflow execution failed")

    @staticmethod
    def _authenticated_conversation_id(
        *,
        deployment_id: uuid.UUID | str,
        subject_id: uuid.UUID | str,
        client_conversation_id: str,
    ) -> str:
        digest = hashlib.sha256(
            (
                "nodease:authenticated-conversation:v1:"
                f"{deployment_id}:{subject_id}:{client_conversation_id}"
            ).encode("utf-8")
        ).hexdigest()
        return f"auth:v1:{digest}"

    @staticmethod
    def _validate_client_conversation_id(value: Any) -> str:
        if not isinstance(value, str):
            raise HTTPException(
                status_code=400,
                detail="Invalid conversation control",
            )
        normalized = value.strip()
        if (
            not normalized
            or len(normalized) > _MAX_LEGACY_CONVERSATION_ID_LENGTH
            or any(
                ord(character) < 32 or ord(character) == 127 for character in normalized
            )
        ):
            raise HTTPException(
                status_code=400,
                detail="Invalid conversation control",
            )
        return normalized

    @staticmethod
    def _declared_input_names(deployment: WorkflowDeployment) -> set[str]:
        input_schema = getattr(deployment, "input_schema", None)
        if not isinstance(input_schema, dict):
            return set()
        variables = input_schema.get("variables")
        if not isinstance(variables, list):
            return set()
        return {
            name
            for variable in variables
            if isinstance(variable, dict)
            and isinstance((name := variable.get("name")), str)
        }

    @staticmethod
    def _extract_input_schema(graph_snapshot: dict) -> dict | None:
        """
        graph_snapshot에서 StartNode의 입력 변수 스키마를 추출합니다.

        Returns:
            {
                "variables": [
                    {"name": "question", "type": "string", "label": "질문"},
                    ...
                ]
            }
        """
        if not graph_snapshot or not graph_snapshot.get("nodes"):
            return None

        for node in graph_snapshot["nodes"]:
            if node.get("type") == "startNode":
                variables = node.get("data", {}).get("variables", [])
                if not variables:
                    return None

                return {
                    "variables": [
                        {
                            "name": var.get("name", ""),
                            "type": var.get("type", "string"),
                            "label": var.get("label", var.get("name", "")),
                        }
                        for var in variables
                        if var.get("name")
                    ]
                }
            elif node.get("type") == "webhookTrigger":
                mappings = node.get("data", {}).get("variable_mappings", [])
                if not mappings:
                    return None

                return {
                    "variables": [
                        {
                            # Webhook의 변수는 모두 string으로 취급하거나,
                            # 필요하면 JSON Path에서 유추해야 하지만 일단 string으로 통일
                            "name": mapping.get("variable_name", ""),
                            "type": "string",
                            "label": mapping.get("variable_name", ""),
                        }
                        for mapping in mappings
                        if mapping.get("variable_name")
                    ]
                }

        return None

    @staticmethod
    def _extract_output_schema(graph_snapshot: dict) -> dict | None:
        """
        graph_snapshot에서 AnswerNode의 출력 스키마를 추출합니다.

        Returns:
            {
                "outputs": [
                    {"variable": "result", "label": "결과"},
                    ...
                ]
            }
        """
        if not graph_snapshot or not graph_snapshot.get("nodes"):
            return None

        for node in graph_snapshot["nodes"]:
            if node.get("type") == "answerNode":
                outputs = node.get("data", {}).get("outputs", [])
                if not outputs:
                    return None

                return {
                    "outputs": [
                        {
                            "variable": output.get("variable", ""),
                            "label": output.get("label", output.get("variable", "")),
                        }
                        for output in outputs
                        if output.get("variable")
                    ]
                }

        return None

    @staticmethod
    def _find_schedule_trigger_node(graph_snapshot: dict) -> dict | None:
        """
        graph_snapshot에서 ScheduleTrigger 노드를 찾습니다.

        Returns:
            ScheduleTrigger 노드 객체 또는 None
        """
        if not graph_snapshot or not graph_snapshot.get("nodes"):
            return None

        for node in graph_snapshot["nodes"]:
            if node.get("type") == "scheduleTrigger":
                return node

        return None

    @staticmethod
    def _ensure_schedule_record(
        db: Session,
        deployment: WorkflowDeployment,
        *,
        runtime_policy: DeploymentRuntimePolicy,
    ) -> Schedule | None:
        if not is_deployment_type_allowed_for_surface(
            deployment.type,
            SURFACE_SCHEDULE_RUN,
            policy=runtime_policy,
        ):
            return None

        schedule_trigger_node = DeploymentService._find_schedule_trigger_node(
            deployment.graph_snapshot
        )
        if not schedule_trigger_node:
            return None

        existing = (
            db.query(Schedule).filter(Schedule.deployment_id == deployment.id).first()
        )
        if existing:
            return existing

        schedule = Schedule(
            deployment_id=deployment.id,
            node_id=schedule_trigger_node["id"],
            cron_expression=schedule_trigger_node["data"]["cron_expression"],
            timezone=schedule_trigger_node["data"].get("timezone", "UTC"),
        )
        db.add(schedule)
        db.flush()
        return schedule

    @staticmethod
    def _deactivate_other_deployments(
        db: Session,
        app_id: uuid.UUID,
        current_deployment_id: uuid.UUID,
        scheduler_service=None,
    ):
        """
        같은 앱의 다른 활성 배포들을 모두 비활성화, 핼퍼 함수로 사용

        Args:
            db: 데이터베이스 세션
            app_id: 앱 ID
            current_deployment_id: 현재 배포 ID (제외할 배포)
            scheduler_service: SchedulerService 인스턴스
        """
        other_deployments = (
            db.query(WorkflowDeployment)
            .filter(
                WorkflowDeployment.app_id == app_id,
                WorkflowDeployment.id != current_deployment_id,
                WorkflowDeployment.is_active.is_(True),
            )
            .all()
        )

        for other in other_deployments:
            other.is_active = False

            # Schedule Job 제거
            other_schedule = (
                db.query(Schedule).filter(Schedule.deployment_id == other.id).first()
            )
            if other_schedule and scheduler_service:
                try:
                    scheduler_service.remove_schedule(other_schedule.id)
                except Exception as e:
                    logger.error(f"[Deployment] ✗ 스케줄 제거 실패: {e}")

    @staticmethod
    def toggle_deployment(
        db: Session,
        deployment_id: uuid.UUID,
        scheduler_service=None,
        *,
        runtime_policy: DeploymentRuntimePolicy,
        user_id: uuid.UUID | str | None = None,
        require_public_chat_conversation_contract: bool = False,
        auth_secret_lifecycle_mutations_enabled: bool = False,
    ) -> WorkflowDeployment:
        """
        배포의 is_active 상태를 토글합니다.

        활성화 시: 같은 앱의 다른 모든 배포를 비활성화합니다 (단일 활성화 정책)

        Args:
            db: 데이터베이스 세션
            deployment_id: 배포 ID
            scheduler_service: SchedulerService 인스턴스 (스케줄 관리용)

        Returns:
            업데이트된 WorkflowDeployment 객체

        Raises:
            HTTPException: 배포를 찾을 수 없는 경우
        """
        # 1. 배포와 App을 공통 lifecycle 순서로 잠금
        initial_deployment = (
            db.query(WorkflowDeployment)
            .filter(WorkflowDeployment.id == deployment_id)
            .first()
        )
        if not initial_deployment:
            raise HTTPException(status_code=404, detail="Deployment not found")

        app = lock_app_for_lifecycle(db, initial_deployment.app_id)
        if not app:
            raise HTTPException(status_code=404, detail="App not found")
        deployment = (
            db.query(WorkflowDeployment)
            .filter(
                WorkflowDeployment.id == deployment_id,
                WorkflowDeployment.app_id == app.id,
            )
            .populate_existing()
            .with_for_update()
            .first()
        )
        if not deployment:
            raise HTTPException(status_code=404, detail="Deployment not found")

        # 2. is_active 토글
        new_state = not deployment.is_active
        if new_state:
            if not app:
                raise HTTPException(status_code=404, detail="App not found")
            workflow_count = (
                db.query(func.count(Workflow.id))
                .filter(Workflow.app_id == app.id)
                .scalar()
                or 0
            )
            if workflow_count > 1:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "deployment.reactivation_provenance_unavailable",
                        "message": (
                            "This deployment cannot be reactivated because its "
                            "source Workflow cannot be verified. Create a new "
                            "deployment from the current primary Workflow."
                        ),
                    },
                )
            DeploymentService.validate_public_chat_conversation_config(
                deployment_type=deployment.type,
                config=getattr(deployment, "config", None),
                graph_snapshot=deployment.graph_snapshot,
                required=require_public_chat_conversation_contract,
            )
            try:
                WorkflowService.validate_mail_credential_references(
                    db,
                    deployment.graph_snapshot,
                    user_id=str(user_id) if user_id is not None else "",
                    organization_id=getattr(app, "organization_id", None),
                    require_resolved=False,
                )
            except HTTPException as exc:
                if exc.status_code == 403:
                    raise
                reason_code = (
                    "mail_credential_unavailable"
                    if exc.detail == "resource.not_found"
                    else "node_configuration_invalid"
                )
                raise DeploymentService.workflow_configuration_validation_blocked(
                    deployment.graph_snapshot,
                    surface="deployment",
                    reason_code=reason_code,
                ) from exc
            try:
                enforce_workflow_configuration_preflight(
                    deployment.graph_snapshot,
                    surface="deployment",
                )
            except WorkflowConfigurationPreflightError as exc:
                raise DeploymentService.workflow_configuration_preflight_blocked(
                    exc
                ) from exc
            DeploymentService._enforce_knowledge_preflight(
                db,
                app=app,
                deployment_type=deployment.type,
                graph_snapshot=deployment.graph_snapshot,
                principal_id=(uuid.UUID(str(user_id)) if user_id is not None else None),
            )
            WorkflowService.validate_external_node_storage_boundaries(
                deployment.graph_snapshot,
                require_resolved=False,
            )
            DeploymentService.ensure_auth_secret_ready_for_activation(
                app,
                deployment_type=deployment.type,
                is_active=True,
                lifecycle_mutations_enabled=(auth_secret_lifecycle_mutations_enabled),
            )

        deployment.is_active = new_state

        # 3. 활성화하는 경우: 같은 앱의 다른 배포를 모두 비활성화
        if new_state:
            DeploymentService._deactivate_other_deployments(
                db, deployment.app_id, deployment_id, scheduler_service
            )

        # 3.1. App의 active_deployment_id 동기화
        if app:
            if new_state:
                app.active_deployment_id = deployment.id
            elif app.active_deployment_id == deployment.id:
                app.active_deployment_id = None

        # 4. 현재 배포의 Schedule 처리
        schedule = (
            db.query(Schedule).filter(Schedule.deployment_id == deployment_id).first()
        )

        if not is_deployment_type_allowed_for_surface(
            deployment.type,
            SURFACE_SCHEDULE_RUN,
            policy=runtime_policy,
        ):
            if schedule:
                if scheduler_service:
                    scheduler_service.remove_schedule(schedule.id)
                db.delete(schedule)
            schedule = None
        elif deployment.is_active and not schedule:
            schedule = DeploymentService._ensure_schedule_record(
                db,
                deployment,
                runtime_policy=runtime_policy,
            )

        if schedule and scheduler_service:
            if deployment.is_active:
                try:
                    scheduler_service.add_schedule(schedule, db)
                except ScheduleConfigurationError:
                    db.rollback()
                    raise HTTPException(
                        status_code=422,
                        detail={
                            "code": "deployment.schedule_configuration_invalid",
                            "message": "Schedule configuration is invalid.",
                        },
                    ) from None
            else:
                scheduler_service.remove_schedule(schedule.id)

        db.commit()
        db.refresh(deployment)

        return deployment

    @staticmethod
    def delete_deployment(
        db: Session, deployment_id: uuid.UUID, scheduler_service=None
    ) -> Dict[str, str]:
        """
        배포를 삭제합니다.

        Args:
            db: 데이터베이스 세션
            deployment_id: 배포 ID
            scheduler_service: SchedulerService 인스턴스 (스케줄 관리용)

        Returns:
            삭제 결과 메시지

        Raises:
            HTTPException: 배포를 찾을 수 없는 경우
        """
        # 1. 배포와 App을 공통 lifecycle 순서로 잠금
        initial_deployment = (
            db.query(WorkflowDeployment)
            .filter(WorkflowDeployment.id == deployment_id)
            .first()
        )
        if not initial_deployment:
            raise HTTPException(status_code=404, detail="Deployment not found")
        app = lock_app_for_lifecycle(db, initial_deployment.app_id)
        if app is None:
            raise HTTPException(status_code=404, detail="Deployment not found")
        deployment = (
            db.query(WorkflowDeployment)
            .filter(
                WorkflowDeployment.id == deployment_id,
                WorkflowDeployment.app_id == app.id,
            )
            .populate_existing()
            .with_for_update()
            .first()
        )
        if deployment is None:
            raise HTTPException(status_code=404, detail="Deployment not found")

        # 2. Schedule 제거
        schedule = (
            db.query(Schedule).filter(Schedule.deployment_id == deployment_id).first()
        )
        if schedule:
            if scheduler_service:
                scheduler_service.remove_schedule(schedule.id)
            db.delete(schedule)

        # 3. App의 active_deployment_id 업데이트
        if str(app.active_deployment_id) == str(deployment_id):
            app.active_deployment_id = None

        # 4. 배포 레코드 삭제
        db.delete(deployment)
        db.commit()

        return {"message": f"Deployment {deployment_id} deleted successfully"}
