import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.dialects import postgresql

from apps.gateway.adapters.db.access_management_mutation_adapter import (
    SqlAlchemyAccessManagementMutationAdapter,
)
from apps.gateway.application.access_management.errors import WorkflowPrimaryChanged
from apps.gateway.application.access_management.models import (
    MemberSnapshot,
    ResourceDescriptor,
)
from apps.gateway.services.app_lifecycle_lock import (
    AppPrimaryChangedDuringMutationError,
)
from apps.gateway.services.resource_permission_registry import resource_permission_spec
from apps.shared.audit.manual_ownership import is_manually_audited
from apps.shared.db.models.app import App
from apps.shared.db.models.organization_membership import OrganizationMembership
from apps.shared.db.models.team import (
    Team,
    TeamMailCredentialPermission,
    UserWorkflowPermission,
)
from apps.shared.db.models.mail_credential import MailCredential
from apps.shared.db.models.user import User
from apps.shared.db.models.user_app_creation_permission import UserAppCreationPermission
from apps.shared.db.models.workflow import Workflow


NOW = datetime.now(timezone.utc)


class _MutationSession:
    def __init__(self) -> None:
        self.info = {}
        self.added = []
        self.deleted = []

    def add(self, value):
        self.added.append(value)

    def delete(self, value):
        self.deleted.append(value)


def _member(*, role="member", state="active", user_active=True):
    return MemberSnapshot(
        membership_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        name="Member",
        email="member@example.invalid",
        user_active=user_active,
        membership_state=state,
        organization_auth_state=role,
        updated_at=NOW,
    )


def _prepared_adapter(member=None):
    member = member or _member()
    session = _MutationSession()
    adapter = SqlAlchemyAccessManagementMutationAdapter(session)
    membership = OrganizationMembership(
        id=member.membership_id,
        organization_id=member.organization_id,
        user_id=member.user_id,
        membership_state=member.membership_state,
        organization_auth_state=member.organization_auth_state,
        updated_at=member.updated_at,
    )
    user = User(
        id=member.user_id,
        email=member.email,
        name=member.name,
        social_provider="local",
        deactivated_at=None if member.user_active else NOW,
    )
    adapter._membership = membership
    adapter._user = user
    adapter._member_snapshot_value = member
    return adapter, session, membership, user


def test_membership_update_descriptor_is_complete_and_does_not_expose_user_profile():
    member = _member()
    adapter, _, membership, _ = _prepared_adapter(member)

    descriptor = adapter.set_membership_state("suspended")

    assert membership.membership_state == "suspended"
    assert descriptor.action == "organization.member.update"
    assert descriptor.before == {
        "organization_id": member.organization_id,
        "user_id": member.user_id,
        "membership_state": "active",
        "organization_auth_state": "member",
    }
    assert descriptor.after == {
        "organization_id": member.organization_id,
        "user_id": member.user_id,
        "membership_state": "suspended",
        "organization_auth_state": "member",
    }
    assert "email" not in descriptor.after
    assert descriptor.effective_access_changed is True


def test_team_create_and_delete_register_exact_manual_listener_ownership():
    member = _member()
    adapter, session, _, _ = _prepared_adapter(member)
    team = Team(
        id=uuid.uuid4(),
        organization_id=member.organization_id,
        name="Builders",
        created_by=uuid.uuid4(),
        is_active=True,
    )
    adapter._team = team
    adapter._team_source_count = 4

    created = adapter.add_team_membership(uuid.uuid4())
    row = session.added[0]

    assert is_manually_audited(session, row, "created")
    assert created.after == {
        "grantee_organization_id": member.organization_id,
        "team_id": team.id,
        "user_id": member.user_id,
    }
    assert created.affected_resource_source_count == 4

    adapter._team_membership = row
    deleted = adapter.remove_team_membership()
    assert session.deleted == [row]
    assert is_manually_audited(session, row, "deleted")
    assert deleted.before == created.after


