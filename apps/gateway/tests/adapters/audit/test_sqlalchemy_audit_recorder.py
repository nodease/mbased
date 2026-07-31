import uuid
from types import SimpleNamespace

import pytest

from apps.gateway.adapters.audit.sqlalchemy_audit_recorder import (
    SqlAlchemyAccessManagementAuditRecorder,
)
from apps.gateway.application.access_management.models import (
    AccessActionCommand,
    AppliedMutationDescriptor,
)
from apps.shared.audit.context import clear_current_metadata, set_current_metadata


class _Session:
    def __init__(self) -> None:
        self.added = []

    def add(self, value):
        self.added.append(value)


def _command(**overrides) -> AccessActionCommand:
    values = {
        "actor_id": uuid.uuid4(),
        "organization_id": uuid.uuid4(),
        "target_user_id": uuid.uuid4(),
        "action": "direct_permission.grant",
        "expected_membership_id": uuid.uuid4(),
        "expected_user_active": True,
        "expected_membership_state": "active",
        "expected_organization_auth_state": "member",
        "resource_type": "workflow",
        "resource_id": uuid.uuid4(),
        "auth_state": "viewer",
        "expected_absent": True,
    }
    values.update(overrides)
    return AccessActionCommand(**values)


def test_mutation_audit_uses_descriptor_safe_snapshot_and_request_metadata():
    session = _Session()
    actor = SimpleNamespace(id=uuid.uuid4(), email="actor@example.invalid", name="Actor")
    command = _command(actor_id=actor.id)
    descriptor = AppliedMutationDescriptor(
        action="user_workflow_permission.created",
        category="data_change",
        target_type="user_workflow_permission",
        target_id=uuid.uuid4(),
        before=None,
        after={
            "grantee_organization_id": command.organization_id,
            "workflow_id": command.resource_id,
            "user_id": command.target_user_id,
            "auth_state": "viewer",
        },
        effective_access_changed=True,
    )
    token = set_current_metadata({"request_id": "request-1", "ip": "127.0.0.1"})
    try:
        SqlAlchemyAccessManagementAuditRecorder(session, actor=actor).record_mutation(
            descriptor,
            command,
            "approved",
        )
    finally:
        clear_current_metadata(token)

    row = session.added[0]
    assert row.action == descriptor.action
    assert row.target_id == str(descriptor.target_id)
    assert row.before is None
    assert row.after == {
        "grantee_organization_id": str(command.organization_id),
        "workflow_id": str(command.resource_id),
        "user_id": str(command.target_user_id),
        "auth_state": "viewer",
    }
    assert row.audit_metadata["organization_id"] == str(command.organization_id)
    assert row.audit_metadata["target_user_id"] == str(command.target_user_id)
    assert row.audit_metadata["reason"] == "approved"
    assert row.audit_metadata["request_id"] == "request-1"
    assert row.audit_metadata["actor"] == {
        "id": str(actor.id),
        "email": actor.email,
        "name": actor.name,
    }


def test_policy_block_has_exact_target_and_safe_scope_metadata():
    session = _Session()
    actor = SimpleNamespace(id=uuid.uuid4(), email=None, name=None)
    command = _command(actor_id=actor.id, team_id=uuid.uuid4())
    membership_id = uuid.uuid4()

    SqlAlchemyAccessManagementAuditRecorder(session, actor=actor).record_policy_block(
        command,
        membership_id=membership_id,
        policy_reason="access_management.manager_override_active",
        reason=None,
    )

    row = session.added[0]
    assert row.action == "policy.block"
    assert row.status == "failure"
    assert row.target_type == "organization_membership"
    assert row.target_id == str(membership_id)
    assert row.before is None and row.after is None
    assert row.audit_metadata["target_user_id"] == str(command.target_user_id)
    assert row.audit_metadata["requested_action"] == command.action
    assert row.audit_metadata["policy_reason"] == (
        "access_management.manager_override_active"
    )
    assert row.audit_metadata["resource_type"] == "workflow"
    assert row.audit_metadata["resource_id"] == str(command.resource_id)
    assert row.audit_metadata["team_id"] == str(command.team_id)
    assert "expected_membership_id" not in row.audit_metadata


def test_policy_block_rejects_unknown_machine_reason_without_writing():
    session = _Session()
    actor = SimpleNamespace(id=uuid.uuid4())

    with pytest.raises(ValueError, match="Unsupported"):
        SqlAlchemyAccessManagementAuditRecorder(session, actor=actor).record_policy_block(
            _command(actor_id=actor.id),
            membership_id=uuid.uuid4(),
            policy_reason="raw exception text",
            reason=None,
        )

    assert session.added == []


@pytest.mark.parametrize(
    "policy_reason",
    [
        "access_management.self_control_forbidden",
        "access_management.last_active_manager",
        "access_management.manager_override_active",
        "access_management.member_state_not_manageable",
        "access_management.target_user_inactive",
        "access_management.stale_state",
    ],
)
def test_policy_block_accepts_all_canonical_access_management_reasons(
    policy_reason,
):
    session = _Session()
    actor = SimpleNamespace(id=uuid.uuid4(), email=None, name=None)
    command = _command(actor_id=actor.id)

    SqlAlchemyAccessManagementAuditRecorder(session, actor=actor).record_policy_block(
        command,
        membership_id=uuid.uuid4(),
        policy_reason=policy_reason,
        reason=None,
    )

    assert len(session.added) == 1
    row = session.added[0]
    assert row.action == "policy.block"
    assert row.category == "action"
    assert row.status == "failure"
    assert row.audit_metadata["policy_reason"] == policy_reason


def test_policy_block_excludes_actor_contact_and_request_network_metadata():
    session = _Session()
    actor = SimpleNamespace(
        id=uuid.uuid4(),
        email="synthetic-secret@example.invalid",
        name="Synthetic Secret Actor",
    )
    command = _command(actor_id=actor.id)
    token = set_current_metadata(
        {
            "request_id": "request-safe",
            "ip": "203.0.113.99",
            "user_agent": "synthetic-secret-agent",
        }
    )
    try:
        SqlAlchemyAccessManagementAuditRecorder(
            session,
            actor=actor,
        ).record_policy_block(
            command,
            membership_id=uuid.uuid4(),
            policy_reason="access_management.self_control_forbidden",
            reason=None,
        )
    finally:
        clear_current_metadata(token)

    metadata = session.added[0].audit_metadata
    assert metadata["request_id"] == "request-safe"
    assert metadata["actor"] == {"id": str(actor.id)}
    assert "ip" not in metadata
    assert "user_agent" not in metadata
    assert actor.email not in repr(metadata)
    assert actor.name not in repr(metadata)
    assert "203.0.113.99" not in repr(metadata)
    assert "synthetic-secret-agent" not in repr(metadata)
