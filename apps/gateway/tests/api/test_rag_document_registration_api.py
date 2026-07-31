import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from apps.gateway.api.v1.endpoints import rag as rag_endpoint
from apps.gateway.services.connection_lifecycle_service import (
    ConnectionLifecycleBusy,
    ConnectionLifecycleHidden,
    ConnectionLifecycleUnavailable,
)
from apps.gateway.services.knowledge_authorization_service import (
    KnowledgePermissionDenied,
    KnowledgeResourceHidden,
)
from apps.gateway.services.knowledge_document_registration_service import (
    KnowledgeDocumentRegistrationHidden,
    KnowledgeDocumentRegistrationPolicyDenied,
    KnowledgeDocumentRegistrationUnavailable,
    KnowledgeDocumentSlotOccupied,
)
from apps.gateway.services.knowledge_document_lifecycle_service import (
    DeletedKnowledgeDocument,
    KnowledgeDocumentLifecycleHidden,
    KnowledgeDocumentLifecycleUnavailable,
)
from apps.shared.db.models.knowledge import SourceType


@pytest.fixture
def upload_http_client(monkeypatch):
    test_app = FastAPI()
    test_app.include_router(rag_endpoint.router, prefix="/api/v1/rag")
    db = SimpleNamespace()
    current_user = SimpleNamespace(id=uuid4())
    test_app.dependency_overrides[rag_endpoint.get_db] = lambda: db
    test_app.dependency_overrides[rag_endpoint.get_current_user] = lambda: current_user
    monkeypatch.setattr(
        "apps.gateway.utils.audit.record_audit",
        lambda **_kwargs: None,
    )

    with TestClient(test_app) as client:
        yield client, db, current_user


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (KnowledgeDocumentRegistrationHidden(), 404, "resource.hidden"),
        (
            KnowledgeDocumentRegistrationPolicyDenied(),
            409,
            "knowledge.document_registration_not_allowed",
        ),
        (
            KnowledgeDocumentSlotOccupied(),
            409,
            "knowledge.document_slot_occupied",
        ),
        (
            KnowledgeDocumentRegistrationUnavailable(),
            503,
            "knowledge.document_registration_unavailable",
        ),
    ],
)
def test_registration_errors_use_safe_stable_http_contract(error, status, code):
    request = SimpleNamespace(state=SimpleNamespace(request_id="req-1"))

    with pytest.raises(HTTPException) as exc_info:
        rag_endpoint._raise_document_registration_error(request, error)

    assert exc_info.value.status_code == status
    assert exc_info.value.detail["error"]["code"] == code
    assert exc_info.value.detail["error"]["request_id"] == "req-1"
    assert "document_id" not in str(exc_info.value.detail)
    assert "filename" not in str(exc_info.value.detail)


def test_backend_cleanup_failure_logs_only_safe_error_type(monkeypatch, caplog):
    class _Storage:
        def delete(self, _file_path):
            raise RuntimeError("provider bucket/key raw detail")

    monkeypatch.setattr(rag_endpoint, "get_storage_service", lambda: _Storage())

    with caplog.at_level(logging.WARNING, logger=rag_endpoint.__name__):
        rag_endpoint._cleanup_backend_upload("private/raw/path.pdf")

    assert "RuntimeError" in caplog.text
    assert "private/raw/path.pdf" not in caplog.text
    assert "provider bucket/key raw detail" not in caplog.text


def test_backend_cleanup_is_disabled_when_commit_outcome_is_unknown():
    assert (
        rag_endpoint._is_backend_upload_cleanup_safe(
            KnowledgeDocumentRegistrationUnavailable(artifact_cleanup_safe=False)
        )
        is False
    )
    assert (
        rag_endpoint._is_backend_upload_cleanup_safe(
            KnowledgeDocumentRegistrationUnavailable(artifact_cleanup_safe=True)
        )
        is True
    )


