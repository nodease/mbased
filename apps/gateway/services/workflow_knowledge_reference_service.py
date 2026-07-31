"""Editable Workflow graph Knowledge reference authorization boundary."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from apps.shared.db.models.knowledge import (
    KnowledgeBase,
    KnowledgeCollection,
)
from apps.shared.domain.workflow_knowledge_references import (
    WorkflowNodeKnowledgeReferences,
    aggregate_workflow_knowledge_reference_ids,
    parse_workflow_knowledge_references,
)
from apps.shared.services.knowledge_permission_service import KnowledgePermissionHelper
from apps.shared.services.knowledge_resource_eligibility import (
    knowledge_base_operational_predicates,
    knowledge_collection_operational_predicates,
    retrieval_visible_chunk_exists,
)


@dataclass(frozen=True, slots=True)
class WorkflowKnowledgeReferenceUnavailable(Exception):
    """Generic resource-policy failure without a resource identifier."""

    field_path: str
    reason_code: str = "knowledge_reference_unavailable"


class WorkflowKnowledgeReferenceAuthorizationUnavailable(RuntimeError):
    """Sanitized retryable database/authorization infrastructure failure."""


class WorkflowKnowledgeReferenceService:
    """Validate graph intent and current editor authorization before a write.

    Collection children are deliberately not loaded here.  Invocation-time
    child KB/source authorization belongs to the MBA-232 runtime resolver.
    """

    def __init__(
        self,
        db: Session,
        *,
        user_id: uuid.UUID,
        organization_id: uuid.UUID,
    ) -> None:
        self.db = db
        self.user_id = user_id
        self.organization_id = organization_id
        self.permission_helper = KnowledgePermissionHelper(
            db,
            user_id=user_id,
            organization_id=organization_id,
        )

    def validate_editable_graph(
        self,
        graph: dict,
        *,
        require_retrieval_ready: bool = True,
    ) -> tuple[WorkflowNodeKnowledgeReferences, ...]:
        parsed_nodes = parse_workflow_knowledge_references(graph)
        self.validate_parsed_references(
            parsed_nodes,
            require_retrieval_ready=require_retrieval_ready,
        )
        return parsed_nodes

    def validate_parsed_references(
        self,
        parsed_nodes: tuple[WorkflowNodeKnowledgeReferences, ...],
        *,
        require_retrieval_ready: bool = True,
    ) -> None:
        """Authorize one structurally validated graph reference snapshot."""

        direct_ids, collection_ids = aggregate_workflow_knowledge_reference_ids(
            parsed_nodes
        )
        if not direct_ids and not collection_ids:
            return

        try:
            direct_kbs = self._load_direct_kbs(direct_ids)
            collections = self._load_collections(collection_ids)
            direct_decisions = self.permission_helper.bulk_evaluate_kb_use(direct_kbs)
            collection_decisions = (
                self.permission_helper.bulk_evaluate_collection_action(
                    collections,
                    "route",
                )
            )
            retrieval_ready_ids = self._retrieval_ready_ids(direct_ids)
        except SQLAlchemyError as exc:
            raise WorkflowKnowledgeReferenceAuthorizationUnavailable(
                "knowledge_reference_authorization_unavailable"
            ) from exc

        direct_by_id = {kb.id: kb for kb in direct_kbs}
        for direct_id in direct_ids:
            decision = direct_decisions.get(direct_id)
            if (
                direct_id not in direct_by_id
                or decision is None
                or not decision.allowed
                or (
                    require_retrieval_ready
                    and direct_id not in retrieval_ready_ids
                )
            ):
                raise WorkflowKnowledgeReferenceUnavailable(
                    self._first_reference_path(
                        parsed_nodes,
                        reference_id=direct_id,
                        collection=False,
                    )
                )

        collection_by_id = {collection.id: collection for collection in collections}
        for collection_id in collection_ids:
            decision = collection_decisions.get(collection_id)
            if (
                collection_id not in collection_by_id
                or decision is None
                or not decision.allowed
            ):
                raise WorkflowKnowledgeReferenceUnavailable(
                    self._first_reference_path(
                        parsed_nodes,
                        reference_id=collection_id,
                        collection=True,
                    )
                )

    def _load_direct_kbs(
        self,
        direct_ids: tuple[uuid.UUID, ...],
    ) -> list[KnowledgeBase]:
        if not direct_ids:
            return []
        return (
            self.db.query(KnowledgeBase)
            .filter(
                KnowledgeBase.id.in_(direct_ids),
                KnowledgeBase.organization_id == self.organization_id,
                *knowledge_base_operational_predicates(),
            )
            .all()
        )

    def _load_collections(
        self,
        collection_ids: tuple[uuid.UUID, ...],
    ) -> list[KnowledgeCollection]:
        if not collection_ids:
            return []
        return (
            self.db.query(KnowledgeCollection)
            .filter(
                KnowledgeCollection.id.in_(collection_ids),
                KnowledgeCollection.organization_id == self.organization_id,
                *knowledge_collection_operational_predicates(),
            )
            .all()
        )

    def _retrieval_ready_ids(
        self,
        direct_ids: tuple[uuid.UUID, ...],
    ) -> set[uuid.UUID]:
        if not direct_ids:
            return set()
        rows = (
            self.db.query(KnowledgeBase.id)
            .filter(
                KnowledgeBase.id.in_(direct_ids),
                KnowledgeBase.organization_id == self.organization_id,
                *knowledge_base_operational_predicates(),
                retrieval_visible_chunk_exists(),
            )
            .all()
        )
        return {row[0] for row in rows}

    @staticmethod
    def _first_reference_path(
        parsed_nodes: tuple[WorkflowNodeKnowledgeReferences, ...],
        *,
        reference_id: uuid.UUID,
        collection: bool,
    ) -> str:
        for node in parsed_nodes:
            references = (
                node.collection_references if collection else node.direct_references
            )
            field_name = "knowledgeCollections" if collection else "knowledgeBases"
            for index, reference in enumerate(references):
                if reference.id == reference_id:
                    return f"{node.field_path}.{field_name}[{index}]"
        return "graph.knowledgeReferences"
