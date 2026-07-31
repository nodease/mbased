from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from apps.shared.db.models.cost_optimizer import CostOptimizerCandidate
from apps.shared.db.models.llm import LLMCredential, LLMModel, LLMUsageLog
from apps.shared.db.models.provider_usage import ProviderUsageOperationRecord
from apps.shared.db.models.user import User
from apps.shared.db.models.workflow import Workflow
from apps.shared.db.models.workflow_run import WorkflowRun
from apps.shared.domain.provider_execution_capability import (
    CapabilityPurpose,
    ProviderExecutionBinding,
    RuntimeIdentityContext,
    RuntimePrincipal,
)
from apps.shared.domain.provider_usage_ledger import (
    ProviderUsageIntentSnapshot,
    ProviderUsageLedgerError,
    ProviderUsageMeasurement,
    ProviderUsageOperation,
    ProviderUsageState,
)
from apps.shared.services.provider_usage_ledger import (
    ProviderUsageLedgerService,
)
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

NOW = datetime(2026, 7, 22, tzinfo=timezone.utc)


def _snapshot() -> ProviderUsageIntentSnapshot:
    organization_id = uuid.uuid4()
    subject_id = uuid.uuid4()
    return ProviderUsageIntentSnapshot(
        binding=ProviderExecutionBinding(
            organization_id=organization_id,
            workflow_id=uuid.uuid4(),
            deployment_id=uuid.uuid4(),
            deployment_version=3,
            node_id="llm-node",
            node_invocation_id=uuid.uuid4(),
            execution_admission_id=uuid.uuid4(),
            provider_attempt_id=uuid.uuid4(),
            purpose=CapabilityPurpose.MAIN_GENERATION,
            container_path=(("loop", "loop-a"),),
        ),
        capability_id=uuid.uuid4(),
        capability_revision=2,
        policy_id=uuid.uuid4(),
        policy_revision=5,
        provider_id=uuid.uuid4(),
        model_id=uuid.uuid4(),
        model_api_id="gpt-safe",
        credential_id=uuid.uuid4(),
        identities=RuntimeIdentityContext(
            execution_subject=RuntimePrincipal.user(subject_id),
            credential_principal=RuntimePrincipal.user(uuid.uuid4()),
            billing_principal=RuntimePrincipal.organization(organization_id),
            audit_actor=RuntimePrincipal.user(subject_id),
        ),
        permission_revision="a" * 64,
        relation_revision="b" * 64,
        egress_revision="c" * 64,
        pricing_revision="d" * 64,
        input_price_per_1k="0.100000",
        output_price_per_1k="0.200000",
        input_token_cap=1_000,
        output_token_cap=100,
        cost_cap_microusd=50_000,
        admitted_input_tokens=300,
        admitted_output_tokens=50,
        expires_at=NOW + timedelta(minutes=5),
    )


def _success() -> ProviderUsageOperation:
    return (
        ProviderUsageOperation.intent(
            operation_id=uuid.uuid4(),
            snapshot=_snapshot(),
            now=NOW,
        )
        .mark_provider_started(now=NOW + timedelta(seconds=1))
        .record_success(
            measurement=ProviderUsageMeasurement(
                prompt_tokens=10,
                completion_tokens=5,
                total_cost_microusd=2_000,
                latency_ms=42,
            ),
            now=NOW + timedelta(seconds=2),
        )
    )


def test_record_round_trip_preserves_the_exact_admitted_snapshot() -> None:
    service = ProviderUsageLedgerService()
    operation = ProviderUsageOperation.intent(
        operation_id=uuid.uuid4(),
        snapshot=_snapshot(),
        now=NOW,
    )
    workflow_run_id = uuid.uuid4()

    record = service._record_from_operation(  # noqa: SLF001
        operation,
        workflow_run_id=workflow_run_id,
        cost_optimizer_candidate_id=None,
    )
    restored = service._domain_operation(record)  # noqa: SLF001

    assert restored == operation
    assert record.workflow_run_id == workflow_run_id
    assert record.container_path == [{"kind": "loop", "node_id": "loop-a"}]
    assert record.credential_principal_id == (
        operation.snapshot.identities.credential_principal.reference_id
    )
    assert Decimal(record.input_price_per_1k) == Decimal("0.100000000")


