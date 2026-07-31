import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import SQLAlchemyError

from apps.gateway.services.knowledge_document_registration_service import (
    KnowledgeDocumentRegistrationHidden,
    KnowledgeDocumentRegistrationPolicyDenied,
    KnowledgeDocumentRegistrationService,
    KnowledgeDocumentRegistrationUnavailable,
    KnowledgeDocumentSlotOccupied,
    is_initial_document_registration_eligible,
)
from apps.shared.db.models.knowledge import (
    Document,
    DocumentVersion,
    KnowledgeBase,
    SourceType,
)


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
        self.db.locked = True
        return self

    def first(self):
        if self.db.query_error is not None:
            raise self.db.query_error
        if self.entity is KnowledgeBase:
            return self.db.kb
        if self.entity is DocumentVersion:
            return self.db.active_version
        return self.db.existing_document


class _Db:
    def __init__(
        self,
        kb,
        *,
        existing_document=None,
        active_version=None,
        query_error=None,
        flush_error=None,
        commit_error=None,
    ):
        self.kb = kb
        self.existing_document = existing_document
        self.active_version = active_version
        self.query_error = query_error
        self.flush_error = flush_error
        self.commit_error = commit_error
        self.locked = False
        self.refreshed_entities = []
        self.added = None
        self.committed = False
        self.rolled_back = False

    def query(self, entity):
        return _Query(self, entity)

    def add(self, value):
        self.added = value

    def commit(self):
        if self.commit_error is not None:
            raise self.commit_error
        self.committed = True

    def flush(self):
        if self.flush_error is not None:
            raise self.flush_error
        if self.added is not None and self.added.id is None:
            self.added.id = uuid.uuid4()

    def rollback(self):
        self.rolled_back = True

def _kb(**overrides):
    values = {
        "id": uuid.uuid4(),
        "organization_id": uuid.uuid4(),
        "lifecycle_state": "active",
        "source_identity_id": None,
        "sync_state": "manual",
        "active_document_version_id": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _register(service, kb):
    return service.register_initial_document(
        knowledge_base_id=kb.id,
        organization_id=kb.organization_id,
        filename="policy.md",
        file_path="owned-reference",
        chunk_size=500,
        chunk_overlap=50,
        source_type=SourceType.FILE,
        meta_info={"chunking_mode": "flat"},
    )


def test_register_initial_document_locks_kb_and_commits_one_pending_row():
    kb = _kb()
    db = _Db(kb)

    document_id = _register(KnowledgeDocumentRegistrationService(db), kb)

    assert db.locked is True
    assert db.refreshed_entities == [KnowledgeBase]
    assert db.committed is True
    assert db.rolled_back is False
    assert document_id == db.added.id
    assert isinstance(db.added, Document)
    assert db.added.knowledge_base_id == kb.id
    assert db.added.status == "pending"
    assert db.added.meta_info == {"chunking_mode": "flat"}


@pytest.mark.parametrize(
    "status",
    ["pending", "processing", "failed", "completed", "waiting_for_approval"],
)
def test_any_existing_document_state_occupies_the_slot(status):
    kb = _kb()
    db = _Db(kb, existing_document=(uuid.uuid4(), status))

    with pytest.raises(KnowledgeDocumentSlotOccupied):
        _register(KnowledgeDocumentRegistrationService(db), kb)

    assert db.locked is True
    assert db.added is None
    assert db.committed is False
    assert db.rolled_back is True


def test_completed_document_with_active_pointer_reports_slot_occupied():
    kb = _kb(active_document_version_id=uuid.uuid4())
    db = _Db(kb, existing_document=(uuid.uuid4(), "completed"))

    with pytest.raises(KnowledgeDocumentSlotOccupied):
        _register(KnowledgeDocumentRegistrationService(db), kb)

    assert db.added is None
    assert db.rolled_back is True


def test_stale_active_pointer_without_document_is_released_on_registration():
    active_version_id = uuid.uuid4()
    kb = _kb(active_document_version_id=active_version_id)
    active_version = SimpleNamespace(
        id=active_version_id,
        knowledge_base_id=kb.id,
        legacy_document_id=None,
        source_identity_id=None,
        status="ready",
        superseded_at=None,
    )
    db = _Db(kb, active_version=active_version)

    _register(KnowledgeDocumentRegistrationService(db), kb)

    assert kb.active_document_version_id is None
    assert active_version.status == "superseded"
    assert active_version.superseded_at is not None
    assert db.refreshed_entities == [KnowledgeBase, DocumentVersion]
    assert db.committed is True


def test_active_pointer_with_live_document_identity_fails_closed():
    active_version_id = uuid.uuid4()
    kb = _kb(active_document_version_id=active_version_id)
    active_version = SimpleNamespace(
        id=active_version_id,
        knowledge_base_id=kb.id,
        legacy_document_id=uuid.uuid4(),
        source_identity_id=None,
        status="ready",
    )
    db = _Db(kb, active_version=active_version)

    with pytest.raises(KnowledgeDocumentRegistrationPolicyDenied):
        _register(KnowledgeDocumentRegistrationService(db), kb)

    assert kb.active_document_version_id == active_version_id
    assert db.added is None
    assert db.rolled_back is True


def test_active_pointer_with_source_identity_fails_closed():
    active_version_id = uuid.uuid4()
    kb = _kb(active_document_version_id=active_version_id)
    active_version = SimpleNamespace(
        id=active_version_id,
        knowledge_base_id=kb.id,
        legacy_document_id=None,
        source_identity_id=uuid.uuid4(),
        status="ready",
    )
    db = _Db(kb, active_version=active_version)

    with pytest.raises(KnowledgeDocumentRegistrationPolicyDenied):
        _register(KnowledgeDocumentRegistrationService(db), kb)

    assert kb.active_document_version_id == active_version_id
    assert db.added is None
    assert db.rolled_back is True


def test_source_managed_kb_does_not_disclose_existing_document_slot():
    kb = _kb(source_identity_id=uuid.uuid4(), sync_state="synced")
    db = _Db(kb, existing_document=(uuid.uuid4(), "completed"))

    with pytest.raises(KnowledgeDocumentRegistrationPolicyDenied):
        _register(KnowledgeDocumentRegistrationService(db), kb)

    assert db.added is None
    assert db.rolled_back is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"source_identity_id": uuid.uuid4()},
        {"sync_state": "synced"},
        {"sync_state": "source_deleted"},
    ],
)
def test_source_managed_or_non_manual_kb_rejects_registration(overrides):
    kb = _kb(**overrides)
    db = _Db(kb)

    with pytest.raises(KnowledgeDocumentRegistrationPolicyDenied):
        _register(KnowledgeDocumentRegistrationService(db), kb)

    assert db.added is None
    assert db.rolled_back is True


