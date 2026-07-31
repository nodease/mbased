import uuid
from datetime import datetime, timezone

import pytest

from apps.gateway.application.access_management.errors import (
    AuditPersistenceFailed,
    InputValidationError,
    LastActiveManager,
    ManagerOverrideActive,
    MemberStateNotManageable,
    PermissionDenied,
    ResourceHidden,
    SelfControlForbidden,
    StaleState,
    TargetUserInactive,
    WorkflowPrimaryChanged,
)
from apps.gateway.application.access_management.models import (
    AccessActionCommand,
    AppliedMutationDescriptor,
    DirectPermissionSnapshot,
    LockedAppCreationPermission,
    LockedDirectPermission,
    LockedMember,
    LockedTeamMembership,
    MemberSnapshot,
    ResourceDescriptor,
)
from apps.gateway.application.access_management.use_cases.execute_access_action import (
    ExecuteAccessAction,
)


NOW = datetime.now(timezone.utc)


def _member(
    *,
    user_active: bool = True,
    membership_state: str = "active",
    role: str = "member",
) -> MemberSnapshot:
    return MemberSnapshot(
        membership_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        name="Member",
        email="member@example.invalid",
        user_active=user_active,
        membership_state=membership_state,
        organization_auth_state=role,
        updated_at=NOW,
    )


def _command(member: MemberSnapshot, action: str, **overrides) -> AccessActionCommand:
    values = {
        "actor_id": uuid.uuid4(),
        "organization_id": member.organization_id,
        "target_user_id": member.user_id,
        "action": action,
        "expected_membership_id": member.membership_id,
        "expected_user_active": member.user_active,
        "expected_membership_state": member.membership_state,
        "expected_organization_auth_state": member.organization_auth_state,
        "reason": " review ",
    }
    values.update(overrides)
    return AccessActionCommand(**values)


class _Adapter:
    def __init__(self, member: MemberSnapshot) -> None:
        self.member = member
        self.manager = True
        self.scope = True
        self.active_manager_count = 2
        self.team = LockedTeamMembership(uuid.uuid4(), True, None, 3)
        self.resource = ResourceDescriptor("workflow", uuid.uuid4(), "Workflow")
        self.direct = LockedDirectPermission(self.resource, None, "none")
        self.app = LockedAppCreationPermission(None)
        self.events: list[str] = []
        self.mutations: list[str] = []
        self.raise_on_mutation = False

    def is_organization_manager(self, actor_id, organization_id):
        self.events.append("authorize.manager")
        return self.manager

    def has_organization_scope(self, actor_id, organization_id):
        self.events.append("authorize.scope")
        return self.scope

    def lock_member(self, organization_id, user_id, *, manager_reduction):
        self.events.append(f"lock.member:{manager_reduction}")
        return LockedMember(self.member, self.active_manager_count)

    def lock_team_membership(
        self,
        organization_id,
        user_id,
        team_id,
        *,
        require_active_team,
    ):
        self.events.append(f"lock.team:{require_active_team}")
        return self.team

    def lock_resource(self, organization_id, resource_type, resource_id):
        self.events.append("lock.resource")
        return self.resource

    def lock_direct_permission(self, organization_id, user_id, resource):
        self.events.append("lock.direct")
        return self.direct

    def lock_app_creation_permission(self, organization_id, user_id):
        self.events.append("lock.app")
        return self.app

    def set_membership_state(self, state):
        return self._mutate("organization.member.update", "organization_membership")

    def set_organization_role(self, role):
        return self._mutate("organization.member.update", "organization_membership")

    def add_team_membership(self, actor_id):
        return self._mutate("team_membership.created", "team_membership", team=True)

    def remove_team_membership(self):
        return self._mutate("team_membership.deleted", "team_membership", team=True)

    def grant_direct_permission(self, actor_id, auth_state):
        return self._mutate("user_workflow_permission.updated", "user_workflow_permission")

    def revoke_direct_permission(self):
        return self._mutate("user_workflow_permission.deleted", "user_workflow_permission")

    def grant_app_creation_permission(self, actor_id):
        return self._mutate(
            "user_app_creation_permission.created",
            "user_app_creation_permission",
        )

    def revoke_app_creation_permission(self):
        return self._mutate(
            "user_app_creation_permission.deleted",
            "user_app_creation_permission",
        )

    def _mutate(self, action, target_type, *, team=False):
        self.events.append("mutation")
        self.mutations.append(action)
        if self.raise_on_mutation:
            raise RuntimeError("write failed")
        return AppliedMutationDescriptor(
            action=action,
            category="data_change" if team else "action",
            target_type=target_type,
            target_id=uuid.uuid4(),
            before={"organization_id": self.member.organization_id},
            after={"organization_id": self.member.organization_id},
            effective_access_changed=None if team else True,
            affected_resource_source_count=3 if team else None,
        )


