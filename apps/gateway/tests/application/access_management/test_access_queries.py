import uuid
from datetime import datetime, timezone

import pytest

from apps.gateway.application.access_management.errors import (
    PermissionDenied,
    ResourceHidden,
)
from apps.gateway.application.access_management.models import (
    AppCreationPermissionSnapshot,
    DirectPermissionSnapshot,
    InheritedResourceCounts,
    MemberSnapshot,
    Page,
    ResourceAccessProjection,
    TeamMembershipProjection,
    TeamPermissionSource,
)
from apps.gateway.application.access_management.use_cases.get_access_profile import (
    GetAccessProfile,
)
from apps.gateway.application.access_management.use_cases.list_resource_access import (
    ListResourceAccess,
)
from apps.gateway.application.access_management.use_cases.list_team_memberships import (
    ListTeamMemberships,
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


class _Query:
    def __init__(self, member: MemberSnapshot | None) -> None:
        self.member = member
        self.manager = True
        self.scope = True
        self.manager_count = 2
        self.calls: list[str] = []
        self.team_page = Page(total=0, items=())
        self.resource_page = Page(total=0, items=())

    def is_organization_manager(self, actor_id, organization_id):
        self.calls.append("manager")
        return self.manager

    def has_organization_scope(self, actor_id, organization_id):
        self.calls.append("scope")
        return self.scope

    def get_member(self, organization_id, user_id):
        self.calls.append("member")
        return self.member

    def count_active_managers(self, organization_id):
        self.calls.append("manager_count")
        return self.manager_count

    def count_team_memberships(self, organization_id, user_id):
        return 3

    def get_app_creation_permission(self, organization_id, user_id):
        return AppCreationPermissionSnapshot(uuid.uuid4(), NOW)

    def count_direct_permissions(self, organization_id, user_id):
        return 4

    def count_team_permission_sources(self, organization_id, user_id):
        return 5

    def list_team_memberships(
        self,
        organization_id,
        user_id,
        *,
        team_id,
        page,
        limit,
    ):
        self.calls.append(f"teams:{team_id}:{page}:{limit}")
        return self.team_page

    def list_resource_access(
        self,
        organization_id,
        user_id,
        *,
        resource_type,
        resource_id,
        source,
        page,
        limit,
    ):
        self.calls.append(
            f"resources:{resource_type}:{resource_id}:{source}:{page}:{limit}"
        )
        return self.resource_page


class _Denial:
    def __init__(self) -> None:
        self.calls = []

    def record_permission_denied(self, actor_id, organization_id):
        self.calls.append((actor_id, organization_id))


def test_profile_returns_counts_directional_controls_and_stored_app_permission():
    member = _member(role="manager")
    query = _Query(member)
    query.manager_count = 1
    denial = _Denial()

    profile = GetAccessProfile(query, query, denial).execute(
        actor_id=uuid.uuid4(),
        organization_id=member.organization_id,
        target_user_id=member.user_id,
    )

    assert profile.member == member
    assert profile.control.is_last_active_manager is True
    assert profile.control.manager_override is True
    assert profile.team_membership_count == 3
    assert profile.permission_counts.direct == 4
    assert profile.permission_counts.team_inherited == 5
    assert profile.app_creation.effective is True
    assert profile.app_creation.effective_source == "manager_override"
    assert profile.app_creation.direct_permission is not None


def test_globally_inactive_profile_preserves_cleanup_inventory_but_disables_effective_access():
    member = _member(user_active=False, role="manager")
    query = _Query(member)

    profile = GetAccessProfile(query, query, _Denial()).execute(
        actor_id=uuid.uuid4(),
        organization_id=member.organization_id,
        target_user_id=member.user_id,
    )

    assert profile.effective_access_enabled is False
    assert profile.control.manager_override is False
    assert profile.app_creation.effective is False
    assert profile.app_creation.effective_source == "none"
    assert profile.app_creation.direct_permission is not None


def test_query_authorization_hides_out_of_scope_and_denies_in_scope_before_target_lookup():
    member = _member()
    query = _Query(member)
    query.manager = False
    query.scope = False
    denial = _Denial()

    with pytest.raises(ResourceHidden):
        GetAccessProfile(query, query, denial).execute(
            actor_id=uuid.uuid4(),
            organization_id=member.organization_id,
            target_user_id=member.user_id,
        )
    assert query.calls == ["manager", "scope"]
    assert denial.calls == []

    query.calls.clear()
    query.scope = True
    with pytest.raises(PermissionDenied):
        GetAccessProfile(query, query, denial).execute(
            actor_id=uuid.uuid4(),
            organization_id=member.organization_id,
            target_user_id=member.user_id,
        )
    assert query.calls == ["manager", "scope"]
    assert len(denial.calls) == 1


@pytest.mark.parametrize("use_case_type", [GetAccessProfile, ListTeamMemberships, ListResourceAccess])
def test_missing_or_ineligible_target_is_hidden(use_case_type):
    query = _Query(None)
    denial = _Denial()
    arguments = {
        "actor_id": uuid.uuid4(),
        "organization_id": uuid.uuid4(),
        "target_user_id": uuid.uuid4(),
    }
    if use_case_type is ListTeamMemberships:
        arguments.update(page=1, limit=20)
    elif use_case_type is ListResourceAccess:
        arguments.update(
            resource_type="workflow",
            source="all",
            page=1,
            limit=20,
        )

    with pytest.raises(ResourceHidden):
        use_case_type(query, query, denial).execute(**arguments)


def test_team_list_delegates_pagination_after_member_scope_check():
    member = _member(membership_state="suspended")
    query = _Query(member)
    item = TeamMembershipProjection(
        team_membership_id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        name="Inactive team",
        is_active=False,
        assigned_at=NOW,
        inherited_resource_counts=InheritedResourceCounts(0, 0, 0),
    )
    query.team_page = Page(total=1, items=(item,))

    result = ListTeamMemberships(query, query, _Denial()).execute(
        actor_id=uuid.uuid4(),
        organization_id=member.organization_id,
        target_user_id=member.user_id,
        team_id=None,
        page=2,
        limit=10,
    )

    assert result.items == (item,)
    assert query.calls[-1] == "teams:None:2:10"


def test_team_list_delegates_exact_team_filter():
    member = _member()
    team_id = uuid.uuid4()
    query = _Query(member)

    result = ListTeamMemberships(query, query, _Denial()).execute(
        actor_id=uuid.uuid4(),
        organization_id=member.organization_id,
        target_user_id=member.user_id,
        team_id=team_id,
        page=1,
        limit=1,
    )

    assert result.total == 0
    assert query.calls[-1] == f"teams:{team_id}:1:1"


@pytest.mark.parametrize(
    ("member", "expected_state"),
    [
        (_member(), "builder"),
        (_member(role="manager"), "manager"),
        (_member(membership_state="suspended"), "none"),
        (_member(user_active=False), "none"),
    ],
)
def test_resource_list_computes_effective_state_from_stored_sources(
    member,
    expected_state,
):
    query = _Query(member)
    item = ResourceAccessProjection(
        resource_type="workflow",
        resource_id=uuid.uuid4(),
        resource_name="Workflow",
        direct_permission=DirectPermissionSnapshot(uuid.uuid4(), "viewer", NOW),
        team_sources=(
            TeamPermissionSource(
                uuid.uuid4(),
                uuid.uuid4(),
                "Builders",
                "builder",
            ),
        ),
    )
    query.resource_page = Page(total=1, items=(item,))

    result = ListResourceAccess(query, query, _Denial()).execute(
        actor_id=uuid.uuid4(),
        organization_id=member.organization_id,
        target_user_id=member.user_id,
        resource_type="workflow",
        resource_id=None,
        source="team",
        page=3,
        limit=5,
    )

    assert result.items[0].effective_auth_state == expected_state
    assert query.calls[-1] == "resources:workflow:None:team:3:5"


def test_resource_list_delegates_exact_resource_filter():
    member = _member()
    resource_id = uuid.uuid4()
    query = _Query(member)
    query.resource_page = Page(total=0, items=())

    result = ListResourceAccess(query, query, _Denial()).execute(
        actor_id=uuid.uuid4(),
        organization_id=member.organization_id,
        target_user_id=member.user_id,
        resource_type="knowledge_base",
        resource_id=resource_id,
        source="all",
        page=1,
        limit=1,
    )

    assert result.total == 0
    assert query.calls[-1] == (
        f"resources:knowledge_base:{resource_id}:all:1:1"
    )
