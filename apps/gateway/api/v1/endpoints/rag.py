import json
import logging
from typing import AsyncIterator, List, Optional
from urllib.parse import quote
from uuid import UUID

from fastapi import (
    APIRouter,
    Body,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from apps.gateway.api.deps import get_db
from apps.gateway.auth.dependencies import get_current_user
from apps.gateway.application.knowledge_document_ingestion.use_cases import (
    DocumentIngestionConflict,
    DocumentIngestionHidden,
    DocumentIngestionPersistenceFailed,
    DocumentIngestionPolicyBlocked,
    DocumentIngestionSettings,
    RequestDocumentIngestionCommand,
)
from apps.gateway.composition.knowledge_document_ingestion import (
    build_read_document_ingestion_status,
    build_request_document_ingestion,
)
from apps.gateway.core.config import settings

# from services.ingestion_local_service import IngestionService
from apps.gateway.services.ingestion.service import (
    IngestionOrchestrator as IngestionService,
)
from apps.gateway.services.ingestion.service import (
    finalize_stale_processing_start,
    recover_timed_out_document_with_artifacts,
)
from apps.gateway.services.connection_use_service import (
    resolve_connection_use_or_hidden,
)
from apps.gateway.services.connection_lifecycle_service import (
    ConnectionLifecycleBusy,
    ConnectionLifecycleHidden,
    ConnectionLifecycleService,
    ConnectionLifecycleUnavailable,
)
from apps.gateway.services.knowledge_authorization_service import (
    KnowledgeAuthorizationService,
    KnowledgePermissionDenied,
    KnowledgeResourceHidden,
)
from apps.gateway.services.knowledge_base_query_service import (
    KnowledgeBaseCreateFailed,
    KnowledgeBaseQueryService,
    KnowledgeSchemaNotReady,
    KnowledgeValidationError,
)
from apps.gateway.services.knowledge_document_projection import (
    project_safe_document_error,
    project_safe_document_progress,
    project_safe_document_progress_message,
    project_safe_document_status,
)
from apps.gateway.services.knowledge_document_lifecycle_service import (
    KnowledgeDocumentLifecycleHidden,
    KnowledgeDocumentLifecycleService,
    KnowledgeDocumentLifecycleUnavailable,
)
from apps.gateway.services.knowledge_document_ingestion_readiness import (
    KnowledgeDocumentIngestionUnavailable,
    require_knowledge_document_ingestion_schema,
)
from apps.gateway.services.knowledge_document_registration_service import (
    KnowledgeDocumentRegistrationError,
    KnowledgeDocumentRegistrationHidden,
    KnowledgeDocumentRegistrationPolicyDenied,
    KnowledgeDocumentRegistrationService,
    KnowledgeDocumentRegistrationUnavailable,
    KnowledgeDocumentSlotOccupied,
)
from apps.gateway.services.rag_agent_answer_service import RAGAgentAnswerService
from apps.gateway.services.retrieval import RetrievalService
from apps.gateway.services.storage import get_storage_service
from apps.gateway.utils.api_errors import (
    error_detail,
    parse_organization_id,
    raise_api_error,
)
from apps.gateway.utils.audit import audit
from apps.shared.audit.actions import AuditAction
from apps.shared.audit.logger import record_audit
from apps.shared.db.models.knowledge import (
    Document,
    KnowledgeBase,
    SourceType,
)
from apps.shared.db.models.user import User
from apps.shared.schemas.rag import (
    ApiPreviewRequest,
    ChunkPreview,
    DocumentAnalyzeResponse,
    IngestionResponse,
    KnowledgeBaseCreate,
    RAGAgentAnswerRequest,
    RAGAgentAnswerResponse,
    RAGAgentSSEEvent,
    RAGResponse,
    SearchQuery,
)
from apps.shared.services.egress_guard import (
    EgressGuardError,
    safe_http_request,
)
from apps.shared.services.outbound_operation_policy import KNOWLEDGE_API_FETCH
from apps.shared.services.knowledge_permission_service import KnowledgePermissionHelper
from apps.shared.services.knowledge_document_ingestion_projection import (
    project_safe_ingestion_job,
)
from apps.shared.services.permission_audit import record_resource_permission_denied
from apps.shared.services.permissions import has_organization_scope_access
from apps.shared.services.rag_filters import normalize_metadata_filter
from apps.shared.services.rag_hierarchy import (
    RAGHierarchyError,
    validate_chunking_request,
)

logger = logging.getLogger(__name__)
router = APIRouter()

SAFE_DOCUMENT_EXTENSIONS = {
    ".csv",
    ".docx",
    ".md",
    ".pdf",
    ".txt",
    ".xls",
    ".xlsx",
}


def _ensure_document_ingestion_schema_ready(db: Session, request: Request) -> None:
    try:
        require_knowledge_document_ingestion_schema(db)
    except KnowledgeDocumentIngestionUnavailable as exc:
        raise_api_error(
            request,
            503,
            "knowledge.ingestion_schema_not_ready",
            "Knowledge document ingestion is temporarily unavailable.",
            {"reason": exc.reason_code},
        )


def _chunking_http_exception(exc: RAGHierarchyError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"reason": exc.reason, "message": exc.message},
    )


