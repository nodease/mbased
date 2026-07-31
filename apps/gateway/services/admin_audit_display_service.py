from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from apps.shared.db.models.app import App
from apps.shared.db.models.audit_log import ActorType, AuditLog
from apps.shared.db.models.knowledge import KnowledgeBase, KnowledgeSourceIdentity
from apps.shared.db.models.organization import Organization
from apps.shared.db.models.organization_membership import OrganizationMembership
from apps.shared.db.models.team import Team
from apps.shared.db.models.user import User
from apps.shared.schemas.audit import AuditDisplayReference
from apps.shared.services.knowledge_safe_text import (
    safe_label_from_text,
    sanitize_kb_safe_metadata,
)

logger = logging.getLogger(__name__)

_DISPLAY_TYPES = {
    "organization",
    "user",
    "team",
    "workflow",
    "app",
    "knowledge_base",
}
_REFERENCE_FIELDS = {
    "organization_id": "organization",
    "grantee_organization_id": "organization",
    "target_user_id": "user",
    "user_id": "user",
    "team_id": "team",
    "workflow_id": "workflow",
    "knowledge_base_id": "knowledge_base",
}


@dataclass(frozen=True)
class AuditDisplayResolution:
    actors: dict[UUID, AuditDisplayReference] = field(default_factory=dict)
    targets: dict[UUID, AuditDisplayReference] = field(default_factory=dict)
    references: dict[str, AuditDisplayReference] = field(default_factory=dict)


class AdminAuditDisplayService:
    @staticmethod
    def resolve(
        db: Session,
        organization_id: Any,
        audit_logs: Iterable[AuditLog],
        *,
        detail_metadata: dict[str, Any] | None = None,
        change_summary: dict[str, Any] | None = None,
    ) -> AuditDisplayResolution:
        logs = list(audit_logs)
        try:
            scope_id = UUID(str(organization_id))
        except (TypeError, ValueError, AttributeError):
            return AuditDisplayResolution()

        actors: dict[UUID, AuditDisplayReference] = {}
        targets: dict[UUID, AuditDisplayReference] = {}
        requested: dict[str, set[UUID]] = {
            resource_type: set() for resource_type in _DISPLAY_TYPES
        }
        actor_ids_without_snapshot: set[UUID] = set()
        target_keys: dict[UUID, tuple[str, UUID]] = {}

        for item in logs:
            actor_display = _actor_snapshot_display(item)
            if actor_display is not None:
                actors[item.id] = actor_display
            elif _is_user_actor(item) and item.actor_id is not None:
                actor_ids_without_snapshot.add(item.actor_id)
                requested["user"].add(item.actor_id)

            target_type = item.target_type or ""
            target_id = _uuid_or_none(item.target_id)
            if target_type in _DISPLAY_TYPES and target_id is not None:
                requested[target_type].add(target_id)
                target_keys[item.id] = (target_type, target_id)

        reference_keys: dict[str, tuple[str, UUID]] = {}
        if detail_metadata:
            _collect_metadata_references(detail_metadata, requested, reference_keys)
        if change_summary:
            _collect_snapshot_references(change_summary, requested, reference_keys)

        current = _resolve_current_references(db, scope_id, requested)
        for item in logs:
            if item.actor_id in actor_ids_without_snapshot:
                actor_display = current.get(("user", item.actor_id))
                if actor_display is not None:
                    actors[item.id] = actor_display
            target_key = target_keys.get(item.id)
            if target_key is not None and target_key in current:
                targets[item.id] = current[target_key]

        references = {
            raw_id: current[key]
            for raw_id, key in reference_keys.items()
            if key in current
        }
        return AuditDisplayResolution(
            actors=actors,
            targets=targets,
            references=references,
        )


def _is_user_actor(item: AuditLog) -> bool:
    return item.actor_type in (ActorType.USER, ActorType.USER.value)


def _actor_snapshot_display(item: AuditLog) -> AuditDisplayReference | None:
    if not _is_user_actor(item):
        return None
    actor = (item.audit_metadata or {}).get("actor")
    if not isinstance(actor, dict):
        return None
    label = _person_label(actor.get("name"), actor.get("email"))
    if label is None:
        return None
    return AuditDisplayReference(label=label, source="event_snapshot")


def _collect_metadata_references(
    metadata: dict[str, Any],
    requested: dict[str, set[UUID]],
    reference_keys: dict[str, tuple[str, UUID]],
) -> None:
    for field_name, resource_type in _REFERENCE_FIELDS.items():
        _add_reference(metadata.get(field_name), resource_type, requested, reference_keys)
    resource_type = metadata.get("resource_type")
    if resource_type in _DISPLAY_TYPES:
        _add_reference(
            metadata.get("resource_id"), resource_type, requested, reference_keys
        )


def _collect_snapshot_references(
    change_summary: dict[str, Any],
    requested: dict[str, set[UUID]],
    reference_keys: dict[str, tuple[str, UUID]],
) -> None:
    for side in ("before", "after"):
        snapshot = change_summary.get(side)
        if not isinstance(snapshot, dict):
            continue
        for field_name, resource_type in _REFERENCE_FIELDS.items():
            _add_reference(
                snapshot.get(field_name), resource_type, requested, reference_keys
            )


def _add_reference(
    value: Any,
    resource_type: str,
    requested: dict[str, set[UUID]],
    reference_keys: dict[str, tuple[str, UUID]],
) -> None:
    resource_id = _uuid_or_none(value)
    if resource_id is None or resource_type not in requested:
        return
    requested[resource_type].add(resource_id)
    reference_keys[str(resource_id)] = (resource_type, resource_id)


