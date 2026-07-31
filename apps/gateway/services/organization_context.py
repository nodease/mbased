import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import Request
from sqlalchemy.orm import Session

from apps.gateway.utils.api_errors import parse_organization_id, raise_api_error
from apps.shared.db.models.organization import Organization
from apps.shared.db.models.organization_membership import (
    ORGANIZATION_AUTH_MANAGER,
    ORGANIZATION_MEMBERSHIP_ACTIVE,
    OrganizationMembership,
)
from apps.shared.db.models.team import Team, TeamMembership
from apps.shared.db.models.user import User
from apps.shared.services.permissions import has_organization_scope_access


def get_user_primary_organization_id(
    db: Session,
    user_id: uuid.UUID,
) -> Optional[uuid.UUID]:
    """Return the first organization available through active organization membership."""

    membership = (
        db.query(OrganizationMembership)
        .join(Organization, Organization.id == OrganizationMembership.organization_id)
        .filter(
            OrganizationMembership.user_id == user_id,
            OrganizationMembership.membership_state == ORGANIZATION_MEMBERSHIP_ACTIVE,
            Organization.is_active.is_(True),
        )
        .order_by(
            OrganizationMembership.accepted_at.asc().nulls_last(),
            OrganizationMembership.created_at.asc(),
        )
        .first()
    )
    return membership.organization_id if membership else None


def resolve_active_organization_id(
    db: Session,
    request: Request,
    raw_organization_id: str | None,
    user_id: uuid.UUID,
) -> uuid.UUID:
    """Resolve X-Organization-Id and verify it is in the user's active scope."""

    organization_id = parse_organization_id(request, raw_organization_id)
    if not has_organization_scope_access(db, user_id, organization_id):
        raise_api_error(
            request,
            404,
            "resource.not_found",
            "Organization not found.",
        )

    return organization_id


def ensure_user_default_organization(
    db: Session,
    user: User | uuid.UUID,
) -> uuid.UUID:
    """Create the default organization/team/membership foundation if missing."""

    user_id = getattr(user, "id", user)
    user_name = getattr(user, "name", None)
    existing_id = get_user_primary_organization_id(db, user_id)
    if existing_id:
        return existing_id

    if not user_name:
        db_user = db.query(User).filter(User.id == user_id).first()
        user_name = db_user.name if db_user else "Personal"

    organization = Organization(
        id=uuid.uuid4(),
        name=f"{user_name}'s Organization",
        created_by=user_id,
        managed_by=user_id,
    )
    now = datetime.now(timezone.utc)
    organization_membership = OrganizationMembership(
        organization_id=organization.id,
        user_id=user_id,
        membership_state=ORGANIZATION_MEMBERSHIP_ACTIVE,
        organization_auth_state=ORGANIZATION_AUTH_MANAGER,
        invited_by=user_id,
        invited_at=now,
        accepted_at=now,
    )
    team = Team(
        id=uuid.uuid4(),
        organization_id=organization.id,
        name="Default",
        created_by=user_id,
        managed_by=user_id,
        is_auto_add=True,
    )
    membership = TeamMembership(
        grantee_organization_id=organization.id,
        user_id=user_id,
        team_id=team.id,
        assigned_by=user_id,
    )
    db.add(organization)
    db.add(organization_membership)
    db.add(team)
    db.add(membership)
    db.flush()
    return organization.id
