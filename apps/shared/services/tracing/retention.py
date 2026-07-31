from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from apps.shared.db.models.workflow_run import (
    RunStatus,
    TracePayload,
    WorkflowNodeRun,
    WorkflowRun,
)
from apps.shared.services.tracing.metadata import TraceMetadataSanitizer
from apps.shared.services.tracing.policy import (
    SCOPE_APP,
    SCOPE_GLOBAL,
    SCOPE_ORGANIZATION,
    TracePolicyService,
)
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

PURGED_MARKER = {"purged": True}
PROMPT_COMPLETION_KINDS = ("prompt", "completion")


class TraceRetentionService:
    """시스템 관리자 수동 실행용 보관 만료 정리 구현."""

    @staticmethod
    def expired_payload_condition(now, redacted_cutoff, prompt_cutoff):
        # 비삭제 정리가 끝난 행은 예행 실행/반복 정리 후보에서 제외합니다.
        return and_(
            TracePayload.retention_purged_at.is_(None),
            or_(
                TracePayload.retention_expires_at <= now,
                and_(
                    TracePayload.payload_kind.notin_(PROMPT_COMPLETION_KINDS),
                    TracePayload.created_at <= redacted_cutoff,
                ),
                and_(
                    TracePayload.payload_kind.in_(PROMPT_COMPLETION_KINDS),
                    TracePayload.created_at <= prompt_cutoff,
                ),
            ),
        )

    @staticmethod
    def purge(
        db: Session,
        scope_type: str = SCOPE_GLOBAL,
        scope_id: Optional[str] = None,
        dry_run: bool = True,
        limit: int = 1000,
    ) -> dict[str, Any]:
        if scope_type == SCOPE_ORGANIZATION:
            raise ValueError("organization_scope_purge_not_supported")

        retention = TracePolicyService.resolve_retention_policy(
            db, app_id=scope_id if scope_type == SCOPE_APP else None
        )
        now = datetime.now(timezone.utc)

        run_query = db.query(WorkflowRun)
        if scope_type == SCOPE_APP:
            run_query = run_query.filter(WorkflowRun.app_id == scope_id)
        elif scope_type != SCOPE_GLOBAL:
            raise ValueError("invalid_scope_type")

        raw_cutoff = now - timedelta(days=retention.raw_payload_retention_days)
        redacted_cutoff = now - timedelta(days=retention.redacted_payload_retention_days)
        prompt_cutoff = now - timedelta(days=retention.prompt_completion_retention_days)
        metadata_cutoff = now - timedelta(days=retention.metadata_retention_days)
        failed_cutoff = now - timedelta(days=retention.failed_trace_retention_days)

        payload_query = db.query(TracePayload)
        if scope_type == SCOPE_APP:
            payload_query = payload_query.join(WorkflowRun).filter(
                WorkflowRun.app_id == scope_id
            )

        # 원문 보관 기간 만료는 마스킹 사본을 유지하고 암호문만 제거합니다.
        raw_payloads = (
            payload_query.filter(
                TracePayload.raw_payload_encrypted.is_not(None),
                TracePayload.created_at <= raw_cutoff,
            )
            .limit(limit)
            .all()
        )
        expired_payloads = (
            payload_query.filter(
                TraceRetentionService.expired_payload_condition(
                    now, redacted_cutoff, prompt_cutoff
                )
            )
            .limit(limit)
            .all()
        )
        expired_runs = (
            run_query.filter(
                WorkflowRun.retention_purged_at.is_(None),
                or_(
                    (WorkflowRun.status == RunStatus.FAILED)
                    & (WorkflowRun.started_at <= failed_cutoff),
                    (WorkflowRun.status != RunStatus.FAILED)
                    & (WorkflowRun.started_at <= metadata_cutoff),
                )
            )
            .limit(limit)
            .all()
        )

        result = {
            "dry_run": dry_run,
            "scope_type": scope_type,
            "scope_id": scope_id,
            "raw_payloads": len(raw_payloads),
            "expired_payloads": len(expired_payloads),
            "expired_traces": len(expired_runs),
            "retention_action": retention.retention_action,
        }
        if dry_run:
            return result

        for payload in raw_payloads:
            payload.raw_payload_encrypted = None
            if payload.storage_mode == "raw_and_redacted":
                payload.storage_mode = "redacted_only"

        for payload in expired_payloads:
            if retention.retention_action == "delete":
                db.delete(payload)
            else:
                payload.redacted_payload = PURGED_MARKER
                payload.raw_payload_encrypted = None
                payload.storage_mode = "metadata_only"
                payload.retention_expires_at = None
                # 표시값을 남겨 요약/익명화 정리가 같은 페이로드를 반복 처리하지 않게 합니다.
                payload.retention_purged_at = now
                payload.redaction_metadata = {
                    **(payload.redaction_metadata or {}),
                    "retention": {
                        "purged_at": now.isoformat(),
                        "action": retention.retention_action,
                    },
                }

        for run in expired_runs:
            if retention.retention_action == "delete":
                db.delete(run)
                continue
            run.inputs = PURGED_MARKER
            run.outputs = PURGED_MARKER
            run.error_message = None
            trace_metadata = run.trace_metadata or {}
            trace_metadata["retention"] = TraceMetadataSanitizer.sanitize_retention_metadata(
                {
                    "purged_at": now.isoformat(),
                    "action": retention.retention_action,
                }
            )
            run.trace_metadata = TraceMetadataSanitizer.sanitize_run_metadata(
                trace_metadata
            )
            # 실행 단위 정리도 표시값을 남겨 같은 trace를 반복 집계하지 않습니다.
            run.retention_purged_at = now
            node_runs = (
                db.query(WorkflowNodeRun)
                .filter(WorkflowNodeRun.workflow_run_id == run.id)
                .all()
            )
            for node_run in node_runs:
                node_run.inputs = PURGED_MARKER
                node_run.outputs = PURGED_MARKER
                node_run.error_message = None

        db.commit()
        return result
