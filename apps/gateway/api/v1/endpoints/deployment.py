import uuid
from typing import Annotated, List

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from apps.gateway.auth.dependencies import get_current_user
from apps.gateway.auth.permissions import ensure_workflow_permission
from apps.gateway.core.config import settings
from apps.gateway.application.deployment.browser_access_errors import (
    BrowserAccessPolicyError,
    BrowserAccessResourceHidden,
)
from apps.gateway.application.deployment.browser_access_models import (
    BrowserAccessRevision,
    BrowserAccessRevisionCommand,
)
from apps.gateway.application.deployment.errors import DeploymentPreflightBlocked
from apps.gateway.api.deps import (
    get_deployment_runtime_policy,
    require_json_content_type,
)
from apps.gateway.composition.deployment import (
    build_browser_access_revision_use_case,
    build_public_browser_access_use_case,
    deployment_browser_access_environment,
)
from apps.gateway.services.knowledge_deployment_preflight_service import (
    deployment_preflight_blocked_http_exception,
)
from apps.gateway.services.organization_context import resolve_active_organization_id
from apps.gateway.utils.api_errors import raise_api_error
from apps.gateway.utils.audit import audit
from apps.gateway.services.deployment_service import (
    DeploymentAuthSecretPreflightError,
    DeploymentService,
)
from apps.gateway.services.deployment_parameter_optimization_service import (
    DeploymentParameterOptimizationConfigurationError,
    DeploymentParameterOptimizationService,
)
from apps.shared.audit.actions import AuditAction
from apps.shared.audit.context import AuditActor, clear_current_actor, set_current_actor
from apps.shared.audit.logger import record_audit
from apps.shared.db.models.app import App
from apps.shared.db.models.user import User
from apps.shared.db.models.workflow_deployment import DeploymentType, WorkflowDeployment
from apps.shared.domain.deployment_runtime_policy import (
    SURFACE_PUBLIC_INFO,
    DeploymentRuntimePolicy,
    is_deployment_type_allowed_for_surface,
)
from apps.shared.domain.public_chat_conversation import (
    public_chat_conversation_capability,
)
from apps.shared.domain.provider_execution_capability import CapabilityPurpose
from apps.shared.domain.workflow_node_location import (
    CanonicalWorkflowNodeLocation,
    WorkflowNodeLocationError,
)
from apps.shared.db.session import get_db
from apps.shared.schemas.deployment import (
    AuthenticatedDeploymentRunRequest,
    DeploymentBrowserAccessPolicy,
    DeploymentBrowserAccessProjection,
    DeploymentBrowserAccessRevisionCreate,
    DeploymentCreate,
    DeploymentLLMCredentialPolicyResponse,
    DeploymentLLMCredentialPolicyUpsert,
    DeploymentPreflightRequest,
    DeploymentPreflightResponse,
    DeploymentParameterOptimizationConfig,
    DeploymentParameterOptimizationStatus,
    DeploymentResponse,
    DeploymentRunInfoResponse,
)
from apps.shared.services.provider_execution_capability import (
    DeploymentCredentialPolicyCommand,
    DeploymentCredentialPolicyView,
    ProviderExecutionCapabilityService,
    ProviderExecutionPolicyError,
)

router = APIRouter()

_AUTH_SECRET_PREFLIGHT_ERROR_STATUS = {
    "app.auth_secret_lifecycle_unavailable": 503,
    "deployment.app_auth_secret_required": 409,
}


def _request_id_from_request(request: Request) -> str | None:
    return getattr(
        getattr(request, "state", None), "request_id", None
    ) or request.headers.get("x-request-id")


def _raise_deployment_auth_secret_preflight_error(
    request: Request,
    error: DeploymentAuthSecretPreflightError,
) -> None:
    status_code = _AUTH_SECRET_PREFLIGHT_ERROR_STATUS.get(error.code)
    if status_code is None:
        raise_api_error(
            request,
            500,
            "deployment.auth_secret_preflight_failed",
            "App authentication secret preflight failed.",
        )
    raise_api_error(
        request,
        status_code,
        error.code,
        error.message,
        error.details,
    )


