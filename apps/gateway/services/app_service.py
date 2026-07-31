import copy
import secrets
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from apps.gateway.services.admin_usage_service import (
    AdminUsageService,
    KST,
    UsageCostBreakdown,
)
from apps.gateway.services.deployment_parameter_optimization_service import (
    DeploymentParameterOptimizationService,
)
from apps.gateway.services.organization_context import ensure_user_default_organization
from apps.gateway.services.workflow_budget_service import WorkflowBudgetService
from apps.gateway.services.workflow_permission_lock import (
    lock_workflow_permission_scope,
)
from apps.shared.db.models.app import App
from apps.shared.db.models.team import TeamWorkflowPermission, UserWorkflowPermission
from apps.shared.db.models.user import User
from apps.shared.db.models.workflow import Workflow
from apps.shared.db.models.workflow_budget import WorkflowBudget
from apps.shared.db.models.workflow_deployment import WorkflowDeployment
from apps.shared.db.models.workflow_run import WorkflowRun
from apps.shared.permissions import (
    AUTH_STATE_MANAGER,
    normalize_resource_auth_state,
    stronger_resource_auth_state,
    workflow_auth_state_allows,
)
from apps.shared.schemas.app import (
    AppOperationAppSummary,
    AppOperationsCostSummary,
    AppOperationDeploymentSummary,
    AppOperationLatestRunSummary,
    AppOperationPermissionSummary,
    AppOperationRow,
    AppCreateRequest,
    AppUpdateRequest,
)
from apps.shared.services.permissions import (
    get_effective_workflow_auth_state,
    get_workflow_permission_sources_by_workflow_ids,
    has_organization_scope_access,
    has_organization_manager_permission,
    has_workflow_permission,
)
from apps.shared.services.provider_usage_cost_read_model import (
    provider_usage_aggregate_subquery,
    summarize_usage_records,
)


