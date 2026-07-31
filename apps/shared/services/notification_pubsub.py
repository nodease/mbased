import json
import logging
from typing import Any

from apps.shared.db.models.organization import Organization
from apps.shared.db.models.organization_membership import (
    ORGANIZATION_AUTH_MANAGER,
    ORGANIZATION_MEMBERSHIP_ACTIVE,
    OrganizationMembership,
)
from apps.shared.db.models.user import User
from apps.shared.pubsub import get_redis_client
from apps.shared.services.permissions import has_organization_manager_permission

logger = logging.getLogger(__name__)

NOTIFICATION_EVENT_CHANGED = "notifications.changed"


def notification_channel(user_id: Any) -> str:
    return f"notifications:user:{user_id}"


def publish_notifications_changed(user_id: Any) -> None:
    try:
        get_redis_client().publish(
            notification_channel(user_id),
            json.dumps({"type": NOTIFICATION_EVENT_CHANGED}),
        )
    except Exception as error:
        logger.warning(
            "Failed to publish notification change: error_type=%s",
            type(error).__name__,
        )


def _organization_manager_recipient_ids(
    db: Any,
    organization_id: Any,
) -> list[Any]:
    organization = (
        db.query(Organization)
        .filter(
            Organization.id == organization_id,
            Organization.is_active.is_(True),
        )
        .first()
    )
    if organization is None:
        return []
    rows = (
        db.query(OrganizationMembership.user_id)
        .join(User, User.id == OrganizationMembership.user_id)
        .filter(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.membership_state
            == ORGANIZATION_MEMBERSHIP_ACTIVE,
            OrganizationMembership.organization_auth_state
            == ORGANIZATION_AUTH_MANAGER,
            User.deactivated_at.is_(None),
        )
        .all()
    )
    recipient_ids = []
    seen_user_ids = set()
    for row in rows:
        user_id = row[0] if isinstance(row, tuple) else row.user_id
        if user_id is None or user_id in seen_user_ids:
            continue
        seen_user_ids.add(user_id)
        recipient_ids.append(user_id)

    # Legacy/imported organization에는 owner membership이 없을 수 있다.
    # 기존 manager 권한 판정을 재사용해 suspended/deactivated owner를 제외한다.
    for user_id in (organization.created_by, organization.managed_by):
        if user_id is None or user_id in seen_user_ids:
            continue
        if not has_organization_manager_permission(db, user_id, organization_id):
            continue
        seen_user_ids.add(user_id)
        recipient_ids.append(user_id)
    return recipient_ids


def publish_notifications_changed_to_organization_managers(
    db: Any,
    organization_id: Any,
) -> None:
    try:
        recipient_ids = _organization_manager_recipient_ids(db, organization_id)
    except Exception as error:
        logger.warning(
            "Failed to resolve security alert notification recipients: error_type=%s",
            type(error).__name__,
        )
        return

    for user_id in recipient_ids:
        publish_notifications_changed(user_id)


def deliver_notifications_changed_to_organization_managers(
    db: Any,
    organization_id: Any,
) -> None:
    """Outbox delivery path: failures must escape so the event can be retried."""
    recipient_ids = _organization_manager_recipient_ids(db, organization_id)
    redis = get_redis_client()
    message = json.dumps({"type": NOTIFICATION_EVENT_CHANGED})
    for user_id in recipient_ids:
        redis.publish(notification_channel(user_id), message)
