from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from apps.shared.db.models.workflow_conversation_execution import (
    ConversationWorkflowExecutionAdmissionRecord,
)
from apps.workflow_engine.application.conversation_memory_admission import (
    ConversationExecutionAdmission,
    ConversationExecutionState,
)


class SqlAlchemyConversationExecutionUnitOfWork:
    def __init__(self, db: Session) -> None:
        self.db = db
        self._transaction = None

    def begin(self) -> None:
        if self._transaction is not None:
            raise RuntimeError("conversation execution transaction is already active")
        self._transaction = self.db.begin()

    def commit(self) -> None:
        if self._transaction is None:
            raise RuntimeError("conversation execution transaction is not active")
        self._transaction.commit()
        self._transaction = None

    def rollback(self) -> None:
        if self._transaction is not None:
            self._transaction.rollback()
            self._transaction = None


class SqlAlchemyConversationExecutionAdmissionRepository:
    def __init__(self, db: Session) -> None:
        self.db = db
        self._records: dict[object, ConversationWorkflowExecutionAdmissionRecord] = {}

    def find_by_dispatch(self, *, organization_id, dispatch_id):
        record = self.db.execute(
            select(ConversationWorkflowExecutionAdmissionRecord).where(
                ConversationWorkflowExecutionAdmissionRecord.organization_id
                == organization_id,
                ConversationWorkflowExecutionAdmissionRecord.dispatch_id == dispatch_id,
            )
        ).scalar_one_or_none()
        return _domain(record) if record is not None else None

    def lock_by_dispatch(self, *, organization_id, dispatch_id):
        record = self.db.execute(
            select(ConversationWorkflowExecutionAdmissionRecord)
            .where(
                ConversationWorkflowExecutionAdmissionRecord.organization_id
                == organization_id,
                ConversationWorkflowExecutionAdmissionRecord.dispatch_id == dispatch_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if record is None:
            return None
        domain = _domain(record)
        self._records[domain.id] = record
        return domain

    def add(self, admission: ConversationExecutionAdmission) -> None:
        record = _record(admission)
        self.db.add(record)
        self._records[admission.id] = record

    def save(self, admission: ConversationExecutionAdmission) -> None:
        record = self._records.get(admission.id)
        if record is None:
            record = self.db.get(
                ConversationWorkflowExecutionAdmissionRecord,
                admission.id,
            )
        if record is None:
            raise RuntimeError("conversation execution admission is missing")
        for field in (
            "state",
            "version",
            "lease_owner",
            "lease_generation",
            "lease_deadline",
            "attempt_id",
            "result_entry_id",
            "result_digest",
            "safe_failure_reason",
            "updated_at",
            "terminal_at",
            "retention_expires_at",
        ):
            value = getattr(admission, field)
            if field == "state":
                value = value.value
            setattr(record, field, value)


    def delete_expired_terminal(self, *, now, limit: int) -> int:
        candidate_ids = tuple(
            self.db.execute(
                select(
                    ConversationWorkflowExecutionAdmissionRecord.id
                )
                .where(
                    ConversationWorkflowExecutionAdmissionRecord.state.in_(
                        ("completed", "failed", "outcome_unknown")
                    ),
                    ConversationWorkflowExecutionAdmissionRecord.retention_expires_at
                    .is_not(None),
                    ConversationWorkflowExecutionAdmissionRecord.retention_expires_at
                    <= now,
                )
                .order_by(
                    ConversationWorkflowExecutionAdmissionRecord.retention_expires_at,
                    ConversationWorkflowExecutionAdmissionRecord.id,
                )
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
            .scalars()
            .all()
        )

        if not candidate_ids:
            return 0
        self.db.execute(
            delete(ConversationWorkflowExecutionAdmissionRecord).where(
                ConversationWorkflowExecutionAdmissionRecord.id.in_(candidate_ids)
            )
        )
        return len(candidate_ids)

def _record(
    admission: ConversationExecutionAdmission,
) -> ConversationWorkflowExecutionAdmissionRecord:
    return ConversationWorkflowExecutionAdmissionRecord(
        id=admission.id,
        organization_id=admission.organization_id,
        dispatch_id=admission.dispatch_id,
        session_id=admission.session_id,
        turn_id=admission.turn_id,
        workflow_id=admission.workflow_id,
        app_id=admission.app_id,
        deployment_id=admission.deployment_id,
        deployment_version=admission.deployment_version,
        request_fingerprint=admission.request_fingerprint,
        memory_contract_version=admission.memory_contract_version,
        mapping_version=admission.mapping_version,
        memory_policy_version=admission.memory_policy_version,
        storage_generation=admission.storage_generation,
        minimum_worker_capability=admission.minimum_worker_capability,
        execution_id=admission.execution_id,
        state=admission.state.value,
        version=admission.version,
        lease_owner=admission.lease_owner,
        lease_generation=admission.lease_generation,
        lease_deadline=admission.lease_deadline,
        attempt_id=admission.attempt_id,
        result_entry_id=admission.result_entry_id,
        result_digest=admission.result_digest,
        safe_failure_reason=admission.safe_failure_reason,
        created_at=admission.created_at,
        updated_at=admission.updated_at,
        terminal_at=admission.terminal_at,
        retention_expires_at=admission.retention_expires_at,
    )


def _domain(
    record: ConversationWorkflowExecutionAdmissionRecord,
) -> ConversationExecutionAdmission:
    return ConversationExecutionAdmission(
        id=record.id,
        organization_id=record.organization_id,
        dispatch_id=record.dispatch_id,
        session_id=record.session_id,
        turn_id=record.turn_id,
        workflow_id=record.workflow_id,
        app_id=record.app_id,
        deployment_id=record.deployment_id,
        deployment_version=record.deployment_version,
        request_fingerprint=record.request_fingerprint,
        memory_contract_version=record.memory_contract_version,
        mapping_version=record.mapping_version,
        memory_policy_version=record.memory_policy_version,
        storage_generation=record.storage_generation,
        minimum_worker_capability=record.minimum_worker_capability,
        execution_id=record.execution_id,
        state=ConversationExecutionState(record.state),
        version=record.version,
        lease_owner=record.lease_owner,
        lease_generation=record.lease_generation,
        lease_deadline=record.lease_deadline,
        attempt_id=record.attempt_id,
        result_entry_id=record.result_entry_id,
        result_digest=record.result_digest,
        safe_failure_reason=record.safe_failure_reason,
        created_at=record.created_at,
        updated_at=record.updated_at,
        terminal_at=record.terminal_at,
        retention_expires_at=record.retention_expires_at,
    )


__all__ = [
    "SqlAlchemyConversationExecutionAdmissionRepository",
    "SqlAlchemyConversationExecutionUnitOfWork",
]
