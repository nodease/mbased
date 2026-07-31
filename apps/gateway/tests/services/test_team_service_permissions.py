import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from apps.gateway.services import resource_permission_registry
from apps.gateway.services import team_service
from apps.gateway.services.team_service import TeamService
from apps.shared.audit.actions import AuditAction
from apps.shared.db.models.team import TeamKnowledgePermission, UserKnowledgePermission
from apps.shared.schemas.team import (
    ResourcePermissionGrantRequest,
    ResourcePermissionRevokeRequest,
    TeamCreateRequest,
    TeamUpdateRequest,
)


class FakeQuery:
    def __init__(self, db):
        self.db = db

    def join(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self.db.first_values.pop(0)


class FakeDb:
    def __init__(self, first_values=None):
        self.first_values = list(first_values or [])
        self.queried_models = []
        self.deleted = []
        self.committed = False

    def query(self, *args, **kwargs):
        if args:
            self.queried_models.append(args[0])
        return FakeQuery(self)

    def add(self, row):
        self.added = row

    def delete(self, row):
        self.deleted.append(row)

    def commit(self):
        self.committed = True

    def refresh(self, row):
        self.refreshed = row


def _organization_membership(user_id, organization_id, auth_state="member"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        user_id=user_id,
        organization_id=organization_id,
        membership_state="active",
        organization_auth_state=auth_state,
    )


def _active_organization(organization_id):
    return SimpleNamespace(id=organization_id, is_active=True)


def test_workflow_resource_manager_can_grant_permission(monkeypatch):
    organization_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    grantee_id = uuid.uuid4()
    permission_row = SimpleNamespace(id=uuid.uuid4(), auth_state="viewer")
    db = FakeDb(
        first_values=[
            SimpleNamespace(id=workflow_id, organization_id=organization_id),
            SimpleNamespace(id=grantee_id, deactivated_at=None),
            SimpleNamespace(id=grantee_id, deactivated_at=None),
            _active_organization(organization_id),
            _organization_membership(grantee_id, organization_id),
            permission_row,
        ]
    )
    user = SimpleNamespace(id=uuid.uuid4())
    events = []

    monkeypatch.setattr(
        team_service, "has_organization_manager_permission", lambda *a: False
    )
    monkeypatch.setattr(
        resource_permission_registry,
        "get_effective_workflow_auth_state",
        lambda *a, **k: "manager",
    )
    monkeypatch.setattr(
        team_service, "record_audit", lambda **event: events.append(event)
    )

    row = TeamService.grant_resource_permission(
        db,
        user,
        ResourcePermissionGrantRequest(
            organization_id=organization_id,
            resource_type="workflow",
            resource_id=workflow_id,
            grantee_type="user",
            grantee_id=grantee_id,
            auth_state="builder",
        ),
    )

    assert row is permission_row
    assert row.auth_state == "builder"
    assert db.committed is True
    assert events[0]["action"] == AuditAction.PERMISSION_GRANT
    assert events[0]["metadata"]["grant_subject_type"] == "user"
    assert events[0]["metadata"]["grant_subject_id"] == str(grantee_id)
    assert events[0]["metadata"]["resource_type"] == "workflow"
    assert events[0]["metadata"]["auth_state"] == "builder"


def test_knowledge_resource_manager_can_grant_user_permission(monkeypatch):
    organization_id = uuid.uuid4()
    knowledge_base_id = uuid.uuid4()
    grantee_id = uuid.uuid4()
    db = FakeDb(
        first_values=[
            SimpleNamespace(
                id=knowledge_base_id,
                organization_id=organization_id,
                lifecycle_state="active",
            ),
            SimpleNamespace(id=grantee_id, deactivated_at=None),
            SimpleNamespace(id=grantee_id, deactivated_at=None),
            _active_organization(organization_id),
            _organization_membership(grantee_id, organization_id),
            None,
        ]
    )
    user = SimpleNamespace(id=uuid.uuid4())
    events = []

    monkeypatch.setattr(
        team_service, "has_organization_manager_permission", lambda *a: False
    )
    monkeypatch.setattr(
        resource_permission_registry,
        "get_effective_knowledge_base_auth_state",
        lambda *a, **k: "manager",
    )
    monkeypatch.setattr(
        team_service, "record_audit", lambda **event: events.append(event)
    )

    row = TeamService.grant_resource_permission(
        db,
        user,
        ResourcePermissionGrantRequest(
            organization_id=organization_id,
            resource_type="knowledge_base",
            resource_id=knowledge_base_id,
            grantee_type="user",
            grantee_id=grantee_id,
            auth_state="builder",
        ),
    )

    assert isinstance(row, UserKnowledgePermission)
    assert row.knowledge_base_id == knowledge_base_id
    assert row.user_id == grantee_id
    assert row.auth_state == "builder"
    assert UserKnowledgePermission in db.queried_models
    assert db.committed is True
    assert events[0]["action"] == AuditAction.PERMISSION_GRANT
    assert events[0]["metadata"]["grant_subject_type"] == "user"
    assert events[0]["metadata"]["grant_subject_id"] == str(grantee_id)
    assert events[0]["metadata"]["resource_type"] == "knowledge_base"
    assert events[0]["metadata"]["auth_state"] == "builder"


def test_user_grant_requires_grantee_organization_membership(monkeypatch):
    organization_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    grantee_id = uuid.uuid4()
    db = FakeDb(
        first_values=[
            SimpleNamespace(id=workflow_id, organization_id=organization_id),
            SimpleNamespace(id=grantee_id, deactivated_at=None),
            SimpleNamespace(id=grantee_id, deactivated_at=None),
            _active_organization(organization_id),
            None,
        ]
    )
    user = SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(
        team_service, "has_organization_manager_permission", lambda *a: True
    )

    with pytest.raises(HTTPException) as exc_info:
        TeamService.grant_resource_permission(
            db,
            user,
            ResourcePermissionGrantRequest(
                organization_id=organization_id,
                resource_type="workflow",
                resource_id=workflow_id,
                grantee_type="user",
                grantee_id=grantee_id,
                auth_state="viewer",
            ),
        )

    assert exc_info.value.status_code == 400


def test_grant_rejects_cross_organization_resource_before_authorization(monkeypatch):
    requested_organization_id = uuid.uuid4()
    resource_organization_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    user = SimpleNamespace(id=uuid.uuid4())
    db = FakeDb(
        first_values=[
            SimpleNamespace(
                id=workflow_id,
                organization_id=resource_organization_id,
            )
        ]
    )
    denied = []

    monkeypatch.setattr(
        team_service,
        "has_organization_manager_permission",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("authorization must not run across organizations")
        ),
    )
    monkeypatch.setattr(
        team_service,
        "record_permission_denied",
        lambda *args, **kwargs: denied.append((args, kwargs)),
    )

    with pytest.raises(HTTPException) as exc_info:
        TeamService.grant_resource_permission(
            db,
            user,
            ResourcePermissionGrantRequest(
                organization_id=requested_organization_id,
                resource_type="workflow",
                resource_id=workflow_id,
                grantee_type="user",
                grantee_id=uuid.uuid4(),
                auth_state="viewer",
            ),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Resource organization mismatch"
    assert db.committed is False
    assert denied == []


def test_resource_permission_manager_denial_is_fail_closed_and_audited(monkeypatch):
    organization_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    user = SimpleNamespace(id=uuid.uuid4())
    db = FakeDb(
        first_values=[
            SimpleNamespace(id=workflow_id, organization_id=organization_id),
        ]
    )
    denied = []

    monkeypatch.setattr(
        team_service,
        "has_organization_manager_permission",
        lambda *args, **kwargs: False,
    )
    monkeypatch.setattr(
        resource_permission_registry,
        "get_effective_workflow_auth_state",
        lambda *args, **kwargs: "viewer",
    )
    monkeypatch.setattr(
        team_service,
        "record_permission_denied",
        lambda *args, **kwargs: denied.append(args),
    )

    with pytest.raises(HTTPException) as exc_info:
        TeamService.grant_resource_permission(
            db,
            user,
            ResourcePermissionGrantRequest(
                organization_id=organization_id,
                resource_type="workflow",
                resource_id=workflow_id,
                grantee_type="user",
                grantee_id=uuid.uuid4(),
                auth_state="viewer",
            ),
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Forbidden"
    assert denied == [
        (user, "workflow", workflow_id, "manage", "viewer", organization_id)
    ]
    assert db.committed is False


def test_team_grant_rejects_inactive_team(monkeypatch):
    organization_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    team_id = uuid.uuid4()
    db = FakeDb(
        first_values=[
            SimpleNamespace(id=workflow_id, organization_id=organization_id),
            SimpleNamespace(
                id=team_id,
                organization_id=organization_id,
                is_active=False,
            ),
        ]
    )
    user = SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(
        team_service, "has_organization_manager_permission", lambda *a: True
    )

    with pytest.raises(HTTPException) as exc_info:
        TeamService.grant_resource_permission(
            db,
            user,
            ResourcePermissionGrantRequest(
                organization_id=organization_id,
                resource_type="workflow",
                resource_id=workflow_id,
                grantee_type="team",
                grantee_id=team_id,
                auth_state="viewer",
            ),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Team is inactive"


def test_llm_resource_manager_can_revoke_permission(monkeypatch):
    organization_id = uuid.uuid4()
    credential_id = uuid.uuid4()
    grantee_id = uuid.uuid4()
    permission_row = SimpleNamespace(id=uuid.uuid4())
    db = FakeDb(
        first_values=[
            SimpleNamespace(id=credential_id, organization_id=organization_id),
            permission_row,
        ]
    )
    user = SimpleNamespace(id=uuid.uuid4())
    events = []

    monkeypatch.setattr(
        team_service, "has_organization_manager_permission", lambda *a: False
    )
    monkeypatch.setattr(
        resource_permission_registry,
        "get_effective_llm_credential_auth_state",
        lambda *a, **k: "manager",
    )
    monkeypatch.setattr(
        team_service, "record_audit", lambda **event: events.append(event)
    )

    result = TeamService.revoke_resource_permission(
        db,
        user,
        ResourcePermissionRevokeRequest(
            organization_id=organization_id,
            resource_type="llm_credential",
            resource_id=credential_id,
            grantee_type="user",
            grantee_id=grantee_id,
        ),
    )

    assert result == {"status": "revoked"}
    assert db.deleted == [permission_row]
    assert db.committed is True
    assert events[0]["action"] == AuditAction.PERMISSION_REVOKE
    assert events[0]["metadata"]["grant_subject_type"] == "user"
    assert events[0]["metadata"]["grant_subject_id"] == str(grantee_id)
    assert events[0]["metadata"]["resource_type"] == "llm_credential"


def test_organization_manager_can_revoke_team_knowledge_permission(monkeypatch):
    organization_id = uuid.uuid4()
    knowledge_base_id = uuid.uuid4()
    team_id = uuid.uuid4()
    permission_row = SimpleNamespace(id=uuid.uuid4())
    db = FakeDb(
        first_values=[
            SimpleNamespace(
                id=knowledge_base_id,
                organization_id=organization_id,
                lifecycle_state="active",
            ),
            permission_row,
        ]
    )
    user = SimpleNamespace(id=uuid.uuid4())
    events = []

    monkeypatch.setattr(
        team_service, "has_organization_manager_permission", lambda *a: True
    )
    monkeypatch.setattr(
        team_service, "record_audit", lambda **event: events.append(event)
    )

    result = TeamService.revoke_resource_permission(
        db,
        user,
        ResourcePermissionRevokeRequest(
            organization_id=organization_id,
            resource_type="knowledge_base",
            resource_id=knowledge_base_id,
            grantee_type="team",
            grantee_id=team_id,
        ),
    )

    assert result == {"status": "revoked"}
    assert db.deleted == [permission_row]
    assert TeamKnowledgePermission in db.queried_models
    assert db.committed is True
    assert events[0]["action"] == AuditAction.PERMISSION_REVOKE
    assert events[0]["metadata"]["grant_subject_type"] == "team"
    assert events[0]["metadata"]["grant_subject_id"] == str(team_id)
    assert events[0]["metadata"]["resource_type"] == "knowledge_base"


def test_team_create_still_requires_organization_manager(monkeypatch):
    organization_id = uuid.uuid4()
    user = SimpleNamespace(id=uuid.uuid4())
    organization = SimpleNamespace(
        id=organization_id,
        created_by=uuid.uuid4(),
        managed_by=None,
        is_active=True,
    )
    membership = _organization_membership(user.id, organization_id)
    denied = []

    monkeypatch.setattr(
        team_service,
        "record_permission_denied",
        lambda *args, **kwargs: denied.append(args),
    )

    with pytest.raises(HTTPException) as exc_info:
        TeamService.create_team(
            FakeDb(
                first_values=[
                    SimpleNamespace(id=user.id, deactivated_at=None),
                    organization,
                    membership,
                    SimpleNamespace(id=user.id, deactivated_at=None),
                    organization,
                    membership,
                ]
            ),
            user,
            TeamCreateRequest(organization_id=organization_id, name="Builders"),
        )

    assert exc_info.value.status_code == 403
    assert denied == [
        (
            user,
            "organization",
            organization_id,
            "manage",
            "none",
            organization_id,
        )
    ]


def test_team_update_rejects_managed_by_outside_organization():
    organization_id = uuid.uuid4()
    user = SimpleNamespace(id=uuid.uuid4())
    manager_id = uuid.uuid4()
    team = SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=organization_id,
        name="Builders",
        description=None,
        managed_by=None,
        is_auto_add=False,
    )
    organization = SimpleNamespace(
        id=organization_id,
        created_by=user.id,
        managed_by=None,
        is_active=True,
    )
    target_user = SimpleNamespace(id=manager_id, deactivated_at=None)
    db = FakeDb(
        first_values=[
            team,
            SimpleNamespace(id=user.id, deactivated_at=None),
            organization,
            None,
            target_user,
            target_user,
            organization,
            None,
        ]
    )

    with pytest.raises(HTTPException) as exc_info:
        TeamService.update_team(
            db,
            user,
            team.id,
            TeamUpdateRequest(managed_by=manager_id),
            organization_id=organization_id,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Managed user is not in the organization"
    assert team.managed_by is None
    assert db.committed is False
