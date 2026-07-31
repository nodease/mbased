import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from apps.gateway.application.knowledge_administration.collection_operations import (
    MAX_REORDER_ITEMS,
    CollectionItemOrderSnapshot,
    compute_order_revision,
)
from apps.gateway.application.knowledge_administration.delegation_subjects import (
    DelegationSubjectPageInvalid,
    decode_subject_cursor,
    encode_subject_cursor,
    escape_like_prefix,
    normalize_subject_query,
    normalize_subject_type,
    validate_subject_page_size,
)
from apps.gateway.services.audit_records import add_action_audit, add_data_change_audit
from apps.shared.db.models.knowledge import (
    KnowledgeBase,
    KnowledgeCollection,
    KnowledgeCollectionItem,
)
from apps.shared.db.models.team import (
    Team,
    TeamKnowledgeCollectionPermission,
    TeamMembership,
    UserKnowledgeCollectionPermission,
)
from apps.shared.db.models.organization_membership import OrganizationMembership
from apps.shared.db.models.user import User
from apps.shared.permissions import knowledge_base_auth_state_allows
from apps.shared.schemas.knowledge import (
    KnowledgeCollectionCreateRequest,
    KnowledgeCollectionItemLinkRequest,
    KnowledgeCollectionItemResponse,
    KnowledgeCollectionItemsResponse,
    KnowledgeCollectionLinkCandidate,
    KnowledgeCollectionPermissionGrantRequest,
    KnowledgeCollectionPermissionBundleGrantRequest,
    KnowledgeCollectionPermissionBulkBundleRequest,
    KnowledgeCollectionPermissionBulkBundleResponse,
    KnowledgeCollectionPermissionResponse,
    KnowledgeCollectionResponse,
    KnowledgeCollectionUpdateRequest,
    KnowledgeCollectionVisibilityRequest,
    KnowledgeCollectionVisibilityResponse,
    KnowledgeDelegationSubject,
    KnowledgeDelegationSubjectsResponse,
)
from apps.shared.services.knowledge_permission_service import KnowledgePermissionHelper
from apps.shared.domain.knowledge_collection_sync import MAX_SYNC_TARGETS
from apps.shared.services.knowledge_collection_sync_targets import (
    scan_collection_sync_targets,
)
from apps.shared.services.knowledge_safe_text import (
    safe_label_from_text,
    sanitize_kb_safe_metadata,
)
from apps.shared.services.permissions import (
    get_effective_knowledge_domain_actions,
    get_effective_knowledge_base_auth_state,
    has_knowledge_base_permission,
    has_organization_manager_permission,
)
from apps.gateway.services.knowledge_collection_policy import (
    bucket_count,
    bulk_permission_count_bucket,
    collection_visibility,
    normalize_optional_text,
    normalize_required_text,
    safe_metadata_key_is_forbidden,
    sanitize_safe_metadata_value,
)

GENERIC_KB_LABEL = "Knowledge Base"
LINK_CANDIDATE_SCAN_LIMIT = 5000
COLLECTION_ROLE_BUNDLE_ACTIONS = {
    "viewer": ("read",),
    "workflow_router": ("read", "route"),
    "maintainer": ("read", "manage"),
    "sync_operator": ("read", "sync"),
}


@dataclass
class KnowledgeCollectionServiceError(Exception):
    status_code: int
    code: str
    message: str
    details: dict[str, Any] | None = None


