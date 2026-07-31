"""
계층 B — 데이터 변경 이력 캡처 (SQLAlchemy ORM 이벤트)

추적 대상 모델의 INSERT/UPDATE/DELETE를 자동 감지해 before/after를 기록한다.
- before/after는 SQLAlchemy 네이티브 attribute history로 산출(컬럼별 old/new).
- 민감 필드는 값 대신 마스킹 토큰으로 저장한다.
- actor는 요청 컨텍스트(contextvar)에서 가져오며, 없으면 system으로 기록한다.

발행 자체가 본 트랜잭션을 막지 않도록, 캡처는 before_flush(변경값 확정 시점),
PK 보정은 after_flush, 발행은 after_commit에서 수행한다. rollback 시 후보를 버린다.
Nested transaction(savepoint)은 계층 B 감사 대상에서 보수적으로 제외한다.
"""

import logging

from apps.shared.audit.context import get_current_actor, get_current_metadata
from apps.shared.audit.logger import record_audit
from apps.shared.audit.manual_ownership import (
    clear_manual_audit_ownership,
    is_manually_audited,
)
from apps.shared.db.models.app import App
from apps.shared.db.models.connection import Connection
from apps.shared.db.models.knowledge import KnowledgeBase
from apps.shared.db.models.llm import LLMCredential
from apps.shared.db.models.mail_credential import MailCredential
from apps.shared.db.models.organization import Organization
from apps.shared.db.models.schedule import Schedule
from apps.shared.db.models.team import (
    Team,
    TeamAuditPermission,
    TeamKnowledgePermission,
    TeamLLMPermission,
    TeamMailCredentialPermission,
    TeamMembership,
    TeamWorkflowPermission,
    UserKnowledgePermission,
    UserLLMPermission,
    UserMailCredentialPermission,
    UserWorkflowPermission,
)
from apps.shared.db.models.user import User
from apps.shared.db.models.workflow import Workflow
from apps.shared.db.models.workflow_deployment import WorkflowDeployment
from apps.shared.db.models.workflow_run import (
    TraceRedactionPolicy,
    TraceRetentionPolicy,
    TraceVisibilityPolicy,
)
from sqlalchemy import inspect
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_MASK = "***changed***"

# 추적 대상 모델 → 감사 로그의 target_type
TRACKED_MODELS = {
    App: "app",
    Connection: "connection",
    LLMCredential: "credential",
    MailCredential: "mail_credential",
    KnowledgeBase: "knowledge",
    Organization: "organization",
    Schedule: "schedule",
    Team: "team",
    TeamMembership: "team_membership",
    TeamWorkflowPermission: "team_workflow_permission",
    UserKnowledgePermission: "user_knowledge_permission",
    UserLLMPermission: "user_llm_permission",
    UserMailCredentialPermission: "user_mail_credential_permission",
    UserWorkflowPermission: "user_workflow_permission",
    TeamKnowledgePermission: "team_knowledge_permission",
    TeamLLMPermission: "team_llm_permission",
    TeamMailCredentialPermission: "team_mail_credential_permission",
    TeamAuditPermission: "team_audit_permission",
    TraceRedactionPolicy: "trace_redaction_policy",
    TraceRetentionPolicy: "trace_retention_policy",
    TraceVisibilityPolicy: "trace_visibility_policy",
    User: "user",
    Workflow: "workflow",
    WorkflowDeployment: "workflow_deployment",
}

TRACKED_OPS = {
    Workflow: {"created", "deleted"},
}

