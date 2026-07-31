import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from apps.gateway.api.v1.endpoints import knowledge as knowledge_endpoint
from apps.gateway.services.knowledge_lifecycle_service import (
    KnowledgeLifecyclePolicyDenied,
)


def test_delete_knowledge_base_delegates_lifecycle_service(monkeypatch):
    kb_id = uuid.uuid4()
    user_id = uuid.uuid4()
    db = object()
    calls = []

    organization_id = uuid.uuid4()
    kb = SimpleNamespace(id=kb_id)
    request = SimpleNamespace(state=SimpleNamespace(request_id="req"))
    service = SimpleNamespace(
        hard_delete_knowledge_base=lambda loaded_kb, **kwargs: calls.append(
            (loaded_kb, kwargs)
        )
    )
    monkeypatch.setattr(
        knowledge_endpoint,
        "resolve_active_organization_id",
        lambda *_args, **_kwargs: organization_id,
    )
    monkeypatch.setattr(
        knowledge_endpoint,
        "has_organization_manager_permission",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        knowledge_endpoint,
        "_knowledge_authorization_service",
        lambda *_args, **_kwargs: SimpleNamespace(
            load_kb=lambda *args, **kwargs: kb
        ),
    )
    monkeypatch.setattr(
        knowledge_endpoint,
        "KnowledgeLifecycleService",
        lambda service_db: service if service_db is db else None,
    )

    response = knowledge_endpoint.delete_knowledge_base(
        kb_id=kb_id,
        request=request,
        acknowledged_hard_delete=True,
        x_organization_id=str(organization_id),
        db=db,
        current_user=SimpleNamespace(id=user_id),
    )

    assert response.status_code == 204
    assert calls == [(kb, {"actor_id": user_id})]


def test_delete_knowledge_base_requires_explicit_acknowledgement(monkeypatch):
    organization_id = uuid.uuid4()
    request = SimpleNamespace(state=SimpleNamespace(request_id="req"))
    monkeypatch.setattr(
        knowledge_endpoint,
        "resolve_active_organization_id",
        lambda *_args, **_kwargs: organization_id,
    )
    monkeypatch.setattr(
        knowledge_endpoint,
        "has_organization_manager_permission",
        lambda *_args, **_kwargs: True,
    )

    with pytest.raises(HTTPException) as exc_info:
        knowledge_endpoint.delete_knowledge_base(
            kb_id=uuid.uuid4(),
            request=request,
            acknowledged_hard_delete=False,
            x_organization_id=str(organization_id),
            db=object(),
            current_user=SimpleNamespace(id=uuid.uuid4()),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"]["code"] == "validation.failed"
    assert exc_info.value.detail["error"]["details"] == {
        "field": "acknowledged_hard_delete"
    }


def test_delete_knowledge_base_hides_retention_policy_details():
    request = SimpleNamespace(state=SimpleNamespace(request_id="req"))

    with pytest.raises(HTTPException) as exc_info:
        knowledge_endpoint._raise_knowledge_lifecycle_policy_error(
            request,
            KnowledgeLifecyclePolicyDenied("retention_policy_unavailable"),
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["error"] == {
        "code": "policy.denied",
        "message": "Knowledge Base retention policy does not allow hard delete.",
        "request_id": "req",
        "details": {},
    }
