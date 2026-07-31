import json
import logging
import re
from typing import List
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from pydantic import ValidationError
from sqlalchemy.orm import Session

from apps.gateway.api.deps import get_db
from apps.gateway.application.knowledge_administration.domain_permissions import (
    DomainPermissionCommand,
    DomainPermissionInputInvalid,
    DomainPermissionPersistenceFailed,
    DomainPermissionSubjectHidden,
    OrganizationManagerRequired,
)
from apps.gateway.application.knowledge_administration.collection_operations import (
    CollectionHidden,
    CollectionInputInvalid,
    CollectionItemRank,
    CollectionOperationCommand,
    CollectionPermissionDenied,
    CollectionPersistenceFailed,
    CollectionPolicyBlocked,
    CollectionPolicyDenied,
    CollectionStateConflict,
    ReorderCollectionItemsCommand,
)
from apps.gateway.auth.dependencies import get_current_user
from apps.gateway.composition.knowledge_administration import (
    build_knowledge_collection_lifecycle_and_order_use_case,
    build_knowledge_domain_permission_use_case,
)
from apps.gateway.composition.knowledge_collection_sync import (
    build_knowledge_collection_sync_use_cases,
)
from apps.gateway.composition.knowledge_document_ingestion import (
    build_read_document_ingestion_status,
    build_redrive_document_ingestion,
    build_request_document_ingestion,
    build_request_knowledge_base_reindex,
)
from apps.gateway.application.knowledge_document_ingestion.use_cases import (
    DocumentIngestionConflict,
    DocumentIngestionHidden,
    DocumentIngestionPersistenceFailed,
    DocumentIngestionPolicyBlocked,
    DocumentIngestionSettings,
    RedriveDocumentIngestionCommand,
    RequestDocumentIngestionCommand,
    RequestKnowledgeBaseReindexCommand,
)
from apps.gateway.application.knowledge_collection_sync.use_cases import (
    CollectionSyncCommand,
    CollectionSyncHidden,
    CollectionSyncJobSnapshot,
    CollectionSyncPermissionDenied,
    CollectionSyncPersistenceFailed,
    CollectionSyncPolicyBlocked,
    CollectionSyncStatusQuery,
)
from apps.gateway.utils.api_errors import raise_api_error
from apps.gateway.utils.audit import audit
from apps.gateway.services.ingestion.service import (
    IngestionPreviewSourceError,
    IngestionOrchestrator as IngestionService,
    finalize_stale_processing_start,
    recover_timed_out_document_with_artifacts,
)
from apps.gateway.services.knowledge_candidate_resolver import KnowledgeCandidateResolver
from apps.gateway.services.connection_lifecycle_service import (
    ConnectionLifecycleBusy,
    ConnectionLifecycleHidden,
    ConnectionLifecycleService,
    ConnectionLifecycleUnavailable,
)
from apps.gateway.services.knowledge_collection_service import (
    KnowledgeCollectionService,
    KnowledgeCollectionServiceError,
)
from apps.gateway.services.knowledge_collection_picker_query_service import (
    KnowledgeCollectionPickerQueryService,
    KnowledgeCollectionPickerUnavailable,
)
from apps.gateway.services.knowledge_document_content_service import (
    KnowledgeDocumentContentService,
)
from apps.gateway.services.knowledge_document_edit_projection import (
    project_document_edit_config,
)
from apps.gateway.services.knowledge_db_source_config import (
    KnowledgeDbSourceConfigInvalid,
    ValidatedKnowledgeDbSourceConfig,
    remove_legacy_connection_details,
    validate_knowledge_db_source_config,
)
from apps.gateway.services.knowledge_document_ingestion_readiness import (
    KnowledgeDocumentIngestionUnavailable,
    require_knowledge_document_ingestion_schema,
)
from apps.gateway.services.knowledge_document_projection import (
    project_safe_document_error,
    project_safe_document_metadata,
    project_safe_document_status,
)
from apps.gateway.services.knowledge_document_registration_service import (
    is_initial_document_registration_eligible,
)
from apps.gateway.services.knowledge_base_query_service import (
    KNOWLEDGE_BASE_MUTATION_COLUMNS,
    KnowledgeBaseCreateFailed,
    KnowledgeBaseNotFound,
    KnowledgeBaseQueryService,
    KnowledgeSchemaNotReady,
    KnowledgeValidationError,
)
from apps.gateway.services.knowledge_authorization_service import (
    KnowledgeAuthorizationService,
    KnowledgePermissionDenied,
    KnowledgeResourceHidden,
)
from apps.gateway.services.knowledge_lifecycle_service import (
    KnowledgeLifecycleNotFound,
    KnowledgeLifecyclePolicyDenied,
    KnowledgeLifecycleService,
)
from apps.gateway.services.knowledge_rag_recommendation_service import (
    KnowledgeRAGRecommendationService,
)
from apps.gateway.services.organization_context import (
    get_user_primary_organization_id,
    resolve_active_organization_id,
)
from apps.shared.audit.actions import AuditAction
from apps.shared.db.models.knowledge import Document, KnowledgeBase
from apps.shared.db.models.user import User
from apps.shared.services.connection_use_resolver import (
    ConnectionUseDenied,
    ConnectionUseUnavailable,
)
from apps.shared.schemas.knowledge import (
    KnowledgeCandidateResolution,
    KnowledgeCandidateResolveRequest,
    KnowledgeCollectionCreateRequest,
    KnowledgeCollectionItemLinkRequest,
    KnowledgeCollectionItemReorderRequest,
    KnowledgeCollectionItemsResponse,
    KnowledgeCollectionLinkCandidatesResponse,
    KnowledgeCollectionLLMSelectableResponse,
    KnowledgeCollectionListResponse,
    KnowledgeCollectionPermissionGrantRequest,
    KnowledgeCollectionPermissionBundleGrantRequest,
    KnowledgeCollectionPermissionBulkBundleRequest,
    KnowledgeCollectionPermissionBulkBundleResponse,
    KnowledgeCollectionPermissionsResponse,
    KnowledgeCollectionResponse,
    KnowledgeCollectionLatestSyncJobResponse,
    KnowledgeCollectionSyncJobResponse,
    KnowledgeCollectionSyncRequestResponse,
    KnowledgeCollectionUpdateRequest,
    KnowledgeCollectionVisibilityRequest,
    KnowledgeCollectionVisibilityResponse,
    KnowledgeDomainCapabilitiesResponse,
    KnowledgeDomainPermissionAction,
    KnowledgeDomainPermissionListResponse,
    KnowledgeDomainPermissionResponse,
    KnowledgeDomainPermissionUpsertRequest,
    KnowledgeDelegationSubjectsResponse,
    KnowledgeRAGRecommendationRequest,
    KnowledgeRAGRecommendationResponse,
)
from apps.shared.schemas.rag import (
    DocumentEditConfigResponse,
    DocumentPreviewRequest,
    DocumentPreviewResponse,
    DocumentResponse,
    KnowledgeBaseCreate,
    KnowledgeBaseDetailResponse,
    KnowledgeBaseResponse,
    KnowledgeSafeMetadataResponse,
    KnowledgeSafeMetadataUpdate,
    KnowledgeUpdate,
)
from apps.shared.services.rag_hierarchy import (
    RAGHierarchyError,
    validate_chunking_request,
)
from apps.shared.services.knowledge_permission_service import KnowledgePermissionHelper
from apps.shared.services.permissions import has_organization_manager_permission
from apps.shared.services.knowledge_schema_readiness import (
    check_knowledge_schema_readiness,
    table_has_column,
)
from apps.shared.services.knowledge_safe_text import sanitize_kb_safe_metadata
from apps.shared.services.knowledge_document_ingestion_projection import (
    project_safe_ingestion_job,
)
from apps.shared.domain.knowledge_collection_sync import (
    progress_category,
    safe_reason_code,
)
from apps.shared.domain.knowledge_document_ingestion import (
    RAW_PARSER_EGRESS_UNAVAILABLE_REASON,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _validated_db_source_config_or_error(
    request: Request,
    db: Session,
    *,
    current_user_id: UUID,
    stored_meta_info: object,
    submitted_db_config: object,
) -> ValidatedKnowledgeDbSourceConfig:
    try:
        return validate_knowledge_db_source_config(
            db,
            execution_subject_user_id=current_user_id,
            stored_meta_info=stored_meta_info,
            submitted_db_config=submitted_db_config,
        )
    except ConnectionUseDenied:
        raise_api_error(
            request,
            404,
            "resource.hidden",
            "Resource not found.",
        )
    except ConnectionUseUnavailable:
        raise_api_error(
            request,
            503,
            "connection.reference_unavailable",
            "The DB connection reference is temporarily unavailable.",
        )
    except KnowledgeDbSourceConfigInvalid:
        raise_api_error(
            request,
            400,
            "validation.failed",
            "Invalid DB source configuration.",
        )


class KnowledgeSchemaIntrospectionError(Exception):
    """Raised when schema readiness cannot be verified safely."""


SAFE_PUBLIC_RECOMMENDATION_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,80}$")
UNSAFE_PUBLIC_RECOMMENDATION_REF_RE = re.compile(
    r"(api[_-]?key|token|secret|password|credential|authorization)",
    re.IGNORECASE,
)