class _Audit:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.mutations = []
        self.blocks = []
        self.fail_mutation = False
        self.fail_block = False

    def record_mutation(self, descriptor, command, reason):
        self.events.append("audit.mutation")
        if self.fail_mutation:
            raise RuntimeError("audit failed")
        self.mutations.append((descriptor, command, reason))

    def record_policy_block(
        self,
        command,
        *,
        membership_id,
        policy_reason,
        reason,
    ):
        self.events.append("audit.block")
        if self.fail_block:
            raise RuntimeError("audit failed")
        self.blocks.append((command, membership_id, policy_reason, reason))


class _Denial:
    def __init__(self) -> None:
        self.calls = []

    def record_permission_denied(self, actor_id, organization_id):
        self.calls.append((actor_id, organization_id))


class _Reason:
    def __init__(self) -> None:
        self.fail = False

    def sanitize(self, reason):
        if self.fail:
            raise RuntimeError("redaction failed")
        return reason.strip() if reason else None


class _Uow:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.fail_flush = False
        self.fail_commit = False
        self.rollbacks = 0

    def flush(self):
        self.events.append("flush")
        if self.fail_flush:
            raise RuntimeError("flush failed")

    def commit(self):
        self.events.append("commit")
        if self.fail_commit:
            raise RuntimeError("commit failed")

    def rollback(self):
        self.events.append("rollback")
        self.rollbacks += 1


def _use_case(member: MemberSnapshot):
    adapter = _Adapter(member)
    audit = _Audit(adapter.events)
    denial = _Denial()
    reason = _Reason()
    uow = _Uow(adapter.events)
    use_case = ExecuteAccessAction(
        adapter,
        adapter,
        adapter,
        adapter,
        adapter,
        adapter,
        audit,
        denial,
        reason,
        uow,
    )
    return use_case, adapter, audit, denial, reason, uow


def test_permission_denial_happens_before_target_lookup_and_is_audited_coarsely():
    member = _member()
    use_case, adapter, _, denial, _, _ = _use_case(member)
    adapter.manager = False
    command = _command(member, "membership.suspend")

    with pytest.raises(PermissionDenied):
        use_case.execute(command)

    assert denial.calls == [(command.actor_id, member.organization_id)]
    assert not any(event.startswith("lock.") for event in adapter.events)


def test_hidden_organization_does_not_emit_permission_denial_or_target_lookup():
    member = _member()
    use_case, adapter, _, denial, _, _ = _use_case(member)
    adapter.manager = False
    adapter.scope = False

    with pytest.raises(ResourceHidden):
        use_case.execute(_command(member, "membership.suspend"))

    assert denial.calls == []
    assert not any(event.startswith("lock.") for event in adapter.events)


@pytest.mark.parametrize("mismatch", ["membership_id", "user_active"])
def test_membership_identity_and_global_state_stale_precede_noop(mismatch):
    member = _member(membership_state="suspended")
    use_case, adapter, audit, _, _, uow = _use_case(member)
    overrides = {"expected_membership_state": "active"}
    if mismatch == "membership_id":
        overrides["expected_membership_id"] = uuid.uuid4()
    else:
        overrides["expected_user_active"] = False

    with pytest.raises(StaleState):
        use_case.execute(_command(member, "membership.suspend", **overrides))

    assert adapter.mutations == []
    assert len(audit.blocks) == 1
    assert audit.blocks[0][1] == member.membership_id
    assert audit.blocks[0][2] == "access_management.stale_state"
    assert uow.rollbacks == 0
    assert adapter.events[-2:] == ["flush", "commit"]


def test_existing_row_id_aba_precedes_same_desired_direct_noop():
    member = _member()
    use_case, adapter, audit, _, _, _ = _use_case(member)
    current = DirectPermissionSnapshot(uuid.uuid4(), "viewer", NOW)
    adapter.direct = LockedDirectPermission(adapter.resource, current, "none")

    with pytest.raises(StaleState):
        use_case.execute(
            _command(
                member,
                "direct_permission.grant",
                resource_type="workflow",
                resource_id=adapter.resource.resource_id,
                auth_state="viewer",
                expected_permission_id=uuid.uuid4(),
                expected_auth_state="viewer",
            )
        )

    assert adapter.mutations == []
    assert len(audit.blocks) == 1