class AppService:
    @staticmethod
    def create_app(
        db: Session,
        request: AppCreateRequest,
        user_id: str,
        organization_id: str = None,
    ):
        """
        새로운 앱을 생성합니다.

        Args:
            db: 데이터베이스 세션
            request: 앱 생성 요청 데이터
            user_id: 생성자 ID (필수)
            organization_id: active organization ID. 없으면 legacy fallback 사용

        Returns:
            생성된 App 객체
        """
        if not organization_id:
            organization_id = ensure_user_default_organization(db, user_id)

        # Public endpoint slug만 생성한다. Secret은 lifecycle API에서 발급한다.
        url_slug = AppService._generate_url_slug(db, request.name)

        # 이름 중복 체크
        if (
            db.query(App)
            .filter(App.organization_id == organization_id, App.name == request.name)
            .first()
        ):
            raise ValueError("App with this name already exists.")

        # App 생성
        app = App(
            organization_id=organization_id,
            name=request.name,
            description=request.description,
            icon=request.icon.model_dump(),
            is_market=request.is_market,
            created_by=user_id,
            url_slug=url_slug,
            auth_secret=None,
        )
        db.add(app)
        db.flush()  # App ID 생성

        # 기본 워크플로우 생성
        workflow = Workflow(
            organization_id=organization_id,
            app_id=app.id,
            created_by=user_id,
        )
        db.add(workflow)
        db.flush()

        # App에 워크플로우 연결
        app.workflow_id = workflow.id
        AppService._grant_workflow_manager_permission(
            db, workflow, user_id, organization_id
        )

        db.commit()
        db.refresh(app)

        AppService._populate_owner_name(db, app)
        return app

    @staticmethod
    def _populate_owner_name(db: Session, app: App):
        """App 객체에 owner_name 속성을 채웁니다."""
        if app.created_by:
            user = db.query(User).filter(User.id == app.created_by).first()
            if user:
                # Pydantic 모델 변환 시 사용될 속성 할당
                setattr(app, "owner_name", user.name)

    @staticmethod
    def _grant_workflow_manager_permission(
        db: Session,
        workflow: Workflow,
        user_id,
        organization_id,
    ) -> None:
        if not organization_id:
            return
        db.add(
            UserWorkflowPermission(
                grantee_organization_id=organization_id,
                workflow_id=workflow.id,
                user_id=user_id,
                auth_state=AUTH_STATE_MANAGER,
                assigned_by=user_id,
            )
        )

    @staticmethod
    def _inherit_primary_workflow_permissions(
        db: Session,
        *,
        source_workflow_id,
        target_workflow: Workflow,
        actor_user_id,
        organization_id,
    ) -> None:
        """Copy the current App collaboration grants to a replacement Workflow."""
        if not organization_id:
            return
        if target_workflow.id is None:
            raise ValueError(
                "Target workflow must be flushed before permission inheritance"
            )
        if target_workflow.organization_id != organization_id:
            raise ValueError(
                "Target workflow organization must match permission organization"
            )
        if source_workflow_id == target_workflow.id:
            raise ValueError("Source and target workflow must be different")

        actor_permission = None
        if source_workflow_id is not None:
            lock_workflow_permission_scope(
                db,
                organization_id=organization_id,
                workflow_id=source_workflow_id,
            )
            user_permissions = (
                db.query(UserWorkflowPermission)
                .filter(
                    UserWorkflowPermission.grantee_organization_id == organization_id,
                    UserWorkflowPermission.workflow_id == source_workflow_id,
                )
                .all()
            )
            for permission in user_permissions:
                auth_state = normalize_resource_auth_state(permission.auth_state)
                if permission.user_id == actor_user_id:
                    auth_state = stronger_resource_auth_state(
                        auth_state, AUTH_STATE_MANAGER
                    )
                inherited = UserWorkflowPermission(
                    grantee_organization_id=organization_id,
                    workflow_id=target_workflow.id,
                    user_id=permission.user_id,
                    auth_state=auth_state,
                    assigned_by=actor_user_id,
                    options=copy.deepcopy(permission.options),
                    flags=permission.flags if permission.flags is not None else 0,
                )
                db.add(inherited)
                if permission.user_id == actor_user_id:
                    actor_permission = inherited

            team_permissions = (
                db.query(TeamWorkflowPermission)
                .filter(
                    TeamWorkflowPermission.grantee_organization_id == organization_id,
                    TeamWorkflowPermission.workflow_id == source_workflow_id,
                )
                .all()
            )
            for permission in team_permissions:
                db.add(
                    TeamWorkflowPermission(
                        grantee_organization_id=organization_id,
                        workflow_id=target_workflow.id,
                        team_id=permission.team_id,
                        auth_state=normalize_resource_auth_state(permission.auth_state),
                        assigned_by=actor_user_id,
                        options=copy.deepcopy(permission.options),
                        flags=permission.flags if permission.flags is not None else 0,
                    )
                )

        if actor_permission is None:
            AppService._grant_workflow_manager_permission(
                db,
                target_workflow,
                actor_user_id,
                organization_id,
            )

    @staticmethod
    def _populate_deployment_status(db: Session, app: App):
        """App 객체에 active_deployment_is_active 속성을 채웁니다."""
        from apps.shared.db.models.workflow_deployment import WorkflowDeployment

        if app.active_deployment_id:
            deployment = (
                db.query(WorkflowDeployment)
                .filter(
                    WorkflowDeployment.id == app.active_deployment_id,
                    WorkflowDeployment.app_id == app.id,
                )
                .first()
            )
            if deployment:
                setattr(app, "active_deployment_is_active", deployment.is_active)
            else:
                setattr(app, "active_deployment_is_active", None)
        else:
            setattr(app, "active_deployment_is_active", None)

    @staticmethod
    def can_read_app(db: Session, app: App, user_id) -> bool:
        if app.is_market:
            return True
        if app.organization_id and has_organization_manager_permission(
            db, user_id, app.organization_id
        ):
            return True
        if app.workflow_id and has_workflow_permission(
            db,
            user_id,
            app.workflow_id,
            "read",
            organization_id=app.organization_id,
        ):
            return True
        return app.organization_id is None and app.created_by == user_id

    @staticmethod
    def can_read_app_operations(db: Session, app: App, user_id) -> bool:
        """운영 현황 row는 최종 사용자 실행 권한보다 좁은 권한으로 제한한다."""
        if app.organization_id and has_organization_manager_permission(
            db, user_id, app.organization_id
        ):
            return True
        if app.workflow_id and has_workflow_permission(
            db,
            user_id,
            app.workflow_id,
            "write",
            organization_id=app.organization_id,
        ):
            return True
        return app.organization_id is None and app.created_by == user_id

    @staticmethod
    def can_manage_app(db: Session, app: App, user_id) -> bool:
        if app.organization_id and has_organization_manager_permission(
            db, user_id, app.organization_id
        ):
            return True
        if app.workflow_id and has_workflow_permission(
            db,
            user_id,
            app.workflow_id,
            "manage",
            organization_id=app.organization_id,
        ):
            return True
        return app.organization_id is None and app.created_by == user_id

    @staticmethod
    def can_deploy_app(db: Session, app: App, user_id) -> bool:
        if app.organization_id and has_organization_manager_permission(
            db, user_id, app.organization_id
        ):
            return True
        if app.workflow_id and has_workflow_permission(
            db,
            user_id,
            app.workflow_id,
            "deploy",
            organization_id=app.organization_id,
        ):
            return True
        return app.organization_id is None and app.created_by == user_id

    @staticmethod
    def can_access_app_scope(db: Session, app: App, user_id) -> bool:
        if app.organization_id:
            return has_organization_scope_access(db, user_id, app.organization_id)
        return app.created_by == user_id

    @staticmethod
    def access_denial_status(db: Session, app: App, user_id, action: str) -> int | None:
        if action == "read":
            if AppService.can_read_app(db, app, user_id):
                return None
        elif action == "manage":
            if AppService.can_manage_app(db, app, user_id):
                return None
            if AppService.can_read_app(db, app, user_id):
                return 403
        elif action == "deploy":
            if AppService.can_deploy_app(db, app, user_id):
                return None
            if AppService.can_read_app(db, app, user_id):
                return 403
        else:
            raise ValueError(f"Unsupported app permission action: {action}")

        if AppService.can_access_app_scope(db, app, user_id):
            return 403
        return 404

    @staticmethod
    def get_app(db: Session, app_id: str, user_id=None):
        """
        특정 앱을 조회합니다.

        Args:
            db: 데이터베이스 세션
            app_id: 앱 ID
            user_id: 요청 유저 ID (선택, 비공개 앱의 경우 소유자만 접근 가능)

        Returns:
            App 객체 또는 None
        """
        app = db.query(App).filter(App.id == app_id).first()

        if not app:
            return None

        if user_id and not AppService.can_read_app(db, app, user_id):
            return None

        AppService._populate_owner_name(db, app)
        AppService._populate_deployment_status(db, app)
        return app

    @staticmethod
    def get_user_apps(db: Session, user_id, organization_id: str = None):
        """
        특정 유저의 모든 앱을 조회합니다.

        Args:
            db: 데이터베이스 세션
            user_id: 유저 ID

        Returns:
            App 객체 리스트
        """
        query = db.query(App).options(joinedload(App.active_deployment))
        if organization_id:
            query = query.filter(App.organization_id == organization_id)

        apps = query.all()
        apps = [app for app in apps if AppService.can_read_app(db, app, user_id)]

        for app in apps:
            AppService._populate_owner_name(db, app)

        # 각 앱에 배포 상태 정보 추가
        for app in apps:
            AppService._populate_deployment_status(db, app)

        AppService._attach_budget_statuses(db, apps, organization_id)
        return apps

    @staticmethod
    def list_app_operations(
        db: Session,
        user_id,
        organization_id: str,
        q: str | None = None,
        permission: str | None = None,
        capability: str | None = None,
        deployment_state: str | None = None,
        run_state: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AppOperationRow]:
        """활성 organization 기준 `/apps/operations` 화면 요약을 반환한다.

        운영 목록에는 공개 URL slug와 인증 secret이 노출되면 안 되므로
        AppResponse 대신 목록 전용 안전 요약 응답 모델을 사용한다.
        """
        query = AppService._operation_apps_query(db, organization_id)
        if q and q.strip():
            escaped_query = AppService._escape_like_pattern(q.strip())
            pattern = f"%{escaped_query}%"
            query = query.filter(
                (App.name.ilike(pattern, escape="\\"))
                | (App.description.ilike(pattern, escape="\\"))
            )

        ordered_query = query.order_by(App.updated_at.desc(), App.name.asc(), App.id.asc())
        should_filter_computed_fields = bool(
            permission or capability or deployment_state or run_state
        )
        if should_filter_computed_fields:
            return AppService._list_filtered_operation_rows(
                db,
                ordered_query,
                user_id,
                permission,
                capability,
                deployment_state,
                run_state,
                limit,
                offset,
            )

        candidate_apps = AppService._list_readable_operation_apps(
            db, ordered_query, user_id, limit, offset
        )
        return AppService._build_operation_rows_for_apps(db, candidate_apps, user_id)

    @staticmethod
    def get_app_operations_cost_summary(
        db: Session,
        user_id,
        organization_id: str,
        *,
        now: datetime | None = None,
    ) -> AppOperationsCostSummary:
        """Return the all-pages cost total for readable active deployments."""
        query = AppService._operation_apps_query(db, organization_id).order_by(
            App.updated_at.desc(), App.name.asc(), App.id.asc()
        )
        active_workflow_ids: list[Any] = []
        for batch in AppService._operation_app_batches(query, limit=100):
            for app in batch:
                active_deployment = AppService._own_active_deployment(app)
                if (
                    app.workflow_id is None
                    or active_deployment is None
                ):
                    continue
                active_workflow_ids.append(app.workflow_id)

        workflow_ids = AppService._readable_operation_workflow_ids(
            db,
            user_id=user_id,
            organization_id=organization_id,
            workflow_ids=active_workflow_ids,
        )
        if not workflow_ids:
            return AppOperationsCostSummary()

        metrics = AppService._operation_metrics_by_workflow_id(
            db,
            workflow_ids,
            organization_id=organization_id,
            now=now or datetime.now(KST),
        )
        projected_total = Decimal("0")
        projected_workflow_execution = Decimal("0")
        projected_agent_builder = Decimal("0")
        unresolved_provider_call_count = 0
        for workflow_id in workflow_ids:
            metric = metrics.get(workflow_id) or {}
            projected_total += AdminUsageService.coalesce_cost(
                metric.get("projected_month_cost")
            )
            projected_workflow_execution += AdminUsageService.coalesce_cost(
                metric.get("projected_month_workflow_execution_cost")
            )
            projected_agent_builder += AdminUsageService.coalesce_cost(
                metric.get("projected_month_agent_builder_cost")
            )
            unresolved_provider_call_count += int(
                metric.get("unresolved_provider_call_count") or 0
            )

        return AppOperationsCostSummary(
            active_workflow_count=len(workflow_ids),
            projected_month_cost=float(projected_total),
            projected_month_workflow_execution_cost=float(
                projected_workflow_execution
            ),
            projected_month_agent_builder_cost=float(projected_agent_builder),
            usage_data_complete=unresolved_provider_call_count == 0,
            unresolved_provider_call_count=unresolved_provider_call_count,
        )

    @staticmethod
    def _operation_apps_query(db: Session, organization_id: str | None):
        """Return operation Apps with a valid primary workflow when one is set."""
        query = (
            db.query(App)
            .outerjoin(Workflow, Workflow.id == App.workflow_id)
            .options(joinedload(App.active_deployment))
        )
        if organization_id:
            query = query.filter(App.organization_id == organization_id)

        # A nullable primary pointer is a supported legacy state. A non-null
        # pointer must belong to this App and organization before its runs,
        # permissions, or costs can be used by the operations surface.
        return query.filter(
            or_(
                App.workflow_id.is_(None),
                (Workflow.app_id == App.id)
                & Workflow.organization_id.is_not_distinct_from(App.organization_id),
            )
        )

    @staticmethod
    def _readable_operation_workflow_ids(
        db: Session,
        *,
        user_id,
        organization_id: str,
        workflow_ids: list[Any],
    ) -> list[Any]:
        """Return write-capable workflows without per-App permission queries."""
        unique_workflow_ids = _unique_workflow_ids(workflow_ids)
        if not unique_workflow_ids:
            return []
        if has_organization_manager_permission(db, user_id, organization_id):
            return unique_workflow_ids

        sources_by_workflow_id = get_workflow_permission_sources_by_workflow_ids(
            db,
            user_id,
            unique_workflow_ids,
            organization_id,
        )
        return [
            workflow_id
            for workflow_id in unique_workflow_ids
            if any(
                workflow_auth_state_allows(source.auth_state, "write")
                for source in sources_by_workflow_id.get(workflow_id, [])
            )
        ]

    @staticmethod
    def _escape_like_pattern(value: str) -> str:
        """사용자 검색어를 SQL LIKE wildcard가 아닌 literal로 검색한다."""
        return (
            value.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )

    @staticmethod
    def _operation_app_batches(query, limit: int):
        batch_size = max(limit, 50)
        query_offset = 0

        while True:
            batch = query.offset(query_offset).limit(batch_size).all()
            if not batch:
                break
            yield batch
            if len(batch) < batch_size:
                break
            query_offset += batch_size

    @staticmethod
    def _list_readable_operation_apps(
        db: Session,
        query,
        user_id,
        limit: int,
        offset: int,
    ) -> list[App]:
        selected_apps: list[App] = []
        skipped = 0

        for batch in AppService._operation_app_batches(query, limit):
            for app in batch:
                if not AppService.can_read_app_operations(db, app, user_id):
                    continue
                if skipped < offset:
                    skipped += 1
                    continue
                selected_apps.append(app)
                if len(selected_apps) >= limit:
                    return selected_apps

        return selected_apps

    @staticmethod
    def _build_operation_rows_for_apps(
        db: Session,
        candidate_apps: list[App],
        user_id,
    ) -> list[AppOperationRow]:
        """지정된 app batch에 대해서만 운영 현황 summary를 계산한다."""

        owner_names = AppService._owner_names_by_id(
            db, [app.created_by for app in candidate_apps if app.created_by]
        )
        latest_runs = AppService._latest_runs_by_workflow_id(
            db, [app.workflow_id for app in candidate_apps if app.workflow_id]
        )
        deployment_history = AppService._deployment_history_by_app_id(
            db, [app.id for app in candidate_apps]
        )
        active_deployment_by_app_id = {
            app.id: deployment
            for app in candidate_apps
            if (deployment := AppService._own_active_deployment(app)) is not None
        }
        automatic_optimization_by_deployment_id = (
            DeploymentParameterOptimizationService.summaries_by_deployment_id(
                db,
                [deployment.id for deployment in active_deployment_by_app_id.values()],
            )
        )
        workflow_ids = [app.workflow_id for app in candidate_apps if app.workflow_id]
        AppService._attach_budget_statuses(
            db,
            candidate_apps,
            candidate_apps[0].organization_id if candidate_apps else None,
        )
        AppService._attach_operation_metrics(db, candidate_apps)
        permission_sources_by_workflow_id = (
            get_workflow_permission_sources_by_workflow_ids(
                db,
                user_id,
                workflow_ids,
                organization_id=candidate_apps[0].organization_id,
            )
            if candidate_apps and workflow_ids
            else {}
        )

        rows = [
            AppService._build_operation_row(
                app,
                user_id,
                owner_names,
                latest_runs,
                deployment_history,
                automatic_optimization_by_deployment_id,
                permission_sources_by_workflow_id,
                db,
            )
            for app in candidate_apps
        ]

        return rows

    @staticmethod
    def _list_filtered_operation_rows(
        db: Session,
        query,
        user_id,
        permission: str | None,
        capability: str | None,
        deployment_state: str | None,
        run_state: str | None,
        limit: int,
        offset: int,
    ) -> list[AppOperationRow]:
        """계산형 filter는 bounded batch로 훑어 필요한 page만 채운다."""
        matched_rows: list[AppOperationRow] = []
        skipped = 0

        for batch in AppService._operation_app_batches(query, limit):
            readable_apps = [
                app
                for app in batch
                if AppService.can_read_app_operations(db, app, user_id)
            ]
            rows = AppService._build_operation_rows_for_apps(db, readable_apps, user_id)
            for row in rows:
                if not AppService._matches_operation_filters(
                    row, permission, capability, deployment_state, run_state
                ):
                    continue
                if skipped < offset:
                    skipped += 1
                    continue
                matched_rows.append(row)
                if len(matched_rows) >= limit:
                    return matched_rows

        return matched_rows

    @staticmethod
    def _build_operation_row(
        app: App,
        user_id,
        owner_names: dict[Any, str | None],
        latest_runs: dict[Any, WorkflowRun],
        deployment_history: dict[Any, WorkflowDeployment],
        automatic_optimization_by_deployment_id: dict[Any, dict[str, Any]],
        permission_sources_by_workflow_id: dict[Any, list],
        db: Session,
    ) -> AppOperationRow:
        permission_summary = None
        permission_sources = []
        permission_status = "not_available"
        permission_error = None

        if app.workflow_id:
            # `/workflows/{workflow_id}/permissions/me`와 action 의미가 어긋나지
            # 않도록 표준 workflow RBAC helper를 그대로 사용한다.
            auth_state = get_effective_workflow_auth_state(
                db, user_id, app.workflow_id, organization_id=app.organization_id
            )
            permission_summary = AppOperationPermissionSummary(
                workflow_id=app.workflow_id,
                organization_id=app.organization_id,
                auth_state=auth_state,
                can_read=workflow_auth_state_allows(auth_state, "read"),
                can_write=workflow_auth_state_allows(auth_state, "write"),
                can_execute=workflow_auth_state_allows(auth_state, "execute"),
                can_deploy=workflow_auth_state_allows(auth_state, "deploy"),
                can_manage=workflow_auth_state_allows(auth_state, "manage"),
            )
            permission_sources = permission_sources_by_workflow_id.get(
                app.workflow_id, []
            )
            permission_status = "loaded"

        return AppOperationRow(
            app=AppOperationAppSummary(
                id=app.id,
                name=app.name,
                description=app.description,
                icon=app.icon,
                workflow_id=app.workflow_id,
                owner_name=owner_names.get(app.created_by),
                budget_status=getattr(app, "budget_status", None),
                operation_metrics=getattr(app, "operation_metrics", None),
                created_at=app.created_at,
                updated_at=app.updated_at,
            ),
            permission=permission_summary,
            permission_status=permission_status,
            permission_sources=permission_sources,
            permission_error=permission_error,
            deployment=AppService._operation_deployment_summary(
                app, deployment_history.get(app.id)
            ),
            latest_run=AppService._operation_latest_run_summary(
                latest_runs.get(app.workflow_id)
            ),
            automatic_optimization=automatic_optimization_by_deployment_id.get(
                active_deployment.id
                if (
                    active_deployment := AppService._own_active_deployment(app)
                ) is not None
                else None
            ),
        )

    @staticmethod
    def _owner_names_by_id(db: Session, user_ids: list[Any]) -> dict[Any, str | None]:
        if not user_ids:
            return {}
        users = db.query(User).filter(User.id.in_(set(user_ids))).all()
        return {user.id: user.name or user.email for user in users}

    @staticmethod
    def _latest_runs_by_workflow_id(
        db: Session, workflow_ids: list[Any]
    ) -> dict[Any, Any]:
        if not workflow_ids:
            return {}

        # workflow별 최신 run 1건만 SQL에서 고른다. 전체 실행 이력을 가져오면
        # 운영 목록 비용이 누적 실행 로그 크기에 비례해 커진다.
        latest_run_ids = (
            db.query(
                WorkflowRun.id.label("id"),
                func.row_number()
                .over(
                    partition_by=WorkflowRun.workflow_id,
                    order_by=[WorkflowRun.started_at.desc(), WorkflowRun.id.desc()],
                )
                .label("row_number"),
            )
            .filter(WorkflowRun.workflow_id.in_(set(workflow_ids)))
            .subquery()
        )
        run_rows = (
            db.query(
                WorkflowRun.workflow_id.label("workflow_id"),
                WorkflowRun.id.label("id"),
                WorkflowRun.status.label("status"),
                WorkflowRun.started_at.label("started_at"),
                WorkflowRun.finished_at.label("finished_at"),
                WorkflowRun.error_message.label("error_message"),
            )
            .join(latest_run_ids, WorkflowRun.id == latest_run_ids.c.id)
            .filter(latest_run_ids.c.row_number == 1)
            .all()
        )
        latest_by_workflow_id = {}
        for run in run_rows:
            run = SimpleNamespace(
                workflow_id=run.workflow_id,
                id=run.id,
                status=run.status,
                started_at=run.started_at,
                finished_at=run.finished_at,
                error_message=run.error_message,
            )
            latest_by_workflow_id.setdefault(run.workflow_id, run)
        return latest_by_workflow_id

    @staticmethod
    def _deployment_history_by_app_id(
        db: Session, app_ids: list[Any]
    ) -> dict[Any, WorkflowDeployment]:
        if not app_ids:
            return {}

        # inactive와 never deployed를 구분하려면 배포 이력 존재 여부가 필요하다.
        # 다만 상태 계산에는 app별 최신 deployment 1건이면 충분하다.
        latest_deployment_ids = (
            db.query(
                WorkflowDeployment.id.label("id"),
                func.row_number()
                .over(
                    partition_by=WorkflowDeployment.app_id,
                    order_by=[
                        WorkflowDeployment.created_at.desc(),
                        WorkflowDeployment.id.desc(),
                    ],
                )
                .label("row_number"),
            )
            .filter(WorkflowDeployment.app_id.in_(set(app_ids)))
            .subquery()
        )
        deployments = (
            db.query(WorkflowDeployment)
            .join(
                latest_deployment_ids,
                WorkflowDeployment.id == latest_deployment_ids.c.id,
            )
            .filter(latest_deployment_ids.c.row_number == 1)
            .all()
        )
        latest_by_app_id = {}
        for deployment in deployments:
            latest_by_app_id.setdefault(deployment.app_id, deployment)
        return latest_by_app_id

    @staticmethod
    def _operation_deployment_summary(
        app: App, latest_deployment: WorkflowDeployment | None
    ) -> AppOperationDeploymentSummary:
        active_deployment = AppService._own_active_deployment(app)
        # deployment 비활성화 시 app.active_deployment_id가 비워질 수 있으므로
        # inactive 여부는 app field만 보지 않고 deployment 이력으로 판단한다.
        if active_deployment and active_deployment.is_active:
            return AppOperationDeploymentSummary(
                state="active",
                deployment_id=active_deployment.id,
                type=AppService._enum_value(active_deployment.type),
                is_active=True,
            )
        if latest_deployment:
            return AppOperationDeploymentSummary(
                state="inactive",
                deployment_id=latest_deployment.id,
                type=AppService._enum_value(latest_deployment.type),
                is_active=False,
            )
        return AppOperationDeploymentSummary(state="undeployed")

    @staticmethod
    def _own_active_deployment(app: App) -> WorkflowDeployment | None:
        """Return the current App's active deployment, never a stale foreign pointer."""
        active_deployment = getattr(app, "active_deployment", None)
        if (
            active_deployment is None
            or getattr(active_deployment, "app_id", None) != app.id
            or not getattr(active_deployment, "is_active", False)
        ):
            return None
        return active_deployment

    @staticmethod
    def _operation_latest_run_summary(
        run: WorkflowRun | None,
    ) -> AppOperationLatestRunSummary:
        if not run:
            return AppOperationLatestRunSummary(state="not_started")

        raw_status = AppService._enum_value(run.status)
        state = AppService._operation_run_state(raw_status)
        return AppOperationLatestRunSummary(
            state=state,
            run_id=run.id,
            raw_status=raw_status,
            started_at=run.started_at,
            finished_at=run.finished_at,
            error_message=AppService._safe_error_message(run.error_message),
        )

    @staticmethod
    def _operation_run_state(status: str | None) -> str:
        normalized = str(status or "").lower()
        if normalized in {"running", "pending", "queued", "in_progress"}:
            return "running"
        if normalized in {"success", "succeeded", "completed"}:
            return "success"
        if normalized in {"failed", "failure", "error", "stopped"}:
            return "failed"
        return "unavailable"

    @staticmethod
    def _safe_error_message(message: str | None) -> str | None:
        if not message:
            return None
        # run error에는 prompt 일부, provider 응답, secret이 섞일 수 있다.
        # 운영 요약에는 원문 대신 일반화된 실패 표시만 노출한다.
        return "execution_failed"

    @staticmethod
    def _enum_value(value: Any) -> Any:
        return getattr(value, "value", value)

    @staticmethod
    def _matches_operation_filters(
        row: AppOperationRow,
        permission: str | None,
        capability: str | None,
        deployment_state: str | None,
        run_state: str | None,
    ) -> bool:
        if permission and row.permission:
            if row.permission.auth_state != permission:
                return False
        elif permission:
            return False

        if capability:
            if row.permission is None:
                return False
            if capability == "execute" and not row.permission.can_execute:
                return False
            if capability == "write" and not row.permission.can_write:
                return False
            if capability == "manage" and not row.permission.can_manage:
                return False

        if deployment_state and row.deployment.state != deployment_state:
            return False
        if run_state and row.latest_run.state != run_state:
            return False
        return True

    @staticmethod
    def _attach_budget_statuses(
        db: Session,
        apps: list[App],
        organization_id: Any,
    ) -> None:
        workflow_ids_by_app_key = _workflow_id_candidates_by_app_key(
            apps,
        )
        workflow_ids = _unique_workflow_ids(
            workflow_id
            for app_workflow_ids in workflow_ids_by_app_key.values()
            for workflow_id in app_workflow_ids
        )
        statuses = {}
        if workflow_ids:
            try:
                statuses = AppService._budget_status_by_workflow_id(
                    db,
                    workflow_ids,
                    organization_id=organization_id,
                    now=datetime.now(KST),
                )
            except Exception:
                _rollback_optional_projection_lookup(db)
                statuses = {}

        for app in apps:
            setattr(
                app,
                "budget_status",
                _first_budget_status(
                    statuses,
                    workflow_ids_by_app_key.get(id(app), []),
                ),
            )

    @staticmethod
    def _attach_operation_metrics(
        db: Session,
        apps: list[App],
    ) -> None:
        workflow_ids_by_app_key = _workflow_id_candidates_by_app_key(apps)
        workflow_ids = _unique_workflow_ids(
            workflow_id
            for app_workflow_ids in workflow_ids_by_app_key.values()
            for workflow_id in app_workflow_ids
        )
        metrics = {}
        if workflow_ids:
            try:
                metrics = AppService._operation_metrics_by_workflow_id(
                    db,
                    workflow_ids,
                    organization_id=apps[0].organization_id if apps else None,
                    now=datetime.now(KST),
                )
            except Exception:
                _rollback_optional_projection_lookup(db)
                metrics = {}

        for app in apps:
            setattr(
                app,
                "operation_metrics",
                _first_operation_metrics(
                    metrics,
                    workflow_ids_by_app_key.get(id(app), []),
                ),
            )

    @staticmethod
    def _operation_metrics_by_workflow_id(
        db: Session,
        workflow_ids: list[Any],
        *,
        organization_id: Any | None = None,
        now: datetime | None = None,
    ) -> dict[Any, dict[str, Any] | None]:
        target_ids = _unique_workflow_ids(workflow_ids)
        metrics = _empty_operation_metrics(target_ids)
        if not target_ids:
            return metrics

        kst_now = (now or datetime.now(KST)).astimezone(KST)
        current_period = AdminUsageService.resolve_month_period_kst(kst_now)
        previous_period = _previous_month_period_kst(current_period.start_at)
        current_costs = _operation_cost_breakdowns(
            db,
            workflow_ids=target_ids,
            period=current_period,
            organization_id=organization_id,
        )
        previous_costs = _budget_status_costs(
            db,
            workflow_ids=target_ids,
            period=previous_period,
            organization_id=organization_id,
        )

        total_seconds = Decimal(
            str((current_period.end_at - current_period.start_at).total_seconds())
        )
        elapsed_seconds = Decimal(
            str(
                max(
                    (kst_now - current_period.start_at).total_seconds(),
                    1,
                )
            )
        )
        projection_multiplier = total_seconds / elapsed_seconds

        for workflow_id in target_ids:
            current_breakdown = current_costs.get(
                workflow_id,
                UsageCostBreakdown(Decimal("0"), Decimal("0")),
            )
            current_cost = current_breakdown.total_cost
            previous_cost = previous_costs.get(workflow_id, Decimal("0"))
            projected_cost = (
                current_cost * projection_multiplier
                if current_cost > 0
                else Decimal("0")
            )
            trend_percent = None
            if previous_cost > 0:
                trend_percent = float(
                    ((projected_cost - previous_cost) / previous_cost) * 100
                )
            metrics[workflow_id] = {
                "current_month_cost": float(current_cost),
                "current_month_workflow_execution_cost": float(
                    current_breakdown.workflow_execution_cost
                ),
                "current_month_agent_builder_cost": float(
                    current_breakdown.agent_builder_cost
                ),
                "projected_month_cost": float(projected_cost),
                "projected_month_workflow_execution_cost": float(
                    current_breakdown.workflow_execution_cost
                    * projection_multiplier
                ),
                "projected_month_agent_builder_cost": float(
                    current_breakdown.agent_builder_cost * projection_multiplier
                ),
                "previous_month_cost": float(previous_cost),
                "trend_percent": trend_percent,
                "usage_data_complete": current_breakdown.usage_data_complete,
                "unresolved_provider_call_count": (
                    current_breakdown.unresolved_provider_call_count
                ),
            }
        return metrics

    @staticmethod
    def _budget_status_by_workflow_id(
        db: Session,
        workflow_ids: list[Any],
        *,
        organization_id: Any,
        now: datetime | None = None,
    ) -> dict[Any, dict[str, Any] | None]:
        target_ids = _unique_workflow_ids(workflow_ids)
        statuses = _empty_budget_statuses(target_ids)
        if not target_ids or organization_id is None:
            return statuses

        period = AdminUsageService.resolve_month_period_kst(now or datetime.now(KST))
        budgets = _active_budget_rows(
            db,
            organization_id=organization_id,
            workflow_ids=target_ids,
        )
        costs = _budget_status_costs(
            db,
            workflow_ids=target_ids,
            period=period,
            organization_id=organization_id,
        )

        for budget in budgets:
            status = _member_budget_status(
                budget,
                current_cost=costs.get(budget.workflow_id, Decimal("0")),
            )
            if status is not None:
                statuses[budget.workflow_id] = status
        return statuses

    @staticmethod
    def list_explore_apps(db: Session, user_id):
        """
        마켓플레이스에 공개된 앱 목록을 조회합니다.

        Args:
            db: 데이터베이스 세션
            user_id: 현재 유저 ID

        Returns:
            공개된 App 객체 리스트
        """
        apps = (
            db.query(App)
            .options(joinedload(App.active_deployment))
            .filter(App.is_market.is_(True))
            .all()
        )

        # owner_name 및 배포 상태 정보 추가
        for app in apps:
            AppService._populate_owner_name(db, app)
            AppService._populate_deployment_status(db, app)

        return apps

    @staticmethod
    def update_app(db: Session, app_id: str, request: AppUpdateRequest, user_id):
        """
        앱 정보를 수정합니다.

        Args:
            db: 데이터베이스 세션
            app_id: 앱 ID
            request: 앱 수정 요청 데이터
            user_id: 요청 유저 ID

        Returns:
            수정된 App 객체 또는 None
        """
        app = db.query(App).filter(App.id == app_id).first()
        if not app:
            return None

        if not AppService.can_manage_app(db, app, user_id):
            return None

        # 필드 업데이트
        if request.name is not None:
            # 이름 중복 체크
            if (
                db.query(App)
                .filter(
                    App.organization_id == app.organization_id,
                    App.name == request.name,
                    App.id != app_id,
                )
                .first()
            ):
                raise ValueError("App with this name already exists.")
            app.name = request.name
        if request.description is not None:
            app.description = request.description
        if request.icon is not None:
            app.icon = request.icon.model_dump()
        if request.is_market is not None:
            # 복제된 앱은 마켓에 공개 불가
            if request.is_market and app.forked_from:
                raise ValueError("Cannot publish cloned app to market")
            app.is_market = request.is_market

        db.commit()
        db.refresh(app)

        AppService._populate_owner_name(db, app)
        return app

    @staticmethod
    def clone_app(
        db: Session,
        source_app_id: str,
        user_id: str,
        organization_id: str = None,
    ):
        """
        기존 앱을 복제합니다.
        """
        from apps.shared.db.models.workflow_deployment import WorkflowDeployment

        # 1. 원본 앱 조회
        source_app = db.query(App).filter(App.id == source_app_id).first()
        if not source_app:
            return None
        if not AppService.can_read_app(db, source_app, user_id):
            return None

        # 2. 활성 배포 확인 (Active Deployment)
        if not source_app.active_deployment_id:
            # 배포된 버전이 없으면 복제 불가 (에러 발생)
            raise ValueError("Cannot clone app without active deployment.")

        # 3. 배포 데이터 조회
        deployment = (
            db.query(WorkflowDeployment)
            .filter(
                WorkflowDeployment.id == source_app.active_deployment_id,
                WorkflowDeployment.app_id == source_app.id,
            )
            .first()
        )

        if not deployment:
            raise ValueError("Active deployment data not found.")

        # 4. 앱 복제 (새로운 객체 생성)
        new_icon = copy.deepcopy(source_app.icon)

        # 복제본도 lifecycle API에서 별도 secret을 발급한다.
        new_slug = AppService._generate_url_slug(db, f"{source_app.name} (복사본)")

        if not organization_id:
            organization_id = ensure_user_default_organization(db, user_id)

        new_app = App(
            organization_id=organization_id,
            name=f"{source_app.name} (복사본)",
            description=source_app.description,
            icon=new_icon,
            url_slug=new_slug,
            auth_secret=None,
            forked_from=source_app_id,  # 원본 추적
            created_by=user_id,
            is_market=False,  # 복제된 앱은 기본적으로 비공개
        )
        db.add(new_app)
        db.flush()

        # 5. 워크플로우 복제 (배포된 스냅샷 기반)
        graph_snapshot = deployment.graph_snapshot

        # 민감 정보 필터링
        cleaned_snapshot = AppService._clean_graph_data(graph_snapshot)

        # graph_snapshot에서 features 분리 (있다면)
        features = cleaned_snapshot.get("features", {})

        # graph 데이터 (features 제외)
        graph_data = {k: v for k, v in cleaned_snapshot.items() if k != "features"}

        new_workflow = Workflow(
            organization_id=organization_id,
            app_id=new_app.id,
            created_by=user_id,
            # 스냅샷 기반 데이터 설정
            graph=graph_data,
            features=features,
            # 배포된 버전은 환경변수/런타임변수가 스냅샷에 포함되지 않을 수 있음 (현재 스키마 기준)
            # 따라서 초기화 또는 스냅샷에 있다면 사용
            env_variables=graph_snapshot.get("env_variables", []),
            runtime_variables=graph_snapshot.get("runtime_variables", []),
        )
        db.add(new_workflow)
        db.flush()

        # App에 워크플로우 연결
        new_app.workflow_id = new_workflow.id
        AppService._grant_workflow_manager_permission(
            db, new_workflow, user_id, organization_id
        )

        db.commit()
        db.refresh(new_app)

        return new_app

    @staticmethod
    def delete_app(db: Session, app_id: str, user_id: str):
        """
        앱을 삭제합니다.
        """
        app = db.query(App).filter(App.id == app_id).first()
        if not app:
            return None

        if not AppService.can_manage_app(db, app, user_id):
            return None

        # 1. Circular dependency 해결을 위해 workflow_id 관계 끊기
        app.workflow_id = None
        db.flush()

        # 2. 연결된 워크플로우 삭제
        # Workflow.app_id가 ON DELETE CASCADE가 아닐 수 있으므로 수동 삭제
        db.query(Workflow).filter(Workflow.app_id == app_id).delete()
        db.flush()

        # 3. 앱 삭제
        # WorkflowDeployment는 ON DELETE CASCADE로 설정되어 있어 자동 삭제됨
        db.delete(app)
        db.commit()

        return True

    @staticmethod
    def _clean_graph_data(graph_snapshot: dict) -> dict:
        """
        그래프 스냅샷에서 민감 정보를 제거합니다.
        (knowledgeBases, knowledgeCollections, api_token, authConfig, password, email 등)
        """
        from apps.shared.domain.workflow_node_binding import (
            strip_workflow_node_bindings,
        )

        cleaned_data = strip_workflow_node_bindings(graph_snapshot)
        pending = list(cleaned_data.get("nodes", []))
        while pending:
            node = pending.pop()
            if not isinstance(node, dict):
                continue
            data = node.get("data", {})
            if not isinstance(data, dict):
                continue
            node_type = node.get("type")

            if node_type == "llmNode":
                data.pop("knowledgeBases", None)
                data.pop("knowledgeCollections", None)
            elif node_type == "githubNode":
                data.pop("api_token", None)
            elif node_type == "httpRequestNode":
                data.pop("authConfig", None)
            elif node_type == "slackPostNode":
                data.pop("authConfig", None)
                data.pop("url", None)
                data.pop("headers", None)
                data.pop("body", None)
            elif node_type == "mailNode":
                data.pop("password", None)
                data.pop("email", None)

            subgraph = data.get("subGraph")
            nested = subgraph.get("nodes") if isinstance(subgraph, dict) else None
            if isinstance(nested, list):
                pending.extend(nested)

        return cleaned_data

    @staticmethod
    def _generate_url_slug(db: Session, name: str) -> str:
        """
        앱 이름으로부터 고유한 URL slug를 생성합니다.
        고유한 App URL Slug를 생성합니다.
        형식: app-{random_hex_4} (예: app-a1b2c3d4)
        """
        while True:
            # 8글자 Hex (4 bytes) -> 총 12글자 (app-XXXXXXXX)
            slug = f"app-{secrets.token_hex(4)}"
            # 중복 체크
            if not db.query(App).filter(App.url_slug == slug).first():
                return slug