def _safe_public_recommendation_ref(value) -> str | None:
    if value is None:
        return None
    ref = str(value).strip()
    if UNSAFE_PUBLIC_RECOMMENDATION_REF_RE.search(ref):
        return None
    if SAFE_PUBLIC_RECOMMENDATION_REF_RE.fullmatch(ref):
        return ref
    return None


def _chunking_http_exception(exc: RAGHierarchyError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"reason": exc.reason, "message": exc.message},
    )


def _raise_document_ingestion_error(request: Request, exc: Exception) -> None:
    if isinstance(exc, DocumentIngestionHidden):
        raise_api_error(
            request,
            404,
            "resource.hidden",
            "Knowledge document not found.",
        )
    if isinstance(exc, DocumentIngestionConflict):
        raise_api_error(
            request,
            409,
            "ingestion.already_in_progress",
            "Another document ingestion intent is already in progress.",
        )
    if isinstance(exc, DocumentIngestionPolicyBlocked):
        raise_api_error(
            request,
            409,
            exc.reason_code,
            "Document ingestion is not available for the current resource state.",
        )
    if isinstance(exc, DocumentIngestionPersistenceFailed):
        if exc.reason_code in {
            "connection.reference_busy",
            "connection.reference_unavailable",
        }:
            raise_api_error(
                request,
                503,
                exc.reason_code,
                "The DB connection reference is temporarily unavailable.",
            )
        raise_api_error(
            request,
            503,
            "ingestion.admission_unavailable",
            "Document ingestion is temporarily unavailable.",
        )
    raise exc


def _ensure_document_ingestion_schema_ready(db: Session, request: Request) -> None:
    try:
        require_knowledge_document_ingestion_schema(db)
    except KnowledgeDocumentIngestionUnavailable as exc:
        raise_api_error(
            request,
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "knowledge.ingestion_schema_not_ready",
            "Knowledge document ingestion is temporarily unavailable.",
            {"reason": exc.reason_code},
        )


def _knowledge_collection_service(
    db: Session,
    request: Request,
    raw_organization_id: str | None,
    current_user: User,
) -> KnowledgeCollectionService:
    organization_id = resolve_active_organization_id(
        db,
        request,
        raw_organization_id,
        current_user.id,
    )
    return KnowledgeCollectionService(
        db,
        user_id=current_user.id,
        organization_id=organization_id,
    )


def _table_has_column(db: Session, table_name: str, column_name: str) -> bool:
    return table_has_column(db, table_name, column_name)


def _knowledge_schema_missing_columns(
    db: Session, required_columns: dict[str, set[str]]
) -> dict[str, list[str]]:
    result = check_knowledge_schema_readiness(db, required_columns)
    if result.reason == "schema_introspection_failed":
        raise KnowledgeSchemaIntrospectionError
    return result.missing_columns


def _raise_knowledge_schema_not_ready(
    request: Request,
    missing_columns: dict[str, list[str]],
    *,
    reason: str | None = None,
) -> None:
    details: dict[str, object] = {"missing_columns": missing_columns}
    if reason is not None:
        details["reason"] = reason
    raise_api_error(
        request,
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "knowledge.schema_not_ready",
        "Knowledge database schema is not ready for this operation.",
        details,
    )


def _ensure_knowledge_schema_columns(
    db: Session,
    request: Request,
    required_columns: dict[str, set[str]],
) -> None:
    try:
        missing = _knowledge_schema_missing_columns(db, required_columns)
    except KnowledgeSchemaIntrospectionError:
        _raise_knowledge_schema_not_ready(
            request,
            {},
            reason="schema_introspection_failed",
        )
    if missing:
        _raise_knowledge_schema_not_ready(request, missing)


def _resolve_create_organization_id(
    db: Session,
    request: Request,
    raw_organization_id: str | None,
    user_id: UUID,
    *,
    organization_column_ready: bool = False,
) -> UUID | None:
    if raw_organization_id is not None:
        if not organization_column_ready:
            _ensure_knowledge_schema_columns(
                db,
                request,
                {"knowledge_bases": {"organization_id"}},
            )
        return resolve_active_organization_id(
            db,
            request,
            raw_organization_id,
            user_id,
        )
    return get_user_primary_organization_id(db, user_id)


def _resolve_read_organization_scope(
    db: Session,
    request: Request,
    raw_organization_id: str | None,
    user_id: UUID,
    *,
    has_organization_id: bool | None = None,
) -> UUID | None:
    if raw_organization_id is None:
        return None
    if has_organization_id is False:
        _raise_knowledge_schema_not_ready(
            request,
            {"knowledge_bases": ["organization_id"]},
        )
    if has_organization_id is None:
        _ensure_knowledge_schema_columns(
            db,
            request,
            {"knowledge_bases": {"organization_id"}},
        )
    return resolve_active_organization_id(
        db,
        request,
        raw_organization_id,
        user_id,
    )


def _raise_collection_service_error(
    request: Request,
    exc: KnowledgeCollectionServiceError,
) -> None:
    raise_api_error(
        request,
        exc.status_code,
        exc.code,
        exc.message,
        exc.details,
    )


def _raise_collection_operation_error(request: Request, exc: Exception) -> None:
    if isinstance(exc, CollectionHidden):
        raise_api_error(
            request,
            status.HTTP_404_NOT_FOUND,
            "resource.hidden",
            "Resource not found.",
        )
    if isinstance(exc, CollectionPermissionDenied):
        raise_api_error(
            request,
            status.HTTP_403_FORBIDDEN,
            "permission.denied",
            "Knowledge Collection permission is required.",
        )
    if isinstance(exc, CollectionPolicyDenied):
        raise_api_error(
            request,
            status.HTTP_403_FORBIDDEN,
            "policy.denied",
            "System-managed collections cannot be manually changed.",
        )
    if isinstance(exc, CollectionPolicyBlocked):
        raise_api_error(
            request,
            status.HTTP_409_CONFLICT,
            "policy.blocked",
            "Knowledge Collection change is blocked by policy.",
            {"policy_reason": exc.reason_code},
        )
    if isinstance(exc, CollectionInputInvalid):
        raise_api_error(
            request,
            status.HTTP_400_BAD_REQUEST,
            "validation.failed",
            "Knowledge Collection request is invalid.",
        )
    if isinstance(exc, CollectionStateConflict):
        raise_api_error(
            request,
            status.HTTP_409_CONFLICT,
            "conflict",
            "Knowledge Collection state changed. Reload and try again.",
            {"reason": exc.reason_code},
        )
    if isinstance(exc, CollectionPersistenceFailed):
        raise_api_error(
            request,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "knowledge.collection_write_failed",
            "Knowledge Collection change could not be saved.",
        )
    raise exc


def _raise_collection_sync_error(request: Request, exc: Exception) -> None:
    if isinstance(exc, CollectionSyncHidden):
        raise_api_error(
            request,
            status.HTTP_404_NOT_FOUND,
            "resource.hidden",
            "Resource not found.",
        )
    if isinstance(exc, CollectionSyncPermissionDenied):
        raise_api_error(
            request,
            status.HTTP_403_FORBIDDEN,
            "permission.denied",
            "Permission denied.",
        )
    if isinstance(exc, CollectionSyncPolicyBlocked):
        safe_code = safe_reason_code(exc.reason_code)
        response_code = (
            safe_code
            if safe_code
            in {
                "sync.no_eligible_targets",
                "sync.not_supported",
                "sync.target_limit_exceeded",
            }
            else "policy.blocked"
        )
        raise_api_error(
            request,
            status.HTTP_409_CONFLICT,
            response_code,
            "Knowledge Collection sync is not available.",
            {"policy_reason": safe_code},
        )
    if isinstance(exc, CollectionSyncPersistenceFailed):
        raise_api_error(
            request,
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "sync.unavailable",
            "Knowledge Collection sync is temporarily unavailable.",
        )
    raise exc


def _collection_sync_job_response(
    job: CollectionSyncJobSnapshot,
) -> KnowledgeCollectionSyncJobResponse:
    return KnowledgeCollectionSyncJobResponse(
        job_id=job.job_id,
        collection_id=job.collection_id,
        status=job.status,
        progress=progress_category(
            status=job.status,
            total_count=job.total_count,
            completed_count=job.completed_count,
            failed_count=job.failed_count,
            skipped_count=job.skipped_count,
        ),
        safe_reason_code=safe_reason_code(job.safe_reason_code),
        retryable=job.retryable,
        requested_at=job.requested_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )


def _raise_domain_permission_error(request: Request, exc: Exception) -> None:
    if isinstance(exc, OrganizationManagerRequired):
        raise_api_error(
            request,
            status.HTTP_403_FORBIDDEN,
            "permission.denied",
            "Organization manager permission is required.",
        )
    if isinstance(exc, DomainPermissionSubjectHidden):
        raise_api_error(
            request,
            status.HTTP_404_NOT_FOUND,
            "resource.hidden",
            "Resource not found.",
        )
    if isinstance(exc, DomainPermissionInputInvalid):
        raise_api_error(
            request,
            status.HTTP_400_BAD_REQUEST,
            "validation.failed",
            "Knowledge domain permission request is invalid.",
        )
    if isinstance(exc, DomainPermissionPersistenceFailed):
        raise_api_error(
            request,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "knowledge.permission_write_failed",
            "Knowledge permission change could not be saved.",
        )
    raise exc


