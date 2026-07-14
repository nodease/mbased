from __future__ import annotations

import copy
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterator

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from apps.shared.db.models.workflow_node_effect_attempt import (
    WorkflowNodeEffectAttempt,
)
from apps.shared.services.app_lifecycle_admission import (
    lock_app_workflow_for_admission,
)
from apps.workflow_engine.application.external_effect import (
    AcquireKind,
    AcquireResult,
    EffectAttemptRecord,
    EffectAttemptSpec,
)
from apps.workflow_engine.domain.external_effect import (
    EffectAttemptStatus,
    EffectOutcome,
    ExternalEffectContext,
    ExternalEffectError,
    ProviderContractRegistry,
    ProviderReplayCapability,
    ReplayDecision,
    ResultReuseCapability,
    decide_replay,
)


class SQLAlchemyEffectAttemptRepository:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        contracts: ProviderContractRegistry,
    ) -> None:
        self.session_factory = session_factory
        self.contracts = contracts

    @contextmanager
    def _session(self) -> Iterator[Session]:
        session = self.session_factory()
        try:
            yield session
        finally:
            session.close()

    @staticmethod
    def _database_now(session: Session) -> datetime:
        value = session.execute(select(text("clock_timestamp()"))).scalar_one()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value

    @staticmethod
    def _slot_filter(spec: EffectAttemptSpec):
        context = spec.context
        return (
            WorkflowNodeEffectAttempt.organization_id == context.organization_id,
            WorkflowNodeEffectAttempt.execution_id == context.execution_id,
            WorkflowNodeEffectAttempt.node_invocation_id == context.node_invocation_id,
            WorkflowNodeEffectAttempt.effect_sequence == spec.effect_sequence,
        )

    def _locked_by_slot(
        self,
        session: Session,
        spec: EffectAttemptSpec,
    ) -> WorkflowNodeEffectAttempt | None:
        return session.execute(
            select(WorkflowNodeEffectAttempt)
            .where(*self._slot_filter(spec))
            .with_for_update()
        ).scalar_one_or_none()

    @staticmethod
    def _same_semantics(row: WorkflowNodeEffectAttempt, spec: EffectAttemptSpec) -> bool:
        return (
            row.app_id == spec.context.app_id
            and row.workflow_id == spec.context.workflow_id
            and row.node_id == spec.context.node_id
            and row.provider == spec.profile.provider
            and row.operation == spec.profile.operation
            and row.provider_contract_version == spec.profile.contract_version
            and row.provider_replay_capability == spec.profile.provider_replay.value
            and row.result_reuse_capability == spec.profile.result_reuse.value
            and row.effect_input_digest == spec.effect_input_digest
        )

    def _record(self, row: WorkflowNodeEffectAttempt) -> EffectAttemptRecord:
        profile = self.contracts.get(
            row.provider,
            row.operation,
            row.provider_contract_version,
        )
        context = ExternalEffectContext(
            organization_id=row.organization_id,
            app_id=row.app_id,
            workflow_id=row.workflow_id,
            execution_id=row.execution_id,
            node_invocation_id=row.node_invocation_id,
            workflow_run_id=row.workflow_run_id,
            node_run_id=row.node_run_id,
            node_id=row.node_id,
        )
        spec = EffectAttemptSpec(
            context=context,
            profile=profile,
            effect_sequence=row.effect_sequence,
            effect_input_digest=row.effect_input_digest,
            replay_deadline_at=row.replay_deadline_at,
            key_version=row.key_version,
            key_format_version=row.key_format_version,
            idempotency_key_fingerprint=row.idempotency_key_fingerprint,
        )
        return EffectAttemptRecord(
            id=row.id,
            spec=spec,
            status=row.status,
            claim_owner=row.claim_owner,
            claim_generation=row.claim_generation,
            claim_expires_at=row.claim_expires_at,
            outcome=EffectOutcome(row.outcome) if row.outcome else None,
            replay_decision=(
                ReplayDecision(row.replay_decision) if row.replay_decision else None
            ),
            replay_result=copy.deepcopy(row.replay_result),
            provider_started_at=row.provider_started_at,
            provider_status_code=row.provider_status_code,
            error_code=row.error_code,
            terminal_at=row.terminal_at,
        )

    def find_by_slot(
        self,
        *,
        context: ExternalEffectContext,
        effect_sequence: int,
    ) -> EffectAttemptRecord | None:
        with self._session() as session:
            row = session.execute(
                select(WorkflowNodeEffectAttempt).where(
                    WorkflowNodeEffectAttempt.organization_id
                    == context.organization_id,
                    WorkflowNodeEffectAttempt.execution_id == context.execution_id,
                    WorkflowNodeEffectAttempt.node_invocation_id
                    == context.node_invocation_id,
                    WorkflowNodeEffectAttempt.effect_sequence == effect_sequence,
                )
            ).scalar_one_or_none()
            return self._record(row) if row is not None else None

    @staticmethod
    def _new_row(
        spec: EffectAttemptSpec,
        *,
        claim_owner: str,
        claim_expires_at: datetime,
        database_now: datetime,
    ) -> WorkflowNodeEffectAttempt:
        context = spec.context
        return WorkflowNodeEffectAttempt(
            organization_id=context.organization_id,
            app_id=context.app_id,
            workflow_id=context.workflow_id,
            execution_id=context.execution_id,
            node_invocation_id=context.node_invocation_id,
            workflow_run_id=context.workflow_run_id,
            node_run_id=context.node_run_id,
            node_id=context.node_id,
            operation=spec.profile.operation,
            effect_sequence=spec.effect_sequence,
            provider=spec.profile.provider,
            provider_contract_version=spec.profile.contract_version,
            provider_replay_capability=spec.profile.provider_replay.value,
            result_reuse_capability=spec.profile.result_reuse.value,
            effect_input_digest=spec.effect_input_digest,
            replay_deadline_at=(
                database_now + spec.profile.retention
                if spec.profile.provider_replay
                is ProviderReplayCapability.SUPPORTED
                else None
            ),
            status=EffectAttemptStatus.PREPARED.value,
            claim_owner=claim_owner,
            claim_expires_at=claim_expires_at,
            claim_generation=1,
            key_version=spec.key_version,
            key_format_version=spec.key_format_version,
            idempotency_key_fingerprint=spec.idempotency_key_fingerprint,
        )

    def acquire(
        self,
        spec: EffectAttemptSpec,
        *,
        claim_owner: str,
        claim_ttl: timedelta,
        allow_retry: bool,
        now: datetime,
    ) -> AcquireResult:
        del now
        try:
            with self._session() as session:
                context = spec.context
                if lock_app_workflow_for_admission(
                    session,
                    app_id=context.app_id,
                    workflow_id=context.workflow_id,
                    organization_id=context.organization_id,
                ) is None:
                    raise ExternalEffectError(
                        "external_effect.stopped",
                        retryable=False,
                        node_id=context.node_id,
                    )
                row = self._locked_by_slot(session, spec)
                db_now = self._database_now(session)
                if row is None:
                    row = self._new_row(
                        spec,
                        claim_owner=claim_owner,
                        claim_expires_at=db_now + claim_ttl,
                        database_now=db_now,
                    )
                    session.add(row)
                    session.commit()
                    session.refresh(row)
                    return AcquireResult(AcquireKind.CLAIMED, self._record(row))
                result = self._acquire_existing(
                    session,
                    row,
                    spec,
                    claim_owner=claim_owner,
                    claim_ttl=claim_ttl,
                    allow_retry=allow_retry,
                    now=db_now,
                )
                session.commit()
                return result
        except IntegrityError:
            with self._session() as session:
                session.rollback()
                row = self._locked_by_slot(session, spec)
                if row is None:
                    raise RuntimeError("effect attempt winner is unavailable") from None
                db_now = self._database_now(session)
                result = self._acquire_existing(
                    session,
                    row,
                    spec,
                    claim_owner=claim_owner,
                    claim_ttl=claim_ttl,
                    allow_retry=allow_retry,
                    now=db_now,
                )
                session.commit()
                return result

    def _acquire_existing(
        self,
        session: Session,
        row: WorkflowNodeEffectAttempt,
        spec: EffectAttemptSpec,
        *,
        claim_owner: str,
        claim_ttl: timedelta,
        allow_retry: bool,
        now: datetime,
    ) -> AcquireResult:
        if not self._same_semantics(row, spec):
            return AcquireResult(AcquireKind.IDENTITY_CONFLICT, self._record(row))
        if row.status == EffectAttemptStatus.TERMINAL.value:
            if row.replay_decision not in {
                ReplayDecision.RETRY_BEFORE_EFFECT.value,
                ReplayDecision.REPLAY_SAME_KEY.value,
            }:
                return AcquireResult(AcquireKind.TERMINAL, self._record(row))
            if not allow_retry:
                row.replay_decision = ReplayDecision.STOP.value
                row.updated_at = now
                session.flush()
                return AcquireResult(AcquireKind.TERMINAL, self._record(row))
            if (
                row.replay_decision == ReplayDecision.REPLAY_SAME_KEY.value
                and (row.replay_deadline_at is None or now >= row.replay_deadline_at)
            ):
                row.replay_decision = ReplayDecision.STOP.value
                row.updated_at = now
                session.flush()
                return AcquireResult(AcquireKind.TERMINAL, self._record(row))
            row.status = EffectAttemptStatus.PREPARED.value
            row.workflow_run_id = spec.context.workflow_run_id
            row.node_run_id = spec.context.node_run_id
            row.claim_owner = claim_owner
            row.claim_expires_at = now + claim_ttl
            row.claim_generation += 1
            row.outcome = None
            row.replay_decision = None
            row.replay_result = None
            row.provider_started_at = None
            row.provider_status_code = None
            row.error_code = None
            row.terminal_at = None
            row.updated_at = now
            session.flush()
            return AcquireResult(AcquireKind.CLAIMED, self._record(row))
        if row.claim_expires_at is not None and row.claim_expires_at <= now:
            if row.status == EffectAttemptStatus.IN_FLIGHT.value:
                outcome = EffectOutcome.EFFECT_OUTCOME_UNKNOWN
                decision = decide_replay(
                    outcome=outcome,
                    provider_replay=ProviderReplayCapability(
                        row.provider_replay_capability
                    ),
                    result_reuse=ResultReuseCapability(row.result_reuse_capability),
                    has_replay_result=False,
                    now=now,
                    replay_deadline_at=row.replay_deadline_at,
                )
                if decision is ReplayDecision.REPLAY_SAME_KEY and not allow_retry:
                    decision = ReplayDecision.STOP
                row.status = EffectAttemptStatus.TERMINAL.value
                row.claim_owner = None
                row.claim_expires_at = None
                row.outcome = outcome.value
                row.replay_decision = decision.value
                row.error_code = "response_lost"
                row.terminal_at = now
                row.updated_at = now
                session.flush()
                return AcquireResult(AcquireKind.TERMINAL, self._record(row))
            row.claim_owner = claim_owner
            row.workflow_run_id = spec.context.workflow_run_id
            row.node_run_id = spec.context.node_run_id
            row.claim_expires_at = now + claim_ttl
            row.claim_generation += 1
            row.updated_at = now
            session.flush()
            return AcquireResult(AcquireKind.CLAIMED, self._record(row))
        return AcquireResult(AcquireKind.WAIT, self._record(row))

    def mark_in_flight(
        self,
        record: EffectAttemptRecord,
        *,
        now: datetime,
    ) -> EffectAttemptRecord:
        del now
        with self._session() as session:
            row = session.execute(
                select(WorkflowNodeEffectAttempt)
                .where(
                    WorkflowNodeEffectAttempt.id == record.id,
                    WorkflowNodeEffectAttempt.organization_id
                    == record.spec.context.organization_id,
                    WorkflowNodeEffectAttempt.status
                    == EffectAttemptStatus.PREPARED.value,
                    WorkflowNodeEffectAttempt.claim_owner == record.claim_owner,
                    WorkflowNodeEffectAttempt.claim_generation
                    == record.claim_generation,
                )
                .with_for_update()
            ).scalar_one_or_none()
            db_now = self._database_now(session)
            if (
                row is None
                or row.claim_expires_at is None
                or row.claim_expires_at <= db_now
            ):
                raise RuntimeError("stale effect claim")
            row.status = EffectAttemptStatus.IN_FLIGHT.value
            row.provider_started_at = db_now
            row.updated_at = db_now
            session.commit()
            session.refresh(row)
            return self._record(row)

    def finish(
        self,
        record: EffectAttemptRecord,
        *,
        outcome: EffectOutcome,
        replay_decision: ReplayDecision,
        replay_result: Any,
        provider_status_code: int | None,
        error_code: str | None,
        now: datetime,
    ) -> EffectAttemptRecord:
        del now
        with self._session() as session:
            row = session.execute(
                select(WorkflowNodeEffectAttempt)
                .where(
                    WorkflowNodeEffectAttempt.id == record.id,
                    WorkflowNodeEffectAttempt.organization_id
                    == record.spec.context.organization_id,
                    WorkflowNodeEffectAttempt.status.in_(
                        (
                            EffectAttemptStatus.PREPARED.value,
                            EffectAttemptStatus.IN_FLIGHT.value,
                        )
                    ),
                    WorkflowNodeEffectAttempt.claim_owner == record.claim_owner,
                    WorkflowNodeEffectAttempt.claim_generation
                    == record.claim_generation,
                )
                .with_for_update()
            ).scalar_one_or_none()
            db_now = self._database_now(session)
            if (
                row is None
                or row.claim_expires_at is None
                or row.claim_expires_at <= db_now
            ):
                raise RuntimeError("stale effect claim")
            row.status = EffectAttemptStatus.TERMINAL.value
            row.claim_owner = None
            row.claim_expires_at = None
            row.outcome = outcome.value
            effective_decision = replay_decision
            if (
                outcome is EffectOutcome.EFFECT_OUTCOME_UNKNOWN
                and replay_decision is ReplayDecision.REPLAY_SAME_KEY
            ):
                effective_decision = decide_replay(
                    outcome=outcome,
                    provider_replay=ProviderReplayCapability(
                        row.provider_replay_capability
                    ),
                    result_reuse=ResultReuseCapability(
                        row.result_reuse_capability
                    ),
                    has_replay_result=False,
                    now=db_now,
                    replay_deadline_at=row.replay_deadline_at,
                )
            row.replay_decision = effective_decision.value
            row.replay_result = copy.deepcopy(replay_result)
            row.provider_status_code = provider_status_code
            row.error_code = error_code
            row.terminal_at = db_now
            row.updated_at = db_now
            session.commit()
            session.refresh(row)
            return self._record(row)

    def _read_by_id(
        self, record: EffectAttemptRecord
    ) -> tuple[EffectAttemptRecord | None, datetime]:
        with self._session() as session:
            row = session.execute(
                select(WorkflowNodeEffectAttempt).where(
                    WorkflowNodeEffectAttempt.id == record.id,
                    WorkflowNodeEffectAttempt.organization_id
                    == record.spec.context.organization_id,
                )
            ).scalar_one_or_none()
            db_now = self._database_now(session)
            return (self._record(row) if row is not None else None, db_now)

    def wait_for_resolution(
        self,
        record: EffectAttemptRecord,
        *,
        deadline: float | None,
    ) -> AcquireResult:
        interval = 0.1
        current = record
        while True:
            monotonic_now = time.monotonic()
            if deadline is not None and monotonic_now >= deadline:
                return AcquireResult(AcquireKind.WAIT, current)
            current, db_now = self._read_by_id(record)
            if current is None:
                raise RuntimeError("effect attempt is unavailable")
            if current.status == EffectAttemptStatus.TERMINAL.value:
                return AcquireResult(AcquireKind.TERMINAL, current)
            expiry = current.claim_expires_at
            if expiry is not None and expiry <= db_now:
                return AcquireResult(AcquireKind.WAIT, current)
            sleep_for = interval
            if deadline is not None:
                sleep_for = min(sleep_for, max(0.0, deadline - monotonic_now))
            if expiry is not None:
                sleep_for = min(
                    sleep_for,
                    max(0.0, (expiry - db_now).total_seconds()),
                )
            if sleep_for <= 0:
                return AcquireResult(AcquireKind.WAIT, current)
            time.sleep(sleep_for)
            interval = min(interval * 2, 1.0)

    def stop_retry(
        self,
        record: EffectAttemptRecord,
        *,
        now: datetime,
    ) -> EffectAttemptRecord:
        del now
        with self._session() as session:
            db_now = self._database_now(session)
            row = session.execute(
                select(WorkflowNodeEffectAttempt)
                .where(
                    WorkflowNodeEffectAttempt.id == record.id,
                    WorkflowNodeEffectAttempt.organization_id
                    == record.spec.context.organization_id,
                    WorkflowNodeEffectAttempt.status
                    == EffectAttemptStatus.TERMINAL.value,
                    WorkflowNodeEffectAttempt.claim_owner.is_(None),
                    WorkflowNodeEffectAttempt.claim_expires_at.is_(None),
                    WorkflowNodeEffectAttempt.claim_generation
                    == record.claim_generation,
                    WorkflowNodeEffectAttempt.replay_decision.in_(
                        (
                            ReplayDecision.RETRY_BEFORE_EFFECT.value,
                            ReplayDecision.REPLAY_SAME_KEY.value,
                        )
                    ),
                )
                .with_for_update()
            ).scalar_one_or_none()
            if row is None:
                current = session.execute(
                    select(WorkflowNodeEffectAttempt).where(
                        WorkflowNodeEffectAttempt.id == record.id,
                        WorkflowNodeEffectAttempt.organization_id
                        == record.spec.context.organization_id,
                    )
                ).scalar_one_or_none()
                if current is None:
                    raise RuntimeError("effect attempt is unavailable")
                return self._record(current)
            row.replay_decision = ReplayDecision.STOP.value
            row.updated_at = db_now
            session.commit()
            session.refresh(row)
            return self._record(row)

    def guard_read_only_slot(
        self,
        *,
        context: ExternalEffectContext,
        effect_sequence: int,
    ) -> None:
        with self._session() as session:
            exists = session.execute(
                select(WorkflowNodeEffectAttempt.id).where(
                    WorkflowNodeEffectAttempt.organization_id
                    == context.organization_id,
                    WorkflowNodeEffectAttempt.execution_id == context.execution_id,
                    WorkflowNodeEffectAttempt.node_invocation_id
                    == context.node_invocation_id,
                    WorkflowNodeEffectAttempt.effect_sequence == effect_sequence,
                )
            ).scalar_one_or_none()
        if exists is not None:
            raise ExternalEffectError(
                "external_effect.identity_conflict",
                retryable=False,
                node_id=context.node_id,
            )
