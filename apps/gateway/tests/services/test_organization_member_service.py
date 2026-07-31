from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql.operators import eq, in_op, is_

from apps.gateway.services.notification_service import (
    NotificationService,
    publish_notifications_changed,
)
from apps.gateway.services import organization_member_service as member_service_module
from apps.gateway.services.organization_member_service import OrganizationMemberService
from apps.shared.audit.actions import AuditAction
from apps.shared.audit.context import clear_current_metadata, set_current_metadata
from apps.shared.db.models.audit_log import AuditLog
from apps.shared.db.models.organization import Organization
from apps.shared.db.models.organization_membership import (
    ORGANIZATION_AUTH_MANAGER,
    ORGANIZATION_AUTH_MEMBER,
    ORGANIZATION_MEMBERSHIP_ACTIVE,
    ORGANIZATION_MEMBERSHIP_INVITED,
    ORGANIZATION_MEMBERSHIP_REMOVED,
    ORGANIZATION_MEMBERSHIP_SUSPENDED,
    OrganizationMembership,
)
from apps.shared.db.models.team import (
    TeamMembership,
    UserLLMPermission,
    UserMailCredentialPermission,
    UserWorkflowPermission,
)
from apps.shared.db.models.user import User
from apps.shared.db.models.user_app_creation_permission import UserAppCreationPermission
from apps.shared.schemas.organization_membership import (
    OrganizationMemberInviteRequest,
    OrganizationMemberUpdateRequest,
)


@pytest.fixture(autouse=True)
def _disable_notification_publish(monkeypatch):
    monkeypatch.setattr(
        "apps.gateway.services.organization_member_service.publish_notifications_changed",
        lambda _user_id: None,
    )


def test_list_active_organizations_uses_active_memberships_only():
    user = _user()
    active_org = _organization("Active", created_by=user.id)
    invited_org = _organization("Invited", created_by=user.id)
    db = _Db(
        users=[user],
        organizations=[active_org, invited_org],
        memberships=[
            _membership(user, active_org, ORGANIZATION_MEMBERSHIP_ACTIVE),
            _membership(user, invited_org, ORGANIZATION_MEMBERSHIP_INVITED),
        ],
    )

    result = OrganizationMemberService.list_active_organizations(db, user)

    assert [item.id for item in result] == [active_org.id]


def test_list_organization_memberships_uses_active_and_invited_memberships():
    user = _user()
    active_org = _organization("Active", created_by=user.id)
    invited_org = _organization("Invited", created_by=user.id)
    removed_org = _organization("Removed", created_by=user.id)
    db = _Db(
        users=[user],
        organizations=[active_org, invited_org, removed_org],
        memberships=[
            _membership(user, active_org, ORGANIZATION_MEMBERSHIP_ACTIVE),
            _membership(user, invited_org, ORGANIZATION_MEMBERSHIP_INVITED),
            _membership(user, removed_org, ORGANIZATION_MEMBERSHIP_REMOVED),
        ],
    )

    result = OrganizationMemberService.list_organization_memberships(db, user)

    assert [item.id for item in result] == [active_org.id, invited_org.id]
    assert result[0].membership_state == ORGANIZATION_MEMBERSHIP_ACTIVE
    assert result[1].membership_state == ORGANIZATION_MEMBERSHIP_INVITED


def test_notification_service_lists_invited_memberships_only():
    user = _user()
    invited_org = _organization("Invited", created_by=user.id)
    active_org = _organization("Active", created_by=user.id)
    removed_org = _organization("Removed", created_by=user.id)
    db = _Db(
        users=[user],
        organizations=[invited_org, active_org, removed_org],
        memberships=[
            _membership(user, invited_org, ORGANIZATION_MEMBERSHIP_INVITED),
            _membership(user, active_org, ORGANIZATION_MEMBERSHIP_ACTIVE),
            _membership(user, removed_org, ORGANIZATION_MEMBERSHIP_REMOVED),
        ],
    )

    result = NotificationService.list_notifications(db, user.id)

    assert len(result) == 1
    assert result[0].type == "organization.invitation"
    assert result[0].organization_id == invited_org.id
    assert result[0].organization_name == "Invited"


def test_member_list_defaults_to_non_removed_and_filters_state(monkeypatch):
    manager = _user()
    member = _user()
    removed = _user()
    org = _organization("Acme", created_by=manager.id)
    db = _Db(
        users=[manager, member, removed],
        organizations=[org],
        memberships=[
            _membership(manager, org, auth_state=ORGANIZATION_AUTH_MANAGER),
            _membership(member, org, ORGANIZATION_MEMBERSHIP_SUSPENDED),
            _membership(removed, org, ORGANIZATION_MEMBERSHIP_REMOVED),
        ],
    )
    monkeypatch.setattr(
        "apps.gateway.services.organization_member_service.has_organization_manager_permission",
        lambda *args: True,
    )

    default_result = OrganizationMemberService.list_members(db, manager, org.id)
    removed_result = OrganizationMemberService.list_members(
        db,
        manager,
        org.id,
        state=ORGANIZATION_MEMBERSHIP_REMOVED,
    )

    assert {item.user_id for item in default_result} == {manager.id, member.id}
    assert [item.user_id for item in removed_result] == [removed.id]