def _require_search_knowledge_base_id(request: Request, query: SearchQuery) -> UUID:
    if query.knowledge_base_id is None:
        raise_api_error(
            request,
            400,
            "validation.failed",
            "knowledge_base_id is required for RAG search-test.",
            {"field": "knowledge_base_id"},
        )
    return query.knowledge_base_id


def _authorize_rag_use(
    request: Request,
    db: Session,
    current_user: User,
    organization_id: UUID,
    knowledge_base_id: UUID,
) -> KnowledgeBase:
    kb = db.query(KnowledgeBase).filter(KnowledgeBase.id == knowledge_base_id).first()
    if (
        kb is None
        or kb.organization_id != organization_id
        or not has_organization_scope_access(db, current_user.id, organization_id)
    ):
        raise_api_error(
            request,
            404,
            "resource.not_found",
            "Knowledge Base not found.",
        )

    decision = KnowledgePermissionHelper(
        db,
        user_id=current_user.id,
        organization_id=organization_id,
    ).evaluate_kb_use(kb)
    if decision.allowed:
        return kb

    if decision.external_reason_code == "resource.hidden":
        raise_api_error(
            request,
            404,
            "resource.hidden",
            "Knowledge Base not found.",
        )

    record_resource_permission_denied(
        user_id=current_user.id,
        resource_type="knowledge_base",
        resource_id=knowledge_base_id,
        action="use",
        effective_auth_state=decision.effective_auth_state,
        organization_id=organization_id,
        metadata={
            "request_id": getattr(request.state, "request_id", None),
            "path": request.url.path,
            "reason_code": decision.reason_code or "kb_use_denied",
        },
    )
    exc = HTTPException(
        status_code=403,
        detail=error_detail(
            request,
            "permission.denied",
            "Knowledge Base use permission is required.",
        ),
    )
    setattr(exc, "audit_recorded", True)
    raise exc


def _authorize_knowledge_document_action(
    request: Request,
    db: Session,
    current_user: User,
    organization_id: UUID,
    document_id: UUID,
    action: str,
) -> tuple[KnowledgeBase, Document]:
    document = db.query(Document).filter(Document.id == document_id).first()
    if document is None:
        raise_api_error(
            request,
            404,
            "resource.hidden",
            "Document not found.",
        )
    try:
        return KnowledgeAuthorizationService(
            db,
            user_id=current_user.id,
            organization_id=organization_id,
        ).load_document(
            document.knowledge_base_id,
            document_id,
            action,
        )
    except KnowledgeResourceHidden:
        raise_api_error(
            request,
            404,
            "resource.hidden",
            "Document not found.",
        )
    except KnowledgePermissionDenied:
        raise_api_error(
            request,
            403,
            "permission.denied",
            "Knowledge permission is required.",
        )


def _record_rag_retrieve_audit(
    request: Request,
    current_user: User,
    organization_id: UUID,
    knowledge_base_id: UUID,
    metadata_filter,
    result_count: int,
    mode: str,
) -> None:
    metadata = {
        "actor": {
            "id": str(current_user.id),
            "email": getattr(current_user, "email", None),
            "name": getattr(current_user, "name", None),
        },
        "request_id": getattr(request.state, "request_id", None),
        "organization_id": str(organization_id),
        "knowledge_base_id": str(knowledge_base_id),
        "retrieval_mode": mode,
        "result_count": result_count,
        "metadata_filter": metadata_filter.audit_summary()
        if metadata_filter is not None
        else {},
        "policy_evaluated": False,
    }
    record_audit(
        action=AuditAction.RAG_RETRIEVE,
        category="action",
        actor_id=current_user.id,
        actor_type="user",
        target_type="knowledge_base",
        target_id=knowledge_base_id,
        status="success",
        metadata=metadata,
    )


def _rag_agent_sse_event(event: str, data: dict) -> str:
    payload = RAGAgentSSEEvent(event=event, data=data).model_dump(mode="json")
    return (
        f"event: {payload['event']}\n"
        f"data: {json.dumps(payload['data'], ensure_ascii=False, default=str)}\n\n"
    )


async def _rag_agent_sse_events(
    events: AsyncIterator[tuple[str, dict]],
) -> AsyncIterator[str]:
    async for event, data in events:
        yield _rag_agent_sse_event(event, data)


