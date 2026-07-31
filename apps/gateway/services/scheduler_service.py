"""Durable distributed schedule dispatch composition and lifecycle facade."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from apps.gateway.application.deployment.schedule_dispatch import (
    ScheduleDispatchUseCase,
)
from apps.gateway.application.deployment.schedule_models import SchedulePublishResult
from apps.gateway.application.deployment.schedule_occurrence import (
    ScheduleOccurrenceUseCase,
)
from apps.gateway.application.deployment.schedule_ports import (
    BudgetDecisionPort,
    NextFireCalculatorPort,
    ScheduleDispatchAuditRecorderPort,
    ScheduleDispatchRepositoryPort,
    ScheduleDispatchUnitOfWork,
    ScheduleConfigurationPreflightPort,
    ScheduleTaskPublisherPort,
)
from apps.shared.db.models.schedule import Schedule
from apps.shared.domain.deployment_runtime_policy import DeploymentRuntimePolicy
from apps.shared.domain.schedule_dispatch import (
    REASON_BUDGET_EVALUATION_FAILED,
    REASON_ENQUEUE_ATTEMPTS_EXHAUSTED,
    REASON_EXECUTION_OUTCOME_UNKNOWN,
    ScheduleDispatchSettings,
)
from apps.shared.services.schedule_dispatch_observability import (
    emit_schedule_dispatch_signal,
)

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], Session]


@dataclass(frozen=True, slots=True)
class ScheduleDispatchDependencies:
    repository: ScheduleDispatchRepositoryPort
    audit: ScheduleDispatchAuditRecorderPort
    budget: BudgetDecisionPort
    configuration_preflight: ScheduleConfigurationPreflightPort
    uow: ScheduleDispatchUnitOfWork


ScheduleDependencyBuilder = Callable[[Session], ScheduleDispatchDependencies]


class SchedulerService:
    """Own one process-local tick; PostgreSQL claims own cross-replica correctness."""

    def __init__(
        self,
        *,
        runtime_policy: DeploymentRuntimePolicy,
        settings: ScheduleDispatchSettings,
        session_factory: SessionFactory,
        publisher: ScheduleTaskPublisherPort,
        dependency_builder: ScheduleDependencyBuilder,
        next_fire: NextFireCalculatorPort,
        start_background: bool = True,
        maintenance_enabled: bool = True,
    ) -> None:
        self.runtime_policy = runtime_policy
        self.settings = settings
        self.session_factory = session_factory
        self.publisher = publisher
        self.dependency_builder = dependency_builder
        self.maintenance_enabled = maintenance_enabled
        self.instance_id = str(uuid.uuid4())
        self.next_fire = next_fire
        self.occurrence_use_case = ScheduleOccurrenceUseCase(
            settings=settings,
            runtime_policy=runtime_policy,
            next_fire=self.next_fire,
        )
        self.dispatch_use_case = ScheduleDispatchUseCase(
            settings=settings,
            runtime_policy=runtime_policy,
        )
        self.scheduler: BackgroundScheduler | None = None
        if start_background and (
            settings.processes_existing_claims or maintenance_enabled
        ):
            scheduler = BackgroundScheduler(timezone="UTC")
            scheduler.add_job(
                self._tick,
                "interval",
                seconds=settings.poll_seconds,
                id="schedule-dispatch-tick",
                max_instances=1,
                coalesce=True,
                replace_existing=True,
                next_run_time=datetime.now(timezone.utc),
            )
            scheduler.start()
            self.scheduler = scheduler
        logger.info("Schedule dispatcher initialized: mode=%s", settings.mode)

    def load_schedules_from_db(self, db: Session) -> None:
        """Compatibility facade; durable reconciliation is owned by the tick."""
        del db

    def add_schedule(self, schedule: Schedule, db: Session) -> None:
        """Validate configuration and initialize its cursor without committing."""
        now = self.dependency_builder(db).repository.database_now()
        schedule.next_run_at = self.next_fire.first_after(
            cron_expression=schedule.cron_expression,
            timezone_name=schedule.timezone,
            now=now,
        )
        schedule.configuration_error_code = None

    def update_schedule(self, schedule: Schedule, db: Session) -> None:
        self.add_schedule(schedule, db)

    def remove_schedule(self, schedule_id: uuid.UUID) -> None:
        """No local job exists; lifecycle rows exclude the schedule from future ticks."""
        del schedule_id

    def run_tick_once(self) -> None:
        """Synchronous bounded tick for scheduler callback and deterministic tests."""
        self._tick()

    def _tick(self) -> None:
        if self.settings.processes_existing_claims:
            try:
                self._recover_critical()
                if self.settings.claims_new_occurrences:
                    self._reconcile_uninitialized()
                    self._claim_due_occurrences()
                batch = self._prepare_publish_batch()
                self._emit_dead_letter_signal(
                    batch.enqueue_attempts_exhausted,
                    REASON_ENQUEUE_ATTEMPTS_EXHAUSTED,
                )
                self._emit_dead_letter_signal(
                    batch.budget_evaluation_failed,
                    REASON_BUDGET_EVALUATION_FAILED,
                )
                for request in batch.requests:
                    emit_schedule_dispatch_signal(
                        logger,
                        "schedule_enqueue_attempt_total",
                        mode=self.settings.mode,
                    )
                    accepted = True
                    try:
                        self.publisher.publish(request)
                    except Exception:
                        accepted = False
                        logger.warning("Schedule dispatch publish failed")
                        emit_schedule_dispatch_signal(
                            logger,
                            "schedule_enqueue_failure_total",
                            mode=self.settings.mode,
                        )
                    try:
                        result = self._record_publish_result(
                            request,
                            accepted=accepted,
                        )
                        if result.dead_lettered_reason is not None:
                            self._emit_dead_letter_signal(
                                1,
                                result.dead_lettered_reason,
                            )
                    except Exception as exc:
                        # The dispatching lease remains the durable recovery source.
                        # One claim's result-write failure must not strand the rest
                        # of the already prepared batch.
                        logger.error(
                            "Schedule publish result write failed: error_type=%s",
                            type(exc).__name__,
                        )
            except Exception as exc:
                logger.error(
                    "Schedule dispatch tick failed: error_type=%s",
                    type(exc).__name__,
                )
        if not self.maintenance_enabled:
            return
        self._run_optional_maintenance(
            self._maintain_visibility,
            operation_name="visibility",
        )
        self._run_optional_maintenance(
            self._cleanup_terminal_claims,
            operation_name="cleanup",
        )
        self._run_optional_maintenance(
            self._observe_claim_ages,
            operation_name="claim_age",
        )

    def _reconcile_uninitialized(self) -> None:
        db = self.session_factory()
        try:
            dependencies = self.dependency_builder(db)
            self.occurrence_use_case.reconcile_uninitialized(
                repository=dependencies.repository,
                audit=dependencies.audit,
                uow=dependencies.uow,
            )
        finally:
            db.close()

    def _claim_due_occurrences(self) -> None:
        db = self.session_factory()
        try:
            dependencies = self.dependency_builder(db)
            created = self.occurrence_use_case.claim_due_occurrences(
                repository=dependencies.repository,
                budget=dependencies.budget,
                audit=dependencies.audit,
                uow=dependencies.uow,
            )
            if created:
                emit_schedule_dispatch_signal(
                    logger,
                    "schedule_claim_created_total",
                    value=created,
                    mode=self.settings.mode,
                )
        except IntegrityError:
            emit_schedule_dispatch_signal(
                logger,
                "schedule_claim_conflict_total",
                mode=self.settings.mode,
            )
            raise
        finally:
            db.close()

    def _prepare_publish_batch(self):
        db = self.session_factory()
        try:
            dependencies = self.dependency_builder(db)
            return self.dispatch_use_case.prepare_publish_batch(
                repository=dependencies.repository,
                budget=dependencies.budget,
                configuration_preflight=dependencies.configuration_preflight,
                audit=dependencies.audit,
                uow=dependencies.uow,
                owner=self.instance_id,
            )
        finally:
            db.close()

    def _record_publish_result(
        self,
        request,
        *,
        accepted: bool,
    ) -> SchedulePublishResult:
        db = self.session_factory()
        try:
            dependencies = self.dependency_builder(db)
            return self.dispatch_use_case.record_publish_result(
                repository=dependencies.repository,
                uow=dependencies.uow,
                request=request,
                accepted=accepted,
            )
        finally:
            db.close()

    def _recover_critical(self) -> None:
        db = self.session_factory()
        try:
            dependencies = self.dependency_builder(db)
            result = self.dispatch_use_case.recover_critical(
                repository=dependencies.repository,
                uow=dependencies.uow,
            )
            self._emit_dead_letter_signal(
                result.enqueue_attempts_exhausted,
                REASON_ENQUEUE_ATTEMPTS_EXHAUSTED,
            )
            self._emit_dead_letter_signal(
                result.execution_outcome_unknown,
                REASON_EXECUTION_OUTCOME_UNKNOWN,
            )
        finally:
            db.close()

    def _maintain_visibility(self) -> None:
        db = self.session_factory()
        try:
            dependencies = self.dependency_builder(db)
            missing = self.dispatch_use_case.maintain_visibility(
                repository=dependencies.repository,
                audit=dependencies.audit,
                uow=dependencies.uow,
            )
            if missing:
                emit_schedule_dispatch_signal(
                    logger,
                    "schedule_claim_workflow_run_missing_total",
                    value=missing,
                    reason="workflow_run_missing",
                    mode=self.settings.mode,
                )
        finally:
            db.close()

    def _cleanup_terminal_claims(self) -> None:
        db = self.session_factory()
        try:
            dependencies = self.dependency_builder(db)
            self.dispatch_use_case.cleanup_terminal_claims(
                repository=dependencies.repository,
                uow=dependencies.uow,
            )
        finally:
            db.close()

    def _observe_claim_ages(self) -> None:
        db = self.session_factory()
        try:
            dependencies = self.dependency_builder(db)
            now = dependencies.repository.database_now()
            pending_age, running_age = dependencies.repository.claim_age_seconds(
                now=now
            )
            emit_schedule_dispatch_signal(
                logger,
                "schedule_claim_pending_age_seconds",
                value=pending_age,
                status="pending",
                mode=self.settings.mode,
            )
            emit_schedule_dispatch_signal(
                logger,
                "schedule_claim_running_age_seconds",
                value=running_age,
                status="running",
                mode=self.settings.mode,
            )
        finally:
            db.close()

    @staticmethod
    def _run_optional_maintenance(
        callback: Callable[[], None], *, operation_name: str
    ) -> None:
        try:
            callback()
        except Exception as exc:
            logger.warning(
                "Schedule dispatch optional maintenance failed: "
                "operation=%s error_type=%s",
                operation_name,
                type(exc).__name__,
            )

    def _emit_dead_letter_signal(self, value: int, reason: str) -> None:
        if value <= 0:
            return
        emit_schedule_dispatch_signal(
            logger,
            "schedule_claim_dead_letter_total",
            value=value,
            status="dead_lettered",
            reason=reason,
            mode=self.settings.mode,
        )

    def shutdown(self) -> None:
        if self.scheduler is not None:
            self.scheduler.shutdown()
            self.scheduler = None


scheduler_service: Optional[SchedulerService] = None


def get_scheduler_service() -> SchedulerService:
    if scheduler_service is None:
        raise RuntimeError("SchedulerService is not initialized")
    return scheduler_service


def init_scheduler_service(
    db: Session,
    *,
    runtime_policy: DeploymentRuntimePolicy,
    settings: ScheduleDispatchSettings,
    session_factory: SessionFactory,
    publisher: ScheduleTaskPublisherPort,
    dependency_builder: ScheduleDependencyBuilder,
    next_fire: NextFireCalculatorPort,
    maintenance_enabled: bool = True,
) -> SchedulerService:
    global scheduler_service

    scheduler_service = SchedulerService(
        runtime_policy=runtime_policy,
        settings=settings,
        session_factory=session_factory,
        publisher=publisher,
        dependency_builder=dependency_builder,
        next_fire=next_fire,
        maintenance_enabled=maintenance_enabled,
    )
    scheduler_service.load_schedules_from_db(db)
    return scheduler_service
