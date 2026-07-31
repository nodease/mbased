"""
Audit System Celery 태스크

사용자 작업 감사(Audit) 로그를 DB(audit_logs)에 저장하는 Celery 태스크입니다.
신규 이벤트는 Audit Outbox worker가 처리합니다. `audit.record`는 rollout 4 이전에
broker에 들어간 메시지를 소진하기 위한 호환성 consumer입니다.
"""

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, NoReturn, Optional

from apps.shared.celery_app import celery_app
from apps.shared.db.models.audit_log import AuditLog
from apps.shared.db.models.security_alert import SecurityAlertReconciliationWatermark
from apps.shared.db.models.user import User  # noqa: F401
from apps.shared.db.session import SessionLocal
from apps.shared.services.audit_event_outbox import (
    AUDIT_EVENT_OUTBOX_TASK_NAME,
    AuditEventOutboxProcessor,
    validate_audit_workflow_correlation,
    workflow_correlation_from_payload,
)
from apps.shared.services.security_alert_aggregation import (
    aggregate_security_alert_detection,
)
from apps.shared.services.security_alert_notification_outbox import (
    SecurityAlertNotificationOutboxProcessor,
    detection_notification_idempotency_key,
    dispatch_security_alert_notification_outbox,
    enqueue_security_alert_notification,
)
from apps.shared.services.security_alert_reconciliation import (
    SQLAlchemySecurityAlertReconciliationRepository,
    reconcile_security_alert_batch,
)
from apps.shared.services.security_alert_rule_evaluator import (
    build_security_alert_cooldown_candidates,
    evaluate_security_alert_rules,
    is_security_alert_event_eligible,
)
from apps.shared.services.security_alert_rule_registry import (
    SECURITY_ALERT_MAX_WINDOW,
)
from sqlalchemy.exc import IntegrityError

logger = logging.getLogger(__name__)
_SECURITY_ALERT_DETECTION_TASK = "security_alert.detect"
_SECURITY_ALERT_RECONCILIATION_TASK = "security_alert.reconcile"
_SECURITY_ALERT_NOTIFICATION_OUTBOX_TASK = (
    "security_alert.notification_outbox.deliver"
)
_AUDIT_EVENT_OUTBOX_TASK = AUDIT_EVENT_OUTBOX_TASK_NAME
_SECURITY_ALERT_PROCESSOR = "security-alert-v1"
_SECURITY_ALERT_RECONCILIATION_BATCH_SIZE = 100


class SecurityAlertTaskRetryError(RuntimeError):
    pass


class AuditEventOutboxTaskRetryError(RuntimeError):
    pass


def _to_uuid(value) -> Optional[uuid.UUID]:
    try:
        if value is None or isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


def _to_datetime(value):
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return value


def _audit_id(data: Dict[str, Any], task_id: str | None) -> uuid.UUID:
    supplied = _to_uuid(data.get("id"))
    if supplied is not None:
        return supplied
    # 배포 중 이미 broker에 들어가 있던 legacy message도 retry마다 같은 ID를 쓴다.
    return uuid.uuid5(uuid.NAMESPACE_URL, f"nodease:audit-task:{task_id}")


def _result(*, status: str, data: Dict[str, Any], audit_id: uuid.UUID) -> Dict[str, str]:
    return {
        "status": status,
        "action": data.get("action"),
        "audit_id": str(audit_id),
    }


def _dispatch_security_alert_detection(audit_id: uuid.UUID) -> None:
    celery_app.send_task(_SECURITY_ALERT_DETECTION_TASK, args=[str(audit_id)])


def _dispatched_result(
    *,
    status: str,
    data: Dict[str, Any],
    audit_id: uuid.UUID,
) -> Dict[str, str]:
    _dispatch_security_alert_detection(audit_id)
    return _result(status=status, data=data, audit_id=audit_id)


def _retry_countdown(task: Any) -> int:
    return 2**task.request.retries


def _retry_security_alert_task(
    task: Any,
    error: Exception,
    *,
    operation: str,
) -> NoReturn:
    logger.error(
        "[Security Alert] task failed: operation=%s error_type=%s",
        operation,
        type(error).__name__,
    )
    raise task.retry(
        exc=SecurityAlertTaskRetryError("security alert task retry requested"),
        countdown=_retry_countdown(task),
    )


def _retry_audit_event_outbox_task(task: Any, error: Exception) -> NoReturn:
    logger.error(
        "[Audit] outbox processor failed: error_type=%s",
        type(error).__name__,
    )
    raise task.retry(
        exc=AuditEventOutboxTaskRetryError("audit outbox task retry requested"),
        countdown=_retry_countdown(task),
    )