def test_member_list_uses_current_month_user_usage_across_membership_states(
    monkeypatch,
):
    now = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)
    manager = _user()
    invited = _user()
    suspended = _user()
    removed = _user()
    no_usage = _user()
    foreign_organization = _organization("Other", created_by=manager.id)
    organization = _organization("Acme", created_by=manager.id)
    primary_workflow = SimpleNamespace(id=uuid4(), organization_id=organization.id)
    non_primary_workflow = SimpleNamespace(
        id=uuid4(), organization_id=organization.id
    )

    def usage(
        user_id,
        cost,
        *,
        workflow_id=primary_workflow.id,
        organization_id=organization.id,
        runtime_surface=None,
        status="success",
        provider_usage_operation_id=None,
    ):
        return SimpleNamespace(
            user_id=user_id,
            workflow_id=workflow_id,
            organization_id=organization_id,
            total_cost=Decimal(cost),
            runtime_surface=runtime_surface,
            status=status,
            created_at=now,
            prompt_tokens=1,
            completion_tokens=1,
            provider_usage_operation_id=provider_usage_operation_id,
        )

    operation_id = uuid4()
    provider_usage_operations = [
        SimpleNamespace(
            id=operation_id,
            organization_id=organization.id,
            workflow_id=primary_workflow.id,
            purpose="main_generation",
            state="succeeded",
            provider_started_at=now,
            prompt_tokens=10,
            completion_tokens=5,
            total_cost_microusd=5_000_000,
            execution_subject_kind="user",
            execution_subject_id=manager.id,
            credential_principal_id=invited.id,
        ),
        SimpleNamespace(
            id=uuid4(),
            organization_id=organization.id,
            workflow_id=primary_workflow.id,
            purpose="main_generation",
            state="outcome_unknown",
            provider_started_at=now,
            prompt_tokens=None,
            completion_tokens=None,
            total_cost_microusd=None,
            execution_subject_kind="user",
            execution_subject_id=manager.id,
            credential_principal_id=invited.id,
        ),
    ]

    db = _Db(
        users=[manager, invited, suspended, removed, no_usage],
        organizations=[organization, foreign_organization],
        memberships=[
            _membership(manager, organization, auth_state=ORGANIZATION_AUTH_MANAGER),
            _membership(invited, organization, ORGANIZATION_MEMBERSHIP_INVITED),
            _membership(suspended, organization, ORGANIZATION_MEMBERSHIP_SUSPENDED),
            _membership(removed, organization, ORGANIZATION_MEMBERSHIP_REMOVED),
            _membership(no_usage, organization),
        ],
        apps=[
            SimpleNamespace(
                organization_id=organization.id,
                workflow_id=primary_workflow.id,
            )
        ],
        workflows=[primary_workflow, non_primary_workflow],
        usage_logs=[
            usage(manager.id, "1.25"),
            usage(
                invited.id,
                "5.00",
                provider_usage_operation_id=operation_id,
            ),
            usage(
                invited.id,
                "2.50",
                runtime_surface="agent_builder_intent",
            ),
            usage(suspended.id, "3.00", organization_id=None),
            usage(
                removed.id,
                "4.25",
                runtime_surface="agent_builder_intent",
            ),
            usage(invited.id, "90.00", organization_id=foreign_organization.id),
            usage(invited.id, "91.00", workflow_id=non_primary_workflow.id),
            usage(
                invited.id,
                "92.00",
                runtime_surface="agent_builder_intent",
                status="pending",
            ),
        ],
        provider_usage_operations=provider_usage_operations,
    )
    monkeypatch.setattr(member_service_module, "_now", lambda: now)
    monkeypatch.setattr(
        member_service_module,
        "has_organization_manager_permission",
        lambda *args: True,
    )

    current_members = OrganizationMemberService.list_members(
        db,
        manager,
        organization.id,
    )
    removed_members = OrganizationMemberService.list_members(
        db,
        manager,
        organization.id,
        state=ORGANIZATION_MEMBERSHIP_REMOVED,
    )
    current_by_user = {member.user_id: member for member in current_members}

    assert current_by_user[manager.id].current_month_usage.total_cost == 6.25
    assert current_by_user[manager.id].current_month_usage.agent_builder_cost == 0
    assert current_by_user[manager.id].current_month_usage.usage_data_complete is False
    assert (
        current_by_user[manager.id]
        .current_month_usage.unresolved_provider_call_count
        == 1
    )
    assert current_by_user[invited.id].current_month_usage.total_cost == 2.5
    assert current_by_user[invited.id].current_month_usage.workflow_execution_cost == 0
    assert current_by_user[invited.id].current_month_usage.agent_builder_cost == 2.5
    assert current_by_user[suspended.id].current_month_usage.total_cost == 3.0
    assert current_by_user[no_usage.id].current_month_usage.total_cost == 0
    assert removed_members[0].user_id == removed.id
    assert removed_members[0].current_month_usage.total_cost == 4.25
    assert removed_members[0].current_month_usage.agent_builder_cost == 4.25


def test_invite_is_idempotent_and_reinvite_removed_records_audit(monkeypatch):
    manager = _user()
    target = _user()
    org = _organization("Acme", created_by=manager.id)
    removed_membership = _membership(
        target,
        org,
        ORGANIZATION_MEMBERSHIP_REMOVED,
        accepted_at=_now(),
        removed_at=_now(),
    )
    db = _Db(
        users=[manager, target],
        organizations=[org],
        memberships=[
            _membership(manager, org, auth_state=ORGANIZATION_AUTH_MANAGER),
            removed_membership,
        ],
    )
    monkeypatch.setattr(
        "apps.gateway.services.organization_member_service.has_organization_manager_permission",
        lambda *args: True,
    )

    token = set_current_metadata(
        {"ip": "127.0.0.1", "user_agent": "test-agent", "request_id": "req-test"}
    )
    try:
        response = OrganizationMemberService.invite_member(
            db,
            manager,
            org.id,
            OrganizationMemberInviteRequest(user_id=target.id),
        )
        again = OrganizationMemberService.invite_member(
            db,
            manager,
            org.id,
            OrganizationMemberInviteRequest(user_id=target.id),
        )
    finally:
        clear_current_metadata(token)

    assert response.id == removed_membership.id
    assert response.membership_state == ORGANIZATION_MEMBERSHIP_INVITED
    assert removed_membership.accepted_at is None
    assert removed_membership.removed_at is None
    assert again.id == removed_membership.id
    assert _audit_actions(db) == [AuditAction.ORGANIZATION_INVITE]
    metadata = db.audit_logs[0].audit_metadata
    assert metadata["request_id"] == "req-test"
    assert metadata["ip"] == "127.0.0.1"
    assert metadata["user_agent"] == "test-agent"
    assert metadata["actor"] == {
        "id": str(manager.id),
        "email": manager.email,
        "name": manager.name,
    }
    assert "target_user_email" not in metadata
    assert target.email not in str(metadata)