def _workflow_id_candidates_by_app_key(
    apps: list[App],
) -> dict[Any, list[Any]]:
    return {
        id(app): _unique_workflow_ids([app.workflow_id]) for app in apps
    }


def _rollback_optional_projection_lookup(db: Session) -> None:
    rollback = getattr(db, "rollback", None)
    if callable(rollback):
        rollback()


def _first_budget_status(
    statuses: dict[Any, dict[str, Any] | None],
    workflow_ids: list[Any],
) -> dict[str, Any] | None:
    for workflow_id in workflow_ids:
        status = statuses.get(workflow_id)
        if status is not None:
            return status
    return None


def _first_operation_metrics(
    metrics: dict[Any, dict[str, Any] | None],
    workflow_ids: list[Any],
) -> dict[str, Any] | None:
    for workflow_id in workflow_ids:
        metric = metrics.get(workflow_id)
        if metric is not None:
            return metric
    return None


def _unique_workflow_ids(workflow_ids) -> list[Any]:
    return list(
        dict.fromkeys(workflow_id for workflow_id in workflow_ids if workflow_id)
    )


def _empty_budget_statuses(workflow_ids: list[Any]) -> dict[Any, dict[str, Any] | None]:
    return {workflow_id: None for workflow_id in workflow_ids}


