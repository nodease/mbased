import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from apps.shared.db.models.knowledge import (
    KnowledgeBase,
    KnowledgeCollection,
    SourceAuthorizationProvenance,
    SourcePolicyKBUseGrant,
)
from apps.shared.db.models.organization_membership import ORGANIZATION_AUTH_MEMBER
from apps.shared.db.models.team import (
    Team,
    TeamKnowledgeCollectionPermission,
    TeamKnowledgePermission,
    TeamMembership,
    UserKnowledgeCollectionPermission,
    UserKnowledgePermission,
)
from apps.shared.permissions import (
    AUTH_STATE_MANAGER,
    AUTH_STATE_NONE,
    AUTH_STATE_OPERATOR,
    knowledge_base_auth_state_allows,
    stronger_resource_auth_state,
)
from apps.shared.schemas.knowledge import KnowledgePermissionDecision
from apps.shared.services.permissions import (
    get_effective_knowledge_base_auth_state,
    get_organization_auth_state,
    has_active_organization_membership,
)
from sqlalchemy import and_, exists, false, or_
from sqlalchemy.orm import Session, load_only

COLLECTION_PERMISSION_ACTIONS = {"read", "route", "manage", "sync"}
SOURCE_ACL_PASS_STATE = "fresh"
SOURCE_ACL_FAIL_STATES = {"stale", "unmapped", "ambiguous", "unverified", "revoked"}
SOURCE_RETRIEVAL_PERMISSION_ACTIONS = {
    "read",
    "view",
    "use",
    "retrieve",
    "search",
}


