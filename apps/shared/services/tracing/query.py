from datetime import datetime
from typing import Any, Optional

from apps.shared.db.models.app import App
from apps.shared.db.models.workflow import Workflow
from apps.shared.db.models.workflow_deployment import WorkflowDeployment
from apps.shared.db.models.workflow_run import (
    TracePayload,
    TraceVisibilityPolicy,
    WorkflowNodeRun,
    WorkflowRun,
)
from apps.shared.services.tracing.access import VIEW_RAW, TraceAccessService
from apps.shared.services.tracing.metadata import TraceMetadataSanitizer
from apps.shared.services.tracing.payload import TracePayloadService
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, selectinload


class TraceQueryService:
    """추적 라우터에서 사용하는 추적 조회 서비스."""

    @staticmethod
    def list_traces(
        db: Session,
        user: Any,
        status: Optional[str] = None,
        trigger_mode: Optional[str] = None,
        app_id: Optional[str] = None,
        workflow_id: Optional[str] = None,
        deployment_id: Optional[str] = None,
        user_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        from_datetime: Optional[datetime] = None,
        to_datetime: Optional[datetime] = None,
        page: int = 1,
        limit: int = 20,
    ) -> dict[str, Any]:
        query = db.query(WorkflowRun)
        if status:
            query = query.filter(WorkflowRun.status == status)
        if trigger_mode:
            query = query.filter(WorkflowRun.trigger_mode == trigger_mode)
        if app_id:
            query = query.filter(WorkflowRun.app_id == app_id)
        if workflow_id:
            query = query.filter(WorkflowRun.workflow_id == workflow_id)
        if deployment_id:
            query = query.filter(WorkflowRun.deployment_id == deployment_id)
        if user_id:
            query = query.filter(WorkflowRun.user_id == user_id)
        if correlation_id:
            query = query.filter(WorkflowRun.correlation_id == correlation_id)
        if from_datetime:
            query = query.filter(WorkflowRun.started_at >= from_datetime)
        if to_datetime:
            query = query.filter(WorkflowRun.started_at <= to_datetime)

        query = TraceQueryService._apply_trace_visibility_filter(db, query, user)
        safe_limit = max(1, min(limit, 100))
        offset = max(page - 1, 0) * safe_limit
        ordered_query = query.order_by(WorkflowRun.started_at.desc())
        visible_total = query.count()
        page_items = ordered_query.offset(offset).limit(safe_limit).all()
        return {
            "total": visible_total,
            "items": [TraceQueryService.trace_summary(run) for run in page_items],
            "has_more": visible_total > offset + len(page_items),
            "total_is_estimated": False,
            "scan_limit_reached": False,
        }

    @staticmethod
    def get_trace(
        db: Session,
        trace_id: str,
        user: Any,
        view_level: str = "metadata",
        include_spans: bool = False,
        include_payloads: bool = False,
    ) -> dict[str, Any]:
        run = (
            db.query(WorkflowRun)
            .options(selectinload(WorkflowRun.node_runs))
            .filter(WorkflowRun.id == trace_id)
            .first()
        )
        if not run:
            raise LookupError("trace_not_found")

        decision = TraceAccessService.check_trace_access(db, run, user, view_level)
        if view_level == VIEW_RAW:
            TraceAccessService.record_payload_access_event(
                db,
                workflow_run_id=run.id,
                actor_user_id=user.id,
                view_level=view_level,
                allowed=decision.allowed,
                reason_code=decision.reason_code,
                strict=decision.allowed,
            )
        if not decision.allowed:
            raise PermissionError(decision.reason_code)

        detail = TraceQueryService.trace_detail(run, view_level=view_level)
        TraceQueryService._apply_trace_io_view(db, run, detail, view_level)
        if include_spans:
            detail["spans"] = [
                TraceQueryService.span_detail(
                    span, view_level=view_level, db=db, run=run, user=user
                )
                for span in sorted(run.node_runs, key=lambda item: item.started_at)
            ]
        if include_payloads:
            detail["payloads"] = TraceQueryService.list_payloads(
                db, str(run.id), user, view_level=view_level
            )
        return detail

    @staticmethod
    def list_spans(db: Session, trace_id: str, user: Any) -> list[dict[str, Any]]:
        run = db.query(WorkflowRun).filter(WorkflowRun.id == trace_id).first()
        if not run:
            raise LookupError("trace_not_found")
        decision = TraceAccessService.check_trace_access(db, run, user)
        if not decision.allowed:
            raise PermissionError(decision.reason_code)
        spans = (
            db.query(WorkflowNodeRun)
            .filter(WorkflowNodeRun.workflow_run_id == run.id)
            .order_by(WorkflowNodeRun.sequence.asc(), WorkflowNodeRun.started_at.asc())
            .all()
        )
        return [TraceQueryService.span_detail(span) for span in spans]

    @staticmethod
    def list_payloads(
        db: Session,
        trace_id: str,
        user: Any,
        view_level: str = "redacted",
        payload_kind: Optional[str] = None,
        node_run_id: Optional[str] = None,
        history: bool = False,
        page: int = 1,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        run = db.query(WorkflowRun).filter(WorkflowRun.id == trace_id).first()
        if not run:
            raise LookupError("trace_not_found")
        initial_decision = TraceAccessService.check_trace_access(
            db, run, user, view_level, payload_kind=payload_kind
        )
        if payload_kind and not initial_decision.allowed:
            if view_level == VIEW_RAW:
                TraceAccessService.record_payload_access_event(
                    db,
                    workflow_run_id=run.id,
                    actor_user_id=user.id,
                    view_level=view_level,
                    allowed=False,
                    reason_code=initial_decision.reason_code,
                    strict=False,
                )
            raise PermissionError(initial_decision.reason_code)
        if view_level == VIEW_RAW and not initial_decision.allowed:
            TraceAccessService.record_payload_access_event(
                db,
                workflow_run_id=run.id,
                actor_user_id=user.id,
                view_level=view_level,
                allowed=False,
                reason_code=initial_decision.reason_code,
                strict=False,
            )
            raise PermissionError(initial_decision.reason_code)

        query = db.query(TracePayload).filter(TracePayload.workflow_run_id == run.id)
        if payload_kind:
            query = query.filter(TracePayload.payload_kind == payload_kind)
        if node_run_id:
            query = query.filter(TracePayload.workflow_node_run_id == node_run_id)
        safe_limit = max(1, min(limit, 1000))
        offset = max(page - 1, 0) * safe_limit
        if not history:
            # 추가 전용 페이로드 중 논리 페이로드별 최신 행만 DB 창 함수로 선택합니다.
            ranked_payloads = query.with_entities(
                TracePayload.id.label("payload_id"),
                func.row_number()
                .over(
                    partition_by=(
                        TracePayload.scope,
                        TracePayload.workflow_node_run_id,
                        TracePayload.payload_kind,
                    ),
                    order_by=(
                        TracePayload.created_at.desc(),
                        TracePayload.sequence.desc(),
                        TracePayload.attempt.desc(),
                    ),
                )
                .label("payload_rank"),
            ).subquery()
            payloads = (
                db.query(TracePayload)
                .join(ranked_payloads, TracePayload.id == ranked_payloads.c.payload_id)
                .filter(ranked_payloads.c.payload_rank == 1)
                .order_by(
                    TracePayload.created_at.desc(),
                    TracePayload.sequence.desc(),
                    TracePayload.attempt.desc(),
                )
                .offset(offset)
                .limit(safe_limit)
                .all()
            )
        else:
            payloads = (
                query.order_by(
                    TracePayload.created_at.asc(),
                    TracePayload.sequence.asc(),
                    TracePayload.attempt.asc(),
                )
                .offset(offset)
                .limit(safe_limit)
                .all()
            )

        allowed_payloads: list[TracePayload] = []
        first_denial = None
        for payload in payloads:
            decision = TraceAccessService.check_trace_access(
                db, run, user, view_level, payload_kind=payload.payload_kind
            )
            if view_level == VIEW_RAW:
                TraceAccessService.record_payload_access_event(
                    db,
                    workflow_run_id=run.id,
                    actor_user_id=user.id,
                    view_level=view_level,
                    allowed=decision.allowed,
                    reason_code=decision.reason_code,
                    payload_id=payload.id,
                    strict=decision.allowed,
                )
            if decision.allowed:
                allowed_payloads.append(payload)
            elif first_denial is None:
                first_denial = decision

        if not allowed_payloads and payloads and first_denial is not None:
            raise PermissionError(first_denial.reason_code)

        return [
            TraceQueryService.payload_detail(payload, view_level=view_level)
            for payload in allowed_payloads
        ]

    @staticmethod
    def _apply_trace_visibility_filter(db: Session, query, user: Any):
        if TraceAccessService.is_system_admin(db, user):
            return query
        if user is None or getattr(user, "id", None) is None:
            return query.filter(WorkflowRun.id.is_(None))

        allowed_app_ids = TraceQueryService._metadata_visible_owned_app_ids(
            db, user.id
        )
        if not allowed_app_ids:
            return query.filter(WorkflowRun.id.is_(None))
        workflow_app_id = (
            select(Workflow.app_id)
            .where(Workflow.id == WorkflowRun.workflow_id)
            .scalar_subquery()
        )
        deployment_app_id = (
            select(WorkflowDeployment.app_id)
            .where(WorkflowDeployment.id == WorkflowRun.deployment_id)
            .scalar_subquery()
        )
        effective_app_id = func.coalesce(
            WorkflowRun.app_id,
            workflow_app_id,
            deployment_app_id,
        )
        return query.filter(effective_app_id.in_(allowed_app_ids))

    @staticmethod
    def _metadata_visible_owned_app_ids(db: Session, user_id: Any) -> list[Any]:
        owned_apps = (
            db.query(App.id, App.organization_id)
            .filter(App.created_by == user_id)
            .all()
        )
        if not owned_apps:
            return []

        app_ids = [row[0] for row in owned_apps]
        organization_ids = list({row[1] for row in owned_apps if row[1] is not None})
        scope_conditions = [
            and_(
                TraceVisibilityPolicy.scope_type == "app",
                TraceVisibilityPolicy.scope_id.in_(app_ids),
            ),
            and_(
                TraceVisibilityPolicy.scope_type == "global",
                TraceVisibilityPolicy.scope_id.is_(None),
            ),
        ]
        if organization_ids:
            scope_conditions.append(
                and_(
                    TraceVisibilityPolicy.scope_type == "organization",
                    TraceVisibilityPolicy.scope_id.in_(organization_ids),
                )
            )
        policies = (
            db.query(TraceVisibilityPolicy)
            .filter(
                TraceVisibilityPolicy.is_active.is_(True),
                or_(*scope_conditions),
            )
            .order_by(TraceVisibilityPolicy.updated_at.desc())
            .all()
        )
        by_scope: dict[tuple[str, Any], Any] = {}
        for policy in policies:
            by_scope.setdefault((policy.scope_type, policy.scope_id), policy)

        global_policy = by_scope.get(("global", None))
        allowed_app_ids = []
        for app_id, organization_id in owned_apps:
            policy = (
                by_scope.get(("app", app_id))
                or by_scope.get(("organization", organization_id))
                or global_policy
            )
            if policy is None or (
                policy.owner_trace_access_enabled
                and not policy.deny_owner_trace_access
            ):
                allowed_app_ids.append(app_id)
        return allowed_app_ids

    @staticmethod
    def _latest_trace_payload(
        db: Session, run: WorkflowRun, payload_kind: str
    ) -> Optional[TracePayload]:
        return (
            db.query(TracePayload)
            .filter(
                TracePayload.workflow_run_id == run.id,
                TracePayload.scope == "trace",
                TracePayload.payload_kind == payload_kind,
            )
            .order_by(
                TracePayload.created_at.desc(),
                TracePayload.sequence.desc(),
                TracePayload.attempt.desc(),
            )
            .first()
        )

    @staticmethod
    def _apply_trace_io_view(
        db: Session, run: WorkflowRun, detail: dict[str, Any], view_level: str
    ) -> None:
        if view_level == "metadata":
            detail["inputs"] = None
            detail["outputs"] = None
            return

        input_payload = TraceQueryService._latest_trace_payload(db, run, "input")
        output_payload = TraceQueryService._latest_trace_payload(db, run, "output")
        if input_payload:
            detail["inputs"] = TracePayloadService.apply_view(input_payload, view_level)
        elif view_level == "raw":
            detail["inputs"] = None
        if output_payload:
            detail["outputs"] = TracePayloadService.apply_view(output_payload, view_level)
        elif view_level == "raw":
            detail["outputs"] = None

    @staticmethod
    def get_payload(
        db: Session,
        trace_id: str,
        payload_id: str,
        user: Any,
        view_level: str = "redacted",
    ) -> dict[str, Any]:
        payload = (
            db.query(TracePayload)
            .filter(
                TracePayload.workflow_run_id == trace_id,
                TracePayload.id == payload_id,
            )
            .first()
        )
        if not payload:
            raise LookupError("payload_not_found")
        run = db.query(WorkflowRun).filter(WorkflowRun.id == trace_id).first()
        if not run:
            raise LookupError("trace_not_found")

        decision = TraceAccessService.check_trace_access(
            db, run, user, view_level, payload_kind=payload.payload_kind
        )
        if view_level == VIEW_RAW:
            TraceAccessService.record_payload_access_event(
                db,
                workflow_run_id=run.id,
                actor_user_id=user.id,
                view_level=view_level,
                allowed=decision.allowed,
                reason_code=decision.reason_code,
                payload_id=payload.id,
                strict=decision.allowed,
            )
        if not decision.allowed:
            raise PermissionError(decision.reason_code)
        return TraceQueryService.payload_detail(payload, view_level=view_level)

    @staticmethod
    def _latest_payloads(payloads: list[TracePayload]) -> list[TracePayload]:
        latest: dict[tuple[Any, Any, str], TracePayload] = {}
        for payload in payloads:
            key = (
                payload.scope,
                payload.workflow_node_run_id,
                payload.payload_kind,
            )
            if key not in latest:
                latest[key] = payload
        return list(latest.values())

    @staticmethod
    def trace_summary(run: WorkflowRun) -> dict[str, Any]:
        return {
            "id": run.id,
            "workflow_id": run.workflow_id,
            "app_id": run.app_id,
            "user_id": run.user_id,
            "deployment_id": run.deployment_id,
            "status": run.status,
            "trigger_mode": run.trigger_mode,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "duration": run.duration,
            "total_tokens": run.total_tokens,
            "total_cost": float(run.total_cost or 0),
            "redaction_applied": run.redaction_applied,
            "pii_detected": run.pii_detected,
            "payload_storage_mode": run.payload_storage_mode,
        }

    @staticmethod
    def trace_detail(run: WorkflowRun, view_level: str = "metadata") -> dict[str, Any]:
        detail = TraceQueryService.trace_summary(run)
        detail.update(
            {
                "inputs": None if view_level == "metadata" else run.inputs,
                "outputs": None if view_level == "metadata" else run.outputs,
                # error_message는 호환 필드이며 metadata view에서는 노출하지 않습니다.
                "error_message": None if view_level == "metadata" else run.error_message,
                "trace_metadata": TraceMetadataSanitizer.sanitize_run_metadata(
                    run.trace_metadata or {}
                ),
                "spans": [],
                "payloads": [],
            }
        )
        return detail

    @staticmethod
    def span_detail(
        span: WorkflowNodeRun,
        view_level: str = "metadata",
        db: Optional[Session] = None,
        run: Optional[WorkflowRun] = None,
        user: Any = None,
    ) -> dict[str, Any]:
        include_io = view_level != "metadata"
        if include_io and span.node_type == "llmNode":
            include_io = TraceQueryService._llm_span_io_allowed(
                db, run, user, view_level
            )
        return {
            "id": span.id,
            "trace_id": span.workflow_run_id,
            "node_id": span.node_id,
            "node_type": span.node_type,
            "status": span.status,
            "started_at": span.started_at,
            "finished_at": span.finished_at,
            "duration": span.duration,
            "inputs": span.inputs if include_io else None,
            "outputs": span.outputs if include_io else None,
            # process_data는 노드 설정/중간값을 포함할 수 있어 메타데이터 조회에서는 숨깁니다.
            "process_data": None if view_level == "metadata" else span.process_data,
            "trace_metadata": TraceMetadataSanitizer.sanitize_span_metadata(
                span.node_type, span.trace_metadata or {}
            ),
            "redaction_applied": span.redaction_applied,
            "pii_detected": span.pii_detected,
            "sequence": span.sequence,
            "retry_count": span.retry_count,
        }

    @staticmethod
    def _llm_span_io_allowed(
        db: Optional[Session],
        run: Optional[WorkflowRun],
        user: Any,
        view_level: str,
    ) -> bool:
        if db is None or run is None or user is None:
            return False
        prompt_decision = TraceAccessService.check_trace_access(
            db, run, user, view_level, payload_kind="prompt"
        )
        completion_decision = TraceAccessService.check_trace_access(
            db, run, user, view_level, payload_kind="completion"
        )
        return prompt_decision.allowed and completion_decision.allowed

    @staticmethod
    def payload_detail(payload: TracePayload, view_level: str = "redacted") -> dict[str, Any]:
        return {
            "id": payload.id,
            "trace_id": payload.workflow_run_id,
            "span_id": payload.workflow_node_run_id,
            "scope": payload.scope,
            "payload_kind": payload.payload_kind,
            "sequence": payload.sequence,
            "attempt": payload.attempt,
            "view": view_level,
            "payload": TracePayloadService.apply_view(payload, view_level),
            "redaction_applied": payload.redaction_applied,
            "pii_detected": payload.pii_detected,
            "secret_detected": payload.secret_detected,
            "redaction_metadata": payload.redaction_metadata,
            "storage_mode": payload.storage_mode,
            "created_at": payload.created_at,
        }