class KnowledgeCollectionService:
    """Manual Knowledge Collection 관리 경계.

    Collection은 grouping/routing/ops 단위이고, 하위 KB content 권한을
    상속하지 않는다. 이 service는 Collection action과 KB manage/use 판정을
    분리해 UI/API가 권한 의미를 섞지 않도록 한다.
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
        self._domain_actions_cache: set[str] | None = None

    def list_collections(
        self,
        *,
        lifecycle_state: str = "active",
        visibility: str | None = None,
        system_managed: bool | None = None,
        limit: int = 100,
    ) -> list[KnowledgeCollectionResponse]:
        if lifecycle_state not in {"active", "archived", "deleted"}:
            raise KnowledgeCollectionServiceError(
                400,
                "validation.failed",
                "Invalid lifecycle_state.",
                {"field": "lifecycle_state"},
            )
        if visibility not in {None, "public", "private"}:
            raise KnowledgeCollectionServiceError(
                400,
                "validation.failed",
                "Invalid visibility.",
                {"field": "visibility"},
            )
        query = self.db.query(KnowledgeCollection).filter(
            KnowledgeCollection.organization_id == self.organization_id,
            KnowledgeCollection.lifecycle_state == lifecycle_state,
        )
        if system_managed is not None:
            query = query.filter(KnowledgeCollection.is_system_managed.is_(system_managed))

        collections = (
            query.order_by(KnowledgeCollection.created_at.desc())
            .all()
        )
        decisions = self.permission_helper.bulk_evaluate_collection_action(
            collections,
            "read",
            include_archived=True,
        )
        responses: list[KnowledgeCollectionResponse] = []
        for collection in collections:
            decision = decisions.get(collection.id)
            if (
                (not decision or not decision.allowed)
                and not self._has_safe_collection_admin_visibility()
            ):
                continue
            if visibility is not None and self._visibility(collection) != visibility:
                continue
            responses.append(self._collection_response(collection))
            if len(responses) >= min(max(limit, 1), 500):
                break
        return responses

    def management_capabilities(self) -> dict[str, bool]:
        can_manage_public_scope = self._is_org_manager()
        return {
            "can_create_collection": (
                can_manage_public_scope or self._has_domain_action("catalog_manage")
            ),
            "can_change_public_visibility": can_manage_public_scope,
        }

    def get_collection(self, collection_id: uuid.UUID) -> KnowledgeCollectionResponse:
        collection = self._collection_or_hidden(collection_id)
        if not self._has_safe_collection_admin_visibility():
            self._require_collection_action(collection, "read")
        return self._collection_response(collection)

    def create_collection(
        self,
        request: KnowledgeCollectionCreateRequest,
    ) -> KnowledgeCollectionResponse:
        self._require_org_manager_or_domain("catalog_manage")
        safe_metadata = self._sanitize_safe_metadata(request.safe_metadata)
        name = self._normalize_required_text(request.name, "name")
        collection = KnowledgeCollection(
            id=uuid.uuid4(),
            organization_id=self.organization_id,
            name=name,
            description=self._normalize_optional_text(request.description),
            safe_metadata=safe_metadata,
            created_by=self.user_id,
            is_system_managed=False,
            sync_state="manual",
            lifecycle_state="active",
        )
        self.db.add(collection)
        try:
            self._record_collection_audit("knowledge.collection.created", collection)
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise KnowledgeCollectionServiceError(
                409,
                "conflict",
                "Knowledge Collection name already exists.",
            ) from exc
        except Exception:
            self.db.rollback()
            raise
        self.db.refresh(collection)
        return self._collection_response(collection)

    def update_collection(
        self,
        collection_id: uuid.UUID,
        request: KnowledgeCollectionUpdateRequest,
    ) -> KnowledgeCollectionResponse:
        collection = self._collection_or_hidden(collection_id)
        self._require_collection_manage_or_catalog(collection)
        if collection.is_system_managed:
            raise KnowledgeCollectionServiceError(
                403,
                "policy.denied",
                "System-managed collections cannot be manually edited.",
            )
        if request.name is not None:
            collection.name = self._normalize_required_text(request.name, "name")
        if request.description is not None:
            collection.description = self._normalize_optional_text(request.description)
        if request.safe_metadata is not None:
            collection.safe_metadata = self._sanitize_safe_metadata(
                request.safe_metadata,
                preserve_visibility=collection.safe_metadata.get("visibility"),
            )
        collection.updated_at = self._now()
        try:
            self._record_collection_audit("knowledge.collection.updated", collection)
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise KnowledgeCollectionServiceError(
                409,
                "conflict",
                "Knowledge Collection name already exists.",
            ) from exc
        except Exception:
            self.db.rollback()
            raise
        self.db.refresh(collection)
        return self._collection_response(collection)

    def list_items(self, collection_id: uuid.UUID) -> list[KnowledgeCollectionItemResponse]:
        return self.list_items_response(collection_id).items

    def list_items_response(
        self, collection_id: uuid.UUID
    ) -> KnowledgeCollectionItemsResponse:
        collection = self._collection_or_hidden(collection_id)
        if not (
            self._visibility(collection) == "private"
            and self._has_domain_action("catalog_manage")
        ):
            self._require_collection_action(collection, "read")
        return self._items_response(collection.id, self._ordered_collection_items(collection.id))

    def list_items_management_response(
        self, collection_id: uuid.UUID
    ) -> KnowledgeCollectionItemsResponse:
        """Return the safe item projection after an authorized membership mutation.

        Collection actions are independent additive grants, so a caller may hold
        ``manage`` without ``read``. Rechecking the mutation authority avoids a
        committed write being reported as a failed API response while preserving
        the stricter ``collection.read`` contract of the standalone GET endpoint.
        """

        collection = self._collection_or_hidden(collection_id)
        self._require_collection_membership_candidate_access(collection)
        return self._items_response(
            collection.id,
            self._ordered_collection_items(collection.id),
        )

    def link_item(
        self,
        collection_id: uuid.UUID,
        request: KnowledgeCollectionItemLinkRequest,
    ) -> KnowledgeCollectionItemsResponse:
        collection = self._locked_collection_or_hidden(collection_id)
        kb = self._knowledge_base_or_hidden(request.knowledge_base_id)
        self._require_collection_membership_mutation(
            collection,
            kb=kb,
            acknowledged_public_runtime_exposure=(
                request.acknowledged_public_runtime_exposure
            ),
            adds_public_exposure=True,
        )
        if kb.lifecycle_state != "active":
            raise KnowledgeCollectionServiceError(
                404,
                "resource.hidden",
                "Resource not found.",
            )

        items = self._locked_collection_items(collection.id)
        existing = next(
            (item for item in items if item.knowledge_base_id == kb.id),
            None,
        )
        if existing is not None:
            self.db.rollback()
            return self.list_items_management_response(collection.id)

        self._normalize_item_ranks(items)

        item = KnowledgeCollectionItem(
            id=uuid.uuid4(),
            organization_id=self.organization_id,
            collection_id=collection.id,
            knowledge_base_id=kb.id,
            rank=len(items),
            safe_metadata={},
        )
        self.db.add(item)
        try:
            self._record_collection_audit(
                "knowledge.collection.item.linked",
                collection,
                metadata={"item_change": "linked"},
            )
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            existing = (
                self.db.query(KnowledgeCollectionItem)
                .filter(
                    KnowledgeCollectionItem.collection_id == collection.id,
                    KnowledgeCollectionItem.knowledge_base_id == kb.id,
                )
                .first()
            )
            if existing is not None:
                return self.list_items_management_response(collection.id)
            raise
        except Exception:
            self.db.rollback()
            raise
        return self.list_items_management_response(collection.id)

    def unlink_item(
        self,
        collection_id: uuid.UUID,
        item_id: uuid.UUID,
        *,
        acknowledged_public_runtime_exposure: bool = False,
    ) -> None:
        collection = self._locked_collection_or_hidden(collection_id)
        items = self._locked_collection_items(collection.id)
        item = next((row for row in items if row.id == item_id), None)
        if item is None:
            self.db.rollback()
            raise KnowledgeCollectionServiceError(
                404, "resource.hidden", "Resource not found."
            )
        kb = self._knowledge_base_or_hidden(item.knowledge_base_id)
        self._require_collection_membership_mutation(
            collection,
            kb=kb,
            acknowledged_public_runtime_exposure=(
                acknowledged_public_runtime_exposure
            ),
        )
        self.db.delete(item)
        self._normalize_item_ranks([row for row in items if row.id != item.id])
        self._record_collection_audit_and_commit(
            "knowledge.collection.item.unlinked",
            collection,
            metadata={"item_change": "unlinked"},
        )

    def list_link_candidates(
        self,
        collection_id: uuid.UUID,
        *,
        limit: int = 100,
    ) -> list[KnowledgeCollectionLinkCandidate]:
        collection = self._collection_or_hidden(collection_id)
        self._require_collection_membership_candidate_access(collection)
        linked_ids = self._linked_kb_ids(collection.id)
        requested_limit = min(max(limit, 1), 500)
        batch_size = min(max(requested_limit * 2, 100), 500)
        candidates: list[KnowledgeCollectionLinkCandidate] = []
        scanned = 0
        while len(candidates) < requested_limit and scanned < LINK_CANDIDATE_SCAN_LIMIT:
            page = self._link_candidate_kb_page(
                limit=min(batch_size, LINK_CANDIDATE_SCAN_LIMIT - scanned),
                offset=scanned,
            )
            if not page:
                break
            scanned += len(page)
            read_decisions = self.permission_helper.bulk_evaluate_kb_action(
                page,
                "read",
            )
            for kb in page:
                if kb.id in linked_ids or not self._kb_link_candidate_allowed(
                    collection,
                    kb,
                ):
                    continue
                read_decision = read_decisions.get(kb.id)
                candidates.append(
                    KnowledgeCollectionLinkCandidate(
                        knowledge_base_id=kb.id,
                        safe_label=self._kb_safe_label(
                            kb,
                            can_read_kb=bool(
                                read_decision and read_decision.allowed
                            ),
                        ),
                        disabled=False,
                        safe_reason_code=None,
                    )
                )
                if len(candidates) >= requested_limit:
                    break
            if len(page) < batch_size:
                break
        return candidates

    def _link_candidate_kb_page(self, *, limit: int, offset: int) -> list[KnowledgeBase]:
        return (
            self.db.query(KnowledgeBase)
            .filter(
                KnowledgeBase.organization_id == self.organization_id,
                KnowledgeBase.lifecycle_state == "active",
            )
            .order_by(KnowledgeBase.created_at.desc(), KnowledgeBase.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

    def _linked_kb_ids(self, collection_id: uuid.UUID) -> set[uuid.UUID]:
        return {
            row[0]
            for row in self.db.query(KnowledgeCollectionItem.knowledge_base_id)
            .filter(
                KnowledgeCollectionItem.organization_id == self.organization_id,
                KnowledgeCollectionItem.collection_id == collection_id,
            )
            .all()
        }

    def list_permissions(
        self,
        collection_id: uuid.UUID,
    ) -> list[KnowledgeCollectionPermissionResponse]:
        collection = self._collection_or_hidden(collection_id)
        self._require_collection_permission_authority(collection)
        team_rows = (
            self.db.query(TeamKnowledgeCollectionPermission)
            .filter(
                TeamKnowledgeCollectionPermission.grantee_organization_id
                == self.organization_id,
                TeamKnowledgeCollectionPermission.knowledge_collection_id
                == collection.id,
            )
            .all()
        )
        user_rows = (
            self.db.query(UserKnowledgeCollectionPermission)
            .filter(
                UserKnowledgeCollectionPermission.grantee_organization_id
                == self.organization_id,
                UserKnowledgeCollectionPermission.knowledge_collection_id
                == collection.id,
            )
            .all()
        )
        return [self._team_permission_response(row) for row in team_rows] + [
            self._user_permission_response(row) for row in user_rows
        ]

    def list_delegation_subjects(
        self,
        collection_id: uuid.UUID,
        *,
        subject_type: str | None,
        query: str | None = None,
        cursor: str | None = None,
        limit: int | str = 25,
    ) -> KnowledgeDelegationSubjectsResponse:
        collection = self._collection_or_hidden(collection_id)
        self._require_collection_permission_authority(collection)
        return self._delegation_subjects_response(
            subject_type=subject_type,
            query=query,
            cursor=cursor,
            limit=limit,
        )

    def list_domain_delegation_subjects(
        self,
        *,
        subject_type: str | None,
        query: str | None = None,
        cursor: str | None = None,
        limit: int | str = 25,
    ) -> KnowledgeDelegationSubjectsResponse:
        self._require_org_manager()
        return self._delegation_subjects_response(
            subject_type=subject_type,
            query=query,
            cursor=cursor,
            limit=limit,
        )

    def _delegation_subjects_response(
        self,
        *,
        subject_type: str | None,
        query: str | None,
        cursor: str | None,
        limit: int | str,
    ) -> KnowledgeDelegationSubjectsResponse:
        try:
            normalized_subject_type = normalize_subject_type(subject_type)
            normalized_query = normalize_subject_query(query)
            requested_limit = validate_subject_page_size(limit)
            decoded_cursor = decode_subject_cursor(
                cursor,
                subject_type=normalized_subject_type,
                query=normalized_query,
            )
        except DelegationSubjectPageInvalid as exc:
            raise KnowledgeCollectionServiceError(
                400,
                "validation.failed",
                "Delegation subject page request is invalid.",
            ) from exc

        if normalized_subject_type == "team":
            subject_query = self.db.query(Team).filter(
                Team.organization_id == self.organization_id,
                Team.is_active.is_(True),
            )
            if normalized_query:
                subject_query = subject_query.filter(
                    Team.name.ilike(
                        f"{escape_like_prefix(normalized_query)}%",
                        escape="\\",
                    )
                )
            if decoded_cursor is not None:
                subject_query = subject_query.filter(
                    Team.id > decoded_cursor.last_subject_id
                )
            rows = subject_query.order_by(Team.id.asc()).limit(requested_limit + 1).all()
        else:
            subject_query = (
                self.db.query(User)
                .join(
                    OrganizationMembership,
                    OrganizationMembership.user_id == User.id,
                )
                .filter(
                    OrganizationMembership.organization_id == self.organization_id,
                    OrganizationMembership.membership_state == "active",
                    User.deactivated_at.is_(None),
                )
            )
            if normalized_query:
                subject_query = subject_query.filter(
                    User.name.ilike(
                        f"{escape_like_prefix(normalized_query)}%",
                        escape="\\",
                    )
                )
            if decoded_cursor is not None:
                subject_query = subject_query.filter(
                    User.id > decoded_cursor.last_subject_id
                )
            rows = subject_query.order_by(User.id.asc()).limit(requested_limit + 1).all()

        has_more = len(rows) > requested_limit
        page = rows[:requested_limit]
        next_cursor = (
            encode_subject_cursor(
                subject_type=normalized_subject_type,
                query=normalized_query,
                last_subject_id=page[-1].id,
            )
            if has_more and page
            else None
        )
        return KnowledgeDelegationSubjectsResponse(
            subjects=[
                KnowledgeDelegationSubject(
                    subject_type=normalized_subject_type,
                    subject_id=subject.id,
                    subject_safe_label=(
                        safe_label_from_text(getattr(subject, "name", None))
                        or (
                            "Team"
                            if normalized_subject_type == "team"
                            else "User"
                        )
                    ),
                )
                for subject in page
            ],
            next_cursor=next_cursor,
        )

    def grant_permission(
        self,
        collection_id: uuid.UUID,
        request: KnowledgeCollectionPermissionGrantRequest,
    ) -> KnowledgeCollectionPermissionResponse:
        collection = self._locked_collection_or_hidden(collection_id)
        authority = self._require_collection_permission_authority(collection)
        self._block_collection_delegate_self_escalation(
            collection,
            request,
            authority=authority,
        )
        if request.subject_type == "team":
            row, created = self._grant_team_permission(collection, request)
            if not created:
                self.db.rollback()
                return self._team_permission_response(row)
            self._record_collection_audit_and_commit(
                "knowledge.collection.permission.granted",
                collection,
                metadata={
                    "subject_type": "team",
                    "permission_action": request.permission_action,
                },
            )
            self.db.refresh(row)
            return self._team_permission_response(row)
        row, created = self._grant_user_permission(collection, request)
        if not created:
            self.db.rollback()
            return self._user_permission_response(row)
        self._record_collection_audit_and_commit(
            "knowledge.collection.permission.granted",
            collection,
            metadata={
                "subject_type": "user",
                "permission_action": request.permission_action,
            },
        )
        self.db.refresh(row)
        return self._user_permission_response(row)

    def grant_permission_bundle(
        self,
        collection_id: uuid.UUID,
        request: KnowledgeCollectionPermissionBundleGrantRequest,
    ) -> list[KnowledgeCollectionPermissionResponse]:
        collection = self._locked_collection_or_hidden(collection_id)
        authority = self._require_collection_permission_authority(collection)
        actions = COLLECTION_ROLE_BUNDLE_ACTIONS[request.role_bundle]
        escalation_probe = KnowledgeCollectionPermissionGrantRequest(
            subject_type=request.subject_type,
            subject_id=request.subject_id,
            permission_action=actions[0],
        )
        self._block_collection_delegate_self_escalation(
            collection,
            escalation_probe,
            authority=authority,
        )

        rows: list[
            TeamKnowledgeCollectionPermission | UserKnowledgeCollectionPermission
        ] = []
        created_rows: list[
            TeamKnowledgeCollectionPermission | UserKnowledgeCollectionPermission
        ] = []
        for action in actions:
            grant = KnowledgeCollectionPermissionGrantRequest(
                subject_type=request.subject_type,
                subject_id=request.subject_id,
                permission_action=action,
            )
            if request.subject_type == "team":
                row, created = self._grant_team_permission(collection, grant)
            else:
                row, created = self._grant_user_permission(collection, grant)
            rows.append(row)
            if created:
                created_rows.append(row)

        if created_rows:
            try:
                self._record_collection_audit(
                    "knowledge.collection.permission_bundle.granted",
                    collection,
                    metadata={
                        "subject_type": request.subject_type,
                        "role_bundle": request.role_bundle,
                        "permission_actions": list(actions),
                    },
                )
                self.db.commit()
            except IntegrityError as exc:
                self.db.rollback()
                raise KnowledgeCollectionServiceError(
                    409,
                    "conflict",
                    "Knowledge Collection permission bundle changed concurrently.",
                ) from exc
            except Exception:
                self.db.rollback()
                raise
            for row in created_rows:
                self.db.refresh(row)
        else:
            self.db.rollback()

        if request.subject_type == "team":
            return [self._team_permission_response(row) for row in rows]
        return [self._user_permission_response(row) for row in rows]

    def revoke_permission_bundle(
        self,
        collection_id: uuid.UUID,
        request: KnowledgeCollectionPermissionBundleGrantRequest,
    ) -> None:
        collection = self._locked_collection_or_hidden(collection_id)
        self._require_collection_permission_authority(collection)
        actions = COLLECTION_ROLE_BUNDLE_ACTIONS[request.role_bundle]
        model, subject_column = self._collection_permission_model_and_subject_column(
            request.subject_type
        )
        rows = (
            self.db.query(model)
            .filter(
                model.grantee_organization_id == self.organization_id,
                model.knowledge_collection_id == collection.id,
                subject_column == request.subject_id,
                model.permission_action.in_(actions),
            )
            .with_for_update()
            .all()
        )
        if not rows:
            self.db.rollback()
            return
        manage_row = next(
            (row for row in rows if row.permission_action == "manage"),
            None,
        )
        if manage_row is not None and self._would_revoke_current_user_last_manage_path(
            collection.id,
            request.subject_type,
            manage_row,
        ):
            self.db.rollback()
            raise KnowledgeCollectionServiceError(
                403,
                "permission.denied",
                "Cannot revoke your own last management path.",
            )
        for row in rows:
            self.db.delete(row)
        self._record_collection_audit_and_commit(
            "knowledge.collection.permission_bundle.revoked",
            collection,
            metadata={
                "subject_type": request.subject_type,
                "role_bundle": request.role_bundle,
                "permission_actions": list(actions),
            },
        )

    def mutate_permission_bundle_bulk(
        self,
        request: KnowledgeCollectionPermissionBulkBundleRequest,
    ) -> KnowledgeCollectionPermissionBulkBundleResponse:
        collection_ids = sorted(request.collection_ids, key=str)
        collections = (
            self.db.query(KnowledgeCollection)
            .filter(
                KnowledgeCollection.organization_id == self.organization_id,
                KnowledgeCollection.id.in_(collection_ids),
                KnowledgeCollection.lifecycle_state != "deleted",
            )
            .order_by(KnowledgeCollection.id.asc())
            .with_for_update()
            .all()
        )
        if len(collections) != len(collection_ids):
            self.db.rollback()
            raise KnowledgeCollectionServiceError(
                404, "resource.hidden", "Resource not found."
            )

        authority = self._require_bulk_collection_permission_authority(collections)
        if request.operation == "grant" and authority == "domain_delegate":
            probe = KnowledgeCollectionPermissionGrantRequest(
                subject_type=request.subject_type,
                subject_id=request.subject_id,
                permission_action=COLLECTION_ROLE_BUNDLE_ACTIONS[
                    request.role_bundle
                ][0],
            )
            self._block_collection_delegate_self_escalation(
                collections[0],
                probe,
                authority=authority,
            )

        self._lock_bundle_subject(
            request.subject_type,
            request.subject_id,
            require_active=request.operation == "grant",
        )
        actions = COLLECTION_ROLE_BUNDLE_ACTIONS[request.role_bundle]
        model, subject_column = self._collection_permission_model_and_subject_column(
            request.subject_type
        )
        existing_rows = (
            self.db.query(model)
            .filter(
                model.grantee_organization_id == self.organization_id,
                model.knowledge_collection_id.in_(collection_ids),
                subject_column == request.subject_id,
                model.permission_action.in_(actions),
            )
            .order_by(model.knowledge_collection_id.asc(), model.permission_action.asc())
            .with_for_update()
            .all()
        )
        existing = {
            (row.knowledge_collection_id, row.permission_action): row
            for row in existing_rows
        }

        if request.operation == "revoke" and "manage" in actions:
            self._require_no_bulk_last_manage_violation(
                collection_ids,
                subject_type=request.subject_type,
                subject_id=request.subject_id,
                planned_rows=[
                    row
                    for row in existing_rows
                    if row.permission_action == "manage"
                ],
            )

        changed_collection_ids: set[uuid.UUID] = set()
        if request.operation == "grant":
            for collection in collections:
                for action in actions:
                    key = (collection.id, action)
                    if key in existing:
                        continue
                    row = model(
                        id=uuid.uuid4(),
                        grantee_organization_id=self.organization_id,
                        assigned_by=self.user_id,
                        knowledge_collection_id=collection.id,
                        permission_action=action,
                        **{
                            "team_id" if request.subject_type == "team" else "user_id": (
                                request.subject_id
                            )
                        },
                    )
                    self.db.add(row)
                    changed_collection_ids.add(collection.id)
        else:
            for row in existing_rows:
                changed_collection_ids.add(row.knowledge_collection_id)
                self.db.delete(row)

        changed_count = len(changed_collection_ids)
        try:
            response = KnowledgeCollectionPermissionBulkBundleResponse(
                operation=request.operation,
                subject_type=request.subject_type,
                role_bundle=request.role_bundle,
                target_count_bucket=bulk_permission_count_bucket(len(collections)),
                changed_count_bucket=bulk_permission_count_bucket(changed_count),
                unchanged_count_bucket=bulk_permission_count_bucket(
                    len(collections) - changed_count
                ),
            )
            if not changed_collection_ids:
                self.db.rollback()
            else:
                for collection in collections:
                    if collection.id not in changed_collection_ids:
                        continue
                    self._record_collection_audit(
                        (
                            "knowledge.collection.permission_bundle.granted"
                            if request.operation == "grant"
                            else "knowledge.collection.permission_bundle.revoked"
                        ),
                        collection,
                        metadata={
                            "subject_type": request.subject_type,
                            "role_bundle": request.role_bundle,
                            "permission_actions": list(actions),
                            "bulk_operation": True,
                        },
                    )
                self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise KnowledgeCollectionServiceError(
                409,
                "conflict",
                "Knowledge Collection permissions changed concurrently.",
            ) from exc
        except Exception:
            self.db.rollback()
            raise

        return response

    def revoke_permission(self, collection_id: uuid.UUID, permission_id: uuid.UUID) -> None:
        collection = self._locked_collection_or_hidden(collection_id)
        self._require_collection_permission_authority(collection)
        row = (
            self.db.query(TeamKnowledgeCollectionPermission)
            .filter(
                TeamKnowledgeCollectionPermission.id == permission_id,
                TeamKnowledgeCollectionPermission.grantee_organization_id
                == self.organization_id,
                TeamKnowledgeCollectionPermission.knowledge_collection_id
                == collection.id,
            )
            .with_for_update()
            .first()
        )
        subject_type = "team"
        if row is None:
            row = (
                self.db.query(UserKnowledgeCollectionPermission)
                .filter(
                    UserKnowledgeCollectionPermission.id == permission_id,
                    UserKnowledgeCollectionPermission.grantee_organization_id
                    == self.organization_id,
                    UserKnowledgeCollectionPermission.knowledge_collection_id
                    == collection.id,
                )
                .with_for_update()
                .first()
            )
            subject_type = "user"
        if row is None:
            raise KnowledgeCollectionServiceError(404, "resource.hidden", "Resource not found.")
        if self._would_revoke_current_user_last_manage_path(
            collection.id,
            subject_type,
            row,
        ):
            raise KnowledgeCollectionServiceError(
                403,
                "permission.denied",
                "Cannot revoke your own last management path.",
            )
        permission_action = row.permission_action
        self.db.delete(row)
        self._record_collection_audit_and_commit(
            "knowledge.collection.permission.revoked",
            collection,
            metadata={
                "subject_type": subject_type,
                "permission_action": permission_action,
            },
        )

    def update_visibility(
        self,
        collection_id: uuid.UUID,
        request: KnowledgeCollectionVisibilityRequest,
    ) -> KnowledgeCollectionVisibilityResponse:
        collection = self._locked_collection_or_hidden(collection_id)
        self._require_org_manager()
        if collection.lifecycle_state != "active":
            self.db.rollback()
            raise KnowledgeCollectionServiceError(
                409,
                "conflict",
                "Archived collections must be restored before changing visibility.",
            )
        if request.visibility == "public" and not request.acknowledged_public_runtime_exposure:
            raise KnowledgeCollectionServiceError(
                400,
                "validation.failed",
                "Public visibility acknowledgement is required.",
                {"field": "acknowledged_public_runtime_exposure"},
            )
        if (
            request.visibility == "public"
            and (
                getattr(collection, "source_identity_id", None) is not None
                or bool(getattr(collection, "source_connector_ref", None))
                or self._collection_has_source_managed_items(collection.id)
            )
        ):
            raise KnowledgeCollectionServiceError(
                409,
                "policy.blocked",
                "Source-managed Knowledge requires public exposure approval.",
                {"policy_reason": "source_public_exposure_required"},
            )

        metadata = dict(collection.safe_metadata or {})
        if self._visibility(collection) == request.visibility:
            self.db.rollback()
            response = self.get_collection(collection_id)
            return KnowledgeCollectionVisibilityResponse(
                collection=response,
                linked_kb_count_bucket=response.linked_kb_count_bucket,
                active_kb_count_bucket=response.active_kb_count_bucket,
            )
        metadata["visibility"] = request.visibility
        collection.safe_metadata = metadata
        collection.updated_at = self._now()
        self._record_collection_audit_and_commit(
            "knowledge.collection.visibility.changed",
            collection,
            metadata={"visibility": request.visibility},
        )
        self.db.refresh(collection)
        response = self._collection_response(collection)
        return KnowledgeCollectionVisibilityResponse(
            collection=response,
            linked_kb_count_bucket=response.linked_kb_count_bucket,
            active_kb_count_bucket=response.active_kb_count_bucket,
        )

    def _grant_team_permission(
        self,
        collection: KnowledgeCollection,
        request: KnowledgeCollectionPermissionGrantRequest,
    ) -> tuple[TeamKnowledgeCollectionPermission, bool]:
        team = (
            self.db.query(Team)
            .filter(
                Team.id == request.subject_id,
                Team.organization_id == self.organization_id,
                Team.is_active.is_(True),
            )
            .with_for_update()
            .first()
        )
        if team is None:
            raise KnowledgeCollectionServiceError(404, "resource.hidden", "Resource not found.")
        row = (
            self.db.query(TeamKnowledgeCollectionPermission)
            .filter(
                TeamKnowledgeCollectionPermission.grantee_organization_id
                == self.organization_id,
                TeamKnowledgeCollectionPermission.team_id == team.id,
                TeamKnowledgeCollectionPermission.knowledge_collection_id
                == collection.id,
                TeamKnowledgeCollectionPermission.permission_action
                == request.permission_action,
            )
            .with_for_update()
            .first()
        )
        if row is not None:
            return row, False
        row = TeamKnowledgeCollectionPermission(
            id=uuid.uuid4(),
            grantee_organization_id=self.organization_id,
            team_id=team.id,
            assigned_by=self.user_id,
            knowledge_collection_id=collection.id,
            permission_action=request.permission_action,
        )
        self.db.add(row)
        return row, True

    def _grant_user_permission(
        self,
        collection: KnowledgeCollection,
        request: KnowledgeCollectionPermissionGrantRequest,
    ) -> tuple[UserKnowledgeCollectionPermission, bool]:
        row = (
            self.db.query(User, OrganizationMembership)
            .join(
                OrganizationMembership,
                OrganizationMembership.user_id == User.id,
            )
            .filter(
                User.id == request.subject_id,
                User.deactivated_at.is_(None),
                OrganizationMembership.organization_id == self.organization_id,
                OrganizationMembership.membership_state == "active",
            )
            .with_for_update()
            .first()
        )
        if row is None:
            raise KnowledgeCollectionServiceError(404, "resource.hidden", "Resource not found.")
        user = row[0]
        permission_row = (
            self.db.query(UserKnowledgeCollectionPermission)
            .filter(
                UserKnowledgeCollectionPermission.grantee_organization_id
                == self.organization_id,
                UserKnowledgeCollectionPermission.user_id == user.id,
                UserKnowledgeCollectionPermission.knowledge_collection_id
                == collection.id,
                UserKnowledgeCollectionPermission.permission_action
                == request.permission_action,
            )
            .with_for_update()
            .first()
        )
        if permission_row is not None:
            return permission_row, False
        permission_row = UserKnowledgeCollectionPermission(
            id=uuid.uuid4(),
            grantee_organization_id=self.organization_id,
            user_id=user.id,
            assigned_by=self.user_id,
            knowledge_collection_id=collection.id,
            permission_action=request.permission_action,
        )
        self.db.add(permission_row)
        return permission_row, True

    def _collection_response(
        self,
        collection: KnowledgeCollection,
    ) -> KnowledgeCollectionResponse:
        permissions = {
            action: self.permission_helper.evaluate_collection_action(
                collection,
                action,
                include_archived=True,
            ).allowed
            for action in ("read", "route", "manage", "sync")
        }
        linked_count = self._linked_kb_count(collection.id)
        active_count = self._active_kb_count(collection.id)
        safe_metadata = dict(collection.safe_metadata or {})
        return KnowledgeCollectionResponse(
            id=collection.id,
            organization_id=collection.organization_id,
            name=collection.name,
            description=collection.description,
            is_system_managed=collection.is_system_managed,
            sync_state=collection.sync_state,
            lifecycle_state=collection.lifecycle_state,
            visibility=self._visibility(collection),
            linked_kb_count_bucket=bucket_count(linked_count),
            active_kb_count_bucket=bucket_count(active_count),
            can_read=permissions["read"],
            can_route=permissions["route"],
            can_manage=permissions["manage"],
            can_sync=permissions["sync"],
            sync_supported=self._sync_supported(collection),
            safe_metadata=safe_metadata,
            created_at=collection.created_at,
            updated_at=collection.updated_at,
        )

    def _sync_supported(self, collection: KnowledgeCollection) -> bool:
        if (
            collection.lifecycle_state != "active"
            or collection.sync_state == "source_deleted"
            or collection.is_system_managed
            or collection.source_identity_id is not None
            or collection.source_connector_ref
        ):
            return False
        return scan_collection_sync_targets(
            self.db,
            self.organization_id,
            collection.id,
            limit=MAX_SYNC_TARGETS + 1,
        ).is_supported

    def _item_response(
        self,
        item: KnowledgeCollectionItem,
    ) -> KnowledgeCollectionItemResponse:
        kb = item.knowledge_base or self._knowledge_base_or_hidden(item.knowledge_base_id)
        auth_state = get_effective_knowledge_base_auth_state(
            self.db,
            self.user_id,
            kb.id,
            organization_id=self.organization_id,
        )
        use_decision = self.permission_helper.evaluate_kb_use(kb)
        return KnowledgeCollectionItemResponse(
            item_id=item.id,
            knowledge_base_id=kb.id,
            safe_label=self._kb_safe_label(
                kb,
                can_read_kb=knowledge_base_auth_state_allows(auth_state, "read"),
            ),
            lifecycle_state=kb.lifecycle_state,
            sync_state=kb.sync_state,
            rank=item.rank,
            can_manage_kb=knowledge_base_auth_state_allows(auth_state, "manage"),
            can_use_kb=use_decision.allowed,
        )

    def _items_response(
        self,
        collection_id: uuid.UUID,
        items: list[KnowledgeCollectionItem],
    ) -> KnowledgeCollectionItemsResponse:
        snapshots = [
            CollectionItemOrderSnapshot(
                item_id=item.id,
                rank=item.rank,
                created_at=item.created_at,
            )
            for item in items
        ]
        reorder_supported = len(items) <= MAX_REORDER_ITEMS
        return KnowledgeCollectionItemsResponse(
            items=[self._item_response(item) for item in items],
            order_revision=compute_order_revision(collection_id, snapshots),
            reorder_supported=reorder_supported,
            safe_reason_code=(
                None if reorder_supported else "item_reorder_limit_exceeded"
            ),
        )

    def _ordered_collection_items(
        self, collection_id: uuid.UUID
    ) -> list[KnowledgeCollectionItem]:
        return (
            self.db.query(KnowledgeCollectionItem)
            .filter(
                KnowledgeCollectionItem.organization_id == self.organization_id,
                KnowledgeCollectionItem.collection_id == collection_id,
            )
            .order_by(
                KnowledgeCollectionItem.rank.asc(),
                KnowledgeCollectionItem.created_at.asc(),
                KnowledgeCollectionItem.id.asc(),
            )
            .all()
        )

    def _locked_collection_items(
        self, collection_id: uuid.UUID
    ) -> list[KnowledgeCollectionItem]:
        return (
            self.db.query(KnowledgeCollectionItem)
            .filter(
                KnowledgeCollectionItem.organization_id == self.organization_id,
                KnowledgeCollectionItem.collection_id == collection_id,
            )
            .order_by(
                KnowledgeCollectionItem.rank.asc(),
                KnowledgeCollectionItem.created_at.asc(),
                KnowledgeCollectionItem.id.asc(),
            )
            .with_for_update()
            .all()
        )

    @staticmethod
    def _normalize_item_ranks(items: list[KnowledgeCollectionItem]) -> None:
        for rank, item in enumerate(items):
            item.rank = rank

    def _team_permission_response(
        self,
        row: TeamKnowledgeCollectionPermission,
    ) -> KnowledgeCollectionPermissionResponse:
        team = row.team or self.db.query(Team).filter(Team.id == row.team_id).first()
        return KnowledgeCollectionPermissionResponse(
            permission_id=row.id,
            subject_type="team",
            subject_id=row.team_id,
            subject_safe_label=(
                safe_label_from_text(getattr(team, "name", None)) or "Team"
            ),
            permission_action=row.permission_action,
        )

    def _user_permission_response(
        self,
        row: UserKnowledgeCollectionPermission,
    ) -> KnowledgeCollectionPermissionResponse:
        user = row.user or self.db.query(User).filter(User.id == row.user_id).first()
        return KnowledgeCollectionPermissionResponse(
            permission_id=row.id,
            subject_type="user",
            subject_id=row.user_id,
            subject_safe_label=(
                safe_label_from_text(getattr(user, "name", None)) or "User"
            ),
            permission_action=row.permission_action,
        )

    def _require_org_manager(self) -> None:
        if not self._is_org_manager():
            raise KnowledgeCollectionServiceError(
                403,
                "permission.denied",
                "Organization manager permission is required.",
            )

    def _require_org_manager_or_domain(self, action: str) -> None:
        if self._is_org_manager() or self._has_domain_action(action):
            return
        raise KnowledgeCollectionServiceError(
            403,
            "permission.denied",
            "Knowledge administration permission is required.",
        )

    def _is_org_manager(self) -> bool:
        return has_organization_manager_permission(
            self.db,
            self.user_id,
            self.organization_id,
        )

    def _has_domain_action(self, action: str) -> bool:
        if self._domain_actions_cache is None:
            self._domain_actions_cache = get_effective_knowledge_domain_actions(
                self.db,
                self.user_id,
                self.organization_id,
            )
        return action in self._domain_actions_cache

    def _has_safe_collection_admin_visibility(self) -> bool:
        return any(
            self._has_domain_action(action)
            for action in (
                "catalog_manage",
                "permission_delegate",
                "lifecycle_manage",
                "sync_manage",
            )
        )

    def _require_collection_manage_or_catalog(
        self,
        collection: KnowledgeCollection,
    ) -> None:
        if (
            not collection.is_system_managed
            and self._visibility(collection) == "private"
            and self._has_domain_action("catalog_manage")
        ):
            return
        self._require_collection_action(collection, "manage")

    def _require_collection_membership_candidate_access(
        self,
        collection: KnowledgeCollection,
    ) -> None:
        if self._visibility(collection) == "public":
            self._require_org_manager()
            return
        self._require_collection_manage_or_catalog(collection)

    def _require_collection_membership_mutation(
        self,
        collection: KnowledgeCollection,
        *,
        kb: KnowledgeBase | None = None,
        acknowledged_public_runtime_exposure: bool = False,
        adds_public_exposure: bool = False,
    ) -> None:
        if collection.lifecycle_state != "active":
            self.db.rollback()
            raise KnowledgeCollectionServiceError(
                409,
                "conflict",
                "Archived collections must be restored before changing membership.",
            )
        if collection.is_system_managed:
            raise KnowledgeCollectionServiceError(
                403,
                "policy.denied",
                "System-managed collections cannot be manually changed.",
            )
        if self._visibility(collection) == "public":
            self._require_org_manager()
            if not acknowledged_public_runtime_exposure:
                raise KnowledgeCollectionServiceError(
                    400,
                    "validation.failed",
                    "Public membership acknowledgement is required.",
                    {"field": "acknowledged_public_runtime_exposure"},
                )
            if adds_public_exposure and (
                getattr(collection, "source_identity_id", None) is not None
                or bool(getattr(collection, "source_connector_ref", None))
                or (kb is not None and self._is_source_managed_kb(kb))
            ):
                raise KnowledgeCollectionServiceError(
                    409,
                    "policy.blocked",
                    "Source-managed Knowledge requires public exposure approval.",
                    {"policy_reason": "source_public_exposure_required"},
                )
            return
        if self._has_domain_action("catalog_manage"):
            return
        self._require_collection_action(collection, "manage")
        if kb is not None:
            self._require_kb_manage(kb)

    def _kb_link_candidate_allowed(
        self,
        collection: KnowledgeCollection,
        kb: KnowledgeBase,
    ) -> bool:
        if self._visibility(collection) == "public":
            return self._is_org_manager() and not self._is_source_managed_kb(kb)
        if self._has_domain_action("catalog_manage"):
            return True
        return self._kb_manage_allowed(kb)

    def _require_collection_permission_authority(
        self,
        collection: KnowledgeCollection,
    ) -> str:
        if self._is_org_manager():
            return "organization_manager"
        decision = self.permission_helper.evaluate_collection_action(
            collection,
            "manage",
            include_archived=True,
        )
        if decision.allowed:
            return "resource_manager"
        if self._has_domain_action("permission_delegate"):
            return "domain_delegate"
        status_code = 404 if decision.external_reason_code == "resource.hidden" else 403
        raise KnowledgeCollectionServiceError(
            status_code,
            decision.external_reason_code,
            "Resource not found." if status_code == 404 else "Permission denied.",
        )

    def _require_bulk_collection_permission_authority(
        self,
        collections: list[KnowledgeCollection],
    ) -> str:
        if self._is_org_manager():
            return "organization_manager"
        decisions = self.permission_helper.bulk_evaluate_collection_action(
            collections,
            "manage",
            include_archived=True,
        )
        denied = [
            decisions.get(collection.id)
            for collection in collections
            if not decisions.get(collection.id)
            or not decisions[collection.id].allowed
        ]
        if not denied:
            return "resource_manager"
        if self._has_domain_action("permission_delegate"):
            return "domain_delegate"
        self.db.rollback()
        hidden = any(
            getattr(decision, "external_reason_code", "resource.hidden")
            == "resource.hidden"
            for decision in denied
        )
        raise KnowledgeCollectionServiceError(
            404 if hidden else 403,
            "resource.hidden" if hidden else "permission.denied",
            "Resource not found." if hidden else "Permission denied.",
        )

    @staticmethod
    def _collection_permission_model_and_subject_column(subject_type: str):
        if subject_type == "team":
            return TeamKnowledgeCollectionPermission, TeamKnowledgeCollectionPermission.team_id
        return UserKnowledgeCollectionPermission, UserKnowledgeCollectionPermission.user_id

    def _lock_bundle_subject(
        self,
        subject_type: str,
        subject_id: uuid.UUID,
        *,
        require_active: bool,
    ) -> None:
        if not require_active:
            return
        if subject_type == "team":
            subject = (
                self.db.query(Team)
                .filter(
                    Team.id == subject_id,
                    Team.organization_id == self.organization_id,
                    Team.is_active.is_(True),
                )
                .with_for_update()
                .first()
            )
        else:
            subject = (
                self.db.query(User, OrganizationMembership)
                .join(
                    OrganizationMembership,
                    OrganizationMembership.user_id == User.id,
                )
                .filter(
                    User.id == subject_id,
                    User.deactivated_at.is_(None),
                    OrganizationMembership.organization_id == self.organization_id,
                    OrganizationMembership.membership_state == "active",
                )
                .with_for_update()
                .first()
            )
        if subject is None:
            self.db.rollback()
            raise KnowledgeCollectionServiceError(
                404, "resource.hidden", "Resource not found."
            )

    def _require_no_bulk_last_manage_violation(
        self,
        collection_ids: list[uuid.UUID],
        *,
        subject_type: str,
        subject_id: uuid.UUID,
        planned_rows: list[
            TeamKnowledgeCollectionPermission | UserKnowledgeCollectionPermission
        ],
    ) -> None:
        if self._is_org_manager() or not planned_rows:
            return
        active_team_ids = self._active_team_ids()
        targets_actor = (
            subject_type == "user" and subject_id == self.user_id
        ) or (
            subject_type == "team" and subject_id in active_team_ids
        )
        if not targets_actor:
            return

        planned_ids = {row.id for row in planned_rows}
        direct_rows = (
            self.db.query(UserKnowledgeCollectionPermission)
            .filter(
                UserKnowledgeCollectionPermission.grantee_organization_id
                == self.organization_id,
                UserKnowledgeCollectionPermission.user_id == self.user_id,
                UserKnowledgeCollectionPermission.knowledge_collection_id.in_(
                    collection_ids
                ),
                UserKnowledgeCollectionPermission.permission_action == "manage",
            )
            .with_for_update()
            .all()
        )
        team_rows: list[TeamKnowledgeCollectionPermission] = []
        if active_team_ids:
            team_rows = (
                self.db.query(TeamKnowledgeCollectionPermission)
                .filter(
                    TeamKnowledgeCollectionPermission.grantee_organization_id
                    == self.organization_id,
                    TeamKnowledgeCollectionPermission.team_id.in_(active_team_ids),
                    TeamKnowledgeCollectionPermission.knowledge_collection_id.in_(
                        collection_ids
                    ),
                    TeamKnowledgeCollectionPermission.permission_action == "manage",
                )
                .with_for_update()
                .all()
            )
        alternate_collection_ids = {
            row.knowledge_collection_id
            for row in [*direct_rows, *team_rows]
            if row.id not in planned_ids
        }
        if any(
            row.knowledge_collection_id not in alternate_collection_ids
            for row in planned_rows
        ):
            self.db.rollback()
            raise KnowledgeCollectionServiceError(
                403,
                "permission.denied",
                "Cannot revoke your own last management path.",
            )

    def _block_collection_delegate_self_escalation(
        self,
        collection: KnowledgeCollection,
        request: KnowledgeCollectionPermissionGrantRequest,
        *,
        authority: str,
    ) -> None:
        if authority != "domain_delegate":
            return
        targets_actor = request.subject_type == "user" and request.subject_id == self.user_id
        if request.subject_type == "team":
            targets_actor = request.subject_id in self._active_team_ids()
        if not targets_actor:
            return
        try:
            add_action_audit(
                self.db,
                "knowledge.collection.permission_grant.blocked",
                self.user_id,
                "knowledge_collection",
                collection.id,
                organization_id=self.organization_id,
                status="blocked",
                metadata={"policy_reason": "knowledge.self_escalation"},
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        raise KnowledgeCollectionServiceError(
            409,
            "policy.blocked",
            "Knowledge permission grant is blocked by policy.",
            {"policy_reason": "knowledge.self_escalation"},
        )

    def _require_collection_action(
        self,
        collection: KnowledgeCollection,
        action: str,
    ) -> None:
        decision = self.permission_helper.evaluate_collection_action(
            collection,
            action,
            include_archived=True,
        )
        if not decision.allowed:
            status_code = 404 if decision.external_reason_code == "resource.hidden" else 403
            raise KnowledgeCollectionServiceError(
                status_code,
                decision.external_reason_code,
                "Resource not found." if status_code == 404 else "Permission denied.",
            )

    def _require_kb_manage(self, kb: KnowledgeBase) -> None:
        if not self._kb_manage_allowed(kb):
            raise KnowledgeCollectionServiceError(
                403,
                "permission.denied",
                "Knowledge Base manage permission is required.",
            )

    def _kb_manage_allowed(self, kb: KnowledgeBase) -> bool:
        return has_knowledge_base_permission(
            self.db,
            self.user_id,
            kb.id,
            "manage",
            organization_id=self.organization_id,
        )

    def _kb_safe_label(
        self,
        kb: KnowledgeBase,
        *,
        can_read_kb: bool = False,
    ) -> str:
        source_identity = getattr(kb, "source_identity", None)
        if source_identity is not None or self._is_source_managed_kb(kb):
            if getattr(source_identity, "display_policy_state", None) == "approved":
                safe_display_name = safe_label_from_text(
                    getattr(source_identity, "safe_display_name", None)
                )
                if safe_display_name:
                    return safe_display_name
            return GENERIC_KB_LABEL

        safe_metadata = sanitize_kb_safe_metadata(
            getattr(kb, "safe_metadata", None)
        )
        safe_label = safe_metadata.get("safe_label")
        if isinstance(safe_label, str):
            return safe_label
        if can_read_kb:
            readable_name = safe_label_from_text(getattr(kb, "name", None))
            if readable_name:
                return readable_name
        return GENERIC_KB_LABEL

    def _collection_or_hidden(self, collection_id: uuid.UUID) -> KnowledgeCollection:
        collection = (
            self.db.query(KnowledgeCollection)
            .filter(
                KnowledgeCollection.id == collection_id,
                KnowledgeCollection.organization_id == self.organization_id,
                KnowledgeCollection.lifecycle_state != "deleted",
            )
            .first()
        )
        if collection is None:
            raise KnowledgeCollectionServiceError(404, "resource.hidden", "Resource not found.")
        return collection

    def _locked_collection_or_hidden(
        self, collection_id: uuid.UUID
    ) -> KnowledgeCollection:
        collection = (
            self.db.query(KnowledgeCollection)
            .filter(
                KnowledgeCollection.id == collection_id,
                KnowledgeCollection.organization_id == self.organization_id,
                KnowledgeCollection.lifecycle_state != "deleted",
            )
            .with_for_update()
            .first()
        )
        if collection is None:
            self.db.rollback()
            raise KnowledgeCollectionServiceError(
                404, "resource.hidden", "Resource not found."
            )
        return collection

    def _item_or_hidden(
        self,
        collection_id: uuid.UUID,
        item_id: uuid.UUID,
    ) -> KnowledgeCollectionItem:
        item = (
            self.db.query(KnowledgeCollectionItem)
            .filter(
                KnowledgeCollectionItem.id == item_id,
                KnowledgeCollectionItem.organization_id == self.organization_id,
                KnowledgeCollectionItem.collection_id == collection_id,
            )
            .first()
        )
        if item is None:
            raise KnowledgeCollectionServiceError(404, "resource.hidden", "Resource not found.")
        return item

    def _knowledge_base_or_hidden(self, kb_id: uuid.UUID) -> KnowledgeBase:
        kb = (
            self.db.query(KnowledgeBase)
            .filter(
                KnowledgeBase.id == kb_id,
                KnowledgeBase.organization_id == self.organization_id,
                KnowledgeBase.lifecycle_state != "deleted",
            )
            .first()
        )
        if kb is None:
            raise KnowledgeCollectionServiceError(404, "resource.hidden", "Resource not found.")
        return kb

    def _linked_kb_count(self, collection_id: uuid.UUID) -> int:
        return (
            self.db.query(func.count(KnowledgeCollectionItem.id))
            .filter(KnowledgeCollectionItem.collection_id == collection_id)
            .scalar()
            or 0
        )

    def _active_kb_count(self, collection_id: uuid.UUID) -> int:
        return (
            self.db.query(func.count(KnowledgeCollectionItem.id))
            .join(KnowledgeBase, KnowledgeBase.id == KnowledgeCollectionItem.knowledge_base_id)
            .filter(
                KnowledgeCollectionItem.collection_id == collection_id,
                KnowledgeBase.lifecycle_state == "active",
            )
            .scalar()
            or 0
        )

    def _collection_has_source_managed_items(
        self,
        collection_id: uuid.UUID,
    ) -> bool:
        return (
            self.db.query(func.count(KnowledgeCollectionItem.id))
            .join(
                KnowledgeBase,
                KnowledgeBase.id == KnowledgeCollectionItem.knowledge_base_id,
            )
            .filter(
                KnowledgeCollectionItem.organization_id == self.organization_id,
                KnowledgeCollectionItem.collection_id == collection_id,
                KnowledgeBase.source_identity_id.is_not(None),
            )
            .scalar()
            or 0
        ) > 0

    def _is_source_managed_kb(self, kb: KnowledgeBase) -> bool:
        return getattr(kb, "source_identity_id", None) is not None

    def _visibility(self, collection: KnowledgeCollection) -> str:
        return collection_visibility(collection)

    def _sanitize_safe_metadata(
        self,
        metadata: dict | None,
        *,
        preserve_visibility: str | None = None,
    ) -> dict:
        if not metadata:
            sanitized: dict[str, Any] = {}
        else:
            sanitized = {}
            for key, value in metadata.items():
                key_text = str(key)
                if key_text == "visibility":
                    continue
                if key_text == "safe_label":
                    if not isinstance(value, str):
                        raise KnowledgeCollectionServiceError(
                            400,
                            "validation.failed",
                            "safe_metadata.safe_label must be a string.",
                            {"field": "safe_metadata.safe_label"},
                        )
                    safe_label = safe_label_from_text(value)
                    if not safe_label:
                        raise KnowledgeCollectionServiceError(
                            400,
                            "validation.failed",
                            "safe_metadata.safe_label must contain display-safe text.",
                            {"field": "safe_metadata.safe_label"},
                        )
                    sanitized["safe_label"] = safe_label
                    continue
                if safe_metadata_key_is_forbidden(key_text):
                    raise KnowledgeCollectionServiceError(
                        400,
                        "validation.failed",
                        "safe_metadata contains a forbidden key.",
                        {"field": "safe_metadata"},
                    )
                try:
                    sanitized[key_text[:64]] = sanitize_safe_metadata_value(value)
                except TypeError as exc:
                    raise KnowledgeCollectionServiceError(
                        400,
                        "validation.failed",
                        "safe_metadata supports only primitive values and primitive lists.",
                        {"field": "safe_metadata"},
                    ) from exc
        if preserve_visibility in {"public", "private"}:
            sanitized["visibility"] = preserve_visibility
        return sanitized

    def _normalize_optional_text(self, value: str | None) -> str | None:
        return normalize_optional_text(value)

    def _normalize_required_text(self, value: str, field: str) -> str:
        normalized = normalize_required_text(value)
        if not normalized:
            raise KnowledgeCollectionServiceError(
                400,
                "validation.failed",
                "Required text field cannot be blank.",
                {"field": field},
            )
        return normalized

    def _would_revoke_current_user_last_manage_path(
        self,
        collection_id: uuid.UUID,
        subject_type: str,
        row: TeamKnowledgeCollectionPermission | UserKnowledgeCollectionPermission,
    ) -> bool:
        if self._is_org_manager() or row.permission_action != "manage":
            return False

        if subject_type == "user":
            if getattr(row, "user_id", None) != self.user_id:
                return False
        elif subject_type == "team":
            if getattr(row, "team_id", None) not in self._active_team_ids():
                return False
        else:
            return False

        # 본인의 마지막 manage 경로를 끊으면 이후 복구가 관리자 개입에 의존한다.
        return not self._has_alternate_collection_manage_path(
            collection_id,
            exclude_permission_id=row.id,
        )

    def _has_alternate_collection_manage_path(
        self,
        collection_id: uuid.UUID,
        *,
        exclude_permission_id: uuid.UUID,
    ) -> bool:
        direct_user_grant = (
            self.db.query(UserKnowledgeCollectionPermission.id)
            .filter(
                UserKnowledgeCollectionPermission.user_id == self.user_id,
                UserKnowledgeCollectionPermission.grantee_organization_id
                == self.organization_id,
                UserKnowledgeCollectionPermission.knowledge_collection_id
                == collection_id,
                UserKnowledgeCollectionPermission.permission_action == "manage",
                UserKnowledgeCollectionPermission.id != exclude_permission_id,
            )
            .first()
        )
        if direct_user_grant is not None:
            return True

        team_ids = self._active_team_ids()
        if not team_ids:
            return False
        return (
            self.db.query(TeamKnowledgeCollectionPermission.id)
            .filter(
                TeamKnowledgeCollectionPermission.grantee_organization_id
                == self.organization_id,
                TeamKnowledgeCollectionPermission.knowledge_collection_id
                == collection_id,
                TeamKnowledgeCollectionPermission.permission_action == "manage",
                TeamKnowledgeCollectionPermission.team_id.in_(team_ids),
                TeamKnowledgeCollectionPermission.id != exclude_permission_id,
            )
            .first()
            is not None
        )

    def _active_team_ids(self) -> set[uuid.UUID]:
        rows = (
            self.db.query(TeamMembership.team_id)
            .join(Team, Team.id == TeamMembership.team_id)
            .filter(
                TeamMembership.user_id == self.user_id,
                TeamMembership.grantee_organization_id == self.organization_id,
                Team.organization_id == self.organization_id,
                Team.is_active.is_(True),
            )
            .all()
        )
        return {row[0] for row in rows}

    def _record_collection_audit_and_commit(
        self,
        action: str,
        collection: KnowledgeCollection,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        try:
            self._record_collection_audit(
                action,
                collection,
                metadata=metadata,
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def _record_collection_audit(
        self,
        action: str,
        collection: KnowledgeCollection,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        # Audit에는 raw source title/path/url이나 hidden count를 넣지 않는다.
        add_data_change_audit(
            self.db,
            action,
            self.user_id,
            "knowledge_collection",
            collection.id,
            organization_id=self.organization_id,
            metadata={
                "organization_id": str(self.organization_id),
                "visibility": self._visibility(collection),
                **(metadata or {}),
            },
        )

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)