@pytest.mark.parametrize(
    ("execution_subject", "audit_actor"),
    [
        (RuntimePrincipal.anonymous_public(), RuntimePrincipal.public_actor()),
        (RuntimePrincipal.system_actor(), RuntimePrincipal.system_actor()),
    ],
)
def test_non_user_execution_never_falls_back_to_the_credential_principal(
    execution_subject,
    audit_actor,
) -> None:
    service = ProviderUsageLedgerService()
    snapshot = _snapshot()
    snapshot = replace(
        snapshot,
        identities=RuntimeIdentityContext(
            execution_subject=execution_subject,
            credential_principal=snapshot.identities.credential_principal,
            billing_principal=snapshot.identities.billing_principal,
            audit_actor=audit_actor,
        ),
    )
    operation = ProviderUsageOperation.intent(
        operation_id=uuid.uuid4(),
        snapshot=snapshot,
        now=NOW,
    )

    record = service._record_from_operation(  # noqa: SLF001
        operation,
        workflow_run_id=None,
        cost_optimizer_candidate_id=None,
    )
    restored = service._domain_operation(record)  # noqa: SLF001

    assert restored.snapshot.identities.execution_subject == execution_subject
    assert restored.snapshot.identities.audit_actor == audit_actor
    assert restored.snapshot.identities.credential_principal.reference_id is not None
    assert restored.snapshot.identities.execution_subject.reference_id is None


def test_final_admission_uses_the_exact_previously_admitted_bounds() -> None:
    service = ProviderUsageLedgerService()
    operation = ProviderUsageOperation.intent(
        operation_id=uuid.uuid4(),
        snapshot=_snapshot(),
        now=NOW,
    )

    command = service._admission_command(operation)  # noqa: SLF001

    assert command.capability_id == operation.snapshot.capability_id
    assert command.capability_revision == operation.snapshot.capability_revision
    assert command.binding == operation.snapshot.binding
    assert command.requested_input_tokens == 300
    assert command.requested_output_tokens == 50


def test_reconciliation_lookup_is_scoped_to_the_explicit_organization() -> None:
    service = ProviderUsageLedgerService()
    operation_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    db = _OperationLookupDb()

    with pytest.raises(ProviderUsageLedgerError) as exc_info:
        service._operation_for_id(  # noqa: SLF001
            db,
            operation_id,
            organization_id=organization_id,
            for_update=True,
        )

    assert exc_info.value.code == "provider_usage.not_found"
    statement = select(ProviderUsageOperationRecord.id).where(
        *db.query_obj.filter_conditions
    )
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert f"provider_usage_operations.id = '{operation_id}'" in sql
    assert (
        f"provider_usage_operations.organization_id = '{organization_id}'" in sql
    )
    assert db.query_obj.lock_calls == ["populate_existing", "with_for_update"]
    assert db.rollbacks == 1


def test_reconciliation_requires_the_exact_locked_state_version() -> None:
    service = ProviderUsageLedgerService()
    unknown = (
        ProviderUsageOperation.intent(
            operation_id=uuid.uuid4(),
            snapshot=_snapshot(),
            now=NOW,
        )
        .mark_provider_started(now=NOW)
        .mark_outcome_unknown(reason_code="provider_timeout", now=NOW)
    )
    record = service._record_from_operation(  # noqa: SLF001
        unknown,
        workflow_run_id=None,
        cost_optimizer_candidate_id=None,
    )
    db = _OperationLookupDb(record)

    with pytest.raises(ProviderUsageLedgerError) as exc_info:
        service.reconcile_success(
            db,
            organization_id=unknown.key.organization_id,
            operation_id=unknown.id,
            expected_state_version=unknown.state_version - 1,
            measurement=ProviderUsageMeasurement(
                prompt_tokens=10,
                completion_tokens=5,
                total_cost_microusd=2_000,
                latency_ms=42,
            ),
            now=NOW + timedelta(minutes=1),
        )

    assert exc_info.value.code == "provider_usage.state_conflict"
    assert record.state == ProviderUsageState.OUTCOME_UNKNOWN.value
    assert db.commits == 0


