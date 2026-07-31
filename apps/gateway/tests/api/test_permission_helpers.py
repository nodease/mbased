import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from apps.gateway.auth import permissions
from apps.gateway.auth.permissions import (
    ensure_llm_credential_permission,
    ensure_workflow_permission,
)
from apps.shared.audit.actions import AuditAction


class FakeQuery:
    def __init__(self, workflow):
        self.workflow = workflow

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self.workflow


class FakeDb:
    def __init__(self, workflow):
        self.workflow = workflow

    def query(self, *args, **kwargs):
        return FakeQuery(self.workflow)


def test_malformed_workflow_id_is_hidden_before_database_query():
    """UUID가 아닌 placeholder는 PostgreSQL cast 오류 대신 safe 404로 닫는다."""

    class QueryMustNotRun:
        def query(self, *_args, **_kwargs):
            raise AssertionError("malformed workflow id reached the database")

    user = SimpleNamespace(id=uuid.uuid4())

    with pytest.raises(HTTPException) as exc_info:
        ensure_workflow_permission(QueryMustNotRun(), user, "default", "read")

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Workflow not found"


def test_workflow_permission_denied_records_permission_audit(monkeypatch):
    workflow = SimpleNamespace(id=uuid.uuid4(), organization_id=uuid.uuid4())
    user = SimpleNamespace(id=uuid.uuid4())
    events = []

    monkeypatch.setattr(
        permissions,
        "get_effective_workflow_auth_state",
        lambda db, user_id, workflow_id, organization_id=None: "viewer",
    )
    monkeypatch.setattr(
        permissions,
        "has_workflow_permission",
        lambda db, user_id, workflow_id, action, organization_id=None: False,
    )
    monkeypatch.setattr(
        permissions,
        "has_organization_scope_access",
        lambda db, user_id, organization_id: True,
    )
    monkeypatch.setattr(
        permissions,
        "record_audit",
        lambda **event: events.append(event),
    )

    with pytest.raises(HTTPException) as exc_info:
        ensure_workflow_permission(FakeDb(workflow), user, workflow.id, "write")

    assert exc_info.value.status_code == 403
    assert getattr(exc_info.value, "audit_recorded", False) is True
    assert events[0]["action"] == AuditAction.PERMISSION_DENIED
    assert events[0]["target_type"] == "workflow"
    assert events[0]["target_id"] == workflow.id
    assert events[0]["status"] == "failure"
    assert events[0]["metadata"]["policy_result"] == "deny"
    assert events[0]["metadata"]["resource_type"] == "workflow"
    assert events[0]["metadata"]["resource_id"] == str(workflow.id)
    assert events[0]["metadata"]["required_permission"] == "write"
    assert events[0]["metadata"]["permission_action"] == "write"
    assert events[0]["metadata"]["effective_auth_state"] == "viewer"
    assert events[0]["metadata"]["organization_id"] == str(workflow.organization_id)


def test_workflow_permission_denied_outside_organization_scope_is_404(monkeypatch):
    workflow = SimpleNamespace(id=uuid.uuid4(), organization_id=uuid.uuid4())
    user = SimpleNamespace(id=uuid.uuid4())
    events = []

    monkeypatch.setattr(
        permissions,
        "get_effective_workflow_auth_state",
        lambda db, user_id, workflow_id, organization_id=None: "none",
    )
    monkeypatch.setattr(
        permissions,
        "has_workflow_permission",
        lambda db, user_id, workflow_id, action, organization_id=None: False,
    )
    monkeypatch.setattr(
        permissions,
        "has_organization_scope_access",
        lambda db, user_id, organization_id: False,
    )
    monkeypatch.setattr(
        permissions,
        "record_audit",
        lambda **event: events.append(event),
    )

    with pytest.raises(HTTPException) as exc_info:
        ensure_workflow_permission(FakeDb(workflow), user, workflow.id, "read")

    assert exc_info.value.status_code == 404
    assert getattr(exc_info.value, "audit_recorded", False) is False
    assert events == []


