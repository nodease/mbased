from __future__ import annotations

import re
import uuid
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import and_, delete, func, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, SessionTransaction, aliased

from apps.memory.application.context import (
    ContextAttemptState,
    ContextCandidatePair,
    ContextEntryReference,
    ContextLeaseState,
    MemoryContextLease,
    MemoryContextPlan,
    MemoryContextProviderAttempt,
)
from apps.memory.application.execution import (
    ConversationExecutionScope,
    ResolveConversationExecutionCommand,
    ResolveTerminalConversationExecutionCommand,
)

from apps.memory.application.public_lifecycle import (
    IdempotencyReservation,
    PublicAppBinding,
    PublicDeploymentBinding,
)
from apps.memory.domain.conversation import (
    AudienceKind,
    ConversationMemoryEntry,
    ConversationPurgeJob,
    ConversationSession,
    ConversationTurn,
    DispatchStatus,
    EntryLifecycle,
    EntryType,
    MemoryTurnDispatchJob,
    ProtectedContent,
    ProtectedEntryContent,
    PurgeStatus,
    RequestIdentity,
    SessionLifecycle,
    TurnStatus,
)
from apps.memory.domain.errors import (
    ActiveTurnConflictError,
    DispatchStateConflictError,
    DuplicateRequestConflictError,
    MemoryAdapterUnavailableError,
    StaleRevisionError,
)
from apps.memory.domain.public_access import (
    AccessGrantState,
    ConversationAccessGrant,
    ConversationIdempotency,
    EncryptedSecretReplay,
    IdempotencyResultSnapshot,
    IdempotencyStatus,
)
from apps.shared.domain.conversation_memory_runtime import (
    ConversationMemoryRuntimeContractError,
    conversation_memory_runtime_requested,
    validate_conversation_memory_runtime,
)
from apps.shared.db.models.app import App
from apps.shared.db.models.conversation_memory import (
    ConversationAccessGrantRecord,
    ConversationIdempotencyRecord,
    ConversationMemoryEntryRecord,
    ConversationPurgeJobRecord,
    ConversationSecretReplayRecord,
    ConversationSessionRecord,
    ConversationTurnRecord,
    MemoryContextLeaseRecord,
    MemoryContextPlanRecord,
    MemoryContextProviderAttemptRecord,
    MemoryEntryDependencyRecord,
    MemoryTurnDispatchJobRecord,
)
from apps.shared.db.models.workflow import Workflow
from apps.shared.db.models.workflow_deployment import DeploymentType, WorkflowDeployment


def delete_conversation_sessions_for_resources(
    session: Session,
    *,
    app_ids: Collection[uuid.UUID],
    workflow_ids: Collection[uuid.UUID],
    deployment_ids: Collection[uuid.UUID],
) -> int:
    """Delete Memory sessions owned by resources removed by demo maintenance."""

    conditions = []
    if app_ids:
        conditions.append(ConversationSessionRecord.app_id.in_(tuple(app_ids)))
    if workflow_ids:
        conditions.append(
            ConversationSessionRecord.workflow_id.in_(tuple(workflow_ids))
        )
    if deployment_ids:
        conditions.append(
            ConversationSessionRecord.deployment_id.in_(tuple(deployment_ids))
        )
    if not conditions:
        return 0

    deleted = (
        session.query(ConversationSessionRecord)
        .filter(or_(*conditions))
        .delete(synchronize_session=False)
    )
    return int(deleted or 0)


@dataclass(frozen=True, slots=True)
class _SessionBaseline:
    lifecycle_revision: int
    content_revision: int


@dataclass(frozen=True, slots=True)
class _TurnBaseline:
    version: int


@dataclass(frozen=True, slots=True)
class _EntryBaseline:
    lifecycle: str
    content_revision: int | None


@dataclass(frozen=True, slots=True)
class _DispatchBaseline:
    status: str
    claim_generation: int
    attempt_count: int


@dataclass(frozen=True, slots=True)
class _AccessGrantBaseline:
    state: str


@dataclass(frozen=True, slots=True)
class _IdempotencyBaseline:
    status: str


@dataclass(frozen=True, slots=True)
class _ContextLeaseBaseline:
    state: str
    claim_generation: int
    provider_attempt_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class _ContextAttemptBaseline:
    status: str
    version: int
    claim_generation: int


