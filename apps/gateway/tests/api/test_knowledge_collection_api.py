import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from apps.gateway.api.v1.endpoints import knowledge as knowledge_endpoint
from apps.gateway.application.knowledge_collection_sync.use_cases import (
    CollectionSyncJobSnapshot,
    CollectionSyncPolicyBlocked,
    CollectionSyncRequestResult,
)
from apps.gateway.auth.dependencies import get_current_user
from apps.gateway.main import app
from apps.gateway.application.knowledge_administration.collection_operations import (
    CollectionStateConflict,
)
from apps.gateway.services.knowledge_collection_service import (
    KnowledgeCollectionServiceError,
)
from apps.shared.schemas.knowledge import (
    KnowledgeCollectionItemsResponse,
    KnowledgeCollectionLLMSelectableResponse,
    KnowledgeCollectionResponse,
    KnowledgeCollectionVisibilityResponse,
)


def _collection_response(collection_id=None, organization_id=None):
    return KnowledgeCollectionResponse(
        id=collection_id or uuid.uuid4(),
        organization_id=organization_id or uuid.uuid4(),
        name="HR Policies",
        description="Safe collection",
        visibility="private",
        linked_kb_count_bucket="1",
        active_kb_count_bucket="1",
        can_read=True,
        can_route=True,
        can_manage=True,
        can_sync=False,
        safe_metadata={"category": "hr"},
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def _sync_job(collection_id: uuid.UUID) -> CollectionSyncJobSnapshot:
    return CollectionSyncJobSnapshot(
        job_id=uuid.uuid4(),
        collection_id=collection_id,
        status="queued",
        total_count=7,
        completed_count=0,
        failed_count=0,
        skipped_count=0,
        retryable=True,
        safe_reason_code=None,
        requested_at=datetime.now(timezone.utc),
        started_at=None,
        completed_at=None,
    )


def test_collection_sync_request_uses_canonical_key_and_safe_projection(monkeypatch):
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    collection_id = uuid.uuid4()
    job = _sync_job(collection_id)
    captured = {}

    monkeypatch.setattr(
        knowledge_endpoint,
        "resolve_active_organization_id",
        lambda db, request, raw, current_user_id: organization_id,
    )

    class RequestUseCase:
        def execute(self, command):
            captured["command"] = command
            return CollectionSyncRequestResult(
                job=job,
                reused=False,
                dispatch_deferred=False,
            )

    monkeypatch.setattr(
        knowledge_endpoint,
        "build_knowledge_collection_sync_use_cases",
        lambda db: SimpleNamespace(request=RequestUseCase()),
    )
    app.dependency_overrides[knowledge_endpoint.get_db] = lambda: object()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)
    key = uuid.uuid4()
    try:
        response = TestClient(app).post(
            f"/api/v1/knowledge/collections/{collection_id}/sync-jobs",
            headers={
                "X-Organization-Id": str(organization_id),
                "Idempotency-Key": str(key),
            },
        )
    finally:
        app.dependency_overrides = {}

    assert response.status_code == 202
    assert captured["command"].idempotency_key == key
    assert captured["command"].organization_id == organization_id
    body = response.json()
    assert body["job"]["job_id"] == str(job.job_id)
    assert body["job"]["progress"] == "none"
    assert "total_count" not in str(body)
    assert "document" not in str(body)


def test_collection_sync_request_rejects_noncanonical_key_before_composition(
    monkeypatch,
):
    user_id = uuid.uuid4()
    collection_id = uuid.uuid4()
    built = False

    def build(_db):
        nonlocal built
        built = True
        raise AssertionError("composition must not run")

    monkeypatch.setattr(
        knowledge_endpoint, "build_knowledge_collection_sync_use_cases", build
    )
    app.dependency_overrides[knowledge_endpoint.get_db] = lambda: object()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)
    try:
        response = TestClient(app).post(
            f"/api/v1/knowledge/collections/{collection_id}/sync-jobs",
            headers={
                "X-Organization-Id": str(uuid.uuid4()),
                "Idempotency-Key": "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA",
            },
        )
    finally:
        app.dependency_overrides = {}

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation.failed"
    assert built is False