def test_terminal_audit_is_deterministic_and_contains_only_safe_metadata() -> None:
    service = ProviderUsageLedgerService()
    operation = _success()

    event_id, outbox = service._new_audit_outbox(operation)  # noqa: SLF001
    repeated_id, repeated = service._new_audit_outbox(operation)  # noqa: SLF001

    assert event_id == repeated_id
    assert outbox.idempotency_key == repeated.idempotency_key
    assert outbox.payload["action"] == "llm.call"
    assert outbox.payload["actor_type"] == "user"
    assert outbox.payload["actor_id"] == str(
        operation.snapshot.identities.audit_actor.reference_id
    )
    assert outbox.payload["target_id"] == str(operation.id)
    assert outbox.payload["audit_metadata"] == {
        "organization_id": str(operation.key.organization_id),
        "workflow_id": str(operation.snapshot.binding.workflow_id),
        "provider_usage_operation_id": str(operation.id),
        "provider_execution_capability_id": str(operation.snapshot.capability_id),
        "provider_execution_capability_revision": 2,
        "purpose": "main_generation",
        "provider_id": str(operation.snapshot.provider_id),
        "model_id": str(operation.snapshot.model_id),
        "provider_usage_status": "succeeded",
    }
    serialized = json.dumps(outbox.payload).lower()
    for forbidden in (
        "prompt",
        "completion",
        "api_key",
        "credential_id",
        "encrypted_config",
        "raw_request",
        "raw_response",
    ):
        assert forbidden not in serialized


def test_terminal_audit_preserves_the_ledger_workflow_run_correlation() -> None:
    service = ProviderUsageLedgerService()
    operation = _success()
    workflow_run_id = uuid.uuid4()
    record = service._record_from_operation(  # noqa: SLF001
        operation,
        workflow_run_id=workflow_run_id,
        cost_optimizer_candidate_id=None,
    )
    added: list[object] = []
    db = SimpleNamespace(add=added.append)

    service._enqueue_terminal_audit(  # noqa: SLF001
        db,
        record=record,
        operation=operation,
    )

    assert len(added) == 1
    assert added[0].payload["workflow_run_id"] == str(workflow_run_id)


def test_projection_values_use_canonical_cost_and_provider_started_time() -> None:
    service = ProviderUsageLedgerService()
    operation = _success()
    candidate_id = uuid.uuid4()

    values = service._usage_projection_values(  # noqa: SLF001
        operation,
        cost_optimizer_candidate_id=candidate_id,
    )

    assert values["provider_usage_operation_id"] == operation.id
    assert values["provider_usage_revision"] == 1
    assert values["total_cost"] == Decimal("0.002")
    assert values["created_at"] == operation.provider_started_at
    assert values["user_id"] == (
        operation.snapshot.identities.credential_principal.reference_id
    )
    assert values["cost_optimizer_candidate_id"] == candidate_id


def test_audit_failure_status_never_serializes_the_safe_reason_as_raw_error() -> None:
    service = ProviderUsageLedgerService()
    started = ProviderUsageOperation.intent(
        operation_id=uuid.uuid4(), snapshot=_snapshot(), now=NOW
    ).mark_provider_started(now=NOW)
    unknown = started.mark_outcome_unknown(
        reason_code="provider_timeout",
        now=NOW + timedelta(seconds=1),
    )

    _, outbox = service._new_audit_outbox(unknown)  # noqa: SLF001

    assert outbox.payload["status"] == "failure"
    assert outbox.payload["audit_metadata"]["safe_reason_code"] == "provider_timeout"
    assert "error_message" not in outbox.payload


