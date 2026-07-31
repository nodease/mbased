from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from apps.gateway.services.app_service import AppService
from apps.shared.audit.actions import AuditAction
from apps.shared.audit.logger import record_audit
from apps.shared.audit.manual_ownership import register_manual_audit_ownership
from apps.shared.db.models.app import App
from apps.shared.domain.app_auth_secret import (
    APP_AUTH_SECRET_VERIFIER_VERSION,
    AppAuthSecretCandidateInvalid,
    app_auth_secret_previous_is_active,
    app_auth_secret_verifier,
    app_auth_secret_verifier_state_is_valid,
    generate_app_auth_secret,
    verify_app_auth_secret,
    verify_legacy_app_auth_secret,
)
from apps.shared.permissions import AUTH_STATE_NONE
from apps.shared.schemas.app import (
    AppAuthSecretRotateResponse,
    AppAuthSecretStatusResponse,
)
from apps.shared.services.permission_audit import record_resource_permission_denied
from apps.shared.services.permissions import (
    get_effective_workflow_auth_state,
    has_organization_scope_access,
)

APP_AUTH_SECRET_PREVIOUS_GRACE = timedelta(minutes=5)


class AppAuthSecretNotFoundError(LookupError):
    pass


class AppAuthSecretPermissionDeniedError(PermissionError):
    pass


class AppAuthSecretVersionConflictError(RuntimeError):
    pass


class AppAuthSecretLifecycleUnavailableError(RuntimeError):
    pass


