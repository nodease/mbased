from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from apps.shared.db.models.audit_log import AuditEventOutbox, AuditLog
from apps.shared.db.models.workflow import Workflow
from apps.shared.db.models.workflow_run import WorkflowNodeRun, WorkflowRun
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

AUDIT_EVENT_OUTBOX_TASK_NAME = "audit.event_outbox.process"
OUTBOX_STATUS_PENDING = "pending"
OUTBOX_STATUS_LEASED = "leased"
OUTBOX_STATUS_SUCCEEDED = "succeeded"
OUTBOX_STATUS_RETRY_SCHEDULED = "retry_scheduled"
OUTBOX_STATUS_DEAD_LETTERED = "dead_lettered"
DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_LEASE_SECONDS = 300
DEFAULT_RETRY_SECONDS = 60
DEFAULT_PROCESS_LIMIT = 100

PersistAudit = Callable[[Any, dict[str, Any]], uuid.UUID]
AfterCommit = Callable[[uuid.UUID], None]

logger = logging.getLogger(__name__)


class AuditWorkflowRunPendingError(RuntimeError):
    pass


@dataclass(frozen=True)
class AuditEventOutboxProcessResult:
    processed_count: int
    recovered_count: int


def _to_uuid(value: Any) -> uuid.UUID | None:
    try:
        if value is None or isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _to_datetime(value: Any) -> datetime | None:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return value


def audit_id_from_payload(payload: dict[str, Any]) -> uuid.UUID:
    audit_id = _to_uuid(payload.get("id"))
    if audit_id is None:
        raise ValueError("invalid_audit_id")
    return audit_id


def workflow_correlation_from_payload(
    payload: dict[str, Any],
    key: str,
) -> uuid.UUID | None:
    value = payload.get(key)
    if value is None:
        metadata = payload.get("audit_metadata")
        if isinstance(metadata, dict):
            value = metadata.get(key)
    return _to_uuid(value)


def workflow_run_correlation_is_pending(
    db: Session,
    payload: dict[str, Any],
) -> bool:
    metadata = payload.get("audit_metadata")
    if not isinstance(metadata, dict):
        return False
    if _to_uuid(metadata.get("organization_id")) is None:
        return False

    workflow_run_id = workflow_correlation_from_payload(payload, "workflow_run_id")
    if workflow_run_id is None:
        return False

    if db.get(WorkflowRun, workflow_run_id) is not None:
        return False
    audit_id = audit_id_from_payload(payload)
    return db.get(AuditLog, audit_id) is None


def build_audit_log(payload: dict[str, Any]) -> AuditLog:
    """Map the durable wire payload without logging its potentially sensitive values."""
    audit_id = audit_id_from_payload(payload)
    occurred_at = _to_datetime(payload.get("occurred_at"))
    if occurred_at is None:
        raise ValueError("invalid_occurred_at")
    return AuditLog(
        id=audit_id,
        occurred_at=occurred_at,
        actor_id=_to_uuid(payload.get("actor_id")),
        actor_type=payload["actor_type"],
        category=payload["category"],
        action=payload["action"],
        target_type=payload.get("target_type"),
        target_id=payload.get("target_id"),
        before=payload.get("before"),
        after=payload.get("after"),
        workflow_run_id=workflow_correlation_from_payload(
            payload,
            "workflow_run_id",
        ),
        workflow_node_run_id=workflow_correlation_from_payload(
            payload,
            "workflow_node_run_id",
        ),
        status=payload.get("status", "success"),
        audit_metadata=payload.get("audit_metadata") or {},
    )


def validate_audit_workflow_correlation(db: Session, audit: AuditLog) -> None:
    """Keep optional correlation from blocking the canonical audit insert."""
    metadata = audit.audit_metadata if isinstance(audit.audit_metadata, dict) else {}
    audit_organization_id = _to_uuid(metadata.get("organization_id"))
    if audit_organization_id is None:
        audit.workflow_run_id = None
        audit.workflow_node_run_id = None
        return
    expects_workflow = "workflow_id" in metadata
    expected_workflow_id = _to_uuid(metadata.get("workflow_id"))

    def run_matches_expected_workflow(candidate: Any) -> bool:
        return not expects_workflow or (
            expected_workflow_id is not None
            and _to_uuid(candidate.workflow_id) == expected_workflow_id
        )

    run = None
    if audit.workflow_run_id is not None:
        run = db.get(WorkflowRun, audit.workflow_run_id)
        if run is None or not run_matches_expected_workflow(run):
            audit.workflow_run_id = None
            audit.workflow_node_run_id = None
            return
        workflow = db.get(Workflow, run.workflow_id)
        if (
            workflow is None
            or _to_uuid(workflow.organization_id) != audit_organization_id
        ):
            audit.workflow_run_id = None
            audit.workflow_node_run_id = None
            return

    if audit.workflow_node_run_id is None:
        return
    node_run = db.get(WorkflowNodeRun, audit.workflow_node_run_id)
    if node_run is None:
        audit.workflow_node_run_id = None
        return
    if audit.workflow_run_id is None:
        run = db.get(WorkflowRun, node_run.workflow_run_id)
        if run is None or not run_matches_expected_workflow(run):
            audit.workflow_node_run_id = None
            return
        workflow = db.get(Workflow, run.workflow_id)
        if (
            workflow is None
            or _to_uuid(workflow.organization_id) != audit_organization_id
        ):
            audit.workflow_node_run_id = None
            return
        audit.workflow_run_id = run.id
        return
    if node_run.workflow_run_id != audit.workflow_run_id:
        audit.workflow_node_run_id = None


