from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy.orm import Session

from apps.gateway.application.access_management.models import (
    AppCreationPermissionSnapshot,
    DirectPermissionSnapshot,
    InheritedResourceCounts,
    MemberSnapshot,
    Page,
    ResourceAccessProjection,
    ResourceType,
    TeamMembershipProjection,
    TeamPermissionSource,
)
from apps.gateway.services.resource_permission_registry import resource_permission_spec
from apps.shared.db.models.app import App
from apps.shared.db.models.knowledge import KnowledgeBase
from apps.shared.db.models.llm import LLMCredential
from apps.shared.db.models.mail_credential import MailCredential
from apps.shared.db.models.organization_membership import (
    ORGANIZATION_AUTH_MANAGER,
    ORGANIZATION_MEMBERSHIP_ACTIVE,
    OrganizationMembership,
)
from apps.shared.db.models.team import (
    Team,
    TeamKnowledgePermission,
    TeamLLMPermission,
    TeamMailCredentialPermission,
    TeamMembership,
    TeamWorkflowPermission,
    UserKnowledgePermission,
    UserLLMPermission,
    UserMailCredentialPermission,
    UserWorkflowPermission,
)
from apps.shared.db.models.user import User
from apps.shared.db.models.user_app_creation_permission import UserAppCreationPermission
from apps.shared.db.models.workflow import Workflow
from apps.shared.services.permissions import (
    has_organization_manager_permission,
    has_organization_scope_access,
)

_OPERATIONAL_STATES = ("viewer", "operator", "builder", "manager")
_DIRECT_MODELS_AND_RESOURCES = (
    (UserWorkflowPermission, Workflow, "workflow_id"),
    (UserKnowledgePermission, KnowledgeBase, "knowledge_base_id"),
    (UserLLMPermission, LLMCredential, "llm_credential_id"),
    (UserMailCredentialPermission, MailCredential, "mail_credential_id"),
)
_TEAM_MODELS_AND_COLUMNS = (
    ("workflow", TeamWorkflowPermission, "workflow_id", Workflow),
    (
        "knowledge_base",
        TeamKnowledgePermission,
        "knowledge_base_id",
        KnowledgeBase,
    ),
    (
        "llm_credential",
        TeamLLMPermission,
        "llm_credential_id",
        LLMCredential,
    ),
    (
        "mail_credential",
        TeamMailCredentialPermission,
        "mail_credential_id",
        MailCredential,
    ),
)