@pytest.mark.parametrize(
    ("reason_code", "expected_code", "expected_policy_reason"),
    [
        (
            "sync.no_eligible_targets",
            "sync.no_eligible_targets",
            "sync.no_eligible_targets",
        ),
        (
            "sync.target_limit_exceeded",
            "sync.target_limit_exceeded",
            "sync.target_limit_exceeded",
        ),
        ("unsafe.internal.reason", "policy.blocked", "sync.internal_error"),
    ],
)
def test_collection_sync_policy_reason_uses_safe_api_code(
    monkeypatch,
    reason_code,
    expected_code,
    expected_policy_reason,
):
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    collection_id = uuid.uuid4()

    monkeypatch.setattr(
        knowledge_endpoint,
        "resolve_active_organization_id",
        lambda db, request, raw, current_user_id: organization_id,
    )

    class RequestUseCase:
        def execute(self, command):
            raise CollectionSyncPolicyBlocked(reason_code)

    monkeypatch.setattr(
        knowledge_endpoint,
        "build_knowledge_collection_sync_use_cases",
        lambda db: SimpleNamespace(request=RequestUseCase()),
    )
    app.dependency_overrides[knowledge_endpoint.get_db] = lambda: object()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)
    try:
        response = TestClient(app).post(
            f"/api/v1/knowledge/collections/{collection_id}/sync-jobs",
            headers={
                "X-Organization-Id": str(organization_id),
                "Idempotency-Key": str(uuid.uuid4()),
            },
        )
    finally:
        app.dependency_overrides = {}

    assert response.status_code == 409
    body = response.json()["error"]
    assert body["code"] == expected_code
    assert body["details"] == {"policy_reason": expected_policy_reason}
    assert reason_code not in str(body) or reason_code == expected_policy_reason


def test_collection_create_route_uses_active_organization(monkeypatch):
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    captured = {}

    monkeypatch.setattr(
        knowledge_endpoint,
        "resolve_active_organization_id",
        lambda db, request, raw, current_user_id: organization_id,
    )

    class FakeService:
        def __init__(self, db, *, user_id, organization_id):
            captured["user_id"] = user_id
            captured["organization_id"] = organization_id

        def create_collection(self, request):
            captured["name"] = request.name
            return _collection_response(organization_id=organization_id)

    monkeypatch.setattr(knowledge_endpoint, "KnowledgeCollectionService", FakeService)
    app.dependency_overrides[knowledge_endpoint.get_db] = lambda: object()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)
    try:
        response = TestClient(app).post(
            "/api/v1/knowledge/collections",
            json={"name": "HR Policies", "safe_metadata": {"category": "hr"}},
            headers={"X-Organization-Id": str(organization_id)},
        )
    finally:
        app.dependency_overrides = {}

    assert response.status_code == 201
    body = response.json()
    assert captured == {
        "user_id": user_id,
        "organization_id": organization_id,
        "name": "HR Policies",
    }
    assert body["organization_id"] == str(organization_id)
    assert "raw_source_url" not in str(body)


def test_collection_list_route_returns_management_capabilities(monkeypatch):
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    collection = _collection_response(organization_id=organization_id)

    monkeypatch.setattr(
        knowledge_endpoint,
        "resolve_active_organization_id",
        lambda db, request, raw, current_user_id: organization_id,
    )

    class FakeService:
        def __init__(self, db, *, user_id, organization_id):
            pass

        def list_collections(self, **kwargs):
            return [collection]

        def management_capabilities(self):
            return {
                "can_create_collection": True,
                "can_change_public_visibility": True,
            }

    monkeypatch.setattr(knowledge_endpoint, "KnowledgeCollectionService", FakeService)
    app.dependency_overrides[knowledge_endpoint.get_db] = lambda: object()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)
    try:
        response = TestClient(app).get(
            "/api/v1/knowledge/collections",
            headers={"X-Organization-Id": str(organization_id)},
        )
    finally:
        app.dependency_overrides = {}

    assert response.status_code == 200
    body = response.json()
    assert body["collections"][0]["id"] == str(collection.id)
    assert body["can_create_collection"] is True
    assert body["can_change_public_visibility"] is True