@celery_app.task(name="audit.record", bind=True, max_retries=3)
def record_audit_log(self, data: Dict[str, Any]):
    """감사 로그 1건 저장."""
    session = SessionLocal()
    audit_id = _audit_id(data, getattr(self.request, "id", None))
    try:
        if session.get(AuditLog, audit_id) is not None:
            return _dispatched_result(
                status="duplicate",
                data=data,
                audit_id=audit_id,
            )

        audit = AuditLog(
            id=audit_id,
            occurred_at=_to_datetime(data.get("occurred_at")),
            actor_id=_to_uuid(data.get("actor_id")),
            actor_type=data["actor_type"],
            category=data["category"],
            action=data["action"],
            target_type=data.get("target_type"),
            target_id=data.get("target_id"),
            before=data.get("before"),
            after=data.get("after"),
            workflow_run_id=workflow_correlation_from_payload(
                data,
                "workflow_run_id",
            ),
            workflow_node_run_id=workflow_correlation_from_payload(
                data,
                "workflow_node_run_id",
            ),
            status=data.get("status", "success"),
            audit_metadata=data.get("audit_metadata") or {},
        )
        validate_audit_workflow_correlation(session, audit)
        session.add(audit)
        session.commit()
        return _dispatched_result(status="success", data=data, audit_id=audit_id)
    except IntegrityError as e:
        session.rollback()
        # 동시에 같은 message를 받은 경우 PK winner가 commit됐으면 성공으로 본다.
        if session.get(AuditLog, audit_id) is not None:
            return _dispatched_result(
                status="duplicate",
                data=data,
                audit_id=audit_id,
            )
        logger.error(
            "[Audit] record_audit_log integrity failure: error_type=%s",
            type(e).__name__,
        )
        raise self.retry(exc=e, countdown=_retry_countdown(self))
    except Exception as e:
        session.rollback()
        logger.error(
            "[Audit] record_audit_log failure: error_type=%s",
            type(e).__name__,
        )
        raise self.retry(exc=e, countdown=_retry_countdown(self))
    finally:
        session.close()


@celery_app.task(
    name=_SECURITY_ALERT_DETECTION_TASK,
    bind=True,
    max_retries=3,
)
def detect_security_alert(self, audit_id: str) -> Dict[str, Any]:
    session = SessionLocal()
    parsed_audit_id = _to_uuid(audit_id)
    changed_organization_ids: set[uuid.UUID] = set()
    try:
        candidate_count = _process_security_alert_audit(
            session,
            parsed_audit_id,
            changed_organization_ids=changed_organization_ids,
        )
        if candidate_count is None:
            return {"status": "missing", "audit_id": audit_id}

        session.commit()
        _dispatch_security_alert_updates(changed_organization_ids)
        return {
            "status": "processed",
            "audit_id": audit_id,
            "candidate_count": candidate_count,
        }
    except Exception as e:
        session.rollback()
        _retry_security_alert_task(self, e, operation="detect")
    finally:
        session.close()


def _process_security_alert_audit(
    db: Any,
    audit_id: uuid.UUID | None,
    *,
    changed_organization_ids: set[uuid.UUID] | None = None,
) -> int | None:
    context = _load_security_alert_detection_context(db, audit_id)
    if context is None:
        return None
    current_event, window_events, activation_started_at = context
    current_changed_organization_ids: set[uuid.UUID] = set()
    candidate_count = _evaluate_and_aggregate_security_alerts(
        db,
        current_event=current_event,
        window_events=window_events,
        activation_started_at=activation_started_at,
        changed_organization_ids=current_changed_organization_ids,
    )
    for organization_id in current_changed_organization_ids:
        enqueue_security_alert_notification(
            db,
            scoped_organization_id=organization_id,
            idempotency_key=detection_notification_idempotency_key(current_event.id),
        )
    if changed_organization_ids is not None:
        changed_organization_ids.update(current_changed_organization_ids)
    return candidate_count


def _evaluate_and_aggregate_security_alerts(
    db: Any,
    *,
    current_event: Any,
    window_events: list[Any],
    activation_started_at: datetime,
    changed_organization_ids: set[uuid.UUID] | None = None,
) -> int:
    threshold_candidates = evaluate_security_alert_rules(
        current_event=current_event,
        window_events=window_events,
        activation_started_at=activation_started_at,
    )
    cooldown_candidates = build_security_alert_cooldown_candidates(
        current_event=current_event,
        activation_started_at=activation_started_at,
    )
    candidates_by_key = {
        candidate.detection_key: candidate for candidate in cooldown_candidates
    }
    candidates_by_key.update(
        {candidate.detection_key: candidate for candidate in threshold_candidates}
    )
    for candidate in candidates_by_key.values():
        alert = aggregate_security_alert_detection(
            db,
            candidate=candidate,
            audit_logs=window_events,
            detected_at=current_event.occurred_at,
        )
        organization_id = getattr(alert, "organization_id", None)
        if organization_id is not None and changed_organization_ids is not None:
            changed_organization_ids.add(organization_id)
    return len(threshold_candidates)