def persist_audit_payload(db: Session, payload: dict[str, Any]) -> uuid.UUID:
    """Insert an AuditLog once; the caller owns the surrounding transaction."""
    audit_id = audit_id_from_payload(payload)
    if db.get(AuditLog, audit_id) is not None:
        return audit_id
    audit = build_audit_log(payload)
    validate_audit_workflow_correlation(db, audit)
    db.add(audit)
    db.flush()
    return audit_id


class AuditEventOutboxService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def lease_due_events(
        self,
        *,
        owner_token: str,
        limit: int = DEFAULT_PROCESS_LIMIT,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        now: datetime | None = None,
    ) -> list[AuditEventOutbox]:
        now = now or datetime.now(timezone.utc)
        events = (
            self.db.query(AuditEventOutbox)
            .filter(
                AuditEventOutbox.status.in_(
                    [OUTBOX_STATUS_PENDING, OUTBOX_STATUS_RETRY_SCHEDULED]
                ),
                or_(
                    AuditEventOutbox.next_retry_at.is_(None),
                    AuditEventOutbox.next_retry_at <= now,
                ),
            )
            .order_by(AuditEventOutbox.created_at.asc())
            .with_for_update(skip_locked=True)
            .limit(limit)
            .all()
        )
        for event in events:
            event.status = OUTBOX_STATUS_LEASED
            event.owner_token = owner_token
            event.lease_expires_at = now + timedelta(seconds=lease_seconds)
            event.attempt_count += 1
            event.updated_at = now
        self.db.flush()
        return events

    def recover_stale_leases(self, *, now: datetime | None = None) -> int:
        now = now or datetime.now(timezone.utc)
        events = (
            self.db.query(AuditEventOutbox)
            .filter(
                AuditEventOutbox.status == OUTBOX_STATUS_LEASED,
                AuditEventOutbox.lease_expires_at.is_not(None),
                AuditEventOutbox.lease_expires_at <= now,
            )
            .with_for_update(skip_locked=True)
            .all()
        )
        for event in events:
            self._apply_retry_or_dead_letter(
                event,
                safe_reason_code="audit.lease_expired",
                now=now,
            )
        self.db.flush()
        return len(events)

    def mark_succeeded(
        self,
        event: AuditEventOutbox,
        *,
        owner_token: str,
        now: datetime | None = None,
    ) -> bool:
        now = now or datetime.now(timezone.utc)
        owned_event = self._lock_owned_lease(
            event_id=event.id,
            owner_token=owner_token,
        )
        if owned_event is None:
            return False
        # AuditLog is canonical after delivery; keep only the idempotency tombstone.
        owned_event.payload = {}
        owned_event.status = OUTBOX_STATUS_SUCCEEDED
        owned_event.owner_token = None
        owned_event.lease_expires_at = None
        owned_event.next_retry_at = None
        owned_event.safe_reason_code = None
        owned_event.delivered_at = now
        owned_event.updated_at = now
        return True

    def mark_retry_or_dead_letter(
        self,
        event: AuditEventOutbox,
        *,
        owner_token: str,
        safe_reason_code: str,
        now: datetime | None = None,
        retry_after_seconds: int = DEFAULT_RETRY_SECONDS,
    ) -> bool:
        owned_event = self._lock_owned_lease(
            event_id=event.id,
            owner_token=owner_token,
        )
        if owned_event is None:
            return False
        self._apply_retry_or_dead_letter(
            owned_event,
            safe_reason_code=safe_reason_code,
            now=now,
            retry_after_seconds=retry_after_seconds,
        )
        return True

    def _lock_owned_lease(
        self,
        *,
        event_id: uuid.UUID,
        owner_token: str,
    ) -> AuditEventOutbox | None:
        return (
            self.db.query(AuditEventOutbox)
            .filter(
                AuditEventOutbox.id == event_id,
                AuditEventOutbox.status == OUTBOX_STATUS_LEASED,
                AuditEventOutbox.owner_token == owner_token,
            )
            .with_for_update()
            .one_or_none()
        )

    def _apply_retry_or_dead_letter(
        self,
        event: AuditEventOutbox,
        *,
        safe_reason_code: str,
        now: datetime | None = None,
        retry_after_seconds: int = DEFAULT_RETRY_SECONDS,
    ) -> None:
        now = now or datetime.now(timezone.utc)
        event.owner_token = None
        event.lease_expires_at = None
        event.safe_reason_code = safe_reason_code
        if event.retryable and event.attempt_count < event.max_attempts:
            event.status = OUTBOX_STATUS_RETRY_SCHEDULED
            event.next_retry_at = now + timedelta(seconds=retry_after_seconds)
        else:
            event.status = OUTBOX_STATUS_DEAD_LETTERED
            event.next_retry_at = None
            event.dead_lettered_at = now
        event.updated_at = now


