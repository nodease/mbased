import uuid
from typing import Any

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session, aliased, joinedload

from apps.gateway.services.rag_agent_answer_constants import MAX_TOP_K, MIN_TOP_K
from apps.gateway.services.rag_agent_answer_lifecycle import RAGAgentAnswerLifecycle
from apps.gateway.services.rag_agent_answer_types import RAGAnswerResolvedContext
from apps.gateway.utils.api_errors import error_detail, raise_api_error
from apps.shared.db.models.knowledge import DocumentChunk, KnowledgeBase, RAGAnswerRun
from apps.shared.db.models.llm import (
    LLMCredential,
    LLMModel,
    LLMRelCredentialModel,
)
from apps.shared.db.models.user import User
from apps.shared.permissions import (
    llm_credential_auth_state_allows,
)
from apps.shared.schemas.rag import (
    CORRELATION_ID_PATTERN,
    CORRELATION_ID_SECRET_PATTERNS,
    RAGAgentAnswerRequest,
)
from apps.shared.services.permission_audit import record_resource_permission_denied
from apps.shared.services.permissions import (
    get_effective_llm_credential_auth_state,
    has_organization_scope_access,
)
from apps.shared.services.knowledge_permission_service import KnowledgePermissionHelper
from apps.shared.services.rag_filters import normalize_metadata_filter


