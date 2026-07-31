import uuid
from datetime import datetime, timezone

import pytest

from apps.gateway.application.access_management.models import (
    AccessActionCommand,
    DirectPermissionSnapshot,
    MemberSnapshot,
    ResourceAccessProjection,
    TeamPermissionSource,
)
from apps.gateway.application.access_management.policies import (
    access_controls,
    action_policy_reason,
    effective_resource_auth_state,
    is_last_active_manager,
    strongest_auth_state,
)


NOW = datetime.now(timezone.utc)


def _member(
    *,
    user_active: bool = True,
    membership_state: str = "active",
    role: str = "member",
    user_id: uuid.UUID | None = None,
) -> MemberSnapshot:
    return MemberSnapshot(
        membership_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=user_id or uuid.uuid4(),
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
    }
    values.update(overrides)
    return AccessActionCommand(**values)


@pytest.mark.parametrize(
    ("member", "count", "expected"),
    [
        (_member(role="manager"), 1, True),
        (_member(role="manager"), 2, False),
        (_member(role="manager", user_active=False), 1, False),
        (_member(role="manager", membership_state="suspended"), 1, False),
        (_member(role="member"), 1, False),
    ],
)
def test_last_active_manager_requires_all_effective_manager_conditions(
    member,
    count,
    expected,
):
    assert is_last_active_manager(member, count) is expected


def test_access_controls_are_directional_for_self_and_last_manager():
    member = _member(role="manager")
    controls = access_controls(member, actor_id=member.user_id, last_active_manager=True)

    assert controls.actions.membership_suspend.reason == "self_control_forbidden"
    assert controls.actions.organization_role_set.member.reason == "self_control_forbidden"
    assert controls.actions.organization_role_set.manager.reason == (
        "member_state_not_applicable"
    )
    assert controls.actions.membership_reactivate.reason == (
        "member_state_not_applicable"
    )
    assert controls.actions.direct_permission_revoke.reason == "manager_override_active"


def test_global_inactive_and_suspended_controls_allow_cleanup_only():
    member = _member(
        user_active=False,
        membership_state="suspended",
        role="manager",
    )
    controls = access_controls(member, actor_id=uuid.uuid4(), last_active_manager=False)

    assert controls.manager_override is False
    assert controls.actions.membership_reactivate.reason == "target_user_inactive"
    assert controls.actions.organization_role_set.member.allowed is True
    assert controls.actions.organization_role_set.manager.reason == (
        "member_state_not_applicable"
    )
    assert controls.actions.team_membership_add.reason == "target_user_inactive"
    assert controls.actions.team_membership_remove.allowed is True
    assert controls.actions.direct_permission_revoke.allowed is True
    assert controls.actions.app_creation_revoke.allowed is True


@pytest.mark.parametrize(
    ("member", "command", "last_manager", "reason"),
    [
        (
            _member(role="manager"),
            lambda member: _command(
                member,
                "membership.suspend",
                actor_id=member.user_id,
            ),
            False,
            "self_control_forbidden",
        ),
        (
            _member(role="manager"),
            lambda member: _command(member, "membership.suspend"),
            True,
            "last_active_manager",
        ),
        (
            _member(role="manager"),
            lambda member: _command(
                member,
                "direct_permission.revoke",
                resource_type="workflow",
                resource_id=uuid.uuid4(),
            ),
            False,
            "manager_override_active",
        ),
        (
            _member(user_active=False),
            lambda member: _command(member, "app_creation.grant"),
            False,
            "target_user_inactive",
        ),
        (
            _member(membership_state="suspended"),
            lambda member: _command(
                member,
                "organization_role.set",
                role="manager",
            ),
            False,
            "member_state_not_manageable",
        ),
        (
            _member(user_active=False, role="manager"),
            lambda member: _command(
                member,
                "organization_role.set",
                role="member",
            ),
            False,
            None,
        ),
        (
            _member(user_active=False),
            lambda member: _command(member, "direct_permission.revoke"),
            False,
            None,
        ),
    ],
)
def test_action_policy_priorities(member, command, last_manager, reason):
    assert (
        action_policy_reason(
            command(member),
            member,
            last_active_manager=last_manager,
        )
        == reason
    )


def test_effective_resource_state_uses_override_and_strongest_source():
    resource_id = uuid.uuid4()
    direct = DirectPermissionSnapshot(uuid.uuid4(), "viewer", NOW)
    team_source = TeamPermissionSource(
        uuid.uuid4(),
        uuid.uuid4(),
        "Builders",
        "builder",
    )
    projection = ResourceAccessProjection(
        resource_type="workflow",
        resource_id=resource_id,
        resource_name="Workflow",
        direct_permission=direct,
        team_sources=(team_source,),
    )

    assert effective_resource_auth_state(_member(), projection) == "builder"
    assert effective_resource_auth_state(_member(role="manager"), projection) == "manager"
    assert (
        effective_resource_auth_state(
            _member(membership_state="suspended"),
            projection,
        )
        == "none"
    )
    assert strongest_auth_state(("viewer", "unknown", "operator")) == "operator"
