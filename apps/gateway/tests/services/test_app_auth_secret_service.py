from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from apps.gateway.services.app_auth_secret_service import (
    AppAuthSecretLifecycleUnavailableError,
    AppAuthSecretPermissionDeniedError,
    AppAuthSecretService,
    AppAuthSecretVersionConflictError,
)
from apps.gateway.services.deployment_service import (
    DeploymentAuthSecretPreflightError,
    DeploymentService,
)
from apps.shared.audit.actions import AuditAction
from apps.shared.db.models.app import App
from apps.shared.db.models.workflow_deployment import DeploymentType
from apps.shared.domain.app_auth_secret import (
    APP_AUTH_SECRET_VERIFIER_VERSION,
    AppAuthSecretCandidateInvalid,
    app_auth_secret_verifier,
)


def _app(*, version: int = 0, secret: str | None = None) -> App:
    now = datetime(2026, 7, 17, 3, 0, tzinfo=timezone.utc)
    return App(
        id=uuid4(),
        organization_id=uuid4(),
        workflow_id=uuid4(),
        name="Test",
        icon={"type": "emoji", "content": "T", "background_color": "#fff"},
        url_slug="test-app",
        auth_secret=None,
        auth_secret_verifier=(
            app_auth_secret_verifier(secret) if secret is not None else None
        ),
        auth_secret_verifier_version=(
            APP_AUTH_SECRET_VERIFIER_VERSION if secret is not None else None
        ),
        auth_secret_generation=version,
        auth_secret_rotated_at=now if version > 0 else None,
        is_market=False,
        created_by=uuid4(),
    )


def _db_returning(app: App) -> MagicMock:
    db = MagicMock()
    query = db.query.return_value
    query.filter.return_value.first.side_effect = [app, app]
    query.filter.return_value.populate_existing.return_value.with_for_update.return_value.first.return_value = app
    return db


@patch(
    "apps.gateway.services.app_auth_secret_service.has_organization_scope_access",
    return_value=True,
)
@patch(
    "apps.gateway.services.app_auth_secret_service.AppService.can_deploy_app",
    return_value=True,
)
@patch("apps.gateway.services.app_auth_secret_service.register_manual_audit_ownership")
@patch("apps.gateway.services.app_auth_secret_service.record_audit")
def test_first_issue_persists_verifier_and_returns_raw_once(
    record_audit,
    register_manual_audit_ownership,
    can_deploy_app,
    has_scope,
):
    app = _app()
    db = _db_returning(app)
    record_audit.return_value = uuid4()
    now = datetime(2026, 7, 17, 4, 0, tzinfo=timezone.utc)

    response = AppAuthSecretService.rotate(
        db,
        app_id=app.id,
        organization_id=app.organization_id,
        actor_user_id=app.created_by,
        expected_version=0,
        revoke_previous_immediately=False,
        lifecycle_mutations_enabled=True,
        now=now,
        secret_generator=lambda: "issued-secret",
    )

    assert response.secret == "issued-secret"
    assert app.auth_secret is None
    assert app.auth_secret_generation == 1
    assert app.auth_secret_verifier == app_auth_secret_verifier("issued-secret")
    assert app.auth_secret_previous_verifier is None
    db.flush.assert_called_once()
    db.commit.assert_called_once()
    audit = record_audit.call_args.kwargs
    assert audit["action"] == AuditAction.APP_AUTH_SECRET_ROTATED
    assert "issued-secret" not in repr(audit)
    assert "verifier" not in repr(audit["metadata"])
    assert "request_id" not in audit["metadata"]
    assert "ip" not in audit["metadata"]
    assert "user_agent" not in audit["metadata"]


@patch(
    "apps.gateway.services.app_auth_secret_service.has_organization_scope_access",
    return_value=True,
)
@patch(
    "apps.gateway.services.app_auth_secret_service.AppService.can_deploy_app",
    return_value=True,
)
@patch("apps.gateway.services.app_auth_secret_service.register_manual_audit_ownership")
@patch(
    "apps.gateway.services.app_auth_secret_service.record_audit",
    return_value=uuid4(),
)
def test_legacy_raw_only_state_authenticates_and_rotates_with_previous_grace(
    record_audit,
    register_manual_audit_ownership,
    can_deploy_app,
    has_scope,
):
    app = _app()
    app.auth_secret = "legacy-secret"
    db = _db_returning(app)
    now = datetime(2026, 7, 17, 4, 0, tzinfo=timezone.utc)

    assert AppAuthSecretService.authenticate(app, "legacy-secret", now=now)
    assert (
        AppAuthSecretService._status_response(
            app,
            lifecycle_mutations_enabled=True,
            now=now,
        ).configured
        is True
    )

    response = AppAuthSecretService.rotate(
        db,
        app_id=app.id,
        organization_id=app.organization_id,
        actor_user_id=app.created_by,
        expected_version=0,
        revoke_previous_immediately=False,
        lifecycle_mutations_enabled=True,
        now=now,
        secret_generator=lambda: "managed-secret",
    )

    assert response.version == 1
    assert response.previous_grace_active is True
    assert app.auth_secret is None
    assert AppAuthSecretService.authenticate(app, "managed-secret", now=now)
    assert AppAuthSecretService.authenticate(app, "legacy-secret", now=now)