class RAGAgentAnswerPreflightResolver:
    """Run 생성 전 visibility와 생성 후 permission preflight를 분리해 검증한다."""

    def __init__(
        self,
        db: Session,
        *,
        current_user: User,
        request: Request,
        organization_id: uuid.UUID,
        lifecycle: RAGAgentAnswerLifecycle,
    ) -> None:
        self.db = db
        self.current_user = current_user
        self.request = request
        self.organization_id = organization_id
        self.lifecycle = lifecycle

    def resolve_visible_context(
        self, payload: RAGAgentAnswerRequest
    ) -> RAGAnswerResolvedContext:
        self._validate_top_k(payload.top_k)
        correlation_id = self._validated_correlation_id(payload.correlation_id)
        metadata_filter = normalize_metadata_filter(
            metadata_filter=payload.metadata_filter,
            classification_filter=payload.classification_filter,
            tags=payload.tags,
            source_type=payload.source_type,
            effective_at=payload.effective_at,
        )

        kb = self._visible_knowledge_base(payload.knowledge_base_id)
        model = self._visible_generation_model(payload.generation_model_id)
        credential = self._visible_credential(payload.credential_id)
        self._ensure_generation_model(model)
        self._ensure_hierarchy_available(payload, kb)
        return RAGAnswerResolvedContext(
            payload=payload,
            correlation_id=correlation_id,
            metadata_filter=metadata_filter,
            kb=kb,
            model=model,
            credential=credential,
        )

    def enforce_post_create_preflight(
        self, run: RAGAnswerRun, resolved: RAGAnswerResolvedContext
    ) -> LLMModel | None:
        self._ensure_kb_use(run, resolved.kb)
        embedding_model = self._ensure_embedding_readiness(run, resolved.kb)
        self._ensure_credential_use_and_relation(
            run, resolved.credential, resolved.model
        )
        return embedding_model

    def _visible_knowledge_base(self, knowledge_base_id: uuid.UUID) -> KnowledgeBase:
        kb = (
            self.db.query(KnowledgeBase)
            .filter(KnowledgeBase.id == knowledge_base_id)
            .first()
        )
        if (
            kb is None
            or kb.organization_id != self.organization_id
            or not has_organization_scope_access(
                self.db, self.current_user.id, self.organization_id
            )
        ):
            self._raise_not_found("Knowledge Base not found.")
        return kb

    def _visible_generation_model(self, model_id: uuid.UUID) -> LLMModel:
        model = (
            self.db.query(LLMModel)
            .options(joinedload(LLMModel.provider))
            .filter(LLMModel.id == model_id)
            .first()
        )
        if model is None or not model.is_active:
            self._raise_not_found("Generation model not found.")
        return model

    def _visible_credential(self, credential_id: uuid.UUID) -> LLMCredential:
        credential = (
            self.db.query(LLMCredential)
            .options(joinedload(LLMCredential.provider))
            .filter(LLMCredential.id == credential_id)
            .first()
        )
        if (
            credential is None
            or not credential.is_valid
            or credential.organization_id != self.organization_id
            or not has_organization_scope_access(
                self.db, self.current_user.id, self.organization_id
            )
        ):
            self._raise_not_found("Credential not found.")
        return credential

    def _ensure_generation_model(self, model: LLMModel) -> None:
        if model.type != "chat":
            raise_api_error(
                self.request,
                400,
                "validation.failed",
                "generation_model_id must reference an active chat model.",
                {"field": "generation_model_id"},
            )

    def _ensure_hierarchy_available(
        self, payload: RAGAgentAnswerRequest, kb: KnowledgeBase
    ) -> None:
        if payload.hierarchy_mode != "parent_child":
            return
        parent = aliased(DocumentChunk)
        exists = (
            self.db.query(DocumentChunk.id)
            .join(parent, DocumentChunk.parent_chunk_id == parent.id)
            .filter(
                DocumentChunk.knowledge_base_id == kb.id,
                DocumentChunk.chunk_level == "child",
                parent.chunk_level == "parent",
                DocumentChunk.document_id == parent.document_id,
                DocumentChunk.knowledge_base_id == parent.knowledge_base_id,
            )
            .first()
        )
        if exists is None:
            raise_api_error(
                self.request,
                422,
                "hierarchy_unavailable",
                "Hierarchical retrieval data is not available for this Knowledge Base.",
            )

    def _ensure_kb_use(self, run: RAGAnswerRun, kb: KnowledgeBase) -> None:
        decision = KnowledgePermissionHelper(
            self.db,
            user_id=self.current_user.id,
            organization_id=self.organization_id,
        ).evaluate_kb_use(kb)
        if decision.allowed:
            return

        reason_code = decision.reason_code or "kb_use_denied"
        if decision.external_reason_code == "resource.hidden":
            self.lifecycle.block_permission(run, reason_code)
            raise_api_error(
                self.request,
                404,
                "resource.hidden",
                "Knowledge Base not found.",
            )

        record_resource_permission_denied(
            user_id=self.current_user.id,
            resource_type="knowledge_base",
            resource_id=kb.id,
            action="use",
            effective_auth_state=decision.effective_auth_state,
            organization_id=self.organization_id,
            metadata=self._permission_metadata(
                run,
                reason_code=reason_code,
            ),
        )
        self._raise_permission_block(run, reason_code)

    def _ensure_embedding_readiness(
        self, run: RAGAnswerRun, kb: KnowledgeBase
    ) -> LLMModel | None:
        if not hasattr(kb, "embedding_model"):
            return None

        embedding_model_id = getattr(kb, "embedding_model", None)
        if not embedding_model_id:
            self._fail_preflight_configuration(
                run,
                "kb_embedding_model_unavailable",
                {"knowledge_base_id": str(kb.id)},
            )

        embedding_model = self._active_embedding_model(embedding_model_id)
        if embedding_model is None:
            self._fail_preflight_configuration(
                run,
                "kb_embedding_model_unavailable",
                {
                    "knowledge_base_id": str(kb.id),
                    "embedding_model": str(embedding_model_id),
                },
            )

        credentials = self._verified_embedding_credentials(embedding_model)
        if not credentials:
            self._fail_preflight_configuration(
                run,
                "embedding_credential_unavailable",
                {
                    "knowledge_base_id": str(kb.id),
                    "embedding_model": str(embedding_model_id),
                },
            )

        last_auth_state = "none"
        for credential in credentials:
            effective_auth_state = get_effective_llm_credential_auth_state(
                self.db,
                self.current_user.id,
                credential.id,
                organization_id=self.organization_id,
            )
            if llm_credential_auth_state_allows(effective_auth_state, "use"):
                return embedding_model
            last_auth_state = effective_auth_state

        denied_resource_type = "llm_model"
        denied_resource_id = embedding_model.id
        if len(credentials) == 1:
            denied_resource_type = "llm_credential"
            denied_resource_id = credentials[0].id
        record_resource_permission_denied(
            user_id=self.current_user.id,
            resource_type=denied_resource_type,
            resource_id=denied_resource_id,
            action="use",
            effective_auth_state=last_auth_state,
            organization_id=self.organization_id,
            metadata=self._permission_metadata(
                run,
                reason_code="embedding_credential_use_denied",
                extra={
                    "knowledge_base_id": str(kb.id),
                    "embedding_model": str(embedding_model_id),
                    "candidate_count": len(credentials),
                    "denied_credential_count": len(credentials),
                },
            ),
        )
        self._raise_permission_block(run, "embedding_credential_use_denied")
        return None

    def _active_embedding_model(self, embedding_model_id: str) -> LLMModel | None:
        return (
            self.db.query(LLMModel)
            .filter(
                LLMModel.model_id_for_api_call == embedding_model_id,
                LLMModel.type == "embedding",
                LLMModel.is_active.is_(True),
            )
            .first()
        )

    def _verified_embedding_credentials(self, embedding_model: LLMModel):
        return (
            self.db.query(LLMCredential)
            .join(
                LLMRelCredentialModel,
                LLMRelCredentialModel.credential_id == LLMCredential.id,
            )
            .options(joinedload(LLMCredential.provider))
            .filter(
                LLMCredential.organization_id == self.organization_id,
                LLMCredential.is_valid.is_(True),
                LLMRelCredentialModel.model_id == embedding_model.id,
                LLMRelCredentialModel.is_verified.is_(True),
            )
            .order_by(
                LLMRelCredentialModel.priority.asc(),
                LLMCredential.credential_name.asc(),
            )
            .all()
        )

    def _ensure_credential_use_and_relation(
        self, run: RAGAnswerRun, credential: LLMCredential, model: LLMModel
    ) -> None:
        effective_auth_state = get_effective_llm_credential_auth_state(
            self.db,
            self.current_user.id,
            credential.id,
            organization_id=self.organization_id,
        )
        if not llm_credential_auth_state_allows(effective_auth_state, "use"):
            record_resource_permission_denied(
                user_id=self.current_user.id,
                resource_type="llm_credential",
                resource_id=credential.id,
                action="use",
                effective_auth_state=effective_auth_state,
                organization_id=self.organization_id,
                metadata=self._permission_metadata(
                    run,
                    reason_code="credential_use_denied",
                ),
            )
            self._raise_permission_block(run, "credential_use_denied")

        relation = (
            self.db.query(LLMRelCredentialModel)
            .filter(
                LLMRelCredentialModel.credential_id == credential.id,
                LLMRelCredentialModel.model_id == model.id,
                LLMRelCredentialModel.is_verified.is_(True),
            )
            .first()
        )
        if relation is not None:
            return

        record_resource_permission_denied(
            user_id=self.current_user.id,
            resource_type="llm_model",
            resource_id=model.id,
            action="use",
            effective_auth_state=effective_auth_state,
            organization_id=self.organization_id,
            metadata=self._permission_metadata(
                run,
                reason_code="credential_model_relation_denied",
                extra={"credential_id": str(credential.id)},
            ),
        )
        self._raise_permission_block(run, "credential_model_relation_denied")

    def _validate_top_k(self, top_k: int) -> None:
        if MIN_TOP_K <= top_k <= MAX_TOP_K:
            return
        raise_api_error(
            self.request,
            400,
            "validation.failed",
            f"top_k must be between {MIN_TOP_K} and {MAX_TOP_K}.",
            {"field": "top_k", "min": MIN_TOP_K, "max": MAX_TOP_K},
        )

    def _validated_correlation_id(self, value: str | None) -> str:
        if value is None:
            return str(uuid.uuid4())
        normalized = value.strip()
        if not CORRELATION_ID_PATTERN.fullmatch(normalized) or any(
            pattern.search(normalized)
            for pattern in CORRELATION_ID_SECRET_PATTERNS
        ):
            raise_api_error(
                self.request,
                400,
                "invalid_correlation_id",
                "correlation_id is invalid.",
                {"field": "correlation_id"},
            )
        return normalized

    def _fail_preflight_configuration(
        self, run: RAGAnswerRun, reason_code: str, details: dict[str, Any] | None = None
    ) -> None:
        self.lifecycle.fail(run, "generation.failed")
        raise_api_error(
            self.request,
            500,
            "generation.failed",
            "RAG answer preflight failed.",
            {
                "answer_run_id": str(run.id),
                "correlation_id": run.correlation_id,
                "reason_code": reason_code,
                **(details or {}),
            },
        )

    def _raise_permission_block(self, run: RAGAnswerRun, reason_code: str) -> None:
        self.lifecycle.block_permission(run, reason_code)
        exc = HTTPException(
            status_code=403,
            detail=error_detail(
                self.request,
                "permission.denied",
                "RAG answer permission denied.",
                self._blocked_details(run, reason_code),
            ),
        )
        setattr(exc, "audit_recorded", True)
        raise exc

    def _permission_metadata(
        self,
        run: RAGAnswerRun,
        *,
        reason_code: str,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "request_id": getattr(self.request.state, "request_id", None),
            "path": self.request.url.path,
            "answer_run_id": str(run.id),
            "correlation_id": run.correlation_id,
            "reason_code": reason_code,
            **(extra or {}),
        }

    @staticmethod
    def _blocked_details(run: RAGAnswerRun, reason_code: str) -> dict[str, str]:
        return {
            "answer_run_id": str(run.id),
            "correlation_id": run.correlation_id,
            "status": "blocked",
            "reason_code": reason_code,
        }

    def _raise_not_found(self, message: str) -> None:
        raise_api_error(self.request, 404, "resource.not_found", message)
