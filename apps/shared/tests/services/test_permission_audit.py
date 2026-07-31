import uuid

from apps.shared.services import permission_audit


def test_user_permission_denial_preserves_user_actor(monkeypatch):
    calls = []
    monkeypatch.setattr(permission_audit, "record_audit", lambda **kwargs: calls.append(kwargs))

    permission_audit.record_resource_permission_denied(
        user_id="user-123",
        resource_type="knowledge_base",
        resource_id="kb-123",
        action="use",
        effective_auth_state="authenticated",
        organization_id="org-123",
    )

    assert calls[0]["actor_id"] == "user-123"
    assert calls[0]["actor_type"] == "user"
    assert calls[0]["metadata"]["organization_id"] == "org-123"


def test_system_permission_denial_has_no_synthetic_user_actor(monkeypatch):
    calls = []
    monkeypatch.setattr(permission_audit, "record_audit", lambda **kwargs: calls.append(kwargs))

    permission_audit.record_system_resource_permission_denied(
        resource_type="llm_credential",
        resource_id="unknown",
        action="use",
        effective_auth_state="none",
        organization_id=None,
        metadata={"runtime_surface": "workflow_llm_node"},
    )

    assert calls[0]["actor_id"] is None
    assert calls[0]["actor_type"] == "system"
    assert calls[0]["metadata"]["runtime_surface"] == "workflow_llm_node"


def test_public_permission_denial_has_public_actor_without_identifier(monkeypatch):
    calls = []
    monkeypatch.setattr(permission_audit, "record_audit", lambda **kwargs: calls.append(kwargs))

    permission_audit.record_public_resource_permission_denied(
        resource_type="llm_credential",
        resource_id="unknown",
        action="use",
        effective_auth_state="none",
        organization_id="org-123",
    )

    assert calls[0]["actor_id"] is None
    assert calls[0]["actor_type"] == "public"
    assert calls[0]["metadata"]["organization_id"] == "org-123"


def test_resource_permission_denied_preserves_verified_organization(monkeypatch):
    actor_id = uuid.uuid4()
    resource_id = uuid.uuid4()
    verified_organization_id = uuid.uuid4()
    untrusted_organization_id = uuid.uuid4()
    events = []

    monkeypatch.setattr(
        permission_audit,
        "record_audit",
        lambda **event: events.append(event),
    )

    permission_audit.record_resource_permission_denied(
        user_id=actor_id,
        resource_type="workflow",
        resource_id=resource_id,
        action="write",
        effective_auth_state="viewer",
        organization_id=verified_organization_id,
        metadata={"organization_id": str(untrusted_organization_id)},
    )

    assert len(events) == 1
    event = events[0]
    assert event["action"] == "permission.denied"
    assert event["actor_id"] == actor_id
    assert event["actor_type"] == "user"
    assert event["category"] == "action"
    assert event["status"] == "failure"
    assert event["target_type"] == "workflow"
    assert event["target_id"] == resource_id
    assert event["metadata"]["organization_id"] == str(
        verified_organization_id
    )