class SqlAlchemyConversationMemoryRepository:
    """Memory-owned persistence adapter over the shared SQLAlchemy registry."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._session_baselines: dict[
            tuple[uuid.UUID, uuid.UUID], _SessionBaseline
        ] = {}
        self._turn_baselines: dict[
            tuple[uuid.UUID, uuid.UUID, uuid.UUID], _TurnBaseline
        ] = {}
        self._entry_baselines: dict[
            tuple[uuid.UUID, uuid.UUID, uuid.UUID], _EntryBaseline
        ] = {}
        self._dispatch_baselines: dict[
            tuple[uuid.UUID, uuid.UUID], _DispatchBaseline
        ] = {}
        self._access_grant_baselines: dict[uuid.UUID, _AccessGrantBaseline] = {}
        self._idempotency_baselines: dict[uuid.UUID, _IdempotencyBaseline] = {}
        self._context_lease_baselines: dict[uuid.UUID, _ContextLeaseBaseline] = {}
        self._context_attempt_baselines: dict[uuid.UUID, _ContextAttemptBaseline] = {}

    def resolve_public_deployment(
        self,
        url_slug: str,
    ) -> PublicDeploymentBinding | None:
        """Resolve the active public Chatbot binding without a row lock."""

        return self._resolve_public_deployment(url_slug, lock_app=False)

    def public_deployment_requires_conversation_runtime(
        self,
        url_slug: str,
    ) -> bool:
        """Detect Memory-on intent before the versioned contract is validated."""

        statement = (
            select(App, Workflow, WorkflowDeployment)
            .join(Workflow, Workflow.id == App.workflow_id)
            .join(
                WorkflowDeployment,
                WorkflowDeployment.id == App.active_deployment_id,
            )
            .where(App.url_slug == url_slug)
        )
        row = _execute(self._session, statement).one_or_none()
        if row is None:
            return False
        app, workflow, deployment = row
        if (
            app.organization_id is None
            or workflow.organization_id is None
            or app.organization_id != workflow.organization_id
            or workflow.app_id != app.id
            or deployment.app_id != app.id
            or deployment.id != app.active_deployment_id
            or deployment.type != DeploymentType.CHATBOT
            or not deployment.is_active
            or deployment.version < 1
        ):
            return False
        return conversation_memory_runtime_requested(
            deployment.graph_snapshot,
            deployment.config,
        )

    def current_time(self) -> datetime:
        return _execute(
            self._session,
            select(func.clock_timestamp()),
        ).scalar_one()

    def resolve_execution_scope(
        self,
        command: ResolveConversationExecutionCommand,
        *,
        for_update: bool,
    ) -> ConversationExecutionScope | None:
        """Resolve one tenant-bound public execution scope in a single snapshot."""

        statement = (
            select(
                ConversationSessionRecord,
                ConversationTurnRecord,
                MemoryTurnDispatchJobRecord,
                ConversationAccessGrantRecord,
                App,
                Workflow,
                WorkflowDeployment,
            )
            .select_from(ConversationSessionRecord)
            .join(
                ConversationTurnRecord,
                and_(
                    ConversationTurnRecord.organization_id
                    == ConversationSessionRecord.organization_id,
                    ConversationTurnRecord.session_id
                    == ConversationSessionRecord.id,
                ),
            )
            .join(
                MemoryTurnDispatchJobRecord,
                and_(
                    MemoryTurnDispatchJobRecord.organization_id
                    == ConversationSessionRecord.organization_id,
                    MemoryTurnDispatchJobRecord.session_id
                    == ConversationSessionRecord.id,
                    MemoryTurnDispatchJobRecord.id
                    == ConversationTurnRecord.dispatch_id,
                    MemoryTurnDispatchJobRecord.turn_id
                    == ConversationTurnRecord.id,
                ),
            )
            .join(
                ConversationAccessGrantRecord,
                and_(
                    ConversationAccessGrantRecord.organization_id
                    == ConversationSessionRecord.organization_id,
                    ConversationAccessGrantRecord.session_id
                    == ConversationSessionRecord.id,
                    ConversationAccessGrantRecord.id
                    == ConversationTurnRecord.access_grant_id,
                ),
            )
            .join(
                App,
                and_(
                    App.id == ConversationSessionRecord.app_id,
                    App.organization_id
                    == ConversationSessionRecord.organization_id,
                ),
            )
            .join(
                Workflow,
                and_(
                    Workflow.id == ConversationSessionRecord.workflow_id,
                    Workflow.organization_id
                    == ConversationSessionRecord.organization_id,
                    Workflow.app_id == App.id,
                ),
            )
            .join(
                WorkflowDeployment,
                and_(
                    WorkflowDeployment.id
                    == ConversationSessionRecord.deployment_id,
                    WorkflowDeployment.app_id == App.id,
                    WorkflowDeployment.id == App.active_deployment_id,
                ),
            )
            .where(
                ConversationSessionRecord.organization_id
                == command.organization_id,
                ConversationTurnRecord.id == command.turn_id,
                ConversationTurnRecord.dispatch_id == command.dispatch_id,
                MemoryTurnDispatchJobRecord.id == command.dispatch_id,
            )
        )
        if for_update:
            statement = statement.with_for_update(
                of=[
                    App,
                    Workflow,
                    WorkflowDeployment,
                    ConversationSessionRecord,
                    ConversationTurnRecord,
                    ConversationAccessGrantRecord,
                    MemoryTurnDispatchJobRecord,
                ]
            ).execution_options(populate_existing=True)
        row = _execute(self._session, statement).one_or_none()
        if row is None:
            return None
        (
            session_record,
            turn_record,
            dispatch_record,
            grant_record,
            app,
            workflow,
            deployment,
        ) = row
        deployment_binding = _public_deployment_binding(
            app,
            workflow,
            deployment,
        )
        if deployment_binding is None:
            return None
        session = _session_domain(session_record)
        turn = _turn_domain(turn_record)
        dispatch = _dispatch_domain(dispatch_record)
        grant = _access_grant_domain(grant_record)
        if for_update:
            self._session_baselines[(session.organization_id, session.id)] = (
                _SessionBaseline(
                    lifecycle_revision=session_record.lifecycle_revision,
                    content_revision=session_record.content_revision,
                )
            )
            self._turn_baselines[
                (turn.organization_id, turn.session_id, turn.id)
            ] = _TurnBaseline(version=turn_record.version)
            self._dispatch_baselines[
                (dispatch.organization_id, dispatch.id)
            ] = _DispatchBaseline(
                status=dispatch_record.status,
                claim_generation=dispatch_record.claim_generation,
                attempt_count=dispatch_record.attempt_count,
            )
            self._access_grant_baselines[grant.id] = _AccessGrantBaseline(
                state=grant_record.state,
            )
        return ConversationExecutionScope(
            deployment=deployment_binding,
            grant=grant,
            session=session,
            turn=turn,
            dispatch=dispatch,
        )

    def resolve_terminal_execution_scope(
        self,
        command: ResolveTerminalConversationExecutionCommand,
        *,
        for_update: bool,
    ) -> ConversationExecutionScope | None:
        """Resolve immutable terminal references without reauthorizing runtime I/O."""

        statement = (
            select(
                ConversationSessionRecord,
                ConversationTurnRecord,
                MemoryTurnDispatchJobRecord,
                ConversationAccessGrantRecord,
                App,
                Workflow,
                WorkflowDeployment,
            )
            .select_from(ConversationSessionRecord)
            .join(
                ConversationTurnRecord,
                and_(
                    ConversationTurnRecord.organization_id
                    == ConversationSessionRecord.organization_id,
                    ConversationTurnRecord.session_id
                    == ConversationSessionRecord.id,
                ),
            )
            .join(
                MemoryTurnDispatchJobRecord,
                and_(
                    MemoryTurnDispatchJobRecord.organization_id
                    == ConversationSessionRecord.organization_id,
                    MemoryTurnDispatchJobRecord.session_id
                    == ConversationSessionRecord.id,
                    MemoryTurnDispatchJobRecord.id
                    == ConversationTurnRecord.dispatch_id,
                    MemoryTurnDispatchJobRecord.turn_id
                    == ConversationTurnRecord.id,
                ),
            )
            .join(
                ConversationAccessGrantRecord,
                and_(
                    ConversationAccessGrantRecord.organization_id
                    == ConversationSessionRecord.organization_id,
                    ConversationAccessGrantRecord.session_id
                    == ConversationSessionRecord.id,
                    ConversationAccessGrantRecord.id
                    == ConversationTurnRecord.access_grant_id,
                ),
            )
            .join(
                App,
                and_(
                    App.id == ConversationSessionRecord.app_id,
                    App.organization_id
                    == ConversationSessionRecord.organization_id,
                ),
            )
            .join(
                Workflow,
                and_(
                    Workflow.id == ConversationSessionRecord.workflow_id,
                    Workflow.organization_id
                    == ConversationSessionRecord.organization_id,
                    Workflow.app_id == App.id,
                ),
            )
            .join(
                WorkflowDeployment,
                and_(
                    WorkflowDeployment.id
                    == ConversationSessionRecord.deployment_id,
                    WorkflowDeployment.app_id == App.id,
                ),
            )
            .where(
                ConversationSessionRecord.organization_id
                == command.organization_id,
                ConversationTurnRecord.id == command.turn_id,
                ConversationTurnRecord.dispatch_id == command.dispatch_id,
                MemoryTurnDispatchJobRecord.id == command.dispatch_id,
            )
        )
        if for_update:
            statement = statement.with_for_update(
                of=[
                    ConversationSessionRecord,
                    ConversationTurnRecord,
                    ConversationAccessGrantRecord,
                    MemoryTurnDispatchJobRecord,
                ]
            ).execution_options(populate_existing=True)
        row = _execute(self._session, statement).one_or_none()
        if row is None:
            return None
        (
            session_record,
            turn_record,
            dispatch_record,
            grant_record,
            app,
            workflow,
            deployment,
        ) = row
        deployment_binding = _public_deployment_binding(
            app,
            workflow,
            deployment,
            require_active=False,
        )
        if deployment_binding is None:
            return None
        session = _session_domain(session_record)
        turn = _turn_domain(turn_record)
        dispatch = _dispatch_domain(dispatch_record)
        grant = _access_grant_domain(grant_record)
        if for_update:
            self._session_baselines[(session.organization_id, session.id)] = (
                _SessionBaseline(
                    lifecycle_revision=session_record.lifecycle_revision,
                    content_revision=session_record.content_revision,
                )
            )
            self._turn_baselines[
                (turn.organization_id, turn.session_id, turn.id)
            ] = _TurnBaseline(version=turn_record.version)
            self._dispatch_baselines[
                (dispatch.organization_id, dispatch.id)
            ] = _DispatchBaseline(
                status=dispatch_record.status,
                claim_generation=dispatch_record.claim_generation,
                attempt_count=dispatch_record.attempt_count,
            )
            self._access_grant_baselines[grant.id] = _AccessGrantBaseline(
                state=grant_record.state,
            )
        return ConversationExecutionScope(
            deployment=deployment_binding,
            grant=grant,
            session=session,
            turn=turn,
            dispatch=dispatch,
        )

    def lock_public_deployment(
        self,
        url_slug: str,
    ) -> PublicDeploymentBinding | None:
        """Resolve the active public Chatbot binding and lock its App row."""

        return self._resolve_public_deployment(url_slug, lock_app=True)

    def resolve_public_app(self, url_slug: str) -> PublicAppBinding | None:
        """Resolve stable App scope without requiring an active deployment."""

        statement = select(App).where(App.url_slug == url_slug)
        app = _execute(self._session, statement).scalar_one_or_none()
        if app is None or app.organization_id is None:
            return None
        return PublicAppBinding(
            organization_id=app.organization_id,
            app_id=app.id,
        )

    def _resolve_public_deployment(
        self,
        url_slug: str,
        *,
        lock_app: bool,
    ) -> PublicDeploymentBinding | None:
        """Resolve the canonical deployment for one public request boundary.

        ``browser_access_policy`` is intentionally not selected as an API
        policy.  It remains an iframe CSP boundary owned by ADR-0043.
        """

        statement = (
            select(App, Workflow, WorkflowDeployment)
            .join(Workflow, Workflow.id == App.workflow_id)
            .join(
                WorkflowDeployment,
                WorkflowDeployment.id == App.active_deployment_id,
            )
            .where(App.url_slug == url_slug)
        )
        if lock_app:
            statement = statement.with_for_update(of=App)
        row = _execute(self._session, statement).one_or_none()
        if row is None:
            return None
        return _public_deployment_binding(*row)

    def reserve_idempotency(
        self,
        record: ConversationIdempotency,
    ) -> IdempotencyReservation:
        insert_statement = (
            pg_insert(ConversationIdempotencyRecord)
            .values(**_idempotency_values(record))
            .on_conflict_do_nothing(constraint="uq_conv_idempotency_scope_key")
            .returning(ConversationIdempotencyRecord.id)
        )
        inserted_id = _execute(self._session, insert_statement).scalar_one_or_none()
        if inserted_id is not None:
            self._idempotency_baselines[record.id] = _IdempotencyBaseline(
                status=record.status.value
            )
            return IdempotencyReservation(record=record, created=True)

        statement = (
            select(ConversationIdempotencyRecord)
            .where(
                ConversationIdempotencyRecord.organization_id == record.organization_id,
                ConversationIdempotencyRecord.operation == record.operation,
                ConversationIdempotencyRecord.scope_digest == record.scope_digest,
                ConversationIdempotencyRecord.idempotency_key_hash
                == record.idempotency_key_hash,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        existing = _execute(self._session, statement).scalar_one_or_none()
        if existing is None:
            raise MemoryAdapterUnavailableError()
        if existing.retention_expires_at <= record.created_at:
            delete_result = _execute(
                self._session,
                delete(ConversationIdempotencyRecord)
                .where(
                    ConversationIdempotencyRecord.id == existing.id,
                    ConversationIdempotencyRecord.retention_expires_at
                    <= record.created_at,
                )
                .execution_options(synchronize_session=False),
            )
            if delete_result.rowcount != 1:
                raise MemoryAdapterUnavailableError()
            replacement_id = _execute(
                self._session,
                insert_statement,
            ).scalar_one_or_none()
            if replacement_id != record.id:
                raise MemoryAdapterUnavailableError()
            self._idempotency_baselines[record.id] = _IdempotencyBaseline(
                status=record.status.value
            )
            return IdempotencyReservation(record=record, created=True)
        domain = _idempotency_domain(existing)
        self._idempotency_baselines[domain.id] = _IdempotencyBaseline(
            status=existing.status
        )
        return IdempotencyReservation(record=domain, created=False)

    def find_idempotency(
        self,
        *,
        organization_id: uuid.UUID,
        operation: str,
        scope_digest: str,
        idempotency_key_hash: str,
        now: datetime,
    ) -> ConversationIdempotency | None:
        statement = (
            select(ConversationIdempotencyRecord)
            .where(
                ConversationIdempotencyRecord.organization_id == organization_id,
                ConversationIdempotencyRecord.operation == operation,
                ConversationIdempotencyRecord.scope_digest == scope_digest,
                ConversationIdempotencyRecord.idempotency_key_hash
                == idempotency_key_hash,
                ConversationIdempotencyRecord.retention_expires_at > now,
            )
            .execution_options(populate_existing=True)
        )
        record = _execute(self._session, statement).scalar_one_or_none()
        return _idempotency_domain(record) if record is not None else None

    def find_authorized_idempotency(
        self,
        *,
        organization_id: uuid.UUID,
        app_id: uuid.UUID,
        operation: str,
        idempotency_key_hash: str,
        verifier_candidates: tuple[tuple[str, str], ...],
        now: datetime,
    ) -> ConversationIdempotency | None:
        if not verifier_candidates:
            return None
        statement = (
            select(ConversationIdempotencyRecord)
            .where(
                ConversationIdempotencyRecord.organization_id == organization_id,
                ConversationIdempotencyRecord.authorization_app_id == app_id,
                ConversationIdempotencyRecord.operation == operation,
                ConversationIdempotencyRecord.idempotency_key_hash
                == idempotency_key_hash,
                tuple_(
                    ConversationIdempotencyRecord.authorization_verifier_key_version,
                    ConversationIdempotencyRecord.authorization_verifier_hash,
                ).in_(verifier_candidates),
                ConversationIdempotencyRecord.retention_expires_at > now,
            )
            .execution_options(populate_existing=True)
        )
        records = list(_execute(self._session, statement).scalars().all())
        if len(records) != 1:
            return None
        return _idempotency_domain(records[0])

    def save_idempotency(self, record: ConversationIdempotency) -> None:
        baseline = self._idempotency_baselines.get(record.id)
        if baseline is None:
            raise StaleRevisionError()
        statement = (
            update(ConversationIdempotencyRecord)
            .where(
                ConversationIdempotencyRecord.id == record.id,
                ConversationIdempotencyRecord.organization_id == record.organization_id,
                ConversationIdempotencyRecord.status == baseline.status,
            )
            .values(**_idempotency_mutable_values(record))
            .execution_options(synchronize_session=False)
        )
        _require_single_row(_execute(self._session, statement))
        self._idempotency_baselines[record.id] = _IdempotencyBaseline(
            status=record.status.value
        )

    def add_access_grant(self, grant: ConversationAccessGrant) -> None:
        self._session.add(_access_grant_record(grant))

    def lock_access_grant(
        self,
        *,
        verifier_candidates: tuple[tuple[str, str], ...],
    ) -> ConversationAccessGrant | None:
        if not verifier_candidates:
            return None
        statement = (
            select(ConversationAccessGrantRecord)
            .where(
                tuple_(
                    ConversationAccessGrantRecord.verifier_key_version,
                    ConversationAccessGrantRecord.verifier_hash,
                ).in_(verifier_candidates),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        records = list(_execute(self._session, statement).scalars().all())
        if len(records) != 1:
            return None
        record = records[0]
        domain = _access_grant_domain(record)
        self._access_grant_baselines[domain.id] = _AccessGrantBaseline(
            state=record.state
        )
        return domain

    def save_access_grant(self, grant: ConversationAccessGrant) -> None:
        baseline = self._access_grant_baselines.get(grant.id)
        if baseline is None:
            raise StaleRevisionError()
        statement = (
            update(ConversationAccessGrantRecord)
            .where(
                ConversationAccessGrantRecord.id == grant.id,
                ConversationAccessGrantRecord.state == baseline.state,
            )
            .values(
                state=grant.state.value,
                replay_record_reference=grant.replay_record_reference,
                revoked_at=grant.revoked_at,
                updated_at=func.now(),
            )
            .execution_options(synchronize_session=False)
        )
        _require_single_row(_execute(self._session, statement))
        self._access_grant_baselines[grant.id] = _AccessGrantBaseline(
            state=grant.state.value
        )

    def add_secret_replay(self, replay: EncryptedSecretReplay) -> None:
        self._session.add(_secret_replay_record(replay))

    def get_secret_replay(
        self,
        *,
        organization_id: uuid.UUID,
        replay_id: uuid.UUID,
    ) -> EncryptedSecretReplay | None:
        statement = (
            select(ConversationSecretReplayRecord)
            .where(
                ConversationSecretReplayRecord.organization_id == organization_id,
                ConversationSecretReplayRecord.id == replay_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        record = _execute(self._session, statement).scalar_one_or_none()
        return _secret_replay_domain(record) if record is not None else None

    def delete_expired_secret_replays(self, *, now: datetime, limit: int) -> int:
        statement = (
            select(ConversationSecretReplayRecord.id)
            .where(ConversationSecretReplayRecord.expires_at <= now)
            .order_by(
                ConversationSecretReplayRecord.expires_at,
                ConversationSecretReplayRecord.id,
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        replay_ids = list(_execute(self._session, statement).scalars().all())
        if not replay_ids:
            return 0
        _execute(
            self._session,
            delete(ConversationSecretReplayRecord)
            .where(ConversationSecretReplayRecord.id.in_(replay_ids))
            .execution_options(synchronize_session=False),
        )
        return len(replay_ids)

    def delete_expired_idempotency_records(self, *, now: datetime, limit: int) -> int:
        statement = (
            select(ConversationIdempotencyRecord.id)
            .where(ConversationIdempotencyRecord.retention_expires_at <= now)
            .order_by(
                ConversationIdempotencyRecord.retention_expires_at,
                ConversationIdempotencyRecord.id,
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        record_ids = list(_execute(self._session, statement).scalars().all())
        if not record_ids:
            return 0
        _execute(
            self._session,
            delete(ConversationIdempotencyRecord)
            .where(ConversationIdempotencyRecord.id.in_(record_ids))
            .execution_options(synchronize_session=False),
        )
        return len(record_ids)

    def find_purge_job(
        self,
        *,
        verifier_candidates: tuple[tuple[str, str], ...],
    ) -> ConversationPurgeJob | None:
        if not verifier_candidates:
            return None
        statement = select(ConversationPurgeJobRecord).where(
            tuple_(
                ConversationPurgeJobRecord.receipt_verifier_key_version,
                ConversationPurgeJobRecord.receipt_verifier_hash,
            ).in_(verifier_candidates)
        )
        records = list(_execute(self._session, statement).scalars().all())
        return _purge_domain(records[0]) if len(records) == 1 else None

    def lock_purge_job_by_id(
        self,
        *,
        organization_id: uuid.UUID,
        purge_job_id: uuid.UUID,
    ) -> ConversationPurgeJob | None:
        statement = (
            select(ConversationPurgeJobRecord)
            .where(
                ConversationPurgeJobRecord.organization_id == organization_id,
                ConversationPurgeJobRecord.id == purge_job_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        record = _execute(self._session, statement).scalar_one_or_none()
        return _purge_domain(record) if record is not None else None

    def add_session(self, session: ConversationSession) -> None:
        self._session.add(_session_record(session))

    def lock_session(
        self,
        *,
        organization_id: uuid.UUID,
        session_id: uuid.UUID,
    ) -> ConversationSession | None:
        statement = (
            select(ConversationSessionRecord)
            .where(
                ConversationSessionRecord.organization_id == organization_id,
                ConversationSessionRecord.id == session_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        record = _execute(self._session, statement).scalar_one_or_none()
        if record is None:
            return None
        domain = _session_domain(record)
        self._session_baselines[(organization_id, session_id)] = _SessionBaseline(
            lifecycle_revision=record.lifecycle_revision,
            content_revision=record.content_revision,
        )
        return domain

    def save_session(self, session: ConversationSession) -> None:
        key = (session.organization_id, session.id)
        baseline = self._session_baselines.get(key)
        if baseline is None:
            raise StaleRevisionError()
        statement = (
            update(ConversationSessionRecord)
            .where(
                ConversationSessionRecord.organization_id == session.organization_id,
                ConversationSessionRecord.id == session.id,
                ConversationSessionRecord.lifecycle_revision
                == baseline.lifecycle_revision,
                ConversationSessionRecord.content_revision == baseline.content_revision,
            )
            .values(
                lifecycle=session.lifecycle.value,
                lifecycle_revision=session.lifecycle_revision,
                content_revision=session.content_revision,
                active_turn_id=session.active_turn_id,
                next_turn_sequence=session.next_turn_sequence,
                idle_expires_at=session.idle_expires_at,
                absolute_expires_at=session.absolute_expires_at,
                closed_at=session.closed_at,
                delete_requested_at=session.delete_requested_at,
                deleted_at=session.deleted_at,
                updated_at=session.updated_at,
            )
            .execution_options(synchronize_session=False)
        )
        _require_single_row(_execute(self._session, statement))
        self._session_baselines[key] = _SessionBaseline(
            lifecycle_revision=session.lifecycle_revision,
            content_revision=session.content_revision,
        )

    def find_turn_by_request(
        self,
        *,
        organization_id: uuid.UUID,
        session_id: uuid.UUID,
        idempotency_key_hash: str,
    ) -> ConversationTurn | None:
        statement = select(ConversationTurnRecord).where(
            ConversationTurnRecord.organization_id == organization_id,
            ConversationTurnRecord.session_id == session_id,
            ConversationTurnRecord.request_idempotency_hash == idempotency_key_hash,
        )
        record = _execute(self._session, statement).scalar_one_or_none()
        return _turn_domain(record) if record is not None else None

    def add_turn(self, turn: ConversationTurn) -> None:
        self._session.add(_turn_record(turn))

    def lock_turn(
        self,
        *,
        organization_id: uuid.UUID,
        session_id: uuid.UUID,
        turn_id: uuid.UUID,
    ) -> ConversationTurn | None:
        statement = (
            select(ConversationTurnRecord)
            .where(
                ConversationTurnRecord.organization_id == organization_id,
                ConversationTurnRecord.session_id == session_id,
                ConversationTurnRecord.id == turn_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        record = _execute(self._session, statement).scalar_one_or_none()
        if record is None:
            return None
        domain = _turn_domain(record)
        self._turn_baselines[(organization_id, session_id, turn_id)] = _TurnBaseline(
            version=record.version
        )
        return domain

    def save_turn(self, turn: ConversationTurn) -> None:
        key = (turn.organization_id, turn.session_id, turn.id)
        baseline = self._turn_baselines.get(key)
        if baseline is None:
            raise StaleRevisionError()
        statement = (
            update(ConversationTurnRecord)
            .where(
                ConversationTurnRecord.organization_id == turn.organization_id,
                ConversationTurnRecord.session_id == turn.session_id,
                ConversationTurnRecord.id == turn.id,
                ConversationTurnRecord.version == baseline.version,
            )
            .values(
                version=turn.version,
                status=turn.status.value,
                assistant_entry_id=turn.assistant_entry_id,
                execution_id=turn.execution_id,
                latest_attempt_id=turn.latest_attempt_id,
                safe_failure_reason=turn.safe_failure_reason,
                started_at=turn.started_at,
                completed_at=turn.completed_at,
                updated_at=turn.updated_at,
            )
            .execution_options(synchronize_session=False)
        )
        _require_single_row(_execute(self._session, statement))
        self._turn_baselines[key] = _TurnBaseline(version=turn.version)

    def add_entry(self, entry: ConversationMemoryEntry) -> None:
        self._session.add(_entry_record(entry))

    def get_entry(
        self,
        *,
        organization_id: uuid.UUID,
        session_id: uuid.UUID,
        entry_id: uuid.UUID,
    ) -> ConversationMemoryEntry | None:
        statement = (
            select(ConversationMemoryEntryRecord)
            .where(
                ConversationMemoryEntryRecord.organization_id == organization_id,
                ConversationMemoryEntryRecord.session_id == session_id,
                ConversationMemoryEntryRecord.id == entry_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        record = _execute(self._session, statement).scalar_one_or_none()
        if record is None:
            return None
        domain = _entry_domain(record)
        self._entry_baselines[(organization_id, session_id, entry_id)] = _EntryBaseline(
            lifecycle=record.lifecycle,
            content_revision=record.content_revision,
        )
        return domain

    def save_entry(self, entry: ConversationMemoryEntry) -> None:
        key = (entry.organization_id, entry.session_id, entry.id)
        baseline = self._entry_baselines.get(key)
        if baseline is None:
            raise StaleRevisionError()
        content_revision_predicate = (
            ConversationMemoryEntryRecord.content_revision.is_(None)
            if baseline.content_revision is None
            else ConversationMemoryEntryRecord.content_revision
            == baseline.content_revision
        )
        statement = (
            update(ConversationMemoryEntryRecord)
            .where(
                ConversationMemoryEntryRecord.organization_id == entry.organization_id,
                ConversationMemoryEntryRecord.session_id == entry.session_id,
                ConversationMemoryEntryRecord.id == entry.id,
                ConversationMemoryEntryRecord.lifecycle == baseline.lifecycle,
                content_revision_predicate,
            )
            .values(
                lifecycle=entry.lifecycle.value,
                content_revision=entry.content_revision,
                invalidated_at=entry.invalidated_at,
                expires_at=entry.expires_at,
                updated_at=entry.updated_at,
            )
            .execution_options(synchronize_session=False)
        )
        _require_single_row(_execute(self._session, statement))
        self._entry_baselines[key] = _EntryBaseline(
            lifecycle=entry.lifecycle.value,
            content_revision=entry.content_revision,
        )

    def list_prior_context_candidates(
        self,
        *,
        organization_id: uuid.UUID,
        session_id: uuid.UUID,
        before_turn_sequence: int,
        limit: int,
    ) -> tuple[ContextCandidatePair, ...]:
        """Return newest-first pair references without projecting protected content."""

        if limit <= 0:
            return ()
        user_entry = aliased(
            ConversationMemoryEntryRecord,
            name="context_user_entry",
        )
        assistant_entry = aliased(
            ConversationMemoryEntryRecord,
            name="context_assistant_entry",
        )
        dependency_count = (
            select(func.count(MemoryEntryDependencyRecord.dependency_id))
            .where(
                MemoryEntryDependencyRecord.organization_id == organization_id,
                MemoryEntryDependencyRecord.session_id == session_id,
                or_(
                    MemoryEntryDependencyRecord.entry_id
                    == ConversationTurnRecord.user_entry_id,
                    MemoryEntryDependencyRecord.entry_id
                    == ConversationTurnRecord.assistant_entry_id,
                ),
            )
            .correlate(ConversationTurnRecord)
            .scalar_subquery()
        )
        statement = (
            select(
                ConversationTurnRecord.id.label("turn_id"),
                ConversationTurnRecord.sequence.label("turn_sequence"),
                ConversationTurnRecord.status.label("turn_status"),
                user_entry.id.label("user_entry_id"),
                user_entry.turn_id.label("user_turn_id"),
                user_entry.sequence.label("user_sequence"),
                user_entry.entry_type.label("user_entry_type"),
                user_entry.content_revision.label("user_content_revision"),
                user_entry.model_content_digest.label("user_content_digest"),
                user_entry.dependency_proof_version.label(
                    "user_dependency_proof_version"
                ),
                assistant_entry.id.label("assistant_entry_id"),
                assistant_entry.turn_id.label("assistant_turn_id"),
                assistant_entry.sequence.label("assistant_sequence"),
                assistant_entry.entry_type.label("assistant_entry_type"),
                assistant_entry.content_revision.label("assistant_content_revision"),
                assistant_entry.model_content_digest.label("assistant_content_digest"),
                assistant_entry.dependency_proof_version.label(
                    "assistant_dependency_proof_version"
                ),
                dependency_count.label("dependency_count"),
            )
            .select_from(ConversationTurnRecord)
            .outerjoin(
                user_entry,
                and_(
                    user_entry.organization_id == organization_id,
                    user_entry.session_id == session_id,
                    user_entry.id == ConversationTurnRecord.user_entry_id,
                    user_entry.turn_id == ConversationTurnRecord.id,
                    user_entry.channel == "conversation",
                    user_entry.lifecycle == EntryLifecycle.APPROVED.value,
                    user_entry.invalidated_at.is_(None),
                    or_(
                        user_entry.expires_at.is_(None),
                        user_entry.expires_at > func.clock_timestamp(),
                    ),
                ),
            )
            .outerjoin(
                assistant_entry,
                and_(
                    assistant_entry.organization_id == organization_id,
                    assistant_entry.session_id == session_id,
                    assistant_entry.id == ConversationTurnRecord.assistant_entry_id,
                    assistant_entry.turn_id == ConversationTurnRecord.id,
                    assistant_entry.channel == "conversation",
                    assistant_entry.lifecycle == EntryLifecycle.APPROVED.value,
                    assistant_entry.invalidated_at.is_(None),
                    or_(
                        assistant_entry.expires_at.is_(None),
                        assistant_entry.expires_at > func.clock_timestamp(),
                    ),
                ),
            )
            .where(
                ConversationTurnRecord.organization_id == organization_id,
                ConversationTurnRecord.session_id == session_id,
                ConversationTurnRecord.sequence < before_turn_sequence,
                ConversationTurnRecord.status == TurnStatus.COMPLETED.value,
            )
            .order_by(ConversationTurnRecord.sequence.desc())
            .limit(limit)
        )
        rows = _execute(self._session, statement).mappings().all()
        return tuple(_context_candidate_domain(row) for row in rows)

    def find_context_plan(
        self,
        plan_id: uuid.UUID,
    ) -> MemoryContextPlan | None:
        statement = select(MemoryContextPlanRecord).where(
            MemoryContextPlanRecord.id == plan_id
        )
        record = _execute(self._session, statement).scalar_one_or_none()
        return _context_plan_domain(record) if record is not None else None

    def add_context_plan(self, plan: MemoryContextPlan) -> None:
        self._session.add(_context_plan_record(plan))

    def find_context_lease(
        self,
        lease_id: uuid.UUID,
    ) -> MemoryContextLease | None:
        return self._load_context_lease(lease_id, for_update=False)

    def lock_context_lease(
        self,
        lease_id: uuid.UUID,
    ) -> MemoryContextLease | None:
        return self._load_context_lease(lease_id, for_update=True)

    def _load_context_lease(
        self,
        lease_id: uuid.UUID,
        *,
        for_update: bool,
    ) -> MemoryContextLease | None:
        statement = select(MemoryContextLeaseRecord).where(
            MemoryContextLeaseRecord.id == lease_id
        )
        if for_update:
            statement = statement.with_for_update().execution_options(
                populate_existing=True
            )
        record = _execute(self._session, statement).scalar_one_or_none()
        if record is None:
            return None
        lease = _context_lease_domain(record)
        if for_update:
            self._context_lease_baselines[lease.id] = _ContextLeaseBaseline(
                state=record.state,
                claim_generation=record.claim_generation,
                provider_attempt_id=record.provider_attempt_id,
            )
        return lease

    def add_context_lease(self, lease: MemoryContextLease) -> None:
        self._session.add(_context_lease_record(lease))

    def save_context_lease(self, lease: MemoryContextLease) -> None:
        baseline = self._context_lease_baselines.get(lease.id)
        if baseline is None:
            raise StaleRevisionError()
        provider_attempt_predicate = (
            MemoryContextLeaseRecord.provider_attempt_id.is_(None)
            if baseline.provider_attempt_id is None
            else MemoryContextLeaseRecord.provider_attempt_id
            == baseline.provider_attempt_id
        )
        statement = (
            update(MemoryContextLeaseRecord)
            .where(
                MemoryContextLeaseRecord.id == lease.id,
                MemoryContextLeaseRecord.organization_id == lease.organization_id,
                MemoryContextLeaseRecord.session_id == lease.session_id,
                MemoryContextLeaseRecord.state == baseline.state,
                MemoryContextLeaseRecord.claim_generation == baseline.claim_generation,
                provider_attempt_predicate,
            )
            .values(
                state=lease.state.value,
                claim_generation=lease.claim_generation,
                provider_attempt_id=lease.provider_attempt_id,
                claim_deadline_at=lease.claim_deadline_at,
                updated_at=func.now(),
            )
            .execution_options(synchronize_session=False)
        )
        _require_single_row(_execute(self._session, statement))
        self._context_lease_baselines[lease.id] = _ContextLeaseBaseline(
            state=lease.state.value,
            claim_generation=lease.claim_generation,
            provider_attempt_id=lease.provider_attempt_id,
        )

    def get_context_entry(
        self,
        reference: ContextEntryReference,
    ) -> ConversationMemoryEntry | None:
        proof_predicate = (
            ConversationMemoryEntryRecord.dependency_proof_version.is_(None)
            if reference.dependency_proof_version is None
            else ConversationMemoryEntryRecord.dependency_proof_version
            == reference.dependency_proof_version
        )
        statement = select(ConversationMemoryEntryRecord).where(
            ConversationMemoryEntryRecord.id == reference.entry_id,
            ConversationMemoryEntryRecord.turn_id == reference.turn_id,
            ConversationMemoryEntryRecord.sequence == reference.sequence,
            ConversationMemoryEntryRecord.entry_type == reference.entry_type.value,
            ConversationMemoryEntryRecord.content_revision
            == reference.content_revision,
            ConversationMemoryEntryRecord.model_content_digest
            == reference.content_digest,
            ConversationMemoryEntryRecord.channel == "conversation",
            ConversationMemoryEntryRecord.lifecycle == EntryLifecycle.APPROVED.value,
            ConversationMemoryEntryRecord.invalidated_at.is_(None),
            proof_predicate,
        )
        record = _execute(self._session, statement).scalar_one_or_none()
        return _entry_domain(record) if record is not None else None

    def find_context_attempt(
        self,
        attempt_id: uuid.UUID,
    ) -> MemoryContextProviderAttempt | None:
        return self._load_context_attempt(attempt_id, for_update=False)

    def lock_context_attempt(
        self,
        attempt_id: uuid.UUID,
    ) -> MemoryContextProviderAttempt | None:
        return self._load_context_attempt(attempt_id, for_update=True)

    def _load_context_attempt(
        self,
        attempt_id: uuid.UUID,
        *,
        for_update: bool,
    ) -> MemoryContextProviderAttempt | None:
        statement = select(MemoryContextProviderAttemptRecord).where(
            MemoryContextProviderAttemptRecord.id == attempt_id
        )
        if for_update:
            statement = statement.with_for_update().execution_options(
                populate_existing=True
            )
        record = _execute(self._session, statement).scalar_one_or_none()
        if record is None:
            return None
        attempt = _context_attempt_domain(record)
        if for_update:
            self._context_attempt_baselines[attempt.id] = _ContextAttemptBaseline(
                status=record.status,
                version=record.version,
                claim_generation=record.claim_generation,
            )
        return attempt

    def add_context_attempt(self, attempt: MemoryContextProviderAttempt) -> None:
        self._session.add(_context_attempt_record(attempt))

    def save_context_attempt(self, attempt: MemoryContextProviderAttempt) -> None:
        baseline = self._context_attempt_baselines.get(attempt.id)
        if baseline is None:
            raise StaleRevisionError()
        statement = (
            update(MemoryContextProviderAttemptRecord)
            .where(
                MemoryContextProviderAttemptRecord.id == attempt.id,
                MemoryContextProviderAttemptRecord.organization_id
                == attempt.organization_id,
                MemoryContextProviderAttemptRecord.session_id == attempt.session_id,
                MemoryContextProviderAttemptRecord.status == baseline.status,
                MemoryContextProviderAttemptRecord.version == baseline.version,
                MemoryContextProviderAttemptRecord.claim_generation
                == baseline.claim_generation,
            )
            .values(
                status=attempt.status.value,
                version=attempt.version,
                claim_generation=attempt.claim_generation,
                claim_deadline_at=attempt.claim_deadline_at,
                provider_started_at=attempt.provider_started_at,
                usage_reference=attempt.usage_reference,
                safe_failure_reason=attempt.safe_failure_reason,
                terminal_at=attempt.terminal_at,
                updated_at=func.now(),
            )
            .execution_options(synchronize_session=False)
        )
        _require_single_row(_execute(self._session, statement))
        self._context_attempt_baselines[attempt.id] = _ContextAttemptBaseline(
            status=attempt.status.value,
            version=attempt.version,
            claim_generation=attempt.claim_generation,
        )

    def add_dispatch_job(self, job: MemoryTurnDispatchJob) -> None:
        self._session.add(_dispatch_record(job))

    def lock_dispatch_job(
        self,
        *,
        organization_id: uuid.UUID,
        dispatch_id: uuid.UUID,
    ) -> MemoryTurnDispatchJob | None:
        statement = (
            select(MemoryTurnDispatchJobRecord)
            .where(
                MemoryTurnDispatchJobRecord.organization_id == organization_id,
                MemoryTurnDispatchJobRecord.id == dispatch_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        record = _execute(self._session, statement).scalar_one_or_none()
        if record is None:
            return None
        domain = _dispatch_domain(record)
        self._dispatch_baselines[(organization_id, dispatch_id)] = _DispatchBaseline(
            status=record.status,
            claim_generation=record.claim_generation,
            attempt_count=record.attempt_count,
        )
        return domain

    def save_dispatch_job(self, job: MemoryTurnDispatchJob) -> None:
        key = (job.organization_id, job.id)
        baseline = self._dispatch_baselines.get(key)
        if baseline is None:
            raise DispatchStateConflictError()
        statement = (
            update(MemoryTurnDispatchJobRecord)
            .where(
                MemoryTurnDispatchJobRecord.organization_id == job.organization_id,
                MemoryTurnDispatchJobRecord.id == job.id,
                MemoryTurnDispatchJobRecord.status == baseline.status,
                MemoryTurnDispatchJobRecord.claim_generation
                == baseline.claim_generation,
                MemoryTurnDispatchJobRecord.attempt_count == baseline.attempt_count,
            )
            .values(
                status=job.status.value,
                claim_generation=job.claim_generation,
                claim_owner=job.claim_owner,
                claim_deadline_at=job.claim_deadline_at,
                attempt_count=job.attempt_count,
                max_attempts=job.max_attempts,
                next_attempt_at=job.next_attempt_at,
                broker_message_id=job.broker_message_id,
                workflow_admission_reference=job.workflow_admission_reference,
                safe_failure_reason=job.safe_failure_reason,
                published_at=job.published_at,
                acknowledged_at=job.acknowledged_at,
                terminal_at=job.terminal_at,
                updated_at=job.updated_at,
            )
            .execution_options(synchronize_session=False)
        )
        _require_single_row(
            _execute(self._session, statement),
            error_type=DispatchStateConflictError,
        )
        self._dispatch_baselines[key] = _DispatchBaseline(
            status=job.status.value,
            claim_generation=job.claim_generation,
            attempt_count=job.attempt_count,
        )

    def add_purge_job(self, job: ConversationPurgeJob) -> None:
        self._session.add(_purge_record(job))


class SqlAlchemyMemoryUnitOfWork:
    """Owns the transaction used by one Memory mutation use case."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._transaction: SessionTransaction | None = None

    def begin(self) -> None:
        if self._transaction is not None:
            raise RuntimeError("memory unit of work is already active")
        try:
            self._transaction = self._session.begin()
        except SQLAlchemyError:
            raise MemoryAdapterUnavailableError() from None

    def commit(self) -> None:
        transaction = self._require_transaction()
        try:
            transaction.commit()
        except SQLAlchemyError as exc:
            _raise_safe_persistence_error(exc)
        self._transaction = None

    def rollback(self) -> None:
        transaction = self._require_transaction()
        try:
            try:
                transaction.rollback()
            except SQLAlchemyError:
                raise MemoryAdapterUnavailableError() from None
        finally:
            self._transaction = None

    def _require_transaction(self) -> SessionTransaction:
        if self._transaction is None:
            raise RuntimeError("memory unit of work is not active")
        return self._transaction