def test_invite_accept_and_decline_publish_notification_changes(monkeypatch):
    manager = _user()
    invited = _user()
    declined = _user()
    org = _organization("Acme", created_by=manager.id)
    invited_membership = _membership(
        invited,
        org,
        ORGANIZATION_MEMBERSHIP_REMOVED,
        accepted_at=_now(),
        removed_at=_now(),
    )
    declined_membership = _membership(
        declined,
        org,
        ORGANIZATION_MEMBERSHIP_INVITED,
    )
    db = _Db(
        users=[manager, invited, declined],
        organizations=[org],
        memberships=[
            _membership(manager, org, auth_state=ORGANIZATION_AUTH_MANAGER),
            invited_membership,
            declined_membership,
        ],
    )
    published = []
    monkeypatch.setattr(
        "apps.gateway.services.organization_member_service.has_organization_manager_permission",
        lambda *args: True,
    )
    monkeypatch.setattr(
        "apps.gateway.services.organization_member_service.publish_notifications_changed",
        lambda user_id: published.append(user_id),
    )

    OrganizationMemberService.invite_member(
        db,
        manager,
        org.id,
        OrganizationMemberInviteRequest(user_id=invited.id),
    )
    OrganizationMemberService.accept_invitation(db, invited, org.id)
    OrganizationMemberService.decline_invitation(db, declined, org.id)

    assert published == [invited.id, invited.id, declined.id]


def test_accept_update_guards_and_state_transitions(monkeypatch):
    manager = _user()
    target = _user()
    org = _organization("Acme", created_by=manager.id)
    target_membership = _membership(target, org, ORGANIZATION_MEMBERSHIP_INVITED)
    db = _Db(
        users=[manager, target],
        organizations=[org],
        memberships=[
            _membership(manager, org, auth_state=ORGANIZATION_AUTH_MANAGER),
            target_membership,
        ],
    )
    monkeypatch.setattr(
        "apps.gateway.services.organization_member_service.has_organization_manager_permission",
        lambda *args: True,
    )

    with pytest.raises(HTTPException) as forced_accept:
        OrganizationMemberService.update_member(
            db,
            manager,
            org.id,
            target.id,
            OrganizationMemberUpdateRequest(
                membership_state=ORGANIZATION_MEMBERSHIP_ACTIVE
            ),
        )

    accepted = OrganizationMemberService.accept_invitation(db, target, org.id)
    suspended = OrganizationMemberService.update_member(
        db,
        manager,
        org.id,
        target.id,
        OrganizationMemberUpdateRequest(
            membership_state=ORGANIZATION_MEMBERSHIP_SUSPENDED
        ),
    )

    assert forced_accept.value.status_code == 409
    assert accepted.membership_state == ORGANIZATION_MEMBERSHIP_ACTIVE
    assert suspended.membership_state == ORGANIZATION_MEMBERSHIP_SUSPENDED
    assert _audit_actions(db) == [
        AuditAction.ORGANIZATION_MEMBER_ACCEPT,
        AuditAction.ORGANIZATION_MEMBER_UPDATE,
    ]
    update_audit = db.audit_logs[-1]
    assert update_audit.before == {
        "organization_id": str(org.id),
        "user_id": str(target.id),
        "membership_state": ORGANIZATION_MEMBERSHIP_ACTIVE,
        "organization_auth_state": ORGANIZATION_AUTH_MEMBER,
    }
    assert update_audit.after == {
        **update_audit.before,
        "membership_state": ORGANIZATION_MEMBERSHIP_SUSPENDED,
    }


def test_decline_invitation_marks_removed_and_records_audit():
    user = _user()
    org = _organization("Acme", created_by=user.id)
    membership = _membership(user, org, ORGANIZATION_MEMBERSHIP_INVITED)
    db = _Db(users=[user], organizations=[org], memberships=[membership])

    response = OrganizationMemberService.decline_invitation(db, user, org.id)

    assert response.membership_state == ORGANIZATION_MEMBERSHIP_REMOVED
    assert membership.accepted_at is None
    assert membership.removed_at is not None
    assert _audit_actions(db) == [AuditAction.ORGANIZATION_MEMBER_DECLINE]
    assert db.commits == 1


@pytest.mark.parametrize(
    "state",
    [ORGANIZATION_MEMBERSHIP_ACTIVE, ORGANIZATION_MEMBERSHIP_SUSPENDED],
)
def test_decline_rejects_non_invited_membership(state):
    user = _user()
    org = _organization("Acme", created_by=user.id)
    db = _Db(
        users=[user], organizations=[org], memberships=[_membership(user, org, state)]
    )

    with pytest.raises(HTTPException) as conflict:
        OrganizationMemberService.decline_invitation(db, user, org.id)

    assert conflict.value.status_code == 409


def test_decline_missing_invitation_returns_404():
    user = _user()
    org = _organization("Acme", created_by=user.id)
    db = _Db(users=[user], organizations=[org], memberships=[])

    with pytest.raises(HTTPException) as missing:
        OrganizationMemberService.decline_invitation(db, user, org.id)

    assert missing.value.status_code == 404


@pytest.mark.parametrize(
    "update_request",
    [
        OrganizationMemberUpdateRequest(),
        OrganizationMemberUpdateRequest(membership_state=None),
    ],
)
def test_update_rejects_missing_update_fields(monkeypatch, update_request):
    manager = _user()
    target = _user()
    org = _organization("Acme", created_by=manager.id)
    db = _Db(
        users=[manager, target],
        organizations=[org],
        memberships=[
            _membership(manager, org, auth_state=ORGANIZATION_AUTH_MANAGER),
            _membership(target, org),
        ],
    )
    monkeypatch.setattr(
        "apps.gateway.services.organization_member_service.has_organization_manager_permission",
        lambda *args: True,
    )

    with pytest.raises(HTTPException) as missing_fields:
        OrganizationMemberService.update_member(
            db,
            manager,
            org.id,
            target.id,
            update_request,
        )

    assert missing_fields.value.status_code == 400
    assert db.audit_logs == []
    assert db.commits == 0


def test_update_noop_returns_current_member_without_audit(monkeypatch):
    manager = _user()
    target = _user()
    org = _organization("Acme", created_by=manager.id)
    target_membership = _membership(target, org)
    db = _Db(
        users=[manager, target],
        organizations=[org],
        memberships=[
            _membership(manager, org, auth_state=ORGANIZATION_AUTH_MANAGER),
            target_membership,
        ],
    )
    monkeypatch.setattr(
        "apps.gateway.services.organization_member_service.has_organization_manager_permission",
        lambda *args: True,
    )

    response = OrganizationMemberService.update_member(
        db,
        manager,
        org.id,
        target.id,
        OrganizationMemberUpdateRequest(
            membership_state=target_membership.membership_state
        ),
    )

    assert response.id == target_membership.id
    assert response.membership_state == target_membership.membership_state
    assert response.organization_auth_state == target_membership.organization_auth_state
    assert db.audit_logs == []
    assert db.commits == 0