def test_collection_picker_uses_active_organization_and_minimal_projection(monkeypatch):
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    collection_id = uuid.uuid4()
    captured = {}

    monkeypatch.setattr(
        knowledge_endpoint,
        "resolve_active_organization_id",
        lambda db, request, raw, current_user_id: organization_id,
    )

    class FakePicker:
        def __init__(self, db, *, user_id, organization_id):
            captured["user_id"] = user_id
            captured["organization_id"] = organization_id

        def list_llm_selectable(self):
            return KnowledgeCollectionLLMSelectableResponse(
                collections=[
                    {"id": collection_id, "safe_label": "사내 문서"}
                ]
            )

    monkeypatch.setattr(
        knowledge_endpoint,
        "KnowledgeCollectionPickerQueryService",
        FakePicker,
    )
    app.dependency_overrides[knowledge_endpoint.get_db] = lambda: object()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)
    try:
        response = TestClient(app).get(
            "/api/v1/knowledge/llm-selectable-collections",
            headers={"X-Organization-Id": str(organization_id)},
        )
    finally:
        app.dependency_overrides = {}

    assert response.status_code == 200
    assert response.json() == {
        "collections": [
            {"id": str(collection_id), "safe_label": "사내 문서"}
        ]
    }
    assert captured == {
        "user_id": user_id,
        "organization_id": organization_id,
    }


def test_collection_visibility_error_uses_safe_envelope(monkeypatch):
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    collection_id = uuid.uuid4()

    monkeypatch.setattr(
        knowledge_endpoint,
        "resolve_active_organization_id",
        lambda db, request, raw, current_user_id: organization_id,
    )

    class FakeService:
        def __init__(self, db, *, user_id, organization_id):
            pass

        def update_visibility(self, collection_id, request):
            raise KnowledgeCollectionServiceError(
                400,
                "validation.failed",
                "Public visibility acknowledgement is required.",
                {"field": "acknowledged_public_runtime_exposure"},
            )

    monkeypatch.setattr(knowledge_endpoint, "KnowledgeCollectionService", FakeService)
    app.dependency_overrides[knowledge_endpoint.get_db] = lambda: object()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)
    try:
        response = TestClient(app).post(
            f"/api/v1/knowledge/collections/{collection_id}/visibility",
            json={
                "visibility": "public",
                "acknowledged_public_runtime_exposure": False,
            },
            headers={"X-Organization-Id": str(organization_id)},
        )
    finally:
        app.dependency_overrides = {}

    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "validation.failed"
    assert "raw_source_url" not in str(body)


def test_collection_visibility_route_returns_safe_summary(monkeypatch):
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    collection = _collection_response(organization_id=organization_id)

    monkeypatch.setattr(
        knowledge_endpoint,
        "resolve_active_organization_id",
        lambda db, request, raw, current_user_id: organization_id,
    )

    class FakeService:
        def __init__(self, db, *, user_id, organization_id):
            pass

        def update_visibility(self, collection_id, request):
            public_collection = collection.model_copy(update={"visibility": "public"})
            return KnowledgeCollectionVisibilityResponse(
                collection=public_collection,
                linked_kb_count_bucket="1",
                active_kb_count_bucket="1",
                sensitive_content_warning="unknown_or_present",
            )

    monkeypatch.setattr(knowledge_endpoint, "KnowledgeCollectionService", FakeService)
    app.dependency_overrides[knowledge_endpoint.get_db] = lambda: object()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)
    try:
        response = TestClient(app).post(
            f"/api/v1/knowledge/collections/{collection.id}/visibility",
            json={
                "visibility": "public",
                "acknowledged_public_runtime_exposure": True,
            },
            headers={"X-Organization-Id": str(organization_id)},
        )
    finally:
        app.dependency_overrides = {}

    assert response.status_code == 200
    body = response.json()
    assert body["collection"]["visibility"] == "public"
    assert body["public_runtime_effect"] == "anonymous_public_only_candidate"
    assert "exact_denied_count" not in str(body)


