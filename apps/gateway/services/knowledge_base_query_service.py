from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
from uuid import UUID

from sqlalchemy import and_, func, literal
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from apps.gateway.services.ingestion.service import (
    finalize_stale_processing_start,
    recover_timed_out_document_with_artifacts,
)
from apps.gateway.services.knowledge_document_projection import (
    project_safe_document_error,
    project_safe_document_metadata,
    project_safe_document_status,
)
from apps.shared.audit.manual_ownership import register_manual_audit_ownership
from apps.shared.db.models.audit_log import (
    ActorType,
    AuditCategory,
    AuditLog,
    AuditStatus,
)
from apps.shared.db.models.knowledge import (
    Document,
    DocumentChunk,
    DocumentVersion,
    KnowledgeBase,
)
from apps.shared.db.models.team import UserKnowledgePermission
from apps.shared.schemas.rag import (
    DocumentResponse,
    KnowledgeBaseCreate,
    KnowledgeBaseDetailResponse,
    KnowledgeBaseResponse,
)
from apps.shared.services.knowledge_permission_service import KnowledgePermissionHelper
from apps.shared.services.knowledge_resource_eligibility import (
    knowledge_base_operational_predicates,
    retrieval_visible_chunk_exists,
)
from apps.shared.services.knowledge_schema_readiness import (
    check_knowledge_schema_readiness,
    table_has_column,
)

logger = logging.getLogger(__name__)

DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
KNOWLEDGE_BASE_NAME_MAX_LENGTH = 255
EMBEDDING_MODEL_MAX_LENGTH = 128
SAFE_EMBEDDING_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
UNSAFE_EMBEDDING_MODEL_RE = re.compile(
    r"(api[_-]?key|token|secret|password|credential|authorization|sk-)",
    re.IGNORECASE,
)
KNOWLEDGE_BASE_MUTATION_COLUMNS = {
    "knowledge_bases": {
        "organization_id",
        "active_document_version_id",
        "source_identity_id",
        "sync_state",
        "lifecycle_state",
        "safe_metadata",
    }
}

ColumnExistsFn = Callable[[Session, str, str], bool]
DocumentRecoveryFn = Callable[[Session, UUID], bool]
PermissionHelperFactory = Callable[..., KnowledgePermissionHelper]


class KnowledgeBaseQueryServiceError(Exception):
    """Base exception for Knowledge Base query service failures."""


class KnowledgeSchemaNotReady(KnowledgeBaseQueryServiceError):
    def __init__(
        self,
        missing_columns: dict[str, list[str]],
        *,
        reason: str | None = None,
    ):
        self.missing_columns = missing_columns
        self.reason = reason
        super().__init__("Knowledge database schema is not ready.")


class KnowledgeBaseCreateFailed(KnowledgeBaseQueryServiceError):
    pass


class KnowledgeBaseNotFound(KnowledgeBaseQueryServiceError):
    pass


class KnowledgeBaseHiddenOrForbidden(KnowledgeBaseNotFound):
    pass