def _domain_permission_response(row) -> KnowledgeDomainPermissionResponse:
    return KnowledgeDomainPermissionResponse(
        permission_id=row.permission_id,
        subject_type=row.subject_type,
        subject_id=row.subject_id,
        subject_safe_label=row.subject_safe_label,
        permission_action=row.permission_action,
        assigned_at=row.assigned_at,
        expires_at=row.expires_at,
        is_expired=row.is_expired,
    )


def _knowledge_base_query_service(db: Session) -> KnowledgeBaseQueryService:
    return KnowledgeBaseQueryService(db)


def _knowledge_authorization_service(
    db: Session,
    *,
    user_id: UUID,
    organization_id: UUID,
) -> KnowledgeAuthorizationService:
    return KnowledgeAuthorizationService(
        db,
        user_id=user_id,
        organization_id=organization_id,
    )


def _raise_knowledge_authorization_error(
    request: Request,
    exc: Exception,
) -> None:
    if isinstance(exc, KnowledgeResourceHidden):
        raise_api_error(
            request,
            status.HTTP_404_NOT_FOUND,
            "resource.hidden",
            "Knowledge resource not found.",
        )
    if isinstance(exc, KnowledgePermissionDenied):
        raise_api_error(
            request,
            status.HTTP_403_FORBIDDEN,
            "permission.denied",
            "Knowledge permission is required.",
        )
    raise exc


def _raise_knowledge_query_service_error(
    request: Request,
    exc: Exception,
) -> None:
    if isinstance(exc, KnowledgeSchemaNotReady):
        _raise_knowledge_schema_not_ready(
            request,
            exc.missing_columns,
            reason=exc.reason,
        )
    if isinstance(exc, KnowledgeBaseCreateFailed):
        raise_api_error(
            request,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "knowledge.create_failed",
            "Knowledge base creation failed.",
        )
    if isinstance(exc, KnowledgeValidationError):
        raise_api_error(
            request,
            status.HTTP_400_BAD_REQUEST,
            "knowledge.validation_failed",
            "Knowledge base request validation failed.",
            {"reason": exc.reason},
        )
    if isinstance(exc, KnowledgeBaseNotFound):
        raise HTTPException(status_code=404, detail="Knowledge Base not found")
    raise exc