# 모델별 민감 필드(평문 저장 금지)
SENSITIVE_FIELDS = {
    App: {
        "auth_secret",
        "auth_secret_verifier",
        "auth_secret_previous_verifier",
    },
    Connection: {
        "database",
        "encrypted_password",
        "encrypted_ssh_password",
        "encrypted_ssh_private_key",
        "host",
        "ssh_host",
        "ssh_username",
        "username",
    },
    LLMCredential: {"encrypted_config"},
    MailCredential: {
        "email_address",
        "encrypted_secret",
        "encryption_key_version",
    },
    KnowledgeBase: {"safe_metadata"},
    Organization: set(),
    Schedule: set(),
    Team: set(),
    TeamMembership: set(),
    TeamWorkflowPermission: set(),
    UserKnowledgePermission: set(),
    UserLLMPermission: set(),
    UserMailCredentialPermission: set(),
    UserWorkflowPermission: set(),
    TeamKnowledgePermission: set(),
    TeamLLMPermission: set(),
    TeamMailCredentialPermission: set(),
    TeamAuditPermission: set(),
    TraceRedactionPolicy: {"regex_rules"},
    TraceRetentionPolicy: set(),
    TraceVisibilityPolicy: set(),
    User: {"password"},
    Workflow: {"env_variables", "graph", "runtime_variables"},
    WorkflowDeployment: {"browser_access_policy", "config", "graph_snapshot"},
}

# Operational cursors are not user configuration changes and have dedicated
# schedule-dispatch observability. Cron/timezone/lifecycle fields remain audited.
IGNORED_UPDATE_FIELDS = {
    Schedule: {"last_run_at", "next_run_at"},
}


def _mask(model_cls, key, value):
    if key in SENSITIVE_FIELDS.get(model_cls, set()):
        return _MASK
    return value


def _all_columns(obj, model_cls):
    """모든 컬럼 값을 dict로 반환(민감 필드 마스킹)."""
    result = {}
    for attr in inspect(obj).mapper.column_attrs:
        key = attr.key
        result[key] = _mask(model_cls, key, getattr(obj, key, None))
    return result


def _changed_columns(obj, model_cls):
    """변경된 컬럼만 before/after dict로 반환(민감 필드 마스킹)."""
    before, after = {}, {}
    state = inspect(obj)
    for attr in state.mapper.column_attrs:
        key = attr.key
        if key in IGNORED_UPDATE_FIELDS.get(model_cls, set()):
            continue
        hist = state.attrs[key].history
        if not hist.has_changes():
            continue
        old = hist.deleted[0] if hist.deleted else None
        new = hist.added[0] if hist.added else None
        before[key] = _mask(model_cls, key, old)
        after[key] = _mask(model_cls, key, new)
    return before, after


def _model_of(obj):
    for model_cls in TRACKED_MODELS:
        if isinstance(obj, model_cls):
            return model_cls
    return None


def _should_track(model_cls, op):
    return op in TRACKED_OPS.get(model_cls, {"created", "updated", "deleted"})


def _discard_buffers(session):
    session.info.pop("_audit_pending", None)
    session.info.pop("_audit_ready", None)


def _disable_for_nested_transaction(session):
    # ponytail: savepoint별 버퍼는 실제 사용처가 생기면 추가한다.
    session.info["_audit_disabled_nested"] = True
    _discard_buffers(session)
    clear_manual_audit_ownership(session)
    logger.warning("[Audit] nested transaction 감지는 계층 B 감사를 건너뜁니다")


def _merge_ready(existing, event):
    if existing["op"] == "created":
        if event["op"] == "deleted":
            return None
        if event["after"]:
            existing["after"].update(event["after"])
        return existing

    if existing["op"] == "updated":
        if event["op"] == "updated":
            existing["after"].update(event["after"])
            return existing
        if event["op"] == "deleted":
            before = dict(event["before"] or {})
            before.update(existing["before"] or {})
            event["before"] = before
            return event

    return event


def _before_flush(session, flush_context, instances):
    """변경값을 캡처해 세션에 임시 보관한다."""
    if session.in_nested_transaction():
        _disable_for_nested_transaction(session)
        return

    try:
        pending = session.info.setdefault("_audit_pending", [])

        for obj in session.new:
            model_cls = _model_of(obj)
            if model_cls is None or not _should_track(model_cls, "created"):
                continue
            if is_manually_audited(session, obj, "created"):
                continue
            pending.append(
                (obj, model_cls, "created", None, _all_columns(obj, model_cls))
            )

        for obj in session.dirty:
            model_cls = _model_of(obj)
            if model_cls is None or not _should_track(model_cls, "updated"):
                continue
            if is_manually_audited(session, obj, "updated"):
                continue
            before, after = _changed_columns(obj, model_cls)
            if not before and not after:
                continue  # 컬럼 변경 없음(관계만 dirty)
            pending.append((obj, model_cls, "updated", before, after))

        for obj in session.deleted:
            model_cls = _model_of(obj)
            if model_cls is None or not _should_track(model_cls, "deleted"):
                continue
            if is_manually_audited(session, obj, "deleted"):
                continue
            pending.append(
                (obj, model_cls, "deleted", _all_columns(obj, model_cls), None)
            )
    except Exception as e:  # noqa: BLE001 - 감사 캡처가 flush를 막지 않는다
        logger.error(f"[Audit] before_flush 캡처 실패: {e}")