def test_last_manager_and_self_remove_are_blocked(monkeypatch):
    manager = _user()
    other = _user()
    org = _organization("Acme", created_by=manager.id)
    db = _Db(
        users=[manager, other],
        organizations=[org],
        memberships=[
            _membership(manager, org, auth_state=ORGANIZATION_AUTH_MANAGER),
            _membership(other, org),
        ],
    )
    monkeypatch.setattr(
        "apps.gateway.services.organization_member_service.has_organization_manager_permission",
        lambda *args: True,
    )

    with pytest.raises(HTTPException) as self_remove:
        OrganizationMemberService.remove_member(db, manager, org.id, manager.id)
    with pytest.raises(HTTPException) as last_manager:
        OrganizationMemberService.update_member(
            db,
            manager,
            org.id,
            manager.id,
            OrganizationMemberUpdateRequest(
                organization_auth_state=ORGANIZATION_AUTH_MEMBER
            ),
        )

    assert self_remove.value.status_code == 400
    assert last_manager.value.status_code in {400, 409}


def test_last_manager_guard_blocks_demote_by_another_manager(monkeypatch):
    actor = _user()
    target = _user()
    org = _organization("Acme", created_by=actor.id)
    db = _Db(
        users=[actor, target],
        organizations=[org],
        memberships=[
            _membership(actor, org),
            _membership(target, org, auth_state=ORGANIZATION_AUTH_MANAGER),
        ],
    )
    monkeypatch.setattr(
        "apps.gateway.services.organization_member_service.has_organization_manager_permission",
        lambda *args: True,
    )

    with pytest.raises(HTTPException) as last_manager:
        OrganizationMemberService.update_member(
            db,
            actor,
            org.id,
            target.id,
            OrganizationMemberUpdateRequest(
                organization_auth_state=ORGANIZATION_AUTH_MEMBER
            ),
        )

    assert last_manager.value.status_code == 409
    # manager membership 후보와 대응 User row를 같은 순서로 각각 잠근다.
    assert db.for_update_calls == 2
    assert str(db.for_update_order_by_args[0][0]).endswith(
        "organization_memberships.id ASC"
    )
    assert db.for_update_kwargs == [
        {"key_share": True},
        {"key_share": True},
    ]


def test_deactivated_manager_role_does_not_block_cleanup_demotion(monkeypatch):
    actor = _user()
    target = _user()
    target.deactivated_at = datetime.now(timezone.utc)
    org = _organization("Acme", created_by=actor.id)
    target_membership = _membership(
        target,
        org,
        auth_state=ORGANIZATION_AUTH_MANAGER,
    )
    db = _Db(
        users=[actor, target],
        organizations=[org],
        memberships=[
            _membership(actor, org, auth_state=ORGANIZATION_AUTH_MANAGER),
            target_membership,
        ],
    )
    monkeypatch.setattr(
        "apps.gateway.services.organization_member_service.has_organization_manager_permission",
        lambda *args: True,
    )

    result = OrganizationMemberService.update_member(
        db,
        actor,
        org.id,
        target.id,
        OrganizationMemberUpdateRequest(
            organization_auth_state=ORGANIZATION_AUTH_MEMBER
        ),
    )

    assert result.organization_auth_state == ORGANIZATION_AUTH_MEMBER
    assert db.commits == 1


def test_update_member_rolls_back_when_audit_add_fails(monkeypatch):
    actor = _user()
    target = _user()
    org = _organization("Acme", created_by=actor.id)
    target_membership = _membership(target, org)
    db = _Db(
        users=[actor, target],
        organizations=[org],
        memberships=[
            _membership(actor, org, auth_state=ORGANIZATION_AUTH_MANAGER),
            target_membership,
        ],
    )
    db.audit_add_error = RuntimeError("audit unavailable")
    monkeypatch.setattr(
        "apps.gateway.services.organization_member_service.has_organization_manager_permission",
        lambda *args: True,
    )

    with pytest.raises(RuntimeError, match="audit unavailable"):
        OrganizationMemberService.update_member(
            db,
            actor,
            org.id,
            target.id,
            OrganizationMemberUpdateRequest(
                membership_state=ORGANIZATION_MEMBERSHIP_SUSPENDED
            ),
        )

    assert db.commits == 0
    assert db.rollbacks == 1


@pytest.mark.parametrize(
    "update_request",
    [
        OrganizationMemberUpdateRequest(
            membership_state=ORGANIZATION_MEMBERSHIP_ACTIVE
        ),
        OrganizationMemberUpdateRequest(
            membership_state=ORGANIZATION_MEMBERSHIP_SUSPENDED
        ),
        OrganizationMemberUpdateRequest(
            organization_auth_state=ORGANIZATION_AUTH_MANAGER
        ),
    ],
)
def test_removed_member_cannot_be_patched(monkeypatch, update_request):
    manager = _user()
    target = _user()
    org = _organization("Acme", created_by=manager.id)
    db = _Db(
        users=[manager, target],
        organizations=[org],
        memberships=[
            _membership(manager, org, auth_state=ORGANIZATION_AUTH_MANAGER),
            _membership(target, org, ORGANIZATION_MEMBERSHIP_REMOVED),
        ],
    )
    monkeypatch.setattr(
        "apps.gateway.services.organization_member_service.has_organization_manager_permission",
        lambda *args: True,
    )

    with pytest.raises(HTTPException) as conflict:
        OrganizationMemberService.update_member(
            db,
            manager,
            org.id,
            target.id,
            update_request,
        )

    assert conflict.value.status_code == 409