def test_managed_generation_never_falls_back_to_legacy_raw_secret():
    app = _app(version=1, secret="managed-secret")
    app.auth_secret = "managed-secret"
    app.auth_secret_verifier = "malformed"
    app.auth_secret_previous_verifier = app_auth_secret_verifier("previous-secret")
    app.auth_secret_previous_verifier_version = APP_AUTH_SECRET_VERIFIER_VERSION
    app.auth_secret_previous_valid_until = datetime.now(timezone.utc) + timedelta(
        minutes=5
    )

    assert not AppAuthSecretService.authenticate(app, "managed-secret")
    status = AppAuthSecretService._status_response(
        app,
        lifecycle_mutations_enabled=True,
        now=None,
    )
    assert status.configured is False
    assert status.previous_grace_active is False
    assert status.previous_valid_until is None


@patch(
    "apps.gateway.services.app_auth_secret_service.has_organization_scope_access",
    return_value=True,
)
@patch(
    "apps.gateway.services.app_auth_secret_service.AppService.can_deploy_app",
    return_value=True,
)
@patch("apps.gateway.services.app_auth_secret_service.register_manual_audit_ownership")
@patch("apps.gateway.services.app_auth_secret_service.record_audit")
def test_rotation_keeps_only_immediate_previous_for_five_minutes(
    record_audit,
    register_manual_audit_ownership,
    can_deploy_app,
    has_scope,
):
    app = _app(version=2, secret="old-secret")
    db = _db_returning(app)
    record_audit.return_value = uuid4()
    now = datetime(2026, 7, 17, 4, 0, tzinfo=timezone.utc)

    response = AppAuthSecretService.rotate(
        db,
        app_id=app.id,
        organization_id=app.organization_id,
        actor_user_id=app.created_by,
        expected_version=2,
        revoke_previous_immediately=False,
        lifecycle_mutations_enabled=True,
        now=now,
        secret_generator=lambda: "new-secret",
    )

    assert response.version == 3
    assert response.previous_valid_until == now + timedelta(minutes=5)
    assert AppAuthSecretService.authenticate(app, "new-secret", now=now)
    assert AppAuthSecretService.authenticate(app, "old-secret", now=now)
    assert not AppAuthSecretService.authenticate(
        app,
        "old-secret",
        now=now + timedelta(minutes=5),
    )


@patch(
    "apps.gateway.services.app_auth_secret_service.has_organization_scope_access",
    return_value=True,
)
@patch(
    "apps.gateway.services.app_auth_secret_service.AppService.can_deploy_app",
    return_value=True,
)
@patch("apps.gateway.services.app_auth_secret_service.register_manual_audit_ownership")
@patch(
    "apps.gateway.services.app_auth_secret_service.record_audit",
    return_value=uuid4(),
)
def test_immediate_revoke_discards_previous_secret(
    record_audit,
    register_manual_audit_ownership,
    can_deploy_app,
    has_scope,
):
    app = _app(version=2, secret="old-secret")
    db = _db_returning(app)
    now = datetime(2026, 7, 17, 4, 0, tzinfo=timezone.utc)

    response = AppAuthSecretService.rotate(
        db,
        app_id=app.id,
        organization_id=app.organization_id,
        actor_user_id=app.created_by,
        expected_version=2,
        revoke_previous_immediately=True,
        lifecycle_mutations_enabled=True,
        now=now,
        secret_generator=lambda: "new-secret",
    )

    assert response.previous_grace_active is False
    assert response.previous_valid_until is None
    assert app.auth_secret_previous_verifier is None
    assert AppAuthSecretService.authenticate(app, "new-secret", now=now)
    assert not AppAuthSecretService.authenticate(app, "old-secret", now=now)


