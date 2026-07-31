import uuid

from apps.gateway.adapters.audit import deployment_preflight as audit_module
from apps.gateway.adapters.audit.deployment_preflight import (
    DeploymentPermissionDenialAuditRecorder,
)
from apps.shared.audit.context import clear_current_metadata, set_current_metadata


def test_mail_permission_denial_audit_uses_safe_resource_metadata(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        audit_module,
        "record_resource_permission_denied",
        lambda **kwargs: captured.update(kwargs),
    )
    principal_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    credential_id = uuid.uuid4()

    token = set_current_metadata(
        {
            "request_id": "request-1",
            "path": "/must-not-be-copied",
        }
    )
    try:
        DeploymentPermissionDenialAuditRecorder().record_mail_credential_use_denied(
            principal_id=principal_id,
            organization_id=organization_id,
            credential_id=credential_id,
            effective_auth_state="viewer",
        )
    finally:
        clear_current_metadata(token)

    assert captured == {
        "user_id": principal_id,
        "resource_type": "mail_credential",
        "resource_id": credential_id,
        "action": "use",
        "effective_auth_state": "viewer",
        "organization_id": organization_id,
        "metadata": {
            "authorization_surface": "configuration_preflight",
            "request_id": "request-1",
        },
    }