def test_invited_member_cannot_be_suspended_with_patch(monkeypatch):
    manager = _user()
    target = _user()
    org = _organization("Acme", created_by=manager.id)
    db = _Db(
        users=[manager, target],
        organizations=[org],
        memberships=[
            _membership(manager, org, auth_state=ORGANIZATION_AUTH_MANAGER),
            _membership(target, org, ORGANIZATION_MEMBERSHIP_INVITED),
        ],
    )
    monkeypatch.setattr(
        "apps.gateway.services.organization_member_service.has_organization_manager_permission",
        lambda *args: True,
    )

    with pytest.raises(HTTPException) as conflict:
        OrganizationMemberService.update_member(
            db,
            manager,
            org.id,
            target.id,
            OrganizationMemberUpdateRequest(
                membership_state=ORGANIZATION_MEMBERSHIP_SUSPENDED
            ),
        )

    assert conflict.value.status_code == 409


def test_update_rejects_removed_member(monkeypatch):
    manager = _user()
    target = _user()
    org = _organization("Acme", created_by=manager.id)
    db = _Db(
        users=[manager, target],
        organizations=[org],
        memberships=[
            _membership(manager, org, auth_state=ORGANIZATION_AUTH_MANAGER),
            _membership(target, org, ORGANIZATION_MEMBERSHIP_REMOVED),
        ],
    )
    monkeypatch.setattr(
        "apps.gateway.services.organization_member_service.has_organization_manager_permission",
        lambda *args: True,
    )

    with pytest.raises(HTTPException) as conflict:
        OrganizationMemberService.update_member(
            db,
            manager,
            org.id,
            target.id,
            OrganizationMemberUpdateRequest(
                organization_auth_state=ORGANIZATION_AUTH_MANAGER
            ),
        )

    assert conflict.value.status_code == 409


def test_remove_member_soft_removes_and_cleans_permissions(monkeypatch):
    manager = _user()
    target = _user()
    second_manager = _user()
    org = _organization("Acme", created_by=manager.id)
    target_membership = _membership(target, org)
    db = _Db(
        users=[manager, target, second_manager],
        organizations=[org],
        memberships=[
            _membership(manager, org, auth_state=ORGANIZATION_AUTH_MANAGER),
            _membership(second_manager, org, auth_state=ORGANIZATION_AUTH_MANAGER),
            target_membership,
        ],
        team_memberships=[_team_membership(org.id, target.id)],
        workflow_permissions=[_user_workflow_permission(org.id, target.id)],
        llm_permissions=[_user_llm_permission(org.id, target.id)],
        mail_permissions=[_user_mail_permission(org.id, target.id)],
        app_creation_permissions=[_user_app_creation_permission(org.id, target.id)],
    )
    monkeypatch.setattr(
        "apps.gateway.services.organization_member_service.has_organization_manager_permission",
        lambda *args: True,
    )

    token = set_current_metadata(
        {"ip": "127.0.0.1", "user_agent": "test-agent", "request_id": "req-test"}
    )
    try:
        response = OrganizationMemberService.remove_member(
            db, manager, org.id, target.id
        )
    finally:
        clear_current_metadata(token)

    assert response.status == "removed"
    assert response.removed_team_memberships == 1
    assert response.revoked_user_permissions.workflow == 1
    assert response.revoked_user_permissions.llm_credential == 1
    assert response.revoked_user_permissions.mail_credential == 1
    assert response.revoked_user_permissions.app_creation == 1
    assert target_membership.membership_state == ORGANIZATION_MEMBERSHIP_REMOVED
    assert db.team_memberships == []
    assert db.workflow_permissions == []
    assert db.llm_permissions == []
    assert db.mail_permissions == []
    assert db.app_creation_permissions == []
    assert _audit_actions(db) == [
        AuditAction.ORGANIZATION_MEMBER_REMOVE,
        AuditAction.PERMISSION_REVOKE,
    ]
    assert (
        db.audit_logs[1].audit_metadata["cleanup"]["user_app_creation_permissions"] == 1
    )
    assert (
        db.audit_logs[1].audit_metadata["cleanup"]["user_mail_credential_permissions"]
        == 1
    )
    for audit_log in db.audit_logs:
        metadata = audit_log.audit_metadata
        assert metadata["request_id"] == "req-test"
        assert metadata["ip"] == "127.0.0.1"
        assert metadata["user_agent"] == "test-agent"
        assert metadata["actor"] == {
            "id": str(manager.id),
            "email": manager.email,
            "name": manager.name,
        }
        assert "target_user_email" not in metadata
        assert target.email not in str(metadata)


def test_member_removal_rechecks_workflow_scopes_after_wait(monkeypatch):
    organization_id = uuid4()
    user_id = uuid4()
    source_permission = _user_workflow_permission(organization_id, user_id)
    target_permission = _user_workflow_permission(organization_id, user_id)
    db = _Db(workflow_permissions=[source_permission])
    lock_calls = []

    def add_inherited_permission_after_source_lock(
        _db,
        *,
        organization_id,
        workflow_id,
    ):
        lock_calls.append((organization_id, workflow_id))
        if workflow_id == source_permission.workflow_id:
            db.workflow_permissions.append(target_permission)

    monkeypatch.setattr(
        member_service_module,
        "lock_workflow_permission_scope",
        add_inherited_permission_after_source_lock,
    )

    member_service_module._lock_member_workflow_permission_scopes(
        db,
        organization_id=organization_id,
        user_id=user_id,
    )

    assert lock_calls == [
        (organization_id, source_permission.workflow_id),
        (organization_id, target_permission.workflow_id),
    ]


def test_remove_invited_member_publishes_notification_after_commit(monkeypatch):
    manager = _user()
    target = _user()
    org = _organization("Acme", created_by=manager.id)
    db = _Db(
        users=[manager, target],
        organizations=[org],
        memberships=[
            _membership(manager, org, auth_state=ORGANIZATION_AUTH_MANAGER),
            _membership(target, org, ORGANIZATION_MEMBERSHIP_INVITED),
        ],
    )
    events = []
    original_commit = db.commit
    monkeypatch.setattr(
        "apps.gateway.services.organization_member_service.has_organization_manager_permission",
        lambda *args: True,
    )

    def commit():
        original_commit()
        events.append("commit")

    monkeypatch.setattr(db, "commit", commit)
    monkeypatch.setattr(
        "apps.gateway.services.organization_member_service.publish_notifications_changed",
        lambda user_id: events.append(("publish", user_id)),
    )

    response = OrganizationMemberService.remove_member(
        db, manager, org.id, target.id
    )

    assert response.status == "removed"
    assert events == ["commit", ("publish", target.id)]


