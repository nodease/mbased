from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable

from apps.shared.db.models.audit_log import AuditLog
from apps.shared.db.models.security_alert import (
    SecurityAlertReconciliationReceipt,
    SecurityAlertReconciliationWatermark,
)
from sqlalchemy import and_, exists, or_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import aliased


@dataclass(frozen=True)
class SecurityAlertReconciliationResult:
    processed_count: int


class SQLAlchemySecurityAlertReconciliationRepository:
    def __init__(self, db: Any, *, process_audit: Callable[[Any], None]):
        self.db = db
        self.process_audit = process_audit
        self.watermark = None
        self.processor_name = None

    def load_security_alert_watermark(self, *, processor_name: str):
        self.processor_name = processor_name
        self.watermark = (
            self.db.query(SecurityAlertReconciliationWatermark)
            .filter(
                SecurityAlertReconciliationWatermark.processor_name
                == processor_name
            )
            .with_for_update()
            .one()
        )
        return self.watermark

    def scan_security_alert_audits(
        self,
        *,
        started_at: datetime,
        replay_horizon: timedelta,
        batch_size: int,
    ):
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")

        candidate_receipt = aliased(SecurityAlertReconciliationReceipt)
        candidate_has_no_receipt = candidate_receipt.audit_log_id.is_(None)

        unprocessed_audit = aliased(AuditLog)
        unprocessed_receipt = aliased(SecurityAlertReconciliationReceipt)
        unprocessed_receipt_exists = (
            exists()
            .where(
                unprocessed_receipt.processor_name == self.processor_name,
                unprocessed_receipt.audit_log_id == unprocessed_audit.id,
            )
            .correlate(unprocessed_audit)
        )
        unprocessed_predecessor_exists = (
            exists()
            .where(
                *_replay_predecessor_conditions(
                    predecessor=unprocessed_audit,
                    candidate=AuditLog,
                    started_at=started_at,
                    replay_horizon=replay_horizon,
                ),
                ~unprocessed_receipt_exists,
            )
            .correlate(AuditLog)
        )

        discovered_audit = aliased(AuditLog)
        discovered_receipt = aliased(SecurityAlertReconciliationReceipt)
        stale_replay_exists = (
            exists()
            .where(
                *_replay_predecessor_conditions(
                    predecessor=discovered_audit,
                    candidate=AuditLog,
                    started_at=started_at,
                    replay_horizon=replay_horizon,
                ),
                discovered_receipt.processor_name == self.processor_name,
                discovered_receipt.audit_log_id == discovered_audit.id,
                discovered_receipt.discovered_generation
                > candidate_receipt.evaluated_generation,
            )
            .correlate(AuditLog, candidate_receipt)
        )
        return (
            self.db.query(AuditLog)
            .outerjoin(
                candidate_receipt,
                and_(
                    candidate_receipt.processor_name == self.processor_name,
                    candidate_receipt.audit_log_id == AuditLog.id,
                ),
            )
            .filter(AuditLog.occurred_at >= started_at)
            .filter(
                or_(
                    candidate_has_no_receipt,
                    unprocessed_predecessor_exists,
                    stale_replay_exists,
                )
            )
            .order_by(AuditLog.occurred_at, AuditLog.id)
            .limit(batch_size)
            .all()
        )

    def process_security_alert_audit(self, audit: Any) -> None:
        self.process_audit(audit)

    def mark_security_alert_audit_processed(
        self,
        *,
        audit_log_id: Any,
        generation: int,
    ) -> None:
        self.db.execute(
            insert(SecurityAlertReconciliationReceipt)
            .values(
                processor_name=self.processor_name,
                audit_log_id=audit_log_id,
                discovered_generation=generation,
                evaluated_generation=generation,
            )
            .on_conflict_do_update(
                index_elements=[
                    SecurityAlertReconciliationReceipt.processor_name,
                    SecurityAlertReconciliationReceipt.audit_log_id,
                ],
                set_={"evaluated_generation": generation},
            )
        )

    def advance_security_alert_watermark(
        self,
        *,
        occurred_at: datetime,
        audit_log_id: Any,
        generation: int,
    ) -> None:
        self.watermark.cursor_occurred_at = occurred_at
        self.watermark.cursor_audit_log_id = audit_log_id
        self.watermark.reconciliation_generation = generation

    def commit(self) -> None:
        self.db.commit()


def reconcile_security_alert_batch(
    repository,
    *,
    processor_name: str,
    replay_horizon: timedelta,
    batch_size: int,
) -> SecurityAlertReconciliationResult:
    watermark = repository.load_security_alert_watermark(
        processor_name=processor_name
    )
    audits = repository.scan_security_alert_audits(
        started_at=watermark.activation_started_at,
        replay_horizon=replay_horizon,
        batch_size=batch_size,
    )
    batch_generation = watermark.reconciliation_generation + 1
    for audit in audits:
        repository.process_security_alert_audit(audit)
        repository.mark_security_alert_audit_processed(
            audit_log_id=audit.id,
            generation=batch_generation,
        )

    latest_cursor = _latest_processed_cursor(watermark, audits)
    if audits and latest_cursor is not None:
        repository.advance_security_alert_watermark(
            occurred_at=latest_cursor[0],
            audit_log_id=latest_cursor[1],
            generation=batch_generation,
        )
    repository.commit()
    return SecurityAlertReconciliationResult(processed_count=len(audits))


def _replay_predecessor_conditions(
    *,
    predecessor,
    candidate,
    started_at: datetime,
    replay_horizon: timedelta,
):
    return (
        predecessor.occurred_at >= started_at,
        predecessor.actor_id.is_not(None),
        predecessor.actor_type == "user",
        predecessor.category == "action",
        predecessor.status == "failure",
        or_(
            and_(
                predecessor.action == "permission.denied",
                predecessor.target_type.is_not(None),
                predecessor.target_id.is_not(None),
            ),
            predecessor.action == "policy.block",
        ),
        candidate.actor_id == predecessor.actor_id,
        candidate.actor_type == "user",
        candidate.category == "action",
        candidate.status == "failure",
        candidate.action == predecessor.action,
        candidate.audit_metadata["organization_id"].astext
        == predecessor.audit_metadata["organization_id"].astext,
        or_(
            predecessor.occurred_at < candidate.occurred_at,
            and_(
                predecessor.occurred_at == candidate.occurred_at,
                predecessor.id <= candidate.id,
            ),
        ),
        candidate.occurred_at <= predecessor.occurred_at + replay_horizon,
    )


def _latest_processed_cursor(watermark, audits):
    cursors = [(audit.occurred_at, audit.id) for audit in audits]
    if watermark.cursor_occurred_at is not None:
        cursors.append(
            (watermark.cursor_occurred_at, watermark.cursor_audit_log_id)
        )
    return max(cursors) if cursors else None