@router.post("/agent/answer", response_model=RAGAgentAnswerResponse)
async def rag_agent_answer(
    payload: RAGAgentAnswerRequest,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    organization_id = parse_organization_id(request, x_organization_id)
    service = RAGAgentAnswerService(
        db=db,
        current_user=current_user,
        request=request,
        organization_id=organization_id,
    )
    return await service.answer(payload)


@router.post("/agent/answer/stream")
async def rag_agent_answer_stream(
    payload: RAGAgentAnswerRequest,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    organization_id = parse_organization_id(request, x_organization_id)
    service = RAGAgentAnswerService(
        db=db,
        current_user=current_user,
        request=request,
        organization_id=organization_id,
    )
    execution = service.prepare_execution(payload)
    return StreamingResponse(
        _rag_agent_sse_events(service.stream_events(execution)),
        media_type="text/event-stream",
    )


@router.post("/upload/presigned-url")
async def generate_presigned_url(
    request: Request,
    filename: str = Body(..., embed=True),
    content_type: str = Body(..., embed=True),
    knowledge_base_id: UUID | None = Body(
        None,
        embed=True,
        alias="knowledgeBaseId",
    ),
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    S3 Presigned URL 생성 (프론트엔드 직접 업로드용)

    브라우저가 S3에 직접 파일을 업로드할 수 있는 임시 URL을 생성합니다.
    이를 통해 백엔드 서버 부하를 줄이고 업로드 속도를 향상시킬 수 있습니다.

    Args:
        filename: 업로드할 파일명
        content_type: 파일의 MIME 타입 (예: application/pdf)
        knowledge_base_id: Knowledge 최초 등록이면 fast precheck할 대상 KB
        current_user: 인증된 사용자

    Returns:
        dict: {
            "upload_url": 브라우저가 PUT 요청을 보낼 Presigned URL,
            "s3_key": S3 객체 키 (나중에 참조용),
            "method": HTTP 메서드 ("PUT")
        }

    Example:
        Request:
        POST /api/v1/rag/upload/presigned-url
        {
            "filename": "document.pdf",
            "content_type": "application/pdf"
        }

        Response:
        {
            "upload_url": "https://s3.amazonaws.com/...?signature=...",
            "s3_key": "uploads/user-123/abc-123_document.pdf",
            "method": "PUT"
        }
    """
    try:
        if knowledge_base_id is not None:
            organization_id = parse_organization_id(request, x_organization_id)
            _load_writable_knowledge_base(
                request,
                db,
                current_user,
                organization_id,
                knowledge_base_id,
            )
            _ensure_initial_document_slot(
                request,
                db,
                knowledge_base_id=knowledge_base_id,
                organization_id=organization_id,
            )

        safe_filename = _validate_safe_document_filename(filename)
        storage = get_storage_service()

        # S3 Presigned URL 생성
        presigned_data = storage.generate_presigned_upload_url(
            filename=safe_filename,
            content_type=content_type,
            user_id=str(current_user.id),
        )

        return {
            "upload_url": presigned_data["url"],
            "s3_key": presigned_data["key"],
            "method": presigned_data["method"],
            "use_backend_proxy": presigned_data.get("use_backend_proxy"),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Presigned URL generation failed: %s", type(e).__name__)
        raise HTTPException(
            status_code=500,
            detail={"reason_code": "storage.presigned_url_failed"},
        )


@router.post("/upload", response_model=IngestionResponse)
@audit(AuditAction.DOCUMENT_UPLOAD)
async def upload_document(
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    file: Optional[UploadFile] = File(None, alias="file"),
    knowledge_base_id: Optional[UUID] = Form(None, alias="knowledgeBaseId"),
    source_type: str = Form("FILE", alias="sourceType"),
    # [NEW] S3 Direct Upload Fields
    s3_file_url: Optional[str] = Form(None, alias="s3FileUrl"),
    s3_file_key: Optional[str] = Form(None, alias="s3FileKey"),
    # API Config Fields
    api_url: Optional[str] = Form(None, alias="apiUrl"),
    api_method: str = Form("GET", alias="apiMethod"),
    api_headers: Optional[str] = Form(None, alias="apiHeaders"),
    api_body: Optional[str] = Form(None, alias="apiBody"),
    connection_id: Optional[str] = Form(None, alias="connectionId"),
    # 지식 베이스 신규 생성일 때만 필요한 정보들
    name: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    ai_model: Optional[str] = Form(None, alias="embeddingModel"),
    top_k: int = Form(5, alias="topK"),
    similarity_threshold: float = Form(0.7, alias="similarity"),
    # 문서별 청킹 설정
    chunk_size: int = Form(1000, alias="chunkSize"),
    chunk_overlap: int = Form(200, alias="chunkOverlap"),
    chunking_mode: str = Form("flat", alias="chunkingMode"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    파일 업로드 및 처리 파이프라인 시작점

    [NEW] S3 Direct Upload 지원:
    - s3_file_url과 s3_file_key가 제공되면 이미 S3에 업로드된 파일로 처리
    - file이 제공되면 기존 방식대로 백엔드를 통해 S3에 업로드 (기존 방식)
    """
    # 0. 환경 변수 확인 (Ingestion Mode)
    ingestion_mode = (settings.STORAGE_TYPE or "LOCAL").upper()
    logger.info(f"=== [upload_document] Request Received (Mode: {ingestion_mode}) ===")
    organization_id = parse_organization_id(request, x_organization_id)

    try:
        source_enum = SourceType(source_type)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid source type")

    try:
        normalized_chunking_mode = validate_chunking_request(
            chunking_mode=chunking_mode,
            source_type=source_enum,
        )
    except RAGHierarchyError as exc:
        raise _chunking_http_exception(exc)

    prepared_db_source = None
    prepared_db_connection_id = None
    if source_enum == SourceType.DB:
        prepared_db_source = _prepare_db_source(
            request,
            db,
            current_user,
            connection_id,
        )
        prepared_db_connection_id = UUID(prepared_db_source[2]["connection_id"])

    # 1. 자료 확인 또는 생성
    target_kb_id, target_ai_model = _get_or_create_knowledge_base(
        request,
        db,
        current_user,
        organization_id,
        knowledge_base_id,
        name,
        description,
        ai_model,
        top_k,
        similarity_threshold,
        file,
    )
    _ensure_initial_document_slot(
        request,
        db,
        knowledge_base_id=target_kb_id,
        organization_id=organization_id,
    )

    # 2. Ingestion Service 초기화
    local_service = IngestionService(
        db,
        user_id=current_user.id,
        organization_id=organization_id,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        ai_model=target_ai_model,
    )

    # 3. 소스 타입별 데이터 준비 (Strategy Pattern)
    backend_owned_upload_path: str | None = None
    if source_enum == SourceType.FILE:
        # [NEW] S3 Direct Upload 방식
        if s3_file_url and s3_file_key:
            file_path, filename, meta_info = _prepare_direct_upload_source(
                s3_file_key=s3_file_key,
                user_id=current_user.id,
            )

        # [기존] 백엔드 중계 업로드 방식
        elif file:
            file_path, filename, meta_info = _prepare_file_source(local_service, file)
            meta_info["upload_method"] = "backend"
            backend_owned_upload_path = file_path
        else:
            raise HTTPException(
                status_code=400,
                detail="File or S3 URL is required for FILE source type",
            )
    elif source_enum == SourceType.API:
        file_path, filename, meta_info = _prepare_api_source(
            api_url, api_method, api_headers, api_body
        )
    elif source_enum == SourceType.DB:  # [NEW] DB 타입 처리
        file_path, filename, meta_info = prepared_db_source
    else:
        raise HTTPException(status_code=400, detail="Invalid source type")

    meta_info = dict(meta_info or {})
    meta_info["chunking_mode"] = normalized_chunking_mode

    if prepared_db_connection_id is not None:
        _lock_db_connection_reference_for_registration(
            request,
            db,
            connection_id=prepared_db_connection_id,
            owner_id=current_user.id,
        )

    # 4. DB 레코드 생성 (Pending 상태)
    try:
        doc_id = KnowledgeDocumentRegistrationService(
            db
        ).register_initial_document(
            knowledge_base_id=target_kb_id,
            organization_id=organization_id,
            filename=filename,
            file_path=file_path,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            source_type=source_enum,
            meta_info=meta_info,
        )
    except KnowledgeDocumentRegistrationError as exc:
        if (
            backend_owned_upload_path is not None
            and _is_backend_upload_cleanup_safe(exc)
        ):
            _cleanup_backend_upload(backend_owned_upload_path)
        _raise_document_registration_error(request, exc)
    except Exception as exc:
        logger.error(
            "Unexpected document registration failure: %s",
            type(exc).__name__,
        )
        raise_api_error(
            request,
            500,
            "knowledge.document_registration_failed",
            "Knowledge document registration failed.",
        )

    return IngestionResponse(
        knowledge_base_id=target_kb_id,
        document_id=doc_id,
        status="pending",
        message="자료가 등록되었습니다. 설정을 확인하고 처리를 시작해주세요.",
    )


def _prepare_db_source(
    request: Request,
    db: Session,
    user: User,
    connection_id: Optional[str],
):
    """DB 소스처리를 위한 데이터 준비"""
    conn = resolve_connection_use_or_hidden(
        request,
        db,
        connection_id=connection_id,
        execution_subject_user_id=user.id,
    )

    return None, "Database source", {"connection_id": str(conn.id)}


def _lock_db_connection_reference_for_registration(
    request: Request,
    db: Session,
    *,
    connection_id: UUID,
    owner_id: UUID,
) -> None:
    """Hold the MBA-273 reference lock through document registration commit."""

    try:
        ConnectionLifecycleService(db).lock_owned_connection_for_reference(
            connection_id=connection_id,
            owner_id=owner_id,
        )
    except ConnectionLifecycleHidden:
        db.rollback()
        raise_api_error(
            request,
            404,
            "resource.hidden",
            "Resource not found.",
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


@router.post("/document/{document_id}/analyze", response_model=DocumentAnalyzeResponse)
async def analyze_document(
    document_id: UUID,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    문서 분석 API: 페이지 수 및 LlamaParse 비용 예측 반환
    """
    organization_id = parse_organization_id(request, x_organization_id)
    _authorize_knowledge_document_action(
        request,
        db,
        current_user,
        organization_id,
        document_id,
        "write",
    )
    ingestion_service = IngestionService(
        db,
        user_id=current_user.id,
        organization_id=organization_id,
    )
    try:
        result = await ingestion_service.analyze_document(document_id)
        return result
    except Exception as e:
        logger.error("Document analysis failed: %s", type(e).__name__)
        raise HTTPException(
            status_code=400,
            detail={"reason_code": "document.analyze_failed"},
        )


@router.post("/document/{document_id}/confirm")
@audit(AuditAction.DOCUMENT_PROCESS, target_param="document_id")
async def confirm_document_parsing(
    document_id: UUID,
    request: Request,
    strategy: str = "llamaparse",  # "llamaparse" or "general"
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    비용 승인 대기 중인 문서의 파싱을 재개합니다.
    """
    organization_id = parse_organization_id(request, x_organization_id)
    kb, _ = _authorize_knowledge_document_action(
        request,
        db,
        current_user,
        organization_id,
        document_id,
        "write",
    )

    if strategy not in {"llamaparse", "general"}:
        raise_api_error(
            request,
            400,
            "validation.failed",
            "Unsupported document parsing strategy.",
        )

    _ensure_document_ingestion_schema_ready(db, request)

    try:
        result = build_request_document_ingestion(db).execute(
            RequestDocumentIngestionCommand(
                actor_id=current_user.id,
                organization_id=organization_id,
                knowledge_base_id=kb.id,
                document_id=document_id,
                operation="resume",
                settings=DocumentIngestionSettings(
                    embedding_model=kb.embedding_model,
                    meta_updates={"strategy": strategy},
                ),
                required_document_status="waiting_for_approval",
            )
        )
    except DocumentIngestionHidden:
        raise_api_error(request, 404, "resource.hidden", "Document not found.")
    except DocumentIngestionConflict:
        raise_api_error(
            request,
            409,
            "ingestion.already_in_progress",
            "Another document ingestion intent is already in progress.",
        )
    except DocumentIngestionPolicyBlocked as exc:
        raise_api_error(
            request,
            409,
            exc.reason_code,
            "Document ingestion is not available for the current resource state.",
        )
    except DocumentIngestionPersistenceFailed:
        raise_api_error(
            request,
            503,
            "ingestion.admission_unavailable",
            "Document ingestion is temporarily unavailable.",
        )

    return {
        "message": f"Parsing resumed with strategy: {strategy}",
        "status": "processing",
        "job_id": str(result.job.job_id),
        "reused": result.reused,
        "dispatch_deferred": result.dispatch_deferred,
    }


@router.delete("/document/{document_id}")
@audit(AuditAction.DOCUMENT_DELETE, target_param="document_id")
def delete_document(
    document_id: UUID,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    문서를 삭제합니다. (연관된 청크도 자동 삭제됨)
    """
    organization_id = parse_organization_id(request, x_organization_id)
    kb, _ = _authorize_knowledge_document_action(
        request,
        db,
        current_user,
        organization_id,
        document_id,
        "write",
    )

    try:
        deleted = KnowledgeDocumentLifecycleService(db).delete_document(
            knowledge_base_id=kb.id,
            organization_id=organization_id,
            document_id=document_id,
        )
    except KnowledgeDocumentLifecycleHidden:
        raise_api_error(
            request,
            404,
            "resource.hidden",
            "Document not found.",
        )
    except KnowledgeDocumentLifecycleUnavailable:
        raise_api_error(
            request,
            503,
            "knowledge.document_delete_unavailable",
            "Document deletion is temporarily unavailable.",
        )

    # DB commit 이후 request가 소유하던 storage reference를 best-effort 정리한다.
    if deleted.file_path:
        storage = get_storage_service()
        try:
            storage.delete(deleted.file_path)
        except Exception as e:
            logger.warning("Failed to delete document file: %s", type(e).__name__)

    return {"status": "success", "message": "Document deleted successfully"}


@router.post("/search-test/chat", response_model=RAGResponse)
async def search_test_chat(
    query: SearchQuery,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Search Test] RAG Chat Mode
    벡터 검색 + LLM 답변 생성
    """
    organization_id = parse_organization_id(request, x_organization_id)
    knowledge_base_id = _require_search_knowledge_base_id(request, query)
    _authorize_rag_use(request, db, current_user, organization_id, knowledge_base_id)
    metadata_filter = normalize_metadata_filter(
        metadata_filter=query.metadata_filter,
        classification_filter=query.classification_filter,
        tags=query.tags,
        source_type=query.source_type,
        effective_at=query.effective_at,
    )
    retrieval_service = RetrievalService(
        db,
        user_id=current_user.id,
        organization_id=organization_id,
    )
    try:
        response = await retrieval_service.generate_answer_for_test(
            query.query,
            knowledge_base_id=str(knowledge_base_id),
            model_id=query.generation_model or "gpt-4o",
            top_k=query.top_k or 5,
            metadata_filter=metadata_filter,
            hierarchy_mode=query.hierarchy_mode,
        )
    except ValueError as exc:
        if str(exc) == "hierarchy_unavailable":
            raise_api_error(
                request,
                422,
                "hierarchy_unavailable",
                "Hierarchical retrieval data is not available for this Knowledge Base.",
            )
        raise
    _record_rag_retrieve_audit(
        request,
        current_user,
        organization_id,
        knowledge_base_id,
        metadata_filter,
        len(response.references),
        query.hierarchy_mode,
    )
    return response


@router.post("/search-test/pure", response_model=List[ChunkPreview])
async def search_test_pure(
    query: SearchQuery,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Search Test] Pure Retrieval Mode
    순수 벡터 검색 (LLM 생성 없음)
    """
    organization_id = parse_organization_id(request, x_organization_id)
    knowledge_base_id = _require_search_knowledge_base_id(request, query)
    _authorize_rag_use(request, db, current_user, organization_id, knowledge_base_id)
    metadata_filter = normalize_metadata_filter(
        metadata_filter=query.metadata_filter,
        classification_filter=query.classification_filter,
        tags=query.tags,
        source_type=query.source_type,
        effective_at=query.effective_at,
    )
    retrieval_service = RetrievalService(
        db,
        user_id=current_user.id,
        organization_id=organization_id,
    )

    # RetrievalService.search_documents 직접 호출 (비동기)
    try:
        results = await retrieval_service.search_documents(
            query.query,
            knowledge_base_id=str(knowledge_base_id),
            top_k=query.top_k or 5,
            metadata_filter=metadata_filter,
            hierarchy_mode=query.hierarchy_mode,
        )
    except ValueError as exc:
        if str(exc) == "hierarchy_unavailable":
            raise_api_error(
                request,
                422,
                "hierarchy_unavailable",
                "Hierarchical retrieval data is not available for this Knowledge Base.",
            )
        raise
    _record_rag_retrieve_audit(
        request,
        current_user,
        organization_id,
        knowledge_base_id,
        metadata_filter,
        len(results),
        query.hierarchy_mode,
    )
    return results


@router.get("/document/{document_id}/progress")
async def get_document_progress(
    document_id: UUID,
    request: Request,
    organization_id_query: UUID = Query(..., alias="organizationId"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [SSE] 문서 처리 진행 상황을 실시간 스트리밍으로 반환합니다.
    """
    import asyncio
    import json

    from fastapi.responses import StreamingResponse

    from apps.shared.pubsub import get_redis_client

    _authorize_knowledge_document_action(
        request,
        db,
        current_user,
        organization_id_query,
        document_id,
        "read",
    )
    _ensure_document_ingestion_schema_ready(db, request)

    async def event_generator():
        while True:
            # 1. DB에서 문서 상태 조회 (Polling)
            db.expire_all()
            doc = db.query(Document).get(document_id)

            if not doc:
                yield 'data: {"error": "Document not found"}\n\n'
                break

            if finalize_stale_processing_start(
                db,
                document_id,
            ) or recover_timed_out_document_with_artifacts(db, document_id):
                db.refresh(doc)

            status = project_safe_document_status(doc.status)
            latest_job = build_read_document_ingestion_status(db).execute(document_id)

            # 2. Redis에서 실시간 진행률 조회 (에러 핸들링 포함)
            redis_progress = None
            try:
                redis_client = get_redis_client()
                redis_key = f"knowledge_progress:{document_id}"
                redis_progress = redis_client.get(redis_key)
            except Exception as e:
                logger.warning("Redis read failed for progress: %s", type(e).__name__)

            # 3. 저장된 raw step/error와 Redis payload를 외부 문자열로 사용하지 않음
            progress = project_safe_document_progress(status, redis_progress)
            step_message = project_safe_document_progress_message(status)

            # 4. 데이터 전송 포맷 (SSE 표준: "data: ...\n\n")
            data = json.dumps(
                {
                    "progress": progress,
                    "message": step_message,
                    "status": status,
                    "error": project_safe_document_error(
                        status,
                        doc.error_message,
                    ),
                    "ingestion_job": project_safe_ingestion_job(latest_job),
                },
                ensure_ascii=False,
            )

            yield f"data: {data}\n\n"

            # 5. 종료 조건
            if status in {"completed", "failed"} or progress >= 100:
                break
            if status == "failed":
                break

            # 1초 대기 (서버 부하 방지)
            await asyncio.sleep(1)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/proxy/preview")
async def proxy_api_preview(
    request: ApiPreviewRequest,
    current_user: User = Depends(get_current_user),
):
    """
    프론트엔드 CORS 문제 해결을 위한 API 프록시 엔드포인트.
    Knowledge/RAG outbound guard를 거치지 않는 raw HTTP client는 사용하지 않는다.
    """
    import asyncio

    # 기본 헤더가 없으면 추가
    headers = request.headers or {}
    if "User-Agent" not in headers:
        headers["User-Agent"] = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        )
    method = str(request.method or "GET").upper()
    if method not in {"GET", "POST"}:
        raise HTTPException(
            status_code=400,
            detail={"reason_code": "egress.unsupported_method"},
        )

    try:
        response = await asyncio.to_thread(
            safe_http_request,
            method,
            request.url,
            headers=headers,
            json_body=request.body,
            operation_id=KNOWLEDGE_API_FETCH,
        )
        if response.status_code >= 400:
            raise HTTPException(
                status_code=response.status_code,
                detail={"reason_code": "egress.upstream_error"},
            )

        try:
            data = response.json()
        except Exception:
            data = response.text

        return {
            "status": response.status_code,
            "data": data,
            "headers": response.headers,
        }

    except EgressGuardError as e:
        logger.warning("[Proxy Log] Egress guard denied request: %s", e.reason_code)
        status_code = 504 if e.reason_code == "egress.timeout" else 400
        raise HTTPException(
            status_code=status_code,
            detail={"reason_code": e.reason_code},
        )

    except HTTPException:
        raise

    except Exception as e:
        logger.error("[Proxy Log] 기타 오류 발생: %s", type(e).__name__)
        raise HTTPException(
            status_code=500,
            detail={"reason_code": "egress.proxy_failed"},
        )


def _get_or_create_knowledge_base(
    request: Request,
    db: Session,
    user: User,
    organization_id: UUID,
    kb_id: Optional[UUID],
    name: Optional[str],
    description: Optional[str],
    ai_model: Optional[str],
    top_k: int,
    similarity_threshold: float,
    file: Optional[UploadFile],
) -> tuple[UUID, str]:
    """자료를 조회하거나 새로 생성합니다."""
    if not kb_id:
        if not has_organization_scope_access(db, user.id, organization_id):
            raise_api_error(
                request,
                404,
                "resource.not_found",
                "Knowledge Base not found.",
            )
        if not ai_model:
            raise HTTPException(
                status_code=400,
                detail="Embedding model must be selected for new Knowledge Base",
            )

        # 이름 결정: 입력된 이름 -> (파일 있으면 파일명) -> "API Source"
        kb_name = name if name else (file.filename if file else "API Source")

        try:
            created = KnowledgeBaseQueryService(db).create(
                KnowledgeBaseCreate(
                    name=kb_name,
                    description=description,
                    embedding_model=ai_model,
                ),
                user_id=user.id,
                organization_id=organization_id,
                top_k=top_k,
                similarity_threshold=similarity_threshold,
            )
        except KnowledgeSchemaNotReady:
            raise_api_error(
                request,
                503,
                "knowledge.schema_not_ready",
                "Knowledge database schema is not ready for this operation.",
            )
        except KnowledgeValidationError as exc:
            raise_api_error(
                request,
                400,
                "knowledge.validation_failed",
                "Knowledge base request validation failed.",
                {"reason": exc.reason},
            )
        except KnowledgeBaseCreateFailed:
            raise_api_error(
                request,
                500,
                "knowledge.create_failed",
                "Knowledge base creation failed.",
            )
        return created.id, created.embedding_model

    else:
        kb = _load_writable_knowledge_base(
            request,
            db,
            user,
            organization_id,
            kb_id,
        )
        return kb.id, kb.embedding_model


def _load_writable_knowledge_base(
    request: Request,
    db: Session,
    user: User,
    organization_id: UUID,
    knowledge_base_id: UUID,
) -> KnowledgeBase:
    try:
        return KnowledgeAuthorizationService(
            db,
            user_id=user.id,
            organization_id=organization_id,
        ).load_kb(knowledge_base_id, "write")
    except KnowledgeResourceHidden:
        raise_api_error(
            request,
            404,
            "resource.hidden",
            "Knowledge Base not found.",
        )
    except KnowledgePermissionDenied:
        raise_api_error(
            request,
            403,
            "permission.denied",
            "Knowledge Base write permission is required.",
        )


def _ensure_initial_document_slot(
    request: Request,
    db: Session,
    *,
    knowledge_base_id: UUID,
    organization_id: UUID,
) -> None:
    try:
        KnowledgeDocumentRegistrationService(db).ensure_available(
            knowledge_base_id=knowledge_base_id,
            organization_id=organization_id,
        )
    except KnowledgeDocumentRegistrationError as exc:
        _raise_document_registration_error(request, exc)


def _raise_document_registration_error(
    request: Request,
    exc: KnowledgeDocumentRegistrationError,
) -> None:
    if isinstance(exc, KnowledgeDocumentRegistrationHidden):
        raise_api_error(
            request,
            404,
            "resource.hidden",
            "Knowledge Base not found.",
        )
    if isinstance(exc, KnowledgeDocumentSlotOccupied):
        raise_api_error(
            request,
            409,
            "knowledge.document_slot_occupied",
            "This Knowledge Base already has a source document.",
        )
    if isinstance(exc, KnowledgeDocumentRegistrationPolicyDenied):
        raise_api_error(
            request,
            409,
            "knowledge.document_registration_not_allowed",
            "This Knowledge Base does not accept manual source registration.",
        )
    if isinstance(exc, KnowledgeDocumentRegistrationUnavailable):
        raise_api_error(
            request,
            503,
            "knowledge.document_registration_unavailable",
            "Knowledge document registration is temporarily unavailable.",
        )
    raise_api_error(
        request,
        500,
        "knowledge.document_registration_failed",
        "Knowledge document registration failed.",
    )


def _cleanup_backend_upload(file_path: str) -> None:
    try:
        get_storage_service().delete(file_path)
    except Exception as exc:
        logger.warning(
            "Backend upload cleanup failed after document registration: %s",
            type(exc).__name__,
        )


def _is_backend_upload_cleanup_safe(
    exc: KnowledgeDocumentRegistrationError,
) -> bool:
    return not isinstance(
        exc,
        KnowledgeDocumentRegistrationUnavailable,
    ) or exc.artifact_cleanup_safe


def _validate_safe_document_filename(filename: str) -> str:
    safe_name = str(filename or "").replace("\\", "/").split("/")[-1]
    ext = "." + safe_name.rsplit(".", 1)[-1].lower() if "." in safe_name else ""
    if not safe_name or ext not in SAFE_DOCUMENT_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail={"reason_code": "file_type.unsupported"},
        )
    return safe_name


def _prepare_file_source(local_service: IngestionService, file: Optional[UploadFile]):
    """파일 자료 처리를 위한 데이터 준비"""
    if not file:
        raise HTTPException(status_code=400, detail="File is required for FILE source")

    filename = _validate_safe_document_filename(file.filename)
    file.filename = filename
    file_path = local_service.save_temp_file(file)
    return file_path, filename, {}


def _prepare_direct_upload_source(
    *,
    s3_file_key: str,
    user_id: UUID,
) -> tuple[str, str, dict]:
    key = str(s3_file_key or "").strip().replace("\\", "/")
    expected_prefix = f"uploads/{user_id}/"
    if (
        not key
        or key.startswith("/")
        or ".." in key.split("/")
        or not key.startswith(expected_prefix)
    ):
        raise HTTPException(
            status_code=400,
            detail={"reason_code": "storage.invalid_object_key"},
        )

    filename = _validate_safe_document_filename(key.rsplit("/", 1)[-1])
    if not settings.S3_BUCKET_NAME or not settings.AWS_REGION:
        raise HTTPException(
            status_code=400,
            detail={"reason_code": "storage.s3_not_configured"},
        )

    encoded_key = quote(key, safe="/")
    file_path = (
        f"https://{settings.S3_BUCKET_NAME}.s3."
        f"{settings.AWS_REGION}.amazonaws.com/{encoded_key}"
    )
    return file_path, filename, {"s3_key": key, "upload_method": "direct"}


def _encrypt_source_config_value(value: str) -> str:
    from apps.shared.utils.encryption import encryption_manager as security_service

    try:
        return security_service.encrypt(value)
    except Exception as exc:
        logger.error("Failed to encrypt API source configuration: %s", type(exc).__name__)
        raise HTTPException(
            status_code=500,
            detail={"reason_code": "source_config.encryption_required"},
        ) from exc


def _prepare_api_source(
    api_url: Optional[str],
    api_method: str,
    api_headers: Optional[str],
    api_body: Optional[str],
):
    """API 자료 처리를 위한 데이터 준비"""
    if not api_url:
        raise HTTPException(status_code=400, detail="API URL is required")

    import json

    # 헤더 처리 (JSON 파싱 및 암호화)
    encrypted_headers = None
    if api_headers:
        try:
            json.loads(api_headers)  # 유효성 검증
        except Exception as e:
            logger.warning("Failed to process API source headers: %s", type(e).__name__)
            raise HTTPException(
                status_code=400,
                detail={"reason_code": "validation.failed"},
            ) from e
        encrypted_headers = _encrypt_source_config_value(api_headers)

    # URL query와 body에는 token/secret이 들어갈 수 있으므로 원문을 durable metadata에 저장하지 않는다.
    encrypted_url = _encrypt_source_config_value(api_url)
    encrypted_body = None
    if api_body:
        try:
            json.loads(api_body)
        except Exception as e:
            logger.warning("Failed to process API source body: %s", type(e).__name__)
            raise HTTPException(
                status_code=400,
                detail={"reason_code": "validation.failed"},
            ) from e
        encrypted_body = _encrypt_source_config_value(api_body)

    method = str(api_method or "GET").upper()
    if method not in {"GET", "POST"}:
        raise HTTPException(
            status_code=400,
            detail={"reason_code": "egress.unsupported_method"},
        )

    meta_info = {
        "api_config": {
            "url_encrypted": encrypted_url,
            "method": method,
            "headers_encrypted": encrypted_headers,
            "body_encrypted": encrypted_body,
            "safe_label": "API source",
        }
    }

    return None, "API source", meta_info