def _empty_operation_metrics(
    workflow_ids: list[Any],
) -> dict[Any, dict[str, Any] | None]:
    return {workflow_id: None for workflow_id in workflow_ids}


def _previous_month_period_kst(month_start: datetime):
    if month_start.month == 1:
        previous_start = month_start.replace(year=month_start.year - 1, month=12)
    else:
        previous_start = month_start.replace(month=month_start.month - 1)
    return AdminUsageService.resolve_period(
        start_at=previous_start,
        end_at=month_start,
    )


def _active_budget_rows(
    db: Session,
    *,
    organization_id: Any,
    workflow_ids: list[Any],
) -> list[Any]:
    workflow_id_set = set(workflow_ids)
    if hasattr(db, "budgets"):
        return [
            budget
            for budget in db.budgets
            if budget.organization_id == organization_id
            and budget.workflow_id in workflow_id_set
            and bool(budget.is_enabled)
            and AdminUsageService.coalesce_cost(budget.monthly_budget_usd) > 0
        ]

    return (
        db.query(WorkflowBudget)
        .filter(
            WorkflowBudget.organization_id == organization_id,
            WorkflowBudget.workflow_id.in_(workflow_id_set),
            WorkflowBudget.is_enabled.is_(True),
            WorkflowBudget.monthly_budget_usd > 0,
        )
        .all()
    )