def test_delete_document_releases_slot_before_best_effort_storage_cleanup(
    upload_http_client,
    monkeypatch,
):
    client, dependency_db, _current_user = upload_http_client
    organization_id = uuid4()
    knowledge_base_id = uuid4()
    document_id = uuid4()
    calls = []
    monkeypatch.setattr(
        rag_endpoint,
        "_authorize_knowledge_document_action",
        lambda *_args, **_kwargs: (
            SimpleNamespace(id=knowledge_base_id),
            SimpleNamespace(id=document_id),
        ),
    )

    class _LifecycleService:
        def __init__(self, db):
            assert db is dependency_db

        def delete_document(self, **kwargs):
            calls.append(("db", kwargs))
            return DeletedKnowledgeDocument(file_path="opaque-storage-reference")

    class _Storage:
        def delete(self, reference):
            calls.append(("storage", reference))

    monkeypatch.setattr(
        rag_endpoint,
        "KnowledgeDocumentLifecycleService",
        _LifecycleService,
    )
    monkeypatch.setattr(rag_endpoint, "get_storage_service", lambda: _Storage())

    response = client.delete(
        f"/api/v1/rag/document/{document_id}",
        headers={"X-Organization-Id": str(organization_id)},
    )

    assert response.status_code == 200
    assert calls == [
        (
            "db",
            {
                "knowledge_base_id": knowledge_base_id,
                "organization_id": organization_id,
                "document_id": document_id,
            },
        ),
        ("storage", "opaque-storage-reference"),
    ]


@pytest.mark.parametrize(
    ("error", "status_code", "reason_code"),
    [
        (KnowledgeDocumentLifecycleHidden(), 404, "resource.hidden"),
        (
            KnowledgeDocumentLifecycleUnavailable(),
            503,
            "knowledge.document_delete_unavailable",
        ),
    ],
)
def test_delete_document_maps_lifecycle_errors_safely(
    upload_http_client,
    monkeypatch,
    error,
    status_code,
    reason_code,
):
    client, _dependency_db, _current_user = upload_http_client
    organization_id = uuid4()
    knowledge_base_id = uuid4()
    document_id = uuid4()
    monkeypatch.setattr(
        rag_endpoint,
        "_authorize_knowledge_document_action",
        lambda *_args, **_kwargs: (
            SimpleNamespace(id=knowledge_base_id),
            SimpleNamespace(id=document_id),
        ),
    )

    class _LifecycleService:
        def __init__(self, _db):
            pass

        def delete_document(self, **_kwargs):
            raise error

    monkeypatch.setattr(
        rag_endpoint,
        "KnowledgeDocumentLifecycleService",
        _LifecycleService,
    )

    response = client.delete(
        f"/api/v1/rag/document/{document_id}",
        headers={"X-Organization-Id": str(organization_id)},
    )

    assert response.status_code == status_code
    assert response.json()["detail"]["error"]["code"] == reason_code
    assert (
        rag_endpoint._is_backend_upload_cleanup_safe(KnowledgeDocumentSlotOccupied())
        is True
    )


def test_generic_presigned_upload_does_not_require_a_knowledge_base(monkeypatch):
    class _Storage:
        def generate_presigned_upload_url(self, **_kwargs):
            return {
                "url": "https://storage.invalid/upload",
                "key": "opaque-key",
                "method": "PUT",
            }

    def fail_if_knowledge_is_loaded(*_args, **_kwargs):
        raise AssertionError("generic workflow input upload must not load a KB")

    monkeypatch.setattr(
        rag_endpoint,
        "_load_writable_knowledge_base",
        fail_if_knowledge_is_loaded,
    )
    monkeypatch.setattr(rag_endpoint, "get_storage_service", lambda: _Storage())

    response = asyncio.run(
        rag_endpoint.generate_presigned_url(
            request=SimpleNamespace(),
            filename="input.pdf",
            content_type="application/pdf",
            knowledge_base_id=None,
            x_organization_id=None,
            db=object(),
            current_user=SimpleNamespace(id="user-id"),
        )
    )

    assert response == {
        "upload_url": "https://storage.invalid/upload",
        "s3_key": "opaque-key",
        "method": "PUT",
        "use_backend_proxy": None,
    }


