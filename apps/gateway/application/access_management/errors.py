from __future__ import annotations


class AccessManagementError(Exception):
    code = "operation.failed"
    message = "Access management operation failed."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.message)


class ResourceHidden(AccessManagementError):
    code = "resource.not_found"
    message = "Resource not found."


class PermissionDenied(AccessManagementError):
    code = "permission.denied"
    message = "Organization manager permission is required."


class InputValidationError(AccessManagementError):
    code = "validation.failed"
    message = "Invalid access action command."


class StaleState(AccessManagementError):
    code = "stale_state"
    message = "The access state changed. Refresh and try again."


class WorkflowPrimaryChanged(AccessManagementError):
    code = "workflow.primary_changed"
    message = "The App primary Workflow changed. Refresh and try again."


class PolicyBlocked(AccessManagementError):
    policy_reason = "access_management.policy_blocked"


class SelfControlForbidden(PolicyBlocked):
    code = "self_control_forbidden"
    message = "You cannot suspend or demote yourself."
    policy_reason = "access_management.self_control_forbidden"


class LastActiveManager(PolicyBlocked):
    code = "last_active_manager"
    message = "Cannot suspend or demote the last active organization manager."
    policy_reason = "access_management.last_active_manager"


class ManagerOverrideActive(PolicyBlocked):
    code = "manager_override_active"
    message = "Demote the member before changing latent resource access."
    policy_reason = "access_management.manager_override_active"


class MemberStateNotManageable(PolicyBlocked):
    code = "member_state_not_manageable"
    message = "The member state does not allow this access action."
    policy_reason = "access_management.member_state_not_manageable"


class TargetUserInactive(PolicyBlocked):
    code = "target_user_inactive"
    message = "The deactivated user can only be cleaned up."
    policy_reason = "access_management.target_user_inactive"


class AuditPersistenceFailed(AccessManagementError):
    code = "audit.persistence_failed"
    message = "The required audit record could not be persisted."


POLICY_ERROR_BY_REASON = {
    "self_control_forbidden": SelfControlForbidden,
    "last_active_manager": LastActiveManager,
    "manager_override_active": ManagerOverrideActive,
    "member_state_not_manageable": MemberStateNotManageable,
    "target_user_inactive": TargetUserInactive,
}