def _after_flush(session, flush_context):
    """PK 확정 후 commit 시 발행할 payload를 준비한다."""
    if session.info.get("_audit_disabled_nested"):
        _discard_buffers(session)
        return

    pending = session.info.pop("_audit_pending", None)
    if not pending:
        return

    actor = get_current_actor()
    if actor is not None:
        actor_id, actor_type, snapshot = (
            actor.actor_id,
            actor.actor_type,
            actor.snapshot,
        )
    else:
        actor_id, actor_type, snapshot = None, "system", {}

    ready = session.info.setdefault("_audit_ready", {})
    for obj, model_cls, op, before, after in pending:
        try:
            target_type = TRACKED_MODELS[model_cls]
            target_id = getattr(obj, "id", None)
            if op == "created" and after is not None and "id" in after:
                after["id"] = target_id
            metadata = get_current_metadata()
            if snapshot:
                metadata["actor"] = snapshot
            event = {
                "op": op,
                "action": f"{target_type}.{op}",
                "category": "data_change",
                "actor_id": actor_id,
                "actor_type": actor_type,
                "target_type": target_type,
                "target_id": target_id,
                "before": before,
                "after": after,
                "metadata": metadata,
            }
            key = (model_cls, id(obj))
            merged = _merge_ready(ready.get(key), event) if key in ready else event
            if merged is None:
                ready.pop(key, None)
            else:
                merged["action"] = f"{target_type}.{merged['op']}"
                ready[key] = merged
        except Exception as e:  # noqa: BLE001
            logger.error(f"[Audit] after_flush payload 준비 실패: {e}")


def _after_commit(session):
    """트랜잭션이 확정된 뒤 감사 이벤트를 발행한다."""
    if session.info.pop("_audit_disabled_nested", None):
        _discard_buffers(session)
        clear_manual_audit_ownership(session)
        return

    ready = session.info.pop("_audit_ready", None)
    if not ready:
        clear_manual_audit_ownership(session)
        return

    for event in ready.values():
        event.pop("op", None)
        try:
            record_audit(**event)
        except Exception as e:  # noqa: BLE001
            logger.error(f"[Audit] after_commit 발행 실패: {e}")
    clear_manual_audit_ownership(session)


def _after_rollback(session):
    """롤백된 변경의 감사 후보를 폐기한다."""
    _discard_buffers(session)
    session.info.pop("_audit_disabled_nested", None)
    clear_manual_audit_ownership(session)


def _after_soft_rollback(session, previous_transaction):
    """savepoint rollback도 보수적으로 계층 B 감사를 비활성화한다."""
    if getattr(previous_transaction, "nested", False):
        session.info["_audit_disabled_nested"] = True
    else:
        session.info.pop("_audit_disabled_nested", None)
    _discard_buffers(session)
    clear_manual_audit_ownership(session)


_registered = False


def register_audit_listeners():
    """ORM 이벤트 리스너를 1회 등록한다(앱/워커 부팅 시 호출)."""
    global _registered
    if _registered:
        return
    from sqlalchemy import event

    event.listen(Session, "before_flush", _before_flush)
    event.listen(Session, "after_flush", _after_flush)
    event.listen(Session, "after_commit", _after_commit)
    event.listen(Session, "after_rollback", _after_rollback)
    event.listen(Session, "after_soft_rollback", _after_soft_rollback)
    _registered = True
    logger.info("[Audit] ORM 변경 이력 리스너 등록 완료")