class KnowledgeValidationError(KnowledgeBaseQueryServiceError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__("Knowledge base request validation failed.")


class KnowledgeConflict(KnowledgeBaseQueryServiceError):
    pass


@dataclass(frozen=True)
class LLMRAGSelectability:
    state: str
    safe_reason_code: str
    completed_document_count: int
    document_count: int

    @property
    def available(self) -> bool:
        return self.state == "available"


def _clean_source_types(source_types) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    if isinstance(source_types, str):
        stripped = source_types.strip("{}")
        source_types = [
            value.strip().strip('"').strip("'")
            for value in stripped.split(",")
            if value.strip()
        ]
    for source_type in source_types or []:
        if source_type is None:
            continue
        value = getattr(source_type, "value", source_type)
        if value is None:
            continue
        value = str(value).strip().strip('"').strip("'")
        if not value or value in seen:
            continue
        seen.add(value)
        cleaned.append(value)
    return cleaned


def _clean_source_type(source_type) -> str:
    value = getattr(source_type, "value", source_type)
    if value is None:
        return "FILE"
    return str(value)


def _safe_metadata_dict(safe_metadata) -> dict:
    if isinstance(safe_metadata, dict):
        return safe_metadata
    return {}


def _max_datetime_or_now(*values):
    candidates = [value for value in values if value is not None]
    if candidates:
        return max(candidates)
    return datetime.now(timezone.utc)


def evaluate_llm_rag_selectability(
    detail: KnowledgeBaseDetailResponse,
) -> LLMRAGSelectability:
    completed_count = sum(
        1
        for doc in detail.documents
        if doc.status == "completed" and (doc.chunk_count or 0) > 0
    )
    if completed_count > 0:
        return LLMRAGSelectability(
            state="available",
            safe_reason_code="completed_document_available",
            completed_document_count=completed_count,
            document_count=len(detail.documents),
        )
    has_completed_documents = any(doc.status == "completed" for doc in detail.documents)
    return LLMRAGSelectability(
        state="not_ready",
        safe_reason_code=(
            "no_documents"
            if not detail.documents
            else (
                "no_completed_document_chunks"
                if has_completed_documents
                else "no_completed_documents"
            )
        ),
        completed_document_count=0,
        document_count=len(detail.documents),
    )


def _validate_create_input(kb_in: KnowledgeBaseCreate) -> tuple[str, str | None, str]:
    name = kb_in.name.strip()
    if not name:
        raise KnowledgeValidationError("name_required")
    if len(name) > KNOWLEDGE_BASE_NAME_MAX_LENGTH:
        raise KnowledgeValidationError("name_too_long")
    embedding_model = (
        DEFAULT_EMBEDDING_MODEL
        if kb_in.embedding_model is None
        else kb_in.embedding_model.strip()
    )
    if (
        not embedding_model
        or len(embedding_model) > EMBEDDING_MODEL_MAX_LENGTH
        or not SAFE_EMBEDDING_MODEL_RE.fullmatch(embedding_model)
        or UNSAFE_EMBEDDING_MODEL_RE.search(embedding_model)
    ):
        raise KnowledgeValidationError("embedding_model_invalid")
    return name, kb_in.description, embedding_model


class KnowledgeBaseQueryService:
    def __init__(
        self,
        db: Session,
        *,
        column_exists: ColumnExistsFn = table_has_column,
        finalize_processing_start: DocumentRecoveryFn = finalize_stale_processing_start,
        recover_processing_timeout: DocumentRecoveryFn = (
            recover_timed_out_document_with_artifacts
        ),
        permission_helper_factory: PermissionHelperFactory = KnowledgePermissionHelper,
    ):
        self.db = db
        self._column_exists = column_exists
        self._finalize_processing_start = finalize_processing_start
        self._recover_processing_timeout = recover_processing_timeout
        self._permission_helper_factory = permission_helper_factory

    def has_column(self, table_name: str, column_name: str) -> bool:
        return self._column_exists(self.db, table_name, column_name)

    def ensure_schema_ready(self, required_columns: dict[str, set[str]]) -> None:
        result = check_knowledge_schema_readiness(self.db, required_columns)
        if not result.ready:
            raise KnowledgeSchemaNotReady(
                result.missing_columns,
                reason=result.reason,
            )

    def create(
        self,
        kb_in: KnowledgeBaseCreate,
        *,
        user_id: UUID,
        organization_id: UUID | None,
        schema_ready: bool = False,
        top_k: int | None = None,
        similarity_threshold: float | None = None,
    ) -> KnowledgeBaseResponse:
        if not schema_ready:
            self.ensure_schema_ready(KNOWLEDGE_BASE_MUTATION_COLUMNS)
        if organization_id is None:
            raise KnowledgeValidationError("organization_required")
        name, description, embedding_model = _validate_create_input(kb_in)
        kb = KnowledgeBase(
            id=uuid.uuid4(),
            name=name,
            description=description,
            embedding_model=embedding_model,
            organization_id=organization_id,
            user_id=user_id,
            **({"top_k": top_k} if top_k is not None else {}),
            **(
                {"similarity_threshold": similarity_threshold}
                if similarity_threshold is not None
                else {}
            ),
        )
        creator_permission = UserKnowledgePermission(
            id=uuid.uuid4(),
            grantee_organization_id=organization_id,
            user_id=user_id,
            knowledge_base_id=kb.id,
            auth_state="manager",
            assigned_by=user_id,
        )
        self.db.add(kb)
        self.db.add(creator_permission)
        register_manual_audit_ownership(self.db, kb, "created")
        register_manual_audit_ownership(self.db, creator_permission, "created")
        self.db.add(
            AuditLog(
                action="knowledge.created",
                category=AuditCategory.DATA_CHANGE,
                actor_id=user_id,
                actor_type=ActorType.USER,
                target_type="knowledge",
                target_id=str(kb.id),
                before=None,
                after={"organization_id": str(organization_id)},
                status=AuditStatus.SUCCESS,
                audit_metadata={"organization_id": str(organization_id)},
            )
        )
        self.db.add(
            AuditLog(
                action="user_knowledge_permission.created",
                category=AuditCategory.DATA_CHANGE,
                actor_id=user_id,
                actor_type=ActorType.USER,
                target_type="user_knowledge_permission",
                target_id=str(creator_permission.id),
                before=None,
                after={"auth_state": "manager"},
                status=AuditStatus.SUCCESS,
                audit_metadata={
                    "organization_id": str(organization_id),
                    "reason_code": "knowledge.creator_manager_bootstrap",
                },
            )
        )
        try:
            self.db.commit()
            self.db.refresh(kb)
        except SQLAlchemyError:
            self.db.rollback()
            logger.exception("knowledge.create.failed")
            raise KnowledgeBaseCreateFailed from None

        return KnowledgeBaseResponse(
            id=kb.id,
            organization_id=kb.organization_id,
            name=kb.name,
            description=kb.description,
            safe_metadata=_safe_metadata_dict(getattr(kb, "safe_metadata", None)),
            document_count=0,
            created_at=kb.created_at,
            updated_at=kb.updated_at,
            source_types=[],
            embedding_model=kb.embedding_model,
        )

    def list_authorized(
        self,
        *,
        user_id: UUID,
        organization_id: UUID,
        schema_ready: bool = False,
    ) -> list[KnowledgeBaseResponse]:
        """List active KBs whose safe metadata is readable by the caller."""

        if not schema_ready:
            self.ensure_schema_ready(KNOWLEDGE_BASE_MUTATION_COLUMNS)
        kbs = (
            self.db.query(KnowledgeBase)
            .filter(
                KnowledgeBase.organization_id == organization_id,
                KnowledgeBase.lifecycle_state == "active",
            )
            .order_by(KnowledgeBase.created_at.desc())
            .all()
        )
        if not kbs:
            return []
        helper = self._permission_helper_factory(
            self.db,
            user_id=user_id,
            organization_id=organization_id,
        )
        decisions = helper.bulk_evaluate_kb_action(kbs, "read")
        allowed = [
            kb
            for kb in kbs
            if decisions.get(kb.id) is not None and decisions[kb.id].allowed
        ]
        if not allowed:
            return []

        stats = self._document_stats_by_kb_id([kb.id for kb in allowed])
        responses = []
        for kb in allowed:
            document_count, last_updated_at, source_types = stats.get(
                kb.id, (0, None, [])
            )
            responses.append(
                KnowledgeBaseResponse(
                    id=kb.id,
                    organization_id=kb.organization_id,
                    name=kb.name,
                    description=kb.description,
                    safe_metadata=_safe_metadata_dict(kb.safe_metadata),
                    document_count=document_count,
                    created_at=kb.created_at,
                    updated_at=_max_datetime_or_now(
                        kb.updated_at, last_updated_at, kb.created_at
                    ),
                    source_types=_clean_source_types(source_types),
                    embedding_model=kb.embedding_model or DEFAULT_EMBEDDING_MODEL,
                )
            )
        return responses

    def _document_stats_by_kb_id(
        self,
        knowledge_base_ids: list[UUID],
    ) -> dict[UUID, tuple[int, datetime | None, list]]:
        rows = (
            self.db.query(
                Document.knowledge_base_id,
                func.count(Document.id),
                func.max(Document.updated_at),
                func.array_agg(Document.source_type),
            )
            .filter(Document.knowledge_base_id.in_(knowledge_base_ids))
            .group_by(Document.knowledge_base_id)
            .all()
        )
        return {
            kb_id: (int(count or 0), last_updated_at, source_types or [])
            for kb_id, count, last_updated_at, source_types in rows
        }

    def get_detail(
        self,
        kb_id: UUID,
        *,
        organization_scope: UUID | None,
        has_organization_id: bool,
        can_edit_settings: bool = True,
        can_manage_safe_metadata: bool = True,
        can_register_initial_document: bool = False,
        can_read: bool = True,
        can_use: bool = False,
        can_write: bool = False,
        can_read_content: bool = False,
        can_manage: bool = False,
    ) -> KnowledgeBaseDetailResponse:
        organization_id_column = (
            KnowledgeBase.organization_id
            if has_organization_id
            else literal(None).label("organization_id")
        )
        has_active_document_version_id = self.has_column(
            "knowledge_bases",
            "active_document_version_id",
        )
        has_safe_metadata = self.has_column("knowledge_bases", "safe_metadata")
        active_document_version_id_column = (
            KnowledgeBase.active_document_version_id
            if has_active_document_version_id
            else literal(None).label("active_document_version_id")
        )
        kb_select_columns = [
            KnowledgeBase.id,
            organization_id_column,
            KnowledgeBase.name,
            KnowledgeBase.description,
            KnowledgeBase.embedding_model,
            KnowledgeBase.created_at,
            KnowledgeBase.updated_at,
            active_document_version_id_column,
        ]
        if has_safe_metadata:
            kb_select_columns.append(KnowledgeBase.safe_metadata)

        kb_query = self.db.query(*kb_select_columns).select_from(KnowledgeBase).filter(
            KnowledgeBase.id == kb_id
        )
        if organization_scope is not None:
            kb_query = kb_query.filter(KnowledgeBase.organization_id == organization_scope)
        kb = kb_query.first()

        if not kb:
            raise KnowledgeBaseNotFound

        if has_safe_metadata and len(kb) == 9:
            (
                kb_id,
                organization_id,
                name,
                description,
                embedding_model,
                created_at,
                updated_at,
                active_document_version_id,
                safe_metadata,
            ) = kb
        else:
            (
                kb_id,
                organization_id,
                name,
                description,
                embedding_model,
                created_at,
                updated_at,
                active_document_version_id,
            ) = kb
            safe_metadata = {}

        doc_rows_query = (
            self.db.query(
                Document.id,
                Document.filename,
                Document.status,
                Document.created_at,
                Document.updated_at,
                Document.error_message,
                Document.source_type,
                Document.meta_info,
            )
            .select_from(Document)
            .filter(Document.knowledge_base_id == kb_id)
            .order_by(Document.created_at.asc())
        )
        doc_rows = doc_rows_query.all()
        if self._recover_document_rows(doc_rows):
            doc_rows = doc_rows_query.all()

        chunk_counts = self._retrieval_visible_chunk_counts(
            kb_id,
            active_document_version_id=active_document_version_id,
        )
        doc_responses = []
        for (
            document_id,
            filename,
            document_status,
            document_created_at,
            document_updated_at,
            error_message,
            source_type,
            meta_info,
        ) in doc_rows:
            doc_responses.append(
                DocumentResponse(
                    id=document_id,
                    filename=filename,
                    status=project_safe_document_status(document_status),
                    created_at=document_created_at or _max_datetime_or_now(),
                    updated_at=document_updated_at,
                    error_message=project_safe_document_error(
                        document_status,
                        error_message,
                    ),
                    chunk_count=chunk_counts.get(document_id, 0),
                    token_count=0,
                    source_type=_clean_source_type(source_type),
                    meta_info=project_safe_document_metadata(meta_info),
                )
            )

        return KnowledgeBaseDetailResponse(
            id=kb_id,
            organization_id=organization_id,
            name=name,
            description=description,
            safe_metadata=_safe_metadata_dict(safe_metadata),
            document_count=len(doc_responses),
            created_at=created_at or _max_datetime_or_now(updated_at),
            updated_at=updated_at or created_at,
            source_types=_clean_source_types([row[6] for row in doc_rows]),
            embedding_model=embedding_model or DEFAULT_EMBEDDING_MODEL,
            documents=doc_responses,
            can_edit_settings=can_edit_settings,
            can_manage_safe_metadata=can_manage_safe_metadata,
            can_register_initial_document=(
                can_register_initial_document and not doc_responses
            ),
            can_read=can_read,
            can_use=can_use,
            can_write=can_write,
            can_read_content=can_read_content,
            can_manage=can_manage,
        )

    def list_llm_selectable(
        self,
        *,
        user_id: UUID,
        organization_id: UUID,
        schema_ready: bool = False,
    ) -> list[KnowledgeBaseDetailResponse]:
        if not schema_ready:
            self.ensure_schema_ready(KNOWLEDGE_BASE_MUTATION_COLUMNS)
        kbs = (
            self.db.query(KnowledgeBase)
            .filter(
                KnowledgeBase.organization_id == organization_id,
                *knowledge_base_operational_predicates(),
                retrieval_visible_chunk_exists(),
            )
            .order_by(KnowledgeBase.created_at.desc())
            .all()
        )
        if not kbs:
            return []

        permission_helper = self._permission_helper_factory(
            self.db,
            user_id=user_id,
            organization_id=organization_id,
        )
        decisions = permission_helper.bulk_evaluate_kb_use(kbs)

        selectable: list[KnowledgeBaseDetailResponse] = []
        for kb in kbs:
            decision = decisions.get(kb.id)
            if decision is None or not decision.allowed:
                continue
            detail = self._detail_response_from_kb(kb)
            if evaluate_llm_rag_selectability(detail).available:
                selectable.append(detail)
        return selectable

    def _detail_response_from_kb(self, kb: KnowledgeBase) -> KnowledgeBaseDetailResponse:
        doc_rows_query = (
            self.db.query(
                Document.id,
                Document.filename,
                Document.status,
                Document.created_at,
                Document.updated_at,
                Document.error_message,
                Document.source_type,
                Document.meta_info,
            )
            .select_from(Document)
            .filter(Document.knowledge_base_id == kb.id)
            .order_by(Document.created_at.asc())
        )
        doc_rows = doc_rows_query.all()
        if self._recover_document_rows(doc_rows):
            doc_rows = doc_rows_query.all()

        chunk_counts = self._retrieval_visible_chunk_counts(
            kb.id,
            active_document_version_id=getattr(kb, "active_document_version_id", None),
        )
        doc_responses = []
        for (
            document_id,
            filename,
            document_status,
            document_created_at,
            document_updated_at,
            error_message,
            source_type,
            meta_info,
        ) in doc_rows:
            doc_responses.append(
                DocumentResponse(
                    id=document_id,
                    filename=filename,
                    status=project_safe_document_status(document_status),
                    created_at=document_created_at or _max_datetime_or_now(),
                    updated_at=document_updated_at,
                    error_message=project_safe_document_error(
                        document_status,
                        error_message,
                    ),
                    chunk_count=chunk_counts.get(document_id, 0),
                    token_count=0,
                    source_type=_clean_source_type(source_type),
                    meta_info=project_safe_document_metadata(meta_info),
                )
            )

        return KnowledgeBaseDetailResponse(
            id=kb.id,
            organization_id=getattr(kb, "organization_id", None),
            name=kb.name,
            description=kb.description,
            safe_metadata=_safe_metadata_dict(getattr(kb, "safe_metadata", None)),
            document_count=len(doc_responses),
            created_at=kb.created_at or _max_datetime_or_now(kb.updated_at),
            updated_at=kb.updated_at or kb.created_at,
            source_types=_clean_source_types([row[6] for row in doc_rows]),
            embedding_model=kb.embedding_model or DEFAULT_EMBEDDING_MODEL,
            documents=doc_responses,
        )

    def _recover_document_rows(self, doc_rows) -> bool:
        document_status_changed = False
        for document_id, *_ in doc_rows:
            if self._finalize_processing_start(
                self.db,
                document_id,
            ) or self._recover_processing_timeout(self.db, document_id):
                document_status_changed = True
        return document_status_changed

    def _retrieval_visible_chunk_counts(
        self,
        kb_id: UUID,
        *,
        active_document_version_id: UUID | None,
    ) -> dict[UUID, int]:
        if not self.has_column("document_chunks", "id"):
            return {}
        query = (
            self.db.query(
                DocumentChunk.document_id,
                func.count(DocumentChunk.id).label("chunk_count"),
            )
            .select_from(DocumentChunk)
            .join(Document, Document.id == DocumentChunk.document_id)
            .filter(
                DocumentChunk.knowledge_base_id == kb_id,
                Document.status == "completed",
            )
        )
        if not self.has_column("document_chunks", "document_version_id"):
            return {
                document_id: int(chunk_count or 0)
                for document_id, chunk_count in query.group_by(
                    DocumentChunk.document_id
                ).all()
            }
        if active_document_version_id is None:
            query = query.filter(DocumentChunk.document_version_id.is_(None))
        else:
            if not self.has_column("document_versions", "status"):
                return {}
            query = (
                query.outerjoin(
                    DocumentVersion,
                    DocumentChunk.document_version_id == DocumentVersion.id,
                )
                .filter(
                    and_(
                        DocumentChunk.document_version_id
                        == active_document_version_id,
                        DocumentVersion.status == "ready",
                    )
                )
            )
        return {
            document_id: int(chunk_count or 0)
            for document_id, chunk_count in (
                query.group_by(DocumentChunk.document_id).all()
            )
        }
