from typing import Any

from sqlalchemy.orm import Session

from apps.shared.db.models.organization import Organization
from apps.shared.db.models.organization_membership import (
    ORGANIZATION_MEMBERSHIP_INVITED,
    OrganizationMembership,
)
from apps.shared.schemas.notification import NotificationItemResponse
from apps.shared.services.notification_pubsub import (
    NOTIFICATION_EVENT_CHANGED,
    notification_channel,
    publish_notifications_changed,
)

__all__ = [
    "NOTIFICATION_EVENT_CHANGED",
    "NotificationService",
    "notification_channel",
    "publish_notifications_changed",
]


class NotificationService:
    @staticmethod
    def list_notifications(db: Session, user_id: Any) -> list[NotificationItemResponse]:
        rows = (
            db.query(Organization, OrganizationMembership)
            .join(
                OrganizationMembership,
                OrganizationMembership.organization_id == Organization.id,
            )
            .filter(
                OrganizationMembership.user_id == user_id,
                OrganizationMembership.membership_state
                == ORGANIZATION_MEMBERSHIP_INVITED,
                Organization.is_active.is_(True),
            )
            .order_by(
                OrganizationMembership.invited_at.desc().nulls_last(),
                Organization.name.asc(),
            )
            .all()
        )
        return [
            NotificationItemResponse(
                id=f"organization_invitation:{membership.id}",
                type="organization.invitation",
                organization_id=organization.id,
                organization_name=organization.name,
                organization_auth_state=membership.organization_auth_state,
                created_at=membership.invited_at or membership.created_at,
            )
            for organization, membership in rows
        ]