def test_projection_claim_is_bounded_skip_locked_and_leased() -> None:
    service = ProviderUsageLedgerService()
    operation = _success()
    record = service._record_from_operation(  # noqa: SLF001
        operation,
        workflow_run_id=None,
        cost_optimizer_candidate_id=None,
    )
    db = _BatchDb([record])

    operation_ids = service.claim_pending_projection_ids(
        db,
        limit=25,
        now=NOW + timedelta(minutes=5),
    )

    assert operation_ids == (operation.id,)
    assert db.query_obj.skip_locked is True
    assert db.query_obj.limit_value == 25
    assert record.projection_next_attempt_at == NOW + timedelta(minutes=7)
    assert db.commits == 1


def test_projection_claim_revisits_a_nullable_projection_after_run_creation() -> None:
    service = ProviderUsageLedgerService()
    db = _BatchDb([])

    service.claim_pending_projection_ids(
        db,
        limit=25,
        now=NOW + timedelta(minutes=5),
    )

    statement = select(ProviderUsageOperationRecord.id).where(
        *db.query_obj.filter_conditions
    )
    sql = " ".join(
        str(
            statement.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        ).split()
    )
    assert (
        "provider_usage_operations.projection_status IN "
        "('awaiting_workflow_run', 'retryable_failure')" in sql
    )
    assert "workflow_runs.id = provider_usage_operations.workflow_run_id" in sql
    assert (
        "llm_usage_logs.provider_usage_operation_id = "
        "provider_usage_operations.id" in sql
    )
    assert "llm_usage_logs.workflow_run_id IS NULL" in sql


def test_projection_waits_only_when_the_snapshotted_workflow_run_is_missing() -> None:
    service = ProviderUsageLedgerService()
    operation = _success()
    record = service._record_from_operation(  # noqa: SLF001
        operation,
        workflow_run_id=uuid.uuid4(),
        cost_optimizer_candidate_id=None,
    )
    db = _ProjectionDb(record=record, usage=None, workflow_run=None)

    usage = service.project_compatibility_usage(
        db,
        operation_id=operation.id,
        now=NOW + timedelta(minutes=1),
    )

    assert usage.workflow_run_id is None
    assert record.projection_status == "awaiting_workflow_run"


def test_late_intent_replay_reopens_only_the_workflow_run_projection() -> None:
    service = ProviderUsageLedgerService()
    operation = _success()
    record = service._record_from_operation(  # noqa: SLF001
        operation,
        workflow_run_id=None,
        cost_optimizer_candidate_id=None,
    )
    record.projection_status = "projected"
    record.projected_usage_log_id = uuid.uuid4()
    record.projected_usage_revision = operation.usage_revision
    record.projected_at = NOW

    service._attach_optional_correlations(  # noqa: SLF001
        record,
        command=SimpleNamespace(
            workflow_run_id=uuid.uuid4(),
            cost_optimizer_candidate_id=None,
        ),
    )

    assert record.projection_status == "awaiting_workflow_run"


def test_late_candidate_correlation_reopens_the_compatibility_projection() -> None:
    service = ProviderUsageLedgerService()
    operation = _success()
    record = service._record_from_operation(  # noqa: SLF001
        operation,
        workflow_run_id=None,
        cost_optimizer_candidate_id=None,
    )
    record.projection_status = "projected"
    record.projected_usage_log_id = uuid.uuid4()
    record.projected_usage_revision = operation.usage_revision
    record.projected_at = NOW

    service._attach_optional_correlations(  # noqa: SLF001
        record,
        command=SimpleNamespace(
            workflow_run_id=None,
            cost_optimizer_candidate_id=uuid.uuid4(),
        ),
    )

    assert record.projection_status == "pending"
    assert record.projected_usage_revision is None


def test_late_candidate_does_not_wait_for_an_unavailable_workflow_run() -> None:
    service = ProviderUsageLedgerService()
    operation = _success()
    record = service._record_from_operation(  # noqa: SLF001
        operation,
        workflow_run_id=uuid.uuid4(),
        cost_optimizer_candidate_id=None,
    )
    record.projection_status = "awaiting_workflow_run"
    record.projected_usage_log_id = uuid.uuid4()
    record.projected_usage_revision = operation.usage_revision
    record.projected_at = NOW

    service._attach_optional_correlations(  # noqa: SLF001
        record,
        command=SimpleNamespace(
            workflow_run_id=record.workflow_run_id,
            cost_optimizer_candidate_id=uuid.uuid4(),
        ),
    )

    assert record.projection_status == "pending"
    assert record.projected_usage_revision is None