def test_llm_credential_permission_denied_records_verified_organization(monkeypatch):
    credential = SimpleNamespace(id=uuid.uuid4(), organization_id=uuid.uuid4())
    user = SimpleNamespace(id=uuid.uuid4())
    events = []

    monkeypatch.setattr(
        permissions,
        "get_effective_llm_credential_auth_state",
        lambda db, user_id, credential_id, organization_id=None: "viewer",
    )
    monkeypatch.setattr(
        permissions,
        "has_llm_credential_permission",
        lambda db, user_id, credential_id, action, organization_id=None: False,
    )
    monkeypatch.setattr(
        permissions,
        "has_organization_scope_access",
        lambda db, user_id, organization_id: True,
    )
    monkeypatch.setattr(
        permissions,
        "record_audit",
        lambda **event: events.append(event),
    )

    with pytest.raises(HTTPException) as exc_info:
        ensure_llm_credential_permission(
            FakeDb(credential),
            user,
            credential.id,
            "read",
        )

    assert exc_info.value.status_code == 403
    assert getattr(exc_info.value, "audit_recorded", False) is True
    assert len(events) == 1
    assert events[0]["action"] == AuditAction.PERMISSION_DENIED
    assert events[0]["actor_id"] == user.id
    assert events[0]["actor_type"] == "user"
    assert events[0]["category"] == "action"
    assert events[0]["status"] == "failure"
    assert events[0]["target_type"] == "llm_credential"
    assert events[0]["target_id"] == credential.id
    assert events[0]["metadata"]["organization_id"] == str(
        credential.organization_id
    )


def test_llm_credential_permission_denied_outside_organization_scope_is_404(
    monkeypatch,
):
    credential = SimpleNamespace(id=uuid.uuid4(), organization_id=uuid.uuid4())
    user = SimpleNamespace(id=uuid.uuid4())
    events = []

    monkeypatch.setattr(
        permissions,
        "has_organization_scope_access",
        lambda db, user_id, organization_id: False,
    )
    monkeypatch.setattr(
        permissions,
        "record_audit",
        lambda **event: events.append(event),
    )

    with pytest.raises(HTTPException) as exc_info:
        ensure_llm_credential_permission(
            FakeDb(credential),
            user,
            credential.id,
            "read",
        )

    assert exc_info.value.status_code == 404
    assert getattr(exc_info.value, "audit_recorded", False) is False
    assert events == []


def test_llm_credential_permission_filters_by_active_organization_before_rbac(
    monkeypatch,
):
    credential = SimpleNamespace(id=uuid.uuid4(), organization_id=uuid.uuid4())
    active_organization_id = uuid.uuid4()
    user = SimpleNamespace(id=uuid.uuid4())
    captured_filters = []

    class OrganizationScopedQuery:
        def filter(self, *criteria):
            captured_filters.extend(criteria)
            return self

        def first(self):
            filter_values = {
                getattr(expression.left, "key", None): expression.right.value
                for expression in captured_filters
            }
            if (
                filter_values.get("id") == credential.id
                and filter_values.get("organization_id")
                == credential.organization_id
            ):
                return credential
            return None

    class OrganizationScopedDb:
        def query(self, *_args, **_kwargs):
            return OrganizationScopedQuery()

    monkeypatch.setattr(
        permissions,
        "has_organization_scope_access",
        lambda *_args, **_kwargs: pytest.fail(
            "cross-organization credential reached scope authorization"
        ),
    )
    monkeypatch.setattr(
        permissions,
        "has_llm_credential_permission",
        lambda *_args, **_kwargs: pytest.fail(
            "cross-organization credential reached resource authorization"
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        ensure_llm_credential_permission(
            OrganizationScopedDb(),
            user,
            credential.id,
            "read",
            active_organization_id=active_organization_id,
        )

    assert exc_info.value.status_code == 404
    assert {
        getattr(expression.left, "key", None) for expression in captured_filters
    } == {"id", "organization_id"}
