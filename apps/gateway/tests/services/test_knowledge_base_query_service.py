import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import SQLAlchemyError

from apps.gateway.services import knowledge_base_query_service as service_module
from apps.gateway.services.knowledge_base_query_service import (
    KnowledgeBaseCreateFailed,
    KnowledgeBaseNotFound,
    KnowledgeBaseQueryService,
    KnowledgeSchemaNotReady,
    KnowledgeValidationError,
    evaluate_llm_rag_selectability,
)
from apps.shared.db.models.knowledge import SourceType
from apps.shared.schemas.knowledge import KnowledgePermissionDecision
from apps.shared.services.knowledge_schema_readiness import (
    KnowledgeSchemaReadinessResult,
)


class FakeKnowledgeQuery:
    def __init__(self, rows):
        self.rows = rows
        self.filters = []

    def select_from(self, *_args, **_kwargs):
        return self

    def join(self, *_args, **_kwargs):
        return self

    def outerjoin(self, *_args, **_kwargs):
        return self

    def filter(self, *_args, **_kwargs):
        self.filters.extend(_args)
        return self

    def group_by(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def all(self):
        return self.rows


class FakeSelectableDb:
    def __init__(self, kbs):
        self.kbs = kbs
        self.query_entities = []
        self.queries = []

    def query(self, *entities):
        self.query_entities.append(entities)
        query = FakeKnowledgeQuery(self.kbs)
        self.queries.append(query)
        return query


def _compiled_filters(query) -> str:
    return " ".join(
        str(
            criterion.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
        for criterion in query.filters
    ).lower()


class FakeAuthorizedListDb:
    def __init__(self, kbs, document_stats):
        self.kbs = kbs
        self.document_stats = document_stats
        self.query_count = 0

    def query(self, *_entities):
        self.query_count += 1
        rows = self.kbs if self.query_count == 1 else self.document_stats
        return FakeKnowledgeQuery(rows)


class FakeAuthorizedPermissionHelper:
    def __init__(self, decisions):
        self.decisions = decisions
        self.actions = []

    def bulk_evaluate_kb_action(self, kbs, action):
        self.actions.append((list(kbs), action))
        return self.decisions


class FakeDetailQuery:
    def __init__(self, *, first_value=None, all_value=None):
        self.first_value = first_value
        self.all_value = all_value or []
        self.filters = []

    def select_from(self, *_args, **_kwargs):
        return self

    def join(self, *_args, **_kwargs):
        return self

    def outerjoin(self, *_args, **_kwargs):
        return self

    def filter(self, *_args, **_kwargs):
        self.filters.extend(_args)
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def group_by(self, *_args, **_kwargs):
        return self

    def first(self):
        return self.first_value

    def all(self):
        return self.all_value


class FakeDetailDb:
    def __init__(self, kb_row, doc_rows, chunk_rows=None):
        self.kb_row = kb_row
        self.doc_rows = doc_rows
        self.chunk_rows = chunk_rows or []
        self.query_count = 0
        self.query_entities = []
        self.queries = []

    def query(self, *entities):
        self.query_count += 1
        self.query_entities.append(entities)
        if self.query_count == 1:
            query = FakeDetailQuery(first_value=self.kb_row)
            self.queries.append(query)
            return query
        if self.query_count == 2:
            query = FakeDetailQuery(all_value=self.doc_rows)
            self.queries.append(query)
            return query
        query = FakeDetailQuery(all_value=self.chunk_rows)
        self.queries.append(query)
        return query


class FakeCreateDb:
    def __init__(self):
        self.added = None
        self.added_items = []
        self.committed = False
        self.rolled_back = False
        self.info = {}

    def add(self, item):
        self.added_items.append(item)
        if isinstance(item, service_module.KnowledgeBase):
            self.added = item

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def refresh(self, item):
        item.id = item.id or uuid.uuid4()
        item.created_at = item.created_at or datetime.now(timezone.utc)
        item.updated_at = item.updated_at or item.created_at


class FailingCreateDb(FakeCreateDb):
    def commit(self):
        raise SQLAlchemyError("simulated write failure")


def test_list_authorized_returns_only_active_org_kbs_with_read_permission():
    organization_id = uuid.uuid4()
    allowed_id = uuid.uuid4()
    denied_id = uuid.uuid4()
    now = datetime(2026, 7, 13, 1, tzinfo=timezone.utc)
    document_updated_at = datetime(2026, 7, 13, 2, tzinfo=timezone.utc)
    allowed = SimpleNamespace(
        id=allowed_id,
        organization_id=organization_id,
        lifecycle_state="active",
        source_identity_id=None,
        name="읽기 허용 KB",
        description=None,
        safe_metadata={"safe_label": "허용"},
        embedding_model="text-embedding-3-small",
        created_at=now,
        updated_at=now,
    )
    denied = SimpleNamespace(
        id=denied_id,
        organization_id=organization_id,
        lifecycle_state="active",
        source_identity_id=None,
        name="읽기 거부 KB",
        description=None,
        safe_metadata={},
        embedding_model="text-embedding-3-small",
        created_at=now,
        updated_at=now,
    )
    permission_helper = FakeAuthorizedPermissionHelper(
        {
            allowed_id: KnowledgePermissionDecision(
                allowed=True,
                resource_visibility="visible",
            ),
            denied_id: KnowledgePermissionDecision(allowed=False),
        }
    )
    db = FakeAuthorizedListDb(
        [allowed, denied],
        [(allowed_id, 2, document_updated_at, [SourceType.FILE, "API"])],
    )

    response = KnowledgeBaseQueryService(
        db,
        permission_helper_factory=lambda *_args, **_kwargs: permission_helper,
    ).list_authorized(
        user_id=uuid.uuid4(),
        organization_id=organization_id,
        schema_ready=True,
    )

    assert permission_helper.actions == [([allowed, denied], "read")]
    assert [item.id for item in response] == [allowed_id]
    assert response[0].document_count == 2
    assert response[0].updated_at == document_updated_at
    assert response[0].source_types == ["FILE", "API"]
    assert response[0].safe_metadata == {"safe_label": "허용"}
    assert db.query_count == 2


def test_list_authorized_does_not_query_document_stats_when_all_kbs_are_hidden():
    organization_id = uuid.uuid4()
    kb = SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=organization_id,
        lifecycle_state="active",
        source_identity_id=None,
    )
    permission_helper = FakeAuthorizedPermissionHelper(
        {kb.id: KnowledgePermissionDecision(allowed=False)}
    )
    db = FakeAuthorizedListDb([kb], [])

    response = KnowledgeBaseQueryService(
        db,
        permission_helper_factory=lambda *_args, **_kwargs: permission_helper,
    ).list_authorized(
        user_id=uuid.uuid4(),
        organization_id=organization_id,
        schema_ready=True,
    )

    assert response == []
    assert db.query_count == 1


def test_get_detail_maps_documents_without_full_kb_orm_load():
    knowledge_base_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    now = datetime(2026, 7, 7, 1, tzinfo=timezone.utc)
    doc_id = uuid.uuid4()
    db = FakeDetailDb(
        (
            knowledge_base_id,
            organization_id,
            "사내 정책 KB",
            "테스트 상세",
            "text-embedding-3-small",
            now,
            None,
            None,
        ),
        [
            (
                doc_id,
                "policy.pdf",
                "completed",
                now,
                None,
                None,
                SourceType.FILE,
                {"category": "policy"},
            )
        ],
    )

    response = KnowledgeBaseQueryService(
        db,
        column_exists=lambda *_args, **_kwargs: False,
        finalize_processing_start=lambda *_args, **_kwargs: False,
        recover_processing_timeout=lambda *_args, **_kwargs: False,
    ).get_detail(
        knowledge_base_id,
        organization_scope=organization_id,
        has_organization_id=True,
    )

    assert all(
        entity is not service_module.KnowledgeBase
        for query_entities in db.query_entities
        for entity in query_entities
    )
    assert response.id == knowledge_base_id
    assert response.organization_id == organization_id
    assert response.document_count == 1
    assert response.source_types == ["FILE"]
    assert response.documents[0].id == doc_id
    assert response.documents[0].chunk_count == 0


def test_get_detail_only_projects_initial_registration_for_empty_kb():
    knowledge_base_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    now = datetime(2026, 7, 15, 1, tzinfo=timezone.utc)

    empty_response = KnowledgeBaseQueryService(
        FakeDetailDb(
            (
                knowledge_base_id,
                organization_id,
                "빈 KB",
                None,
                "text-embedding-3-small",
                now,
                None,
                None,
            ),
            [],
        ),
        column_exists=lambda *_args, **_kwargs: False,
        finalize_processing_start=lambda *_args, **_kwargs: False,
        recover_processing_timeout=lambda *_args, **_kwargs: False,
    ).get_detail(
        knowledge_base_id,
        organization_scope=organization_id,
        has_organization_id=True,
        can_register_initial_document=True,
    )
    occupied_response = KnowledgeBaseQueryService(
        FakeDetailDb(
            (
                knowledge_base_id,
                organization_id,
                "사용 중 KB",
                None,
                "text-embedding-3-small",
                now,
                None,
                None,
            ),
            [
                (
                    uuid.uuid4(),
                    "policy.md",
                    "failed",
                    now,
                    None,
                    None,
                    SourceType.FILE,
                    {},
                )
            ],
        ),
        column_exists=lambda *_args, **_kwargs: False,
        finalize_processing_start=lambda *_args, **_kwargs: False,
        recover_processing_timeout=lambda *_args, **_kwargs: False,
    ).get_detail(
        knowledge_base_id,
        organization_scope=organization_id,
        has_organization_id=True,
        can_register_initial_document=True,
    )

    assert empty_response.can_register_initial_document is True
    assert occupied_response.can_register_initial_document is False


def test_list_llm_selectable_uses_kb_use_permission_and_ready_boundary(monkeypatch):
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    now = datetime(2026, 7, 7, 1, tzinfo=timezone.utc)
    ready_allowed_id = uuid.uuid4()
    ready_denied_id = uuid.uuid4()
    not_ready_allowed_id = uuid.uuid4()
    kbs = [
        SimpleNamespace(
            id=ready_allowed_id,
            organization_id=organization_id,
            lifecycle_state="active",
            source_identity_id=None,
            name="권한 있는 완료 KB",
            description=None,
            embedding_model="text-embedding-3-small",
            created_at=now,
            updated_at=now,
        ),
        SimpleNamespace(
            id=ready_denied_id,
            organization_id=organization_id,
            lifecycle_state="active",
            source_identity_id=None,
            name="권한 없는 완료 KB",
            description=None,
            embedding_model="text-embedding-3-small",
            created_at=now,
            updated_at=now,
        ),
        SimpleNamespace(
            id=not_ready_allowed_id,
            organization_id=organization_id,
            lifecycle_state="active",
            source_identity_id=None,
            name="권한 있는 처리 전 KB",
            description=None,
            embedding_model="text-embedding-3-small",
            created_at=now,
            updated_at=now,
        ),
    ]
    captured = {}

    class FakePermissionHelper:
        def __init__(self, _db, *, user_id, organization_id):
            captured["user_id"] = user_id
            captured["organization_id"] = organization_id

        def bulk_evaluate_kb_use(self, kbs):
            return {
                kb.id: SimpleNamespace(allowed=kb.id != ready_denied_id)
                for kb in kbs
            }

    service = KnowledgeBaseQueryService(
        FakeSelectableDb(kbs),
        permission_helper_factory=FakePermissionHelper,
    )
    detail_by_id = {
        ready_allowed_id: service_module.KnowledgeBaseDetailResponse(
            id=ready_allowed_id,
            organization_id=organization_id,
            name="권한 있는 완료 KB",
            description=None,
            document_count=1,
            created_at=now,
            updated_at=now,
            source_types=["FILE"],
            embedding_model="text-embedding-3-small",
            documents=[
                service_module.DocumentResponse(
                    id=uuid.uuid4(),
                    filename="ready.md",
                    status="completed",
                    created_at=now,
                    updated_at=now,
                    chunk_count=1,
                    token_count=10,
                )
            ],
        ),
        ready_denied_id: service_module.KnowledgeBaseDetailResponse(
            id=ready_denied_id,
            organization_id=organization_id,
            name="권한 없는 완료 KB",
            description=None,
            document_count=1,
            created_at=now,
            updated_at=now,
            source_types=["FILE"],
            embedding_model="text-embedding-3-small",
            documents=[
                service_module.DocumentResponse(
                    id=uuid.uuid4(),
                    filename="denied.md",
                    status="completed",
                    created_at=now,
                    updated_at=now,
                    chunk_count=1,
                    token_count=10,
                )
            ],
        ),
        not_ready_allowed_id: service_module.KnowledgeBaseDetailResponse(
            id=not_ready_allowed_id,
            organization_id=organization_id,
            name="권한 있는 처리 전 KB",
            description=None,
            document_count=1,
            created_at=now,
            updated_at=now,
            source_types=["FILE"],
            embedding_model="text-embedding-3-small",
            documents=[
                service_module.DocumentResponse(
                    id=uuid.uuid4(),
                    filename="pending.md",
                    status="pending",
                    created_at=now,
                    updated_at=now,
                    chunk_count=0,
                    token_count=0,
                )
            ],
        ),
    }
    monkeypatch.setattr(
        service,
        "_detail_response_from_kb",
        lambda kb: detail_by_id[kb.id],
    )

    response = service.list_llm_selectable(
        user_id=user_id,
        organization_id=organization_id,
        schema_ready=True,
    )

    assert captured == {"user_id": user_id, "organization_id": organization_id}
    assert [item.id for item in response] == [ready_allowed_id]


def test_list_llm_selectable_excludes_completed_document_without_visible_chunks(
    monkeypatch,
):
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    now = datetime(2026, 7, 7, 1, tzinfo=timezone.utc)
    kb_id = uuid.uuid4()
    kb = SimpleNamespace(
        id=kb_id,
        organization_id=organization_id,
        lifecycle_state="active",
        source_identity_id=None,
        name="완료됐지만 검색 불가 KB",
        description=None,
        embedding_model="text-embedding-3-small",
        created_at=now,
        updated_at=now,
    )

    class FakePermissionHelper:
        def __init__(self, _db, *, user_id, organization_id):
            pass

        def bulk_evaluate_kb_use(self, kbs):
            return {item.id: SimpleNamespace(allowed=True) for item in kbs}

    service = KnowledgeBaseQueryService(
        FakeSelectableDb([kb]),
        permission_helper_factory=FakePermissionHelper,
    )
    monkeypatch.setattr(
        service,
        "_detail_response_from_kb",
        lambda _kb: service_module.KnowledgeBaseDetailResponse(
            id=kb_id,
            organization_id=organization_id,
            name="완료됐지만 검색 불가 KB",
            description=None,
            document_count=1,
            created_at=now,
            updated_at=now,
            source_types=["FILE"],
            embedding_model="text-embedding-3-small",
            documents=[
                service_module.DocumentResponse(
                    id=uuid.uuid4(),
                    filename="empty-ready.md",
                    status="completed",
                    created_at=now,
                    updated_at=now,
                    chunk_count=0,
                    token_count=0,
                )
            ],
        ),
    )

    response = service.list_llm_selectable(
        user_id=user_id,
        organization_id=organization_id,
        schema_ready=True,
    )

    assert response == []


def test_list_llm_selectable_query_excludes_source_deleted_and_not_ready_kbs():
    db = FakeSelectableDb([])
    service = KnowledgeBaseQueryService(db)

    assert service.list_llm_selectable(
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        schema_ready=True,
    ) == []

    sql = _compiled_filters(db.queries[0])
    assert "knowledge_bases.lifecycle_state = 'active'" in sql
    assert "knowledge_bases.sync_state != 'source_deleted'" in sql
    assert "documents.status = 'completed'" in sql
    assert "document_chunks.document_version_id = knowledge_bases.active_document_version_id" in sql


def test_get_detail_raises_not_found_for_missing_or_hidden_kb():
    db = FakeDetailDb(None, [])

    with pytest.raises(KnowledgeBaseNotFound):
        KnowledgeBaseQueryService(db).get_detail(
            uuid.uuid4(),
            organization_scope=uuid.uuid4(),
            has_organization_id=True,
        )


def test_get_detail_uses_chunk_counts_when_chunk_table_is_available():
    knowledge_base_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    now = datetime(2026, 7, 7, 1, tzinfo=timezone.utc)
    ready_doc_id = uuid.uuid4()
    empty_doc_id = uuid.uuid4()
    db = FakeDetailDb(
        (
            knowledge_base_id,
            organization_id,
            "청크 집계 KB",
            None,
            "text-embedding-3-small",
            now,
            None,
            None,
        ),
        [
            (
                ready_doc_id,
                "ready.pdf",
                "completed",
                now,
                None,
                None,
                SourceType.FILE,
                {},
            ),
            (
                empty_doc_id,
                "empty.pdf",
                "completed",
                now,
                None,
                None,
                SourceType.FILE,
                {},
            ),
        ],
        [(ready_doc_id, 2)],
    )

    response = KnowledgeBaseQueryService(
        db,
        column_exists=lambda *_args, **_kwargs: True,
        finalize_processing_start=lambda *_args, **_kwargs: False,
        recover_processing_timeout=lambda *_args, **_kwargs: False,
    ).get_detail(
        knowledge_base_id,
        organization_scope=organization_id,
        has_organization_id=True,
    )

    assert {doc.id: doc.chunk_count for doc in response.documents} == {
        ready_doc_id: 2,
        empty_doc_id: 0,
    }


def test_get_detail_normalizes_non_dict_meta_info_to_safe_empty_dict():
    knowledge_base_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    now = datetime(2026, 7, 7, 1, tzinfo=timezone.utc)
    document_id = uuid.uuid4()
    db = FakeDetailDb(
        (
            knowledge_base_id,
            organization_id,
            "비정상 메타데이터 KB",
            None,
            "text-embedding-3-small",
            now,
            None,
            None,
        ),
        [
            (
                document_id,
                "corrupt-meta.pdf",
                "completed",
                now,
                None,
                None,
                SourceType.FILE,
                ["unexpected", "metadata"],
            ),
        ],
        [(document_id, 1)],
    )

    response = KnowledgeBaseQueryService(
        db,
        column_exists=lambda *_args, **_kwargs: True,
        finalize_processing_start=lambda *_args, **_kwargs: False,
        recover_processing_timeout=lambda *_args, **_kwargs: False,
    ).get_detail(
        knowledge_base_id,
        organization_scope=organization_id,
        has_organization_id=True,
    )

    assert response.documents[0].meta_info == {}


def test_get_detail_projects_internal_document_metadata_to_safe_allowlist():
    knowledge_base_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    now = datetime(2026, 7, 7, 1, tzinfo=timezone.utc)
    document_id = uuid.uuid4()
    db = FakeDetailDb(
        (
            knowledge_base_id,
            organization_id,
            "문서 메타데이터 경계 KB",
            None,
            "text-embedding-3-small",
            now,
            None,
            None,
        ),
        [
            (
                document_id,
                "safe-document.pdf",
                "processing",
                now,
                None,
                None,
                SourceType.API,
                {
                    "progress": 25,
                    "chunking_mode": "flat",
                    "api_config": {"url_encrypted": None},
                    "connection_id": None,
                    "source_identity_id": None,
                },
            ),
        ],
        [],
    )

    response = KnowledgeBaseQueryService(
        db,
        column_exists=lambda *_args, **_kwargs: True,
        finalize_processing_start=lambda *_args, **_kwargs: False,
        recover_processing_timeout=lambda *_args, **_kwargs: False,
    ).get_detail(
        knowledge_base_id,
        organization_scope=organization_id,
        has_organization_id=True,
    )

    assert response.documents[0].meta_info == {
        "progress": 25,
        "chunking_mode": "flat",
    }


def test_get_detail_replaces_persisted_document_failure_detail():
    knowledge_base_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    now = datetime(2026, 7, 7, 1, tzinfo=timezone.utc)
    document_id = uuid.uuid4()
    db = FakeDetailDb(
        (
            knowledge_base_id,
            organization_id,
            "문서 실패 경계 KB",
            None,
            "text-embedding-3-small",
            now,
            None,
            None,
        ),
        [
            (
                document_id,
                "safe-document.pdf",
                "failed",
                now,
                None,
                "legacy-internal-exception-marker",
                SourceType.FILE,
                {"processing_current_step": "legacy-step-marker"},
            ),
        ],
        [],
    )

    response = KnowledgeBaseQueryService(
        db,
        column_exists=lambda *_args, **_kwargs: True,
        finalize_processing_start=lambda *_args, **_kwargs: False,
        recover_processing_timeout=lambda *_args, **_kwargs: False,
    ).get_detail(
        knowledge_base_id,
        organization_scope=organization_id,
        has_organization_id=True,
    )

    document = response.documents[0]
    assert document.error_message == (
        "Document processing failed. You can retry the document."
    )
    assert document.meta_info == {}
    serialized = repr(document.model_dump(mode="json"))
    assert "legacy-internal-exception-marker" not in serialized
    assert "legacy-step-marker" not in serialized


def test_get_detail_counts_only_active_ready_version_chunks_for_selectability():
    knowledge_base_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    active_version_id = uuid.uuid4()
    now = datetime(2026, 7, 7, 1, tzinfo=timezone.utc)
    document_id = uuid.uuid4()
    db = FakeDetailDb(
        (
            knowledge_base_id,
            organization_id,
            "버전 청크 KB",
            None,
            "text-embedding-3-small",
            now,
            None,
            active_version_id,
        ),
        [
            (
                document_id,
                "versioned.pdf",
                "completed",
                now,
                None,
                None,
                SourceType.FILE,
                {},
            ),
        ],
        [(document_id, 1)],
    )

    response = KnowledgeBaseQueryService(
        db,
        column_exists=lambda *_args, **_kwargs: True,
        finalize_processing_start=lambda *_args, **_kwargs: False,
        recover_processing_timeout=lambda *_args, **_kwargs: False,
    ).get_detail(
        knowledge_base_id,
        organization_scope=organization_id,
        has_organization_id=True,
    )

    chunk_filter_sql = " ".join(str(item) for item in db.queries[2].filters)
    assert response.documents[0].chunk_count == 1
    assert "document_chunks.document_version_id" in chunk_filter_sql
    assert "document_versions.status" in chunk_filter_sql


def test_llm_rag_selectability_reports_not_ready_when_completed_document_has_no_chunks():
    detail = service_module.KnowledgeBaseDetailResponse(
        id=uuid.uuid4(),
        organization_id=None,
        name="청크 없는 완료 KB",
        description=None,
        document_count=1,
        created_at=datetime(2026, 7, 7, 1, tzinfo=timezone.utc),
        updated_at=None,
        source_types=["FILE"],
        embedding_model="text-embedding-3-small",
        documents=[
            service_module.DocumentResponse(
                id=uuid.uuid4(),
                filename="empty-completed.pdf",
                status="completed",
                created_at=datetime(2026, 7, 7, 1, tzinfo=timezone.utc),
                chunk_count=0,
            )
        ],
    )

    selectability = evaluate_llm_rag_selectability(detail)

    assert selectability.available is False
    assert selectability.state == "not_ready"
    assert selectability.safe_reason_code == "no_completed_document_chunks"
    assert selectability.completed_document_count == 0
    assert selectability.document_count == 1


def test_llm_rag_selectability_reports_not_ready_without_completed_document():
    detail = service_module.KnowledgeBaseDetailResponse(
        id=uuid.uuid4(),
        organization_id=None,
        name="처리 중 KB",
        description=None,
        document_count=1,
        created_at=datetime(2026, 7, 7, 1, tzinfo=timezone.utc),
        updated_at=None,
        source_types=["FILE"],
        embedding_model="text-embedding-3-small",
        documents=[
            service_module.DocumentResponse(
                id=uuid.uuid4(),
                filename="pending.pdf",
                status="processing",
                created_at=datetime(2026, 7, 7, 1, tzinfo=timezone.utc),
            )
        ],
    )

    selectability = evaluate_llm_rag_selectability(detail)

    assert selectability.available is False
    assert selectability.state == "not_ready"
    assert selectability.safe_reason_code == "no_completed_documents"
    assert selectability.completed_document_count == 0
    assert selectability.document_count == 1


def test_llm_rag_selectability_reports_empty_kb_as_not_ready():
    detail = service_module.KnowledgeBaseDetailResponse(
        id=uuid.uuid4(),
        organization_id=None,
        name="빈 KB",
        description=None,
        document_count=0,
        created_at=datetime(2026, 7, 7, 1, tzinfo=timezone.utc),
        updated_at=None,
        source_types=[],
        embedding_model="text-embedding-3-small",
        documents=[],
    )

    selectability = evaluate_llm_rag_selectability(detail)

    assert selectability.available is False
    assert selectability.safe_reason_code == "no_documents"
    assert selectability.document_count == 0


def test_create_raises_schema_not_ready_without_http_dependency(monkeypatch):
    monkeypatch.setattr(
        service_module,
        "check_knowledge_schema_readiness",
        lambda *_args, **_kwargs: KnowledgeSchemaReadinessResult(
            missing_columns={"knowledge_bases": ["sync_state"]},
        ),
    )

    with pytest.raises(KnowledgeSchemaNotReady) as exc_info:
        KnowledgeBaseQueryService(FakeCreateDb()).create(
            service_module.KnowledgeBaseCreate(name="stale schema KB"),
            user_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
        )

    assert exc_info.value.missing_columns == {"knowledge_bases": ["sync_state"]}


def test_create_rolls_back_on_write_failure():
    db = FailingCreateDb()

    with pytest.raises(KnowledgeBaseCreateFailed):
        KnowledgeBaseQueryService(db).create(
            service_module.KnowledgeBaseCreate(name="쓰기 실패 KB"),
            user_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            schema_ready=True,
        )

    assert db.rolled_back is True


@pytest.mark.parametrize(
    ("name", "embedding_model", "reason"),
    [
        ("   ", "text-embedding-3-small", "name_required"),
        ("가" * 256, "text-embedding-3-small", "name_too_long"),
        ("정책 KB", "", "embedding_model_invalid"),
        ("정책 KB", "   ", "embedding_model_invalid"),
        ("정책 KB", "sk-secret-like-model", "embedding_model_invalid"),
        ("정책 KB", "../bad model", "embedding_model_invalid"),
    ],
)
def test_create_validates_request_before_db_write(name, embedding_model, reason):
    db = FakeCreateDb()

    with pytest.raises(KnowledgeValidationError) as exc_info:
        KnowledgeBaseQueryService(db).create(
            service_module.KnowledgeBaseCreate(
                name=name,
                embedding_model=embedding_model,
            ),
            user_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            schema_ready=True,
        )

    assert exc_info.value.reason == reason
    assert db.added is None
    assert db.committed is False
    assert db.rolled_back is False


def test_create_trims_name_and_embedding_model_before_insert():
    db = FakeCreateDb()

    response = KnowledgeBaseQueryService(db).create(
        service_module.KnowledgeBaseCreate(
            name="  정책 KB  ",
            embedding_model=" text-embedding-3-small ",
        ),
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        schema_ready=True,
    )

    assert response.name == "정책 KB"
    assert response.embedding_model == "text-embedding-3-small"
    assert db.added.name == "정책 KB"
    assert db.added.embedding_model == "text-embedding-3-small"


def test_create_allows_duplicate_display_names_with_distinct_ids():
    class TrackingCreateDb(FakeCreateDb):
        def __init__(self):
            super().__init__()
            self.knowledge_bases = []

        def add(self, item):
            super().add(item)
            if isinstance(item, service_module.KnowledgeBase):
                self.knowledge_bases.append(item)

    db = TrackingCreateDb()
    service = KnowledgeBaseQueryService(db)
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()

    first = service.create(
        service_module.KnowledgeBaseCreate(name="중복 표시명 KB"),
        user_id=user_id,
        organization_id=organization_id,
        schema_ready=True,
    )
    second = service.create(
        service_module.KnowledgeBaseCreate(name="중복 표시명 KB"),
        user_id=user_id,
        organization_id=organization_id,
        schema_ready=True,
    )

    assert first.name == second.name == "중복 표시명 KB"
    assert first.id != second.id
    assert [item.name for item in db.knowledge_bases] == [
        "중복 표시명 KB",
        "중복 표시명 KB",
    ]


def test_create_bootstraps_creator_manager_and_audits_in_one_commit():
    db = FakeCreateDb()
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()

    response = KnowledgeBaseQueryService(db).create(
        service_module.KnowledgeBaseCreate(name="원자 생성 KB"),
        user_id=user_id,
        organization_id=organization_id,
        schema_ready=True,
    )

    permissions = [
        item
        for item in db.added_items
        if isinstance(item, service_module.UserKnowledgePermission)
    ]
    audits = [
        item for item in db.added_items if isinstance(item, service_module.AuditLog)
    ]
    assert response.id == db.added.id
    assert len(permissions) == 1
    assert permissions[0].knowledge_base_id == response.id
    assert permissions[0].user_id == user_id
    assert permissions[0].auth_state == "manager"
    assert {audit.action for audit in audits} == {
        "knowledge.created",
        "user_knowledge_permission.created",
    }
    assert db.committed is True