@patch(
    "apps.gateway.services.app_auth_secret_service.has_organization_scope_access",
    return_value=True,
)
@patch(
    "apps.gateway.services.app_auth_secret_service.AppService.can_deploy_app",
    return_value=True,
)
def test_stale_version_does_not_generate_or_commit(
    can_deploy_app,
    has_scope,
):
    app = _app(version=2, secret="current-secret")
    db = _db_returning(app)
    generator = MagicMock(return_value="must-not-be-created")

    with pytest.raises(AppAuthSecretVersionConflictError):
        AppAuthSecretService.rotate(
            db,
            app_id=app.id,
            organization_id=app.organization_id,
            actor_user_id=app.created_by,
            expected_version=1,
            revoke_previous_immediately=False,
            lifecycle_mutations_enabled=True,
            secret_generator=generator,
        )

    generator.assert_not_called()
    db.commit.assert_not_called()


@patch(
    "apps.gateway.services.app_auth_secret_service.has_organization_scope_access",
    return_value=True,
)
@patch(
    "apps.gateway.services.app_auth_secret_service.AppService.can_deploy_app",
    return_value=False,
)
@patch(
    "apps.gateway.services.app_auth_secret_service.get_effective_workflow_auth_state",
    return_value="viewer",
)
@patch(
    "apps.gateway.services.app_auth_secret_service.record_resource_permission_denied"
)
def test_permission_denial_is_audited_without_mutation(
    record_denied,
    get_effective_state,
    can_deploy_app,
    has_scope,
):
    app = _app()
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = app

    with pytest.raises(AppAuthSecretPermissionDeniedError):
        AppAuthSecretService.status(
            db,
            app_id=app.id,
            organization_id=app.organization_id,
            actor_user_id=app.created_by,
            lifecycle_mutations_enabled=True,
        )

    record_denied.assert_called_once()
    assert record_denied.call_args.kwargs["action"] == "deploy"
    db.commit.assert_not_called()


@patch(
    "apps.gateway.services.app_auth_secret_service.has_organization_scope_access",
    return_value=True,
)
@patch(
    "apps.gateway.services.app_auth_secret_service.AppService.can_deploy_app",
    side_effect=[True, False],
)
@patch(
    "apps.gateway.services.app_auth_secret_service.get_effective_workflow_auth_state",
    return_value="viewer",
)
@patch(
    "apps.gateway.services.app_auth_secret_service.record_resource_permission_denied"
)
def test_permission_is_rechecked_after_lock_before_secret_generation(
    record_denied,
    get_effective_state,
    can_deploy_app,
    has_scope,
):
    app = _app(version=1, secret="current-secret")
    db = _db_returning(app)
    generator = MagicMock(return_value="must-not-be-created")

    with pytest.raises(AppAuthSecretPermissionDeniedError):
        AppAuthSecretService.rotate(
            db,
            app_id=app.id,
            organization_id=app.organization_id,
            actor_user_id=app.created_by,
            expected_version=1,
            revoke_previous_immediately=False,
            lifecycle_mutations_enabled=True,
            secret_generator=generator,
        )

    generator.assert_not_called()
    record_denied.assert_called_once()
    db.rollback.assert_called_once()
    db.commit.assert_not_called()


@patch(
    "apps.gateway.services.app_auth_secret_service.has_organization_scope_access",
    return_value=True,
)
@patch(
    "apps.gateway.services.app_auth_secret_service.AppService.can_deploy_app",
    return_value=True,
)
@patch("apps.gateway.services.app_auth_secret_service.register_manual_audit_ownership")
@patch(
    "apps.gateway.services.app_auth_secret_service.record_audit",
    return_value=None,
)
def test_audit_failure_rolls_back_and_returns_no_secret(
    record_audit,
    register_manual_audit_ownership,
    can_deploy_app,
    has_scope,
):
    app = _app()
    db = _db_returning(app)

    with pytest.raises(RuntimeError, match="audit_unavailable"):
        AppAuthSecretService.rotate(
            db,
            app_id=app.id,
            organization_id=app.organization_id,
            actor_user_id=app.created_by,
            expected_version=0,
            revoke_previous_immediately=False,
            lifecycle_mutations_enabled=True,
            secret_generator=lambda: "not-returned",
        )

    db.rollback.assert_called_once()
    db.commit.assert_not_called()


@patch(
    "apps.gateway.services.app_auth_secret_service.has_organization_scope_access",
    return_value=True,
)
@patch(
    "apps.gateway.services.app_auth_secret_service.AppService.can_deploy_app",
    return_value=True,
)
@patch("apps.gateway.services.app_auth_secret_service.register_manual_audit_ownership")
@patch("apps.gateway.services.app_auth_secret_service.record_audit")
def test_invalid_generator_result_rolls_back_before_audit_or_commit(
    record_audit,
    register_manual_audit_ownership,
    can_deploy_app,
    has_scope,
):
    app = _app()
    db = _db_returning(app)

    with pytest.raises(
        AppAuthSecretCandidateInvalid,
        match="candidate_invalid",
    ):
        AppAuthSecretService.rotate(
            db,
            app_id=app.id,
            organization_id=app.organization_id,
            actor_user_id=app.created_by,
            expected_version=0,
            revoke_previous_immediately=False,
            lifecycle_mutations_enabled=True,
            secret_generator=lambda: b"not-a-string",  # type: ignore[return-value]
        )

    register_manual_audit_ownership.assert_not_called()
    record_audit.assert_not_called()
    db.rollback.assert_called_once()
    db.flush.assert_not_called()
    db.commit.assert_not_called()