class AuditEventOutboxProcessor:
    def __init__(
        self,
        db: Session,
        *,
        persist_audit: PersistAudit = persist_audit_payload,
        after_commit: AfterCommit | None = None,
    ) -> None:
        self.db = db
        self.outbox = AuditEventOutboxService(db)
        self.persist_audit = persist_audit
        self.after_commit = after_commit

    def process_due_events(
        self,
        *,
        owner_token: str,
        limit: int = DEFAULT_PROCESS_LIMIT,
    ) -> AuditEventOutboxProcessResult:
        recovered_count = self.outbox.recover_stale_leases()
        events = self.outbox.lease_due_events(owner_token=owner_token, limit=limit)
        # A crash during persistence must leave a durable lease for recovery.
        self.db.commit()
        processed_count = 0
        for event in events:
            audit_id: uuid.UUID | None = None
            try:
                if (
                    event.retryable
                    and event.attempt_count < event.max_attempts
                    and workflow_run_correlation_is_pending(self.db, event.payload)
                ):
                    raise AuditWorkflowRunPendingError("workflow_run_pending")
                audit_id = self.persist_audit(self.db, event.payload)
                if not self.outbox.mark_succeeded(event, owner_token=owner_token):
                    # Fencing failed: do not commit an AuditLog for a lease this
                    # worker no longer owns.
                    self.db.rollback()
                    continue
                # AuditLog insert and terminal Outbox transition are atomic.
                self.db.commit()
            except Exception as error:  # noqa: BLE001 - row retry owns failures
                self.db.rollback()
                audit_id = self._resolve_concurrent_winner(
                    event,
                    error,
                    owner_token=owner_token,
                )
                if audit_id is None:
                    safe_reason_code = (
                        "audit.workflow_run_pending"
                        if isinstance(error, AuditWorkflowRunPendingError)
                        else "audit.persistence_failed"
                    )
                    self.outbox.mark_retry_or_dead_letter(
                        event,
                        owner_token=owner_token,
                        safe_reason_code=safe_reason_code,
                    )
                    self.db.commit()
                    if isinstance(error, AuditWorkflowRunPendingError):
                        logger.info("[Audit] outbox workflow run correlation pending")
                    else:
                        logger.warning(
                            "[Audit] outbox persistence failed: error_type=%s",
                            type(error).__name__,
                        )
                    continue
            processed_count += 1
            self._run_after_commit(audit_id)
        return AuditEventOutboxProcessResult(
            processed_count=processed_count,
            recovered_count=recovered_count,
        )

    def _resolve_concurrent_winner(
        self,
        event: AuditEventOutbox,
        error: Exception,
        *,
        owner_token: str,
    ) -> uuid.UUID | None:
        if not isinstance(error, IntegrityError):
            return None
        try:
            audit_id = audit_id_from_payload(event.payload)
        except (KeyError, ValueError, TypeError):
            return None
        if self.db.get(AuditLog, audit_id) is None:
            return None
        if not self.outbox.mark_succeeded(event, owner_token=owner_token):
            self.db.rollback()
            return None
        self.db.commit()
        return audit_id

    def _run_after_commit(self, audit_id: uuid.UUID) -> None:
        if self.after_commit is None:
            return
        try:
            self.after_commit(audit_id)
        except Exception as error:  # noqa: BLE001 - reconciliation is fallback
            logger.warning(
                "[Audit] post-commit dispatch failed: error_type=%s",
                type(error).__name__,
            )