def _require_single_row(
    result: Any,
    *,
    error_type: type[StaleRevisionError] = StaleRevisionError,
) -> None:
    if result.rowcount != 1:
        raise error_type()


def _execute(session: Session, statement):
    try:
        return session.execute(statement)
    except SQLAlchemyError as exc:
        _raise_safe_persistence_error(exc)


def _raise_safe_persistence_error(exc: SQLAlchemyError) -> None:
    if isinstance(exc, IntegrityError):
        constraint_name = getattr(getattr(exc, "orig", None), "diag", None)
        constraint_name = getattr(constraint_name, "constraint_name", None)
        if constraint_name == "uq_conv_turns_one_active":
            raise ActiveTurnConflictError() from None
        if constraint_name == "uq_conv_turns_session_request":
            raise DuplicateRequestConflictError() from None
    raise MemoryAdapterUnavailableError() from None


def _session_record(session: ConversationSession) -> ConversationSessionRecord:
    return ConversationSessionRecord(
        id=session.id,
        organization_id=session.organization_id,
        app_id=session.app_id,
        workflow_id=session.workflow_id,
        deployment_id=session.deployment_id,
        deployment_version=session.deployment_version,
        deployment_snapshot_hash=session.deployment_snapshot_hash,
        mapping_version=session.mapping_version,
        memory_policy_version=session.memory_policy_version,
        memory_contract_version=session.memory_contract_version,
        storage_generation=session.storage_generation,
        audience_kind=session.audience_kind.value,
        subject_type=session.subject_type,
        subject_id=session.subject_id,
        lifecycle=session.lifecycle.value,
        lifecycle_revision=session.lifecycle_revision,
        content_revision=session.content_revision,
        active_turn_id=session.active_turn_id,
        next_turn_sequence=session.next_turn_sequence,
        idle_expires_at=session.idle_expires_at,
        absolute_expires_at=session.absolute_expires_at,
        closed_at=session.closed_at,
        delete_requested_at=session.delete_requested_at,
        deleted_at=session.deleted_at,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


def _session_domain(record: ConversationSessionRecord) -> ConversationSession:
    return ConversationSession(
        id=record.id,
        organization_id=record.organization_id,
        app_id=record.app_id,
        workflow_id=record.workflow_id,
        deployment_id=record.deployment_id,
        deployment_version=record.deployment_version,
        deployment_snapshot_hash=record.deployment_snapshot_hash,
        mapping_version=record.mapping_version,
        memory_policy_version=record.memory_policy_version,
        memory_contract_version=record.memory_contract_version,
        storage_generation=record.storage_generation,
        audience_kind=AudienceKind(record.audience_kind),
        subject_type=record.subject_type,
        subject_id=record.subject_id,
        lifecycle=SessionLifecycle(record.lifecycle),
        lifecycle_revision=record.lifecycle_revision,
        content_revision=record.content_revision,
        active_turn_id=record.active_turn_id,
        next_turn_sequence=record.next_turn_sequence,
        idle_expires_at=record.idle_expires_at,
        absolute_expires_at=record.absolute_expires_at,
        created_at=record.created_at,
        updated_at=record.updated_at,
        closed_at=record.closed_at,
        delete_requested_at=record.delete_requested_at,
        deleted_at=record.deleted_at,
    )


def _turn_record(turn: ConversationTurn) -> ConversationTurnRecord:
    return ConversationTurnRecord(
        id=turn.id,
        organization_id=turn.organization_id,
        session_id=turn.session_id,
        sequence=turn.sequence,
        version=turn.version,
        started_lifecycle_revision=turn.started_lifecycle_revision,
        request_idempotency_hash=turn.request_identity.idempotency_key_hash,
        request_fingerprint=turn.request_identity.request_fingerprint,
        request_fingerprint_key_version=turn.request_fingerprint_key_version,
        access_grant_id=turn.access_grant_id,
        status=turn.status.value,
        user_entry_id=turn.user_entry_id,
        assistant_entry_id=turn.assistant_entry_id,
        dispatch_id=turn.dispatch_id,
        execution_id=turn.execution_id,
        latest_attempt_id=turn.latest_attempt_id,
        safe_failure_reason=turn.safe_failure_reason,
        started_at=turn.started_at,
        completed_at=turn.completed_at,
        created_at=turn.created_at,
        updated_at=turn.updated_at,
    )


def _turn_domain(record: ConversationTurnRecord) -> ConversationTurn:
    return ConversationTurn(
        id=record.id,
        organization_id=record.organization_id,
        session_id=record.session_id,
        sequence=record.sequence,
        version=record.version,
        started_lifecycle_revision=record.started_lifecycle_revision,
        request_identity=RequestIdentity(
            idempotency_key_hash=record.request_idempotency_hash,
            request_fingerprint=record.request_fingerprint,
        ),
        status=TurnStatus(record.status),
        user_entry_id=record.user_entry_id,
        dispatch_id=record.dispatch_id,
        assistant_entry_id=record.assistant_entry_id,
        execution_id=record.execution_id,
        latest_attempt_id=record.latest_attempt_id,
        safe_failure_reason=record.safe_failure_reason,
        created_at=record.created_at,
        updated_at=record.updated_at,
        started_at=record.started_at,
        completed_at=record.completed_at,
        access_grant_id=record.access_grant_id,
        request_fingerprint_key_version=record.request_fingerprint_key_version,
    )


def _entry_record(entry: ConversationMemoryEntry) -> ConversationMemoryEntryRecord:
    content = entry.content
    display = content.display if content is not None else None
    model = content.model if content is not None else None
    return ConversationMemoryEntryRecord(
        id=entry.id,
        organization_id=entry.organization_id,
        session_id=entry.session_id,
        turn_id=entry.turn_id,
        sequence=entry.sequence,
        entry_type=entry.entry_type.value,
        lifecycle=entry.lifecycle.value,
        channel=entry.channel,
        producer_node_id=entry.producer_node_id,
        display_ciphertext=display.ciphertext if display is not None else None,
        display_key_version=display.key_version if display is not None else None,
        display_format_version=(
            display.format_version if display is not None else None
        ),
        display_content_digest=(
            display.content_digest if display is not None else None
        ),
        display_plaintext_byte_length=(
            display.plaintext_byte_length if display is not None else None
        ),
        model_ciphertext=model.ciphertext if model is not None else None,
        model_key_version=model.key_version if model is not None else None,
        model_format_version=model.format_version if model is not None else None,
        model_content_digest=(model.content_digest if model is not None else None),
        model_plaintext_byte_length=(
            model.plaintext_byte_length if model is not None else None
        ),
        content_revision=entry.content_revision,
        idempotency_key_hash=entry.idempotency_key_hash,
        dependency_proof_version=entry.dependency_proof_version,
        sensitivity="unclassified",
        expires_at=entry.expires_at,
        invalidated_at=entry.invalidated_at,
        erased_at=None if content is not None else entry.updated_at,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
    )


def _entry_domain(record: ConversationMemoryEntryRecord) -> ConversationMemoryEntry:
    display = _projection_domain(record, "display")
    model = _projection_domain(record, "model")
    content = (
        ProtectedEntryContent(display=display, model=model)
        if display is not None or model is not None
        else None
    )
    return ConversationMemoryEntry(
        id=record.id,
        organization_id=record.organization_id,
        session_id=record.session_id,
        turn_id=record.turn_id,
        sequence=record.sequence,
        entry_type=EntryType(record.entry_type),
        lifecycle=EntryLifecycle(record.lifecycle),
        channel=record.channel,
        producer_node_id=record.producer_node_id,
        content=content,
        content_revision=record.content_revision,
        idempotency_key_hash=record.idempotency_key_hash,
        created_at=record.created_at,
        updated_at=record.updated_at,
        dependency_proof_version=record.dependency_proof_version,
        invalidated_at=record.invalidated_at,
        expires_at=record.expires_at,
    )


def _projection_domain(
    record: ConversationMemoryEntryRecord,
    projection: str,
) -> ProtectedContent | None:
    ciphertext = getattr(record, f"{projection}_ciphertext")
    if ciphertext is None:
        return None
    return ProtectedContent(
        ciphertext=bytes(ciphertext),
        key_version=getattr(record, f"{projection}_key_version"),
        format_version=getattr(record, f"{projection}_format_version"),
        content_digest=getattr(record, f"{projection}_content_digest"),
        plaintext_byte_length=getattr(
            record,
            f"{projection}_plaintext_byte_length",
        ),
    )


def _context_candidate_domain(row: Any) -> ContextCandidatePair:
    try:
        dependency_count = int(row["dependency_count"])
        if dependency_count < 0:
            raise ValueError
        return ContextCandidatePair(
            turn_id=_context_uuid(row["turn_id"]),
            turn_sequence=int(row["turn_sequence"]),
            status=TurnStatus(row["turn_status"]),
            user=_context_reference_from_row(row, "user"),
            assistant=_context_reference_from_row(row, "assistant"),
            dependency_count=dependency_count,
        )
    except (KeyError, TypeError, ValueError):
        raise MemoryAdapterUnavailableError() from None


def _context_reference_from_row(
    row: Any,
    prefix: str,
) -> ContextEntryReference | None:
    entry_id = row[f"{prefix}_entry_id"]
    turn_id = row[f"{prefix}_turn_id"]
    sequence = row[f"{prefix}_sequence"]
    entry_type = row[f"{prefix}_entry_type"]
    content_revision = row[f"{prefix}_content_revision"]
    content_digest = row[f"{prefix}_content_digest"]
    if any(
        value is None
        for value in (
            entry_id,
            turn_id,
            sequence,
            entry_type,
            content_revision,
            content_digest,
        )
    ):
        return None
    return ContextEntryReference(
        entry_id=_context_uuid(entry_id),
        turn_id=_context_uuid(turn_id),
        sequence=int(sequence),
        entry_type=EntryType(entry_type),
        content_revision=int(content_revision),
        content_digest=str(content_digest),
        dependency_proof_version=row[f"{prefix}_dependency_proof_version"],
    )


def _context_plan_record(plan: MemoryContextPlan) -> MemoryContextPlanRecord:
    return MemoryContextPlanRecord(
        id=plan.id,
        organization_id=plan.organization_id,
        session_id=plan.session_id,
        turn_id=plan.turn_id,
        channel="conversation",
        ordered_references=[
            _context_candidate_payload(pair) for pair in plan.ordered_pairs
        ],
        policy_version=plan.policy_version,
        content_digest=plan.content_digest,
        lifecycle_revision=plan.lifecycle_revision,
        content_revision=plan.content_revision,
        source_revision=plan.turn_version,
        authorization_revision_set_digest=(plan.authorization_revision_set_digest),
        expires_at=plan.expires_at,
        invalidated_at=None,
    )


def _context_plan_domain(record: MemoryContextPlanRecord) -> MemoryContextPlan:
    try:
        if record.turn_id is None or not isinstance(record.ordered_references, list):
            raise ValueError
        return MemoryContextPlan(
            id=record.id,
            organization_id=record.organization_id,
            session_id=record.session_id,
            turn_id=record.turn_id,
            ordered_pairs=tuple(
                _context_candidate_from_payload(payload)
                for payload in record.ordered_references
            ),
            policy_version=record.policy_version,
            content_digest=record.content_digest,
            lifecycle_revision=record.lifecycle_revision,
            content_revision=record.content_revision,
            turn_version=record.source_revision,
            authorization_revision_set_digest=(
                record.authorization_revision_set_digest
            ),
            expires_at=record.expires_at,
        )
    except (KeyError, TypeError, ValueError):
        raise MemoryAdapterUnavailableError() from None


def _context_candidate_payload(pair: ContextCandidatePair) -> dict[str, object]:
    return {
        "turn_id": str(pair.turn_id),
        "turn_sequence": pair.turn_sequence,
        "status": pair.status.value,
        "user": _context_reference_payload(pair.user),
        "assistant": _context_reference_payload(pair.assistant),
        "dependency_count": pair.dependency_count,
    }


def _context_candidate_from_payload(payload: Any) -> ContextCandidatePair:
    if not isinstance(payload, dict):
        raise ValueError
    dependency_count = int(payload["dependency_count"])
    if dependency_count < 0:
        raise ValueError
    return ContextCandidatePair(
        turn_id=_context_uuid(payload["turn_id"]),
        turn_sequence=int(payload["turn_sequence"]),
        status=TurnStatus(payload["status"]),
        user=_context_reference_from_payload(payload["user"]),
        assistant=_context_reference_from_payload(payload["assistant"]),
        dependency_count=dependency_count,
    )


def _context_reference_payload(
    reference: ContextEntryReference | None,
) -> dict[str, object] | None:
    if reference is None:
        return None
    return {
        "entry_id": str(reference.entry_id),
        "turn_id": str(reference.turn_id),
        "sequence": reference.sequence,
        "entry_type": reference.entry_type.value,
        "content_revision": reference.content_revision,
        "content_digest": reference.content_digest,
        "dependency_proof_version": reference.dependency_proof_version,
    }


def _context_reference_from_payload(
    payload: Any,
) -> ContextEntryReference | None:
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise ValueError
    return ContextEntryReference(
        entry_id=_context_uuid(payload["entry_id"]),
        turn_id=_context_uuid(payload["turn_id"]),
        sequence=int(payload["sequence"]),
        entry_type=EntryType(payload["entry_type"]),
        content_revision=int(payload["content_revision"]),
        content_digest=str(payload["content_digest"]),
        dependency_proof_version=payload.get("dependency_proof_version"),
    )


def _context_lease_record(
    lease: MemoryContextLease,
) -> MemoryContextLeaseRecord:
    return MemoryContextLeaseRecord(
        id=lease.id,
        organization_id=lease.organization_id,
        session_id=lease.session_id,
        turn_id=lease.turn_id,
        plan_id=lease.plan_id,
        node_invocation_id=lease.node_invocation_id,
        audience_kind=AudienceKind.PUBLIC_CHATBOT.value,
        subject_type=None,
        subject_id=None,
        provider_capability_reference=lease.provider_capability_reference,
        provider_capability_revision=lease.provider_capability_revision,
        purpose="main_generation",
        state=lease.state.value,
        claim_generation=lease.claim_generation,
        provider_attempt_id=lease.provider_attempt_id,
        claim_deadline_at=lease.claim_deadline_at,
        expires_at=lease.expires_at,
        invalidated_at=None,
    )


def _context_lease_domain(
    record: MemoryContextLeaseRecord,
) -> MemoryContextLease:
    if record.turn_id is None:
        raise MemoryAdapterUnavailableError()
    try:
        return MemoryContextLease(
            id=record.id,
            organization_id=record.organization_id,
            session_id=record.session_id,
            turn_id=record.turn_id,
            plan_id=record.plan_id,
            node_invocation_id=record.node_invocation_id,
            provider_capability_reference=record.provider_capability_reference,
            provider_capability_revision=record.provider_capability_revision,
            provider_attempt_id=record.provider_attempt_id,
            state=ContextLeaseState(record.state),
            claim_generation=record.claim_generation,
            claim_deadline_at=record.claim_deadline_at,
            expires_at=record.expires_at,
        )
    except (TypeError, ValueError):
        raise MemoryAdapterUnavailableError() from None


def _context_attempt_record(
    attempt: MemoryContextProviderAttempt,
) -> MemoryContextProviderAttemptRecord:
    return MemoryContextProviderAttemptRecord(
        id=attempt.id,
        organization_id=attempt.organization_id,
        session_id=attempt.session_id,
        turn_id=attempt.turn_id,
        lease_id=attempt.lease_id,
        plan_id=attempt.plan_id,
        node_invocation_id=attempt.node_invocation_id,
        provider_capability_reference=attempt.provider_capability_reference,
        provider_capability_revision=attempt.provider_capability_revision,
        purpose="main_generation",
        status=attempt.status.value,
        version=attempt.version,
        claim_generation=attempt.claim_generation,
        claim_deadline_at=attempt.claim_deadline_at,
        provider_started_at=attempt.provider_started_at,
        provider_correlation_reference=None,
        usage_reference=attempt.usage_reference,
        safe_failure_reason=attempt.safe_failure_reason,
        terminal_at=attempt.terminal_at,
    )


def _context_attempt_domain(
    record: MemoryContextProviderAttemptRecord,
) -> MemoryContextProviderAttempt:
    if record.turn_id is None:
        raise MemoryAdapterUnavailableError()
    try:
        return MemoryContextProviderAttempt(
            id=record.id,
            organization_id=record.organization_id,
            session_id=record.session_id,
            turn_id=record.turn_id,
            lease_id=record.lease_id,
            plan_id=record.plan_id,
            node_invocation_id=record.node_invocation_id,
            provider_capability_reference=record.provider_capability_reference,
            provider_capability_revision=record.provider_capability_revision,
            status=ContextAttemptState(record.status),
            version=record.version,
            claim_generation=record.claim_generation,
            claim_deadline_at=record.claim_deadline_at,
            provider_started_at=record.provider_started_at,
            usage_reference=record.usage_reference,
            safe_failure_reason=record.safe_failure_reason,
            terminal_at=record.terminal_at,
        )
    except (TypeError, ValueError):
        raise MemoryAdapterUnavailableError() from None


def _context_uuid(value: object) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def _dispatch_record(job: MemoryTurnDispatchJob) -> MemoryTurnDispatchJobRecord:
    return MemoryTurnDispatchJobRecord(
        id=job.id,
        organization_id=job.organization_id,
        session_id=job.session_id,
        turn_id=job.turn_id,
        memory_contract_version=job.memory_contract_version,
        storage_generation=job.storage_generation,
        minimum_worker_capability=job.minimum_worker_capability,
        status=job.status.value,
        claim_generation=job.claim_generation,
        claim_owner=job.claim_owner,
        claim_deadline_at=job.claim_deadline_at,
        attempt_count=job.attempt_count,
        max_attempts=job.max_attempts,
        next_attempt_at=job.next_attempt_at,
        broker_message_id=job.broker_message_id,
        workflow_admission_reference=job.workflow_admission_reference,
        safe_failure_reason=job.safe_failure_reason,
        published_at=job.published_at,
        acknowledged_at=job.acknowledged_at,
        terminal_at=job.terminal_at,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def _dispatch_domain(record: MemoryTurnDispatchJobRecord) -> MemoryTurnDispatchJob:
    return MemoryTurnDispatchJob(
        id=record.id,
        organization_id=record.organization_id,
        session_id=record.session_id,
        turn_id=record.turn_id,
        memory_contract_version=record.memory_contract_version,
        storage_generation=record.storage_generation,
        minimum_worker_capability=record.minimum_worker_capability,
        status=DispatchStatus(record.status),
        claim_generation=record.claim_generation,
        claim_owner=record.claim_owner,
        claim_deadline_at=record.claim_deadline_at,
        attempt_count=record.attempt_count,
        max_attempts=record.max_attempts,
        next_attempt_at=record.next_attempt_at,
        broker_message_id=record.broker_message_id,
        workflow_admission_reference=record.workflow_admission_reference,
        safe_failure_reason=record.safe_failure_reason,
        created_at=record.created_at,
        updated_at=record.updated_at,
        published_at=record.published_at,
        acknowledged_at=record.acknowledged_at,
        terminal_at=record.terminal_at,
    )


def _purge_record(job: ConversationPurgeJob) -> ConversationPurgeJobRecord:
    return ConversationPurgeJobRecord(
        id=job.id,
        organization_id=job.organization_id,
        session_id=job.session_id,
        session_reference_digest=job.session_reference_digest,
        app_id=job.app_id,
        deployment_id=job.deployment_id,
        deployment_version=job.deployment_version,
        audience_kind=(
            job.audience_kind.value if job.audience_kind is not None else None
        ),
        receipt_verifier_hash=job.receipt_verifier_hash,
        receipt_verifier_key_version=job.receipt_verifier_key_version,
        receipt_expires_at=job.receipt_expires_at,
        status=job.status.value,
        claim_generation=job.claim_generation,
        claim_owner=None,
        claim_deadline_at=None,
        attempt_count=job.attempt_count,
        max_attempts=job.max_attempts,
        next_attempt_at=None,
        safe_failure_reason=job.safe_failure_reason,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def _purge_domain(record: ConversationPurgeJobRecord) -> ConversationPurgeJob:
    return ConversationPurgeJob(
        id=record.id,
        organization_id=record.organization_id,
        session_id=record.session_id,
        session_reference_digest=record.session_reference_digest,
        app_id=record.app_id,
        deployment_id=record.deployment_id,
        deployment_version=record.deployment_version,
        audience_kind=(
            AudienceKind(record.audience_kind)
            if record.audience_kind is not None
            else None
        ),
        receipt_verifier_hash=record.receipt_verifier_hash,
        receipt_verifier_key_version=record.receipt_verifier_key_version,
        receipt_expires_at=record.receipt_expires_at,
        status=PurgeStatus(record.status),
        claim_generation=record.claim_generation,
        attempt_count=record.attempt_count,
        max_attempts=record.max_attempts,
        safe_failure_reason=record.safe_failure_reason,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _access_grant_record(
    grant: ConversationAccessGrant,
) -> ConversationAccessGrantRecord:
    return ConversationAccessGrantRecord(
        id=grant.id,
        organization_id=grant.organization_id,
        session_id=grant.session_id,
        deployment_id=grant.deployment_id,
        deployment_version=grant.deployment_version,
        audience_kind=grant.audience_kind.value,
        verifier_hash=grant.verifier_hash,
        verifier_key_version=grant.verifier_key_version,
        state=grant.state.value,
        replay_record_reference=grant.replay_record_reference,
        issued_at=grant.issued_at,
        expires_at=grant.expires_at,
        revoked_at=grant.revoked_at,
        created_at=grant.issued_at,
        updated_at=grant.issued_at,
    )


def _access_grant_domain(
    record: ConversationAccessGrantRecord,
) -> ConversationAccessGrant:
    return ConversationAccessGrant(
        id=record.id,
        organization_id=record.organization_id,
        session_id=record.session_id,
        deployment_id=record.deployment_id,
        deployment_version=record.deployment_version,
        audience_kind=AudienceKind(record.audience_kind),
        verifier_hash=record.verifier_hash,
        verifier_key_version=record.verifier_key_version,
        state=AccessGrantState(record.state),
        replay_record_reference=record.replay_record_reference,
        issued_at=record.issued_at,
        expires_at=record.expires_at,
        revoked_at=record.revoked_at,
    )


def _idempotency_values(record: ConversationIdempotency) -> dict[str, object]:
    return {
        "id": record.id,
        "organization_id": record.organization_id,
        "operation": record.operation,
        "scope_digest": record.scope_digest,
        "idempotency_key_hash": record.idempotency_key_hash,
        "request_fingerprint": record.request_fingerprint,
        "authorization_app_id": record.authorization_app_id,
        "authorization_verifier_key_version": record.authorization_verifier_key_version,
        "authorization_verifier_hash": record.authorization_verifier_hash,
        "status": record.status.value,
        "resource_type": record.resource_type,
        "resource_reference": record.resource_reference,
        "replay_record_reference": record.replay_record_reference,
        "secret_replay_expires_at": record.secret_replay_expires_at,
        "retention_expires_at": record.retention_expires_at,
        "safe_result_code": record.safe_result_code,
        **_idempotency_result_snapshot_values(record.result_snapshot),
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _idempotency_domain(
    record: ConversationIdempotencyRecord,
) -> ConversationIdempotency:
    return ConversationIdempotency(
        id=record.id,
        organization_id=record.organization_id,
        operation=record.operation,
        scope_digest=record.scope_digest,
        idempotency_key_hash=record.idempotency_key_hash,
        request_fingerprint=record.request_fingerprint,
        authorization_app_id=record.authorization_app_id,
        authorization_verifier_key_version=(record.authorization_verifier_key_version),
        authorization_verifier_hash=record.authorization_verifier_hash,
        status=IdempotencyStatus(record.status),
        resource_type=record.resource_type,
        resource_reference=record.resource_reference,
        replay_record_reference=record.replay_record_reference,
        secret_replay_expires_at=record.secret_replay_expires_at,
        retention_expires_at=record.retention_expires_at,
        safe_result_code=record.safe_result_code,
        result_snapshot=_idempotency_result_snapshot_domain(record),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _idempotency_mutable_values(
    record: ConversationIdempotency,
) -> dict[str, object]:
    return {
        "status": record.status.value,
        "resource_type": record.resource_type,
        "resource_reference": record.resource_reference,
        "replay_record_reference": record.replay_record_reference,
        "secret_replay_expires_at": record.secret_replay_expires_at,
        "retention_expires_at": record.retention_expires_at,
        "safe_result_code": record.safe_result_code,
        **_idempotency_result_snapshot_values(record.result_snapshot),
        "updated_at": record.updated_at,
    }


def _idempotency_result_snapshot_values(
    snapshot: IdempotencyResultSnapshot | None,
) -> dict[str, object]:
    return {
        "result_lifecycle": snapshot.lifecycle.value if snapshot is not None else None,
        "result_lifecycle_revision": (
            snapshot.lifecycle_revision if snapshot is not None else None
        ),
        "result_memory_contract_version": (
            snapshot.memory_contract_version if snapshot is not None else None
        ),
        "result_expires_at": snapshot.expires_at if snapshot is not None else None,
        "result_previous_lifecycle": (
            snapshot.previous_lifecycle.value
            if snapshot is not None and snapshot.previous_lifecycle is not None
            else None
        ),
        "result_previous_lifecycle_revision": (
            snapshot.previous_lifecycle_revision if snapshot is not None else None
        ),
    }


def _idempotency_result_snapshot_domain(
    record: ConversationIdempotencyRecord,
) -> IdempotencyResultSnapshot | None:
    values = (
        record.result_lifecycle,
        record.result_lifecycle_revision,
        record.result_memory_contract_version,
        record.result_expires_at,
        record.result_previous_lifecycle,
        record.result_previous_lifecycle_revision,
    )
    if all(value is None for value in values):
        return None
    if record.result_lifecycle is None or record.result_lifecycle_revision is None:
        raise MemoryAdapterUnavailableError()
    try:
        return IdempotencyResultSnapshot(
            lifecycle=SessionLifecycle(record.result_lifecycle),
            lifecycle_revision=record.result_lifecycle_revision,
            memory_contract_version=record.result_memory_contract_version,
            expires_at=record.result_expires_at,
            previous_lifecycle=(
                SessionLifecycle(record.result_previous_lifecycle)
                if record.result_previous_lifecycle is not None
                else None
            ),
            previous_lifecycle_revision=record.result_previous_lifecycle_revision,
        )
    except (TypeError, ValueError):
        raise MemoryAdapterUnavailableError() from None


def _secret_replay_record(
    replay: EncryptedSecretReplay,
) -> ConversationSecretReplayRecord:
    return ConversationSecretReplayRecord(
        id=replay.id,
        organization_id=replay.organization_id,
        idempotency_record_id=replay.idempotency_record_id,
        purpose=replay.purpose,
        ciphertext=replay.ciphertext,
        key_version=replay.key_version,
        associated_data_digest=replay.associated_data_digest,
        expires_at=replay.expires_at,
        created_at=replay.created_at,
    )


def _secret_replay_domain(
    record: ConversationSecretReplayRecord,
) -> EncryptedSecretReplay:
    return EncryptedSecretReplay(
        id=record.id,
        organization_id=record.organization_id,
        idempotency_record_id=record.idempotency_record_id,
        purpose=record.purpose,
        ciphertext=bytes(record.ciphertext),
        key_version=record.key_version,
        associated_data_digest=record.associated_data_digest,
        expires_at=record.expires_at,
        created_at=record.created_at,
    )


def _public_deployment_binding(
    app: App,
    workflow: Workflow,
    deployment: WorkflowDeployment,
    *,
    require_active: bool = True,
) -> PublicDeploymentBinding | None:
    if (
        app.organization_id is None
        or workflow.organization_id is None
        or app.organization_id != workflow.organization_id
        or workflow.app_id != app.id
        or deployment.app_id != app.id
        or deployment.type != DeploymentType.CHATBOT
        or deployment.version < 1
        or (
            require_active
            and (
                deployment.id != app.active_deployment_id
                or not deployment.is_active
            )
        )
    ):
        return None
    config = deployment.config if isinstance(deployment.config, dict) else {}
    mapping_version = _safe_config_version(
        config,
        "conversation_mapping_version",
        "mapping-v1",
    )
    memory_policy_version = _safe_config_version(
        config,
        "memory_policy_version",
        "memory-v1",
    )
    if mapping_version is None or memory_policy_version is None:
        return None
    runtime_contract = None
    try:
        runtime_contract = validate_conversation_memory_runtime(
            deployment.graph_snapshot,
            config,
        )
    except ConversationMemoryRuntimeContractError:
        # Lifecycle remains available while activation readiness stays
        # fail-closed.  Runtime use cases require the ready projection.
        runtime_contract = None
    if runtime_contract is not None:
        mapping_version = runtime_contract.mapping_version
        memory_policy_version = runtime_contract.memory_policy_version
    runtime_current = (
        deployment.id == app.active_deployment_id and deployment.is_active
    )
    return PublicDeploymentBinding(
        organization_id=workflow.organization_id,
        app_id=app.id,
        workflow_id=workflow.id,
        deployment_id=deployment.id,
        deployment_version=deployment.version,
        mapping_version=mapping_version,
        memory_policy_version=memory_policy_version,
        memory_contract_version="conversation-memory-v1",
        storage_generation=1,
        runtime_contract_ready=(
            runtime_contract is not None
            and runtime_current
        ),
        runtime_start_node_id=(
            runtime_contract.start_node_id if runtime_contract else None
        ),
        runtime_input_variable=(
            runtime_contract.input_variable if runtime_contract else None
        ),
        runtime_llm_node_id=(
            runtime_contract.llm_node_id if runtime_contract else None
        ),
        runtime_answer_node_id=(
            runtime_contract.answer_node_id if runtime_contract else None
        ),
        runtime_output_variable=(
            runtime_contract.output_variable if runtime_contract else None
        ),
        runtime_max_turns=(
            runtime_contract.memory.max_turns if runtime_contract else None
        ),
        runtime_max_context_tokens=(
            runtime_contract.memory.max_context_tokens if runtime_contract else None
        ),
    )


def _safe_config_version(
    config: dict[str, object],
    name: str,
    default: str,
) -> str | None:
    value = config.get(name, default)
    if not isinstance(value, str) or not value:
        return None
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}", value):
        return None
    return value
