import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from apps.gateway.api.deps import get_access_management_application
from apps.gateway.application.access_management.errors import (
    AuditPersistenceFailed,
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
    AccessActionResult,
    AccessControl,
    AppCreationAccess,
    AppCreationPermissionSnapshot,
    DirectPermissionSnapshot,
    InheritedResourceCounts,
    MemberAccessControlSummary,
    MemberAccessControls,
    MemberAccessProfile,
    MemberResourceAccess,
    MemberSnapshot,
    OrganizationRoleControls,
    Page,
    PermissionCounts,
    TeamMembershipProjection,
    TeamPermissionSource,
)
from apps.gateway.auth.dependencies import get_current_user
from apps.gateway.main import app


NOW = datetime(2026, 7, 10, tzinfo=timezone.utc)


class _UseCase:
    def __init__(self, result=None, error=None) -> None:
        self.result = result
        self.error = error
        self.calls = []

    def execute(self, *args, **kwargs):
        self.calls.append(args[0] if args else kwargs)
        if self.error is not None:
            raise self.error
        return self.result


def _application(*, profile=None, teams=None, resources=None, action=None):
    return SimpleNamespace(
        get_access_profile=profile or _UseCase(),
        list_team_memberships=teams or _UseCase(),
        list_resource_access=resources or _UseCase(),
        execute_access_action=action or _UseCase(),
    )


@pytest.fixture(autouse=True)
def _reset_overrides():
    app.dependency_overrides = {}
    yield
    app.dependency_overrides = {}


def _client(application, *, actor_id=None):
    actor_id = actor_id or uuid.uuid4()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=actor_id,
        email="manager@example.invalid",
        name="Manager",
    )
    app.dependency_overrides[get_access_management_application] = lambda: application
    return TestClient(app), actor_id


def _member(organization_id=None, user_id=None):
    return MemberSnapshot(
        membership_id=uuid.uuid4(),
        organization_id=organization_id or uuid.uuid4(),
        user_id=user_id or uuid.uuid4(),
        name="Member",
        email="member@example.invalid",
        user_active=True,
        membership_state="active",
        organization_auth_state="member",
        updated_at=NOW,
    )


def _profile(member):
    allowed = AccessControl(True)
    not_applicable = AccessControl(False, "member_state_not_applicable")
    return MemberAccessProfile(
        member=member,
        control=MemberAccessControlSummary(
            is_self=False,
            is_last_active_manager=False,
            manager_override=False,
            actions=MemberAccessControls(
                membership_suspend=allowed,
                membership_reactivate=not_applicable,
                organization_role_set=OrganizationRoleControls(
                    member=not_applicable,
                    manager=allowed,
                ),
                team_membership_add=allowed,
                team_membership_remove=allowed,
                direct_permission_grant=allowed,
                direct_permission_revoke=allowed,
                app_creation_grant=allowed,
                app_creation_revoke=allowed,
            ),
        ),
        effective_access_enabled=True,
        team_membership_count=1,
        app_creation=AppCreationAccess(
            effective=True,
            effective_source="direct",
            direct_permission=AppCreationPermissionSnapshot(uuid.uuid4(), NOW),
        ),
        permission_counts=PermissionCounts(direct=2, team_inherited=3),
    )