class SqlAlchemyAccessManagementQueryAdapter:
    def __init__(self, db: Session) -> None:
        self.db = db

    def is_organization_manager(
        self,
        actor_id: uuid.UUID,
        organization_id: uuid.UUID,
    ) -> bool:
        return has_organization_manager_permission(
            self.db,
            actor_id,
            organization_id,
        )

    def has_organization_scope(
        self,
        actor_id: uuid.UUID,
        organization_id: uuid.UUID,
    ) -> bool:
        return has_organization_scope_access(
            self.db,
            actor_id,
            organization_id,
        )

    def get_member(
        self,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> MemberSnapshot | None:
        row = (
            self.db.query(OrganizationMembership, User)
            .join(User, User.id == OrganizationMembership.user_id)
            .filter(
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.user_id == user_id,
                OrganizationMembership.membership_state.in_(("active", "suspended")),
            )
            .first()
        )
        if row is None:
            return None
        membership, user = row
        return _member_snapshot(membership, user)

    def count_active_managers(self, organization_id: uuid.UUID) -> int:
        return (
            self.db.query(OrganizationMembership)
            .join(User, User.id == OrganizationMembership.user_id)
            .filter(
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.membership_state
                == ORGANIZATION_MEMBERSHIP_ACTIVE,
                OrganizationMembership.organization_auth_state
                == ORGANIZATION_AUTH_MANAGER,
                User.deactivated_at.is_(None),
            )
            .count()
        )

    def count_team_memberships(
        self,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> int:
        return (
            self.db.query(TeamMembership)
            .filter(
                TeamMembership.grantee_organization_id == organization_id,
                TeamMembership.user_id == user_id,
            )
            .count()
        )

    def get_app_creation_permission(
        self,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> AppCreationPermissionSnapshot | None:
        row = (
            self.db.query(UserAppCreationPermission)
            .filter(
                UserAppCreationPermission.grantee_organization_id == organization_id,
                UserAppCreationPermission.user_id == user_id,
            )
            .first()
        )
        return _app_permission_snapshot(row) if row is not None else None

    def count_direct_permissions(
        self,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> int:
        return sum(
            self._count_operational_direct_permissions(
                organization_id,
                user_id,
                model,
                resource_model,
                resource_column,
            )
            for model, resource_model, resource_column in _DIRECT_MODELS_AND_RESOURCES
        )

    def count_team_permission_sources(
        self,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> int:
        sources: set[tuple[str, uuid.UUID, uuid.UUID]] = set()
        for (
            resource_type,
            model,
            resource_column,
            resource_model,
        ) in _TEAM_MODELS_AND_COLUMNS:
            query = (
                self.db.query(model)
                .join(
                    TeamMembership,
                    (TeamMembership.team_id == model.team_id)
                    & (
                        TeamMembership.grantee_organization_id
                        == model.grantee_organization_id
                    ),
                )
                .join(Team, Team.id == model.team_id)
                .join(
                    resource_model,
                    (resource_model.id == getattr(model, resource_column))
                    & (resource_model.organization_id == model.grantee_organization_id),
                )
            )
            query = _filter_operational_resource(query, resource_model)
            rows = query.filter(
                model.grantee_organization_id == organization_id,
                TeamMembership.user_id == user_id,
                Team.is_active.is_(True),
                model.auth_state.in_(_OPERATIONAL_STATES),
            ).all()
            sources.update(
                (resource_type, getattr(row, resource_column), row.team_id)
                for row in rows
            )
        return len(sources)

    def list_team_memberships(
        self,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        *,
        team_id: uuid.UUID | None,
        page: int,
        limit: int,
    ) -> Page[TeamMembershipProjection]:
        base = (
            self.db.query(TeamMembership, Team)
            .join(
                Team,
                (Team.id == TeamMembership.team_id)
                & (Team.organization_id == TeamMembership.grantee_organization_id),
            )
            .filter(
                TeamMembership.grantee_organization_id == organization_id,
                TeamMembership.user_id == user_id,
            )
        )
        if team_id is not None:
            base = base.filter(Team.id == team_id)
        total = base.count()
        rows = (
            base.order_by(Team.name.asc(), Team.id.asc())
            .offset((page - 1) * limit)
            .limit(limit)
            .all()
        )
        active_team_ids = [team.id for _, team in rows if team.is_active]
        counts = self._team_resource_counts(organization_id, active_team_ids)
        return Page(
            total=total,
            items=tuple(
                TeamMembershipProjection(
                    team_membership_id=membership.id,
                    team_id=team.id,
                    name=team.name,
                    is_active=team.is_active,
                    assigned_at=membership.assigned_at,
                    inherited_resource_counts=(
                        counts.get(team.id, InheritedResourceCounts(0, 0, 0))
                        if team.is_active
                        else InheritedResourceCounts(0, 0, 0)
                    ),
                )
                for membership, team in rows
            ),
        )

    def list_resource_access(
        self,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        *,
        resource_type: ResourceType,
        resource_id: uuid.UUID | None,
        source: str,
        page: int,
        limit: int,
    ) -> Page[ResourceAccessProjection]:
        spec = resource_permission_spec(resource_type)
        direct_route = spec.user_route
        team_route = spec.team_route
        direct_resource = getattr(direct_route.model, direct_route.resource_column)
        team_resource = getattr(team_route.model, team_route.resource_column)

        direct_ids = self.db.query(direct_resource.label("resource_id")).filter(
            direct_route.model.grantee_organization_id == organization_id,
            direct_route.model.user_id == user_id,
        )
        team_ids = (
            self.db.query(team_resource.label("resource_id"))
            .join(
                TeamMembership,
                (TeamMembership.team_id == team_route.model.team_id)
                & (
                    TeamMembership.grantee_organization_id
                    == team_route.model.grantee_organization_id
                ),
            )
            .join(Team, Team.id == team_route.model.team_id)
            .filter(
                team_route.model.grantee_organization_id == organization_id,
                TeamMembership.user_id == user_id,
                Team.is_active.is_(True),
                team_route.model.auth_state.in_(_OPERATIONAL_STATES),
            )
        )
        if source == "direct":
            source_ids = direct_ids
        elif source == "team":
            source_ids = team_ids
        else:
            source_ids = direct_ids.union(team_ids)
        source_subquery = source_ids.distinct().subquery()

        resource_query = self._resource_page_query(
            resource_type,
            organization_id,
            source_subquery,
        )
        if resource_id is not None:
            resource_query = resource_query.filter(
                source_subquery.c.resource_id == resource_id
            )
        total = resource_query.count()
        resource_rows = (
            resource_query.order_by("resource_name", "resource_id")
            .offset((page - 1) * limit)
            .limit(limit)
            .all()
        )
        resource_ids = [row.resource_id for row in resource_rows]
        if not resource_ids:
            return Page(total=total, items=())

        direct_rows = (
            self.db.query(direct_route.model)
            .filter(
                direct_route.model.grantee_organization_id == organization_id,
                direct_route.model.user_id == user_id,
                direct_resource.in_(resource_ids),
            )
            .all()
        )
        direct_by_resource = {
            getattr(row, direct_route.resource_column): DirectPermissionSnapshot(
                permission_id=row.id,
                auth_state=row.auth_state,
                assigned_at=row.assigned_at,
            )
            for row in direct_rows
        }
        team_rows = (
            self.db.query(team_route.model, TeamMembership, Team)
            .join(
                TeamMembership,
                (TeamMembership.team_id == team_route.model.team_id)
                & (
                    TeamMembership.grantee_organization_id
                    == team_route.model.grantee_organization_id
                ),
            )
            .join(Team, Team.id == team_route.model.team_id)
            .filter(
                team_route.model.grantee_organization_id == organization_id,
                TeamMembership.user_id == user_id,
                Team.is_active.is_(True),
                team_route.model.auth_state.in_(_OPERATIONAL_STATES),
                team_resource.in_(resource_ids),
            )
            .order_by(Team.name.asc(), Team.id.asc())
            .all()
        )
        team_by_resource: dict[uuid.UUID, list[TeamPermissionSource]] = defaultdict(
            list
        )
        for permission, membership, team in team_rows:
            team_by_resource[getattr(permission, team_route.resource_column)].append(
                TeamPermissionSource(
                    team_membership_id=membership.id,
                    team_id=team.id,
                    team_name=team.name,
                    auth_state=permission.auth_state,
                )
            )
        return Page(
            total=total,
            items=tuple(
                ResourceAccessProjection(
                    resource_type=resource_type,
                    resource_id=row.resource_id,
                    resource_name=row.resource_name,
                    direct_permission=direct_by_resource.get(row.resource_id),
                    team_sources=tuple(team_by_resource.get(row.resource_id, [])),
                )
                for row in resource_rows
            ),
        )

    def _team_resource_counts(
        self,
        organization_id: uuid.UUID,
        team_ids: list[uuid.UUID],
    ) -> dict[uuid.UUID, InheritedResourceCounts]:
        if not team_ids:
            return {}
        by_team: dict[uuid.UUID, dict[str, set[uuid.UUID]]] = defaultdict(
            lambda: {
                "workflow": set(),
                "knowledge_base": set(),
                "llm_credential": set(),
                "mail_credential": set(),
            }
        )
        for (
            resource_type,
            model,
            resource_column,
            resource_model,
        ) in _TEAM_MODELS_AND_COLUMNS:
            query = self.db.query(model).join(
                resource_model,
                (resource_model.id == getattr(model, resource_column))
                & (resource_model.organization_id == model.grantee_organization_id),
            )
            query = _filter_operational_resource(query, resource_model)
            rows = query.filter(
                model.grantee_organization_id == organization_id,
                model.team_id.in_(team_ids),
                model.auth_state.in_(_OPERATIONAL_STATES),
            ).all()
            for row in rows:
                by_team[row.team_id][resource_type].add(getattr(row, resource_column))
        return {
            team_id: InheritedResourceCounts(
                workflow=len(values["workflow"]),
                knowledge_base=len(values["knowledge_base"]),
                llm_credential=len(values["llm_credential"]),
                mail_credential=len(values["mail_credential"]),
            )
            for team_id, values in by_team.items()
        }

    def _count_operational_direct_permissions(
        self,
        organization_id,
        user_id,
        model,
        resource_model,
        resource_column,
    ) -> int:
        query = self.db.query(model).join(
            resource_model,
            (resource_model.id == getattr(model, resource_column))
            & (resource_model.organization_id == model.grantee_organization_id),
        )
        query = _filter_operational_resource(query, resource_model)
        return query.filter(
            model.grantee_organization_id == organization_id,
            model.user_id == user_id,
            model.auth_state != "none",
        ).count()

    def _resource_page_query(
        self,
        resource_type: ResourceType,
        organization_id: uuid.UUID,
        source_subquery: Any,
    ):
        if resource_type == "workflow":
            return (
                self.db.query(
                    Workflow.id.label("resource_id"),
                    App.name.label("resource_name"),
                )
                .join(source_subquery, source_subquery.c.resource_id == Workflow.id)
                .join(App, App.id == Workflow.app_id)
                .filter(
                    Workflow.organization_id == organization_id,
                    App.organization_id == organization_id,
                )
            )
        if resource_type == "knowledge_base":
            return (
                self.db.query(
                    KnowledgeBase.id.label("resource_id"),
                    KnowledgeBase.name.label("resource_name"),
                )
                .join(
                    source_subquery,
                    source_subquery.c.resource_id == KnowledgeBase.id,
                )
                .filter(
                    KnowledgeBase.organization_id == organization_id,
                    KnowledgeBase.lifecycle_state == "active",
                )
            )
        if resource_type == "mail_credential":
            return (
                self.db.query(
                    MailCredential.id.label("resource_id"),
                    MailCredential.credential_name.label("resource_name"),
                )
                .join(
                    source_subquery,
                    source_subquery.c.resource_id == MailCredential.id,
                )
                .filter(
                    MailCredential.organization_id == organization_id,
                    MailCredential.status == "active",
                )
            )
        return (
            self.db.query(
                LLMCredential.id.label("resource_id"),
                LLMCredential.credential_name.label("resource_name"),
            )
            .join(
                source_subquery,
                source_subquery.c.resource_id == LLMCredential.id,
            )
            .filter(
                LLMCredential.organization_id == organization_id,
                LLMCredential.is_valid.is_(True),
            )
        )


def _member_snapshot(
    membership: OrganizationMembership,
    user: User,
) -> MemberSnapshot:
    return MemberSnapshot(
        membership_id=membership.id,
        organization_id=membership.organization_id,
        user_id=membership.user_id,
        name=user.name or "",
        email=user.email,
        user_active=user.deactivated_at is None,
        membership_state=membership.membership_state,
        organization_auth_state=membership.organization_auth_state,
        updated_at=membership.updated_at,
    )


def _app_permission_snapshot(
    row: UserAppCreationPermission,
) -> AppCreationPermissionSnapshot:
    return AppCreationPermissionSnapshot(
        permission_id=row.id,
        assigned_at=row.assigned_at,
    )


def _filter_operational_resource(query, resource_model):
    if resource_model is KnowledgeBase:
        return query.filter(KnowledgeBase.lifecycle_state == "active")
    if resource_model is LLMCredential:
        return query.filter(LLMCredential.is_valid.is_(True))
    if resource_model is MailCredential:
        return query.filter(MailCredential.status == "active")
    return query
