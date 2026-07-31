from datetime import datetime, timezone
from uuid import uuid4

import pytest
from apps.shared.db.models.organization_membership import (
    ORGANIZATION_AUTH_MANAGER,
    ORGANIZATION_AUTH_MEMBER,
    ORGANIZATION_MEMBERSHIP_ACTIVE,
    ORGANIZATION_MEMBERSHIP_INVITED,
    ORGANIZATION_MEMBERSHIP_REMOVED,
    ORGANIZATION_MEMBERSHIP_SUSPENDED,
)
from apps.shared.schemas.organization_membership import (
    ORGANIZATION_AUTH_STATES,
    ORGANIZATION_MEMBERSHIP_STATES,
    ORGANIZATION_PATCH_MEMBERSHIP_STATES,
    OrganizationCurrentResponse,
    OrganizationMemberInviteRequest,
    OrganizationMemberRemoveResponse,
    OrganizationMemberResponse,
    OrganizationMemberUpdateRequest,
    OrganizationSummaryResponse,
    RevokedUserPermissionCounts,
)
from pydantic import ValidationError


def test_membership_schema_allowed_state_sets_match_db_contract():
    assert ORGANIZATION_MEMBERSHIP_STATES == {
        "invited",
        "active",
        "suspended",
        "removed",
    }
    assert ORGANIZATION_PATCH_MEMBERSHIP_STATES == {"active", "suspended"}
    assert ORGANIZATION_AUTH_STATES == {"member", "manager"}


def test_invite_request_defaults_to_member_auth_state():
    request = OrganizationMemberInviteRequest(user_id=uuid4())

    assert request.organization_auth_state == ORGANIZATION_AUTH_MEMBER


@pytest.mark.parametrize(
    "auth_state",
    [ORGANIZATION_AUTH_MEMBER, ORGANIZATION_AUTH_MANAGER],
)
def test_invite_request_accepts_allowed_auth_states(auth_state):
    request = OrganizationMemberInviteRequest(
        user_id=uuid4(),
        organization_auth_state=auth_state.upper(),
    )

    assert request.organization_auth_state == auth_state


def test_invite_request_rejects_unknown_auth_state():
    with pytest.raises(ValidationError):
        OrganizationMemberInviteRequest(user_id=uuid4(), organization_auth_state="owner")


def test_invite_request_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        OrganizationMemberInviteRequest(user_id=uuid4(), organization_auth_sate="manager")


@pytest.mark.parametrize(
    "membership_state",
    [ORGANIZATION_MEMBERSHIP_ACTIVE, ORGANIZATION_MEMBERSHIP_SUSPENDED],
)
def test_update_request_accepts_patchable_membership_states(membership_state):
    request = OrganizationMemberUpdateRequest(membership_state=membership_state.upper())

    assert request.membership_state == membership_state


@pytest.mark.parametrize(
    "membership_state",
    [ORGANIZATION_MEMBERSHIP_INVITED, ORGANIZATION_MEMBERSHIP_REMOVED],
)
def test_update_request_rejects_non_patchable_membership_states(membership_state):
    with pytest.raises(ValidationError):
        OrganizationMemberUpdateRequest(membership_state=membership_state)


@pytest.mark.parametrize(
    "auth_state",
    [ORGANIZATION_AUTH_MEMBER, ORGANIZATION_AUTH_MANAGER],
)
def test_update_request_accepts_allowed_auth_states(auth_state):
    request = OrganizationMemberUpdateRequest(organization_auth_state=auth_state.upper())

    assert request.organization_auth_state == auth_state


def test_update_request_rejects_unknown_auth_state():
    with pytest.raises(ValidationError):
        OrganizationMemberUpdateRequest(organization_auth_state="owner")


def test_update_request_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        OrganizationMemberUpdateRequest(organization_auth_sate="manager")


@pytest.mark.parametrize(
    "schema",
    [
        OrganizationMemberResponse,
        OrganizationSummaryResponse,
        OrganizationCurrentResponse,
    ],
)
def test_response_schemas_reject_invalid_state_or_auth_state(schema):
    data = _response_data()

    with pytest.raises(ValidationError):
        schema(**{**data, "membership_state": "deleted"})

    with pytest.raises(ValidationError):
        schema(**{**data, "organization_auth_state": "owner"})


def test_remove_response_defaults_missing_cleanup_counts_to_zero():
    response = OrganizationMemberRemoveResponse(
        status="removed",
        removed_team_memberships=2,
        revoked_user_permissions=RevokedUserPermissionCounts(workflow=1),
    )

    assert response.removed_team_memberships == 2
    assert response.revoked_user_permissions.workflow == 1
    assert response.revoked_user_permissions.llm_credential == 0
    assert response.revoked_user_permissions.app_creation == 0
    assert response.revoked_user_permissions.knowledge_base == 0
    assert response.revoked_user_permissions.audit == 0


def test_remove_response_rejects_negative_cleanup_counts():
    with pytest.raises(ValidationError):
        OrganizationMemberRemoveResponse(
            status="removed",
            removed_team_memberships=-1,
            revoked_user_permissions=RevokedUserPermissionCounts(),
        )

    with pytest.raises(ValidationError):
        RevokedUserPermissionCounts(workflow=-1)

    with pytest.raises(ValidationError):
        RevokedUserPermissionCounts(app_creation=-1)


def _response_data():
    now = datetime.now(timezone.utc)
    return {
        "id": uuid4(),
        "organization_id": uuid4(),
        "user_id": uuid4(),
        "user_email": "member@example.com",
        "user_name": "Member",
        "name": "Org",
        "membership_state": ORGANIZATION_MEMBERSHIP_ACTIVE,
        "organization_auth_state": ORGANIZATION_AUTH_MEMBER,
        "is_active": True,
        "invited_by": uuid4(),
        "invited_at": now,
        "accepted_at": now,
        "removed_at": None,
        "created_at": now,
        "updated_at": now,
    }
