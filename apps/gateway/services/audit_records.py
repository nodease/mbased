"""현재 DB 세션에 동기 audit row를 남기는 헬퍼.

비동기 발행(`apps.shared.audit.logger.record_audit`)과 달리, 권한 부여/회수처럼
본 작업과 같은 트랜잭션에서 audit이 함께 커밋되어야 하는 경로에서 사용한다
(ADR-0016). commit은 호출자가 수행한다.
"""

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy.orm import Session

from apps.shared.db.models.audit_log import AuditLog


def _json_safe(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def add_action_audit(
    db: Session,
    action: str,
    actor_id: Any,
    target_type: str,
    target_id: Any,
    *,
    organization_id: Any | None = None,
    metadata: dict[str, Any] | None = None,
    status: str = "success",
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    audit_metadata = _json_safe(dict(metadata or {}))
    if organization_id is not None:
        audit_metadata["organization_id"] = str(organization_id)

    db.add(
        AuditLog(
            action=action,
            category="action",
            actor_id=actor_id,
            actor_type="user",
            target_type=target_type,
            target_id=str(target_id),
            before=_json_safe(before),
            after=_json_safe(after),
            status=status,
            audit_metadata=audit_metadata,
        )
    )


def add_data_change_audit(
    db: Session,
    action: str,
    actor_id: Any,
    target_type: str,
    target_id: Any,
    *,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    organization_id: Any | None = None,
    metadata: dict[str, Any] | None = None,
    status: str = "success",
) -> None:
    audit_metadata = _json_safe(dict(metadata or {}))
    if organization_id is not None:
        audit_metadata["organization_id"] = str(organization_id)

    db.add(
        AuditLog(
            action=action,
            category="data_change",
            actor_id=actor_id,
            actor_type="user",
            target_type=target_type,
            target_id=str(target_id),
            before=_json_safe(before),
            after=_json_safe(after),
            status=status,
            audit_metadata=audit_metadata,
        )
    )
