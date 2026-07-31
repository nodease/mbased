from __future__ import annotations

import uuid

from apps.shared.audit.context import get_current_metadata
from apps.shared.services.permission_audit import record_resource_permission_denied


class DeploymentPermissionDenialAuditRecorder:
    def record_mail_credential_use_denied(
        self,
        *,
        principal_id: uuid.UUID,
        organization_id: uuid.UUID,
        credential_id: uuid.UUID,
        effective_auth_state: str,
    ) -> None:
        request_id = get_current_metadata().get("request_id")
        metadata = {"authorization_surface": "configuration_preflight"}
        if isinstance(request_id, str) and request_id:
            metadata["request_id"] = request_id
        record_resource_permission_denied(
            user_id=principal_id,
            resource_type="mail_credential",
            resource_id=credential_id,
            action="use",
            effective_auth_state=effective_auth_state,
            organization_id=organization_id,
            metadata=metadata,
        )
