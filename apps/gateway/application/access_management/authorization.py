from __future__ import annotations

import uuid

from .errors import PermissionDenied, ResourceHidden
from .ports import OrganizationAuthorizationPort, PermissionDenialPort


def require_organization_manager(
    authorization: OrganizationAuthorizationPort,
    denial_recorder: PermissionDenialPort,
    *,
    actor_id: uuid.UUID,
    organization_id: uuid.UUID,
) -> None:
    if authorization.is_organization_manager(actor_id, organization_id):
        return
    if not authorization.has_organization_scope(actor_id, organization_id):
        raise ResourceHidden("Organization not found.")
    denial_recorder.record_permission_denied(actor_id, organization_id)
    raise PermissionDenied()