class AppAuthSecretService:
    @staticmethod
    def is_configured(app: App) -> bool:
        version = int(getattr(app, "auth_secret_generation", 0) or 0)
        if version > 0:
            return app_auth_secret_verifier_state_is_valid(
                getattr(app, "auth_secret_verifier", None),
                getattr(app, "auth_secret_verifier_version", None),
            )
        legacy_secret = getattr(app, "auth_secret", None)
        if version != 0 or not isinstance(legacy_secret, str):
            return False
        try:
            app_auth_secret_verifier(legacy_secret)
        except AppAuthSecretCandidateInvalid:
            return False
        return True

    @staticmethod
    def status(
        db: Session,
        *,
        app_id: UUID,
        organization_id: UUID,
        actor_user_id: UUID,
        lifecycle_mutations_enabled: bool,
        now: datetime | None = None,
    ) -> AppAuthSecretStatusResponse:
        app = AppAuthSecretService._get_scoped_app(
            db,
            app_id=app_id,
            organization_id=organization_id,
            actor_user_id=actor_user_id,
        )
        AppAuthSecretService._require_deploy_permission(
            db,
            app=app,
            actor_user_id=actor_user_id,
        )
        return AppAuthSecretService._status_response(
            app,
            lifecycle_mutations_enabled=lifecycle_mutations_enabled,
            now=now,
        )

    @staticmethod
    def rotate(
        db: Session,
        *,
        app_id: UUID,
        organization_id: UUID,
        actor_user_id: UUID,
        expected_version: int,
        revoke_previous_immediately: bool,
        lifecycle_mutations_enabled: bool,
        now: datetime | None = None,
        secret_generator: Callable[[], str] = generate_app_auth_secret,
    ) -> AppAuthSecretRotateResponse:
        preflight_app = AppAuthSecretService._get_scoped_app(
            db,
            app_id=app_id,
            organization_id=organization_id,
            actor_user_id=actor_user_id,
        )
        AppAuthSecretService._require_deploy_permission(
            db,
            app=preflight_app,
            actor_user_id=actor_user_id,
        )
        if not lifecycle_mutations_enabled:
            raise AppAuthSecretLifecycleUnavailableError(
                "app.auth_secret_lifecycle_unavailable"
            )

        locked_app = (
            db.query(App)
            .filter(
                App.id == app_id,
                App.organization_id == organization_id,
            )
            .populate_existing()
            .with_for_update()
            .first()
        )
        if locked_app is None:
            raise AppAuthSecretNotFoundError("app.not_found")
        try:
            AppAuthSecretService._require_deploy_permission(
                db,
                app=locked_app,
                actor_user_id=actor_user_id,
            )
        except Exception:
            db.rollback()
            raise

        current_version = int(locked_app.auth_secret_generation or 0)
        if current_version != expected_version:
            db.rollback()
            raise AppAuthSecretVersionConflictError("app.auth_secret_version_conflict")

        try:
            current_time = now or datetime.now(timezone.utc)
            if current_time.tzinfo is None:
                raise ValueError("app.auth_secret_clock_invalid")
            rotated_at = current_time.astimezone(timezone.utc)

            new_secret = secret_generator()
            if not isinstance(new_secret, str):
                raise AppAuthSecretCandidateInvalid("app.auth_secret_candidate_invalid")
            new_verifier = app_auth_secret_verifier(new_secret)

            previous_verifier = None
            previous_verifier_version = None
            if app_auth_secret_verifier_state_is_valid(
                locked_app.auth_secret_verifier,
                locked_app.auth_secret_verifier_version,
            ):
                previous_verifier = locked_app.auth_secret_verifier
                previous_verifier_version = locked_app.auth_secret_verifier_version
            elif current_version == 0 and isinstance(locked_app.auth_secret, str):
                try:
                    previous_verifier = app_auth_secret_verifier(locked_app.auth_secret)
                    previous_verifier_version = APP_AUTH_SECRET_VERIFIER_VERSION
                except AppAuthSecretCandidateInvalid:
                    pass

            previous_grace_active = (
                previous_verifier is not None and not revoke_previous_immediately
            )
            if previous_grace_active:
                locked_app.auth_secret_previous_verifier = previous_verifier
                locked_app.auth_secret_previous_verifier_version = (
                    previous_verifier_version
                )
                locked_app.auth_secret_previous_valid_until = (
                    rotated_at + APP_AUTH_SECRET_PREVIOUS_GRACE
                )
            else:
                locked_app.auth_secret_previous_verifier = None
                locked_app.auth_secret_previous_verifier_version = None
                locked_app.auth_secret_previous_valid_until = None
            previous_valid_until = locked_app.auth_secret_previous_valid_until

            # Lifecycle mutation is enabled only after verifier-aware Gateway
            # convergence, so newly issued credentials never need legacy raw storage.
            locked_app.auth_secret = None
            locked_app.auth_secret_verifier = new_verifier
            locked_app.auth_secret_verifier_version = APP_AUTH_SECRET_VERIFIER_VERSION
            locked_app.auth_secret_generation = current_version + 1
            locked_app.auth_secret_rotated_at = rotated_at

            response = AppAuthSecretRotateResponse(
                secret=new_secret,
                version=current_version + 1,
                rotated_at=rotated_at,
                previous_grace_active=previous_grace_active,
                previous_valid_until=(
                    previous_valid_until if previous_grace_active else None
                ),
            )

            register_manual_audit_ownership(db, locked_app, "updated")
            audit_id = record_audit(
                action=AuditAction.APP_AUTH_SECRET_ROTATED,
                category="action",
                actor_id=actor_user_id,
                actor_type="user",
                target_type="app",
                target_id=locked_app.id,
                status="success",
                metadata={
                    "organization_id": str(organization_id),
                    "app_id": str(locked_app.id),
                    "previous_version": current_version,
                    "current_version": current_version + 1,
                    "revoke_previous_immediately": revoke_previous_immediately,
                    "previous_grace_active": previous_grace_active,
                },
                db_session=db,
            )
            if audit_id is None:
                raise RuntimeError("app.auth_secret_audit_unavailable")

            db.flush()
            db.commit()
        except Exception:
            db.rollback()
            raise

        return response

    @staticmethod
    def authenticate(
        app: App,
        candidate: str | bytes | None,
        *,
        now: datetime | None = None,
    ) -> bool:
        if candidate is None:
            return False
        current_version = int(app.auth_secret_generation or 0)
        if current_version == 0:
            return verify_legacy_app_auth_secret(candidate, app.auth_secret)
        return verify_app_auth_secret(
            candidate,
            current_verifier=app.auth_secret_verifier,
            current_verifier_version=app.auth_secret_verifier_version,
            previous_verifier=app.auth_secret_previous_verifier,
            previous_verifier_version=app.auth_secret_previous_verifier_version,
            previous_valid_until=app.auth_secret_previous_valid_until,
            now=now,
        )

    @staticmethod
    def _get_scoped_app(
        db: Session,
        *,
        app_id: UUID,
        organization_id: UUID,
        actor_user_id: UUID,
    ) -> App:
        if not has_organization_scope_access(db, actor_user_id, organization_id):
            raise AppAuthSecretNotFoundError("app.not_found")
        app = (
            db.query(App)
            .filter(
                App.id == app_id,
                App.organization_id == organization_id,
            )
            .first()
        )
        if app is None:
            raise AppAuthSecretNotFoundError("app.not_found")
        return app

    @staticmethod
    def _require_deploy_permission(
        db: Session,
        *,
        app: App,
        actor_user_id: UUID,
    ) -> None:
        if AppService.can_deploy_app(db, app, actor_user_id):
            return
        effective_auth_state: Any = AUTH_STATE_NONE
        if app.workflow_id is not None:
            effective_auth_state = get_effective_workflow_auth_state(
                db,
                actor_user_id,
                app.workflow_id,
                organization_id=app.organization_id,
            )
        record_resource_permission_denied(
            user_id=actor_user_id,
            resource_type="app",
            resource_id=app.id,
            action="deploy",
            effective_auth_state=str(effective_auth_state),
            organization_id=app.organization_id,
            metadata={},
        )
        raise AppAuthSecretPermissionDeniedError("permission.denied")

    @staticmethod
    def _status_response(
        app: App,
        *,
        lifecycle_mutations_enabled: bool,
        now: datetime | None,
    ) -> AppAuthSecretStatusResponse:
        version = int(app.auth_secret_generation or 0)
        managed_configured = version > 0 and app_auth_secret_verifier_state_is_valid(
            app.auth_secret_verifier,
            app.auth_secret_verifier_version,
        )
        previous_grace_active = (
            managed_configured
            and app_auth_secret_previous_is_active(
                previous_verifier=app.auth_secret_previous_verifier,
                previous_verifier_version=app.auth_secret_previous_verifier_version,
                previous_valid_until=app.auth_secret_previous_valid_until,
                now=now,
            )
        )
        return AppAuthSecretStatusResponse(
            configured=AppAuthSecretService.is_configured(app),
            version=version,
            rotation_enabled=lifecycle_mutations_enabled,
            rotated_at=app.auth_secret_rotated_at if version > 0 else None,
            previous_grace_active=previous_grace_active,
            previous_valid_until=(
                app.auth_secret_previous_valid_until if previous_grace_active else None
            ),
        )

__all__ = [
    "APP_AUTH_SECRET_PREVIOUS_GRACE",
    "AppAuthSecretLifecycleUnavailableError",
    "AppAuthSecretNotFoundError",
    "AppAuthSecretPermissionDeniedError",
    "AppAuthSecretService",
    "AppAuthSecretVersionConflictError",
]
