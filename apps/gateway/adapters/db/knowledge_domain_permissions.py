from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.gateway.application.knowledge_administration.domain_permissions import (
    DomainPermissionCommand,
    DomainPermissionProjection,
    DomainSubjectType,
)
from apps.shared.db.models.organization_membership import (
    ORGANIZATION_MEMBERSHIP_ACTIVE,
    OrganizationMembership,
)
from apps.shared.db.models.team import (
    Team,
    TeamKnowledgeDomainPermission,
    UserKnowledgeDomainPermission,
)
from apps.shared.db.models.user import User
from apps.shared.services.permissions import (
    get_effective_knowledge_domain_actions,
    has_organization_manager_permission,
)


class SqlAlchemyKnowledgeDomainAuthorization:
    def __init__(self, db: Session) -> None:
        self.db = db

    def is_organization_manager(
        self, actor_id: uuid.UUID, organization_id: uuid.UUID
    ) -> bool:
        return has_organization_manager_permission(
            self.db, actor_id, organization_id
        )

    def effective_actions(
        self, actor_id: uuid.UUID, organization_id: uuid.UUID
    ) -> set[str]:
        return get_effective_knowledge_domain_actions(
            self.db, actor_id, organization_id
        )


class SqlAlchemyKnowledgeDomainPermissionRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_permissions(
        self, organization_id: uuid.UUID
    ) -> list[DomainPermissionProjection]:
        evaluated_at = datetime.now(timezone.utc)
        team_rows = (
            self.db.query(TeamKnowledgeDomainPermission, Team)
            .join(Team, Team.id == TeamKnowledgeDomainPermission.team_id)
            .filter(
                TeamKnowledgeDomainPermission.organization_id == organization_id,
                Team.organization_id == organization_id,
            )
            .order_by(
                Team.name.asc(),
                TeamKnowledgeDomainPermission.permission_action.asc(),
            )
            .all()
        )
        user_rows = (
            self.db.query(UserKnowledgeDomainPermission, User)
            .join(User, User.id == UserKnowledgeDomainPermission.user_id)
            .filter(
                UserKnowledgeDomainPermission.organization_id == organization_id
            )
            .order_by(
                User.name.asc(),
                UserKnowledgeDomainPermission.permission_action.asc(),
            )
            .all()
        )
        projections = [
            self._projection(row, "team", team.name, evaluated_at)
            for row, team in team_rows
        ]
        projections.extend(
            self._projection(row, "user", user.name or user.email, evaluated_at)
            for row, user in user_rows
        )
        return projections

    def lock_active_subject_for_grant(
        self,
        organization_id: uuid.UUID,
        subject_type: DomainSubjectType,
        subject_id: uuid.UUID,
    ) -> str | None:
        if subject_type == "team":
            team = (
                self.db.query(Team)
                .filter(
                    Team.id == subject_id,
                    Team.organization_id == organization_id,
                    Team.is_active.is_(True),
                )
                .with_for_update()
                .first()
            )
            return None if team is None else team.name

        row = (
            self.db.query(User, OrganizationMembership)
            .join(
                OrganizationMembership,
                OrganizationMembership.user_id == User.id,
            )
            .filter(
                User.id == subject_id,
                User.deactivated_at.is_(None),
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.membership_state
                == ORGANIZATION_MEMBERSHIP_ACTIVE,
            )
            .with_for_update()
            .first()
        )
        if row is None:
            return None
        user = row[0]
        return user.name or user.email

    def upsert(
        self, command: DomainPermissionCommand
    ) -> tuple[uuid.UUID, bool]:
        model, subject_column = self._model_and_subject_column(command.subject_type)
        row = (
            self.db.query(model)
            .filter(
                model.organization_id == command.organization_id,
                subject_column == command.subject_id,
                model.permission_action == command.permission_action,
            )
            .with_for_update()
            .first()
        )
        created = row is None
        if row is None:
            values = {
                "organization_id": command.organization_id,
                "permission_action": command.permission_action,
                "assigned_by": command.actor_id,
                "assigned_at": datetime.now(timezone.utc),
                "expires_at": command.expires_at,
            }
            values["team_id" if command.subject_type == "team" else "user_id"] = (
                command.subject_id
            )
            row = model(**values)
            self.db.add(row)
        else:
            row.assigned_by = command.actor_id
            row.assigned_at = datetime.now(timezone.utc)
            row.expires_at = command.expires_at
        self.db.flush()
        return row.id, created

    def revoke(self, command: DomainPermissionCommand) -> uuid.UUID | None:
        row = self.db.execute(self._revoke_statement(command)).scalar_one_or_none()
        if row is None:
            return None
        permission_id = row.id
        self.db.delete(row)
        return permission_id

    @classmethod
    def _revoke_statement(cls, command: DomainPermissionCommand):
        model, subject_column = cls._model_and_subject_column(command.subject_type)
        return (
            select(model)
            .where(
                model.organization_id == command.organization_id,
                subject_column == command.subject_id,
                model.permission_action == command.permission_action,
            )
            .with_for_update()
        )

    @staticmethod
    def _model_and_subject_column(subject_type: DomainSubjectType):
        if subject_type == "team":
            return TeamKnowledgeDomainPermission, TeamKnowledgeDomainPermission.team_id
        return UserKnowledgeDomainPermission, UserKnowledgeDomainPermission.user_id

    @staticmethod
    def _projection(row, subject_type, label, evaluated_at):
        expires_at = row.expires_at
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return DomainPermissionProjection(
            permission_id=row.id,
            subject_type=subject_type,
            subject_id=row.team_id if subject_type == "team" else row.user_id,
            subject_safe_label=label,
            permission_action=row.permission_action,
            assigned_at=row.assigned_at,
            expires_at=row.expires_at,
            is_expired=expires_at is not None and expires_at <= evaluated_at,
        )
