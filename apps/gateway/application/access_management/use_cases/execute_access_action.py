from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from ..authorization import require_organization_manager
from ..errors import (
    AuditPersistenceFailed,
    InputValidationError,
    POLICY_ERROR_BY_REASON,
    PolicyBlocked,
    ResourceHidden,
    StaleState,
    WorkflowPrimaryChanged,
)
from ..models import (
    AccessActionCommand,
    AccessActionResult,
    AppliedMutationDescriptor,
    LockedAppCreationPermission,
    LockedDirectPermission,
    LockedMember,
    LockedTeamMembership,
)
from ..policies import action_policy_reason, is_last_active_manager
from ..ports import (
    AppCreationPermissionPort,
    AuditRecorderPort,
    DirectPermissionPort,
    MembershipPort,
    OrganizationAuthorizationPort,
    PermissionDenialPort,
    ReasonSanitizerPort,
    ResourceScopePort,
    TeamMembershipPort,
    UnitOfWork,
)


@dataclass(frozen=True)
class _ActionContext:
    member: LockedMember
    team: LockedTeamMembership | None = None
    direct: LockedDirectPermission | None = None
    app_creation: LockedAppCreationPermission | None = None


class ExecuteAccessAction:
    def __init__(
        self,
        authorization: OrganizationAuthorizationPort,
        membership: MembershipPort,
        team_membership: TeamMembershipPort,
        resource_scope: ResourceScopePort,
        direct_permission: DirectPermissionPort,
        app_creation_permission: AppCreationPermissionPort,
        audit_recorder: AuditRecorderPort,
        denial_recorder: PermissionDenialPort,
        reason_sanitizer: ReasonSanitizerPort,
        unit_of_work: UnitOfWork,
    ) -> None:
        self.authorization = authorization
        self.membership = membership
        self.team_membership = team_membership
        self.resource_scope = resource_scope
        self.direct_permission = direct_permission
        self.app_creation_permission = app_creation_permission
        self.audit_recorder = audit_recorder
        self.denial_recorder = denial_recorder
        self.reason_sanitizer = reason_sanitizer
        self.unit_of_work = unit_of_work

    def execute(self, command: AccessActionCommand) -> AccessActionResult:
        require_organization_manager(
            self.authorization,
            self.denial_recorder,
            actor_id=command.actor_id,
            organization_id=command.organization_id,
        )
        reason = self._sanitize_reason(command.reason)
        manager_reduction = command.action == "membership.suspend" or (
            command.action == "organization_role.set" and command.role == "member"
        )
        locked_member = self.membership.lock_member(
            command.organization_id,
            command.target_user_id,
            manager_reduction=manager_reduction,
        )
        if locked_member is None:
            self.unit_of_work.rollback()
            raise ResourceHidden("Member not found.")

        context = self._lock_action_context(command, locked_member)
        member = locked_member.member
        if (
            member.membership_id != command.expected_membership_id
            or member.user_active != command.expected_user_active
        ):
            self._raise_stale(command, reason, member.membership_id)

        self._validate_child_identity(command, context, reason)
        unchanged = self._unchanged_result(command, context)
        if unchanged is not None:
            self.unit_of_work.rollback()
            return unchanged

        if (
            member.membership_state != command.expected_membership_state
            or member.organization_auth_state
            != command.expected_organization_auth_state
        ):
            self._raise_stale(command, reason, member.membership_id)
        self._validate_source_precondition(command, context, reason)

        last_manager = is_last_active_manager(
            member,
            locked_member.active_manager_count
            if locked_member.active_manager_count is not None
            else 2,
        )
        policy_reason = action_policy_reason(
            command,
            member,
            last_active_manager=last_manager,
        )
        if policy_reason is not None:
            error_type = POLICY_ERROR_BY_REASON[policy_reason]
            self._raise_policy_block(
                command,
                reason,
                member.membership_id,
                error_type(),
            )

        try:
            descriptor = self._apply(command)
            self.audit_recorder.record_mutation(descriptor, command, reason)
            self.unit_of_work.flush()
            self.unit_of_work.commit()
        except InputValidationError:
            self.unit_of_work.rollback()
            raise
        except Exception as exc:
            self.unit_of_work.rollback()
            raise AuditPersistenceFailed() from exc
        return _applied_result(command, descriptor)

    def _sanitize_reason(self, reason: str | None) -> str | None:
        try:
            return self.reason_sanitizer.sanitize(reason)
        except Exception as exc:
            self.unit_of_work.rollback()
            raise AuditPersistenceFailed() from exc

    def _lock_action_context(
        self,
        command: AccessActionCommand,
        member: LockedMember,
    ) -> _ActionContext:
        if command.action.startswith("team_membership."):
            if command.team_id is None:
                self.unit_of_work.rollback()
                raise InputValidationError("team_id is required.")
            team = self.team_membership.lock_team_membership(
                command.organization_id,
                command.target_user_id,
                command.team_id,
                require_active_team=command.action == "team_membership.add",
            )
            if team is None:
                self.unit_of_work.rollback()
                raise ResourceHidden("Team not found.")
            return _ActionContext(member=member, team=team)

        if command.action.startswith("direct_permission."):
            if command.resource_type is None or command.resource_id is None:
                self.unit_of_work.rollback()
                raise InputValidationError(
                    "resource_type and resource_id are required."
                )
            try:
                resource = self.resource_scope.lock_resource(
                    command.organization_id,
                    command.resource_type,
                    command.resource_id,
                )
            except WorkflowPrimaryChanged:
                self.unit_of_work.rollback()
                raise
            if resource is None:
                self.unit_of_work.rollback()
                raise ResourceHidden("Resource not found.")
            direct = self.direct_permission.lock_direct_permission(
                command.organization_id,
                command.target_user_id,
                resource,
            )
            return _ActionContext(member=member, direct=direct)

        if command.action.startswith("app_creation."):
            app_creation = self.app_creation_permission.lock_app_creation_permission(
                command.organization_id,
                command.target_user_id,
            )
            return _ActionContext(member=member, app_creation=app_creation)
        return _ActionContext(member=member)

    def _validate_child_identity(
        self,
        command: AccessActionCommand,
        context: _ActionContext,
        reason: str | None,
    ) -> None:
        if command.action == "team_membership.remove":
            current_id = context.team.membership_id if context.team else None
            if (
                current_id is not None
                and current_id != command.expected_team_membership_id
            ):
                self._raise_stale(
                    command,
                    reason,
                    context.member.member.membership_id,
                )

        if command.action.startswith("direct_permission."):
            current = context.direct.permission if context.direct else None
            if current is None:
                if (
                    command.action == "direct_permission.revoke"
                    or command.expected_permission_id is not None
                ):
                    self.unit_of_work.rollback()
                    raise ResourceHidden("Direct permission not found.")
            elif (
                command.expected_permission_id is not None
                and current.permission_id != command.expected_permission_id
            ):
                self._raise_stale(
                    command,
                    reason,
                    context.member.member.membership_id,
                )

        if command.action == "app_creation.revoke":
            current = context.app_creation.permission if context.app_creation else None
            if current is None:
                self.unit_of_work.rollback()
                raise ResourceHidden("App creation permission not found.")
            if current.permission_id != command.expected_permission_id:
                self._raise_stale(
                    command,
                    reason,
                    context.member.member.membership_id,
                )

    def _unchanged_result(
        self,
        command: AccessActionCommand,
        context: _ActionContext,
    ) -> AccessActionResult | None:
        member = context.member.member
        if (
            command.action == "membership.suspend"
            and member.membership_state == "suspended"
        ):
            return _unchanged(command, "organization_membership", member.membership_id)
        if (
            command.action == "membership.reactivate"
            and member.membership_state == "active"
        ):
            return _unchanged(command, "organization_membership", member.membership_id)
        if (
            command.action == "organization_role.set"
            and command.role == member.organization_auth_state
        ):
            return _unchanged(command, "organization_membership", member.membership_id)
        if command.action == "team_membership.add" and context.team:
            if context.team.membership_id is not None:
                return _unchanged(
                    command,
                    "team_membership",
                    context.team.membership_id,
                    team=True,
                )
        if command.action == "team_membership.remove" and context.team:
            if context.team.membership_id is None:
                return _unchanged(command, "team_membership", None, team=True)
        if command.action == "direct_permission.grant" and context.direct:
            current = context.direct.permission
            if current is not None and current.auth_state == command.auth_state:
                return _unchanged(
                    command,
                    _direct_target_type(command.resource_type),
                    current.permission_id,
                )
        if command.action == "app_creation.grant" and context.app_creation:
            current = context.app_creation.permission
            if current is not None:
                return _unchanged(
                    command,
                    "user_app_creation_permission",
                    current.permission_id,
                )
        return None

    def _validate_source_precondition(
        self,
        command: AccessActionCommand,
        context: _ActionContext,
        reason: str | None,
    ) -> None:
        if command.action == "direct_permission.grant" and context.direct:
            current = context.direct.permission
            if command.expected_absent is True:
                if current is not None:
                    self._raise_stale(
                        command,
                        reason,
                        context.member.member.membership_id,
                    )
            elif current is None or current.auth_state != command.expected_auth_state:
                self._raise_stale(
                    command,
                    reason,
                    context.member.member.membership_id,
                )
        elif command.action == "direct_permission.revoke" and context.direct:
            current = context.direct.permission
            if current is None or current.auth_state != command.expected_auth_state:
                self._raise_stale(
                    command,
                    reason,
                    context.member.member.membership_id,
                )

    def _apply(self, command: AccessActionCommand) -> AppliedMutationDescriptor:
        if command.action == "membership.suspend":
            return self.membership.set_membership_state("suspended")
        if command.action == "membership.reactivate":
            return self.membership.set_membership_state("active")
        if command.action == "organization_role.set":
            if command.role is None:
                raise InputValidationError("role is required.")
            return self.membership.set_organization_role(command.role)
        if command.action == "team_membership.add":
            return self.team_membership.add_team_membership(command.actor_id)
        if command.action == "team_membership.remove":
            return self.team_membership.remove_team_membership()
        if command.action == "direct_permission.grant":
            if command.auth_state is None:
                raise InputValidationError("auth_state is required.")
            return self.direct_permission.grant_direct_permission(
                command.actor_id,
                command.auth_state,
            )
        if command.action == "direct_permission.revoke":
            return self.direct_permission.revoke_direct_permission()
        if command.action == "app_creation.grant":
            return self.app_creation_permission.grant_app_creation_permission(
                command.actor_id
            )
        if command.action == "app_creation.revoke":
            return self.app_creation_permission.revoke_app_creation_permission()
        raise InputValidationError("Unsupported access action.")

    def _raise_stale(
        self,
        command: AccessActionCommand,
        reason: str | None,
        membership_id: UUID,
    ) -> None:
        self._commit_policy_block(
            command,
            reason,
            membership_id=membership_id,
            policy_reason="access_management.stale_state",
        )
        raise StaleState()

    def _raise_policy_block(
        self,
        command: AccessActionCommand,
        reason: str | None,
        membership_id: UUID,
        error: PolicyBlocked,
    ) -> None:
        self._commit_policy_block(
            command,
            reason,
            membership_id=membership_id,
            policy_reason=error.policy_reason,
        )
        raise error

    def _commit_policy_block(
        self,
        command: AccessActionCommand,
        reason: str | None,
        *,
        membership_id: UUID,
        policy_reason: str,
    ) -> None:
        try:
            self.audit_recorder.record_policy_block(
                command,
                membership_id=membership_id,
                policy_reason=policy_reason,
                reason=reason,
            )
            self.unit_of_work.flush()
            self.unit_of_work.commit()
        except Exception as exc:
            self.unit_of_work.rollback()
            raise AuditPersistenceFailed() from exc


def _direct_target_type(resource_type: str | None) -> str:
    target_type = {
        "workflow": "user_workflow_permission",
        "knowledge_base": "user_knowledge_permission",
        "llm_credential": "user_llm_permission",
        "mail_credential": "user_mail_credential_permission",
    }.get(resource_type)
    if target_type is None:
        raise InputValidationError("Unsupported resource type.")
    return target_type


def _unchanged(
    command: AccessActionCommand,
    target_type: str,
    target_id,
    *,
    team: bool = False,
) -> AccessActionResult:
    return AccessActionResult(
        status="unchanged",
        action=command.action,
        target_type=target_type,
        target_id=target_id,
        effective_access_changed=None if team else False,
        affected_resource_source_count=0 if team else None,
    )


def _applied_result(
    command: AccessActionCommand,
    descriptor: AppliedMutationDescriptor,
) -> AccessActionResult:
    return AccessActionResult(
        status="applied",
        action=command.action,
        target_type=descriptor.target_type,
        target_id=descriptor.target_id,
        effective_access_changed=descriptor.effective_access_changed,
        affected_resource_source_count=descriptor.affected_resource_source_count,
    )
