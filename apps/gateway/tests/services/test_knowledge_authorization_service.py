import uuid
from types import SimpleNamespace

import pytest

from apps.gateway.services.knowledge_authorization_service import (
    KnowledgeAuthorizationService,
    KnowledgePermissionDenied,
    KnowledgeResourceHidden,
)


class FakeQuery:
    def __init__(self, values):
        self.values = values

    def filter(self, *args):
        return self

    def first(self):
        return self.values.pop(0)


class FakeDb:
    def __init__(self, values):
        self.values = list(values)

    def query(self, *args):
        return FakeQuery(self.values)


def test_missing_or_cross_scope_kb_is_hidden():
    service = KnowledgeAuthorizationService(
        FakeDb([None]), user_id=uuid.uuid4(), organization_id=uuid.uuid4()
    )

    with pytest.raises(KnowledgeResourceHidden):
        service.load_kb(uuid.uuid4(), "read")


def test_visible_but_insufficient_action_is_forbidden(monkeypatch):
    kb = SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        lifecycle_state="active",
    )
    service = KnowledgeAuthorizationService(
        FakeDb([kb]), user_id=uuid.uuid4(), organization_id=kb.organization_id
    )
    monkeypatch.setattr(
        service.permission_helper,
        "evaluate_kb_action",
        lambda *args, **kwargs: SimpleNamespace(
            allowed=False,
            resource_visibility="visible",
        ),
    )

    with pytest.raises(KnowledgePermissionDenied):
        service.load_kb(kb.id, "write")


def test_document_must_belong_to_authorized_kb(monkeypatch):
    kb = SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        lifecycle_state="active",
    )
    service = KnowledgeAuthorizationService(
        FakeDb([kb, None]), user_id=uuid.uuid4(), organization_id=kb.organization_id
    )
    monkeypatch.setattr(
        service.permission_helper,
        "evaluate_kb_action",
        lambda *args, **kwargs: SimpleNamespace(
            allowed=True,
            resource_visibility="visible",
        ),
    )

    with pytest.raises(KnowledgeResourceHidden):
        service.load_document(kb.id, uuid.uuid4(), "read")


def test_archived_kb_can_be_loaded_only_for_lifecycle_management(monkeypatch):
    kb = SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        lifecycle_state="archived",
    )
    service = KnowledgeAuthorizationService(
        FakeDb([kb]), user_id=uuid.uuid4(), organization_id=kb.organization_id
    )
    monkeypatch.setattr(
        service.permission_helper,
        "evaluate_kb_action",
        lambda *args, **kwargs: SimpleNamespace(
            allowed=kwargs.get("include_archived") is True,
            resource_visibility="resource_hidden",
        ),
    )

    assert service.load_kb(kb.id, "manage", include_archived=True) is kb
