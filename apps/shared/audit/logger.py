"""감사 이벤트를 PostgreSQL Outbox에 저장한다."""

import logging
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

from apps.shared.db.models.audit_log import AuditEventOutbox
from apps.shared.db.session import SessionLocal
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _serialize(value: Any) -> Any:
    """Outbox JSONB 저장을 위해 UUID/datetime/Enum/dict/list를 재귀 변환한다."""
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    # 알 수 없는 타입은 문자열로 안전 변환
    return str(value)


def _store_outbox(
    payload: dict[str, Any],
    *,
    db_session: Session | None,
) -> bool:
    outbox = AuditEventOutbox(
        payload=payload,
        status="pending",
        attempt_count=0,
        max_attempts=5,
        retryable=True,
        idempotency_key=str(payload["id"]),
    )
    if db_session is not None:
        try:
            db_session.add(outbox)
            return True
        except Exception as exc:  # noqa: BLE001 - audit must not block caller
            logger.error(
                "[Audit] outbox enqueue failed: error_type=%s",
                type(exc).__name__,
            )
            return False

    owned_session: Session | None = None
    try:
        owned_session = SessionLocal()
        owned_session.add(outbox)
        owned_session.commit()
        return True
    except Exception as exc:  # noqa: BLE001 - audit must not block caller
        if owned_session is not None:
            try:
                owned_session.rollback()
            except Exception:  # noqa: BLE001 - never expose or replace root failure
                pass
        logger.error(
            "[Audit] outbox persistence failed: error_type=%s",
            type(exc).__name__,
        )
        return False
    finally:
        if owned_session is not None:
            try:
                owned_session.close()
            except Exception:  # noqa: BLE001 - audit cleanup must not block caller
                pass


def record_audit(
    action: str,
    category: str,
    *,
    actor_id: Optional[Any] = None,
    actor_type: str = "user",
    target_type: Optional[str] = None,
    target_id: Optional[Any] = None,
    before: Optional[Dict[str, Any]] = None,
    after: Optional[Dict[str, Any]] = None,
    status: str = "success",
    metadata: Optional[Dict[str, Any]] = None,
    workflow_run_id: Optional[Any] = None,
    workflow_node_run_id: Optional[Any] = None,
    db_session: Session | None = None,
) -> Optional[uuid.UUID]:
    """감사 ID를 먼저 고정하고 PostgreSQL Outbox에 저장한다.

    Caller session이 있으면 Outbox row만 추가하고 commit은 caller에게 맡긴다.
    Session이 없으면 독립된 짧은 transaction으로 Outbox를 먼저 확정한다.
    """
    audit_id = uuid.uuid4()
    try:
        audit_metadata = metadata or {}
        data = {
            "id": audit_id,
            "action": action,
            "category": category,
            "actor_id": actor_id,
            "actor_type": actor_type,
            "target_type": target_type,
            "target_id": str(target_id) if target_id is not None else None,
            "before": before,
            "after": after,
            "status": status,
            "audit_metadata": audit_metadata,
            "workflow_run_id": workflow_run_id
            if workflow_run_id is not None
            else audit_metadata.get("workflow_run_id"),
            "workflow_node_run_id": workflow_node_run_id
            if workflow_node_run_id is not None
            else audit_metadata.get("workflow_node_run_id"),
            "occurred_at": datetime.now(timezone.utc),
        }
        payload = _serialize(data)
    except Exception as exc:  # noqa: BLE001 - 감사 준비는 본 요청을 막지 않는다
        logger.error(
            "[Audit] payload preparation failed: action=%s error_type=%s",
            action,
            type(exc).__name__,
        )
        return None

    if not _store_outbox(payload, db_session=db_session):
        return None
    return audit_id
