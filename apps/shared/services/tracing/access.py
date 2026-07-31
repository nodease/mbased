import hashlib
import hmac
import os
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from apps.shared.db.models.app import App
from apps.shared.db.models.workflow import Workflow
from apps.shared.db.models.workflow_deployment import WorkflowDeployment
from apps.shared.db.models.workflow_run import (
    TracePayloadAccessEvent,
    WorkflowRun,
)
from apps.shared.db.session import SessionLocal
from apps.shared.services.tracing.observability import TraceObservabilityService
from apps.shared.services.tracing.policy import (
    ResolvedVisibilityPolicy,
    TracePolicyService,
)
from apps.shared.services.tracing.rbac import TraceRbacService
from sqlalchemy.orm import Session

VIEW_METADATA = "metadata"
VIEW_REDACTED = "redacted"
VIEW_RAW = "raw"
VALID_VIEW_LEVELS = {VIEW_METADATA, VIEW_REDACTED, VIEW_RAW}
PROMPT_COMPLETION_KINDS = {"prompt", "completion"}
TRACE_AUDIT_ACTOR_REF_SECRET_ENV = "TRACE_AUDIT_ACTOR_REF_SECRET"


def _same_uuid(left: Any, right: Any) -> bool:
    try:
        return uuid.UUID(str(left)) == uuid.UUID(str(right))
    except (TypeError, ValueError):
        return False