@patch(
    "apps.gateway.services.app_auth_secret_service.has_organization_scope_access",
    return_value=True,
)
@patch(
    "apps.gateway.services.app_auth_secret_service.AppService.can_deploy_app",
    return_value=True,
)
@patch("apps.gateway.services.app_auth_secret_service.register_manual_audit_ownership")
@patch(
    "apps.gateway.services.app_auth_secret_service.record_audit",
    return_value=uuid4(),
)
def test_commit_failure_rolls_back_and_returns_no_secret(
    record_audit,
    register_manual_audit_ownership,
    can_deploy_app,
    has_scope,
):
    app = _app()
    db = _db_returning(app)
    db.commit.side_effect = RuntimeError("commit failed")

    with pytest.raises(RuntimeError, match="commit failed"):
        AppAuthSecretService.rotate(
            db,
            app_id=app.id,
            organization_id=app.organization_id,
            actor_user_id=app.created_by,
            expected_version=0,
            revoke_previous_immediately=False,
            lifecycle_mutations_enabled=True,
            secret_generator=lambda: "not-returned",
        )

    db.rollback.assert_called_once()


@patch(
    "apps.gateway.services.app_auth_secret_service.has_organization_scope_access",
    return_value=True,
)
@patch(
    "apps.gateway.services.app_auth_secret_service.AppService.can_deploy_app",
    return_value=True,
)
def test_disabled_lifecycle_gate_rejects_before_lock_or_secret_generation(
    can_deploy_app,
    has_scope,
):
    app = _app(version=1, secret="current-secret")
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = app
    generator = MagicMock(return_value="must-not-be-created")

    with pytest.raises(AppAuthSecretLifecycleUnavailableError):
        AppAuthSecretService.rotate(
            db,
            app_id=app.id,
            organization_id=app.organization_id,
            actor_user_id=app.created_by,
            expected_version=1,
            revoke_previous_immediately=False,
            lifecycle_mutations_enabled=False,
            secret_generator=generator,
        )

    generator.assert_not_called()
    db.query.return_value.filter.return_value.populate_existing.assert_not_called()
    db.flush.assert_not_called()
    db.commit.assert_not_called()


def test_active_api_deployment_without_secret_is_blocked_while_lifecycle_disabled():
    app = _app()

    with pytest.raises(DeploymentAuthSecretPreflightError) as exc_info:
        DeploymentService.ensure_auth_secret_ready_for_activation(
            app,
            deployment_type=DeploymentType.API,
            is_active=True,
            lifecycle_mutations_enabled=False,
        )

    assert exc_info.value.code == "app.auth_secret_lifecycle_unavailable"
    assert exc_info.value.details == {}


def test_active_api_deployment_requires_secret_after_lifecycle_activation():
    app = _app()

    with pytest.raises(DeploymentAuthSecretPreflightError) as exc_info:
        DeploymentService.ensure_auth_secret_ready_for_activation(
            app,
            deployment_type=DeploymentType.API,
            is_active=True,
            lifecycle_mutations_enabled=True,
        )

    assert exc_info.value.code == "deployment.app_auth_secret_required"
    assert exc_info.value.details == {
        "required_actions": ["issue_app_auth_secret"]
    }


@pytest.mark.parametrize(
    ("deployment_type", "is_active"),
    [
        (DeploymentType.API, False),
        (DeploymentType.WEBAPP, True),
    ],
)
def test_deployment_without_secret_is_allowed_when_auth_secret_is_not_required(
    deployment_type,
    is_active,
):
    DeploymentService.ensure_auth_secret_ready_for_activation(
        _app(),
        deployment_type=deployment_type,
        is_active=is_active,
        lifecycle_mutations_enabled=False,
    )


@pytest.mark.parametrize("deployment_type", [DeploymentType.API, DeploymentType.WEBHOOK])
def test_active_secret_protected_deployment_accepts_managed_secret(deployment_type):
    DeploymentService.ensure_auth_secret_ready_for_activation(
        _app(version=1, secret="managed-secret"),
        deployment_type=deployment_type,
        is_active=True,
        lifecycle_mutations_enabled=False,
    )
