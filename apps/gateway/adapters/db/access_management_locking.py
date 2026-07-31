from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import case
from sqlalchemy.orm import Session

from apps.shared.db.models.organization_membership import (
    ORGANIZATION_AUTH_MANAGER,
    ORGANIZATION_MEMBERSHIP_ACTIVE,
    OrganizationMembership,
)
from apps.shared.db.models.user import User


@dataclass(frozen=True)
class LockedAccessSubjectRows:
    membership: OrganizationMembership
    user: User
    active_manager_count: int | None


def lock_access_subject_rows(
    db: Session,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    manager_reduction: bool,
    allowed_membership_states: frozenset[str] | None = frozenset(
        {"active", "suspended"}
    ),
) -> LockedAccessSubjectRows | None:
    active_manager_count: int | None = None
    locked_manager_memberships: list[OrganizationMembership] = []
    locked_users: dict[uuid.UUID, User] = {}
    if manager_reduction:
        locked_manager_memberships = (
            db.query(OrganizationMembership)
            .filter(
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.membership_state
                == ORGANIZATION_MEMBERSHIP_ACTIVE,
                OrganizationMembership.organization_auth_state
                == ORGANIZATION_AUTH_MANAGER,
            )
            .order_by(OrganizationMembership.id.asc())
            # Serialize subject mutations without blocking permission FK checks.
            .with_for_update(key_share=True)
            .all()
        )
        if locked_manager_memberships:
            user_order = {
                membership.user_id: index
                for index, membership in enumerate(locked_manager_memberships)
            }
            users = (
                db.query(User)
                .filter(User.id.in_(tuple(user_order)))
                .order_by(case(user_order, value=User.id))
                .with_for_update(key_share=True)
                .all()
            )
            locked_users = {user.id: user for user in users}
        active_manager_count = sum(
            1
            for membership in locked_manager_memberships
            if locked_users.get(membership.user_id) is not None
            and locked_users[membership.user_id].deactivated_at is None
        )

    membership = next(
        (
            item
            for item in locked_manager_memberships
            if item.user_id == user_id
        ),
        None,
    )
    if membership is None:
        membership = (
            db.query(OrganizationMembership)
            .filter(
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.user_id == user_id,
            )
            .with_for_update(key_share=True)
            .first()
        )
    if membership is None or (
        allowed_membership_states is not None
        and membership.membership_state not in allowed_membership_states
    ):
        return None

    user = locked_users.get(user_id)
    if user is None:
        user = (
            db.query(User)
            .filter(User.id == user_id)
            .with_for_update(key_share=True)
            .first()
        )
    if user is None:
        return None
    return LockedAccessSubjectRows(
        membership=membership,
        user=user,
        active_manager_count=active_manager_count,
    )