def _deployment_app_and_workflow_id(db: Session, deployment_id: str):
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
    return deployment, app, app.workflow_id


def _deployment_workflow_id(db: Session, deployment_id: str):
    _, _, workflow_id = _deployment_app_and_workflow_id(db, deployment_id)
    return workflow_id


def _ensure_app_matches_active_organization(
    db: Session,
    request: Request,
    current_user: User,
    app: App,
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
    app_organization_id = getattr(app, "organization_id", None)
    if app_organization_id is not None and str(app_organization_id) != str(
        organization_id
    ):
        raise HTTPException(status_code=404, detail="Deployment not found")


def _deployment_toggle_audit_action(deployment: WorkflowDeployment, app: App) -> str:
    if (
        not deployment.is_active
        and app.active_deployment_id is not None
        and app.active_deployment_id != deployment.id
    ):
        return AuditAction.DEPLOYMENT_ACTIVATE_PREVIOUS
    return AuditAction.DEPLOYMENT_TOGGLE


def _deployment_audit_actor(user: User) -> AuditActor:
    return AuditActor(
        actor_id=str(user.id),
        actor_type="user",
        snapshot={
            "id": str(user.id),
            "email": getattr(user, "email", None),
            "name": getattr(user, "name", None),
        },
    )


def _record_deployment_toggle_audit(
    action: str,
    current_user: User,
    deployment_id: str,
    status: str,
    metadata: dict | None = None,
) -> None:
    actor = _deployment_audit_actor(current_user)
    audit_metadata = {"actor": actor.snapshot}
    if metadata:
        audit_metadata.update(metadata)
    record_audit(
        action=action,
        category="action",
        actor_id=current_user.id,
        actor_type="user",
        target_type="deployment",
        target_id=deployment_id,
        status=status,
        metadata=audit_metadata,
    )


def _raise_browser_access_policy_error(exc: BrowserAccessPolicyError) -> None:
    detail = {
        "code": exc.code,
        "message": "Browser access policy is invalid.",
    }
    if exc.origin_index is not None:
        detail["field"] = (
            f"browser_access_policy.embedding.parent_origins[{exc.origin_index}]"
        )
    raise HTTPException(status_code=422, detail=detail) from None


def _deployment_response_from_browser_revision(
    revision: BrowserAccessRevision,
) -> DeploymentResponse:
    return DeploymentResponse(
        id=revision.id,
        app_id=revision.app_id,
        version=revision.version,
        type=DeploymentType(revision.deployment_type),
        graph_snapshot=revision.graph_snapshot,
        config=revision.config,
        input_schema=revision.input_schema,
        output_schema=revision.output_schema,
        description=revision.description,
        created_by=revision.created_by,
        created_at=revision.created_at,
        is_active=revision.is_active,
        browser_access_policy=revision.browser_access_policy,
        url_slug=revision.url_slug,
    )


def _deployment_llm_credential_policy_response(
    policy: DeploymentCredentialPolicyView,
) -> DeploymentLLMCredentialPolicyResponse:
    return DeploymentLLMCredentialPolicyResponse(
        id=policy.id,
        deployment_id=policy.deployment_id,
        deployment_version=policy.deployment_version,
        node_id=policy.node_id,
        container_path=[
            {"kind": kind, "node_id": node_id}
            for kind, node_id in policy.container_path
        ],
        purpose=policy.purpose.value,
        model_id=policy.model_id,
        credential_id=policy.credential_id,
        policy_revision=policy.policy_revision,
        is_active=policy.is_active,
        created_at=policy.created_at,
        updated_at=policy.updated_at,
    )


def _deployment_llm_policy_audit_metadata(kwargs: dict) -> dict[str, str]:
    node_id = kwargs.get("node_id")
    policy_in = kwargs.get("policy_in")
    if not isinstance(node_id, str) or not isinstance(
        policy_in, DeploymentLLMCredentialPolicyUpsert
    ):
        return {}
    try:
        location = CanonicalWorkflowNodeLocation(
            tuple(
                (segment.kind, segment.node_id) for segment in policy_in.container_path
            ),
            node_id,
        )
    except WorkflowNodeLocationError:
        return {}
    return {
        "node_location_ref": location.safe_reference,
        "purpose": policy_in.purpose,
        "model_id": str(policy_in.model_id),
    }


def _raise_provider_execution_policy_error(
    exc: ProviderExecutionPolicyError,
) -> None:
    if exc.code == "resource_not_found":
        raise HTTPException(status_code=404, detail="Deployment not found") from None
    if exc.code == "permission_denied":
        raise HTTPException(status_code=403, detail="permission.denied") from None
    if exc.code == "query_embedding_rollout_unavailable":
        status_code = 503
    else:
        status_code = (
            409
            if exc.code
            in {
                "selection_ambiguous",
                "capability_attempt_reused",
                "capability_stale",
            }
            else 422
        )
    raise HTTPException(
        status_code=status_code,
        detail={
            "code": exc.code,
            "message": "LLM credential policy is not ready.",
        },
    ) from None


@router.post("", response_model=DeploymentResponse)
@audit(AuditAction.WORKFLOW_DEPLOY)
def create_deployment(
    deployment_in: DeploymentCreate,
    request: Request,
    runtime_policy: Annotated[
        DeploymentRuntimePolicy,
        Depends(get_deployment_runtime_policy),
    ],
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    워크플로우를 배포합니다.
    [TEST] bugfix/KAN-000, gateway 배포를 위해 주석 추가
    """
    app = db.query(App).filter(App.id == deployment_in.app_id).first()
    if not app or not app.workflow_id:
        raise HTTPException(status_code=404, detail="App not found")
    ensure_workflow_permission(db, current_user, app.workflow_id, "deploy")
    try:
        return DeploymentService.create_deployment(
            db,
            deployment_in,
            current_user.id,
            observed_workflow_id=app.workflow_id,
            runtime_policy=runtime_policy,
            require_public_chat_conversation_contract=(
                settings.PUBLIC_CHAT_CONVERSATION_ROLLOUT_MODE == "strict"
            ),
            auth_secret_lifecycle_mutations_enabled=(
                settings.APP_AUTH_SECRET_LIFECYCLE_MODE == "active"
            ),
        )
    except DeploymentAuthSecretPreflightError as error:
        _raise_deployment_auth_secret_preflight_error(request, error)


@router.post("/preflight", response_model=DeploymentPreflightResponse)
def preview_deployment_preflight(
    preflight_in: DeploymentPreflightRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    배포 graph snapshot과 deployment type 기준으로 runtime availability를 검사합니다.
    """
    app = db.query(App).filter(App.id == preflight_in.app_id).first()
    if not app or not app.workflow_id:
        raise HTTPException(status_code=404, detail="App not found")
    ensure_workflow_permission(db, current_user, app.workflow_id, "deploy")
    browser_access_policy = DeploymentService.normalize_browser_access_policy(
        preflight_in.type,
        preflight_in.browser_access_policy,
    )
    graph_snapshot = DeploymentService._resolve_graph_snapshot(
        db,
        app.workflow_id,
        preflight_in.graph_snapshot,
    )
    DeploymentService.validate_public_chat_conversation_config(
        deployment_type=preflight_in.type,
        config=preflight_in.config,
        graph_snapshot=graph_snapshot,
        required=(settings.PUBLIC_CHAT_CONVERSATION_ROLLOUT_MODE == "strict"),
    )
    try:
        result = DeploymentService.preview_knowledge_preflight(
            db,
            app=app,
            deployment_type=preflight_in.type,
            graph_snapshot=graph_snapshot,
            audience_hint=preflight_in.audience,
            is_active=preflight_in.is_active,
            principal_id=current_user.id,
            auth_secret_lifecycle_mutations_enabled=(
                settings.APP_AUTH_SECRET_LIFECYCLE_MODE == "active"
            ),
        )
    except DeploymentAuthSecretPreflightError as error:
        _raise_deployment_auth_secret_preflight_error(request, error)
    result.normalized_browser_access_policy = (
        DeploymentBrowserAccessPolicy.model_validate(browser_access_policy.to_dict())
        if browser_access_policy is not None
        else None
    )
    return result


@router.get("", response_model=List[DeploymentResponse])
def get_deployments(
    app_id: str = None,
    workflow_id: str = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    특정 앱의 배포 이력을 조회합니다.
    app_id 또는 workflow_id 중 하나는 필수입니다.
    """
    target_workflow_id = workflow_id
    if app_id:
        app = db.query(App).filter(App.id == app_id).first()
        if not app:
            return []
        if not app.workflow_id:
            return []
        target_workflow_id = app.workflow_id
        ensure_workflow_permission(db, current_user, target_workflow_id, "read")
        if workflow_id:
            try:
                supplied_workflow_id = uuid.UUID(str(workflow_id))
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400, detail="app_id does not match workflow_id"
                )
            if uuid.UUID(str(target_workflow_id)) != supplied_workflow_id:
                raise HTTPException(
                    status_code=400, detail="app_id does not match workflow_id"
                )
    elif target_workflow_id:
        ensure_workflow_permission(db, current_user, target_workflow_id, "read")
    else:
        return []
    return DeploymentService.list_deployments(
        db,
        app_id=app_id,
        workflow_id=workflow_id,
        skip=skip,
        limit=limit,
    )


@router.get("/nodes", response_model=List[dict])
def list_workflow_nodes(
    excluded_app_id: str = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    배포된 워크플로우 노드 목록을 조회합니다. (재사용 가능한 모듈)
    """
    return DeploymentService.list_workflow_node_deployments(
        db, current_user.id, excluded_app_id=excluded_app_id
    )


@router.post(
    "/{source_deployment_id}/browser-access-revisions",
    response_model=DeploymentResponse,
    status_code=201,
)
def create_browser_access_revision(
    source_deployment_id: uuid.UUID,
    revision_in: DeploymentBrowserAccessRevisionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    source_deployment, _, workflow_id = _deployment_app_and_workflow_id(
        db,
        str(source_deployment_id),
    )
    ensure_workflow_permission(db, current_user, workflow_id, "deploy")
    DeploymentService.migrate_legacy_node_secrets(
        db,
        source_deployment,
        actor_id=current_user.id,
    )
    scheduler_service = None
    if revision_in.is_active:
        from apps.gateway.services.scheduler_service import get_scheduler_service

        scheduler_service = get_scheduler_service()
    use_case = build_browser_access_revision_use_case(
        db,
        actor=current_user,
        scheduler_service=scheduler_service,
    )
    try:
        revision = use_case.execute(
            BrowserAccessRevisionCommand(
                source_deployment_id=source_deployment_id,
                actor_id=current_user.id,
                browser_access_policy=revision_in.browser_access_policy.model_dump(
                    mode="python"
                ),
                is_active=revision_in.is_active,
                environment=deployment_browser_access_environment(),
            )
        )
    except BrowserAccessPolicyError as exc:
        _raise_browser_access_policy_error(exc)
    except BrowserAccessResourceHidden:
        raise HTTPException(status_code=404, detail="Deployment not found") from None
    except DeploymentPreflightBlocked as exc:
        raise deployment_preflight_blocked_http_exception(exc.result) from exc
    return _deployment_response_from_browser_revision(revision)


@router.get(
    "/public/{url_slug}/browser-access",
    response_model=DeploymentBrowserAccessProjection,
)
def get_public_browser_access_policy(
    url_slug: str,
    response: Response,
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store"
    try:
        projection = build_public_browser_access_use_case(db).execute(
            url_slug,
            environment=deployment_browser_access_environment(),
        )
    except BrowserAccessResourceHidden:
        raise HTTPException(
            status_code=404,
            detail="Deployment not found",
            headers={"Cache-Control": "no-store"},
        ) from None
    return DeploymentBrowserAccessProjection(
        contract_version=projection.contract_version,
        deployment_version=projection.deployment_version,
        embedding={
            "enabled": projection.enabled,
            "frame_ancestors": list(projection.frame_ancestors),
        },
    )


@router.get(
    "/{deployment_id}/llm-credential-policies",
    response_model=List[DeploymentLLMCredentialPolicyResponse],
)
def list_deployment_llm_credential_policies(
    deployment_id: uuid.UUID,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List manager-visible server-side policies without exposing a secret."""
    organization_id = resolve_active_organization_id(
        db,
        request,
        x_organization_id,
        current_user.id,
    )
    try:
        policies = ProviderExecutionCapabilityService.list_deployment_policies(
            db,
            actor_id=current_user.id,
            organization_id=organization_id,
            deployment_id=deployment_id,
        )
    except ProviderExecutionPolicyError as exc:
        _raise_provider_execution_policy_error(exc)
    return [_deployment_llm_credential_policy_response(policy) for policy in policies]


@router.put(
    "/{deployment_id}/llm-credential-policies/{node_id}",
    response_model=DeploymentLLMCredentialPolicyResponse,
)
@audit(
    AuditAction.DEPLOYMENT_LLM_CREDENTIAL_POLICY_UPSERT,
    target_param="deployment_id",
    metadata_factory=_deployment_llm_policy_audit_metadata,
)
def replace_deployment_llm_credential_policy(
    deployment_id: uuid.UUID,
    node_id: str,
    policy_in: DeploymentLLMCredentialPolicyUpsert,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a revisioned manager policy for one deployment snapshot LLM node."""
    organization_id = resolve_active_organization_id(
        db,
        request,
        x_organization_id,
        current_user.id,
    )
    try:
        policy = ProviderExecutionCapabilityService.replace_deployment_policy(
            db,
            actor_id=current_user.id,
            command=DeploymentCredentialPolicyCommand(
                organization_id=organization_id,
                deployment_id=deployment_id,
                node_id=node_id,
                model_id=policy_in.model_id,
                credential_id=policy_in.credential_id,
                purpose=CapabilityPurpose(policy_in.purpose),
                container_path=tuple(
                    (segment.kind, segment.node_id)
                    for segment in policy_in.container_path
                ),
            ),
            query_embedding_policy_writes_enabled=(
                settings.QUERY_EMBEDDING_POLICY_WRITE_MODE == "active"
            ),
        )
        db.commit()
    except ProviderExecutionPolicyError as exc:
        db.rollback()
        _raise_provider_execution_policy_error(exc)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "code": "selection_ambiguous",
                "message": "LLM credential policy is not ready.",
            },
        ) from None
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail={
                "code": "deployment.llm_credential_policy_failed",
                "message": "LLM credential policy could not be updated.",
            },
        ) from None
    return _deployment_llm_credential_policy_response(policy)


@router.get("/{deployment_id}/run-info", response_model=DeploymentRunInfoResponse)
def get_authenticated_deployment_run_info(
    deployment_id: str,
    request: Request,
    runtime_policy: Annotated[
        DeploymentRuntimePolicy,
        Depends(get_deployment_runtime_policy),
    ],
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    로그인 사용자 실행 화면에 필요한 safe 배포 정보를 조회합니다.
    """
    deployment, app, workflow_id = _deployment_app_and_workflow_id(
        db,
        deployment_id,
    )
    _ensure_app_matches_active_organization(
        db,
        request,
        current_user,
        app,
        x_organization_id,
    )
    ensure_workflow_permission(db, current_user, workflow_id, "execute")
    return DeploymentService.get_deployment_run_info(
        db,
        deployment.id,
        runtime_policy=runtime_policy,
    )


@router.post(
    "/{deployment_id}/run",
    dependencies=[Depends(require_json_content_type)],
)
async def run_authenticated_deployment(
    deployment_id: str,
    request: Request,
    runtime_policy: Annotated[
        DeploymentRuntimePolicy,
        Depends(get_deployment_runtime_policy),
    ],
    request_body: AuthenticatedDeploymentRunRequest | None = Body(default=None),
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    로그인 사용자의 권한 주체로 활성 배포 snapshot을 실행합니다.
    """
    _, app, workflow_id = _deployment_app_and_workflow_id(db, deployment_id)
    _ensure_app_matches_active_organization(
        db,
        request,
        current_user,
        app,
        x_organization_id,
    )
    ensure_workflow_permission(db, current_user, workflow_id, "execute")

    request_body = request_body or AuthenticatedDeploymentRunRequest()
    inputs = request_body.inputs
    if not isinstance(inputs, dict):
        raise HTTPException(status_code=400, detail="inputs must be an object")

    client_conversation_id = (
        str(request_body.conversation.client_id)
        if request_body.conversation is not None
        else None
    )

    return await DeploymentService.run_authenticated_deployment(
        db=db,
        deployment_id=deployment_id,
        user_inputs=inputs,
        client_conversation_id=client_conversation_id,
        current_user_id=current_user.id,
        runtime_policy=runtime_policy,
        request_id=_request_id_from_request(request),
        correlation_id=request.headers.get("x-correlation-id"),
    )


@router.get("/{deployment_id}", response_model=DeploymentResponse)
def get_deployment(
    deployment_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    특정 배포 ID의 상세 정보를 조회합니다.
    """
    workflow_id = _deployment_workflow_id(db, deployment_id)
    ensure_workflow_permission(db, current_user, workflow_id, "read")
    return DeploymentService.get_deployment(db, deployment_id)


@router.get(
    "/{deployment_id}/parameter-optimization",
    response_model=DeploymentParameterOptimizationStatus,
)
def get_deployment_parameter_optimization(
    deployment_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """현재 배포의 LLM 파라미터 자동 최적화 수집·예산 상태를 조회합니다."""
    deployment, _, workflow_id = _deployment_app_and_workflow_id(db, deployment_id)
    ensure_workflow_permission(db, current_user, workflow_id, "read")
    return DeploymentParameterOptimizationService.summary_for_deployment(
        db,
        deployment.id,
    )


@router.patch(
    "/{deployment_id}/parameter-optimization",
    response_model=DeploymentParameterOptimizationStatus,
)
def update_deployment_parameter_optimization(
    deployment_id: str,
    config: DeploymentParameterOptimizationConfig,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """배포를 다시 만들지 않고 자동 최적화의 수집 주기와 검증 예산을 조정합니다."""
    deployment, _, workflow_id = _deployment_app_and_workflow_id(db, deployment_id)
    ensure_workflow_permission(db, current_user, workflow_id, "deploy")

    try:
        deployment_config = dict(deployment.config or {})
        deployment_config["parameter_optimization"] = config.model_dump(mode="json")
        deployment.config = deployment_config
        if config.enabled:
            DeploymentParameterOptimizationService.update_plan(
                db,
                deployment=deployment,
                workflow_id=workflow_id,
                config=config,
            )
        else:
            DeploymentParameterOptimizationService.disable_plan(
                db,
                deployment_id=deployment.id,
            )
        db.commit()
    except DeploymentParameterOptimizationConfigurationError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return DeploymentParameterOptimizationService.summary_for_deployment(
        db,
        deployment.id,
    )


@router.get("/public/{url_slug}/info")
def get_deployment_info_public(
    url_slug: str,
    response: Response,
    runtime_policy: Annotated[
        DeploymentRuntimePolicy,
        Depends(get_deployment_runtime_policy),
    ],
    db: Session = Depends(get_db),
):
    """
    배포 정보 공개 조회 (웹 앱/임베딩용, 인증 불필요)

    공유 페이지에서 입력 폼을 동적으로 생성하기 위해
    input_schema와 output_schema를 조회합니다.

    """
    response.headers["Cache-Control"] = "no-store"

    from fastapi import HTTPException

    from apps.shared.db.models.app import App
    from apps.shared.db.models.workflow_deployment import WorkflowDeployment
    from apps.shared.schemas.deployment import DeploymentInfoResponse

    # 1. url_slug로 App 조회
    app = db.query(App).filter(App.url_slug == url_slug).first()
    if not app:
        raise HTTPException(status_code=404, detail="App not found")

    # 2. 활성 배포 조회
    if not app.active_deployment_id:
        raise HTTPException(
            status_code=404, detail="No active deployment found for this app"
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
        raise HTTPException(status_code=404, detail="Active deployment not found")

    if not deployment.is_active:
        raise HTTPException(status_code=404, detail="Deployment is inactive")
    if not is_deployment_type_allowed_for_surface(
        deployment.type,
        SURFACE_PUBLIC_INFO,
        policy=runtime_policy,
    ):
        raise HTTPException(status_code=404, detail="Active deployment not found")

    return DeploymentInfoResponse(
        url_slug=app.url_slug,
        name=app.name,
        version=deployment.version,
        description=deployment.description,
        type=deployment.type.value,
        input_schema=deployment.input_schema,
        output_schema=deployment.output_schema,
        public_conversation_contract=(
            public_chat_conversation_capability(
                deployment.config,
                deployment.graph_snapshot,
            )
            if deployment.type is DeploymentType.CHATBOT
            else None
        ),
    )


@router.patch("/{deployment_id}/toggle", response_model=DeploymentResponse)
def toggle_deployment(
    deployment_id: str,
    request: Request,
    runtime_policy: Annotated[
        DeploymentRuntimePolicy,
        Depends(get_deployment_runtime_policy),
    ],
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    배포의 is_active 상태를 토글합니다.
    """
    from apps.gateway.services.scheduler_service import get_scheduler_service

    deployment, app, workflow_id = _deployment_app_and_workflow_id(db, deployment_id)
    ensure_workflow_permission(db, current_user, workflow_id, "deploy")
    audit_action = _deployment_toggle_audit_action(deployment, app)
    actor = _deployment_audit_actor(current_user)
    token = set_current_actor(actor)
    try:
        scheduler = get_scheduler_service()
        result = DeploymentService.toggle_deployment(
            db,
            deployment_id,
            scheduler,
            runtime_policy=runtime_policy,
            user_id=current_user.id,
            require_public_chat_conversation_contract=(
                settings.PUBLIC_CHAT_CONVERSATION_ROLLOUT_MODE == "strict"
            ),
            auth_secret_lifecycle_mutations_enabled=(
                settings.APP_AUTH_SECRET_LIFECYCLE_MODE == "active"
            ),
        )
    except Exception as e:
        _record_deployment_toggle_audit(
            audit_action,
            current_user,
            deployment_id,
            "failure",
            {"error": str(e)},
        )
        if isinstance(e, DeploymentAuthSecretPreflightError):
            _raise_deployment_auth_secret_preflight_error(request, e)
        raise
    else:
        _record_deployment_toggle_audit(
            audit_action,
            current_user,
            deployment_id,
            "success",
        )
        return result
    finally:
        clear_current_actor(token)


@router.delete("/{deployment_id}")
@audit(AuditAction.DEPLOYMENT_DELETE, target_param="deployment_id")
def delete_deployment(
    deployment_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    배포를 삭제합니다.
    """
    from apps.gateway.services.scheduler_service import get_scheduler_service

    workflow_id = _deployment_workflow_id(db, deployment_id)
    ensure_workflow_permission(db, current_user, workflow_id, "manage")
    scheduler = get_scheduler_service()
    return DeploymentService.delete_deployment(db, deployment_id, scheduler)