def test_stale_provider_started_reconciles_to_unknown_without_provider_io() -> None:
    service = ProviderUsageLedgerService()
    started = ProviderUsageOperation.intent(
        operation_id=uuid.uuid4(),
        snapshot=_snapshot(),
        now=NOW,
    ).mark_provider_started(now=NOW + timedelta(seconds=1))
    record = service._record_from_operation(  # noqa: SLF001
        started,
        workflow_run_id=None,
        cost_optimizer_candidate_id=None,
    )
    db = _BatchDb([record])

    reconciled_ids = service.reconcile_stale_provider_started(
        db,
        limit=10,
        stale_after=timedelta(minutes=15),
        now=NOW + timedelta(minutes=30),
    )

    assert reconciled_ids == (started.id,)
    assert record.state == ProviderUsageState.OUTCOME_UNKNOWN.value
    assert record.safe_reason_code == "stale_provider_started"
    assert record.audit_event_id is not None
    assert len(db.added) == 1
    assert db.query_obj.skip_locked is True
    assert db.commits == 1


def test_projection_reconciliation_continues_after_one_safe_failure() -> None:
    service = ProviderUsageLedgerService()
    operations = [_success(), _success()]
    records = [
        service._record_from_operation(  # noqa: SLF001
            operation,
            workflow_run_id=None,
            cost_optimizer_candidate_id=None,
        )
        for operation in operations
    ]
    db = _BatchDb(records)
    projected: list[uuid.UUID] = []

    def project(_db, *, operation_id, now=None):
        del now
        projected.append(operation_id)
        if operation_id == operations[0].id:
            raise ProviderUsageLedgerError("provider_usage.projection_commit_failed")

    service.project_compatibility_usage = project  # type: ignore[method-assign]

    result = service.reconcile_pending_projections(
        db,
        limit=10,
        now=NOW + timedelta(minutes=5),
    )

    assert projected == [operation.id for operation in operations]
    assert result.claimed_count == 2
    assert result.projected_count == 1
    assert result.failed_count == 1


def test_projection_identity_conflict_is_recorded_as_terminal_failure() -> None:
    service = ProviderUsageLedgerService()
    operation = _success()
    record = service._record_from_operation(  # noqa: SLF001
        operation,
        workflow_run_id=None,
        cost_optimizer_candidate_id=None,
    )
    usage = LLMUsageLog(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        organization_id=operation.key.organization_id,
        workflow_id=operation.snapshot.binding.workflow_id,
        node_id=operation.snapshot.binding.node_id,
        provider_usage_operation_id=operation.id,
        provider_usage_revision=1,
        prompt_tokens=10,
        completion_tokens=5,
        total_cost=Decimal("0.002"),
        latency_ms=42,
        status="success",
        created_at=operation.provider_started_at,
    )
    db = _ProjectionDb(record=record, usage=usage)

    with pytest.raises(ProviderUsageLedgerError) as exc_info:
        service.project_compatibility_usage(
            db,
            operation_id=operation.id,
            now=NOW + timedelta(minutes=1),
        )

    assert exc_info.value.code == "provider_usage.projection_conflict"
    assert record.projection_status == "terminal_failure"
    assert record.projection_reason_code == "projection_conflict"
    assert record.projection_next_attempt_at is None
    assert record.projection_attempts == 1


