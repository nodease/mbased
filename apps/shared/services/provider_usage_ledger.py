"""Durable provider-attempt usage ledger and compatibility projection.

Every public mutation is intended to run in a short, independently-owned
transaction.  No method performs provider network I/O or stores provider
payloads, credentials, prompts, completions, or arbitrary exception text.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from apps.shared.audit.actions import AuditAction
from apps.shared.db.models.audit_log import AuditEventOutbox
from apps.shared.db.models.cost_optimizer import CostOptimizerCandidate
from apps.shared.db.models.llm import (
    LLMCredential,
    LLMModel,
    LLMUsageLog,
    ProviderExecutionCapabilityRecord,
)
from apps.shared.db.models.provider_usage import (
    ProviderUsageCorrectionRecord,
    ProviderUsageOperationRecord,
)
from apps.shared.db.models.user import User
from apps.shared.db.models.workflow import Workflow
from apps.shared.db.models.workflow_run import WorkflowRun
from apps.shared.domain.provider_execution_capability import (
    CapabilityPurpose,
    PrincipalKind,
    ProviderExecutionBinding,
    RuntimeIdentityContext,
    RuntimePrincipal,
)
from apps.shared.domain.provider_usage_ledger import (
    ProviderUsageIntentSnapshot,
    ProviderUsageLedgerError,
    ProviderUsageMeasurement,
    ProviderUsageOperation,
    ProviderUsageOperationKey,
    ProviderUsageState,
)
from apps.shared.domain.workflow_node_location import (
    CanonicalWorkflowNodeLocation,
)
from apps.shared.services.provider_execution_capability import (
    ProviderExecutionCapabilityAdmissionCommand,
    ProviderExecutionCapabilityService,
    ProviderExecutionPolicyError,
)
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

_MICROUSD_PER_USD = Decimal("1000000")
_PROJECTION_RETRY_DELAY = timedelta(minutes=1)
_PROJECTION_LEASE_DURATION = timedelta(minutes=2)
_CORRECTION_SOURCES = frozenset(
    {"provider_reconciliation", "billing_import", "operator"}
)
_CORRECTION_REASONS = frozenset(
    {"provider_reported_usage", "billing_reconciliation"}
)


@dataclass(frozen=True, slots=True)
class ProviderUsageIntentCommand:
    snapshot: ProviderUsageIntentSnapshot
    workflow_run_id: uuid.UUID | None = None
    cost_optimizer_candidate_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class ProviderUsageCorrectionCommand:
    operation_id: uuid.UUID
    expected_usage_revision: int
    correction_key: str
    measurement: ProviderUsageMeasurement
    source: str
    reason_code: str


@dataclass(frozen=True, slots=True)
class ProviderUsageProjectionBatchResult:
    claimed_count: int
    projected_count: int
    failed_count: int


class ProviderUsageLedgerService:
    def __init__(self, *, capability_service: Any = None) -> None:
        self._capability_service = (
            capability_service
            if capability_service is not None
            else ProviderExecutionCapabilityService
        )

    def record_intent(
        self,
        db: Session,
        *,
        command: ProviderUsageIntentCommand,
        now: datetime | None = None,
    ) -> ProviderUsageOperation:
        """Reserve the canonical attempt without authorizing a provider replay."""

        intent_now = now or self._database_clock_now(db)
        candidate = ProviderUsageOperation.intent(
            operation_id=uuid.uuid4(),
            snapshot=command.snapshot,
            now=intent_now,
        )
        existing = self._operation_for_key(db, candidate.key, for_update=True)
        if existing is not None:
            operation = self._domain_operation(existing)
            operation.require_same_intent(command.snapshot)
            self._attach_optional_correlations(existing, command=command)
            self._commit_or_raise(db, "provider_usage.intent_commit_failed")
            return self._domain_operation(existing)

        self._require_live_capability_snapshot(db, command.snapshot)
        record = self._record_from_operation(
            candidate,
            workflow_run_id=command.workflow_run_id,
            cost_optimizer_candidate_id=command.cost_optimizer_candidate_id,
        )
        db.add(record)
        try:
            db.commit()
        except IntegrityError:
            self._rollback_safely(db)
            concurrent = self._operation_for_key(db, candidate.key, for_update=True)
            if concurrent is None:
                raise ProviderUsageLedgerError(
                    "provider_usage.intent_commit_failed"
                ) from None
            operation = self._domain_operation(concurrent)
            operation.require_same_intent(command.snapshot)
            self._attach_optional_correlations(concurrent, command=command)
            self._commit_or_raise(db, "provider_usage.intent_commit_failed")
            return self._domain_operation(concurrent)
        except SQLAlchemyError as exc:
            self._rollback_safely(db)
            raise ProviderUsageLedgerError(
                "provider_usage.intent_commit_failed"
            ) from exc
        return candidate

    def mark_provider_started(
        self,
        db: Session,
        *,
        operation_id: uuid.UUID,
        expected_state_version: int,
        now: datetime | None = None,
    ) -> ProviderUsageOperation:
        """Re-admit the exact snapshot and commit the final pre-I/O fence."""

        record = self._operation_for_id(db, operation_id, for_update=True)
        operation = self._domain_operation(record)
        if record.state_version != expected_state_version:
            self._rollback_safely(db)
            raise ProviderUsageLedgerError("provider_usage.state_conflict")
        try:
            lease = self._capability_service.admit_capability(
                db,
                command=self._admission_command(operation),
            )
        except ProviderExecutionPolicyError as exc:
            self._rollback_safely(db)
            raise ProviderUsageLedgerError("provider_usage.capability_stale") from exc
        self._require_admission_matches_snapshot(lease, operation.snapshot)
        started_at = now or self._database_clock_now(db)
        updated = operation.mark_provider_started(now=started_at)
        self._apply_operation(record, updated)
        self._commit_or_raise(db, "provider_usage.start_commit_failed")
        return updated

    def record_success(
        self,
        db: Session,
        *,
        operation_id: uuid.UUID,
        expected_state_version: int,
        measurement: ProviderUsageMeasurement,
        now: datetime | None = None,
    ) -> ProviderUsageOperation:
        record = self._operation_for_id(db, operation_id, for_update=True)
        operation = self._domain_operation(record)
        updated = operation.record_success(
            measurement=measurement,
            now=now or self._database_clock_now(db),
        )
        if updated is operation:
            self._commit_or_raise(db, "provider_usage.terminal_commit_failed")
            return operation
        self._require_state_version(record, expected_state_version)
        self._apply_operation(record, updated)
        self._enqueue_terminal_audit(db, record=record, operation=updated)
        self._commit_or_raise(db, "provider_usage.terminal_commit_failed")
        return updated

    def mark_outcome_unknown(
        self,
        db: Session,
        *,
        operation_id: uuid.UUID,
        expected_state_version: int,
        reason_code: str,
        now: datetime | None = None,
    ) -> ProviderUsageOperation:
        record = self._operation_for_id(db, operation_id, for_update=True)
        operation = self._domain_operation(record)
        updated = operation.mark_outcome_unknown(
            reason_code=reason_code,
            now=now or self._database_clock_now(db),
        )
        if updated is operation:
            self._commit_or_raise(db, "provider_usage.terminal_commit_failed")
            return operation
        self._require_state_version(record, expected_state_version)
        self._apply_operation(record, updated)
        self._enqueue_terminal_audit(db, record=record, operation=updated)
        self._commit_or_raise(db, "provider_usage.terminal_commit_failed")
        return updated

    def record_definitive_failure(
        self,
        db: Session,
        *,
        operation_id: uuid.UUID,
        expected_state_version: int,
        reason_code: str,
        now: datetime | None = None,
    ) -> ProviderUsageOperation:
        record = self._operation_for_id(db, operation_id, for_update=True)
        operation = self._domain_operation(record)
        updated = operation.record_definitive_failure(
            reason_code=reason_code,
            now=now or self._database_clock_now(db),
        )
        if updated is operation:
            self._commit_or_raise(db, "provider_usage.terminal_commit_failed")
            return operation
        self._require_state_version(record, expected_state_version)
        self._apply_operation(record, updated)
        self._enqueue_terminal_audit(db, record=record, operation=updated)
        self._commit_or_raise(db, "provider_usage.terminal_commit_failed")
        return updated

    def reconcile_success(
        self,
        db: Session,
        *,
        organization_id: uuid.UUID,
        operation_id: uuid.UUID,
        expected_state_version: int,
        measurement: ProviderUsageMeasurement,
        now: datetime | None = None,
    ) -> ProviderUsageOperation:
        record = self._operation_for_id(
            db,
            operation_id,
            organization_id=organization_id,
            for_update=True,
        )
        self._require_state_version(record, expected_state_version)
        operation = self._domain_operation(record)
        updated = operation.reconcile_success(
            measurement=measurement,
            now=now or self._database_clock_now(db),
        )
        if updated is operation:
            self._commit_or_raise(db, "provider_usage.reconcile_commit_failed")
            return operation
        self._apply_operation(record, updated)
        self._commit_or_raise(db, "provider_usage.reconcile_commit_failed")
        return updated

    def reconcile_definitive_failure(
        self,
        db: Session,
        *,
        organization_id: uuid.UUID,
        operation_id: uuid.UUID,
        expected_state_version: int,
        reason_code: str,
        now: datetime | None = None,
    ) -> ProviderUsageOperation:
        record = self._operation_for_id(
            db,
            operation_id,
            organization_id=organization_id,
            for_update=True,
        )
        self._require_state_version(record, expected_state_version)
        operation = self._domain_operation(record)
        updated = operation.reconcile_definitive_failure(
            reason_code=reason_code,
            now=now or self._database_clock_now(db),
        )
        if updated is operation:
            self._commit_or_raise(db, "provider_usage.reconcile_commit_failed")
            return operation
        self._apply_operation(record, updated)
        self._commit_or_raise(db, "provider_usage.reconcile_commit_failed")
        return updated

    def apply_correction(
        self,
        db: Session,
        *,
        command: ProviderUsageCorrectionCommand,
        now: datetime | None = None,
    ) -> ProviderUsageOperation:
        if (
            not command.correction_key
            or len(command.correction_key) > 128
            or command.source not in _CORRECTION_SOURCES
            or command.reason_code not in _CORRECTION_REASONS
        ):
            raise ValueError("provider usage correction metadata is invalid")
        record = self._operation_for_id(db, command.operation_id, for_update=True)
        existing = (
            db.query(ProviderUsageCorrectionRecord)
            .filter(
                ProviderUsageCorrectionRecord.operation_id == command.operation_id,
                ProviderUsageCorrectionRecord.correction_key == command.correction_key,
            )
            .one_or_none()
        )
        if existing is not None:
            if not self._correction_matches(existing, command):
                self._rollback_safely(db)
                raise ProviderUsageLedgerError("provider_usage.correction_conflict")
            self._commit_or_raise(db, "provider_usage.correction_commit_failed")
            return self._domain_operation(record)

        operation = self._domain_operation(record)
        updated = operation.apply_correction(
            measurement=command.measurement,
            expected_usage_revision=command.expected_usage_revision,
        )
        db.add(
            ProviderUsageCorrectionRecord(
                id=uuid.uuid4(),
                operation_id=operation.id,
                correction_key=command.correction_key,
                base_usage_revision=operation.usage_revision,
                resulting_usage_revision=updated.usage_revision,
                prompt_tokens=command.measurement.prompt_tokens,
                completion_tokens=command.measurement.completion_tokens,
                total_cost_microusd=command.measurement.total_cost_microusd,
                latency_ms=command.measurement.latency_ms,
                source=command.source,
                reason_code=command.reason_code,
                created_at=now or self._database_clock_now(db),
            )
        )
        self._apply_operation(record, updated)
        record.projection_status = "pending"
        record.projection_reason_code = None
        record.projection_next_attempt_at = None
        self._commit_or_raise(db, "provider_usage.correction_commit_failed")
        return updated

    def project_compatibility_usage(
        self,
        db: Session,
        *,
        operation_id: uuid.UUID,
        now: datetime | None = None,
    ) -> LLMUsageLog:
        """Idempotently converge the legacy projection without being canonical."""

        record = self._operation_for_id(db, operation_id, for_update=True)
        operation = self._domain_operation(record)
        if operation.state is not ProviderUsageState.SUCCEEDED:
            self._rollback_safely(db)
            raise ProviderUsageLedgerError("provider_usage.projection_not_allowed")
        projected_at = now or self._database_clock_now(db)
        try:
            usage = (
                db.query(LLMUsageLog)
                .filter(LLMUsageLog.provider_usage_operation_id == operation_id)
                .one_or_none()
            )
            values = self._usage_projection_values(
                operation,
                cost_optimizer_candidate_id=record.cost_optimizer_candidate_id,
            )
            values.update(
                self._live_projection_reference_values(
                    db,
                    record=record,
                    operation=operation,
                )
            )
            run = self._compatible_workflow_run(db, record)
            target_run_id = run.id if run is not None else None
            if usage is None:
                usage = LLMUsageLog(**values, workflow_run_id=target_run_id)
                db.add(usage)
                db.flush()
                if run is not None:
                    self._apply_run_usage_delta(
                        run,
                        prompt_delta=operation.measurement.prompt_tokens,
                        completion_delta=operation.measurement.completion_tokens,
                        cost_delta=self._cost_usd(operation),
                    )
            else:
                self._require_projection_identity(
                    usage,
                    operation,
                    workflow_id=values["workflow_id"],
                )
                previous_prompt = int(usage.prompt_tokens or 0)
                previous_completion = int(usage.completion_tokens or 0)
                previous_cost = Decimal(str(usage.total_cost or 0))
                previous_run_id = usage.workflow_run_id
                if previous_run_id is not None and previous_run_id != target_run_id:
                    raise ProviderUsageLedgerError(
                        "provider_usage.workflow_run_conflict"
                    )
                for name, value in values.items():
                    setattr(usage, name, value)
                if previous_run_id is None and target_run_id is not None:
                    usage.workflow_run_id = target_run_id
                    self._apply_run_usage_delta(
                        run,
                        prompt_delta=operation.measurement.prompt_tokens,
                        completion_delta=operation.measurement.completion_tokens,
                        cost_delta=self._cost_usd(operation),
                    )
                elif run is not None and target_run_id is not None:
                    self._apply_run_usage_delta(
                        run,
                        prompt_delta=operation.measurement.prompt_tokens
                        - previous_prompt,
                        completion_delta=operation.measurement.completion_tokens
                        - previous_completion,
                        cost_delta=self._cost_usd(operation) - previous_cost,
                    )
            record.projection_status = (
                "awaiting_workflow_run"
                if record.workflow_run_id is not None and target_run_id is None
                else "projected"
            )
            record.projected_usage_log_id = usage.id
            record.projected_usage_revision = operation.usage_revision
            record.projected_at = projected_at
            record.projection_attempts += 1
            record.projection_next_attempt_at = None
            record.projection_reason_code = None
            record.updated_at = projected_at
            db.commit()
        except ProviderUsageLedgerError as exc:
            self._rollback_safely(db)
            terminal_reason = {
                "provider_usage.projection_conflict": "projection_conflict",
                "provider_usage.workflow_run_conflict": "workflow_run_conflict",
                "provider_usage.record_invalid": "projection_record_invalid",
                "provider_usage.projection_principal_unavailable": (
                    "projection_principal_unavailable"
                ),
            }.get(exc.code)
            if terminal_reason is not None:
                self._record_projection_failure(
                    db,
                    operation_id=operation_id,
                    now=projected_at,
                    retryable=False,
                    reason_code=terminal_reason,
                )
            raise
        except SQLAlchemyError as exc:
            self._rollback_safely(db)
            self._record_projection_failure(
                db,
                operation_id=operation_id,
                now=projected_at,
                retryable=True,
                reason_code="projection_commit_failed",
            )
            raise ProviderUsageLedgerError(
                "provider_usage.projection_commit_failed"
            ) from exc
        return usage

    def claim_pending_projection_ids(
        self,
        db: Session,
        *,
        limit: int = 100,
        now: datetime | None = None,
    ) -> tuple[uuid.UUID, ...]:
        """Lease a bounded projection batch without holding locks while projecting."""

        self._require_batch_limit(limit)
        claimed_at = now or self._database_clock_now(db)
        revision_projection_ready = and_(
            ProviderUsageOperationRecord.projection_status.in_(
                ("pending", "retryable_failure")
            ),
            or_(
                ProviderUsageOperationRecord.projected_usage_revision.is_(None),
                ProviderUsageOperationRecord.projected_usage_revision
                < ProviderUsageOperationRecord.usage_revision,
            ),
        )
        late_workflow_run_ready = and_(
            ProviderUsageOperationRecord.projection_status.in_(
                ("awaiting_workflow_run", "retryable_failure")
            ),
            ProviderUsageOperationRecord.workflow_run_id.is_not(None),
            exists(
                select(WorkflowRun.id).where(
                    WorkflowRun.id
                    == ProviderUsageOperationRecord.workflow_run_id
                )
            ),
            exists(
                select(LLMUsageLog.id).where(
                    LLMUsageLog.provider_usage_operation_id
                    == ProviderUsageOperationRecord.id,
                    LLMUsageLog.workflow_run_id.is_(None),
                )
            ),
        )
        records = (
            db.query(ProviderUsageOperationRecord)
            .filter(
                ProviderUsageOperationRecord.state
                == ProviderUsageState.SUCCEEDED.value,
                or_(
                    revision_projection_ready,
                    late_workflow_run_ready,
                ),
                or_(
                    ProviderUsageOperationRecord.projection_next_attempt_at.is_(None),
                    ProviderUsageOperationRecord.projection_next_attempt_at
                    <= claimed_at,
                ),
            )
            .order_by(
                ProviderUsageOperationRecord.provider_started_at,
                ProviderUsageOperationRecord.id,
            )
            .with_for_update(skip_locked=True)
            .limit(limit)
            .all()
        )
        for record in records:
            record.projection_next_attempt_at = (
                claimed_at + _PROJECTION_LEASE_DURATION
            )
            record.updated_at = claimed_at
        self._commit_or_raise(db, "provider_usage.projection_claim_failed")
        return tuple(record.id for record in records)

    def reconcile_pending_projections(
        self,
        db: Session,
        *,
        limit: int = 100,
        now: datetime | None = None,
    ) -> ProviderUsageProjectionBatchResult:
        operation_ids = self.claim_pending_projection_ids(
            db,
            limit=limit,
            now=now,
        )
        projected_count = 0
        failed_count = 0
        for operation_id in operation_ids:
            try:
                self.project_compatibility_usage(
                    db,
                    operation_id=operation_id,
                    now=now,
                )
            except ProviderUsageLedgerError:
                failed_count += 1
            else:
                projected_count += 1
        return ProviderUsageProjectionBatchResult(
            claimed_count=len(operation_ids),
            projected_count=projected_count,
            failed_count=failed_count,
        )

    def reconcile_stale_provider_started(
        self,
        db: Session,
        *,
        stale_after: timedelta,
        limit: int = 100,
        now: datetime | None = None,
    ) -> tuple[uuid.UUID, ...]:
        """Classify abandoned started fences as unknown; never call a provider."""

        self._require_batch_limit(limit)
        if stale_after <= timedelta(0):
            raise ValueError("stale_after must be positive")
        reconciled_at = now or self._database_clock_now(db)
        stale_before = reconciled_at - stale_after
        records = (
            db.query(ProviderUsageOperationRecord)
            .filter(
                ProviderUsageOperationRecord.state
                == ProviderUsageState.PROVIDER_STARTED.value,
                ProviderUsageOperationRecord.provider_started_at <= stale_before,
            )
            .order_by(
                ProviderUsageOperationRecord.provider_started_at,
                ProviderUsageOperationRecord.id,
            )
            .with_for_update(skip_locked=True)
            .limit(limit)
            .all()
        )
        for record in records:
            operation = self._domain_operation(record)
            updated = operation.mark_outcome_unknown(
                reason_code="stale_provider_started",
                now=reconciled_at,
            )
            self._apply_operation(record, updated)
            self._enqueue_terminal_audit(
                db,
                record=record,
                operation=updated,
            )
        self._commit_or_raise(db, "provider_usage.reconcile_commit_failed")
        return tuple(record.id for record in records)

    @staticmethod
    def _admission_command(
        operation: ProviderUsageOperation,
    ) -> ProviderExecutionCapabilityAdmissionCommand:
        snapshot = operation.snapshot
        return ProviderExecutionCapabilityAdmissionCommand(
            capability_id=snapshot.capability_id,
            capability_revision=snapshot.capability_revision,
            binding=snapshot.binding,
            requested_input_tokens=snapshot.admitted_input_tokens,
            requested_output_tokens=snapshot.admitted_output_tokens,
        )

    @staticmethod
    def _record_from_operation(
        operation: ProviderUsageOperation,
        *,
        workflow_run_id: uuid.UUID | None,
        cost_optimizer_candidate_id: uuid.UUID | None,
    ) -> ProviderUsageOperationRecord:
        snapshot = operation.snapshot
        binding = snapshot.binding
        identities = snapshot.identities
        credential_principal_id = identities.credential_principal.reference_id
        billing_principal_id = identities.billing_principal.reference_id
        if credential_principal_id is None or billing_principal_id is None:
            raise ProviderUsageLedgerError("provider_usage.intent_invalid")
        measurement = operation.measurement
        return ProviderUsageOperationRecord(
            id=operation.id,
            organization_id=binding.organization_id,
            workflow_id=binding.workflow_id,
            deployment_id=binding.deployment_id,
            deployment_version=binding.deployment_version,
            node_id=binding.node_id,
            container_path=CanonicalWorkflowNodeLocation(
                binding.container_path,
                binding.node_id,
            ).to_container_path_payload(),
            node_invocation_id=binding.node_invocation_id,
            execution_admission_id=binding.execution_admission_id,
            provider_attempt_id=binding.provider_attempt_id,
            purpose=binding.purpose.value,
            capability_id=snapshot.capability_id,
            capability_revision=snapshot.capability_revision,
            capability_expires_at=snapshot.expires_at,
            policy_id=snapshot.policy_id,
            policy_revision=snapshot.policy_revision,
            provider_id=snapshot.provider_id,
            model_id=snapshot.model_id,
            model_api_id=snapshot.model_api_id,
            credential_id=snapshot.credential_id,
            permission_revision=snapshot.permission_revision,
            relation_revision=snapshot.relation_revision,
            egress_revision=snapshot.egress_revision,
            pricing_revision=snapshot.pricing_revision,
            input_price_per_1k=Decimal(snapshot.input_price_per_1k),
            output_price_per_1k=Decimal(snapshot.output_price_per_1k),
            input_token_cap=snapshot.input_token_cap,
            output_token_cap=snapshot.output_token_cap,
            cost_cap_microusd=snapshot.cost_cap_microusd,
            admitted_input_tokens=snapshot.admitted_input_tokens,
            admitted_output_tokens=snapshot.admitted_output_tokens,
            execution_subject_kind=identities.execution_subject.kind.value,
            execution_subject_id=identities.execution_subject.reference_id,
            credential_principal_kind=identities.credential_principal.kind.value,
            credential_principal_id=credential_principal_id,
            billing_principal_kind=identities.billing_principal.kind.value,
            billing_principal_id=billing_principal_id,
            audit_actor_kind=identities.audit_actor.kind.value,
            audit_actor_id=identities.audit_actor.reference_id,
            state=operation.state.value,
            state_version=operation.state_version,
            safe_reason_code=operation.reason_code,
            intent_created_at=operation.intent_created_at,
            provider_started_at=operation.provider_started_at,
            terminal_at=operation.terminal_at,
            prompt_tokens=measurement.prompt_tokens if measurement else None,
            completion_tokens=measurement.completion_tokens if measurement else None,
            total_cost_microusd=(
                measurement.total_cost_microusd if measurement else None
            ),
            latency_ms=measurement.latency_ms if measurement else None,
            usage_revision=operation.usage_revision,
            workflow_run_id=workflow_run_id,
            cost_optimizer_candidate_id=cost_optimizer_candidate_id,
            projection_status="pending",
            projection_attempts=0,
            created_at=operation.intent_created_at,
            updated_at=operation.intent_created_at,
        )

    @staticmethod
    def _domain_operation(record: ProviderUsageOperationRecord) -> ProviderUsageOperation:
        try:
            identities = RuntimeIdentityContext(
                execution_subject=RuntimePrincipal(
                    PrincipalKind(record.execution_subject_kind),
                    record.execution_subject_id,
                ),
                credential_principal=RuntimePrincipal(
                    PrincipalKind(record.credential_principal_kind),
                    record.credential_principal_id,
                ),
                billing_principal=RuntimePrincipal(
                    PrincipalKind(record.billing_principal_kind),
                    record.billing_principal_id,
                ),
                audit_actor=RuntimePrincipal(
                    PrincipalKind(record.audit_actor_kind),
                    record.audit_actor_id,
                ),
            )
            location = CanonicalWorkflowNodeLocation.from_container_path_payload(
                container_path=record.container_path,
                node_id=record.node_id,
            )
            snapshot = ProviderUsageIntentSnapshot(
                binding=ProviderExecutionBinding(
                    organization_id=record.organization_id,
                    workflow_id=record.workflow_id,
                    deployment_id=record.deployment_id,
                    deployment_version=record.deployment_version,
                    node_id=record.node_id,
                    node_invocation_id=record.node_invocation_id,
                    execution_admission_id=record.execution_admission_id,
                    provider_attempt_id=record.provider_attempt_id,
                    purpose=CapabilityPurpose(record.purpose),
                    container_path=location.container_path,
                ),
                capability_id=record.capability_id,
                capability_revision=record.capability_revision,
                policy_id=record.policy_id,
                policy_revision=record.policy_revision,
                provider_id=record.provider_id,
                model_id=record.model_id,
                model_api_id=record.model_api_id,
                credential_id=record.credential_id,
                identities=identities,
                permission_revision=record.permission_revision,
                relation_revision=record.relation_revision,
                egress_revision=record.egress_revision,
                pricing_revision=record.pricing_revision,
                input_price_per_1k=str(record.input_price_per_1k),
                output_price_per_1k=str(record.output_price_per_1k),
                input_token_cap=record.input_token_cap,
                output_token_cap=record.output_token_cap,
                cost_cap_microusd=record.cost_cap_microusd,
                admitted_input_tokens=record.admitted_input_tokens,
                admitted_output_tokens=record.admitted_output_tokens,
                expires_at=record.capability_expires_at,
            )
            base = ProviderUsageOperation.intent(
                operation_id=record.id,
                snapshot=snapshot,
                now=record.intent_created_at,
            )
            measurement = None
            if record.state == ProviderUsageState.SUCCEEDED.value:
                measurement = ProviderUsageMeasurement(
                    prompt_tokens=record.prompt_tokens,
                    completion_tokens=record.completion_tokens,
                    total_cost_microusd=record.total_cost_microusd,
                    latency_ms=record.latency_ms,
                )
            return replace(
                base,
                state=ProviderUsageState(record.state),
                state_version=record.state_version,
                provider_started_at=record.provider_started_at,
                terminal_at=record.terminal_at,
                reason_code=record.safe_reason_code,
                measurement=measurement,
                usage_revision=record.usage_revision,
            )
        except (TypeError, ValueError) as exc:
            raise ProviderUsageLedgerError("provider_usage.record_invalid") from exc

    @staticmethod
    def _apply_operation(
        record: ProviderUsageOperationRecord,
        operation: ProviderUsageOperation,
    ) -> None:
        measurement = operation.measurement
        record.state = operation.state.value
        record.state_version = operation.state_version
        record.safe_reason_code = operation.reason_code
        record.provider_started_at = operation.provider_started_at
        record.terminal_at = operation.terminal_at
        record.prompt_tokens = measurement.prompt_tokens if measurement else None
        record.completion_tokens = (
            measurement.completion_tokens if measurement else None
        )
        record.total_cost_microusd = (
            measurement.total_cost_microusd if measurement else None
        )
        record.latency_ms = measurement.latency_ms if measurement else None
        record.usage_revision = operation.usage_revision
        record.updated_at = operation.terminal_at or operation.provider_started_at

    @staticmethod
    def _usage_projection_values(
        operation: ProviderUsageOperation,
        *,
        cost_optimizer_candidate_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        if operation.measurement is None or operation.provider_started_at is None:
            raise ProviderUsageLedgerError("provider_usage.projection_not_allowed")
        snapshot = operation.snapshot
        principal_id = snapshot.identities.credential_principal.reference_id
        if principal_id is None:
            raise ProviderUsageLedgerError("provider_usage.record_invalid")
        return {
            "user_id": principal_id,
            "organization_id": snapshot.binding.organization_id,
            "credential_id": snapshot.credential_id,
            "model_id": snapshot.model_id,
            "workflow_id": snapshot.binding.workflow_id,
            "cost_optimizer_candidate_id": cost_optimizer_candidate_id,
            "node_id": snapshot.binding.node_id,
            "prompt_tokens": operation.measurement.prompt_tokens,
            "completion_tokens": operation.measurement.completion_tokens,
            "total_cost": ProviderUsageLedgerService._cost_usd(operation),
            "latency_ms": operation.measurement.latency_ms,
            "status": "success",
            "error_message": None,
            "provider_usage_operation_id": operation.id,
            "provider_usage_revision": operation.usage_revision,
            "created_at": operation.provider_started_at,
        }

    @staticmethod
    def _new_audit_outbox(
        operation: ProviderUsageOperation,
        *,
        workflow_run_id: uuid.UUID | None = None,
    ) -> tuple[uuid.UUID, AuditEventOutbox]:
        if operation.terminal_at is None:
            raise ProviderUsageLedgerError("provider_usage.audit_not_allowed")
        event_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"nodease:provider-usage:{operation.id}:llm.call",
        )
        snapshot = operation.snapshot
        actor = snapshot.identities.audit_actor
        metadata: dict[str, Any] = {
            "organization_id": str(operation.key.organization_id),
            "workflow_id": str(snapshot.binding.workflow_id),
            "provider_usage_operation_id": str(operation.id),
            "provider_execution_capability_id": str(snapshot.capability_id),
            "provider_execution_capability_revision": snapshot.capability_revision,
            "purpose": operation.key.purpose.value,
            "provider_id": str(snapshot.provider_id),
            "model_id": str(snapshot.model_id),
            "provider_usage_status": operation.state.value,
        }
        if operation.reason_code is not None:
            metadata["safe_reason_code"] = operation.reason_code
        payload = {
            "id": str(event_id),
            "action": AuditAction.LLM_CALL,
            "category": "action",
            "actor_id": (
                str(actor.reference_id) if actor.reference_id is not None else None
            ),
            "actor_type": actor.kind.value,
            "target_type": "provider_usage_operation",
            "target_id": str(operation.id),
            "before": None,
            "after": None,
            "status": (
                "success"
                if operation.state is ProviderUsageState.SUCCEEDED
                else "failure"
            ),
            "audit_metadata": metadata,
            "workflow_run_id": (
                str(workflow_run_id) if workflow_run_id is not None else None
            ),
            "workflow_node_run_id": None,
            "occurred_at": operation.terminal_at.astimezone(timezone.utc).isoformat(),
        }
        return event_id, AuditEventOutbox(
            id=event_id,
            payload=payload,
            status="pending",
            attempt_count=0,
            max_attempts=5,
            retryable=True,
            idempotency_key=f"provider-usage:{operation.id}:llm.call",
        )

    def _enqueue_terminal_audit(
        self,
        db: Session,
        *,
        record: ProviderUsageOperationRecord,
        operation: ProviderUsageOperation,
    ) -> None:
        if record.audit_event_id is not None:
            return
        event_id, outbox = self._new_audit_outbox(
            operation,
            workflow_run_id=record.workflow_run_id,
        )
        record.audit_event_id = event_id
        db.add(outbox)

    @staticmethod
    def _require_projection_identity(
        usage: LLMUsageLog,
        operation: ProviderUsageOperation,
        *,
        workflow_id: uuid.UUID | None,
    ) -> None:
        snapshot = operation.snapshot
        principal_id = snapshot.identities.credential_principal.reference_id
        if (
            usage.provider_usage_operation_id != operation.id
            or usage.user_id != principal_id
            or usage.organization_id != snapshot.binding.organization_id
            or (
                usage.workflow_id is not None
                and usage.workflow_id != workflow_id
            )
            or usage.node_id != snapshot.binding.node_id
        ):
            raise ProviderUsageLedgerError("provider_usage.projection_conflict")

    @staticmethod
    def _live_projection_reference_values(
        db: Session,
        *,
        record: ProviderUsageOperationRecord,
        operation: ProviderUsageOperation,
    ) -> dict[str, uuid.UUID | None]:
        """Resolve only compatibility FKs; immutable ledger snapshots stay intact."""

        snapshot = operation.snapshot
        principal_id = snapshot.identities.credential_principal.reference_id
        if principal_id is None or db.get(User, principal_id) is None:
            raise ProviderUsageLedgerError(
                "provider_usage.projection_principal_unavailable"
            )

        def existing_id(model: type[Any], reference_id: uuid.UUID | None):
            if reference_id is None or db.get(model, reference_id) is None:
                return None
            return reference_id

        return {
            "credential_id": existing_id(LLMCredential, snapshot.credential_id),
            "model_id": existing_id(LLMModel, snapshot.model_id),
            "workflow_id": existing_id(Workflow, snapshot.binding.workflow_id),
            "cost_optimizer_candidate_id": existing_id(
                CostOptimizerCandidate,
                record.cost_optimizer_candidate_id,
            ),
        }

    @staticmethod
    def _compatible_workflow_run(
        db: Session,
        record: ProviderUsageOperationRecord,
    ) -> WorkflowRun | None:
        if record.workflow_run_id is None:
            return None
        run = (
            db.query(WorkflowRun)
            .filter(WorkflowRun.id == record.workflow_run_id)
            .populate_existing()
            .with_for_update()
            .one_or_none()
        )
        if run is None:
            return None
        workflow = db.get(Workflow, run.workflow_id)
        if (
            run.workflow_id != record.workflow_id
            or workflow is None
            or workflow.organization_id != record.organization_id
        ):
            raise ProviderUsageLedgerError("provider_usage.workflow_run_conflict")
        return run

    @staticmethod
    def _apply_run_usage_delta(
        run: WorkflowRun,
        *,
        prompt_delta: int,
        completion_delta: int,
        cost_delta: Decimal,
    ) -> None:
        run.total_tokens = int(run.total_tokens or 0) + prompt_delta + completion_delta
        run.total_cost = Decimal(str(run.total_cost or 0)) + cost_delta

    @staticmethod
    def _cost_usd(operation: ProviderUsageOperation) -> Decimal:
        if operation.measurement is None:
            raise ProviderUsageLedgerError("provider_usage.outcome_not_known")
        return Decimal(operation.measurement.total_cost_microusd) / _MICROUSD_PER_USD

    def _record_projection_failure(
        self,
        db: Session,
        *,
        operation_id: uuid.UUID,
        now: datetime,
        retryable: bool,
        reason_code: str,
    ) -> None:
        try:
            record = self._operation_for_id(db, operation_id, for_update=True)
            record.projection_status = (
                "retryable_failure" if retryable else "terminal_failure"
            )
            record.projection_attempts += 1
            record.projection_next_attempt_at = (
                now + _PROJECTION_RETRY_DELAY if retryable else None
            )
            record.projection_reason_code = reason_code
            record.updated_at = now
            db.commit()
        except Exception:
            self._rollback_safely(db)

    @staticmethod
    def _require_state_version(
        record: ProviderUsageOperationRecord,
        expected_state_version: int,
    ) -> None:
        if (
            isinstance(expected_state_version, bool)
            or not isinstance(expected_state_version, int)
            or record.state_version != expected_state_version
        ):
            raise ProviderUsageLedgerError("provider_usage.state_conflict")

    @staticmethod
    def _require_batch_limit(limit: int) -> None:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("provider usage batch limit must be between 1 and 1000")

    @staticmethod
    def _attach_optional_correlations(
        record: ProviderUsageOperationRecord,
        *,
        command: ProviderUsageIntentCommand,
    ) -> None:
        for field_name, proposed in (
            ("workflow_run_id", command.workflow_run_id),
            ("cost_optimizer_candidate_id", command.cost_optimizer_candidate_id),
        ):
            current = getattr(record, field_name)
            if proposed is None:
                continue
            if current is None:
                setattr(record, field_name, proposed)
                if (
                    record.state == ProviderUsageState.SUCCEEDED.value
                ):
                    if (
                        field_name == "workflow_run_id"
                        and record.projection_status == "projected"
                    ):
                        record.projection_status = "awaiting_workflow_run"
                    elif (
                        field_name == "cost_optimizer_candidate_id"
                        and record.projected_usage_log_id is not None
                        and record.projection_status
                        in {
                            "projected",
                            "awaiting_workflow_run",
                            "retryable_failure",
                        }
                    ):
                        record.projection_status = "pending"
                        record.projected_usage_revision = None
                    if record.projection_status in {
                        "pending",
                        "awaiting_workflow_run",
                    }:
                        record.projection_next_attempt_at = None
                        record.projection_reason_code = None
            elif current != proposed:
                raise ProviderUsageLedgerError("provider_usage.correlation_conflict")

    @staticmethod
    def _correction_matches(
        record: ProviderUsageCorrectionRecord,
        command: ProviderUsageCorrectionCommand,
    ) -> bool:
        measurement = command.measurement
        return (
            record.base_usage_revision == command.expected_usage_revision
            and record.prompt_tokens == measurement.prompt_tokens
            and record.completion_tokens == measurement.completion_tokens
            and record.total_cost_microusd == measurement.total_cost_microusd
            and record.latency_ms == measurement.latency_ms
            and record.source == command.source
            and record.reason_code == command.reason_code
        )

    @staticmethod
    def _require_admission_matches_snapshot(
        lease: Any,
        snapshot: ProviderUsageIntentSnapshot,
    ) -> None:
        capability = getattr(lease, "capability", None)
        credential = getattr(lease, "credential", None)
        model = getattr(lease, "model", None)
        provider = getattr(lease, "provider", None)
        principal = getattr(capability, "credential_principal", None)
        try:
            matches = (
                capability.id == snapshot.capability_id
                and capability.revision == snapshot.capability_revision
                and capability.binding == snapshot.binding
                and capability.policy_id == snapshot.policy_id
                and capability.policy_revision == snapshot.policy_revision
                and capability.provider_id == snapshot.provider_id
                and capability.model_id == snapshot.model_id
                and capability.credential_id == snapshot.credential_id
                and capability.permission_revision == snapshot.permission_revision
                and capability.relation_revision == snapshot.relation_revision
                and capability.egress_revision == snapshot.egress_revision
                and capability.pricing_revision == snapshot.pricing_revision
                and capability.input_token_cap == snapshot.input_token_cap
                and capability.output_token_cap == snapshot.output_token_cap
                and capability.cost_cap_microusd == snapshot.cost_cap_microusd
                and capability.expires_at == snapshot.expires_at
                and principal == snapshot.identities.credential_principal
                and uuid.UUID(str(credential.id)) == snapshot.credential_id
                and uuid.UUID(str(model.id)) == snapshot.model_id
                and uuid.UUID(str(provider.id)) == snapshot.provider_id
                and model.model_id_for_api_call == snapshot.model_api_id
                and Decimal(str(model.input_price_1k))
                == Decimal(snapshot.input_price_per_1k)
                and Decimal(str(model.output_price_1k))
                == Decimal(snapshot.output_price_per_1k)
            )
        except (AttributeError, TypeError, ValueError):
            matches = False
        if not matches:
            raise ProviderUsageLedgerError("provider_usage.capability_stale")

    @staticmethod
    def _require_live_capability_snapshot(
        db: Session,
        snapshot: ProviderUsageIntentSnapshot,
    ) -> None:
        record = (
            db.query(ProviderExecutionCapabilityRecord)
            .filter(
                ProviderExecutionCapabilityRecord.id == snapshot.capability_id,
                ProviderExecutionCapabilityRecord.organization_id
                == snapshot.binding.organization_id,
            )
            .populate_existing()
            .with_for_update()
            .one_or_none()
        )
        binding = snapshot.binding
        identities = snapshot.identities
        if record is None or (
            record.state != "active"
            or record.workflow_id != binding.workflow_id
            or record.deployment_id != binding.deployment_id
            or record.deployment_version != binding.deployment_version
            or record.node_id != binding.node_id
            or record.node_invocation_id != binding.node_invocation_id
            or record.execution_admission_id != binding.execution_admission_id
            or record.provider_attempt_id != binding.provider_attempt_id
            or record.purpose != binding.purpose.value
            or record.capability_revision != snapshot.capability_revision
            or record.policy_id != snapshot.policy_id
            or record.policy_revision != snapshot.policy_revision
            or record.provider_id != snapshot.provider_id
            or record.model_id != snapshot.model_id
            or record.credential_id != snapshot.credential_id
            or record.credential_principal_user_id
            != identities.credential_principal.reference_id
            or record.execution_subject_kind != identities.execution_subject.kind.value
            or record.execution_subject_id
            != identities.execution_subject.reference_id
            or record.billing_principal_kind != identities.billing_principal.kind.value
            or record.billing_principal_id != identities.billing_principal.reference_id
            or record.audit_actor_kind != identities.audit_actor.kind.value
            or record.audit_actor_id != identities.audit_actor.reference_id
            or record.permission_revision != snapshot.permission_revision
            or record.relation_revision != snapshot.relation_revision
            or record.egress_revision != snapshot.egress_revision
            or record.pricing_revision != snapshot.pricing_revision
            or record.input_token_cap != snapshot.input_token_cap
            or record.output_token_cap != snapshot.output_token_cap
            or record.cost_cap_microusd != snapshot.cost_cap_microusd
            or record.expires_at != snapshot.expires_at
        ):
            raise ProviderUsageLedgerError("provider_usage.capability_stale")

    @staticmethod
    def _operation_for_key(
        db: Session,
        key: ProviderUsageOperationKey,
        *,
        for_update: bool,
    ) -> ProviderUsageOperationRecord | None:
        query = db.query(ProviderUsageOperationRecord).filter(
            ProviderUsageOperationRecord.organization_id == key.organization_id,
            ProviderUsageOperationRecord.provider_attempt_id
            == key.provider_attempt_id,
            ProviderUsageOperationRecord.purpose == key.purpose.value,
        )
        if for_update:
            query = query.populate_existing().with_for_update()
        return query.one_or_none()

    @staticmethod
    def _operation_for_id(
        db: Session,
        operation_id: uuid.UUID,
        *,
        organization_id: uuid.UUID | None = None,
        for_update: bool,
    ) -> ProviderUsageOperationRecord:
        query = db.query(ProviderUsageOperationRecord).filter(
            ProviderUsageOperationRecord.id == operation_id
        )
        if organization_id is not None:
            query = query.filter(
                ProviderUsageOperationRecord.organization_id == organization_id
            )
        if for_update:
            query = query.populate_existing().with_for_update()
        record = query.one_or_none()
        if record is None:
            ProviderUsageLedgerService._rollback_safely(db)
            raise ProviderUsageLedgerError("provider_usage.not_found")
        return record

    @staticmethod
    def _database_clock_now(db: Session) -> datetime:
        value = db.execute(select(func.clock_timestamp())).scalar_one()
        if not isinstance(value, datetime):
            raise ProviderUsageLedgerError("provider_usage.database_clock_unavailable")
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _commit_or_raise(db: Session, code: str) -> None:
        try:
            db.commit()
        except SQLAlchemyError as exc:
            ProviderUsageLedgerService._rollback_safely(db)
            raise ProviderUsageLedgerError(code) from exc

    @staticmethod
    def _rollback_safely(db: Session) -> None:
        try:
            db.rollback()
        except SQLAlchemyError:
            pass


__all__ = [
    "ProviderUsageCorrectionCommand",
    "ProviderUsageIntentCommand",
    "ProviderUsageLedgerService",
    "ProviderUsageProjectionBatchResult",
]
