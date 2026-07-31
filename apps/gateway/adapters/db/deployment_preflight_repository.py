from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from apps.gateway.application.deployment.models import (
    KnowledgeBaseSnapshot,
    KnowledgeCollectionPreflightSnapshot,
    MailCredentialSnapshot,
    WorkflowNodeTargetSnapshot,
)
from apps.shared.db.models.app import App
from apps.shared.db.models.knowledge import (
    KnowledgeBase,
    KnowledgeCollection,
    KnowledgeCollectionItem,
)
from apps.shared.db.models.mail_credential import (
    MAIL_CREDENTIAL_ACTIVE,
    MailCredential,
)
from apps.shared.db.models.workflow_deployment import DeploymentType, WorkflowDeployment
from apps.shared.domain.knowledge_runtime_candidates import (
    MAX_RUNTIME_CANDIDATE_BUDGET,
)
from apps.shared.services.knowledge_resource_eligibility import (
    is_anonymous_public_knowledge_collection,
    knowledge_base_operational_predicates,
    knowledge_collection_anonymous_public_predicates,
    knowledge_collection_operational_predicates,
    retrieval_visible_chunk_exists,
)
from apps.shared.permissions import mail_credential_auth_state_allows
from apps.shared.services.permissions import (
    get_effective_mail_credential_auth_states,
)


class SqlAlchemyDeploymentPreflightRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_active_knowledge_bases(
        self,
        knowledge_base_ids: Iterable[uuid.UUID],
        organization_id: uuid.UUID | None,
    ) -> dict[uuid.UUID, KnowledgeBaseSnapshot]:
        ids = _dedupe_ids(knowledge_base_ids)
        if not ids or organization_id is None:
            return {}
        rows = (
            self.db.query(KnowledgeBase)
            .filter(
                KnowledgeBase.id.in_(ids),
                KnowledgeBase.organization_id == organization_id,
                *knowledge_base_operational_predicates(),
                retrieval_visible_chunk_exists(),
            )
            .all()
        )
        return {
            row.id: KnowledgeBaseSnapshot(
                id=row.id,
                source_managed=getattr(row, "source_identity_id", None) is not None,
            )
            for row in rows
        }

    def get_public_runtime_eligible_knowledge_base_ids(
        self,
        knowledge_base_ids: Iterable[uuid.UUID],
        organization_id: uuid.UUID | None,
    ) -> set[uuid.UUID]:
        ids = _dedupe_ids(knowledge_base_ids)
        if not ids or organization_id is None:
            return set()

        items = (
            self.db.query(KnowledgeCollectionItem)
            .join(
                KnowledgeBase,
                and_(
                    KnowledgeBase.id == KnowledgeCollectionItem.knowledge_base_id,
                    KnowledgeBase.organization_id
                    == KnowledgeCollectionItem.organization_id,
                ),
            )
            .filter(
                KnowledgeCollectionItem.organization_id == organization_id,
                KnowledgeCollectionItem.knowledge_base_id.in_(ids),
                KnowledgeBase.organization_id == organization_id,
                *knowledge_base_operational_predicates(),
                retrieval_visible_chunk_exists(),
            )
            .all()
        )
        collection_ids = _dedupe_ids(item.collection_id for item in items)
        if not collection_ids:
            return set()

        collections = (
            self.db.query(KnowledgeCollection)
            .filter(
                KnowledgeCollection.id.in_(collection_ids),
                KnowledgeCollection.organization_id == organization_id,
                *knowledge_collection_anonymous_public_predicates(),
            )
            .all()
        )
        public_collection_ids = {
            collection.id
            for collection in collections
            if is_anonymous_public_knowledge_collection(collection)
        }
        return {
            item.knowledge_base_id
            for item in items
            if item.collection_id in public_collection_ids
        }

    def get_active_knowledge_collections(
        self,
        collection_ids: Iterable[uuid.UUID],
        organization_id: uuid.UUID | None,
    ) -> dict[uuid.UUID, KnowledgeCollectionPreflightSnapshot]:
        ids = _dedupe_ids(collection_ids)
        if not ids or organization_id is None:
            return {}

        collections = (
            self.db.query(KnowledgeCollection)
            .filter(
                KnowledgeCollection.id.in_(ids),
                KnowledgeCollection.organization_id == organization_id,
                *knowledge_collection_operational_predicates(),
            )
            .all()
        )
        active_collection_ids = [collection.id for collection in collections]
        if not active_collection_ids:
            return {}

        aggregate_rows = (
            self.db.query(
                KnowledgeCollectionItem.collection_id,
                func.count(KnowledgeCollectionItem.knowledge_base_id),
                func.count(KnowledgeBase.source_identity_id),
            )
            .join(
                KnowledgeBase,
                and_(
                    KnowledgeBase.id
                    == KnowledgeCollectionItem.knowledge_base_id,
                    KnowledgeBase.organization_id
                    == KnowledgeCollectionItem.organization_id,
                ),
            )
            .filter(
                KnowledgeCollectionItem.organization_id == organization_id,
                KnowledgeCollectionItem.collection_id.in_(
                    active_collection_ids
                ),
                KnowledgeBase.organization_id == organization_id,
                *knowledge_base_operational_predicates(),
                retrieval_visible_chunk_exists(),
            )
            .group_by(KnowledgeCollectionItem.collection_id)
            .all()
        )
        aggregate_by_collection: dict[uuid.UUID, tuple[int, bool]] = {}
        for collection_id, member_count, source_managed_count in aggregate_rows:
            safe_member_count = min(
                max(int(member_count or 0), 0),
                MAX_RUNTIME_CANDIDATE_BUDGET + 1,
            )
            aggregate_by_collection[collection_id] = (
                safe_member_count,
                int(source_managed_count or 0) > 0,
            )

        result: dict[uuid.UUID, KnowledgeCollectionPreflightSnapshot] = {}
        for collection in collections:
            metadata = getattr(collection, "safe_metadata", None)
            safe_metadata = metadata if isinstance(metadata, dict) else {}
            member_count, has_source_managed_members = (
                aggregate_by_collection.get(collection.id, (0, False))
            )
            result[collection.id] = KnowledgeCollectionPreflightSnapshot(
                id=collection.id,
                public=safe_metadata.get("visibility") == "public",
                source_managed=(
                    getattr(collection, "source_identity_id", None) is not None
                ),
                has_source_managed_members=has_source_managed_members,
                candidate_member_count=member_count,
            )
        return result

    def get_workflow_node_target(
        self,
        app_id: uuid.UUID,
        organization_id: uuid.UUID | None,
    ) -> WorkflowNodeTargetSnapshot | None:
        query = self.db.query(App).filter(App.id == app_id)
        if organization_id is not None:
            query = query.filter(App.organization_id == organization_id)
        app = query.first()
        if app is None:
            return None

        deployment_id = _uuid_or_none(getattr(app, "active_deployment_id", None))
        if deployment_id is None:
            return WorkflowNodeTargetSnapshot(
                app_id=app.id,
                organization_id=app.organization_id,
                workflow_id=getattr(app, "workflow_id", None),
                deployment_id=None,
                deployment_version=None,
                deployment_type=None,
                active_graph_snapshot=None,
                active_pointer_valid=False,
            )

        deployment = (
            self.db.query(WorkflowDeployment)
            .filter(
                WorkflowDeployment.id == deployment_id,
                WorkflowDeployment.app_id == app.id,
                WorkflowDeployment.is_active.is_(True),
                WorkflowDeployment.type == DeploymentType.WORKFLOW_NODE,
            )
            .first()
        )
        return WorkflowNodeTargetSnapshot(
            app_id=app.id,
            organization_id=app.organization_id,
            workflow_id=getattr(app, "workflow_id", None),
            deployment_id=deployment.id if deployment is not None else None,
            deployment_version=(
                getattr(deployment, "version", None) if deployment is not None else None
            ),
            deployment_type=(deployment.type.value if deployment is not None else None),
            active_graph_snapshot=(
                deployment.graph_snapshot if deployment is not None else None
            ),
            active_pointer_valid=deployment is not None,
        )

    def get_workflow_node_deployment(
        self,
        app_id: uuid.UUID,
        deployment_id: uuid.UUID,
        organization_id: uuid.UUID | None,
    ) -> WorkflowNodeTargetSnapshot | None:
        query = self.db.query(App).filter(App.id == app_id)
        if organization_id is not None:
            query = query.filter(App.organization_id == organization_id)
        app = query.first()
        if app is None or app.active_deployment_id is None:
            return None
        active_exists = (
            self.db.query(WorkflowDeployment.id)
            .filter(
                WorkflowDeployment.id == app.active_deployment_id,
                WorkflowDeployment.app_id == app.id,
                WorkflowDeployment.is_active.is_(True),
                WorkflowDeployment.type == DeploymentType.WORKFLOW_NODE,
            )
            .first()
            is not None
        )
        deployment = (
            self.db.query(WorkflowDeployment)
            .filter(
                WorkflowDeployment.id == deployment_id,
                WorkflowDeployment.app_id == app.id,
                WorkflowDeployment.type == DeploymentType.WORKFLOW_NODE,
            )
            .first()
        )
        if deployment is None:
            return None
        return WorkflowNodeTargetSnapshot(
            app_id=app.id,
            organization_id=app.organization_id,
            workflow_id=getattr(app, "workflow_id", None),
            deployment_id=deployment.id,
            deployment_version=getattr(deployment, "version", None),
            deployment_type=deployment.type.value,
            active_graph_snapshot=deployment.graph_snapshot,
            active_pointer_valid=active_exists,
        )

    def get_mail_credential_snapshots(
        self,
        mail_credential_ids: Iterable[uuid.UUID],
        organization_id: uuid.UUID | None,
        principal_id: uuid.UUID | None,
    ) -> dict[uuid.UUID, MailCredentialSnapshot]:
        ids = _dedupe_ids(mail_credential_ids)
        if not ids or organization_id is None:
            return {}
        rows = (
            self.db.query(MailCredential)
            .filter(
                MailCredential.id.in_(ids),
                MailCredential.organization_id == organization_id,
                MailCredential.status == MAIL_CREDENTIAL_ACTIVE,
            )
            .all()
        )
        auth_states = (
            get_effective_mail_credential_auth_states(
                self.db,
                principal_id,
                (row.id for row in rows),
                organization_id,
            )
            if principal_id is not None
            else {}
        )
        return {
            row.id: MailCredentialSnapshot(
                provider=row.provider,
                auth_type=row.auth_type,
                usable_by_principal=mail_credential_auth_state_allows(
                    auth_states.get(row.id, "none"),
                    "use",
                ),
                effective_auth_state=auth_states.get(row.id, "none"),
            )
            for row in rows
        }


def _dedupe_ids(values: Iterable[uuid.UUID]) -> list[uuid.UUID]:
    result: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _uuid_or_none(value: object) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None