def test_direct_permission_mutation_locks_subject_before_resource():
    member = _member()
    use_case, adapter, _, _, _, _ = _use_case(member)

    result = use_case.execute(
        _command(
            member,
            "direct_permission.grant",
            resource_type="workflow",
            resource_id=adapter.resource.resource_id,
            auth_state="viewer",
            expected_absent=True,
        )
    )

    assert result.status == "applied"
    assert adapter.events.index("lock.member:False") < adapter.events.index(
        "lock.resource"
    )


def test_workflow_primary_change_rolls_back_and_propagates_stable_error():
    member = _member()
    use_case, adapter, audit, _, _, uow = _use_case(member)

    def _raise_primary_changed(*args, **kwargs):
        adapter.events.append("lock.resource")
        raise WorkflowPrimaryChanged()

    adapter.lock_resource = _raise_primary_changed

    with pytest.raises(WorkflowPrimaryChanged):
        use_case.execute(
            _command(
                member,
                "direct_permission.grant",
                resource_type="workflow",
                resource_id=adapter.resource.resource_id,
                auth_state="viewer",
                expected_auth_state="none",
            )
        )

    assert uow.rollbacks == 1
    assert adapter.mutations == []
    assert audit.mutations == []


@pytest.mark.parametrize(
    "setup,command",
    [
        (
            lambda adapter: None,
            lambda member, adapter: _command(
                member,
                "membership.suspend",
                expected_membership_state="active",
            ),
        ),
        (
            lambda adapter: setattr(
                adapter,
                "team",
                LockedTeamMembership(uuid.uuid4(), True, None, 0),
            ),
            lambda member, adapter: _command(
                member,
                "team_membership.remove",
                team_id=adapter.team.team_id,
                expected_team_membership_id=uuid.uuid4(),
            ),
        ),
    ],
)
def test_contract_defined_noop_rolls_back_read_transaction_without_audit(
    setup,
    command,
):
    member = _member(membership_state="suspended")
    use_case, adapter, audit, _, _, uow = _use_case(member)
    setup(adapter)

    result = use_case.execute(command(member, adapter))

    assert result.status == "unchanged"
    assert audit.mutations == []
    assert audit.blocks == []
    assert uow.rollbacks == 1


def test_expected_absent_same_value_retry_is_unchanged_before_manager_override():
    member = _member(role="manager")
    use_case, adapter, audit, _, _, _ = _use_case(member)
    current = DirectPermissionSnapshot(uuid.uuid4(), "viewer", NOW)
    adapter.direct = LockedDirectPermission(adapter.resource, current, "none")

    result = use_case.execute(
        _command(
            member,
            "direct_permission.grant",
            resource_type="workflow",
            resource_id=adapter.resource.resource_id,
            auth_state="viewer",
            expected_absent=True,
        )
    )

    assert result.status == "unchanged"
    assert audit.blocks == []
    assert adapter.mutations == []


def test_expected_absent_conflicting_direct_value_is_stale():
    member = _member()
    use_case, adapter, audit, _, _, _ = _use_case(member)
    current = DirectPermissionSnapshot(uuid.uuid4(), "operator", NOW)
    adapter.direct = LockedDirectPermission(adapter.resource, current, "none")

    with pytest.raises(StaleState):
        use_case.execute(
            _command(
                member,
                "direct_permission.grant",
                resource_type="workflow",
                resource_id=adapter.resource.resource_id,
                auth_state="viewer",
                expected_absent=True,
            )
        )

    assert audit.blocks[0][2] == "access_management.stale_state"


@pytest.mark.parametrize(
    ("member", "action", "extra", "error_type", "policy_reason"),
    [
        (
            _member(role="manager"),
            "membership.suspend",
            lambda member, adapter: {"actor_id": member.user_id},
            SelfControlForbidden,
            "access_management.self_control_forbidden",
        ),
        (
            _member(role="manager"),
            "membership.suspend",
            lambda member, adapter: {},
            LastActiveManager,
            "access_management.last_active_manager",
        ),
        (
            _member(role="manager"),
            "app_creation.grant",
            lambda member, adapter: {"expected_absent": True},
            ManagerOverrideActive,
            "access_management.manager_override_active",
        ),
        (
            _member(membership_state="suspended"),
            "organization_role.set",
            lambda member, adapter: {"role": "manager"},
            MemberStateNotManageable,
            "access_management.member_state_not_manageable",
        ),
        (
            _member(user_active=False),
            "app_creation.grant",
            lambda member, adapter: {"expected_absent": True},
            TargetUserInactive,
            "access_management.target_user_inactive",
        ),
    ],
)
def test_policy_blocks_are_committed_without_mutation(
    member,
    action,
    extra,
    error_type,
    policy_reason,
):
    use_case, adapter, audit, _, _, _ = _use_case(member)
    if error_type is LastActiveManager:
        adapter.active_manager_count = 1

    with pytest.raises(error_type):
        use_case.execute(_command(member, action, **extra(member, adapter)))

    assert adapter.mutations == []
    assert len(audit.blocks) == 1
    assert audit.blocks[0][2] == policy_reason
    assert adapter.events[-2:] == ["flush", "commit"]


