from __future__ import annotations

from .models import (
    AccessActionCommand,
    AccessControl,
    AppCreationAccess,
    AppCreationPermissionSnapshot,
    MemberAccessControlSummary,
    MemberAccessControls,
    MemberSnapshot,
    OrganizationRoleControls,
    ResourceAccessProjection,
    ResourceAuthState,
)

AUTH_STATE_RANK: dict[ResourceAuthState, int] = {
    "none": 0,
    "viewer": 1,
    "operator": 2,
    "builder": 3,
    "manager": 4,
}

_RESOURCE_ACTIONS = {
    "team_membership.add",
    "team_membership.remove",
    "direct_permission.grant",
    "direct_permission.revoke",
    "app_creation.grant",
    "app_creation.revoke",
}
_CLEANUP_ACTIONS = {
    "membership.suspend",
    "team_membership.remove",
    "direct_permission.revoke",
    "app_creation.revoke",
}


def is_last_active_manager(member: MemberSnapshot, active_manager_count: int) -> bool:
    return (
        member.user_active
        and member.membership_state == "active"
        and member.organization_auth_state == "manager"
        and active_manager_count <= 1
    )


def access_controls(
    member: MemberSnapshot,
    *,
    actor_id,
    last_active_manager: bool,
) -> MemberAccessControlSummary:
    is_self = member.user_id == actor_id
    manager_override = member.manager_override

    suspend = _state_control(member.membership_state == "active")
    if suspend.allowed and is_self:
        suspend = _blocked("self_control_forbidden")
    elif suspend.allowed and last_active_manager:
        suspend = _blocked("last_active_manager")

    reactivate = _state_control(member.membership_state == "suspended")
    if reactivate.allowed and not member.user_active:
        reactivate = _blocked("target_user_inactive")

    role_member = _state_control(member.organization_auth_state == "manager")
    if role_member.allowed and is_self:
        role_member = _blocked("self_control_forbidden")
    elif role_member.allowed and last_active_manager:
        role_member = _blocked("last_active_manager")

    role_manager = _state_control(member.organization_auth_state == "member")
    if role_manager.allowed and not member.user_active:
        role_manager = _blocked("target_user_inactive")
    elif role_manager.allowed and member.membership_state != "active":
        role_manager = _blocked("member_state_not_manageable")

    grant = _allowed()
    remove = _allowed()
    if manager_override:
        grant = _blocked("manager_override_active")
        remove = _blocked("manager_override_active")
    elif not member.user_active:
        grant = _blocked("target_user_inactive")
    elif member.membership_state != "active":
        grant = _blocked("member_state_not_manageable")

    return MemberAccessControlSummary(
        is_self=is_self,
        is_last_active_manager=last_active_manager,
        manager_override=manager_override,
        actions=MemberAccessControls(
            membership_suspend=suspend,
            membership_reactivate=reactivate,
            organization_role_set=OrganizationRoleControls(
                member=role_member,
                manager=role_manager,
            ),
            team_membership_add=grant,
            team_membership_remove=remove,
            direct_permission_grant=grant,
            direct_permission_revoke=remove,
            app_creation_grant=grant,
            app_creation_revoke=remove,
        ),
    )


def action_policy_reason(
    command: AccessActionCommand,
    member: MemberSnapshot,
    *,
    last_active_manager: bool,
) -> str | None:
    if command.action == "membership.suspend" or (
        command.action == "organization_role.set" and command.role == "member"
    ):
        if command.actor_id == member.user_id:
            return "self_control_forbidden"
        if last_active_manager:
            return "last_active_manager"

    if member.manager_override and command.action in _RESOURCE_ACTIONS:
        return "manager_override_active"

    if not member.user_active and not _is_global_inactive_cleanup(command, member):
        return "target_user_inactive"

    if member.membership_state == "suspended" and not _is_suspended_action_allowed(
        command,
        member,
    ):
        return "member_state_not_manageable"
    return None


def effective_resource_auth_state(
    member: MemberSnapshot,
    projection: ResourceAccessProjection,
) -> ResourceAuthState:
    if not member.effective_access_enabled:
        return "none"
    if member.manager_override:
        return "manager"
    states = [source.auth_state for source in projection.team_sources]
    if projection.direct_permission is not None:
        states.append(projection.direct_permission.auth_state)
    return strongest_auth_state(states)


def strongest_auth_state(states) -> ResourceAuthState:
    strongest: ResourceAuthState = "none"
    for state in states:
        normalized: ResourceAuthState = state if state in AUTH_STATE_RANK else "none"
        if AUTH_STATE_RANK[normalized] > AUTH_STATE_RANK[strongest]:
            strongest = normalized
    return strongest


def app_creation_access(
    member: MemberSnapshot,
    permission: AppCreationPermissionSnapshot | None,
) -> AppCreationAccess:
    if member.manager_override:
        return AppCreationAccess(
            effective=True,
            effective_source="manager_override",
            direct_permission=permission,
        )
    effective = member.effective_access_enabled and permission is not None
    return AppCreationAccess(
        effective=effective,
        effective_source="direct" if effective else "none",
        direct_permission=permission,
    )


def _is_global_inactive_cleanup(
    command: AccessActionCommand,
    member: MemberSnapshot,
) -> bool:
    if command.action in _CLEANUP_ACTIONS:
        return True
    return (
        command.action == "organization_role.set"
        and command.role == "member"
        and member.organization_auth_state == "manager"
    )


def _is_suspended_action_allowed(
    command: AccessActionCommand,
    member: MemberSnapshot,
) -> bool:
    if command.action in {
        "membership.reactivate",
        "team_membership.remove",
        "direct_permission.revoke",
        "app_creation.revoke",
    }:
        return True
    return (
        command.action == "organization_role.set"
        and command.role == "member"
        and member.organization_auth_state == "manager"
    )


def _state_control(applicable: bool) -> AccessControl:
    return _allowed() if applicable else _blocked("member_state_not_applicable")


def _allowed() -> AccessControl:
    return AccessControl(allowed=True)


def _blocked(reason: str) -> AccessControl:
    return AccessControl(allowed=False, reason=reason)