def test_remove_invited_member_does_not_publish_when_commit_fails(monkeypatch):
    manager = _user()
    target = _user()
    org = _organization("Acme", created_by=manager.id)
    db = _Db(
        users=[manager, target],
        organizations=[org],
        memberships=[
            _membership(manager, org, auth_state=ORGANIZATION_AUTH_MANAGER),
            _membership(target, org, ORGANIZATION_MEMBERSHIP_INVITED),
        ],
    )
    published = []
    monkeypatch.setattr(
        "apps.gateway.services.organization_member_service.has_organization_manager_permission",
        lambda *args: True,
    )

    def fail_commit():
        raise RuntimeError("commit failed")

    monkeypatch.setattr(db, "commit", fail_commit)
    monkeypatch.setattr(
        "apps.gateway.services.organization_member_service.publish_notifications_changed",
        lambda user_id: published.append(user_id),
    )

    with pytest.raises(RuntimeError, match="commit failed"):
        OrganizationMemberService.remove_member(db, manager, org.id, target.id)

    assert published == []
    assert db.rollbacks == 1


@pytest.mark.parametrize(
    "membership_state",
    [
        ORGANIZATION_MEMBERSHIP_ACTIVE,
        ORGANIZATION_MEMBERSHIP_SUSPENDED,
        ORGANIZATION_MEMBERSHIP_REMOVED,
    ],
)
def test_remove_non_invited_member_does_not_publish_notification(
    monkeypatch, membership_state
):
    manager = _user()
    target = _user()
    org = _organization("Acme", created_by=manager.id)
    db = _Db(
        users=[manager, target],
        organizations=[org],
        memberships=[
            _membership(manager, org, auth_state=ORGANIZATION_AUTH_MANAGER),
            _membership(target, org, membership_state),
        ],
    )
    published = []
    monkeypatch.setattr(
        "apps.gateway.services.organization_member_service.has_organization_manager_permission",
        lambda *args: True,
    )
    monkeypatch.setattr(
        "apps.gateway.services.organization_member_service.publish_notifications_changed",
        lambda user_id: published.append(user_id),
    )

    response = OrganizationMemberService.remove_member(
        db, manager, org.id, target.id
    )

    assert response.status == "removed"
    assert published == []


def test_remove_invited_member_keeps_commit_when_notification_publish_fails(
    monkeypatch,
):
    manager = _user()
    target = _user()
    org = _organization("Acme", created_by=manager.id)
    target_membership = _membership(
        target, org, ORGANIZATION_MEMBERSHIP_INVITED
    )
    db = _Db(
        users=[manager, target],
        organizations=[org],
        memberships=[
            _membership(manager, org, auth_state=ORGANIZATION_AUTH_MANAGER),
            target_membership,
        ],
    )
    monkeypatch.setattr(
        "apps.gateway.services.organization_member_service.has_organization_manager_permission",
        lambda *args: True,
    )

    class FailingRedis:
        def publish(self, *_args, **_kwargs):
            raise RuntimeError("redis unavailable")

    monkeypatch.setattr(
        "apps.shared.services.notification_pubsub.get_redis_client",
        lambda: FailingRedis(),
    )
    monkeypatch.setattr(
        "apps.gateway.services.organization_member_service.publish_notifications_changed",
        publish_notifications_changed,
    )

    response = OrganizationMemberService.remove_member(
        db, manager, org.id, target.id
    )

    assert response.status == "removed"
    assert target_membership.membership_state == ORGANIZATION_MEMBERSHIP_REMOVED
    assert db.commits == 1
    assert db.rollbacks == 0


def test_invite_conflict_and_not_found_cases(monkeypatch):
    manager = _user()
    suspended_user = _user()
    deactivated_user = _user()
    deactivated_user.deactivated_at = _now()
    org = _organization("Acme", created_by=manager.id)
    db = _Db(
        users=[manager, suspended_user, deactivated_user],
        organizations=[org],
        memberships=[
            _membership(manager, org, auth_state=ORGANIZATION_AUTH_MANAGER),
            _membership(suspended_user, org, ORGANIZATION_MEMBERSHIP_SUSPENDED),
        ],
    )
    monkeypatch.setattr(
        "apps.gateway.services.organization_member_service.has_organization_manager_permission",
        lambda *args: True,
    )

    def _invite(user_id):
        return OrganizationMemberService.invite_member(
            db, manager, org.id, OrganizationMemberInviteRequest(user_id=user_id)
        )

    with pytest.raises(HTTPException) as suspended:
        _invite(suspended_user.id)
    with pytest.raises(HTTPException) as self_invite:
        _invite(manager.id)
    with pytest.raises(HTTPException) as deactivated:
        _invite(deactivated_user.id)
    with pytest.raises(HTTPException) as missing:
        _invite(uuid4())

    assert suspended.value.status_code == 409
    assert self_invite.value.status_code == 400
    assert deactivated.value.status_code == 404
    assert missing.value.status_code == 404
    assert db.audit_logs == []


def test_invite_conflict_on_flush_returns_409(monkeypatch):
    # 동시 invite로 unique 제약이 깨지면 INSERT(flush) 시점에 IntegrityError가 나고,
    # 이를 409로 변환하면서 audit/commit은 일어나지 않아야 한다.
    manager = _user()
    target = _user()
    org = _organization("Acme", created_by=manager.id)
    db = _Db(
        users=[manager, target],
        organizations=[org],
        memberships=[_membership(manager, org, auth_state=ORGANIZATION_AUTH_MANAGER)],
    )
    monkeypatch.setattr(
        "apps.gateway.services.organization_member_service.has_organization_manager_permission",
        lambda *args: True,
    )

    def _raise_integrity_error():
        raise IntegrityError("INSERT", {}, Exception("duplicate"))

    monkeypatch.setattr(db, "flush", _raise_integrity_error)

    with pytest.raises(HTTPException) as conflict:
        OrganizationMemberService.invite_member(
            db, manager, org.id, OrganizationMemberInviteRequest(user_id=target.id)
        )

    assert conflict.value.status_code == 409
    assert db.audit_logs == []
    assert db.commits == 0