def test_direct_permission_create_uses_manual_ownership_and_stronger_team_source():
    member = _member()
    adapter, session, _, _ = _prepared_adapter(member)
    resource = ResourceDescriptor("workflow", uuid.uuid4(), "Workflow")
    adapter._resource = resource
    adapter._direct_model = UserWorkflowPermission
    adapter._direct_route = resource_permission_spec("workflow").user_route
    adapter._strongest_team_state = "builder"

    descriptor = adapter.grant_direct_permission(uuid.uuid4(), "viewer")
    row = session.added[0]

    assert is_manually_audited(session, row, "created")
    assert descriptor.action == "user_workflow_permission.created"
    assert descriptor.before is None
    assert descriptor.after == {
        "grantee_organization_id": member.organization_id,
        "user_id": member.user_id,
        "workflow_id": resource.resource_id,
        "auth_state": "viewer",
    }
    assert descriptor.effective_access_changed is False


def test_direct_update_and_delete_register_object_specific_ownership():
    member = _member()
    adapter, session, _, _ = _prepared_adapter(member)
    resource = ResourceDescriptor("workflow", uuid.uuid4(), "Workflow")
    row = UserWorkflowPermission(
        id=uuid.uuid4(),
        grantee_organization_id=member.organization_id,
        user_id=member.user_id,
        workflow_id=resource.resource_id,
        auth_state="viewer",
        assigned_by=uuid.uuid4(),
        assigned_at=NOW,
    )
    adapter._resource = resource
    adapter._direct_model = UserWorkflowPermission
    adapter._direct_route = resource_permission_spec("workflow").user_route
    adapter._direct_permission = row

    updated = adapter.grant_direct_permission(uuid.uuid4(), "operator")
    assert is_manually_audited(session, row, "updated")
    assert updated.before["auth_state"] == "viewer"
    assert updated.after["auth_state"] == "operator"
    assert updated.effective_access_changed is True

    deleted = adapter.revoke_direct_permission()
    assert is_manually_audited(session, row, "deleted")
    assert session.deleted == [row]
    assert deleted.before["auth_state"] == "operator"


def test_mail_team_source_count_excludes_revoked_credentials():
    class CountingQuery:
        def __init__(self):
            self.filters = []

        def join(self, *args):
            return self

        def filter(self, *args):
            self.filters.extend(args)
            return self

        def count(self):
            return 0

    query = CountingQuery()
    session = _MutationSession()
    session.query = lambda *_entities: query
    adapter = SqlAlchemyAccessManagementMutationAdapter(session)

    adapter._count_operational_team_permission(
        uuid.uuid4(),
        uuid.uuid4(),
        TeamMailCredentialPermission,
        MailCredential,
        "mail_credential_id",
    )

    assert "mail_credentials.status" in " ".join(str(item) for item in query.filters)


def test_app_creation_descriptor_preserves_organization_provenance():
    member = _member()
    adapter, session, _, _ = _prepared_adapter(member)

    created = adapter.grant_app_creation_permission(uuid.uuid4())
    row = session.added[0]
    assert isinstance(row, UserAppCreationPermission)
    assert created.after == {
        "grantee_organization_id": member.organization_id,
        "user_id": member.user_id,
    }

    adapter._app_permission = row
    deleted = adapter.revoke_app_creation_permission()
    assert deleted.before == created.after
    assert session.deleted == [row]


class _LockQuery:
    def __init__(self, session, name, *, all_rows=(), first_row=None):
        self.session = session
        self.name = name
        self.all_rows = list(all_rows)
        self.first_row = first_row

    def filter(self, *args):
        return self

    def order_by(self, *args):
        self.session.events.append(f"order:{self.name}")
        return self

    def with_for_update(self, **_kwargs):
        self.session.events.append(f"lock:{self.name}")
        return self

    def all(self):
        return self.all_rows

    def first(self):
        return self.first_row


class _LockSession:
    def __init__(self, managers, users):
        self.managers = managers
        self.users = users
        self.events = []

    def query(self, model):
        if model is OrganizationMembership:
            return _LockQuery(self, "memberships", all_rows=self.managers)
        if model is User:
            return _LockQuery(self, "users", all_rows=self.users)
        raise AssertionError(f"unexpected query model: {model}")