def _actor_user_ref(actor_user_id: Any) -> Optional[str]:
    try:
        value = str(uuid.UUID(str(actor_user_id)))
    except (TypeError, ValueError):
        return None
    secret = os.getenv(TRACE_AUDIT_ACTOR_REF_SECRET_ENV)
    if not secret:
        return None
    # 사용자 삭제 후에도 감사 이벤트를 상관분석할 수 있도록 HMAC 기반 단방향 참조만 남깁니다.
    return hmac.new(
        secret.encode("utf-8"),
        f"trace-actor:{value}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


@dataclass(frozen=True)
class TraceAccessDecision:
    allowed: bool
    reason_code: str
    app_id: Optional[uuid.UUID] = None
    is_system_admin: bool = False
    is_app_owner: bool = False
    rbac_auth_state: Optional[str] = None


@dataclass(frozen=True)
class TraceAccessContext:
    app_id: Optional[uuid.UUID]
    workflow_id: Optional[uuid.UUID]
    organization_id: Optional[uuid.UUID]
    visibility: ResolvedVisibilityPolicy
    is_system_admin: bool
    is_app_owner: bool
    rbac_auth_state: Optional[str] = None


class TraceAccessService:
    """추적 접근 정책과 RBAC 결과를 조합하는 접근 제어 경계."""

    @staticmethod
    def is_system_admin(db: Session, user: Any) -> bool:
        return TraceRbacService.is_system_admin(db, user)

    @staticmethod
    def resolve_trace_app_id(db: Session, run: WorkflowRun) -> Optional[uuid.UUID]:
        if run.app_id:
            return run.app_id

        workflow = db.query(Workflow).filter(Workflow.id == run.workflow_id).first()
        if workflow and workflow.app_id:
            return workflow.app_id

        if run.deployment_id:
            deployment = (
                db.query(WorkflowDeployment)
                .filter(WorkflowDeployment.id == run.deployment_id)
                .first()
            )
            if deployment and deployment.app_id:
                return deployment.app_id

        return None

    @staticmethod
    def is_app_owner(db: Session, app_id: Optional[uuid.UUID], user: Any) -> bool:
        if app_id is None or user is None:
            return False
        app = db.query(App).filter(App.id == app_id).first()
        return bool(app and _same_uuid(app.created_by, getattr(user, "id", None)))

    @staticmethod
    def resolve_trace_workflow_id(
        db: Session, run: WorkflowRun, app_id: Optional[uuid.UUID]
    ) -> Optional[uuid.UUID]:
        workflow_id = getattr(run, "workflow_id", None)
        if workflow_id:
            try:
                return uuid.UUID(str(workflow_id))
            except (TypeError, ValueError):
                return None

        if app_id is None or db is None:
            return None
        app = db.query(App).filter(App.id == app_id).first()
        if app and app.workflow_id:
            return app.workflow_id
        return None

    @staticmethod
    def resolve_trace_organization_id(
        db: Session,
        run: WorkflowRun,
        app_id: Optional[uuid.UUID],
        workflow_id: Optional[uuid.UUID],
    ) -> Optional[uuid.UUID]:
        if db is None:
            return None

        if app_id is not None:
            app = db.query(App).filter(App.id == app_id).first()
            if app and app.organization_id:
                return app.organization_id

        if workflow_id is not None:
            workflow = db.query(Workflow).filter(Workflow.id == workflow_id).first()
            if workflow and workflow.organization_id:
                return workflow.organization_id

        return None

    @staticmethod
    def check_trace_access(
        db: Session,
        run: WorkflowRun,
        user: Any,
        view_level: str = VIEW_METADATA,
        payload_kind: Optional[str] = None,
    ) -> TraceAccessDecision:
        if view_level not in VALID_VIEW_LEVELS:
            return TraceAccessDecision(False, "invalid_view_level")

        context = TraceAccessService._resolve_context(db, run, user)
        if isinstance(context, TraceAccessDecision):
            return context

        is_prompt_completion = payload_kind in PROMPT_COMPLETION_KINDS

        if context.is_system_admin:
            # 시스템 관리자와 앱 소유자가 동시에 참이면 시스템 관리자 정책을 우선 적용합니다.
            return TraceAccessService._admin_decision(
                context, view_level, is_prompt_completion
            )

        if context.is_app_owner:
            return TraceAccessService._owner_decision(
                context, view_level, is_prompt_completion
            )

        return TraceAccessService._rbac_decision(
            context, view_level, is_prompt_completion
        )

    @staticmethod
    def _resolve_context(
        db: Session, run: WorkflowRun, user: Any
    ) -> TraceAccessContext | TraceAccessDecision:
        try:
            app_id = TraceAccessService.resolve_trace_app_id(db, run)
        except Exception as error:
            TraceObservabilityService.record_trace_access_context_failed(
                "trace_app_context_unavailable", error=error
            )
            return TraceAccessDecision(False, "trace_app_context_unavailable")

        try:
            workflow_id = TraceAccessService.resolve_trace_workflow_id(db, run, app_id)
            organization_id = TraceAccessService.resolve_trace_organization_id(
                db, run, app_id, workflow_id
            )
        except Exception as error:
            TraceObservabilityService.record_trace_access_context_failed(
                "trace_resource_context_unavailable", app_id=app_id, error=error
            )
            return TraceAccessDecision(
                False, "trace_resource_context_unavailable", app_id
            )

        try:
            visibility = TracePolicyService.resolve_visibility_policy(
                db,
                app_id=app_id,
                organization_id=organization_id,
            )
        except Exception as error:
            TraceObservabilityService.record_trace_access_context_failed(
                "visibility_policy_unavailable", app_id=app_id, error=error
            )
            return TraceAccessDecision(
                False, "visibility_policy_unavailable", app_id
            )

        is_admin = TraceAccessService.is_system_admin(db, user)
        try:
            is_owner = TraceAccessService.is_app_owner(db, app_id, user)
        except Exception as error:
            TraceObservabilityService.record_trace_access_context_failed(
                "app_owner_context_unavailable", app_id=app_id, error=error
            )
            return TraceAccessDecision(
                False,
                "app_owner_context_unavailable",
                app_id,
                is_system_admin=is_admin,
            )

        try:
            rbac_auth_state = TraceRbacService.get_workflow_auth_state(
                db,
                user,
                workflow_id,
                organization_id=organization_id,
            )
        except Exception as error:
            # RBAC 조회 실패는 추가 권한을 열지 않는 방식으로 닫고, 기본 owner/admin 판단은 유지합니다.
            TraceObservabilityService.record_trace_access_context_failed(
                "rbac_context_unavailable", app_id=app_id, error=error
            )
            rbac_auth_state = None

        return TraceAccessContext(
            app_id=app_id,
            workflow_id=workflow_id,
            organization_id=organization_id,
            visibility=visibility,
            is_system_admin=is_admin,
            is_app_owner=is_owner,
            rbac_auth_state=rbac_auth_state,
        )

    @staticmethod
    def _admin_decision(
        context: TraceAccessContext,
        view_level: str,
        is_prompt_completion: bool,
    ) -> TraceAccessDecision:
        visibility = context.visibility
        if view_level in {VIEW_METADATA, VIEW_REDACTED}:
            return TraceAccessDecision(
                True,
                "system_admin",
                context.app_id,
                True,
                context.is_app_owner,
                context.rbac_auth_state,
            )
        if not visibility.admin_raw_payload_access_enabled:
            return TraceAccessDecision(
                False,
                "admin_raw_payload_access_disabled",
                context.app_id,
                True,
                context.is_app_owner,
                context.rbac_auth_state,
            )
        if is_prompt_completion and not visibility.admin_prompt_completion_access_enabled:
            return TraceAccessDecision(
                False,
                "admin_prompt_completion_access_disabled",
                context.app_id,
                True,
                context.is_app_owner,
                context.rbac_auth_state,
            )
        return TraceAccessDecision(
            True,
            "system_admin_raw",
            context.app_id,
            True,
            context.is_app_owner,
            context.rbac_auth_state,
        )

    @staticmethod
    def _owner_decision(
        context: TraceAccessContext,
        view_level: str,
        is_prompt_completion: bool,
    ) -> TraceAccessDecision:
        visibility = context.visibility
        if visibility.deny_owner_trace_access:
            return TraceAccessDecision(
                False,
                "owner_trace_access_denied",
                context.app_id,
                False,
                True,
                context.rbac_auth_state,
            )
        if view_level == VIEW_METADATA:
            return TraceAccessDecision(
                visibility.owner_trace_access_enabled,
                "app_owner"
                if visibility.owner_trace_access_enabled
                else "owner_metadata_disabled",
                context.app_id,
                False,
                True,
                context.rbac_auth_state,
            )
        if view_level == VIEW_REDACTED:
            if (
                is_prompt_completion
                and not visibility.owner_prompt_completion_access_enabled
            ):
                return TraceAccessDecision(
                    False,
                    "owner_prompt_completion_access_disabled",
                    context.app_id,
                    False,
                    True,
                    context.rbac_auth_state,
                )
            return TraceAccessDecision(
                visibility.owner_redacted_payload_access_enabled,
                "app_owner_redacted"
                if visibility.owner_redacted_payload_access_enabled
                else "owner_redacted_payload_access_disabled",
                context.app_id,
                False,
                True,
                context.rbac_auth_state,
            )
        if is_prompt_completion and not visibility.owner_prompt_completion_access_enabled:
            return TraceAccessDecision(
                False,
                "owner_prompt_completion_access_disabled",
                context.app_id,
                False,
                True,
                context.rbac_auth_state,
            )
        return TraceAccessDecision(
            visibility.owner_raw_payload_access_enabled,
            "app_owner_raw"
            if visibility.owner_raw_payload_access_enabled
            else "owner_raw_payload_access_disabled",
            context.app_id,
            False,
            True,
            context.rbac_auth_state,
        )

    @staticmethod
    def _rbac_decision(
        context: TraceAccessContext,
        view_level: str,
        is_prompt_completion: bool,
    ) -> TraceAccessDecision:
        visibility = context.visibility
        auth_state = context.rbac_auth_state

        if not auth_state:
            return TraceAccessDecision(
                False,
                "regular_user_trace_access_denied",
                context.app_id,
                rbac_auth_state=auth_state,
            )
        if visibility.deny_owner_trace_access:
            return TraceAccessDecision(
                False,
                "rbac_trace_access_denied",
                context.app_id,
                rbac_auth_state=auth_state,
            )
        if view_level == VIEW_METADATA:
            allowed = (
                visibility.owner_trace_access_enabled
                and TraceRbacService.auth_state_at_least(auth_state, "viewer")
            )
            return TraceAccessDecision(
                allowed,
                "rbac_metadata"
                if allowed
                else "rbac_metadata_access_disabled",
                context.app_id,
                rbac_auth_state=auth_state,
            )
        if view_level == VIEW_REDACTED:
            if (
                is_prompt_completion
                and not visibility.owner_prompt_completion_access_enabled
            ):
                return TraceAccessDecision(
                    False,
                    "rbac_prompt_completion_access_disabled",
                    context.app_id,
                    rbac_auth_state=auth_state,
                )
            allowed = (
                visibility.owner_redacted_payload_access_enabled
                and TraceRbacService.auth_state_at_least(auth_state, "builder")
            )
            return TraceAccessDecision(
                allowed,
                "rbac_redacted"
                if allowed
                else "rbac_redacted_payload_access_disabled",
                context.app_id,
                rbac_auth_state=auth_state,
            )
        if is_prompt_completion and not visibility.owner_prompt_completion_access_enabled:
            return TraceAccessDecision(
                False,
                "rbac_prompt_completion_access_disabled",
                context.app_id,
                rbac_auth_state=auth_state,
            )
        allowed = (
            visibility.owner_raw_payload_access_enabled
            and TraceRbacService.auth_state_at_least(auth_state, "manager")
        )
        return TraceAccessDecision(
            allowed,
            "rbac_raw" if allowed else "rbac_raw_payload_access_disabled",
            context.app_id,
            rbac_auth_state=auth_state,
        )

    @staticmethod
    def record_payload_access_event(
        _request_db: Session,
        workflow_run_id: Any,
        actor_user_id: Any,
        view_level: str,
        allowed: bool,
        reason_code: str,
        payload_id: Any = None,
        strict: bool = True,
    ) -> bool:
        """요청 세션 대신 감사 전용 세션으로 원문 조회 시도를 기록합니다."""
        if view_level != VIEW_RAW:
            return True
        # 원문 응답은 감사 기록과 분리될 수 없도록 독립 트랜잭션에 먼저 기록합니다.
        audit_db = SessionLocal()
        try:
            event = TracePayloadAccessEvent(
                payload_id=payload_id,
                workflow_run_id=workflow_run_id,
                actor_user_id=actor_user_id,
                actor_user_ref=_actor_user_ref(actor_user_id),
                view_level=view_level,
                allowed=allowed,
                reason_code=reason_code,
            )
            audit_db.add(event)
            audit_db.commit()
            return True
        except Exception as error:
            audit_db.rollback()
            TraceObservabilityService.record_raw_payload_audit_failed(
                workflow_run_id=workflow_run_id,
                payload_id=payload_id,
                allowed=allowed,
                reason_code=reason_code,
                error=error,
            )
            if strict:
                raise
            return False
        finally:
            audit_db.close()