def _budget_status_costs(
    db: Session,
    *,
    workflow_ids: list[Any],
    period,
    organization_id: Any | None = None,
) -> dict[Any, Decimal]:
    if hasattr(db, "budgets"):
        return _fake_budget_status_costs(
            db,
            workflow_ids=workflow_ids,
            period=period,
            organization_id=organization_id,
        )
    return _budget_status_costs_query(
        db,
        workflow_ids=workflow_ids,
        period=period,
        organization_id=organization_id,
    )


def _operation_cost_breakdowns(
    db: Session,
    *,
    workflow_ids: list[Any],
    period,
    organization_id: Any | None = None,
) -> dict[Any, UsageCostBreakdown]:
    if hasattr(db, "budgets"):
        return _fake_operation_cost_breakdowns(
            db,
            workflow_ids=workflow_ids,
            period=period,
            organization_id=organization_id,
        )
    return _operation_cost_breakdowns_query(
        db,
        workflow_ids=workflow_ids,
        period=period,
        organization_id=organization_id,
    )


def _fake_operation_cost_breakdowns(
    db,
    *,
    workflow_ids: list[Any],
    period,
    organization_id: Any | None = None,
) -> dict[Any, UsageCostBreakdown]:
    workflow_id_set = set(workflow_ids)
    return {
        workflow_id: UsageCostBreakdown(
            total_cost=usage.total_cost,
            agent_builder_cost=usage.agent_builder_cost,
            unresolved_provider_call_count=(
                usage.unresolved_provider_call_count
            ),
        )
        for workflow_id in workflow_id_set
        for usage in (
            summarize_usage_records(
                workflow_id=workflow_id,
                organization_id=organization_id,
                start_at=period.start_at,
                end_at=period.end_at,
                legacy_usage_logs=getattr(db, "usage_logs", ()),
                provider_operations=getattr(
                    db, "provider_usage_operations", ()
                ),
            ),
        )
    }


