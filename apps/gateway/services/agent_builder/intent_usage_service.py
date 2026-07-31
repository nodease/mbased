"""Durable Agent Builder intent usage attribution."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from apps.gateway.application.agent_builder.intent_usage import (
    AGENT_BUILDER_INTENT_RUNTIME_SURFACE,
    AgentBuilderIntentUsageConflictError,
    AgentBuilderIntentUsageContext,
    AgentBuilderIntentUsageRecordingError,
    AgentBuilderIntentUsageReservation,
    AgentBuilderIntentUsageSample,
)
from apps.gateway.services.llm_service import LLMService
from apps.shared.db.models.agent_builder import AgentBuilderRequest, AgentBuilderSession
from apps.shared.db.models.app import App
from apps.shared.db.models.llm import (
    LLMCredential,
    LLMModel,
    LLMRelCredentialModel,
    LLMUsageLog,
)
from apps.shared.db.models.workflow import Workflow
from apps.shared.db.session import SessionLocal
from apps.shared.services.permissions import (
    has_active_organization_membership,
    has_llm_credential_permission,
)

_COST_QUANTUM = Decimal("0.000001")
_PENDING_STATUS = "pending"
_SUCCESS_STATUS = "success"


@dataclass(frozen=True, slots=True)
class _PreparedIntentUsage:
    reservation: AgentBuilderIntentUsageReservation
    sample: AgentBuilderIntentUsageSample
    total_cost: Decimal


class AgentBuilderIntentUsageService:
    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] = SessionLocal,
        max_record_attempts: int = 2,
    ) -> None:
        if max_record_attempts < 1:
            raise ValueError("max_record_attempts must be positive")
        self._session_factory = session_factory
        self._max_record_attempts = max_record_attempts

    def reserve(
        self,
        context: AgentBuilderIntentUsageContext,
        *,
        credential_id: uuid.UUID,
        model_id: uuid.UUID,
        model_api_id: str,
        attempt: int,
    ) -> AgentBuilderIntentUsageReservation:
        last_error: SQLAlchemyError | None = None
        for _ in range(self._max_record_attempts):
            try:
                return self._reserve_once(
                    context,
                    credential_id=credential_id,
                    model_id=model_id,
                    model_api_id=model_api_id,
                    attempt=attempt,
                )
            except AgentBuilderIntentUsageRecordingError:
                raise
            except SQLAlchemyError as exc:
                last_error = exc
        raise AgentBuilderIntentUsageRecordingError(
            "intent_usage_recording_failed"
        ) from last_error

    def _reserve_once(
        self,
        context: AgentBuilderIntentUsageContext,
        *,
        credential_id: uuid.UUID,
        model_id: uuid.UUID,
        model_api_id: str,
        attempt: int,
    ) -> AgentBuilderIntentUsageReservation:
        normalized_model_api_id = (
            model_api_id.strip() if isinstance(model_api_id, str) else ""
        )
        if (
            not normalized_model_api_id
            or isinstance(attempt, bool)
            or not isinstance(attempt, int)
            or attempt < 1
        ):
            raise AgentBuilderIntentUsageRecordingError(
                "intent_usage_recording_failed"
            )

        with self._session_factory() as db:
            with db.begin():
                self._validate_attribution(db, context, lock_primary=True)
                model = db.execute(
                    select(LLMModel)
                    .where(LLMModel.id == model_id)
                    .with_for_update(read=True)
                ).scalar_one_or_none()
                credential = db.execute(
                    select(LLMCredential)
                    .where(LLMCredential.id == credential_id)
                    .with_for_update(read=True)
                ).scalar_one_or_none()
                if (
                    model is None
                    or credential is None
                    or not credential.is_valid
                    or credential.organization_id != context.organization_id
                    or credential.provider_id != model.provider_id
                    or not model.is_active
                    or model.type != "chat"
                    or model.model_id_for_api_call != normalized_model_api_id
                ):
                    raise AgentBuilderIntentUsageRecordingError(
                        "intent_usage_recording_failed"
                    )
                verified_relation_id = db.execute(
                    select(LLMRelCredentialModel.id)
                    .where(
                        LLMRelCredentialModel.credential_id == credential.id,
                        LLMRelCredentialModel.model_id == model.id,
                        LLMRelCredentialModel.is_verified.is_(True),
                    )
                    .limit(1)
                    .with_for_update(read=True)
                ).scalar_one_or_none()
                if (
                    verified_relation_id is None
                    or not has_active_organization_membership(
                        db,
                        context.user_id,
                        context.organization_id,
                    )
                    or not has_llm_credential_permission(
                        db,
                        context.user_id,
                        credential.id,
                        "use",
                        organization_id=context.organization_id,
                    )
                ):
                    raise AgentBuilderIntentUsageRecordingError(
                        "intent_usage_recording_failed"
                    )
                input_price, output_price = self._prices_for_model(
                    model,
                    normalized_model_api_id,
                )
                reservation_id = uuid.uuid4()
                statement = (
                    insert(LLMUsageLog)
                    .values(
                        id=reservation_id,
                        user_id=context.user_id,
                        organization_id=context.organization_id,
                        credential_id=credential_id,
                        model_id=model_id,
                        workflow_id=context.workflow_id,
                        workflow_run_id=None,
                        cost_optimizer_candidate_id=None,
                        node_id=None,
                        runtime_surface=context.runtime_surface,
                        runtime_session_id=context.session_id,
                        runtime_request_id=context.request_id,
                        runtime_attempt=attempt,
                        prompt_tokens=0,
                        completion_tokens=0,
                        total_cost=Decimal("0"),
                        latency_ms=0,
                        status=_PENDING_STATUS,
                        error_message=None,
                    )
                    .on_conflict_do_nothing(
                        index_elements=(
                            LLMUsageLog.runtime_surface,
                            LLMUsageLog.runtime_session_id,
                            LLMUsageLog.runtime_request_id,
                            LLMUsageLog.runtime_attempt,
                        ),
                        index_where=(
                            LLMUsageLog.runtime_surface
                            == AGENT_BUILDER_INTENT_RUNTIME_SURFACE
                        ),
                    )
                    .returning(LLMUsageLog.id)
                )
                inserted_id = db.execute(statement).scalar_one_or_none()
                if inserted_id is None:
                    raise AgentBuilderIntentUsageConflictError(
                        "usage_attempt_already_reserved"
                    )
            return AgentBuilderIntentUsageReservation(
                id=reservation_id,
                context=context,
                credential_id=credential_id,
                model_id=model_id,
                model_api_id=normalized_model_api_id,
                attempt=attempt,
                input_price_1k=input_price,
                output_price_1k=output_price,
            )

    def record(
        self,
        reservation: AgentBuilderIntentUsageReservation,
        sample: AgentBuilderIntentUsageSample,
    ) -> uuid.UUID:
        if not self._sample_matches_reservation(reservation, sample):
            raise AgentBuilderIntentUsageConflictError("usage_attempt_conflict")
        prepared = _PreparedIntentUsage(
            reservation=reservation,
            sample=sample,
            total_cost=self._calculate_cost(reservation, sample),
        )
        last_error: SQLAlchemyError | None = None
        for _ in range(self._max_record_attempts):
            try:
                return self._record_once(prepared)
            except AgentBuilderIntentUsageRecordingError:
                raise
            except SQLAlchemyError as exc:
                last_error = exc
        raise AgentBuilderIntentUsageRecordingError(
            "intent_usage_recording_failed"
        ) from last_error

    def _record_once(self, prepared: _PreparedIntentUsage) -> uuid.UUID:
        reservation = prepared.reservation
        sample = prepared.sample
        with self._session_factory() as db:
            with db.begin():
                row = db.execute(
                    select(LLMUsageLog)
                    .where(LLMUsageLog.id == reservation.id)
                    .with_for_update()
                ).scalar_one_or_none()
                if row is None:
                    raise AgentBuilderIntentUsageRecordingError(
                        "intent_usage_recording_failed"
                    )
                if row.status == _SUCCESS_STATUS:
                    if self._matches_success(row, prepared):
                        return row.id
                    raise AgentBuilderIntentUsageConflictError(
                        "usage_attempt_conflict"
                    )
                if not self._matches_pending(row, reservation):
                    raise AgentBuilderIntentUsageConflictError(
                        "usage_attempt_conflict"
                    )
                row.prompt_tokens = sample.prompt_tokens
                row.completion_tokens = sample.completion_tokens
                row.total_cost = prepared.total_cost
                row.latency_ms = sample.latency_ms
                row.status = _SUCCESS_STATUS
                row.error_message = None
                row_id = row.id
            return row_id

    def cancel(self, reservation: AgentBuilderIntentUsageReservation) -> None:
        last_error: SQLAlchemyError | None = None
        for _ in range(self._max_record_attempts):
            try:
                self._cancel_once(reservation)
                return
            except AgentBuilderIntentUsageRecordingError:
                raise
            except SQLAlchemyError as exc:
                last_error = exc
        raise AgentBuilderIntentUsageRecordingError(
            "intent_usage_recording_failed"
        ) from last_error

    def _cancel_once(self, reservation: AgentBuilderIntentUsageReservation) -> None:
        with self._session_factory() as db:
            with db.begin():
                row = db.execute(
                    select(LLMUsageLog)
                    .where(LLMUsageLog.id == reservation.id)
                    .with_for_update()
                ).scalar_one_or_none()
                if row is None:
                    return
                if not self._matches_pending(row, reservation):
                    raise AgentBuilderIntentUsageConflictError(
                        "usage_attempt_conflict"
                    )
                db.delete(row)

    @staticmethod
    def _validate_attribution(
        db: Session,
        context: AgentBuilderIntentUsageContext,
        *,
        lock_primary: bool = False,
    ) -> None:
        request_statement = (
            select(AgentBuilderRequest.id)
            .join(
                AgentBuilderSession,
                AgentBuilderSession.id == AgentBuilderRequest.session_id,
            )
            .where(
                AgentBuilderRequest.id == context.request_id,
                AgentBuilderRequest.session_id == context.session_id,
                AgentBuilderRequest.user_id == context.user_id,
                AgentBuilderRequest.organization_id == context.organization_id,
                AgentBuilderRequest.status == "processing",
                AgentBuilderSession.user_id == context.user_id,
                AgentBuilderSession.organization_id == context.organization_id,
                AgentBuilderSession.workflow_id == context.workflow_id,
            )
        )
        if lock_primary:
            request_statement = request_statement.with_for_update(
                of=AgentBuilderRequest
            )
        request_exists = db.execute(request_statement).scalar_one_or_none()
        primary_statement = (
            select(Workflow.id)
            .join(App, App.id == Workflow.app_id)
            .where(
                Workflow.id == context.workflow_id,
                Workflow.organization_id == context.organization_id,
                App.organization_id == context.organization_id,
                App.workflow_id == Workflow.id,
            )
        )
        if lock_primary:
            primary_statement = primary_statement.with_for_update(of=App)
        primary_workflow = db.execute(primary_statement).scalar_one_or_none()
        if request_exists is None or primary_workflow is None:
            raise AgentBuilderIntentUsageRecordingError(
                "intent_usage_recording_failed"
            )

    @staticmethod
    def _prices_for_model(
        model: LLMModel,
        model_api_id: str,
    ) -> tuple[Decimal, Decimal]:
        input_price = model.input_price_1k
        output_price = model.output_price_1k
        if input_price is None or output_price is None:
            clean_id = model_api_id.replace("models/", "")
            pricing = LLMService.KNOWN_MODEL_PRICES.get(clean_id)
            if pricing is None:
                pricing = LLMService.KNOWN_MODEL_PRICES.get(
                    LLMService._normalize_model_id(model_api_id)
                )
            if pricing is None:
                return Decimal("0"), Decimal("0")
            input_price = pricing["input"]
            output_price = pricing["output"]
        input_decimal = Decimal(str(input_price))
        output_decimal = Decimal(str(output_price))
        if input_decimal < 0 or output_decimal < 0:
            raise AgentBuilderIntentUsageRecordingError(
                "intent_usage_recording_failed"
            )
        return input_decimal, output_decimal

    @staticmethod
    def _calculate_cost(
        reservation: AgentBuilderIntentUsageReservation,
        sample: AgentBuilderIntentUsageSample,
    ) -> Decimal:
        cost = (
            Decimal(sample.prompt_tokens) * reservation.input_price_1k
            + Decimal(sample.completion_tokens) * reservation.output_price_1k
        ) / Decimal(1000)
        return cost.quantize(_COST_QUANTUM, rounding=ROUND_HALF_UP)

    @staticmethod
    def _sample_matches_reservation(
        reservation: AgentBuilderIntentUsageReservation,
        sample: AgentBuilderIntentUsageSample,
    ) -> bool:
        return (
            sample.credential_id == reservation.credential_id
            and sample.model_id == reservation.model_id
            and sample.model_api_id == reservation.model_api_id
            and sample.attempt == reservation.attempt
        )

    @staticmethod
    def _matches_pending(
        row: LLMUsageLog,
        reservation: AgentBuilderIntentUsageReservation,
    ) -> bool:
        context = reservation.context
        stored_cost = Decimal(str(row.total_cost or 0)).quantize(_COST_QUANTUM)
        return (
            row.id == reservation.id
            and row.runtime_surface == context.runtime_surface
            and row.runtime_session_id == context.session_id
            and row.runtime_request_id == context.request_id
            and row.runtime_attempt == reservation.attempt
            and row.user_id == context.user_id
            and row.organization_id == context.organization_id
            and row.credential_id in {None, reservation.credential_id}
            and row.model_id in {None, reservation.model_id}
            and row.workflow_id == context.workflow_id
            and row.workflow_run_id is None
            and row.node_id is None
            and row.prompt_tokens == 0
            and row.completion_tokens == 0
            and stored_cost == Decimal("0").quantize(_COST_QUANTUM)
            and row.latency_ms == 0
            and row.status == _PENDING_STATUS
            and row.error_message is None
        )

    @staticmethod
    def _matches_success(
        row: LLMUsageLog,
        prepared: _PreparedIntentUsage,
    ) -> bool:
        reservation = prepared.reservation
        context = reservation.context
        sample = prepared.sample
        stored_cost = Decimal(str(row.total_cost or 0)).quantize(_COST_QUANTUM)
        return (
            row.id == reservation.id
            and row.runtime_surface == context.runtime_surface
            and row.runtime_session_id == context.session_id
            and row.runtime_request_id == context.request_id
            and row.runtime_attempt == reservation.attempt
            and row.user_id == context.user_id
            and row.organization_id == context.organization_id
            and row.credential_id in {None, reservation.credential_id}
            and row.model_id in {None, reservation.model_id}
            and row.workflow_id == context.workflow_id
            and row.workflow_run_id is None
            and row.node_id is None
            and row.prompt_tokens == sample.prompt_tokens
            and row.completion_tokens == sample.completion_tokens
            and stored_cost == prepared.total_cost
            and row.latency_ms == sample.latency_ms
            and row.status == _SUCCESS_STATUS
            and row.error_message is None
        )