class KnowledgePermissionHelper:
    """Central Knowledge permission helper for safe candidate resolution.

    Routers, Builder preflight, and runtime code should consume this helper output
    instead of joining team permissions, source-owned grants, and source ACL
    provenance directly.
    """

    def __init__(
        self,
        db: Session,
        *,
        user_id: uuid.UUID,
        organization_id: uuid.UUID,
        requester_subject_type: str = "user",
        requester_subject_id: uuid.UUID | None = None,
        evaluation_time: datetime | None = None,
    ) -> None:
        if evaluation_time is not None:
            if (
                not isinstance(evaluation_time, datetime)
                or evaluation_time.tzinfo is None
                or evaluation_time.utcoffset() is None
            ):
                raise ValueError("knowledge_permission_evaluation_time_invalid")
            evaluation_time = evaluation_time.astimezone(timezone.utc)
        self.db = db
        self.user_id = user_id
        self.organization_id = organization_id
        self.requester_subject_type = requester_subject_type
        self.requester_subject_id = requester_subject_id or user_id
        self._evaluation_time = evaluation_time
        self._team_ids_cache: set[uuid.UUID] | None = None
        self._bulk_manual_auth_state_by_kb_id: dict[uuid.UUID, str] | None = None
        self._bulk_source_policy_allowed_kb_ids: set[uuid.UUID] | None = None
        self._bulk_source_authorization_by_key: (
            dict[tuple[uuid.UUID, uuid.UUID | None], SourceAuthorizationProvenance]
            | None
        ) = None

    def evaluate_collection_action(
        self,
        collection: KnowledgeCollection,
        action: str,
        *,
        include_archived: bool = False,
    ) -> KnowledgePermissionDecision:
        if action not in COLLECTION_PERMISSION_ACTIONS:
            return self._denied(
                reason_code="permission.invalid_action",
                external_reason_code="resource.hidden",
            )
        if not self._collection_in_scope(
            collection,
            include_archived=include_archived,
        ):
            return self._denied(reason_code="resource.hidden")

        organization_auth_state = self._organization_auth_state()
        if organization_auth_state == AUTH_STATE_MANAGER:
            return self._allowed(
                effective_auth_state=AUTH_STATE_MANAGER,
                safe_metadata=self._collection_safe_metadata(collection),
            )
        if organization_auth_state != ORGANIZATION_AUTH_MEMBER:
            return self._denied(reason_code="resource.hidden")

        if not self._collection_has_action(collection.id, action):
            return self._denied(
                resource_visibility="visible",
                reason_code=f"collection_{action}_denied",
                external_reason_code="permission.denied",
                safe_metadata=self._collection_safe_metadata(collection),
            )

        return self._allowed(
            effective_auth_state=AUTH_STATE_OPERATOR,
            safe_metadata=self._collection_safe_metadata(collection),
        )

    def bulk_evaluate_collection_action(
        self,
        collections: Iterable[KnowledgeCollection],
        action: str,
        *,
        include_archived: bool = False,
    ) -> dict[uuid.UUID, KnowledgePermissionDecision]:
        collection_list = list(collections)
        if not collection_list:
            return {}

        # Lightweight test/fake helpers historically provide only the single-row
        # hook. Production sessions always take the bounded bulk path below.
        if self.db is None:
            return {
                collection.id: self.evaluate_collection_action(
                    collection,
                    action,
                    include_archived=include_archived,
                )
                for collection in collection_list
            }

        if action not in COLLECTION_PERMISSION_ACTIONS:
            return {
                collection.id: self._denied(
                    reason_code="permission.invalid_action",
                    external_reason_code="resource.hidden",
                )
                for collection in collection_list
            }

        organization_auth_state = self._organization_auth_state()
        in_scope_by_id = {
            collection.id: self._collection_in_scope(
                collection,
                include_archived=include_archived,
            )
            for collection in collection_list
        }
        allowed_collection_ids: set[uuid.UUID] = set()
        if organization_auth_state == ORGANIZATION_AUTH_MEMBER:
            scoped_collection_ids = [
                collection.id
                for collection in collection_list
                if in_scope_by_id[collection.id]
            ]
            if scoped_collection_ids:
                allowed_collection_ids = self._bulk_collection_action_ids(
                    scoped_collection_ids,
                    action,
                )

        decisions: dict[uuid.UUID, KnowledgePermissionDecision] = {}
        for collection in collection_list:
            if not in_scope_by_id[collection.id]:
                decisions[collection.id] = self._denied(reason_code="resource.hidden")
            elif organization_auth_state == AUTH_STATE_MANAGER:
                decisions[collection.id] = self._allowed(
                    effective_auth_state=AUTH_STATE_MANAGER,
                    safe_metadata=self._collection_safe_metadata(collection),
                )
            elif organization_auth_state != ORGANIZATION_AUTH_MEMBER:
                decisions[collection.id] = self._denied(reason_code="resource.hidden")
            elif collection.id not in allowed_collection_ids:
                decisions[collection.id] = self._denied(
                    resource_visibility="visible",
                    reason_code=f"collection_{action}_denied",
                    external_reason_code="permission.denied",
                    safe_metadata=self._collection_safe_metadata(collection),
                )
            else:
                decisions[collection.id] = self._allowed(
                    effective_auth_state=AUTH_STATE_OPERATOR,
                    safe_metadata=self._collection_safe_metadata(collection),
                )
        return decisions

    def scope_collection_query_for_action(self, query: Any, action: str) -> Any:
        """Apply effective Collection authorization before ordering and limits."""
        if action not in COLLECTION_PERMISSION_ACTIONS:
            return query.filter(false())

        organization_auth_state = self._organization_auth_state()
        if organization_auth_state == AUTH_STATE_MANAGER:
            return query
        if organization_auth_state != ORGANIZATION_AUTH_MEMBER:
            return query.filter(false())

        direct_permission_exists = exists().where(
            and_(
                UserKnowledgeCollectionPermission.knowledge_collection_id
                == KnowledgeCollection.id,
                UserKnowledgeCollectionPermission.user_id == self.user_id,
                UserKnowledgeCollectionPermission.grantee_organization_id
                == self.organization_id,
                UserKnowledgeCollectionPermission.permission_action == action,
            )
        )
        team_permission_exists = exists().where(
            and_(
                TeamKnowledgeCollectionPermission.knowledge_collection_id
                == KnowledgeCollection.id,
                TeamKnowledgeCollectionPermission.grantee_organization_id
                == self.organization_id,
                TeamKnowledgeCollectionPermission.permission_action == action,
                TeamMembership.team_id
                == TeamKnowledgeCollectionPermission.team_id,
                TeamMembership.user_id == self.user_id,
                TeamMembership.grantee_organization_id == self.organization_id,
                Team.organization_id == self.organization_id,
                Team.id == TeamKnowledgeCollectionPermission.team_id,
                Team.is_active.is_(True),
            )
        )
        return query.filter(or_(direct_permission_exists, team_permission_exists))

    def evaluate_kb_use(self, kb: KnowledgeBase) -> KnowledgePermissionDecision:
        if not self._kb_in_scope(kb):
            return self._denied(reason_code="resource.hidden")

        # KB use grant와 source ACL/requester authorization은 별도 gate다.
        # source-managed KB에서는 use grant가 있어도 source gate를 통과해야 한다.
        effective_auth_state = self._effective_kb_use_auth_state(kb)
        if not knowledge_base_auth_state_allows(effective_auth_state, "use"):
            return self._denied(
                resource_visibility="visible",
                effective_auth_state=effective_auth_state,
                reason_code="kb_use_denied",
                external_reason_code="permission.denied",
                safe_metadata=self._kb_safe_metadata(kb),
            )

        if not self._is_source_managed(kb):
            return self._allowed(
                effective_auth_state=effective_auth_state,
                safe_metadata=self._kb_safe_metadata(kb),
            )

        source_decision = self._evaluate_source_authorization(kb)
        if not source_decision.allowed:
            source_decision.effective_auth_state = effective_auth_state
            # Hidden source path에서는 내부 KB id를 safe metadata에 싣지 않는다.
            # 허용된 candidate에만 KB id가 포함된다.
            source_decision.safe_metadata = {"source_managed": True}
            return source_decision

        return self._allowed(
            effective_auth_state=effective_auth_state,
            source_acl_state=source_decision.source_acl_state,
            requester_source_authorization=(
                source_decision.requester_source_authorization
            ),
            freshness_epoch=source_decision.freshness_epoch,
            safe_metadata=self._kb_safe_metadata(kb),
        )

    def evaluate_kb_action(
        self,
        kb: KnowledgeBase,
        action: str,
        *,
        include_archived: bool = False,
    ) -> KnowledgePermissionDecision:
        if action == "use":
            return self.evaluate_kb_use(kb)
        if action not in {"read", "write", "content_read", "manage"}:
            return self._denied(reason_code="permission.invalid_action")
        if not self._kb_in_scope(kb, include_archived=include_archived):
            return self._denied(reason_code="resource.hidden")

        auth_state = (
            get_effective_knowledge_base_auth_state(
                self.db,
                self.user_id,
                kb.id,
                organization_id=self.organization_id,
                include_archived=True,
            )
            if include_archived
            else self._manual_kb_auth_state(kb)
        )
        if auth_state == AUTH_STATE_NONE:
            return self._denied(reason_code="resource.hidden")
        if not knowledge_base_auth_state_allows(auth_state, action):
            return self._denied(
                resource_visibility="visible",
                effective_auth_state=auth_state,
                reason_code=f"kb_{action}_denied",
                external_reason_code="permission.denied",
                safe_metadata=self._kb_safe_metadata(kb),
            )
        if action == "content_read" and self._is_source_managed(kb):
            return self._denied(
                resource_visibility="visible",
                effective_auth_state=auth_state,
                reason_code="source_content_policy_required",
                external_reason_code="permission.denied",
                safe_metadata={"source_managed": True},
            )
        return self._allowed(
            effective_auth_state=auth_state,
            safe_metadata=self._kb_safe_metadata(kb),
        )

    def bulk_evaluate_kb_action(
        self,
        kbs: Iterable[KnowledgeBase],
        action: str,
    ) -> dict[uuid.UUID, KnowledgePermissionDecision]:
        kb_list = list(kbs)
        if not kb_list:
            return {}
        if action == "use":
            return self.bulk_evaluate_kb_use(kb_list)
        previous = self._bulk_manual_auth_state_by_kb_id
        try:
            self._bulk_manual_auth_state_by_kb_id = self._bulk_manual_kb_auth_states(
                kb_list
            )
            return {kb.id: self.evaluate_kb_action(kb, action) for kb in kb_list}
        finally:
            self._bulk_manual_auth_state_by_kb_id = previous

    def bulk_evaluate_kb_use(
        self,
        kbs: Iterable[KnowledgeBase],
    ) -> dict[uuid.UUID, KnowledgePermissionDecision]:
        kb_list = list(kbs)
        if not kb_list:
            return {}

        previous_context = (
            self._bulk_manual_auth_state_by_kb_id,
            self._bulk_source_policy_allowed_kb_ids,
            self._bulk_source_authorization_by_key,
        )
        try:
            self._prepare_bulk_kb_context(kb_list)
            return {kb.id: self.evaluate_kb_use(kb) for kb in kb_list}
        finally:
            (
                self._bulk_manual_auth_state_by_kb_id,
                self._bulk_source_policy_allowed_kb_ids,
                self._bulk_source_authorization_by_key,
            ) = previous_context

    def _effective_kb_use_auth_state(self, kb: KnowledgeBase) -> str:
        manual_auth_state = self._manual_kb_auth_state(kb)
        source_policy_auth_state = self._source_policy_kb_use_auth_state(kb)
        return stronger_resource_auth_state(manual_auth_state, source_policy_auth_state)

    def _manual_kb_auth_state(self, kb: KnowledgeBase) -> str:
        if self._bulk_manual_auth_state_by_kb_id is not None:
            return self._bulk_manual_auth_state_by_kb_id.get(kb.id, AUTH_STATE_NONE)
        return get_effective_knowledge_base_auth_state(
            self.db,
            self.user_id,
            kb.id,
            organization_id=self.organization_id,
        )

    def _source_policy_kb_use_auth_state(self, kb: KnowledgeBase) -> str:
        if self._bulk_source_policy_allowed_kb_ids is not None:
            return (
                AUTH_STATE_OPERATOR
                if kb.id in self._bulk_source_policy_allowed_kb_ids
                else AUTH_STATE_NONE
            )
        if self._organization_auth_state() == AUTH_STATE_MANAGER:
            return AUTH_STATE_MANAGER

        # Source policy grant는 KB use 후보일 뿐 source ACL freshness gate를
        # 우회하지 않는다. 최종 차단 여부는 _evaluate_source_authorization()에서 판정한다.
        grants = self._active_source_policy_grants(kb)
        return AUTH_STATE_OPERATOR if grants else AUTH_STATE_NONE

    def _active_source_policy_grants(
        self, kb: KnowledgeBase
    ) -> list[SourcePolicyKBUseGrant]:
        if not self._can_consume_source_policy_grants():
            return []
        now = self._now()
        subject_filters = [
            and_(
                SourcePolicyKBUseGrant.subject_type == "organization",
                SourcePolicyKBUseGrant.subject_id == self.organization_id,
            ),
            and_(
                SourcePolicyKBUseGrant.subject_type == "user",
                SourcePolicyKBUseGrant.subject_id == self.user_id,
            ),
        ]
        team_ids = self._active_team_ids()
        if team_ids:
            subject_filters.append(
                and_(
                    SourcePolicyKBUseGrant.subject_type == "team",
                    SourcePolicyKBUseGrant.subject_id.in_(team_ids),
                )
            )

        return (
            self.db.query(SourcePolicyKBUseGrant)
            .filter(
                SourcePolicyKBUseGrant.organization_id == self.organization_id,
                SourcePolicyKBUseGrant.knowledge_base_id == kb.id,
                or_(
                    SourcePolicyKBUseGrant.source_identity_id.is_(None),
                    SourcePolicyKBUseGrant.source_identity_id == kb.source_identity_id,
                ),
                SourcePolicyKBUseGrant.permission_action == "use",
                SourcePolicyKBUseGrant.status == "active",
                or_(
                    SourcePolicyKBUseGrant.expires_at.is_(None),
                    SourcePolicyKBUseGrant.expires_at > now,
                ),
                or_(*subject_filters),
            )
            .all()
        )

    def _evaluate_source_authorization(
        self, kb: KnowledgeBase
    ) -> KnowledgePermissionDecision:
        # source_acl_state는 freshness/mapping 상태만 표현한다.
        # requester가 원본 source에서 볼 수 없는지는 requester_source_authorization으로 분리한다.
        provenance = self._latest_source_authorization(kb)
        if provenance is None:
            return self._source_denied(
                source_acl_state="unverified",
                requester_source_authorization="unknown",
                reason_code="source_acl.unverified",
            )

        freshness_epoch = int(provenance.freshness_epoch or 0)
        source_acl_state = provenance.source_acl_state
        requester_authorization = provenance.requester_source_authorization

        if self._is_expired(provenance.freshness_expires_at):
            return self._source_denied(
                source_acl_state="stale",
                requester_source_authorization=requester_authorization,
                freshness_epoch=freshness_epoch,
                reason_code="source_acl.stale",
            )
        if source_acl_state in SOURCE_ACL_FAIL_STATES:
            return self._source_denied(
                source_acl_state=source_acl_state,
                requester_source_authorization=requester_authorization,
                freshness_epoch=freshness_epoch,
                reason_code=f"source_acl.{source_acl_state}",
            )
        if source_acl_state != SOURCE_ACL_PASS_STATE:
            return self._source_denied(
                source_acl_state="unverified",
                requester_source_authorization=requester_authorization,
                freshness_epoch=freshness_epoch,
                reason_code="source_acl.unverified",
            )
        if requester_authorization != "allowed":
            return self._source_denied(
                source_acl_state=SOURCE_ACL_PASS_STATE,
                requester_source_authorization=requester_authorization or "unknown",
                freshness_epoch=freshness_epoch,
                reason_code=(
                    "source_authorization.denied"
                    if requester_authorization == "denied"
                    else "source_authorization.unknown"
                ),
            )

        source_permission_action = getattr(
            provenance,
            "source_permission_action",
            None,
        )
        if (
            not isinstance(source_permission_action, str)
            or source_permission_action.strip().lower()
            not in SOURCE_RETRIEVAL_PERMISSION_ACTIONS
        ):
            return self._source_denied(
                source_acl_state=SOURCE_ACL_PASS_STATE,
                requester_source_authorization="allowed",
                freshness_epoch=freshness_epoch,
                reason_code="source_authorization.operation_unverified",
            )

        return self._allowed(
            source_acl_state=SOURCE_ACL_PASS_STATE,
            requester_source_authorization="allowed",
            freshness_epoch=freshness_epoch,
        )

    def _latest_source_authorization(
        self,
        kb: KnowledgeBase,
    ) -> SourceAuthorizationProvenance | None:
        if self._bulk_source_authorization_by_key is not None:
            return self._bulk_source_authorization_by_key.get(
                (kb.id, kb.source_identity_id)
            )
        query = self.db.query(SourceAuthorizationProvenance).filter(
            SourceAuthorizationProvenance.organization_id == self.organization_id,
            SourceAuthorizationProvenance.knowledge_base_id == kb.id,
            SourceAuthorizationProvenance.requester_subject_type
            == self.requester_subject_type,
            SourceAuthorizationProvenance.requester_subject_id
            == self.requester_subject_id,
            SourceAuthorizationProvenance.status == "active",
        )
        query = query.options(
            load_only(
                SourceAuthorizationProvenance.knowledge_base_id,
                SourceAuthorizationProvenance.source_identity_id,
                SourceAuthorizationProvenance.source_acl_state,
                SourceAuthorizationProvenance.requester_source_authorization,
                SourceAuthorizationProvenance.source_permission_action,
                SourceAuthorizationProvenance.freshness_epoch,
                SourceAuthorizationProvenance.freshness_expires_at,
            )
        )
        if kb.source_identity_id is not None:
            query = query.filter(
                SourceAuthorizationProvenance.source_identity_id
                == kb.source_identity_id
            )
        return (
            query.order_by(
                SourceAuthorizationProvenance.freshness_epoch.desc(),
                SourceAuthorizationProvenance.updated_at.desc(),
            )
            .first()
        )

    def _prepare_bulk_kb_context(self, kbs: list[KnowledgeBase]) -> None:
        """여러 KB 판정에 필요한 입력을 선조회해 N+1 permission query를 피한다."""
        kb_ids = [kb.id for kb in kbs if kb is not None]
        if not kb_ids:
            self._bulk_manual_auth_state_by_kb_id = {}
            self._bulk_source_policy_allowed_kb_ids = set()
            self._bulk_source_authorization_by_key = {}
            return

        self._bulk_manual_auth_state_by_kb_id = self._bulk_manual_kb_auth_states(kbs)
        self._bulk_source_policy_allowed_kb_ids = self._bulk_source_policy_kb_ids(kbs)
        self._bulk_source_authorization_by_key = (
            self._bulk_latest_source_authorization_by_key(kbs)
        )

    def _bulk_manual_kb_auth_states(
        self, kbs: list[KnowledgeBase]
    ) -> dict[uuid.UUID, str]:
        kb_ids = [kb.id for kb in kbs if kb is not None]
        states = {kb_id: AUTH_STATE_NONE for kb_id in kb_ids}
        organization_auth_state = self._organization_auth_state()
        if organization_auth_state == AUTH_STATE_MANAGER:
            return {kb_id: AUTH_STATE_MANAGER for kb_id in kb_ids}
        if organization_auth_state != ORGANIZATION_AUTH_MEMBER:
            return states

        rows = (
            self.db.query(
                TeamKnowledgePermission.knowledge_base_id,
                TeamKnowledgePermission.auth_state,
            )
            .join(TeamMembership, TeamMembership.team_id == TeamKnowledgePermission.team_id)
            .join(Team, Team.id == TeamKnowledgePermission.team_id)
            .filter(
                TeamMembership.user_id == self.user_id,
                TeamKnowledgePermission.knowledge_base_id.in_(kb_ids),
                Team.is_active.is_(True),
                TeamMembership.grantee_organization_id == self.organization_id,
                TeamKnowledgePermission.grantee_organization_id == self.organization_id,
                TeamMembership.grantee_organization_id
                == TeamKnowledgePermission.grantee_organization_id,
                Team.organization_id == self.organization_id,
            )
            .all()
        )
        for kb_id, auth_state in rows:
            states[kb_id] = stronger_resource_auth_state(states[kb_id], auth_state)

        direct_rows = (
            self.db.query(
                UserKnowledgePermission.knowledge_base_id,
                UserKnowledgePermission.auth_state,
            )
            .filter(
                UserKnowledgePermission.user_id == self.user_id,
                UserKnowledgePermission.knowledge_base_id.in_(kb_ids),
                UserKnowledgePermission.grantee_organization_id
                == self.organization_id,
            )
            .all()
        )
        for kb_id, auth_state in direct_rows:
            states[kb_id] = stronger_resource_auth_state(states[kb_id], auth_state)
        return states

    def _bulk_source_policy_kb_ids(self, kbs: list[KnowledgeBase]) -> set[uuid.UUID]:
        if self._organization_auth_state() == AUTH_STATE_MANAGER:
            return {kb.id for kb in kbs if kb is not None}
        if not self._can_consume_source_policy_grants():
            return set()

        kb_source_identity_by_id = {
            kb.id: getattr(kb, "source_identity_id", None)
            for kb in kbs
            if kb is not None
        }
        if not kb_source_identity_by_id:
            return set()

        now = self._now()
        subject_filters = [
            and_(
                SourcePolicyKBUseGrant.subject_type == "organization",
                SourcePolicyKBUseGrant.subject_id == self.organization_id,
            ),
            and_(
                SourcePolicyKBUseGrant.subject_type == "user",
                SourcePolicyKBUseGrant.subject_id == self.user_id,
            ),
        ]
        team_ids = self._active_team_ids()
        if team_ids:
            subject_filters.append(
                and_(
                    SourcePolicyKBUseGrant.subject_type == "team",
                    SourcePolicyKBUseGrant.subject_id.in_(team_ids),
                )
            )

        rows = (
            self.db.query(
                SourcePolicyKBUseGrant.knowledge_base_id,
                SourcePolicyKBUseGrant.source_identity_id,
            )
            .filter(
                SourcePolicyKBUseGrant.organization_id == self.organization_id,
                SourcePolicyKBUseGrant.knowledge_base_id.in_(
                    list(kb_source_identity_by_id)
                ),
                SourcePolicyKBUseGrant.permission_action == "use",
                SourcePolicyKBUseGrant.status == "active",
                or_(
                    SourcePolicyKBUseGrant.expires_at.is_(None),
                    SourcePolicyKBUseGrant.expires_at > now,
                ),
                or_(*subject_filters),
            )
            .all()
        )

        allowed_kb_ids: set[uuid.UUID] = set()
        for kb_id, grant_source_identity_id in rows:
            kb_source_identity_id = kb_source_identity_by_id.get(kb_id)
            if (
                grant_source_identity_id is None
                or grant_source_identity_id == kb_source_identity_id
            ):
                allowed_kb_ids.add(kb_id)
        return allowed_kb_ids

    def _bulk_latest_source_authorization_by_key(
        self, kbs: list[KnowledgeBase]
    ) -> dict[tuple[uuid.UUID, uuid.UUID | None], SourceAuthorizationProvenance]:
        source_managed = [
            kb for kb in kbs if kb is not None and self._is_source_managed(kb)
        ]
        if not source_managed:
            return {}

        rows = (
            self.db.query(SourceAuthorizationProvenance)
            .options(
                load_only(
                    SourceAuthorizationProvenance.knowledge_base_id,
                    SourceAuthorizationProvenance.source_identity_id,
                    SourceAuthorizationProvenance.source_acl_state,
                    SourceAuthorizationProvenance.requester_source_authorization,
                    SourceAuthorizationProvenance.source_permission_action,
                    SourceAuthorizationProvenance.freshness_epoch,
                    SourceAuthorizationProvenance.freshness_expires_at,
                )
            )
            .filter(
                SourceAuthorizationProvenance.organization_id == self.organization_id,
                SourceAuthorizationProvenance.knowledge_base_id.in_(
                    [kb.id for kb in source_managed]
                ),
                SourceAuthorizationProvenance.requester_subject_type
                == self.requester_subject_type,
                SourceAuthorizationProvenance.requester_subject_id
                == self.requester_subject_id,
                SourceAuthorizationProvenance.status == "active",
                SourceAuthorizationProvenance.source_identity_id.in_(
                    [kb.source_identity_id for kb in source_managed]
                ),
            )
            .order_by(
                SourceAuthorizationProvenance.knowledge_base_id,
                SourceAuthorizationProvenance.source_identity_id,
                SourceAuthorizationProvenance.freshness_epoch.desc(),
                SourceAuthorizationProvenance.updated_at.desc(),
            )
            .all()
        )

        latest_by_key: dict[
            tuple[uuid.UUID, uuid.UUID | None], SourceAuthorizationProvenance
        ] = {}
        for row in rows:
            key = (row.knowledge_base_id, row.source_identity_id)
            latest_by_key.setdefault(key, row)
        return latest_by_key

    def _collection_has_action(
        self,
        collection_id: uuid.UUID,
        action: str,
    ) -> bool:
        team_query = (
            self.db.query(TeamKnowledgeCollectionPermission.id)
            .join(
                TeamMembership,
                TeamMembership.team_id == TeamKnowledgeCollectionPermission.team_id,
            )
            .join(Team, Team.id == TeamKnowledgeCollectionPermission.team_id)
            .filter(
                TeamMembership.user_id == self.user_id,
                TeamMembership.grantee_organization_id == self.organization_id,
                TeamKnowledgeCollectionPermission.grantee_organization_id
                == self.organization_id,
                TeamMembership.grantee_organization_id
                == TeamKnowledgeCollectionPermission.grantee_organization_id,
                Team.organization_id == self.organization_id,
                Team.is_active.is_(True),
                TeamKnowledgeCollectionPermission.knowledge_collection_id
                == collection_id,
                TeamKnowledgeCollectionPermission.permission_action == action,
            )
        )
        if team_query.first() is not None:
            return True

        return (
            self.db.query(UserKnowledgeCollectionPermission.id)
            .filter(
                UserKnowledgeCollectionPermission.user_id == self.user_id,
                UserKnowledgeCollectionPermission.grantee_organization_id
                == self.organization_id,
                UserKnowledgeCollectionPermission.knowledge_collection_id
                == collection_id,
                UserKnowledgeCollectionPermission.permission_action == action,
            )
            .first()
            is not None
        )

    def _bulk_collection_action_ids(
        self,
        collection_ids: Iterable[uuid.UUID],
        action: str,
    ) -> set[uuid.UUID]:
        bounded_collection_ids = list(dict.fromkeys(collection_ids))
        if not bounded_collection_ids:
            return set()

        team_rows = (
            self.db.query(
                TeamKnowledgeCollectionPermission.knowledge_collection_id,
            )
            .join(
                TeamMembership,
                TeamMembership.team_id == TeamKnowledgeCollectionPermission.team_id,
            )
            .join(Team, Team.id == TeamKnowledgeCollectionPermission.team_id)
            .filter(
                TeamMembership.user_id == self.user_id,
                TeamMembership.grantee_organization_id == self.organization_id,
                TeamKnowledgeCollectionPermission.grantee_organization_id
                == self.organization_id,
                TeamMembership.grantee_organization_id
                == TeamKnowledgeCollectionPermission.grantee_organization_id,
                Team.organization_id == self.organization_id,
                Team.is_active.is_(True),
                TeamKnowledgeCollectionPermission.knowledge_collection_id.in_(
                    bounded_collection_ids
                ),
                TeamKnowledgeCollectionPermission.permission_action == action,
            )
            .all()
        )
        direct_rows = (
            self.db.query(
                UserKnowledgeCollectionPermission.knowledge_collection_id,
            )
            .filter(
                UserKnowledgeCollectionPermission.user_id == self.user_id,
                UserKnowledgeCollectionPermission.grantee_organization_id
                == self.organization_id,
                UserKnowledgeCollectionPermission.knowledge_collection_id.in_(
                    bounded_collection_ids
                ),
                UserKnowledgeCollectionPermission.permission_action == action,
            )
            .all()
        )
        return {row[0] for row in [*team_rows, *direct_rows]}

    def _active_team_ids(self) -> set[uuid.UUID]:
        if self._team_ids_cache is not None:
            return self._team_ids_cache
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
        self._team_ids_cache = {row[0] for row in rows}
        return self._team_ids_cache

    def _organization_auth_state(self) -> str:
        return get_organization_auth_state(
            self.db,
            self.user_id,
            self.organization_id,
        )

    def _can_consume_source_policy_grants(self) -> bool:
        # Source-policy grant는 조직 active member에게만 적용한다.
        # legacy owner/manager fallback은 위의 manager override 경로에서만 처리한다.
        return has_active_organization_membership(
            self.db,
            self.user_id,
            self.organization_id,
        )

    def _collection_in_scope(
        self,
        collection: KnowledgeCollection,
        *,
        include_archived: bool = False,
    ) -> bool:
        lifecycle_state = getattr(collection, "lifecycle_state", "active")
        return (
            collection is not None
            and collection.organization_id == self.organization_id
            and lifecycle_state != "deleted"
            and (lifecycle_state == "active" or include_archived)
        )

    def _kb_in_scope(
        self,
        kb: KnowledgeBase,
        *,
        include_archived: bool = False,
    ) -> bool:
        lifecycle_state = getattr(kb, "lifecycle_state", "active")
        return (
            kb is not None
            and kb.organization_id == self.organization_id
            and lifecycle_state != "deleted"
            and (lifecycle_state == "active" or include_archived)
        )

    def _is_source_managed(self, kb: KnowledgeBase) -> bool:
        return kb.source_identity_id is not None

    def _is_expired(self, expires_at: datetime | None) -> bool:
        if expires_at is None:
            return False
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return expires_at <= self._now()

    def _now(self) -> datetime:
        return self._evaluation_time or datetime.now(timezone.utc)

    def _allowed(
        self,
        *,
        effective_auth_state: str = AUTH_STATE_NONE,
        source_acl_state: str = "not_source_managed",
        requester_source_authorization: str = "not_applicable",
        freshness_epoch: int = 0,
        safe_metadata: dict[str, Any] | None = None,
    ) -> KnowledgePermissionDecision:
        return KnowledgePermissionDecision(
            allowed=True,
            resource_visibility="visible",
            effective_auth_state=effective_auth_state,
            source_acl_state=source_acl_state,
            requester_source_authorization=requester_source_authorization,
            freshness_epoch=freshness_epoch,
            reason_code="allowed",
            external_reason_code="allowed",
            safe_metadata=safe_metadata or {},
        )

    def _denied(
        self,
        *,
        resource_visibility: str = "resource_hidden",
        effective_auth_state: str = AUTH_STATE_NONE,
        source_acl_state: str = "not_source_managed",
        requester_source_authorization: str = "not_applicable",
        freshness_epoch: int = 0,
        reason_code: str,
        external_reason_code: str = "resource.hidden",
        safe_metadata: dict[str, Any] | None = None,
    ) -> KnowledgePermissionDecision:
        return KnowledgePermissionDecision(
            allowed=False,
            resource_visibility=resource_visibility,
            effective_auth_state=effective_auth_state,
            source_acl_state=source_acl_state,
            requester_source_authorization=requester_source_authorization,
            freshness_epoch=freshness_epoch,
            reason_code=reason_code,
            external_reason_code=external_reason_code,
            safe_metadata=safe_metadata or {},
        )

    def _source_denied(
        self,
        *,
        source_acl_state: str,
        requester_source_authorization: str,
        reason_code: str,
        freshness_epoch: int = 0,
    ) -> KnowledgePermissionDecision:
        return self._denied(
            source_acl_state=source_acl_state,
            requester_source_authorization=requester_source_authorization,
            freshness_epoch=freshness_epoch,
            reason_code=reason_code,
            external_reason_code="resource.hidden",
        )

    def _collection_safe_metadata(self, collection: KnowledgeCollection) -> dict[str, Any]:
        metadata = dict(getattr(collection, "safe_metadata", None) or {})
        metadata.update(
            {
                "collection_id": str(collection.id),
                "sync_state": getattr(collection, "sync_state", None),
                "lifecycle_state": getattr(collection, "lifecycle_state", None),
                "is_system_managed": bool(
                    getattr(collection, "is_system_managed", False)
                ),
            }
        )
        return metadata

    def _kb_safe_metadata(self, kb: KnowledgeBase) -> dict[str, Any]:
        return {
            "knowledge_base_id": str(kb.id),
            "sync_state": getattr(kb, "sync_state", None),
            "lifecycle_state": getattr(kb, "lifecycle_state", None),
            "source_managed": self._is_source_managed(kb),
        }