def _operation_cost_breakdowns_query(
    db: Session,
    *,
    workflow_ids: list[Any],
    period,
    organization_id: Any | None = None,
) -> dict[Any, UsageCostBreakdown]:
    usage = provider_usage_aggregate_subquery(
        organization_id=organization_id,
        start_at=period.start_at,
        end_at=period.end_at,
    )
    rows = (
        db.query(
            usage.c.workflow_id,
            usage.c.total_cost,
            usage.c.agent_builder_cost,
            usage.c.unresolved_provider_call_count,
        )
        .filter(usage.c.workflow_id.in_(set(workflow_ids)))
        .all()
    )
    return {
        row.workflow_id: UsageCostBreakdown(
            total_cost=AdminUsageService.coalesce_cost(row.total_cost),
            agent_builder_cost=AdminUsageService.coalesce_cost(
                row.agent_builder_cost
            ),
            unresolved_provider_call_count=int(
                row.unresolved_provider_call_count or 0
            ),
        )
        for row in rows
    }


def _member_budget_status(
    budget,
    *,
    current_cost: Decimal,
) -> dict[str, Any] | None:
    monthly_budget = AdminUsageService.coalesce_cost(budget.monthly_budget_usd)
    status = WorkflowBudgetService.classify_budget_usage(
        current_cost=current_cost,
        monthly_budget_usd=monthly_budget,
        is_enabled=budget.is_enabled,
    )
    if status is None:
        return None
    return {
        "usage_ratio": float(current_cost / monthly_budget),
        "status": status,
    }


def _fake_budget_status_costs(
    db,
    *,
    workflow_ids: list[Any],
    period,
    organization_id: Any | None = None,
) -> dict[Any, Decimal]:
    return {
        workflow_id: summarize_usage_records(
            workflow_id=workflow_id,
            organization_id=organization_id,
            start_at=period.start_at,
            end_at=period.end_at,
            legacy_usage_logs=getattr(db, "usage_logs", ()),
            provider_operations=getattr(db, "provider_usage_operations", ()),
        ).total_cost
        for workflow_id in set(workflow_ids)
    }


def _budget_status_costs_query(
    db: Session,
    *,
    workflow_ids: list[Any],
    period,
    organization_id: Any | None = None,
) -> dict[Any, Decimal]:
    usage = provider_usage_aggregate_subquery(
        organization_id=organization_id,
        start_at=period.start_at,
        end_at=period.end_at,
    )
    rows = (
        db.query(usage.c.workflow_id, usage.c.total_cost)
        .filter(usage.c.workflow_id.in_(set(workflow_ids)))
        .all()
    )
    return {
        row.workflow_id: AdminUsageService.coalesce_cost(row.total_cost)
        for row in rows
    }
