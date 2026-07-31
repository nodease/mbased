import uuid
from types import SimpleNamespace

from sqlalchemy.dialects import postgresql

from apps.gateway.services.knowledge_collection_picker_query_service import (
    MAX_LLM_SELECTABLE_COLLECTION_SCAN,
    KnowledgeCollectionPickerQueryService,
)


class _Query:
    def __init__(self, rows):
        self.rows = rows
        self.filters = []
        self.limit_value = None
        self.events = []

    def options(self, *_args):
        self.events.append("options")
        return self

    def filter(self, *criteria):
        self.events.append("filter")
        self.filters.extend(criteria)
        return self

    def order_by(self, *_args):
        self.events.append("order_by")
        return self

    def limit(self, value):
        self.events.append("limit")
        self.limit_value = value
        return self

    def all(self):
        self.events.append("all")
        if self.limit_value is None:
            return self.rows
        return self.rows[: self.limit_value]


class _Db:
    def __init__(self, rows):
        self.query_value = _Query(rows)

    def query(self, _model):
        return self.query_value


def _collection(*, organization_id, safe_metadata=None, source_identity=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=organization_id,
        lifecycle_state="active",
        name="RAW COLLECTION NAME",
        description="RAW DESCRIPTION",
        safe_metadata=safe_metadata or {},
        source_identity_id=(uuid.uuid4() if source_identity is not None else None),
        source_identity=source_identity,
    )


def _scope_to_ids(monkeypatch, service, allowed_ids):
    allowed_ids = set(allowed_ids)

    def _scope(query, action):
        assert action == "route"
        query.events.append("permission_scope")
        query.rows = [row for row in query.rows if row.id in allowed_ids]
        return query

    monkeypatch.setattr(
        service.permission_helper,
        "scope_collection_query_for_action",
        _scope,
    )


def _compiled_filters(query):
    return " ".join(
        str(
            criterion.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
        for criterion in query.filters
    ).lower()


def test_picker_returns_only_route_allowed_minimal_projection(monkeypatch):
    organization_id = uuid.uuid4()
    allowed = _collection(
        organization_id=organization_id,
        safe_metadata={"safe_label": "사내 규정"},
    )
    denied = _collection(
        organization_id=organization_id,
        safe_metadata={"safe_label": "숨김 문서"},
    )
    db = _Db([allowed, denied])
    service = KnowledgeCollectionPickerQueryService(
        db,
        user_id=uuid.uuid4(),
        organization_id=organization_id,
    )
    _scope_to_ids(monkeypatch, service, [allowed.id])

    response = service.list_llm_selectable()

    assert response.model_dump(mode="json") == {
        "collections": [{"id": str(allowed.id), "safe_label": "사내 규정"}]
    }
    assert db.query_value.limit_value == MAX_LLM_SELECTABLE_COLLECTION_SCAN
    assert len(db.query_value.filters) == 3
    assert "knowledge_collections.sync_state != 'source_deleted'" in _compiled_filters(
        db.query_value
    )
    assert db.query_value.events == [
        "options",
        "filter",
        "permission_scope",
        "order_by",
        "limit",
        "all",
    ]
    assert "RAW COLLECTION NAME" not in response.model_dump_json()
    assert "RAW DESCRIPTION" not in response.model_dump_json()


def test_source_managed_label_requires_active_approved_display_policy(monkeypatch):
    organization_id = uuid.uuid4()
    approved = _collection(
        organization_id=organization_id,
        source_identity=SimpleNamespace(
            display_policy_state="approved",
            is_active=True,
            safe_display_name="공개 승인 라벨",
        ),
    )
    unapproved = _collection(
        organization_id=organization_id,
        source_identity=SimpleNamespace(
            display_policy_state="unreviewed",
            is_active=True,
            safe_display_name="노출 금지 라벨",
        ),
    )
    db = _Db([approved, unapproved])
    service = KnowledgeCollectionPickerQueryService(
        db,
        user_id=uuid.uuid4(),
        organization_id=organization_id,
    )
    _scope_to_ids(monkeypatch, service, [approved.id, unapproved.id])

    response = service.list_llm_selectable()

    assert [item.safe_label for item in response.collections] == [
        "공개 승인 라벨",
        None,
    ]


def test_permission_scope_is_applied_before_scan_limit(monkeypatch):
    organization_id = uuid.uuid4()
    denied_recent = [
        _collection(organization_id=organization_id) for _ in range(500)
    ]
    allowed_older = _collection(
        organization_id=organization_id,
        safe_metadata={"safe_label": "older allowed"},
    )
    db = _Db([*denied_recent, allowed_older])
    service = KnowledgeCollectionPickerQueryService(
        db,
        user_id=uuid.uuid4(),
        organization_id=organization_id,
    )
    _scope_to_ids(monkeypatch, service, [allowed_older.id])

    response = service.list_llm_selectable()

    assert [item.id for item in response.collections] == [allowed_older.id]
    assert db.query_value.events.index("permission_scope") < db.query_value.events.index(
        "limit"
    )


def test_permission_scoped_result_is_capped_at_scan_limit(monkeypatch):
    organization_id = uuid.uuid4()
    allowed = [
        _collection(organization_id=organization_id) for _ in range(501)
    ]
    db = _Db(allowed)
    service = KnowledgeCollectionPickerQueryService(
        db,
        user_id=uuid.uuid4(),
        organization_id=organization_id,
    )
    _scope_to_ids(monkeypatch, service, [row.id for row in allowed])

    response = service.list_llm_selectable()

    assert len(response.collections) == MAX_LLM_SELECTABLE_COLLECTION_SCAN
    assert [item.id for item in response.collections] == [
        row.id for row in allowed[:MAX_LLM_SELECTABLE_COLLECTION_SCAN]
    ]