def test_projection_drops_deleted_optional_control_references() -> None:
    service = ProviderUsageLedgerService()
    operation = _success()
    candidate_id = uuid.uuid4()
    record = service._record_from_operation(  # noqa: SLF001
        operation,
        workflow_run_id=None,
        cost_optimizer_candidate_id=candidate_id,
    )
    db = _ProjectionDb(
        record=record,
        usage=None,
        missing_references={
            (LLMCredential, operation.snapshot.credential_id),
            (LLMModel, operation.snapshot.model_id),
            (Workflow, operation.snapshot.binding.workflow_id),
            (CostOptimizerCandidate, candidate_id),
        },
    )

    usage = service.project_compatibility_usage(
        db,
        operation_id=operation.id,
        now=NOW + timedelta(minutes=1),
    )

    assert usage.user_id == (
        operation.snapshot.identities.credential_principal.reference_id
    )
    assert usage.credential_id is None
    assert usage.model_id is None
    assert usage.workflow_id is None
    assert usage.cost_optimizer_candidate_id is None
    assert record.projection_status == "projected"


def test_projection_attaches_a_late_exact_workflow_and_run() -> None:
    service = ProviderUsageLedgerService()
    operation = _success()
    run_id = uuid.uuid4()
    record = service._record_from_operation(  # noqa: SLF001
        operation,
        workflow_run_id=run_id,
        cost_optimizer_candidate_id=None,
    )
    usage = LLMUsageLog(
        id=uuid.uuid4(),
        user_id=operation.snapshot.identities.credential_principal.reference_id,
        organization_id=operation.key.organization_id,
        workflow_id=None,
        workflow_run_id=None,
        node_id=operation.snapshot.binding.node_id,
        provider_usage_operation_id=operation.id,
        provider_usage_revision=1,
        prompt_tokens=operation.measurement.prompt_tokens,
        completion_tokens=operation.measurement.completion_tokens,
        total_cost=Decimal("0.002"),
        latency_ms=operation.measurement.latency_ms,
        status="success",
        created_at=operation.provider_started_at,
    )
    run = SimpleNamespace(
        id=run_id,
        workflow_id=operation.snapshot.binding.workflow_id,
        total_tokens=0,
        total_cost=Decimal("0"),
    )
    db = _ProjectionDb(record=record, usage=usage, workflow_run=run)

    projected = service.project_compatibility_usage(
        db,
        operation_id=operation.id,
        now=NOW + timedelta(minutes=1),
    )

    assert projected.workflow_id == operation.snapshot.binding.workflow_id
    assert projected.workflow_run_id == run_id
    assert run.total_tokens == 15
    assert run.total_cost == Decimal("0.002")
    assert db.workflow_run_query is not None
    assert db.workflow_run_query.lock_calls == ["populate_existing", "with_for_update"]


def test_projection_rejects_a_run_from_a_different_workflow_organization() -> None:
    service = ProviderUsageLedgerService()
    operation = _success()
    run_id = uuid.uuid4()
    record = service._record_from_operation(  # noqa: SLF001
        operation,
        workflow_run_id=run_id,
        cost_optimizer_candidate_id=None,
    )
    usage = LLMUsageLog(
        id=uuid.uuid4(),
        user_id=operation.snapshot.identities.credential_principal.reference_id,
        organization_id=operation.key.organization_id,
        workflow_id=operation.snapshot.binding.workflow_id,
        workflow_run_id=None,
        node_id=operation.snapshot.binding.node_id,
        provider_usage_operation_id=operation.id,
        provider_usage_revision=1,
        prompt_tokens=operation.measurement.prompt_tokens,
        completion_tokens=operation.measurement.completion_tokens,
        total_cost=Decimal("0.002"),
        latency_ms=operation.measurement.latency_ms,
        status="success",
        created_at=operation.provider_started_at,
    )
    run = SimpleNamespace(
        id=run_id,
        workflow_id=operation.snapshot.binding.workflow_id,
        total_tokens=0,
        total_cost=Decimal("0"),
    )
    db = _ProjectionDb(
        record=record,
        usage=usage,
        workflow_run=run,
        workflow_organization_id=uuid.uuid4(),
    )

    with pytest.raises(ProviderUsageLedgerError) as exc_info:
        service.project_compatibility_usage(
            db,
            operation_id=operation.id,
            now=NOW + timedelta(minutes=1),
        )

    assert exc_info.value.code == "provider_usage.workflow_run_conflict"
    assert usage.workflow_run_id is None
    assert run.total_tokens == 0
    assert run.total_cost == Decimal("0")
    assert record.projection_status == "terminal_failure"


