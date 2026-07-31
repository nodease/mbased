import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import SQLAlchemyError

from apps.gateway.services import (
    knowledge_document_lifecycle_service as lifecycle_module,
)
from apps.gateway.services.knowledge_document_lifecycle_service import (
    KnowledgeDocumentLifecycleHidden,
    KnowledgeDocumentLifecycleService,
    KnowledgeDocumentLifecycleUnavailable,
)
from apps.shared.db.models.knowledge import Document, DocumentVersion, KnowledgeBase


class _Query:
    def __init__(self, db, entity):
        self.db = db
        self.entity = entity

    def filter(self, *_args):
        return self

    def populate_existing(self):
        self.db.refreshed_entities.append(self.entity)
        return self

    def with_for_update(self):
        self.db.locked_entities.append(self.entity)
        return self

    def first(self):
        if self.db.query_error is not None:
            raise self.db.query_error
        if self.entity is KnowledgeBase:
            return self.db.kb
        if self.entity is Document:
            return self.db.document
        if self.entity is DocumentVersion:
            return self.db.active_version
        return self.db.other_document


class _Db:
    def __init__(
        self,
        *,
        kb,
        document,
        active_version=None,
        other_document=None,
        query_error=None,
    ):
        self.kb = kb
        self.document = document
        self.active_version = active_version
        self.other_document = other_document
        self.query_error = query_error
        self.locked_entities = []
        self.refreshed_entities = []
        self.deleted = None
        self.committed = False
        self.rolled_back = False

    def query(self, entity):
        return _Query(self, entity)

    def delete(self, document):
        self.deleted = document

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


def _entities():
    organization_id = uuid.uuid4()
    knowledge_base_id = uuid.uuid4()
    document_id = uuid.uuid4()
    active_version_id = uuid.uuid4()
    kb = SimpleNamespace(
        id=knowledge_base_id,
        organization_id=organization_id,
        lifecycle_state="active",
        active_document_version_id=active_version_id,
    )
    document = SimpleNamespace(
        id=document_id,
        knowledge_base_id=knowledge_base_id,
        file_path="opaque-reference",
    )
    active_version = SimpleNamespace(
        id=active_version_id,
        knowledge_base_id=knowledge_base_id,
        legacy_document_id=document_id,
        status="ready",
        superseded_at=None,
    )
    return kb, document, active_version


def test_delete_document_joins_write_lock_and_releases_owned_active_version(
    monkeypatch,
):
    kb, document, active_version = _entities()
    db = _Db(kb=kb, document=document, active_version=active_version)
    calls = []
    monkeypatch.setattr(
        lifecycle_module,
        "acquire_document_write_lock",
        lambda session, document_id: calls.append((session, document_id)),
    )

    result = KnowledgeDocumentLifecycleService(db).delete_document(
        knowledge_base_id=kb.id,
        organization_id=kb.organization_id,
        document_id=document.id,
    )

    assert calls == [(db, document.id)]
    assert db.locked_entities == [KnowledgeBase, Document, DocumentVersion]
    assert db.refreshed_entities == [KnowledgeBase, Document, DocumentVersion]
    assert kb.active_document_version_id is None
    assert active_version.status == "superseded"
    assert active_version.superseded_at is not None
    assert db.deleted is document
    assert db.committed is True
    assert result.file_path == "opaque-reference"


def test_delete_legacy_sibling_does_not_release_another_document_active_version(
    monkeypatch,
):
    kb, document, active_version = _entities()
    active_version.legacy_document_id = uuid.uuid4()
    db = _Db(
        kb=kb,
        document=document,
        active_version=active_version,
        other_document=(uuid.uuid4(),),
    )
    monkeypatch.setattr(
        lifecycle_module,
        "acquire_document_write_lock",
        lambda *_args: None,
    )

    KnowledgeDocumentLifecycleService(db).delete_document(
        knowledge_base_id=kb.id,
        organization_id=kb.organization_id,
        document_id=document.id,
    )

    assert kb.active_document_version_id == active_version.id
    assert active_version.status == "ready"
    assert active_version.superseded_at is None


def test_delete_document_hides_stale_authorized_resource_and_rolls_back(
    monkeypatch,
):
    kb, document, _active_version = _entities()
    db = _Db(kb=kb, document=None)
    monkeypatch.setattr(
        lifecycle_module,
        "acquire_document_write_lock",
        lambda *_args: None,
    )

    with pytest.raises(KnowledgeDocumentLifecycleHidden):
        KnowledgeDocumentLifecycleService(db).delete_document(
            knowledge_base_id=kb.id,
            organization_id=kb.organization_id,
            document_id=document.id,
        )

    assert db.deleted is None
    assert db.rolled_back is True


def test_delete_document_hides_kb_archived_after_authorization(monkeypatch):
    kb, document, active_version = _entities()
    kb.lifecycle_state = "archived"
    db = _Db(kb=kb, document=document, active_version=active_version)
    monkeypatch.setattr(
        lifecycle_module,
        "acquire_document_write_lock",
        lambda *_args: None,
    )

    with pytest.raises(KnowledgeDocumentLifecycleHidden):
        KnowledgeDocumentLifecycleService(db).delete_document(
            knowledge_base_id=kb.id,
            organization_id=kb.organization_id,
            document_id=document.id,
        )

    assert db.deleted is None
    assert db.rolled_back is True


def test_delete_document_redacts_database_failure(monkeypatch):
    kb, document, _active_version = _entities()
    db = _Db(
        kb=kb,
        document=document,
        query_error=SQLAlchemyError("raw database detail"),
    )
    monkeypatch.setattr(
        lifecycle_module,
        "acquire_document_write_lock",
        lambda *_args: None,
    )

    with pytest.raises(KnowledgeDocumentLifecycleUnavailable) as exc_info:
        KnowledgeDocumentLifecycleService(db).delete_document(
            knowledge_base_id=kb.id,
            organization_id=kb.organization_id,
            document_id=document.id,
        )

    assert "raw database detail" not in str(exc_info.value)
    assert db.rolled_back is True