def test_suspended_manager_can_be_demoted_and_global_inactive_permission_can_revoke():
    suspended = _member(membership_state="suspended", role="manager")
    use_case, adapter, audit, _, _, _ = _use_case(suspended)
    demote = use_case.execute(
        _command(suspended, "organization_role.set", role="member")
    )

    inactive = _member(user_active=False)
    use_case, adapter, audit, _, _, _ = _use_case(inactive)
    current = DirectPermissionSnapshot(uuid.uuid4(), "viewer", NOW)
    adapter.direct = LockedDirectPermission(adapter.resource, current, "none")
    revoke = use_case.execute(
        _command(
            inactive,
            "direct_permission.revoke",
            resource_type="workflow",
            resource_id=adapter.resource.resource_id,
            expected_permission_id=current.permission_id,
            expected_auth_state="viewer",
        )
    )

    assert demote.status == "applied"
    assert revoke.status == "applied"
    assert len(audit.mutations) == 1


@pytest.mark.parametrize("action", ["direct_permission.revoke", "app_creation.revoke"])
def test_missing_direct_or_app_revoke_is_hidden_without_actor_audit(action):
    member = _member()
    use_case, adapter, audit, _, _, uow = _use_case(member)
    if action.startswith("direct"):
        command = _command(
            member,
            action,
            resource_type="workflow",
            resource_id=adapter.resource.resource_id,
            expected_permission_id=uuid.uuid4(),
            expected_auth_state="viewer",
        )
    else:
        command = _command(
            member,
            action,
            expected_permission_id=uuid.uuid4(),
        )

    with pytest.raises(ResourceHidden):
        use_case.execute(command)

    assert audit.mutations == []
    assert audit.blocks == []
    assert uow.rollbacks == 1


def test_applied_mutation_audit_flush_and_commit_order_is_atomic():
    member = _member()
    use_case, adapter, audit, _, _, _ = _use_case(member)

    result = use_case.execute(_command(member, "membership.suspend"))

    assert result.status == "applied"
    assert len(audit.mutations) == 1
    assert audit.mutations[0][2] == "review"
    assert adapter.events[-4:] == ["mutation", "audit.mutation", "flush", "commit"]


@pytest.mark.parametrize("failure", ["mutation", "audit", "flush", "commit", "reason"])
def test_any_required_write_or_sanitization_failure_rolls_back(failure):
    member = _member()
    use_case, adapter, audit, _, reason, uow = _use_case(member)
    adapter.raise_on_mutation = failure == "mutation"
    audit.fail_mutation = failure == "audit"
    uow.fail_flush = failure == "flush"
    uow.fail_commit = failure == "commit"
    reason.fail = failure == "reason"

    with pytest.raises(AuditPersistenceFailed):
        use_case.execute(_command(member, "membership.suspend"))

    assert uow.rollbacks == 1


@pytest.mark.parametrize(
    ("action", "overrides"),
    [
        ("team_membership.add", {"expected_absent": True}),
        (
            "direct_permission.grant",
            {"auth_state": "viewer", "expected_absent": True},
        ),
    ],
)
def test_invalid_internal_command_rolls_back_before_adapter_lookup(action, overrides):
    member = _member()
    use_case, adapter, _, _, _, uow = _use_case(member)

    with pytest.raises(InputValidationError):
        use_case.execute(_command(member, action, **overrides))

    assert uow.rollbacks == 1
    assert not any(event in {"lock.team:True", "lock.resource"} for event in adapter.events)


def test_policy_block_audit_failure_returns_persistence_error_without_mutation():
    member = _member(role="manager")
    use_case, adapter, audit, _, _, uow = _use_case(member)
    audit.fail_block = True

    with pytest.raises(AuditPersistenceFailed):
        use_case.execute(
            _command(
                member,
                "membership.suspend",
                actor_id=member.user_id,
            )
        )

    assert adapter.mutations == []
    assert uow.rollbacks == 1