@router.post(
    "", response_model=KnowledgeBaseResponse, status_code=status.HTTP_201_CREATED
)
@audit(AuditAction.KNOWLEDGE_CREATE)
def create_knowledge_base(
    kb_in: KnowledgeBaseCreate,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    빈 지식 베이스를 생성합니다. (소스 없음)
    """
    _ensure_knowledge_schema_columns(
        db,
        request,
        KNOWLEDGE_BASE_MUTATION_COLUMNS,
    )
    service = _knowledge_base_query_service(db)
    organization_id = _resolve_create_organization_id(
        db,
        request,
        x_organization_id,
        current_user.id,
        organization_column_ready=True,
    )
    try:
        return service.create(
            kb_in,
            user_id=current_user.id,
            organization_id=organization_id,
            schema_ready=True,
        )
    except (
        KnowledgeSchemaNotReady,
        KnowledgeBaseCreateFailed,
        KnowledgeValidationError,
    ) as exc:
        _raise_knowledge_query_service_error(request, exc)


@router.get("", response_model=List[KnowledgeBaseResponse])
def list_knowledge_bases(
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    사용자의 자료 목록을 조회합니다.
    각 지식 베이스 그룹에 포함된 문서 개수도 함께 반환합니다.
    """
    _ensure_knowledge_schema_columns(
        db,
        request,
        KNOWLEDGE_BASE_MUTATION_COLUMNS,
    )
    organization_id = resolve_active_organization_id(
        db,
        request,
        x_organization_id,
        current_user.id,
    )
    return _knowledge_base_query_service(db).list_authorized(
        user_id=current_user.id,
        organization_id=organization_id,
        schema_ready=True,
    )


@router.get("/llm-selectable", response_model=List[KnowledgeBaseDetailResponse])
def list_llm_selectable_knowledge_bases(
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    LLM 노드 RAG picker에서 선택 가능한 Knowledge Base 후보를 조회합니다.
    일반 관리 목록과 달리 owner filter가 아니라 active organization, KB use 권한,
    retrieval-visible completed chunk 기준으로 후보를 제한합니다.
    """
    _ensure_knowledge_schema_columns(
        db,
        request,
        KNOWLEDGE_BASE_MUTATION_COLUMNS,
    )
    organization_id = resolve_active_organization_id(
        db,
        request,
        x_organization_id,
        current_user.id,
    )
    service = _knowledge_base_query_service(db)
    return service.list_llm_selectable(
        user_id=current_user.id,
        organization_id=organization_id,
        schema_ready=True,
    )


@router.get(
    "/llm-selectable-collections",
    response_model=KnowledgeCollectionLLMSelectableResponse,
)
def list_llm_selectable_knowledge_collections(
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return only active Collections the current editor may route through."""

    organization_id = resolve_active_organization_id(
        db,
        request,
        x_organization_id,
        current_user.id,
    )
    service = KnowledgeCollectionPickerQueryService(
        db,
        user_id=current_user.id,
        organization_id=organization_id,
    )
    try:
        return service.list_llm_selectable()
    except KnowledgeCollectionPickerUnavailable:
        raise_api_error(
            request,
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "knowledge.collection_picker_unavailable",
            "Knowledge Collection candidates are temporarily unavailable.",
        )


@router.post("/candidates/resolve", response_model=KnowledgeCandidateResolution)
def resolve_knowledge_candidates(
    candidate_request: KnowledgeCandidateResolveRequest,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Workflow Builder와 deployment preflight가 사용할 안전한 Knowledge 후보를 조회합니다.
    """
    organization_id = resolve_active_organization_id(
        db,
        request,
        x_organization_id,
        current_user.id,
    )
    runtime_permission_helper = None
    if candidate_request.intended_execution_subject_id:
        runtime_permission_helper = KnowledgePermissionHelper(
            db,
            user_id=candidate_request.intended_execution_subject_id,
            organization_id=organization_id,
        )

    resolver = KnowledgeCandidateResolver(
        db,
        user_id=current_user.id,
        organization_id=organization_id,
        runtime_permission_helper=runtime_permission_helper,
    )

    # 예상 실행 대상이 명시되어도 Phase 7에서는 후보 노출 scope만 좁힌다.
    # 실제 runtime 권한 판정은 Workflow execution_subject 기준으로 다시 수행한다.
    if candidate_request.mode == "explicit_kb":
        return resolver.resolve_explicit_kbs(candidate_request.knowledge_base_ids)

    return resolver.resolve_auto_collection_candidates(
        collection_ids=candidate_request.collection_ids,
        max_collections=candidate_request.max_collections,
        max_candidate_kbs=candidate_request.max_candidate_kbs,
    )


def _safe_validation_errors(exc: ValidationError) -> list[dict]:
    # workflow_intent/node_purpose는 prompt-like 입력이므로 validation 응답에서도 raw input을 제거한다.
    # Pydantic errors()의 input 필드는 의도치 않게 사용자 원문을 echo할 수 있다.
    errors = []
    for error in exc.errors():
        errors.append(
            {
                "loc": list(error.get("loc", ())),
                "msg": error.get("msg", "Invalid input."),
                "type": error.get("type", "value_error"),
            }
        )
    return errors


async def _parse_rag_recommendation_request(
    request: Request,
) -> KnowledgeRAGRecommendationRequest:
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        raise_api_error(
            request,
            422,
            "validation.failed",
            "Request validation failed.",
            {
                "errors": [
                    {
                        "loc": ["body"],
                        "msg": "Invalid JSON body.",
                        "type": "json_invalid",
                    }
                ]
            },
        )

    try:
        return KnowledgeRAGRecommendationRequest.model_validate(payload)
    except ValidationError as exc:
        raise_api_error(
            request,
            422,
            "validation.failed",
            "Request validation failed.",
            {"errors": _safe_validation_errors(exc)},
        )


@router.post("/rag-recommendations", response_model=KnowledgeRAGRecommendationResponse)
async def recommend_rag_options(
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Workflow Builder가 LLM node RAG 옵션을 구성할 때 사용할 안전한 KB 추천을 반환합니다.
    """
    recommendation_request = await _parse_rag_recommendation_request(request)
    if recommendation_request.mode == "explicit_kb":
        raise_api_error(
            request,
            422,
            "knowledge.rag_recommendations.explicit_ids_not_allowed",
            "Explicit Knowledge Base identifiers are not accepted at this public boundary.",
        )
    organization_id = resolve_active_organization_id(
        db,
        request,
        x_organization_id,
        current_user.id,
    )
    safe_request_payload = recommendation_request.model_dump(
        exclude={
            "intended_execution_subject_id",
            "knowledge_base_ids",
            "collection_ids",
        }
    )
    safe_request_payload["pending_resolution_ref"] = _safe_public_recommendation_ref(
        recommendation_request.pending_resolution_ref
    )
    if isinstance(safe_request_payload.get("knowledge_requirement"), dict):
        knowledge_requirement = dict(safe_request_payload["knowledge_requirement"])
        knowledge_requirement["requirement_id"] = _safe_public_recommendation_ref(
            knowledge_requirement.get("requirement_id")
        )
        safe_request_payload["knowledge_requirement"] = knowledge_requirement
    recommendation_request = KnowledgeRAGRecommendationRequest.model_validate(
        {
            **safe_request_payload,
            "intended_execution_subject_id": current_user.id,
        }
    )
    service = KnowledgeRAGRecommendationService(
        db,
        user_id=current_user.id,
        organization_id=organization_id,
    )
    result = service.recommend_for_builder(recommendation_request)
    for recommendation in result.recommendations:
        recommendation.materialized_knowledge_bases = []
    return result


@router.get(
    "/domain-capabilities",
    response_model=KnowledgeDomainCapabilitiesResponse,
)
def get_knowledge_domain_capabilities(
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    organization_id = resolve_active_organization_id(
        db, request, x_organization_id, current_user.id
    )
    actions, is_manager = build_knowledge_domain_permission_use_case(db).capabilities(
        current_user.id, organization_id
    )
    return KnowledgeDomainCapabilitiesResponse(
        actions=sorted(actions),
        can_manage_domain_permissions=is_manager,
        can_create_collection="catalog_manage" in actions,
        can_delegate_permissions="permission_delegate" in actions,
        can_manage_lifecycle="lifecycle_manage" in actions,
        can_manage_sync="sync_manage" in actions,
        can_change_public_visibility=is_manager,
    )


@router.get(
    "/domain-permissions",
    response_model=KnowledgeDomainPermissionListResponse,
)
def list_knowledge_domain_permissions(
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    organization_id = resolve_active_organization_id(
        db, request, x_organization_id, current_user.id
    )
    try:
        rows = build_knowledge_domain_permission_use_case(db).list_permissions(
            current_user.id, organization_id
        )
    except (OrganizationManagerRequired, DomainPermissionPersistenceFailed) as exc:
        _raise_domain_permission_error(request, exc)
    return KnowledgeDomainPermissionListResponse(
        permissions=[_domain_permission_response(row) for row in rows]
    )


@router.get(
    "/domain-delegation-subjects",
    response_model=KnowledgeDelegationSubjectsResponse,
)
def list_knowledge_domain_delegation_subjects(
    request: Request,
    subject_type: str | None = Query(default=None),
    query: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: str = Query(default="25"),
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = _knowledge_collection_service(db, request, x_organization_id, current_user)
    try:
        return service.list_domain_delegation_subjects(
            subject_type=subject_type,
            query=query,
            cursor=cursor,
            limit=limit,
        )
    except KnowledgeCollectionServiceError as exc:
        _raise_collection_service_error(request, exc)


def _change_knowledge_domain_permission(
    *,
    request: Request,
    db: Session,
    current_user: User,
    organization_id: UUID,
    subject_type: str,
    subject_id: UUID,
    permission_action: KnowledgeDomainPermissionAction,
    expires_at,
    revoke: bool,
):
    command = DomainPermissionCommand(
        actor_id=current_user.id,
        organization_id=organization_id,
        subject_type=subject_type,
        subject_id=subject_id,
        permission_action=permission_action,
        expires_at=expires_at,
    )
    use_case = build_knowledge_domain_permission_use_case(db)
    try:
        return use_case.revoke(command) if revoke else use_case.grant(command)
    except (
        OrganizationManagerRequired,
        DomainPermissionSubjectHidden,
        DomainPermissionInputInvalid,
        DomainPermissionPersistenceFailed,
    ) as exc:
        _raise_domain_permission_error(request, exc)


@router.put(
    "/domain-permissions/teams/{team_id}/{permission_action}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def put_team_knowledge_domain_permission(
    team_id: UUID,
    permission_action: KnowledgeDomainPermissionAction,
    body: KnowledgeDomainPermissionUpsertRequest,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    organization_id = resolve_active_organization_id(
        db, request, x_organization_id, current_user.id
    )
    _change_knowledge_domain_permission(
        request=request,
        db=db,
        current_user=current_user,
        organization_id=organization_id,
        subject_type="team",
        subject_id=team_id,
        permission_action=permission_action,
        expires_at=body.expires_at,
        revoke=False,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/domain-permissions/teams/{team_id}/{permission_action}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_team_knowledge_domain_permission(
    team_id: UUID,
    permission_action: KnowledgeDomainPermissionAction,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    organization_id = resolve_active_organization_id(
        db, request, x_organization_id, current_user.id
    )
    _change_knowledge_domain_permission(
        request=request,
        db=db,
        current_user=current_user,
        organization_id=organization_id,
        subject_type="team",
        subject_id=team_id,
        permission_action=permission_action,
        expires_at=None,
        revoke=True,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put(
    "/domain-permissions/users/{user_id}/{permission_action}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def put_user_knowledge_domain_permission(
    user_id: UUID,
    permission_action: KnowledgeDomainPermissionAction,
    body: KnowledgeDomainPermissionUpsertRequest,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    organization_id = resolve_active_organization_id(
        db, request, x_organization_id, current_user.id
    )
    _change_knowledge_domain_permission(
        request=request,
        db=db,
        current_user=current_user,
        organization_id=organization_id,
        subject_type="user",
        subject_id=user_id,
        permission_action=permission_action,
        expires_at=body.expires_at,
        revoke=False,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/domain-permissions/users/{user_id}/{permission_action}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_user_knowledge_domain_permission(
    user_id: UUID,
    permission_action: KnowledgeDomainPermissionAction,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    organization_id = resolve_active_organization_id(
        db, request, x_organization_id, current_user.id
    )
    _change_knowledge_domain_permission(
        request=request,
        db=db,
        current_user=current_user,
        organization_id=organization_id,
        subject_type="user",
        subject_id=user_id,
        permission_action=permission_action,
        expires_at=None,
        revoke=True,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/collections", response_model=KnowledgeCollectionListResponse)
def list_knowledge_collections(
    request: Request,
    lifecycle_state: str = Query(default="active"),
    visibility: str | None = Query(default=None),
    system_managed: bool | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = _knowledge_collection_service(db, request, x_organization_id, current_user)
    try:
        collections = service.list_collections(
            lifecycle_state=lifecycle_state,
            visibility=visibility,
            system_managed=system_managed,
            limit=limit,
        )
        capabilities = service.management_capabilities()
    except KnowledgeCollectionServiceError as exc:
        _raise_collection_service_error(request, exc)
    return KnowledgeCollectionListResponse(collections=collections, **capabilities)


@router.post(
    "/collection-permissions/bulk-bundles",
    response_model=KnowledgeCollectionPermissionBulkBundleResponse,
)
def mutate_knowledge_collection_permission_bundles(
    permission_request: KnowledgeCollectionPermissionBulkBundleRequest,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = _knowledge_collection_service(db, request, x_organization_id, current_user)
    try:
        return service.mutate_permission_bundle_bulk(permission_request)
    except KnowledgeCollectionServiceError as exc:
        _raise_collection_service_error(request, exc)


@router.post(
    "/collections",
    response_model=KnowledgeCollectionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_knowledge_collection(
    collection_request: KnowledgeCollectionCreateRequest,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = _knowledge_collection_service(db, request, x_organization_id, current_user)
    try:
        return service.create_collection(collection_request)
    except KnowledgeCollectionServiceError as exc:
        _raise_collection_service_error(request, exc)


@router.post(
    "/collections/{collection_id}/sync-jobs",
    response_model=KnowledgeCollectionSyncRequestResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def request_knowledge_collection_sync(
    collection_id: UUID,
    request: Request,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=36,
        max_length=36,
    ),
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        parsed_key = UUID(idempotency_key)
        if str(parsed_key) != idempotency_key:
            raise ValueError
    except (TypeError, ValueError, AttributeError):
        raise_api_error(
            request,
            status.HTTP_400_BAD_REQUEST,
            "validation.failed",
            "Idempotency-Key must be a canonical UUID.",
            {"field": "Idempotency-Key"},
        )
    organization_id = resolve_active_organization_id(
        db, request, x_organization_id, current_user.id
    )
    use_cases = build_knowledge_collection_sync_use_cases(db)
    try:
        result = use_cases.request.execute(
            CollectionSyncCommand(
                actor_id=current_user.id,
                organization_id=organization_id,
                collection_id=collection_id,
                idempotency_key=parsed_key,
            )
        )
    except (
        CollectionSyncHidden,
        CollectionSyncPermissionDenied,
        CollectionSyncPolicyBlocked,
        CollectionSyncPersistenceFailed,
    ) as exc:
        _raise_collection_sync_error(request, exc)
    return KnowledgeCollectionSyncRequestResponse(
        job=_collection_sync_job_response(result.job),
        reused=result.reused,
        dispatch_deferred=result.dispatch_deferred,
    )


@router.get(
    "/collections/{collection_id}/sync-jobs/latest",
    response_model=KnowledgeCollectionLatestSyncJobResponse,
)
def get_latest_knowledge_collection_sync_job(
    collection_id: UUID,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    organization_id = resolve_active_organization_id(
        db, request, x_organization_id, current_user.id
    )
    try:
        job = build_knowledge_collection_sync_use_cases(db).read.execute(
            CollectionSyncStatusQuery(
                actor_id=current_user.id,
                organization_id=organization_id,
                collection_id=collection_id,
            )
        )
    except (CollectionSyncHidden, CollectionSyncPermissionDenied) as exc:
        _raise_collection_sync_error(request, exc)
    return KnowledgeCollectionLatestSyncJobResponse(
        job=_collection_sync_job_response(job) if job is not None else None
    )


@router.get(
    "/collections/{collection_id}/sync-jobs/{job_id}",
    response_model=KnowledgeCollectionSyncJobResponse,
)
def get_knowledge_collection_sync_job(
    collection_id: UUID,
    job_id: UUID,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    organization_id = resolve_active_organization_id(
        db, request, x_organization_id, current_user.id
    )
    try:
        job = build_knowledge_collection_sync_use_cases(db).read.execute(
            CollectionSyncStatusQuery(
                actor_id=current_user.id,
                organization_id=organization_id,
                collection_id=collection_id,
                job_id=job_id,
            )
        )
    except (CollectionSyncHidden, CollectionSyncPermissionDenied) as exc:
        _raise_collection_sync_error(request, exc)
    if job is None:
        raise_api_error(
            request,
            status.HTTP_404_NOT_FOUND,
            "resource.hidden",
            "Resource not found.",
        )
    return _collection_sync_job_response(job)


@router.get("/collections/{collection_id}", response_model=KnowledgeCollectionResponse)
def get_knowledge_collection(
    collection_id: UUID,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = _knowledge_collection_service(db, request, x_organization_id, current_user)
    try:
        return service.get_collection(collection_id)
    except KnowledgeCollectionServiceError as exc:
        _raise_collection_service_error(request, exc)


@router.patch("/collections/{collection_id}", response_model=KnowledgeCollectionResponse)
def update_knowledge_collection(
    collection_id: UUID,
    collection_request: KnowledgeCollectionUpdateRequest,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = _knowledge_collection_service(db, request, x_organization_id, current_user)
    try:
        return service.update_collection(collection_id, collection_request)
    except KnowledgeCollectionServiceError as exc:
        _raise_collection_service_error(request, exc)


@router.delete("/collections/{collection_id}", status_code=status.HTTP_204_NO_CONTENT)
def archive_knowledge_collection(
    collection_id: UUID,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    organization_id = resolve_active_organization_id(
        db, request, x_organization_id, current_user.id
    )
    try:
        build_knowledge_collection_lifecycle_and_order_use_case(db).archive(
            CollectionOperationCommand(
                actor_id=current_user.id,
                organization_id=organization_id,
                collection_id=collection_id,
            )
        )
    except (
        CollectionHidden,
        CollectionPermissionDenied,
        CollectionPolicyBlocked,
        CollectionPolicyDenied,
        CollectionStateConflict,
        CollectionPersistenceFailed,
    ) as exc:
        _raise_collection_operation_error(request, exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/collections/{collection_id}/restore",
    status_code=status.HTTP_204_NO_CONTENT,
)
def restore_knowledge_collection(
    collection_id: UUID,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    organization_id = resolve_active_organization_id(
        db, request, x_organization_id, current_user.id
    )
    try:
        build_knowledge_collection_lifecycle_and_order_use_case(db).restore(
            CollectionOperationCommand(
                actor_id=current_user.id,
                organization_id=organization_id,
                collection_id=collection_id,
            )
        )
    except (
        CollectionHidden,
        CollectionPermissionDenied,
        CollectionPolicyBlocked,
        CollectionPolicyDenied,
        CollectionStateConflict,
        CollectionPersistenceFailed,
    ) as exc:
        _raise_collection_operation_error(request, exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/collections/{collection_id}/items",
    response_model=KnowledgeCollectionItemsResponse,
)
def list_knowledge_collection_items(
    collection_id: UUID,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = _knowledge_collection_service(db, request, x_organization_id, current_user)
    try:
        return service.list_items_response(collection_id)
    except KnowledgeCollectionServiceError as exc:
        _raise_collection_service_error(request, exc)


@router.post(
    "/collections/{collection_id}/items",
    response_model=KnowledgeCollectionItemsResponse,
)
def link_knowledge_collection_item(
    collection_id: UUID,
    item_request: KnowledgeCollectionItemLinkRequest,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = _knowledge_collection_service(db, request, x_organization_id, current_user)
    try:
        return service.link_item(collection_id, item_request)
    except KnowledgeCollectionServiceError as exc:
        _raise_collection_service_error(request, exc)


@router.patch(
    "/collections/{collection_id}/items/reorder",
    response_model=KnowledgeCollectionItemsResponse,
)
def reorder_knowledge_collection_items(
    collection_id: UUID,
    reorder_request: KnowledgeCollectionItemReorderRequest,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    organization_id = resolve_active_organization_id(
        db, request, x_organization_id, current_user.id
    )
    try:
        build_knowledge_collection_lifecycle_and_order_use_case(db).reorder(
            ReorderCollectionItemsCommand(
                actor_id=current_user.id,
                organization_id=organization_id,
                collection_id=collection_id,
                expected_order_revision=reorder_request.expected_order_revision,
                items=tuple(
                    CollectionItemRank(item_id=item.item_id, rank=item.rank)
                    for item in reorder_request.items
                ),
                acknowledged_public_runtime_exposure=(
                    reorder_request.acknowledged_public_runtime_exposure
                ),
            )
        )
        return KnowledgeCollectionService(
            db,
            user_id=current_user.id,
            organization_id=organization_id,
        ).list_items_management_response(collection_id)
    except (
        CollectionHidden,
        CollectionPermissionDenied,
        CollectionPolicyBlocked,
        CollectionPolicyDenied,
        CollectionStateConflict,
        CollectionInputInvalid,
        CollectionPersistenceFailed,
    ) as exc:
        _raise_collection_operation_error(request, exc)
    except KnowledgeCollectionServiceError as exc:
        _raise_collection_service_error(request, exc)


@router.delete(
    "/collections/{collection_id}/items/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def unlink_knowledge_collection_item(
    collection_id: UUID,
    item_id: UUID,
    request: Request,
    acknowledged_public_runtime_exposure: bool = Query(default=False),
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = _knowledge_collection_service(db, request, x_organization_id, current_user)
    try:
        service.unlink_item(
            collection_id,
            item_id,
            acknowledged_public_runtime_exposure=(
                acknowledged_public_runtime_exposure
            ),
        )
    except KnowledgeCollectionServiceError as exc:
        _raise_collection_service_error(request, exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/collections/{collection_id}/link-candidates",
    response_model=KnowledgeCollectionLinkCandidatesResponse,
)
def list_knowledge_collection_link_candidates(
    collection_id: UUID,
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = _knowledge_collection_service(db, request, x_organization_id, current_user)
    try:
        return KnowledgeCollectionLinkCandidatesResponse(
            candidates=service.list_link_candidates(collection_id, limit=limit)
        )
    except KnowledgeCollectionServiceError as exc:
        _raise_collection_service_error(request, exc)


@router.get(
    "/collections/{collection_id}/permissions",
    response_model=KnowledgeCollectionPermissionsResponse,
)
def list_knowledge_collection_permissions(
    collection_id: UUID,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = _knowledge_collection_service(db, request, x_organization_id, current_user)
    try:
        return KnowledgeCollectionPermissionsResponse(
            permissions=service.list_permissions(collection_id)
        )
    except KnowledgeCollectionServiceError as exc:
        _raise_collection_service_error(request, exc)


@router.get(
    "/collections/{collection_id}/delegation-subjects",
    response_model=KnowledgeDelegationSubjectsResponse,
)
def list_knowledge_collection_delegation_subjects(
    collection_id: UUID,
    request: Request,
    subject_type: str | None = Query(default=None),
    query: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: str = Query(default="25"),
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = _knowledge_collection_service(db, request, x_organization_id, current_user)
    try:
        return service.list_delegation_subjects(
            collection_id,
            subject_type=subject_type,
            query=query,
            cursor=cursor,
            limit=limit,
        )
    except KnowledgeCollectionServiceError as exc:
        _raise_collection_service_error(request, exc)


@router.post(
    "/collections/{collection_id}/permissions",
    response_model=KnowledgeCollectionPermissionsResponse,
)
def grant_knowledge_collection_permission(
    collection_id: UUID,
    permission_request: KnowledgeCollectionPermissionGrantRequest,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = _knowledge_collection_service(db, request, x_organization_id, current_user)
    try:
        permission = service.grant_permission(collection_id, permission_request)
        return KnowledgeCollectionPermissionsResponse(permissions=[permission])
    except KnowledgeCollectionServiceError as exc:
        _raise_collection_service_error(request, exc)


@router.post(
    "/collections/{collection_id}/permissions/bundles",
    response_model=KnowledgeCollectionPermissionsResponse,
)
def grant_knowledge_collection_permission_bundle(
    collection_id: UUID,
    permission_request: KnowledgeCollectionPermissionBundleGrantRequest,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = _knowledge_collection_service(db, request, x_organization_id, current_user)
    try:
        return KnowledgeCollectionPermissionsResponse(
            permissions=service.grant_permission_bundle(
                collection_id,
                permission_request,
            )
        )
    except KnowledgeCollectionServiceError as exc:
        _raise_collection_service_error(request, exc)


@router.post(
    "/collections/{collection_id}/permissions/bundles/revoke",
    status_code=status.HTTP_204_NO_CONTENT,
)
def revoke_knowledge_collection_permission_bundle(
    collection_id: UUID,
    permission_request: KnowledgeCollectionPermissionBundleGrantRequest,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = _knowledge_collection_service(db, request, x_organization_id, current_user)
    try:
        service.revoke_permission_bundle(collection_id, permission_request)
    except KnowledgeCollectionServiceError as exc:
        _raise_collection_service_error(request, exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/collections/{collection_id}/permissions/{permission_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def revoke_knowledge_collection_permission(
    collection_id: UUID,
    permission_id: UUID,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = _knowledge_collection_service(db, request, x_organization_id, current_user)
    try:
        service.revoke_permission(collection_id, permission_id)
    except KnowledgeCollectionServiceError as exc:
        _raise_collection_service_error(request, exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/collections/{collection_id}/visibility",
    response_model=KnowledgeCollectionVisibilityResponse,
)
def update_knowledge_collection_visibility(
    collection_id: UUID,
    visibility_request: KnowledgeCollectionVisibilityRequest,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = _knowledge_collection_service(db, request, x_organization_id, current_user)
    try:
        return service.update_visibility(collection_id, visibility_request)
    except KnowledgeCollectionServiceError as exc:
        _raise_collection_service_error(request, exc)


@router.get("/{kb_id}", response_model=KnowledgeBaseDetailResponse)
def get_knowledge_base(
    kb_id: UUID,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    지식 베이스의 상세 정보를 조회합니다.
    포함된 자료 목록과 각 자료의 상태를 함께 반환합니다.
    """
    _ensure_knowledge_schema_columns(
        db,
        request,
        KNOWLEDGE_BASE_MUTATION_COLUMNS,
    )
    organization_id = resolve_active_organization_id(
        db,
        request,
        x_organization_id,
        current_user.id,
    )
    authorization = _knowledge_authorization_service(
        db,
        user_id=current_user.id,
        organization_id=organization_id,
    )
    try:
        kb = authorization.load_kb(kb_id, "read")
    except (KnowledgeResourceHidden, KnowledgePermissionDenied) as exc:
        _raise_knowledge_authorization_error(request, exc)
    capabilities = authorization.capabilities(kb)

    try:
        return _knowledge_base_query_service(db).get_detail(
            kb_id,
            organization_scope=organization_id,
            has_organization_id=True,
            can_edit_settings=capabilities.can_write,
            can_manage_safe_metadata=capabilities.can_manage,
            can_register_initial_document=(
                capabilities.can_write
                and is_initial_document_registration_eligible(kb)
            ),
            can_read=capabilities.can_read,
            can_use=capabilities.can_use,
            can_write=capabilities.can_write,
            can_read_content=capabilities.can_read_content,
            can_manage=capabilities.can_manage,
        )
    except KnowledgeBaseNotFound as exc:
        _raise_knowledge_query_service_error(request, exc)


def _manageable_knowledge_base(
    kb_id: UUID,
    request: Request,
    raw_organization_id: str | None,
    db: Session,
    current_user: User,
) -> KnowledgeBase:
    organization_id = resolve_active_organization_id(
        db,
        request,
        raw_organization_id,
        current_user.id,
    )
    try:
        return _knowledge_authorization_service(
            db,
            user_id=current_user.id,
            organization_id=organization_id,
        ).load_kb(kb_id, "manage")
    except (KnowledgeResourceHidden, KnowledgePermissionDenied) as exc:
        _raise_knowledge_authorization_error(request, exc)


def _authorized_knowledge_document(
    kb_id: UUID,
    document_id: UUID,
    action: str,
    request: Request,
    raw_organization_id: str | None,
    db: Session,
    current_user: User,
    *,
    domain_action: str | None = None,
) -> tuple[KnowledgeBase, Document]:
    organization_id = resolve_active_organization_id(
        db,
        request,
        raw_organization_id,
        current_user.id,
    )
    try:
        return _knowledge_authorization_service(
            db,
            user_id=current_user.id,
            organization_id=organization_id,
        ).load_document(
            kb_id,
            document_id,
            action,
            domain_action=domain_action,
        )
    except (KnowledgeResourceHidden, KnowledgePermissionDenied) as exc:
        _raise_knowledge_authorization_error(request, exc)


@router.get(
    "/{kb_id}/safe-metadata",
    response_model=KnowledgeSafeMetadataResponse,
)
def get_knowledge_safe_metadata(
    kb_id: UUID,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    kb = _manageable_knowledge_base(
        kb_id,
        request,
        x_organization_id,
        db,
        current_user,
    )
    return KnowledgeSafeMetadataResponse(
        safe_metadata=sanitize_kb_safe_metadata(kb.safe_metadata or {}),
    )


@router.patch(
    "/{kb_id}/safe-metadata",
    response_model=KnowledgeSafeMetadataResponse,
)
@audit(AuditAction.KNOWLEDGE_UPDATE, target_param="kb_id")
def update_knowledge_safe_metadata(
    kb_id: UUID,
    update_data: KnowledgeSafeMetadataUpdate,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _ensure_knowledge_schema_columns(db, request, KNOWLEDGE_BASE_MUTATION_COLUMNS)
    kb = _manageable_knowledge_base(
        kb_id,
        request,
        x_organization_id,
        db,
        current_user,
    )
    requested = update_data.model_dump(exclude_unset=True)
    sanitized = sanitize_kb_safe_metadata(requested)
    current = sanitize_kb_safe_metadata(kb.safe_metadata or {})
    for key in ("safe_label", "kb_safe_description", "kb_safe_topics"):
        if key not in requested:
            continue
        if key in sanitized:
            current[key] = sanitized[key]
        else:
            current.pop(key, None)
    kb.safe_metadata = current
    db.commit()
    db.refresh(kb)
    return KnowledgeSafeMetadataResponse(safe_metadata=current)


@router.patch("/{kb_id}", status_code=status.HTTP_204_NO_CONTENT)
@audit(AuditAction.KNOWLEDGE_UPDATE, target_param="kb_id")
def update_knowledge_base(
    kb_id: UUID,
    update_data: KnowledgeUpdate,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    지식 베이스의 설정을 수정합니다. (이름, 설명, 즐겨찾기 임베딩 모델)
    """
    _ensure_knowledge_schema_columns(
        db,
        request,
        KNOWLEDGE_BASE_MUTATION_COLUMNS,
    )
    organization_id = resolve_active_organization_id(
        db,
        request,
        x_organization_id,
        current_user.id,
    )
    try:
        kb = _knowledge_authorization_service(
            db,
            user_id=current_user.id,
            organization_id=organization_id,
        ).load_kb(kb_id, "write")
    except (KnowledgeResourceHidden, KnowledgePermissionDenied) as exc:
        _raise_knowledge_authorization_error(request, exc)

    embedding_model_changed = (
        update_data.embedding_model is not None
        and update_data.embedding_model != kb.embedding_model
    )
    if embedding_model_changed:
        _ensure_document_ingestion_schema_ready(db, request)

    if update_data.name is not None:
        kb.name = update_data.name
    if update_data.description is not None:
        kb.description = update_data.description
    # 임베딩 모델 변경 및 재인덱싱 트리거
    if embedding_model_changed:
        try:
            build_request_knowledge_base_reindex(db).execute(
                RequestKnowledgeBaseReindexCommand(
                    actor_id=current_user.id,
                    organization_id=organization_id,
                    knowledge_base_id=kb.id,
                    embedding_model=update_data.embedding_model,
                )
            )
        except (
            DocumentIngestionHidden,
            DocumentIngestionConflict,
            DocumentIngestionPolicyBlocked,
            DocumentIngestionPersistenceFailed,
        ) as exc:
            _raise_document_ingestion_error(request, exc)
    else:
        db.commit()
    db.refresh(kb)

    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _raise_knowledge_lifecycle_policy_error(
    request: Request,
    exc: Exception,
) -> None:
    if isinstance(exc, KnowledgeLifecycleNotFound):
        raise_api_error(
            request,
            status.HTTP_404_NOT_FOUND,
            "resource.hidden",
            "Knowledge Base not found.",
        )
    if isinstance(exc, KnowledgeLifecyclePolicyDenied):
        if exc.reason_code == "retention_policy_unavailable":
            raise_api_error(
                request,
                status.HTTP_403_FORBIDDEN,
                "policy.denied",
                "Knowledge Base retention policy does not allow hard delete.",
            )
        raise_api_error(
            request,
            status.HTTP_403_FORBIDDEN,
            "policy.denied",
            "Source-managed Knowledge lifecycle is controlled by its source.",
        )
    raise exc


@router.post("/{kb_id}/archive", status_code=status.HTTP_204_NO_CONTENT)
def archive_knowledge_base(
    kb_id: UUID,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    organization_id = resolve_active_organization_id(
        db,
        request,
        x_organization_id,
        current_user.id,
    )
    try:
        kb = _knowledge_authorization_service(
            db,
            user_id=current_user.id,
            organization_id=organization_id,
        ).load_kb(
            kb_id,
            "manage",
            domain_action="lifecycle_manage",
        )
        KnowledgeLifecycleService(db).archive_knowledge_base(
            kb,
            actor_id=current_user.id,
        )
    except (KnowledgeResourceHidden, KnowledgePermissionDenied) as exc:
        _raise_knowledge_authorization_error(request, exc)
    except (KnowledgeLifecycleNotFound, KnowledgeLifecyclePolicyDenied) as exc:
        _raise_knowledge_lifecycle_policy_error(request, exc)

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{kb_id}/restore", status_code=status.HTTP_204_NO_CONTENT)
def restore_knowledge_base(
    kb_id: UUID,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    organization_id = resolve_active_organization_id(
        db,
        request,
        x_organization_id,
        current_user.id,
    )
    try:
        kb = _knowledge_authorization_service(
            db,
            user_id=current_user.id,
            organization_id=organization_id,
        ).load_kb(
            kb_id,
            "manage",
            include_archived=True,
            domain_action="lifecycle_manage",
        )
        KnowledgeLifecycleService(db).restore_knowledge_base(
            kb,
            actor_id=current_user.id,
        )
    except (KnowledgeResourceHidden, KnowledgePermissionDenied) as exc:
        _raise_knowledge_authorization_error(request, exc)
    except (KnowledgeLifecycleNotFound, KnowledgeLifecyclePolicyDenied) as exc:
        _raise_knowledge_lifecycle_policy_error(request, exc)

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{kb_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_knowledge_base(
    kb_id: UUID,
    request: Request,
    acknowledged_hard_delete: bool = Query(default=False),
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    organization_id = resolve_active_organization_id(
        db,
        request,
        x_organization_id,
        current_user.id,
    )
    if not has_organization_manager_permission(
        db,
        current_user.id,
        organization_id,
    ):
        raise_api_error(
            request,
            status.HTTP_403_FORBIDDEN,
            "permission.denied",
            "Organization manager permission is required.",
        )
    if not acknowledged_hard_delete:
        raise_api_error(
            request,
            status.HTTP_400_BAD_REQUEST,
            "validation.failed",
            "Hard delete acknowledgement is required.",
            {"field": "acknowledged_hard_delete"},
        )
    try:
        kb = _knowledge_authorization_service(
            db,
            user_id=current_user.id,
            organization_id=organization_id,
        ).load_kb(
            kb_id,
            "manage",
            include_archived=True,
        )
        KnowledgeLifecycleService(db).hard_delete_knowledge_base(
            kb,
            actor_id=current_user.id,
        )
    except (KnowledgeResourceHidden, KnowledgePermissionDenied) as exc:
        _raise_knowledge_authorization_error(request, exc)
    except (KnowledgeLifecycleNotFound, KnowledgeLifecyclePolicyDenied) as exc:
        _raise_knowledge_lifecycle_policy_error(request, exc)

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{kb_id}/documents/{document_id}", response_model=DocumentResponse)
def get_document(
    kb_id: UUID,
    document_id: UUID,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    특정 문서를 조회합니다.
    """
    _, doc = _authorized_knowledge_document(
        kb_id,
        document_id,
        "read",
        request,
        x_organization_id,
        db,
        current_user,
    )

    if doc.status in {"indexing", "processing"}:
        _ensure_document_ingestion_schema_ready(db, request)

    if finalize_stale_processing_start(
        db,
        doc.id,
    ) or recover_timed_out_document_with_artifacts(db, doc.id):
        db.refresh(doc)

    return DocumentResponse(
        id=doc.id,
        filename=doc.filename,
        status=project_safe_document_status(doc.status),
        created_at=doc.created_at,
        updated_at=doc.updated_at,
        error_message=project_safe_document_error(doc.status, doc.error_message),
        chunk_count=len(doc.chunks),
        # token_count=doc.token_count,
        source_type=doc.source_type,
        meta_info=project_safe_document_metadata(doc.meta_info),
    )


@router.get("/{kb_id}/documents/{document_id}/ingestion")
def get_document_ingestion_status(
    kb_id: UUID,
    document_id: UUID,
    request: Request,
    response: Response,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    response.headers["Cache-Control"] = "no-store"
    _authorized_knowledge_document(
        kb_id,
        document_id,
        "read",
        request,
        x_organization_id,
        db,
        current_user,
    )
    _ensure_document_ingestion_schema_ready(db, request)
    job = build_read_document_ingestion_status(db).execute(document_id)
    return {"job": project_safe_ingestion_job(job)}


@router.post(
    "/{kb_id}/documents/{document_id}/ingestion/retry",
    status_code=status.HTTP_202_ACCEPTED,
)
@audit(AuditAction.DOCUMENT_PROCESS, target_param="document_id")
def retry_document_ingestion(
    kb_id: UUID,
    document_id: UUID,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _authorized_knowledge_document(
        kb_id,
        document_id,
        "read",
        request,
        x_organization_id,
        db,
        current_user,
    )
    _ensure_document_ingestion_schema_ready(db, request)
    latest_job = build_read_document_ingestion_status(db).execute(document_id)
    domain_action = (
        "sync_manage"
        if latest_job is not None and latest_job.operation == "sync"
        else None
    )
    kb, _ = _authorized_knowledge_document(
        kb_id,
        document_id,
        "write",
        request,
        x_organization_id,
        db,
        current_user,
        domain_action=domain_action,
    )
    try:
        result = build_redrive_document_ingestion(db).execute(
            RedriveDocumentIngestionCommand(
                actor_id=current_user.id,
                organization_id=kb.organization_id,
                knowledge_base_id=kb.id,
                document_id=document_id,
                expected_job_id=(latest_job.job_id if latest_job is not None else None),
            )
        )
    except (
        DocumentIngestionHidden,
        DocumentIngestionConflict,
        DocumentIngestionPolicyBlocked,
        DocumentIngestionPersistenceFailed,
    ) as exc:
        _raise_document_ingestion_error(request, exc)
    return {
        "status": "processing",
        "job": project_safe_ingestion_job(result.job),
        "dispatch_deferred": result.dispatch_deferred,
    }


@router.get(
    "/{kb_id}/documents/{document_id}/edit-config",
    response_model=DocumentEditConfigResponse,
)
def get_document_edit_config(
    kb_id: UUID,
    document_id: UUID,
    request: Request,
    response: Response,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return a bounded edit projection to an authorized KB writer."""

    response.headers["Cache-Control"] = "no-store"
    _, doc = _authorized_knowledge_document(
        kb_id,
        document_id,
        "write",
        request,
        x_organization_id,
        db,
        current_user,
    )
    return DocumentEditConfigResponse(**project_document_edit_config(doc))


@router.get("/{kb_id}/documents/{document_id}/content")
def get_document_content(
    kb_id: UUID,
    document_id: UUID,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    문서의 원본 파일을 반환합니다. (브라우저 표시용)
    """
    _, doc = _authorized_knowledge_document(
        kb_id,
        document_id,
        "content_read",
        request,
        x_organization_id,
        db,
        current_user,
    )

    return KnowledgeDocumentContentService().build_content_response(doc)


def _lock_db_connection_reference(
    request: Request,
    db: Session,
    *,
    document: Document,
    owner_id: UUID,
    db_config: dict | None,
) -> ConnectionLifecycleService | None:
    if document.source_type != "DB" or not db_config:
        return None

    raw_connection_id = db_config.get("connection_id")
    if raw_connection_id is None:
        return None
    try:
        connection_id = UUID(str(raw_connection_id))
    except (TypeError, ValueError, AttributeError):
        raise_api_error(
            request,
            400,
            "validation.failed",
            "The DB connection reference is invalid.",
        )

    lifecycle_service = ConnectionLifecycleService(db)
    try:
        lifecycle_service.lock_owned_connection_for_reference(
            connection_id=connection_id,
            owner_id=owner_id,
        )
    except ConnectionLifecycleHidden:
        db.rollback()
        raise_api_error(
            request,
            404,
            "resource.hidden",
            "Connection not found.",
        )
    except ConnectionLifecycleBusy:
        db.rollback()
        raise_api_error(
            request,
            503,
            "connection.reference_busy",
            "The DB connection reference is temporarily busy.",
        )
    except ConnectionLifecycleUnavailable:
        db.rollback()
        raise_api_error(
            request,
            503,
            "connection.reference_unavailable",
            "The DB connection reference is temporarily unavailable.",
        )
    return lifecycle_service


def _commit_db_connection_reference(
    request: Request,
    lifecycle_service: ConnectionLifecycleService,
) -> None:
    try:
        lifecycle_service.commit_reference_mutation()
    except ConnectionLifecycleBusy:
        raise_api_error(
            request,
            503,
            "connection.reference_busy",
            "The DB connection reference is temporarily busy.",
        )
    except ConnectionLifecycleUnavailable:
        raise_api_error(
            request,
            503,
            "connection.reference_unavailable",
            "The DB connection reference is temporarily unavailable.",
        )


@router.post(
    "/{kb_id}/documents/{document_id}/process", status_code=status.HTTP_202_ACCEPTED
)
@audit(AuditAction.DOCUMENT_PROCESS, target_param="document_id")
async def process_document(
    kb_id: UUID,
    document_id: UUID,
    preview_request: DocumentPreviewRequest,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    문서 설정(청킹 등)을 저장하고 백그라운드 처리를 시작합니다.
    """

    # 1. 문서 조회 (권한 확인)
    kb, doc = _authorized_knowledge_document(
        kb_id,
        document_id,
        "write",
        request,
        x_organization_id,
        db,
        current_user,
    )
    _ensure_document_ingestion_schema_ready(db, request)

    try:
        normalized_chunking_mode = validate_chunking_request(
            chunking_mode=preview_request.chunking_mode,
            source_type=doc.source_type,
            selection_mode=preview_request.selection_mode,
        )
    except RAGHierarchyError as exc:
        raise _chunking_http_exception(exc)

    expected_document_updated_at = doc.updated_at
    validated_db_config = None
    connection_reference_lifecycle = None
    if doc.source_type == "DB":
        validated_db_config = _validated_db_source_config_or_error(
            request,
            db,
            current_user_id=current_user.id,
            stored_meta_info=doc.meta_info,
            submitted_db_config=preview_request.db_config,
        )
        selections = validated_db_config.persisted_db_config.get("selections", [])
        join_config = validated_db_config.persisted_db_config.get("join_config", {})
        if len(selections) == 2 and not join_config.get("enabled", False):
            raise HTTPException(
                status_code=400, detail="선택한 테이블 간 FK 관계가 없습니다."
            )
        connection_reference_lifecycle = _lock_db_connection_reference(
            request,
            db,
            document=doc,
            owner_id=current_user.id,
            db_config=validated_db_config.runtime_config,
        )

    meta_updates = {
        "segment_identifier": preview_request.segment_identifier,
        "remove_urls_emails": preview_request.remove_urls_emails,
        "remove_whitespace": preview_request.remove_whitespace,
        "strategy": preview_request.strategy,
        "chunking_mode": normalized_chunking_mode,
        "db_config": (
            validated_db_config.persisted_db_config
            if validated_db_config is not None
            else preview_request.db_config
        ),
        "selection_mode": preview_request.selection_mode,
        "chunk_range": preview_request.chunk_range,
        "keyword_filter": preview_request.keyword_filter,
    }
    meta_remove_keys: tuple[str, ...] = ()
    if validated_db_config is not None:
        meta_updates["connection_id"] = str(validated_db_config.connection_id)
        current_meta = dict(doc.meta_info or {})
        sanitized_current_meta = dict(current_meta)
        remove_legacy_connection_details(sanitized_current_meta)
        meta_remove_keys = tuple(
            sorted(set(current_meta).difference(sanitized_current_meta))
        )

    try:
        result = build_request_document_ingestion(
            db,
            unit_of_work=connection_reference_lifecycle,
        ).execute(
            RequestDocumentIngestionCommand(
                actor_id=current_user.id,
                organization_id=kb.organization_id,
                knowledge_base_id=kb.id,
                document_id=document_id,
                operation="process",
                settings=DocumentIngestionSettings(
                    chunk_size=preview_request.chunk_size,
                    chunk_overlap=preview_request.chunk_overlap,
                    embedding_model=kb.embedding_model,
                    meta_updates=meta_updates,
                    meta_remove_keys=meta_remove_keys,
                ),
                expected_document_updated_at=expected_document_updated_at,
                require_document_revision_match=(
                    connection_reference_lifecycle is not None
                ),
            )
        )
    except (
        DocumentIngestionHidden,
        DocumentIngestionConflict,
        DocumentIngestionPolicyBlocked,
        DocumentIngestionPersistenceFailed,
    ) as exc:
        _raise_document_ingestion_error(request, exc)

    return {
        "status": "processing",
        "message": "Document processing started",
        "job_id": str(result.job.job_id),
        "reused": result.reused,
        "dispatch_deferred": result.dispatch_deferred,
    }


@router.post(
    "/{kb_id}/documents/{document_id}/preview", response_model=DocumentPreviewResponse
)
def preview_document_chunking(
    kb_id: UUID,
    document_id: UUID,
    preview_request: DocumentPreviewRequest,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    문서 청킹 설정을 미리보기 합니다. DB를 업데이트하지 않고 결과만 반환합니다.
    """
    # 1. 문서 존재 및 권한 확인
    kb, doc = _authorized_knowledge_document(
        kb_id,
        document_id,
        "write",
        request,
        x_organization_id,
        db,
        current_user,
    )

    try:
        normalized_chunking_mode = validate_chunking_request(
            chunking_mode=preview_request.chunking_mode,
            source_type=doc.source_type,
            selection_mode=preview_request.selection_mode,
        )
    except RAGHierarchyError as exc:
        raise _chunking_http_exception(exc)

    preview_db_config = preview_request.db_config
    preview_meta_info = doc.meta_info
    if doc.source_type == "DB":
        validated_db_config = _validated_db_source_config_or_error(
            request,
            db,
            current_user_id=current_user.id,
            stored_meta_info=doc.meta_info,
            submitted_db_config=preview_request.db_config,
        )
        preview_db_config = validated_db_config.runtime_config
        preview_meta_info = dict(doc.meta_info or {})
        preview_meta_info["connection_id"] = str(
            validated_db_config.connection_id
        )
        preview_meta_info["db_config"] = (
            validated_db_config.persisted_db_config
        )
        remove_legacy_connection_details(preview_meta_info)

    # 2. 서비스 호출
    service = IngestionService(
        db,
        user_id=current_user.id,
        organization_id=kb.organization_id,
    )
    try:
        segments = service.preview_chunking(
            file_path=doc.file_path,
            chunk_size=preview_request.chunk_size,
            chunk_overlap=preview_request.chunk_overlap,
            segment_identifier=preview_request.segment_identifier,
            remove_urls_emails=preview_request.remove_urls_emails,
            remove_whitespace=preview_request.remove_whitespace,
            strategy=preview_request.strategy,
            source_type=doc.source_type,
            chunking_mode=normalized_chunking_mode,
            meta_info=preview_meta_info,
            db_config=preview_db_config,
            # 필터링 파라미터 전달
            selection_mode=preview_request.selection_mode,
            chunk_range=preview_request.chunk_range,
            keyword_filter=preview_request.keyword_filter,
        )
    except IngestionPreviewSourceError as exc:
        if exc.reason_code == "resource.hidden":
            raise_api_error(
                request,
                404,
                "resource.hidden",
                "Resource not found.",
            )
        if exc.reason_code == "source.temporarily_unavailable":
            raise_api_error(
                request,
                503,
                "source.temporarily_unavailable",
                "The DB source is temporarily unavailable.",
            )
        if exc.reason_code == RAW_PARSER_EGRESS_UNAVAILABLE_REASON:
            raise_api_error(
                request,
                409,
                RAW_PARSER_EGRESS_UNAVAILABLE_REASON,
                "External document parsing is not available.",
            )
        raise_api_error(
            request,
            400,
            "validation.failed",
            "Invalid DB source configuration.",
        )
    except ValueError as e:
        logger.warning("Preview validation failed: %s", type(e).__name__)
        raise HTTPException(
            status_code=400,
            detail={"reason_code": "validation.failed"},
        )
    except Exception as e:
        logger.error("Preview failed: %s", type(e).__name__)
        raise HTTPException(
            status_code=500,
            detail={"reason_code": "preview.failed"},
        )

    # 3. 응답 반환
    return DocumentPreviewResponse(
        segments=segments,
        total_count=len(segments),
        preview_text_sample="",  # 필요시 원본 텍스트 일부 반환 가능
    )


@router.post(
    "/{kb_id}/documents/{document_id}/sync", status_code=status.HTTP_202_ACCEPTED
)
@audit(AuditAction.DOCUMENT_PROCESS, target_param="document_id")
async def sync_document(
    kb_id: UUID,
    document_id: UUID,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    문서를 동기화합니다. (API 소스 등 재위)
    기존 설정을 유지하면서 처리를 다시 시작합니다.
    """
    # 1. 문서 조회
    kb, doc = _authorized_knowledge_document(
        kb_id,
        document_id,
        "write",
        request,
        x_organization_id,
        db,
        current_user,
        domain_action="sync_manage",
    )
    _ensure_document_ingestion_schema_ready(db, request)

    expected_document_updated_at = doc.updated_at
    connection_reference_lifecycle = None
    if str(getattr(doc.source_type, "value", doc.source_type)) == "DB":
        connection_reference_lifecycle = _lock_db_connection_reference(
            request,
            db,
            document=doc,
            owner_id=current_user.id,
            db_config={
                "connection_id": dict(doc.meta_info or {}).get("connection_id")
            },
        )

    try:
        result = build_request_document_ingestion(
            db,
            unit_of_work=connection_reference_lifecycle,
        ).execute(
            RequestDocumentIngestionCommand(
                actor_id=current_user.id,
                organization_id=kb.organization_id,
                knowledge_base_id=kb.id,
                document_id=document_id,
                operation="sync",
                settings=DocumentIngestionSettings(
                    embedding_model=kb.embedding_model,
                ),
                expected_document_updated_at=expected_document_updated_at,
                require_document_revision_match=(
                    connection_reference_lifecycle is not None
                ),
            )
        )
    except (
        DocumentIngestionHidden,
        DocumentIngestionConflict,
        DocumentIngestionPolicyBlocked,
        DocumentIngestionPersistenceFailed,
    ) as exc:
        _raise_document_ingestion_error(request, exc)

    return {
        "status": "processing",
        "message": "Document sync started",
        "job_id": str(result.job.job_id),
        "reused": result.reused,
        "dispatch_deferred": result.dispatch_deferred,
    }