def test_collection_restore_route_passes_active_organization_to_use_case(monkeypatch):
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    collection_id = uuid.uuid4()
    captured = {}

    monkeypatch.setattr(
        knowledge_endpoint,
        "resolve_active_organization_id",
        lambda db, request, raw, current_user_id: organization_id,
    )

    class FakeUseCase:
        def restore(self, command):
            captured["command"] = command

    monkeypatch.setattr(
        knowledge_endpoint,
        "build_knowledge_collection_lifecycle_and_order_use_case",
        lambda db: FakeUseCase(),
    )
    app.dependency_overrides[knowledge_endpoint.get_db] = lambda: object()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)
    try:
        response = TestClient(app).post(
            f"/api/v1/knowledge/collections/{collection_id}/restore",
            headers={"X-Organization-Id": str(organization_id)},
        )
    finally:
        app.dependency_overrides = {}

    assert response.status_code == 204
    assert captured["command"].actor_id == user_id
    assert captured["command"].organization_id == organization_id
    assert captured["command"].collection_id == collection_id


def test_collection_reorder_stale_revision_uses_safe_conflict_envelope(monkeypatch):
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    collection_id = uuid.uuid4()

    monkeypatch.setattr(
        knowledge_endpoint,
        "resolve_active_organization_id",
        lambda db, request, raw, current_user_id: organization_id,
    )

    class FakeUseCase:
        def reorder(self, command):
            raise CollectionStateConflict("collection_order_stale")

    monkeypatch.setattr(
        knowledge_endpoint,
        "build_knowledge_collection_lifecycle_and_order_use_case",
        lambda db: FakeUseCase(),
    )
    app.dependency_overrides[knowledge_endpoint.get_db] = lambda: object()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)
    try:
        response = TestClient(app).patch(
            f"/api/v1/knowledge/collections/{collection_id}/items/reorder",
            json={
                "items": [{"item_id": str(uuid.uuid4()), "rank": 0}],
                "expected_order_revision": f"ord_v1_{'0' * 64}",
            },
            headers={"X-Organization-Id": str(organization_id)},
        )
    finally:
        app.dependency_overrides = {}

    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "conflict"
    assert body["error"]["details"] == {"reason": "collection_order_stale"}
    assert collection_id.hex not in str(body)


def test_empty_collection_reorder_returns_management_projection(monkeypatch):
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    collection_id = uuid.uuid4()
    revision = f"ord_v1_{'0' * 64}"
    captured = {}

    monkeypatch.setattr(
        knowledge_endpoint,
        "resolve_active_organization_id",
        lambda db, request, raw, current_user_id: organization_id,
    )

    class FakeUseCase:
        def reorder(self, command):
            captured["command"] = command

    class FakeService:
        def __init__(self, db, *, user_id, organization_id):
            captured["service_user_id"] = user_id
            captured["service_organization_id"] = organization_id

        def list_items_management_response(self, requested_collection_id):
            captured["projection_collection_id"] = requested_collection_id
            return KnowledgeCollectionItemsResponse(
                items=[],
                order_revision=revision,
            )

    monkeypatch.setattr(
        knowledge_endpoint,
        "build_knowledge_collection_lifecycle_and_order_use_case",
        lambda db: FakeUseCase(),
    )
    monkeypatch.setattr(knowledge_endpoint, "KnowledgeCollectionService", FakeService)
    app.dependency_overrides[knowledge_endpoint.get_db] = lambda: object()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)
    try:
        response = TestClient(app).patch(
            f"/api/v1/knowledge/collections/{collection_id}/items/reorder",
            json={
                "items": [],
                "expected_order_revision": revision,
            },
            headers={"X-Organization-Id": str(organization_id)},
        )
    finally:
        app.dependency_overrides = {}

    assert response.status_code == 200
    assert captured["command"].items == ()
    assert captured["projection_collection_id"] == collection_id
    assert response.json()["items"] == []
