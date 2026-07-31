from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from apps.gateway.application.resource_permissions.mutation import (
    PermissionMutationCommand,
    PermissionMutationNotFound,
    PermissionMutationResult,
    PermissionProjection,
)
from apps.gateway.services.resource_permission_registry import (
    ResourcePermissionSpec,
    resource_permission_spec,
)
from apps.gateway.services.workflow_permission_lock import (
    lock_workflow_permission_scope,
)
from apps.shared.audit.manual_ownership import register_manual_audit_ownership


class SqlAlchemyResourcePermissionMutationRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def mutate(
        self, command: PermissionMutationCommand
    ) -> PermissionMutationResult:
        spec = resource_permission_spec(command.resource_type)
        route = spec.permission_route(command.grantee_type)
        self._lock_scope(command)
        self._lock_natural_key(command)
        existing = (
            self.db.query(route.model)
            .filter(
                route.model.grantee_organization_id == command.organization_id,
                getattr(route.model, route.resource_column) == command.resource_id,
                getattr(route.model, route.grantee_column) == command.grantee_id,
            )
            .first()
        )
        if command.operation == "delete":
            return self._delete(command, spec, existing)
        return self._upsert(command, spec, existing)

    def _upsert(
        self,
        command: PermissionMutationCommand,
        spec: ResourcePermissionSpec,
        existing,
    ) -> PermissionMutationResult:
        if command.auth_state is None:
            raise ValueError("auth_state is required for permission upsert")
        route = spec.permission_route(command.grantee_type)
        before_projection = (
            self._projection(command, route, existing)
            if existing is not None
            else None
        )
        values = {
            "grantee_organization_id": command.organization_id,
            route.resource_column: command.resource_id,
            route.grantee_column: command.grantee_id,
            "auth_state": command.auth_state,
            "assigned_by": command.actor_id,
            "assigned_at": command.assigned_at,
            "options": {},
            "flags": 0,
        }
        insert_stmt = pg_insert(route.model).values(**values)
        conflict_columns = [route.model.grantee_organization_id]
        if command.grantee_type == "team":
            conflict_columns.extend(
                [
                    getattr(route.model, route.resource_column),
                    getattr(route.model, route.grantee_column),
                ]
            )
        else:
            conflict_columns.extend(
                [
                    getattr(route.model, route.grantee_column),
                    getattr(route.model, route.resource_column),
                ]
            )
        upsert_stmt = (
            insert_stmt.on_conflict_do_update(
                index_elements=conflict_columns,
                set_={
                    "auth_state": insert_stmt.excluded.auth_state,
                    "assigned_by": insert_stmt.excluded.assigned_by,
                    "assigned_at": insert_stmt.excluded.assigned_at,
                },
                where=route.model.auth_state != insert_stmt.excluded.auth_state,
            )
            .returning(route.model)
            .execution_options(populate_existing=True)
        )
        permission = self.db.scalars(upsert_stmt).one_or_none()
        if permission is None:
            if existing is None:
                raise RuntimeError("permission upsert returned no row")
            permission = existing
            status = "unchanged"
        else:
            status = "created" if existing is None else "updated"
        projection = self._projection(command, route, permission)
        return PermissionMutationResult(
            status=status,
            permission=projection,
            before=(
                before_projection.safe_snapshot()
                if before_projection is not None
                else None
            ),
            after=projection.safe_snapshot(),
        )

    def _delete(
        self,
        command: PermissionMutationCommand,
        spec: ResourcePermissionSpec,
        existing,
    ) -> PermissionMutationResult:
        if existing is None:
            raise PermissionMutationNotFound()
        route = spec.permission_route(command.grantee_type)
        projection = self._projection(command, route, existing)
        register_manual_audit_ownership(self.db, existing, "deleted")
        self.db.delete(existing)
        return PermissionMutationResult(
            status="deleted",
            permission=projection,
            before=projection.safe_snapshot(),
            after=None,
        )

    def _lock_scope(self, command: PermissionMutationCommand) -> None:
        if command.resource_type == "workflow":
            lock_workflow_permission_scope(
                self.db,
                organization_id=command.organization_id,
                workflow_id=command.resource_id,
            )

    def _lock_natural_key(self, command: PermissionMutationCommand) -> None:
        namespace_resource = {
            "workflow": "workflow",
            "llm_credential": "llm",
        }[command.resource_type]
        namespace = (
            f"{command.grantee_type}_{namespace_resource}_permission"
        )
        lock_key = ":".join(
            str(value)
            for value in (
                command.organization_id,
                command.resource_id,
                command.grantee_id,
            )
        )
        self.db.execute(
            select(
                func.pg_advisory_xact_lock(
                    func.hashtext(namespace),
                    func.hashtext(lock_key),
                )
            )
        )

    @staticmethod
    def _projection(command, route, permission) -> PermissionProjection:
        return PermissionProjection(
            permission_id=permission.id,
            organization_id=permission.grantee_organization_id,
            resource_type=command.resource_type,
            resource_id=getattr(permission, route.resource_column),
            grantee_type=command.grantee_type,
            grantee_id=getattr(permission, route.grantee_column),
            auth_state=permission.auth_state,
            assigned_by=permission.assigned_by,
            assigned_at=permission.assigned_at,
            options=dict(permission.options or {}),
            flags=permission.flags,
        )
