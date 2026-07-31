from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from apps.shared.db.models.knowledge import KnowledgeBase, KnowledgeCollection
from apps.shared.domain.knowledge_runtime_candidates import (
    KnowledgeRuntimeCandidateResolution,
)
from apps.shared.schemas.rag import ChunkPreview
from apps.shared.schemas.workflow_citation import (
    MAX_WORKFLOW_CITATIONS,
    MAX_WORKFLOW_CITATION_PREVIEW_LENGTH,
    MAX_WORKFLOW_CITATION_SECTION_LENGTH,
    CitationDisplayMode,
    WorkflowCitationEnvelope,
    WorkflowCitationItem,
)
from apps.shared.services.knowledge_safe_text import (
    safe_label_from_text,
    sanitize_safe_text,
)
from apps.shared.services.tracing.policy import TracePolicyService
from apps.shared.services.tracing.redaction import TraceRedactionService

logger = logging.getLogger(__name__)

GENERIC_DIRECT_CITATION_LABEL = "참조 문서"
GENERIC_COLLECTION_CITATION_LABEL = "지식 Collection 문서"


@dataclass(frozen=True, slots=True)
class PromptEvidence:
    knowledge_base_id: str
    chunk: ChunkPreview
    prompt_content: str


class WorkflowCitationProjector:
    """Authorized runtime candidates를 사용자 응답 전용 Citation으로 투영한다."""

    def __init__(
        self,
        *,
        db_session: Any,
        organization_id: uuid.UUID,
        resolution: KnowledgeRuntimeCandidateResolution,
    ) -> None:
        self._db_session = db_session
        self._organization_id = organization_id
        self._resolution = resolution
        self._candidate_by_id = {
            str(candidate.knowledge_base_id): candidate
            for candidate in resolution.candidates
        }
        self._label_by_kb_id = self._load_labels()

    def label_for(self, knowledge_base_id: str) -> str:
        candidate = self._candidate_by_id.get(str(knowledge_base_id))
        if candidate is None:
            return GENERIC_DIRECT_CITATION_LABEL
        fallback = (
            GENERIC_DIRECT_CITATION_LABEL
            if candidate.provenance.kind == "direct"
            else GENERIC_COLLECTION_CITATION_LABEL
        )
        return self._label_by_kb_id.get(str(knowledge_base_id), fallback)

    def project(
        self,
        evidence: list[PromptEvidence],
        *,
        mode: CitationDisplayMode,
    ) -> WorkflowCitationEnvelope:
        if mode == "hidden":
            return WorkflowCitationEnvelope()

        items: list[WorkflowCitationItem] = []
        seen: set[tuple[object, ...]] = set()
        for item in evidence:
            candidate = self._candidate_by_id.get(str(item.knowledge_base_id))
            if candidate is None:
                continue

            label = self.label_for(item.knowledge_base_id)
            page_number = self._positive_int_or_none(item.chunk.page_number)
            section = self._safe_section(
                item.chunk,
                collection_derived=(candidate.provenance.kind == "collection"),
            )
            preview = (
                self._safe_content_preview(item.prompt_content)
                if mode == "detailed"
                else None
            )
            fingerprint = (label, page_number, section, preview)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)

            rank = len(items) + 1
            items.append(
                WorkflowCitationItem(
                    citation_id=f"evidence-{rank}",
                    evidence_rank=rank,
                    label=label,
                    page_number=page_number,
                    section=section,
                    content_preview=preview,
                )
            )
            if len(items) >= MAX_WORKFLOW_CITATIONS:
                break

        return WorkflowCitationEnvelope(items=items)

    def _load_labels(self) -> dict[str, str]:
        direct_ids = [
            candidate.knowledge_base_id
            for candidate in self._resolution.candidates
            if candidate.provenance.kind == "direct"
        ]
        collection_ids = {
            candidate.provenance.collection_id
            for candidate in self._resolution.candidates
            if candidate.provenance.kind == "collection"
            and candidate.provenance.collection_id is not None
        }

        direct_labels = self._load_direct_labels(direct_ids)
        collection_labels = self._load_collection_labels(collection_ids)
        labels: dict[str, str] = {}
        for candidate in self._resolution.candidates:
            kb_id = str(candidate.knowledge_base_id)
            if candidate.provenance.kind == "direct":
                labels[kb_id] = direct_labels.get(
                    kb_id,
                    GENERIC_DIRECT_CITATION_LABEL,
                )
                continue
            labels[kb_id] = collection_labels.get(
                str(candidate.provenance.collection_id),
                GENERIC_COLLECTION_CITATION_LABEL,
            )
        return labels

    def _load_direct_labels(self, ids: list[uuid.UUID]) -> dict[str, str]:
        if not ids:
            return {}
        try:
            rows = (
                self._db_session.query(KnowledgeBase)
                .filter(
                    KnowledgeBase.organization_id == self._organization_id,
                    KnowledgeBase.id.in_(ids),
                    KnowledgeBase.lifecycle_state == "active",
                    KnowledgeBase.sync_state != "source_deleted",
                )
                .all()
            )
        except Exception as exc:
            logger.info(
                "Workflow Citation KB label lookup skipped: error_type=%s",
                type(exc).__name__,
            )
            return {}
        return self._project_resource_labels(
            rows,
            fallback=GENERIC_DIRECT_CITATION_LABEL,
            resource_kind="KB",
        )

    def _load_collection_labels(
        self,
        ids: set[uuid.UUID],
    ) -> dict[str, str]:
        if not ids:
            return {}
        try:
            rows = (
                self._db_session.query(KnowledgeCollection)
                .filter(
                    KnowledgeCollection.organization_id == self._organization_id,
                    KnowledgeCollection.id.in_(ids),
                    KnowledgeCollection.lifecycle_state == "active",
                    KnowledgeCollection.sync_state != "source_deleted",
                )
                .all()
            )
        except Exception as exc:
            logger.info(
                "Workflow Citation Collection label lookup skipped: error_type=%s",
                type(exc).__name__,
            )
            return {}
        return self._project_resource_labels(
            rows,
            fallback=GENERIC_COLLECTION_CITATION_LABEL,
            resource_kind="Collection",
        )

    @staticmethod
    def _project_resource_labels(
        rows: list[Any],
        *,
        fallback: str,
        resource_kind: str,
    ) -> dict[str, str]:
        labels: dict[str, str] = {}
        for row in rows:
            resource_id = str(row.id)
            try:
                labels[resource_id] = WorkflowCitationProjector._approved_resource_label(
                    row
                )
            except Exception as exc:
                logger.info(
                    "Workflow Citation %s label projection fallback: error_type=%s",
                    resource_kind,
                    type(exc).__name__,
                )
                labels[resource_id] = fallback
        return labels

    @staticmethod
    def _approved_resource_label(resource: Any) -> str:
        source_identity_id = getattr(resource, "source_identity_id", None)
        if source_identity_id is not None:
            source_identity = getattr(resource, "source_identity", None)
            if (
                source_identity is not None
                and getattr(source_identity, "is_active", False)
                and getattr(source_identity, "display_policy_state", None) == "approved"
            ):
                approved_label = safe_label_from_text(
                    getattr(source_identity, "safe_display_name", None)
                )
                if approved_label:
                    return approved_label
            return (
                GENERIC_COLLECTION_CITATION_LABEL
                if isinstance(resource, KnowledgeCollection)
                else GENERIC_DIRECT_CITATION_LABEL
            )

        safe_metadata = getattr(resource, "safe_metadata", None)
        safe_label = safe_label_from_text(
            safe_metadata.get("safe_label") if isinstance(safe_metadata, dict) else None
        )
        if safe_label:
            return safe_label
        return (
            GENERIC_COLLECTION_CITATION_LABEL
            if isinstance(resource, KnowledgeCollection)
            else GENERIC_DIRECT_CITATION_LABEL
        )

    @staticmethod
    def _safe_section(
        chunk: ChunkPreview,
        *,
        collection_derived: bool,
    ) -> str | None:
        if collection_derived:
            return None
        metadata = chunk.metadata_summary
        section_value = None
        if isinstance(metadata, dict):
            for key in ("section", "heading"):
                value = metadata.get(key)
                if isinstance(value, str) and value.strip():
                    section_value = value
                    break
        if section_value is None and isinstance(chunk.hierarchy_path, list):
            section_value = next(
                (
                    value
                    for value in reversed(chunk.hierarchy_path)
                    if isinstance(value, str) and value.strip()
                ),
                None,
            )
        return sanitize_safe_text(
            section_value,
            max_length=MAX_WORKFLOW_CITATION_SECTION_LENGTH,
        )

    @staticmethod
    def _safe_content_preview(value: str) -> str | None:
        """Use the common fail-closed redaction policy before a user-visible preview."""
        result = TraceRedactionService.redact_payload(
            value,
            TracePolicyService.fail_closed_redaction_policy(),
            payload_kind="workflow_citation_preview",
        )
        if (
            result.failed
            or result.pii_detected
            or result.secret_detected
            or not isinstance(result.redacted_payload, str)
        ):
            return None
        return sanitize_safe_text(
            result.redacted_payload,
            max_length=MAX_WORKFLOW_CITATION_PREVIEW_LENGTH,
        )

    @staticmethod
    def _positive_int_or_none(value: object) -> int | None:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            return None
        return value