def test_accept_is_idempotent_when_already_active():
    user = _user()
    org = _organization("Acme", created_by=user.id)
    db = _Db(
        users=[user],
        organizations=[org],
        memberships=[_membership(user, org, ORGANIZATION_MEMBERSHIP_ACTIVE)],
    )

    response = OrganizationMemberService.accept_invitation(db, user, org.id)

    assert response.membership_state == ORGANIZATION_MEMBERSHIP_ACTIVE
    assert db.audit_logs == []
    assert db.commits == 0


@pytest.mark.parametrize(
    "state",
    [ORGANIZATION_MEMBERSHIP_SUSPENDED, ORGANIZATION_MEMBERSHIP_REMOVED],
)
def test_accept_rejects_suspended_or_removed(state):
    user = _user()
    org = _organization("Acme", created_by=user.id)
    db = _Db(
        users=[user],
        organizations=[org],
        memberships=[_membership(user, org, state)],
    )

    with pytest.raises(HTTPException) as conflict:
        OrganizationMemberService.accept_invitation(db, user, org.id)

    assert conflict.value.status_code == 409


def test_accept_missing_invitation_returns_404():
    user = _user()
    org = _organization("Acme", created_by=user.id)
    db = _Db(users=[user], organizations=[org], memberships=[])

    with pytest.raises(HTTPException) as missing:
        OrganizationMemberService.accept_invitation(db, user, org.id)

    assert missing.value.status_code == 404


def test_remove_is_idempotent_for_already_removed(monkeypatch):
    manager = _user()
    target = _user()
    org = _organization("Acme", created_by=manager.id)
    db = _Db(
        users=[manager, target],
        organizations=[org],
        memberships=[
            _membership(manager, org, auth_state=ORGANIZATION_AUTH_MANAGER),
            _membership(target, org, ORGANIZATION_MEMBERSHIP_REMOVED),
        ],
    )
    monkeypatch.setattr(
        "apps.gateway.services.organization_member_service.has_organization_manager_permission",
        lambda *args: True,
    )

    response = OrganizationMemberService.remove_member(db, manager, org.id, target.id)

    assert response.status == "removed"
    assert response.removed_team_memberships == 0
    assert response.revoked_user_permissions.workflow == 0
    assert db.audit_logs == []
    assert db.commits == 0


@pytest.mark.parametrize(
    ("scope_access", "expected_status"),
    [(True, 403), (False, 404)],
)
def test_manager_gate_hides_or_forbids_non_manager(
    monkeypatch, scope_access, expected_status
):
    user = _user()
    org = _organization("Acme", created_by=user.id)
    db = _Db(users=[user], organizations=[org], memberships=[])
    events = []
    monkeypatch.setattr(
        "apps.gateway.services.organization_member_service.has_organization_manager_permission",
        lambda *args: False,
    )
    monkeypatch.setattr(
        "apps.gateway.services.organization_member_service.has_organization_scope_access",
        lambda *args: scope_access,
    )
    monkeypatch.setattr(
        "apps.shared.services.permission_audit.record_audit",
        lambda **event: events.append(event),
    )

    token = set_current_metadata(
        {
            "ip": "127.0.0.1",
            "method": "GET",
            "path": f"/api/v1/organizations/{org.id}/members",
            "user_agent": "test-agent",
            "request_id": "req-test",
        }
    )
    try:
        with pytest.raises(HTTPException) as denied:
            OrganizationMemberService.list_members(db, user, org.id)
    finally:
        clear_current_metadata(token)

    assert denied.value.status_code == expected_status
    if scope_access:
        assert getattr(denied.value, "audit_recorded", False) is True
        assert events == [
            {
                "action": AuditAction.PERMISSION_DENIED,
                "category": "action",
                "actor_id": user.id,
                "actor_type": "user",
                "target_type": "organization",
                "target_id": org.id,
                "status": "failure",
                "metadata": {
                    "request_id": "req-test",
                    "organization_id": str(org.id),
                    "policy_result": "deny",
                    "resource_type": "organization",
                    "resource_id": str(org.id),
                    "required_permission": "manage_members",
                    "permission_action": "manage_members",
                    "effective_auth_state": ORGANIZATION_AUTH_MEMBER,
                },
            }
        ]
    else:
        assert getattr(denied.value, "audit_recorded", False) is False
        assert events == []


class _Db:
    def __init__(
        self,
        users=None,
        organizations=None,
        memberships=None,
        team_memberships=None,
        workflow_permissions=None,
        llm_permissions=None,
        mail_permissions=None,
        app_creation_permissions=None,
        apps=None,
        workflows=None,
        usage_logs=None,
        provider_usage_operations=None,
    ):
        self.users = users or []
        self.organizations = organizations or []
        self.memberships = memberships or []
        self.team_memberships = team_memberships or []
        self.workflow_permissions = workflow_permissions or []
        self.llm_permissions = llm_permissions or []
        self.mail_permissions = mail_permissions or []
        self.app_creation_permissions = app_creation_permissions or []
        self.apps = apps or []
        self.workflows = workflows or []
        self.usage_logs = usage_logs or []
        self.provider_usage_operations = provider_usage_operations or []
        self.audit_logs = []
        self.commits = 0
        self.rollbacks = 0
        self.audit_add_error = None
        self.for_update_calls = 0
        self.for_update_order_by_args = []
        self.for_update_kwargs = []
        self.executed_statements = []

    def query(self, *models):
        if models == (Organization, OrganizationMembership):
            rows = [
                (organization, membership)
                for organization in self.organizations
                for membership in self.memberships
                if organization.id == membership.organization_id
            ]
            return _Query(rows, on_for_update=self._record_for_update)
        model = models[0]
        if model is Organization:
            return _Query(self.organizations, on_for_update=self._record_for_update)
        if model is User:
            return _Query(self.users, on_for_update=self._record_for_update)
        if model is OrganizationMembership:
            return _Query(self.memberships, on_for_update=self._record_for_update)
        if model is TeamMembership:
            return _Query(
                self.team_memberships,
                self.team_memberships,
                on_for_update=self._record_for_update,
            )
        if model is UserWorkflowPermission:
            return _Query(
                self.workflow_permissions,
                self.workflow_permissions,
                on_for_update=self._record_for_update,
            )
        if model is UserLLMPermission:
            return _Query(
                self.llm_permissions,
                self.llm_permissions,
                on_for_update=self._record_for_update,
            )
        if model is UserMailCredentialPermission:
            return _Query(
                self.mail_permissions,
                self.mail_permissions,
                on_for_update=self._record_for_update,
            )
        if model is UserAppCreationPermission:
            return _Query(
                self.app_creation_permissions,
                self.app_creation_permissions,
                on_for_update=self._record_for_update,
            )
        return _Query([], on_for_update=self._record_for_update)

    def _record_for_update(self, order_by_args=(), lock_kwargs=None):
        # 실제 DB lock 대신 서비스가 with_for_update()를 호출했는지만 기록한다.
        self.for_update_calls += 1
        self.for_update_order_by_args.append(tuple(order_by_args))
        self.for_update_kwargs.append(dict(lock_kwargs or {}))

    def add(self, row):
        if isinstance(row, AuditLog):
            if self.audit_add_error is not None:
                raise self.audit_add_error
            self.audit_logs.append(row)
        elif isinstance(row, OrganizationMembership):
            self.memberships.append(row)

    def execute(self, statement):
        self.executed_statements.append(statement)

    def flush(self):
        for membership in self.memberships:
            if membership.id is None:
                membership.id = uuid4()

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def refresh(self, row):
        return row