def test_projection_missing_legacy_principal_is_terminal_not_retryable() -> None:
    service = ProviderUsageLedgerService()
    operation = _success()
    principal_id = (
        operation.snapshot.identities.credential_principal.reference_id
    )
    assert principal_id is not None
    record = service._record_from_operation(  # noqa: SLF001
        operation,
        workflow_run_id=None,
        cost_optimizer_candidate_id=None,
    )
    db = _ProjectionDb(
        record=record,
        usage=None,
        missing_references={(User, principal_id)},
    )

    with pytest.raises(ProviderUsageLedgerError) as exc_info:
        service.project_compatibility_usage(
            db,
            operation_id=operation.id,
            now=NOW + timedelta(minutes=1),
        )

    assert exc_info.value.code == "provider_usage.projection_principal_unavailable"
    assert record.projection_status == "terminal_failure"
    assert record.projection_reason_code == "projection_principal_unavailable"
    assert record.projection_next_attempt_at is None
    assert record.projection_attempts == 1


class _BatchQuery:
    def __init__(self, records):
        self.records = records
        self.skip_locked = None
        self.limit_value = None
        self.filter_conditions = []

    def filter(self, *args):
        self.filter_conditions.extend(args)
        return self

    def order_by(self, *_args):
        return self

    def with_for_update(self, **kwargs):
        self.skip_locked = kwargs.get("skip_locked")
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def all(self):
        return self.records[: self.limit_value]


class _BatchDb:
    def __init__(self, records):
        self.query_obj = _BatchQuery(records)
        self.added = []
        self.commits = 0
        self.rollbacks = 0

    def query(self, *_models):
        return self.query_obj

    def add(self, value):
        self.added.append(value)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class _OneQuery:
    def __init__(self, value):
        self.value = value
        self.lock_calls = []

    def filter(self, *_args):
        return self

    def populate_existing(self):
        self.lock_calls.append("populate_existing")
        return self

    def with_for_update(self, **_kwargs):
        self.lock_calls.append("with_for_update")
        return self

    def one_or_none(self):
        return self.value


class _OperationLookupQuery(_OneQuery):
    def __init__(self, value=None):
        super().__init__(value)
        self.filter_conditions = []

    def filter(self, *conditions):
        self.filter_conditions.extend(conditions)
        return self


class _OperationLookupDb:
    def __init__(self, value=None):
        self.query_obj = _OperationLookupQuery(value)
        self.rollbacks = 0
        self.commits = 0

    def query(self, *_models):
        return self.query_obj

    def rollback(self):
        self.rollbacks += 1

    def commit(self):
        self.commits += 1


class _ProjectionDb:
    def __init__(
        self,
        *,
        record,
        usage,
        workflow_run=None,
        workflow_organization_id=None,
        missing_references=None,
    ):
        self.record = record
        self.usage = usage
        self.workflow_run = workflow_run
        self.workflow_organization_id = (
            record.organization_id
            if workflow_organization_id is None
            else workflow_organization_id
        )
        self.missing_references = missing_references or set()
        self.added = []
        self.commits = 0
        self.rollbacks = 0
        self.workflow_run_query = None

    def query(self, model):
        if model is ProviderUsageOperationRecord:
            return _OneQuery(self.record)
        if model is LLMUsageLog:
            return _OneQuery(self.usage)
        if model is WorkflowRun:
            self.workflow_run_query = _OneQuery(self.workflow_run)
            return self.workflow_run_query
        raise AssertionError(f"unexpected query model: {model}")

    def get(self, model, reference_id):
        if (model, reference_id) in self.missing_references:
            return None
        if model is Workflow:
            return SimpleNamespace(
                id=reference_id,
                organization_id=self.workflow_organization_id,
            )
        return object()

    def add(self, value):
        self.added.append(value)

    def flush(self):
        for value in self.added:
            if isinstance(value, LLMUsageLog) and value.id is None:
                value.id = uuid.uuid4()

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1