def test_manager_reduction_locks_memberships_then_users_and_excludes_deactivated():
    organization_id = uuid.uuid4()
    active_id = uuid.uuid4()
    deactivated_id = uuid.uuid4()
    managers = [
        OrganizationMembership(
            id=uuid.uuid4(),
            organization_id=organization_id,
            user_id=active_id,
            membership_state="active",
            organization_auth_state="manager",
            updated_at=NOW,
        ),
        OrganizationMembership(
            id=uuid.uuid4(),
            organization_id=organization_id,
            user_id=deactivated_id,
            membership_state="active",
            organization_auth_state="manager",
            updated_at=NOW,
        ),
    ]
    users = [
        User(
            id=active_id,
            email="active@example.invalid",
            name="Active",
            social_provider="local",
        ),
        User(
            id=deactivated_id,
            email="inactive@example.invalid",
            name="Inactive",
            social_provider="local",
            deactivated_at=NOW,
        ),
    ]
    session = _LockSession(managers, users)

    result = SqlAlchemyAccessManagementMutationAdapter(session).lock_member(
        organization_id,
        active_id,
        manager_reduction=True,
    )

    assert result.active_manager_count == 1
    assert session.events == [
        "order:memberships",
        "lock:memberships",
        "order:users",
        "lock:users",
    ]


class _ResourceQuery:
    def __init__(self, session, name, row):
        self.session = session
        self.name = name
        self.row = row

    def filter(self, *args):
        return self

    def with_for_update(self, **_kwargs):
        self.session.events.append(f"row-lock:{self.name}")
        return self

    def populate_existing(self):
        return self

    def first(self):
        self.session.events.append(f"read:{self.name}")
        return self.row


class _ResourceSession:
    def __init__(self, workflow, app):
        self.workflow = workflow
        self.app = app
        self.events = []
        self.lock_statements = []

    def query(self, model):
        if model is Workflow:
            return _ResourceQuery(self, "workflow", self.workflow)
        if model is App:
            return _ResourceQuery(self, "app", self.app)
        raise AssertionError(f"unexpected query model: {model}")

    def execute(self, statement):
        self.events.append("workflow-permission-scope-lock")
        self.lock_statements.append(statement)


def test_workflow_resource_lock_uses_app_lifecycle_before_permission_scope():
    organization_id = uuid.uuid4()
    app = App(
        id=uuid.uuid4(),
        organization_id=organization_id,
        name="Workflow permission scope",
        created_by=uuid.uuid4(),
    )
    workflow = Workflow(
        id=uuid.uuid4(),
        organization_id=organization_id,
        app_id=app.id,
        created_by=app.created_by,
    )
    session = _ResourceSession(workflow, app)

    result = SqlAlchemyAccessManagementMutationAdapter(session).lock_resource(
        organization_id,
        "workflow",
        workflow.id,
    )

    assert result.resource_id == workflow.id
    assert session.events == [
        "row-lock:workflow",
        "read:workflow",
        "read:app",
        "row-lock:app",
        "read:app",
        "workflow-permission-scope-lock",
    ]
    compiled = str(
        session.lock_statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "workflow_permission_scope" in compiled


def test_workflow_resource_lock_propagates_primary_change_before_permission_scope(
    monkeypatch,
):
    organization_id = uuid.uuid4()
    app = App(
        id=uuid.uuid4(),
        organization_id=organization_id,
        name="Stale workflow permission scope",
        created_by=uuid.uuid4(),
    )
    workflow = Workflow(
        id=uuid.uuid4(),
        organization_id=organization_id,
        app_id=app.id,
        created_by=app.created_by,
    )
    session = _ResourceSession(workflow, app)

    def _raise_primary_changed(*args, **kwargs):
        raise AppPrimaryChangedDuringMutationError

    monkeypatch.setattr(
        "apps.gateway.adapters.db.access_management_mutation_adapter."
        "lock_app_for_workflow_mutation",
        _raise_primary_changed,
    )

    with pytest.raises(WorkflowPrimaryChanged):
        SqlAlchemyAccessManagementMutationAdapter(session).lock_resource(
            organization_id,
            "workflow",
            workflow.id,
        )

    assert "workflow-permission-scope-lock" not in session.events