def _dispatch_security_alert_updates(
    organization_ids: set[uuid.UUID],
) -> None:
    if not organization_ids:
        return
    try:
        dispatch_security_alert_notification_outbox()
    except Exception as error:
        logger.warning(
            "[Security Alert] notification outbox dispatch failed: error_type=%s",
            type(error).__name__,
        )


def _load_security_alert_detection_context(
    db: Any,
    audit_id: uuid.UUID | None,
) -> tuple[Any, list[Any], datetime] | None:
    current_event = db.get(AuditLog, audit_id)
    if current_event is None:
        return None
    activation_started_at = _security_alert_activation_started_at(
        db,
        current_event,
    )
    if not is_security_alert_event_eligible(
        event=current_event,
        activation_started_at=activation_started_at,
    ):
        return current_event, [], activation_started_at
    window_started_at = max(
        activation_started_at,
        current_event.occurred_at - SECURITY_ALERT_MAX_WINDOW,
    )
    organization_id = (current_event.audit_metadata or {}).get("organization_id")
    window_events = (
        db.query(AuditLog)
        .filter(
            AuditLog.actor_id == current_event.actor_id,
            AuditLog.audit_metadata["organization_id"].astext
            == str(organization_id),
            AuditLog.occurred_at >= window_started_at,
            AuditLog.occurred_at <= current_event.occurred_at,
        )
        .order_by(AuditLog.occurred_at, AuditLog.id)
        .all()
    )
    return current_event, window_events, activation_started_at


def _security_alert_activation_started_at(db: Any, current_event: Any) -> datetime:
    watermark = db.get(
        SecurityAlertReconciliationWatermark,
        _SECURITY_ALERT_PROCESSOR,
    )
    return (
        watermark.activation_started_at
        if watermark is not None
        else current_event.occurred_at
    )


@celery_app.task(
    name=_SECURITY_ALERT_RECONCILIATION_TASK,
    bind=True,
    max_retries=3,
)
def reconcile_security_alerts(self) -> Dict[str, Any]:
    session = SessionLocal()
    changed_organization_ids: set[uuid.UUID] = set()
    try:
        repository = SQLAlchemySecurityAlertReconciliationRepository(
            session,
            process_audit=lambda audit: _process_reconciliation_audit(
                session,
                audit,
                changed_organization_ids=changed_organization_ids,
            ),
        )
        result = reconcile_security_alert_batch(
            repository,
            processor_name=_SECURITY_ALERT_PROCESSOR,
            replay_horizon=SECURITY_ALERT_MAX_WINDOW,
            batch_size=_SECURITY_ALERT_RECONCILIATION_BATCH_SIZE,
        )
        _dispatch_security_alert_updates(changed_organization_ids)
        return {
            "status": "processed",
            "processed_count": result.processed_count,
        }
    except Exception as e:
        session.rollback()
        _retry_security_alert_task(self, e, operation="reconcile")
    finally:
        session.close()


@celery_app.task(
    name=_SECURITY_ALERT_NOTIFICATION_OUTBOX_TASK,
    bind=True,
    max_retries=3,
)
def deliver_security_alert_notification_outbox(
    self,
    limit: int = 100,
) -> Dict[str, int]:
    session = SessionLocal()
    try:
        result = SecurityAlertNotificationOutboxProcessor(session).process_due_events(
            owner_token=str(uuid.uuid4()),
            limit=limit,
        )
        return {
            "processed_count": result.processed_count,
            "recovered_count": result.recovered_count,
        }
    except Exception as error:
        session.rollback()
        _retry_security_alert_task(self, error, operation="notification_outbox")
    finally:
        session.close()


@celery_app.task(
    name=_AUDIT_EVENT_OUTBOX_TASK,
    bind=True,
    max_retries=3,
)
def process_audit_event_outbox(self, limit: int = 100) -> Dict[str, int]:
    session = SessionLocal()
    try:
        result = AuditEventOutboxProcessor(
            session,
            after_commit=_dispatch_security_alert_detection,
        ).process_due_events(
            owner_token=str(uuid.uuid4()),
            limit=limit,
        )
        return {
            "processed_count": result.processed_count,
            "recovered_count": result.recovered_count,
        }
    except Exception as error:
        session.rollback()
        _retry_audit_event_outbox_task(self, error)
    finally:
        session.close()


def _process_reconciliation_audit(
    db: Any,
    audit: AuditLog,
    *,
    changed_organization_ids: set[uuid.UUID] | None = None,
) -> None:
    _process_security_alert_audit(
        db,
        audit.id,
        changed_organization_ids=changed_organization_ids,
    )