class _Query:
    def __init__(self, items, backing=None, on_for_update=None):
        self.items = list(items)
        self.backing = backing
        self.filters = []
        self.on_for_update = on_for_update
        self.order_by_args = []

    def join(self, *args, **kwargs):
        return self

    def options(self, *args, **kwargs):
        return self

    def filter(self, *expressions):
        self.filters.extend(expressions)
        return self

    def order_by(self, *args):
        self.order_by_args.extend(args)
        return self

    def with_for_update(self, **kwargs):
        if self.on_for_update is not None:
            self.on_for_update(self.order_by_args, kwargs)
        return self

    def all(self):
        return [item for item in self.items if self._matches(item)]

    def first(self):
        return next(iter(self.all()), None)

    def count(self):
        return len(self.all())

    def delete(self, synchronize_session=False):
        matches = self.all()
        if self.backing is not None:
            self.backing[:] = [item for item in self.backing if item not in matches]
        return len(matches)

    def _matches(self, item):
        return all(_matches_expression(item, expression) for expression in self.filters)


def _matches_expression(item, expression):
    if not hasattr(expression, "left"):
        return True
    value = _field_value(item, str(expression.left))
    if expression.operator is eq:
        return value == expression.right.value
    if expression.operator is is_:
        if str(expression.right).lower() == "null":
            return value is None
        return value is (str(expression.right) == "true")
    if expression.operator is in_op:
        return value in expression.right.value
    return True


def _field_value(item, field):
    if isinstance(item, tuple):
        organization, membership = item
        if field.startswith("organization."):
            return _field_value(organization, field)
        return _field_value(membership, field)
    mapping = {
        "organization.id": "id",
        "organization.is_active": "is_active",
        "users.id": "id",
        "users.deactivated_at": "deactivated_at",
        "organization_memberships.user_id": "user_id",
        "organization_memberships.organization_id": "organization_id",
        "organization_memberships.membership_state": "membership_state",
        "organization_memberships.organization_auth_state": "organization_auth_state",
        "team_memberships.grantee_organization_id": "grantee_organization_id",
        "team_memberships.user_id": "user_id",
        "user_workflow_permissions.grantee_organization_id": "grantee_organization_id",
        "user_workflow_permissions.user_id": "user_id",
        "user_llm_permissions.grantee_organization_id": "grantee_organization_id",
        "user_llm_permissions.user_id": "user_id",
        "user_mail_credential_permissions.grantee_organization_id": "grantee_organization_id",
        "user_mail_credential_permissions.user_id": "user_id",
        (
            "user_app_creation_permissions.grantee_organization_id"
        ): "grantee_organization_id",
        "user_app_creation_permissions.user_id": "user_id",
    }
    return getattr(item, mapping[field])


def _user():
    return User(
        id=uuid4(),
        email=f"{uuid4()}@example.com",
        name="User",
        social_provider="local",
    )


def _organization(name, created_by):
    return Organization(
        id=uuid4(),
        name=name,
        created_by=created_by,
        is_active=True,
    )


def _membership(
    user,
    organization,
    membership_state=ORGANIZATION_MEMBERSHIP_ACTIVE,
    auth_state=ORGANIZATION_AUTH_MEMBER,
    accepted_at=None,
    removed_at=None,
):
    membership = OrganizationMembership(
        id=uuid4(),
        organization_id=organization.id,
        user_id=user.id,
        membership_state=membership_state,
        organization_auth_state=auth_state,
        invited_by=organization.created_by,
        invited_at=_now(),
        accepted_at=accepted_at,
        removed_at=removed_at,
        created_at=_now(),
        updated_at=_now(),
    )
    membership.user = user
    return membership


def _team_membership(organization_id, user_id):
    return TeamMembership(
        id=uuid4(),
        grantee_organization_id=organization_id,
        user_id=user_id,
        team_id=uuid4(),
        assigned_by=uuid4(),
    )


def _user_workflow_permission(organization_id, user_id):
    return UserWorkflowPermission(
        id=uuid4(),
        grantee_organization_id=organization_id,
        user_id=user_id,
        workflow_id=uuid4(),
        assigned_by=uuid4(),
        auth_state="viewer",
    )


def _user_llm_permission(organization_id, user_id):
    return UserLLMPermission(
        id=uuid4(),
        grantee_organization_id=organization_id,
        user_id=user_id,
        llm_credential_id=uuid4(),
        assigned_by=uuid4(),
        auth_state="viewer",
    )


def _user_mail_permission(organization_id, user_id):
    return UserMailCredentialPermission(
        id=uuid4(),
        grantee_organization_id=organization_id,
        user_id=user_id,
        mail_credential_id=uuid4(),
        assigned_by=uuid4(),
        auth_state="viewer",
    )


def _user_app_creation_permission(organization_id, user_id):
    return UserAppCreationPermission(
        id=uuid4(),
        grantee_organization_id=organization_id,
        user_id=user_id,
        assigned_by=uuid4(),
    )


def _now():
    return datetime.now(timezone.utc)


def _audit_actions(db):
    return [log.action for log in db.audit_logs]