def test_knowledge_presigned_upload_rejects_occupied_slot_before_storage(
    monkeypatch,
):
    organization_id = uuid4()
    knowledge_base_id = uuid4()
    calls: list[str] = []

    monkeypatch.setattr(
        rag_endpoint,
        "parse_organization_id",
        lambda *_args, **_kwargs: organization_id,
    )
    monkeypatch.setattr(
        rag_endpoint,
        "_load_writable_knowledge_base",
        lambda *_args, **_kwargs: calls.append("authorize"),
    )

    def reject_slot(*_args, **_kwargs):
        calls.append("slot")
        raise HTTPException(status_code=409, detail={"reason_code": "occupied"})

    monkeypatch.setattr(
        rag_endpoint,
        "_ensure_initial_document_slot",
        reject_slot,
    )
    monkeypatch.setattr(
        rag_endpoint,
        "get_storage_service",
        lambda: (_ for _ in ()).throw(
            AssertionError("storage must not run for an occupied KB")
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            rag_endpoint.generate_presigned_url(
                request=SimpleNamespace(),
                filename="policy.pdf",
                content_type="application/pdf",
                knowledge_base_id=knowledge_base_id,
                x_organization_id=str(organization_id),
                db=object(),
                current_user=SimpleNamespace(id=uuid4()),
            )
        )

    assert exc_info.value.status_code == 409
    assert calls == ["authorize", "slot"]


def test_prepare_db_source_preflights_owner_without_runtime_lock():
    connection_id = uuid4()
    user_id = uuid4()
    connection = SimpleNamespace(
        id=connection_id,
        user_id=user_id,
        type="postgres",
        name="Operational DB",
    )

    class _Query:
        def populate_existing(self):
            return self

        def filter(self, *_args):
            return self

        def with_for_update(self):
            raise AssertionError("authorization preflight must not acquire a row lock")

        def one_or_none(self):
            return connection

    query = _Query()
    db = SimpleNamespace(query=lambda _model: query)

    file_path, filename, meta_info = rag_endpoint._prepare_db_source(
        SimpleNamespace(state=SimpleNamespace(request_id="request-id")),
        db,
        SimpleNamespace(id=user_id),
        connection_id,
    )

    assert file_path is None
    assert filename == "Database source"
    assert meta_info == {"connection_id": str(connection_id)}


@pytest.mark.parametrize("source_type", list(SourceType))
def test_upload_route_parses_each_source_type_and_uses_canonical_registration(
    source_type,
    upload_http_client,
    monkeypatch,
):
    client, dependency_db, current_user = upload_http_client
    organization_id = uuid4()
    knowledge_base_id = uuid4()
    document_id = uuid4()
    connection_id = uuid4()
    captured: dict[str, object] = {}
    events: list[str] = []

    def get_or_create(*_args, **_kwargs):
        events.append("knowledge_base")
        return knowledge_base_id, "text-embedding-3-small"

    monkeypatch.setattr(rag_endpoint, "_get_or_create_knowledge_base", get_or_create)

    def ensure_slot(_request, db, **kwargs):
        events.append("slot")
        captured["slot"] = (db, kwargs)

    monkeypatch.setattr(rag_endpoint, "_ensure_initial_document_slot", ensure_slot)
    monkeypatch.setattr(
        rag_endpoint,
        "IngestionService",
        lambda *_args, **_kwargs: SimpleNamespace(),
    )

    def prepare_file(_service, file):
        captured["prepared"] = (SourceType.FILE, file.filename)
        return "owned/file/path.pdf", "policy.pdf", {"source": "file"}

    def prepare_api(api_url, api_method, api_headers, api_body):
        captured["prepared"] = (
            SourceType.API,
            api_url,
            api_method,
            api_headers,
            api_body,
        )
        return None, "API source", {"source": "api"}

    def prepare_db(_request, db, user, parsed_connection_id):
        events.append("connection_preflight")
        captured["prepared"] = (
            SourceType.DB,
            db,
            user,
            parsed_connection_id,
        )
        return None, "DB source", {"connection_id": str(connection_id)}

    monkeypatch.setattr(rag_endpoint, "_prepare_file_source", prepare_file)
    monkeypatch.setattr(rag_endpoint, "_prepare_api_source", prepare_api)
    monkeypatch.setattr(rag_endpoint, "_prepare_db_source", prepare_db)

    def lock_reference(_request, db, *, connection_id, owner_id):
        events.append("connection_reference_lock")
        captured["connection_reference_lock"] = (
            db,
            connection_id,
            owner_id,
        )

    monkeypatch.setattr(
        rag_endpoint,
        "_lock_db_connection_reference_for_registration",
        lock_reference,
    )

    class RegistrationService:
        def __init__(self, db):
            assert db is dependency_db

        def register_initial_document(self, **kwargs):
            events.append("registration")
            captured["registration"] = kwargs
            return document_id

    monkeypatch.setattr(
        rag_endpoint,
        "KnowledgeDocumentRegistrationService",
        RegistrationService,
    )

    data = {
        "knowledgeBaseId": str(knowledge_base_id),
        "sourceType": source_type.value,
        "chunkSize": "512",
        "chunkOverlap": "64",
        "chunkingMode": "flat",
    }
    files = None
    if source_type == SourceType.FILE:
        files = {"file": ("policy.pdf", b"test content", "application/pdf")}
    elif source_type == SourceType.API:
        data.update(
            {
                "apiUrl": "https://api.example.test/policies",
                "apiMethod": "POST",
                "apiHeaders": '{"Authorization":"redacted"}',
                "apiBody": '{"query":"policy"}',
            }
        )
    else:
        data["connectionId"] = str(connection_id)

    response = client.post(
        "/api/v1/rag/upload",
        headers={"X-Organization-Id": str(organization_id)},
        data=data,
        files=files,
    )

    assert response.status_code == 200
    assert response.json() == {
        "knowledge_base_id": str(knowledge_base_id),
        "document_id": str(document_id),
        "status": "pending",
        "message": "자료가 등록되었습니다. 설정을 확인하고 처리를 시작해주세요.",
    }
    assert captured["slot"] == (
        dependency_db,
        {
            "knowledge_base_id": knowledge_base_id,
            "organization_id": organization_id,
        },
    )
    registration = captured["registration"]
    assert registration["knowledge_base_id"] == knowledge_base_id
    assert registration["organization_id"] == organization_id
    assert registration["source_type"] == source_type
    assert registration["chunk_size"] == 512
    assert registration["chunk_overlap"] == 64
    assert registration["meta_info"]["chunking_mode"] == "flat"

    prepared = captured["prepared"]
    assert prepared[0] == source_type
    if source_type == SourceType.FILE:
        assert prepared == (SourceType.FILE, "policy.pdf")
        assert registration["filename"] == "policy.pdf"
        assert registration["file_path"] == "owned/file/path.pdf"
    elif source_type == SourceType.API:
        assert prepared == (
            SourceType.API,
            "https://api.example.test/policies",
            "POST",
            '{"Authorization":"redacted"}',
            '{"query":"policy"}',
        )
        assert registration["filename"] == "API source"
        assert registration["file_path"] is None
    else:
        assert prepared[1:] == (dependency_db, current_user, str(connection_id))
        assert captured["connection_reference_lock"] == (
            dependency_db,
            connection_id,
            current_user.id,
        )
        assert events == [
            "connection_preflight",
            "knowledge_base",
            "slot",
            "connection_reference_lock",
            "registration",
        ]
        assert registration["filename"] == "DB source"
        assert registration["file_path"] is None


@pytest.mark.parametrize(
    ("error", "status_code", "reason_code"),
    [
        (ConnectionLifecycleHidden(), 404, "resource.hidden"),
        (ConnectionLifecycleBusy(), 503, "connection.reference_busy"),
        (
            ConnectionLifecycleUnavailable(),
            503,
            "connection.reference_unavailable",
        ),
    ],
)
def test_db_reference_lock_errors_are_safe(
    monkeypatch,
    error,
    status_code,
    reason_code,
):
    db = SimpleNamespace(rollback=Mock())

    class LifecycleService:
        def __init__(self, dependency):
            assert dependency is db

        def lock_owned_connection_for_reference(self, **_kwargs):
            raise error

    monkeypatch.setattr(
        rag_endpoint,
        "ConnectionLifecycleService",
        LifecycleService,
    )

    with pytest.raises(HTTPException) as exc_info:
        rag_endpoint._lock_db_connection_reference_for_registration(
            SimpleNamespace(state=SimpleNamespace(request_id="request-id")),
            db,
            connection_id=uuid4(),
            owner_id=uuid4(),
        )

    assert exc_info.value.status_code == status_code
    assert exc_info.value.detail["error"]["code"] == reason_code
    db.rollback.assert_called_once_with()


def test_upload_route_rejects_occupied_slot_before_source_side_effect(
    upload_http_client,
    monkeypatch,
):
    client, _dependency_db, _current_user = upload_http_client
    organization_id = uuid4()
    knowledge_base_id = uuid4()

    monkeypatch.setattr(
        rag_endpoint,
        "_get_or_create_knowledge_base",
        lambda *_args, **_kwargs: (knowledge_base_id, "text-embedding-3-small"),
    )

    def reject_slot(request, *_args, **_kwargs):
        rag_endpoint._raise_document_registration_error(
            request,
            KnowledgeDocumentSlotOccupied(),
        )

    monkeypatch.setattr(rag_endpoint, "_ensure_initial_document_slot", reject_slot)
    monkeypatch.setattr(
        rag_endpoint,
        "IngestionService",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("source preparation must not start for an occupied KB")
        ),
    )

    response = client.post(
        "/api/v1/rag/upload",
        headers={"X-Organization-Id": str(organization_id)},
        data={
            "knowledgeBaseId": str(knowledge_base_id),
            "sourceType": "API",
            "apiUrl": "https://api.example.test/policies",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["error"]["code"] == (
        "knowledge.document_slot_occupied"
    )


def test_db_upload_rejects_occupied_slot_before_reference_lock(
    upload_http_client,
    monkeypatch,
):
    client, _dependency_db, _current_user = upload_http_client
    organization_id = uuid4()
    knowledge_base_id = uuid4()
    connection_id = uuid4()

    monkeypatch.setattr(
        rag_endpoint,
        "_prepare_db_source",
        lambda *_args, **_kwargs: (
            None,
            "DB source",
            {"connection_id": str(connection_id)},
        ),
    )
    monkeypatch.setattr(
        rag_endpoint,
        "_get_or_create_knowledge_base",
        lambda *_args, **_kwargs: (knowledge_base_id, "text-embedding-3-small"),
    )

    def reject_slot(request, *_args, **_kwargs):
        rag_endpoint._raise_document_registration_error(
            request,
            KnowledgeDocumentSlotOccupied(),
        )

    monkeypatch.setattr(rag_endpoint, "_ensure_initial_document_slot", reject_slot)
    monkeypatch.setattr(
        rag_endpoint,
        "_lock_db_connection_reference_for_registration",
        lambda *_args, **_kwargs: pytest.fail(
            "reference lock must not be acquired for an occupied KB"
        ),
    )

    response = client.post(
        "/api/v1/rag/upload",
        headers={"X-Organization-Id": str(organization_id)},
        data={
            "knowledgeBaseId": str(knowledge_base_id),
            "sourceType": "DB",
            "connectionId": str(connection_id),
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["error"]["code"] == (
        "knowledge.document_slot_occupied"
    )


@pytest.mark.parametrize(
    ("authorization_error", "expected_status", "expected_code"),
    [
        (KnowledgeResourceHidden, 404, "resource.hidden"),
        (KnowledgePermissionDenied, 403, "permission.denied"),
    ],
)
def test_upload_route_preserves_hidden_and_denied_write_boundaries(
    authorization_error,
    expected_status,
    expected_code,
    upload_http_client,
    monkeypatch,
):
    client, _dependency_db, _current_user = upload_http_client
    organization_id = uuid4()
    knowledge_base_id = uuid4()

    class AuthorizationService:
        def __init__(self, *_args, **_kwargs):
            pass

        def load_kb(self, _knowledge_base_id, _action):
            raise authorization_error()

    monkeypatch.setattr(
        rag_endpoint,
        "KnowledgeAuthorizationService",
        AuthorizationService,
    )
    monkeypatch.setattr(
        rag_endpoint,
        "IngestionService",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("source preparation must not run after authorization denial")
        ),
    )

    response = client.post(
        "/api/v1/rag/upload",
        headers={"X-Organization-Id": str(organization_id)},
        data={
            "knowledgeBaseId": str(knowledge_base_id),
            "sourceType": "API",
            "apiUrl": "https://api.example.test/policies",
        },
    )

    assert response.status_code == expected_status
    assert response.json()["detail"]["error"]["code"] == expected_code


@pytest.mark.parametrize(
    ("registration_error", "expected_status", "cleanup_expected"),
    [
        (KnowledgeDocumentSlotOccupied(), 409, True),
        (
            KnowledgeDocumentRegistrationUnavailable(artifact_cleanup_safe=True),
            503,
            True,
        ),
        (
            KnowledgeDocumentRegistrationUnavailable(artifact_cleanup_safe=False),
            503,
            False,
        ),
    ],
)
def test_backend_upload_cleanup_follows_registration_outcome(
    registration_error,
    expected_status,
    cleanup_expected,
    upload_http_client,
    monkeypatch,
):
    client, _dependency_db, _current_user = upload_http_client
    organization_id = uuid4()
    knowledge_base_id = uuid4()
    cleanup_paths: list[str] = []

    monkeypatch.setattr(
        rag_endpoint,
        "_get_or_create_knowledge_base",
        lambda *_args, **_kwargs: (knowledge_base_id, "text-embedding-3-small"),
    )
    monkeypatch.setattr(
        rag_endpoint,
        "_ensure_initial_document_slot",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        rag_endpoint,
        "IngestionService",
        lambda *_args, **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(
        rag_endpoint,
        "_prepare_file_source",
        lambda *_args, **_kwargs: (
            "request-owned/upload.pdf",
            "upload.pdf",
            {},
        ),
    )
    monkeypatch.setattr(
        rag_endpoint,
        "_cleanup_backend_upload",
        cleanup_paths.append,
    )

    class RegistrationService:
        def __init__(self, _db):
            pass

        def register_initial_document(self, **_kwargs):
            raise registration_error

    monkeypatch.setattr(
        rag_endpoint,
        "KnowledgeDocumentRegistrationService",
        RegistrationService,
    )

    response = client.post(
        "/api/v1/rag/upload",
        headers={"X-Organization-Id": str(organization_id)},
        data={
            "knowledgeBaseId": str(knowledge_base_id),
            "sourceType": "FILE",
        },
        files={"file": ("upload.pdf", b"test content", "application/pdf")},
    )

    assert response.status_code == expected_status
    assert cleanup_paths == (["request-owned/upload.pdf"] if cleanup_expected else [])
    assert "request-owned/upload.pdf" not in response.text


def test_direct_upload_conflict_never_deletes_unowned_object(
    upload_http_client,
    monkeypatch,
):
    client, _dependency_db, _current_user = upload_http_client
    organization_id = uuid4()
    knowledge_base_id = uuid4()
    cleanup_paths: list[str] = []

    monkeypatch.setattr(
        rag_endpoint,
        "_get_or_create_knowledge_base",
        lambda *_args, **_kwargs: (knowledge_base_id, "text-embedding-3-small"),
    )
    monkeypatch.setattr(
        rag_endpoint,
        "_ensure_initial_document_slot",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        rag_endpoint,
        "IngestionService",
        lambda *_args, **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(
        rag_endpoint,
        "_prepare_direct_upload_source",
        lambda **_kwargs: (
            "https://storage.invalid/unowned.pdf",
            "unowned.pdf",
            {"upload_method": "direct"},
        ),
    )
    monkeypatch.setattr(
        rag_endpoint,
        "_cleanup_backend_upload",
        cleanup_paths.append,
    )

    class RegistrationService:
        def __init__(self, _db):
            pass

        def register_initial_document(self, **_kwargs):
            raise KnowledgeDocumentSlotOccupied()

    monkeypatch.setattr(
        rag_endpoint,
        "KnowledgeDocumentRegistrationService",
        RegistrationService,
    )

    response = client.post(
        "/api/v1/rag/upload",
        headers={"X-Organization-Id": str(organization_id)},
        data={
            "knowledgeBaseId": str(knowledge_base_id),
            "sourceType": "FILE",
            "s3FileUrl": "https://storage.invalid/unowned.pdf",
            "s3FileKey": "uploads/user/unowned.pdf",
        },
    )

    assert response.status_code == 409
    assert cleanup_paths == []