def test_access_profile_serializes_application_result_and_path_scope():
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    use_case = _UseCase(_profile(_member(organization_id, user_id)))
    client, actor_id = _client(_application(profile=use_case))

    response = client.get(
        f"/api/v1/organizations/{organization_id}/members/{user_id}/access-profile",
        headers={"X-Organization-Id": str(organization_id)},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["member"]["user_id"] == str(user_id)
    assert body["permission_counts"] == {"direct": 2, "team_inherited": 3}
    assert use_case.calls == [
        {
            "actor_id": actor_id,
            "organization_id": organization_id,
            "target_user_id": user_id,
        }
    ]


def test_team_and_resource_list_preserve_aliases_pagination_and_source_detail():
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    team_id = uuid.uuid4()
    membership_id = uuid.uuid4()
    team_use_case = _UseCase(
        Page(
            total=1,
            items=(
                TeamMembershipProjection(
                    membership_id,
                    team_id,
                    "Builders",
                    True,
                    NOW,
                    InheritedResourceCounts(2, 1, 0),
                ),
            ),
        )
    )
    resource_id = uuid.uuid4()
    permission_id = uuid.uuid4()
    resource_use_case = _UseCase(
        Page(
            total=1,
            items=(
                MemberResourceAccess(
                    "workflow",
                    resource_id,
                    "Workflow",
                    "builder",
                    DirectPermissionSnapshot(permission_id, "viewer", NOW),
                    (
                        TeamPermissionSource(
                            membership_id,
                            team_id,
                            "Builders",
                            "builder",
                        ),
                    ),
                ),
            ),
        )
    )
    client, _ = _client(
        _application(teams=team_use_case, resources=resource_use_case)
    )
    headers = {"X-Organization-Id": str(organization_id)}

    teams = client.get(
        f"/api/v1/organizations/{organization_id}/members/{user_id}/team-memberships"
        f"?teamId={team_id}&page=2&limit=5",
        headers=headers,
    )
    resources = client.get(
        f"/api/v1/organizations/{organization_id}/members/{user_id}/resource-access"
        f"?resourceType=workflow&resourceId={resource_id}"
        "&source=team&page=3&limit=7",
        headers=headers,
    )

    assert teams.status_code == 200
    assert teams.json()["items"][0]["inherited_resource_counts"]["total"] == 3
    assert team_use_case.calls[0]["page"] == 2
    assert team_use_case.calls[0]["team_id"] == team_id
    assert resources.status_code == 200
    assert resources.json()["items"][0]["team_sources"][0] == {
        "team_membership_id": str(membership_id),
        "team_id": str(team_id),
        "team_name": "Builders",
        "auth_state": "builder",
    }
    assert resource_use_case.calls[0]["resource_type"] == "workflow"
    assert resource_use_case.calls[0]["resource_id"] == resource_id
    assert resource_use_case.calls[0]["source"] == "team"
    assert resource_use_case.calls[0]["page"] == 3
    assert resource_use_case.calls[0]["limit"] == 7


def test_action_builds_command_from_authenticated_actor_and_path_not_body():
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    membership_id = uuid.uuid4()
    target_id = uuid.uuid4()
    use_case = _UseCase(
        AccessActionResult(
            status="applied",
            action="membership.suspend",
            target_type="organization_membership",
            target_id=target_id,
            effective_access_changed=True,
            affected_resource_source_count=None,
        )
    )
    client, actor_id = _client(_application(action=use_case))

    response = client.post(
        f"/api/v1/organizations/{organization_id}/members/{user_id}/access-actions",
        headers={"X-Organization-Id": str(organization_id)},
        json={
            "action": "membership.suspend",
            "expected_membership_id": str(membership_id),
            "expected_user_active": True,
            "expected_membership_state": "active",
            "expected_organization_auth_state": "member",
            "reason": "  review  ",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "applied"
    command = use_case.calls[0]
    assert command.actor_id == actor_id
    assert command.organization_id == organization_id
    assert command.target_user_id == user_id
    assert command.reason == "review"


def test_direct_permission_action_maps_primary_change_to_retryable_conflict():
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    use_case = _UseCase(error=WorkflowPrimaryChanged())
    client, _ = _client(_application(action=use_case))

    response = client.post(
        f"/api/v1/organizations/{organization_id}/members/{user_id}/access-actions",
        headers={"X-Organization-Id": str(organization_id)},
        json={
            "action": "direct_permission.grant",
            "expected_membership_id": str(uuid.uuid4()),
            "expected_user_active": True,
            "expected_membership_state": "active",
            "expected_organization_auth_state": "member",
            "resource_type": "workflow",
            "resource_id": str(uuid.uuid4()),
            "auth_state": "viewer",
            "expected_absent": True,
        },
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "workflow.primary_changed"


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (SelfControlForbidden(), 400, "self_control_forbidden"),
        (PermissionDenied(), 403, "permission.denied"),
        (ResourceHidden(), 404, "resource.not_found"),
        (LastActiveManager(), 409, "last_active_manager"),
        (ManagerOverrideActive(), 409, "manager_override_active"),
        (MemberStateNotManageable(), 409, "member_state_not_manageable"),
        (TargetUserInactive(), 409, "target_user_inactive"),
        (StaleState(), 409, "stale_state"),
        (WorkflowPrimaryChanged(), 409, "workflow.primary_changed"),
        (AuditPersistenceFailed(), 500, "audit.persistence_failed"),
    ],
)
def test_application_errors_map_to_stable_api_envelope(error, status, code):
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    use_case = _UseCase(error=error)
    client, _ = _client(_application(profile=use_case))

    with patch("apps.gateway.main.record_audit") as fallback_audit:
        response = client.get(
            f"/api/v1/organizations/{organization_id}/members/{user_id}/access-profile",
            headers={"X-Organization-Id": str(organization_id)},
        )

    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    if isinstance(error, PermissionDenied):
        fallback_audit.assert_not_called()


@pytest.mark.parametrize(
    ("headers", "status", "code"),
    [
        ({}, 400, "organization.required"),
        ({"X-Organization-Id": "not-a-uuid"}, 422, "validation.failed"),
        ({"X-Organization-Id": str(uuid.uuid4())}, 404, "resource.not_found"),
    ],
)
def test_header_validation_precedes_application_call(headers, status, code):
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    use_case = _UseCase(_profile(_member(organization_id, user_id)))
    client, _ = _client(_application(profile=use_case))

    response = client.get(
        f"/api/v1/organizations/{organization_id}/members/{user_id}/access-profile",
        headers=headers,
    )

    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert use_case.calls == []


@pytest.mark.parametrize(
    "payload",
    [
        {
            "action": "membership.suspend",
            "expected_membership_id": "not-a-uuid",
            "expected_user_active": True,
            "expected_membership_state": "active",
            "expected_organization_auth_state": "member",
        },
        {
            "action": "membership.suspend",
            "expected_membership_id": str(uuid.uuid4()),
            "expected_user_active": True,
            "expected_membership_state": "active",
            "expected_organization_auth_state": "member",
            "team_id": str(uuid.uuid4()),
        },
        {
            "action": "direct_permission.grant",
            "expected_membership_id": str(uuid.uuid4()),
            "expected_user_active": True,
            "expected_membership_state": "active",
            "expected_organization_auth_state": "member",
            "resource_type": "workflow",
            "resource_id": str(uuid.uuid4()),
            "auth_state": "none",
            "expected_absent": True,
        },
    ],
)
def test_invalid_discriminated_action_is_422_without_use_case_call(payload):
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    use_case = _UseCase()
    client, _ = _client(_application(action=use_case))

    response = client.post(
        f"/api/v1/organizations/{organization_id}/members/{user_id}/access-actions",
        headers={"X-Organization-Id": str(organization_id)},
        json=payload,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation.failed"
    assert use_case.calls == []


def test_resource_query_rejects_unknown_filter_and_out_of_range_pagination():
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    use_case = _UseCase()
    client, _ = _client(_application(resources=use_case))
    headers = {"X-Organization-Id": str(organization_id)}

    invalid_type = client.get(
        f"/api/v1/organizations/{organization_id}/members/{user_id}/resource-access"
        "?resourceType=unknown",
        headers=headers,
    )
    invalid_limit = client.get(
        f"/api/v1/organizations/{organization_id}/members/{user_id}/resource-access"
        "?resourceType=workflow&limit=101",
        headers=headers,
    )

    assert invalid_type.status_code == 422
    assert invalid_limit.status_code == 422
    assert use_case.calls == []
