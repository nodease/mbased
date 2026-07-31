from __future__ import annotations

from dataclasses import dataclass

from apps.shared.audit.manual_ownership import register_manual_audit_ownership
from apps.shared.db.models.audit_log import (
    ActorType,
    AuditCategory,
    AuditLog,
    AuditStatus,
)
from apps.shared.db.models.knowledge import KnowledgeBase
from apps.shared.db.models.organization_membership import (
    ORGANIZATION_MEMBERSHIP_ACTIVE,
    OrganizationMembership,
)
from apps.shared.db.models.team import UserKnowledgePermission
from apps.shared.db.models.user import User
from apps.shared.permissions import normalize_resource_auth_state
from sqlalchemy import and_
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class KnowledgeOwnerBackfillReport:
    total: int = 0
    eligible: int = 0
    already_manager: int = 0
    would_grant: int = 0
    granted: int = 0
    missing_organization: int = 0
    owner_inactive_or_missing: int = 0
    owner_membership_invalid: int = 0

    def safe_dict(self) -> dict[str, int]:
        return {
            "total": self.total,
            "eligible": self.eligible,
            "already_manager": self.already_manager,
            "would_grant": self.would_grant,
            "granted": self.granted,
            "missing_organization": self.missing_organization,
            "owner_inactive_or_missing": self.owner_inactive_or_missing,
            "owner_membership_invalid": self.owner_membership_invalid,
        }


def backfill_knowledge_owner_manager_permissions(
    db: Session,
    *,
    apply: bool = False,
) -> KnowledgeOwnerBackfillReport:
    rows = (
        db.query(
            KnowledgeBase,
            User,
            OrganizationMembership,
            UserKnowledgePermission,
        )
        .outerjoin(User, User.id == KnowledgeBase.user_id)
        .outerjoin(
            OrganizationMembership,
            and_(
                OrganizationMembership.organization_id
                == KnowledgeBase.organization_id,
                OrganizationMembership.user_id == KnowledgeBase.user_id,
            ),
        )
        .outerjoin(
            UserKnowledgePermission,
            and_(
                UserKnowledgePermission.grantee_organization_id
                == KnowledgeBase.organization_id,
                UserKnowledgePermission.user_id == KnowledgeBase.user_id,
                UserKnowledgePermission.knowledge_base_id == KnowledgeBase.id,
            ),
        )
        .order_by(KnowledgeBase.id.asc())
        .all()
    )
    counts = {
        "total": len(rows),
        "eligible": 0,
        "already_manager": 0,
        "would_grant": 0,
        "granted": 0,
        "missing_organization": 0,
        "owner_inactive_or_missing": 0,
        "owner_membership_invalid": 0,
    }
    try:
        for kb, owner, membership, permission in rows:
            reason = _ineligible_reason(kb, owner, membership)
            if reason is not None:
                counts[reason] += 1
                continue
            counts["eligible"] += 1
            if (
                permission is not None
                and normalize_resource_auth_state(permission.auth_state) == "manager"
            ):
                counts["already_manager"] += 1
                continue
            counts["would_grant"] += 1
            if not apply:
                continue
            operation = "created" if permission is None else "updated"
            if permission is None:
                permission = UserKnowledgePermission(
                    grantee_organization_id=kb.organization_id,
                    user_id=kb.user_id,
                    knowledge_base_id=kb.id,
                    auth_state="manager",
                    assigned_by=kb.user_id,
                )
                db.add(permission)
            else:
                permission.auth_state = "manager"
                permission.assigned_by = kb.user_id
            register_manual_audit_ownership(db, permission, operation)
            db.flush()
            _record_backfill_audit(db, permission, operation)
            counts["granted"] += 1
        if apply:
            db.commit()
        else:
            db.rollback()
    except Exception:
        db.rollback()
        raise
    return KnowledgeOwnerBackfillReport(**counts)


def _ineligible_reason(kb, owner, membership) -> str | None:
    if kb.organization_id is None:
        return "missing_organization"
    if owner is None or owner.deactivated_at is not None:
        return "owner_inactive_or_missing"
    if membership is None or membership.membership_state != ORGANIZATION_MEMBERSHIP_ACTIVE:
        return "owner_membership_invalid"
    return None


def _record_backfill_audit(
    db: Session,
    permission: UserKnowledgePermission,
    operation: str,
) -> None:
    db.add(
        AuditLog(
            action=f"user_knowledge_permission.{operation}",
            category=AuditCategory.DATA_CHANGE,
            actor_id=None,
            actor_type=ActorType.SYSTEM,
            target_type="user_knowledge_permission",
            target_id=str(permission.id),
            before=None,
            after={"auth_state": "manager"},
            status=AuditStatus.SUCCESS,
            audit_metadata={
                "organization_id": str(permission.grantee_organization_id),
                "reason_code": "knowledge.owner_manager_backfill",
            },
        )
    )