def _resolve_current_references(
    db: Session,
    organization_id: UUID,
    requested: dict[str, set[UUID]],
) -> dict[tuple[str, UUID], AuditDisplayReference]:
    resolved: dict[tuple[str, UUID], AuditDisplayReference] = {}

    organization_ids = requested["organization"] & {organization_id}
    if organization_ids:
        for row in _query_rows(
            "organization",
            lambda: db.query(Organization)
            .filter(Organization.id.in_(organization_ids))
            .all(),
        ):
            _store_named(resolved, "organization", row.id, row.name)

    user_ids = requested["user"]
    member_ids: set[UUID] = set()
    if user_ids:
        memberships = _query_rows(
            "user membership",
            lambda: db.query(OrganizationMembership)
            .filter(
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.user_id.in_(user_ids),
                OrganizationMembership.membership_state.in_(("active", "suspended")),
            )
            .all(),
        )
        member_ids = {row.user_id for row in memberships}
    if member_ids:
        for row in _query_rows(
            "user",
            lambda: db.query(User)
            .filter(User.id.in_(member_ids), User.deactivated_at.is_(None))
            .all(),
        ):
            label = _person_label(row.name, row.email)
            if label is not None:
                resolved[("user", row.id)] = AuditDisplayReference(
                    label=label,
                    source="current_resource",
                )

    team_ids = requested["team"]
    if team_ids:
        for row in _query_rows(
            "team",
            lambda: db.query(Team)
            .filter(
                Team.id.in_(team_ids),
                Team.organization_id == organization_id,
                Team.is_active.is_(True),
            )
            .all(),
        ):
            _store_named(resolved, "team", row.id, row.name)

    workflow_ids = requested["workflow"]
    if workflow_ids:
        for row in _query_rows(
            "workflow",
            lambda: db.query(App)
            .filter(
                App.workflow_id.in_(workflow_ids),
                App.organization_id == organization_id,
            )
            .all(),
        ):
            if row.workflow_id is not None:
                _store_named(resolved, "workflow", row.workflow_id, row.name)

    app_ids = requested["app"]
    if app_ids:
        for row in _query_rows(
            "app",
            lambda: db.query(App)
            .filter(App.id.in_(app_ids), App.organization_id == organization_id)
            .all(),
        ):
            _store_named(resolved, "app", row.id, row.name)

    knowledge_base_ids = requested["knowledge_base"]
    if knowledge_base_ids:
        knowledge_bases = _query_rows(
            "knowledge base",
            lambda: db.query(KnowledgeBase)
            .filter(
                KnowledgeBase.id.in_(knowledge_base_ids),
                KnowledgeBase.organization_id == organization_id,
            )
            .all(),
        )
        source_knowledge_bases: dict[UUID, set[UUID]] = {}
        for row in knowledge_bases:
            if getattr(row, "lifecycle_state", None) == "deleted":
                continue
            source_identity_id = _uuid_or_none(
                getattr(row, "source_identity_id", None)
            )
            if source_identity_id is not None:
                source_knowledge_bases.setdefault(source_identity_id, set()).add(row.id)
                continue
            safe_metadata = sanitize_kb_safe_metadata(
                getattr(row, "safe_metadata", None)
            )
            safe_label = safe_label_from_text(safe_metadata.get("safe_label"))
            if safe_label is not None:
                _store_named(resolved, "knowledge_base", row.id, safe_label)

        if source_knowledge_bases:
            for row in _query_rows(
                "knowledge source identity",
                lambda: db.query(KnowledgeSourceIdentity)
                .filter(
                    KnowledgeSourceIdentity.id.in_(source_knowledge_bases),
                    KnowledgeSourceIdentity.organization_id == organization_id,
                    KnowledgeSourceIdentity.display_policy_state == "approved",
                    KnowledgeSourceIdentity.is_active.is_(True),
                )
                .all(),
            ):
                safe_label = safe_label_from_text(row.safe_display_name)
                if safe_label is None:
                    continue
                for knowledge_base_id in source_knowledge_bases.get(row.id, set()):
                    _store_named(
                        resolved,
                        "knowledge_base",
                        knowledge_base_id,
                        safe_label,
                    )

    return resolved


def _query_rows(label: str, query: Callable[[], Iterable[Any]]) -> list[Any]:
    try:
        return list(query())
    except Exception as exc:  # display failure must not fail audit retrieval
        logger.warning(
            "[AuditDisplay] %s lookup failed: %s",
            label,
            type(exc).__name__,
        )
        return []


def _store_named(
    resolved: dict[tuple[str, UUID], AuditDisplayReference],
    resource_type: str,
    resource_id: UUID,
    value: Any,
) -> None:
    label = _safe_scalar(value, max_length=255)
    if label is None:
        return
    resolved[(resource_type, resource_id)] = AuditDisplayReference(
        label=label,
        source="current_resource",
    )


def _person_label(name: Any, email: Any) -> str | None:
    safe_name = _safe_scalar(name, max_length=255)
    safe_email = _safe_scalar(email, max_length=320)
    if safe_name and safe_email:
        return f"{safe_name} ({safe_email})"
    return safe_name or safe_email


def _safe_scalar(value: Any, *, max_length: int) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > max_length:
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        return None
    return normalized


def _uuid_or_none(value: Any) -> UUID | None:
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None