def test_inactive_or_missing_kb_is_hidden():
    for kb in (_kb(lifecycle_state="archived"), None):
        db = _Db(kb)
        with pytest.raises(KnowledgeDocumentRegistrationHidden):
            KnowledgeDocumentRegistrationService(db).ensure_available(
                knowledge_base_id=uuid.uuid4(),
                organization_id=uuid.uuid4(),
            )


def test_database_failures_are_redacted_as_unavailable_and_rollback_mutation():
    kb = _kb()
    db = _Db(kb, commit_error=SQLAlchemyError("raw database detail"))

    with pytest.raises(KnowledgeDocumentRegistrationUnavailable) as exc_info:
        _register(KnowledgeDocumentRegistrationService(db), kb)

    assert "raw database detail" not in str(exc_info.value)
    assert db.rolled_back is True
    assert exc_info.value.artifact_cleanup_safe is False


def test_flush_failure_allows_request_owned_artifact_cleanup():
    kb = _kb()
    db = _Db(kb, flush_error=SQLAlchemyError("raw database detail"))

    with pytest.raises(KnowledgeDocumentRegistrationUnavailable) as exc_info:
        _register(KnowledgeDocumentRegistrationService(db), kb)

    assert exc_info.value.artifact_cleanup_safe is True
    assert db.rolled_back is True


def test_fast_precheck_maps_database_failure_without_raw_detail():
    db = _Db(None, query_error=SQLAlchemyError("raw database detail"))

    with pytest.raises(KnowledgeDocumentRegistrationUnavailable) as exc_info:
        KnowledgeDocumentRegistrationService(db).ensure_available(
            knowledge_base_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
        )

    assert "raw database detail" not in str(exc_info.value)


def test_policy_helper_does_not_treat_write_authority_as_source_eligibility():
    assert is_initial_document_registration_eligible(_kb()) is True
    assert (
        is_initial_document_registration_eligible(
            _kb(active_document_version_id=uuid.uuid4())
        )
        is True
    )
    assert (
        is_initial_document_registration_eligible(
            _kb(source_identity_id=uuid.uuid4())
        )
        is False
    )
