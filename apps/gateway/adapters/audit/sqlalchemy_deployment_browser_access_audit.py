from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from apps.gateway.application.deployment.browser_access_models import (
    BrowserAccessRevision,
    BrowserAccessRevisionCommand,
    BrowserAccessSourceSnapshot,
)
from apps.shared.audit.actions import AuditAction
from apps.shared.audit.context import get_current_metadata
from apps.shared.db.models.audit_log import AuditLog


class SqlAlchemyDeploymentBrowserAccessAuditRecorder:
    def __init__(self, db: Session, *, actor: Any) -> None:
        self.db = db
        self.actor = actor

    def record_revision(
        self,
        command: BrowserAccessRevisionCommand,
        source: BrowserAccessSourceSnapshot,
        revision: BrowserAccessRevision,
        *,
        policy_digest: str,
    ) -> None:
        embedding = revision.browser_access_policy["embedding"]
        metadata = dict(get_current_metadata())
        metadata.update(
            {
                "organization_id": (
                    str(source.organization_id)
                    if source.organization_id is not None
                    else None
                ),
                "actor": {
                    "id": str(self.actor.id),
                    "email": getattr(self.actor, "email", None),
                    "name": getattr(self.actor, "name", None),
                },
                "change_kind": "browser_access_revision",
                "source_version": source.version,
                "new_version": revision.version,
                "contract_version": revision.browser_access_policy[
                    "contract_version"
                ],
                "embedding_enabled": embedding["enabled"],
                "origin_count": len(embedding["parent_origins"]),
                "policy_digest": policy_digest,
                "activated": command.is_active,
            }
        )
        self.db.add(
            AuditLog(
                action=AuditAction.WORKFLOW_DEPLOY,
                category="action",
                actor_id=command.actor_id,
                actor_type="user",
                target_type="deployment",
                target_id=str(revision.id),
                before=None,
                after=None,
                status="success",
                audit_metadata=metadata,
            )
        )
