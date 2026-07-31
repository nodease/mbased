from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from apps.gateway.api.v1.endpoints import knowledge as knowledge_endpoint
from apps.gateway.api.v1.endpoints import rag as rag_endpoint
from apps.gateway.services.ingestion.factory import IngestionFactory
from apps.gateway.services.ingestion.service import (
    IngestionOrchestrator,
    IngestionPreviewSourceError,
)
from apps.gateway.services.knowledge_db_source_config import (
    validate_knowledge_db_source_config,
)
from apps.shared.db.models.connection import Connection
from apps.shared.db.models.knowledge import SourceType
from apps.shared.db.models.user import User
from apps.shared.schemas.rag import DocumentPreviewRequest
from apps.shared.services.ingestion.processors.base import ProcessingResult


@pytest.fixture
def db_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    User.__table__.create(engine)
    Connection.__table__.create(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def _insert_user(session: Session, user_id: uuid.UUID) -> None:
    session.execute(
        User.__table__.insert().values(
            id=user_id,
            email=f"{user_id}@example.test",
            name="Test User",
            social_provider="local",
        )
    )


def _insert_connection(
    session: Session,
    *,
    connection_id: uuid.UUID,
    owner_id: uuid.UUID,
) -> None:
    session.execute(
        Connection.__table__.insert().values(
            id=connection_id,
            user_id=owner_id,
            name="sensitive-connection-label",
            type="postgres",
            host="db.internal.example",
            port=5432,
            database="application",
            username="service-user",
            encrypted_password="opaque-ciphertext",
            use_ssh=False,
        )
    )
    session.commit()


def _request() -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(request_id="request-id"))


def _document(connection_id: uuid.UUID) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        source_type="DB",
        chunk_size=500,
        chunk_overlap=50,
        meta_info={"connection_id": str(connection_id), "chunking_mode": "flat"},
        file_path=None,
        updated_at=None,
    )


def _preview_request(connection_id: object, **extra_db_config) -> DocumentPreviewRequest:
    return DocumentPreviewRequest(
        source_type="DB",
        db_config={
            "connection_id": connection_id,
            "selections": [{"table_name": "safe_table", "columns": ["id"]}],
            **extra_db_config,
        },
    )


def _assert_resource_hidden(exc: HTTPException) -> None:
    assert exc.status_code == 404
    assert exc.detail["error"]["code"] == "resource.hidden"
    serialized = repr(exc.detail)
    assert "sensitive-connection-label" not in serialized
    assert "db.internal.example" not in serialized
    assert "service-user" not in serialized
    assert "opaque-ciphertext" not in serialized


def test_rag_db_source_rejects_other_users_connection(db_session: Session) -> None:
    owner_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    connection_id = uuid.uuid4()
    _insert_user(db_session, owner_id)
    _insert_user(db_session, actor_id)
    _insert_connection(
        db_session,
        connection_id=connection_id,
        owner_id=owner_id,
    )

    with pytest.raises(HTTPException) as exc_info:
        rag_endpoint._prepare_db_source(
            _request(),
            db_session,
            SimpleNamespace(id=actor_id),
            connection_id,
        )

    _assert_resource_hidden(exc_info.value)


def test_rag_upload_hides_malformed_connection_reference(
    db_session: Session,
    monkeypatch,
) -> None:
    test_app = FastAPI()
    test_app.include_router(rag_endpoint.router, prefix="/rag")
    test_app.dependency_overrides[rag_endpoint.get_db] = lambda: db_session
    test_app.dependency_overrides[rag_endpoint.get_current_user] = lambda: SimpleNamespace(
        id=uuid.uuid4()
    )
    monkeypatch.setattr("apps.gateway.utils.audit.record_audit", lambda **_event: None)

    response = TestClient(test_app).post(
        "/rag/upload",
        data={
            "sourceType": "DB",
            "connectionId": "not-a-uuid",
        },
        headers={"X-Organization-Id": str(uuid.uuid4())},
    )

    assert response.status_code == 404
    assert response.json()["detail"]["error"]["code"] == "resource.hidden"


def test_rag_db_source_persists_only_opaque_reference(db_session: Session) -> None:
    owner_id = uuid.uuid4()
    connection_id = uuid.uuid4()
    _insert_user(db_session, owner_id)
    _insert_connection(
        db_session,
        connection_id=connection_id,
        owner_id=owner_id,
    )

    file_path, filename, meta_info = rag_endpoint._prepare_db_source(
        _request(),
        db_session,
        SimpleNamespace(id=owner_id),
        connection_id,
    )

    assert file_path is None
    assert filename == "Database source"
    assert meta_info == {"connection_id": str(connection_id)}


def test_rag_connection_lookup_failure_is_safe_503() -> None:
    db = Mock()
    db.query.side_effect = SQLAlchemyError("sensitive backend detail")

    with pytest.raises(HTTPException) as exc_info:
        rag_endpoint._prepare_db_source(
            _request(),
            db,
            SimpleNamespace(id=uuid.uuid4()),
            uuid.uuid4(),
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["error"]["code"] == (
        "connection.reference_unavailable"
    )
    assert "sensitive backend detail" not in repr(exc_info.value.detail)


def test_knowledge_connection_lookup_failure_is_safe_503() -> None:
    db = Mock()
    db.query.side_effect = SQLAlchemyError("sensitive backend detail")

    with pytest.raises(HTTPException) as exc_info:
        knowledge_endpoint._validated_db_source_config_or_error(
            _request(),
            db,
            current_user_id=uuid.uuid4(),
            stored_meta_info={},
            submitted_db_config={"connection_id": str(uuid.uuid4())},
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["error"]["code"] == (
        "connection.reference_unavailable"
    )
    assert "sensitive backend detail" not in repr(exc_info.value.detail)


def test_submitted_db_config_bypasses_malformed_legacy_json(
    db_session: Session,
) -> None:
    owner_id = uuid.uuid4()
    connection_id = uuid.uuid4()
    _insert_user(db_session, owner_id)
    _insert_connection(
        db_session,
        connection_id=connection_id,
        owner_id=owner_id,
    )

    validated = validate_knowledge_db_source_config(
        db_session,
        execution_subject_user_id=owner_id,
        stored_meta_info={"db_config": "{malformed legacy config"},
        submitted_db_config={
            "connection_id": str(connection_id),
            "selections": [{"table_name": "safe_table", "columns": ["id"]}],
        },
    )

    assert validated.connection_id == connection_id
    assert validated.persisted_db_config == {
        "selections": [{"table_name": "safe_table", "columns": ["id"]}]
    }


def test_legacy_json_db_config_is_decoded_without_submission(
    db_session: Session,
) -> None:
    owner_id = uuid.uuid4()
    connection_id = uuid.uuid4()
    _insert_user(db_session, owner_id)
    _insert_connection(
        db_session,
        connection_id=connection_id,
        owner_id=owner_id,
    )

    validated = validate_knowledge_db_source_config(
        db_session,
        execution_subject_user_id=owner_id,
        stored_meta_info={
            "db_config": json.dumps(
                {
                    "connection_id": str(connection_id),
                    "selections": [
                        {"table_name": "legacy_table", "columns": ["id"]}
                    ],
                }
            )
        },
        submitted_db_config=None,
    )

    assert validated.connection_id == connection_id
    assert validated.persisted_db_config == {
        "selections": [{"table_name": "legacy_table", "columns": ["id"]}]
    }


@pytest.mark.asyncio
async def test_process_rejects_other_users_connection_before_mutation(
    db_session: Session,
    monkeypatch,
) -> None:
    owner_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    connection_id = uuid.uuid4()
    _insert_user(db_session, owner_id)
    _insert_user(db_session, actor_id)
    _insert_connection(
        db_session,
        connection_id=connection_id,
        owner_id=owner_id,
    )
    document = _document(connection_id)
    original_meta = dict(document.meta_info)
    monkeypatch.setattr(
        knowledge_endpoint,
        "_authorized_knowledge_document",
        lambda *args, **kwargs: (
            SimpleNamespace(
                embedding_model="embedding-model",
                organization_id=uuid.uuid4(),
            ),
            document,
        ),
    )
    monkeypatch.setattr(
        knowledge_endpoint,
        "build_request_document_ingestion",
        lambda *_args, **_kwargs: pytest.fail("admission must not start"),
    )
    monkeypatch.setattr(
        knowledge_endpoint,
        "_ensure_document_ingestion_schema_ready",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_endpoint.process_document.__wrapped__(
            kb_id=uuid.uuid4(),
            document_id=document.id,
            preview_request=_preview_request(connection_id),
            request=_request(),
            x_organization_id=str(uuid.uuid4()),
            db=db_session,
            current_user=SimpleNamespace(id=actor_id),
        )

    _assert_resource_hidden(exc_info.value)
    assert document.meta_info == original_meta


def test_preview_rejects_malformed_connection_before_processor(
    db_session: Session,
    monkeypatch,
) -> None:
    actor_id = uuid.uuid4()
    document = _document(uuid.uuid4())
    monkeypatch.setattr(
        knowledge_endpoint,
        "_authorized_knowledge_document",
        lambda *args, **kwargs: (SimpleNamespace(), document),
    )
    monkeypatch.setattr(
        knowledge_endpoint,
        "IngestionService",
        lambda *_args, **_kwargs: pytest.fail("processor must not start"),
    )

    with pytest.raises(HTTPException) as exc_info:
        knowledge_endpoint.preview_document_chunking(
            kb_id=uuid.uuid4(),
            document_id=document.id,
            preview_request=_preview_request("not-a-uuid"),
            request=_request(),
            x_organization_id=str(uuid.uuid4()),
            db=db_session,
            current_user=SimpleNamespace(id=actor_id),
        )

    _assert_resource_hidden(exc_info.value)


@pytest.mark.parametrize(
    ("processor_reason", "expected_reason"),
    [
        ("resource.hidden", "resource.hidden"),
        ("source.temporarily_unavailable", "source.temporarily_unavailable"),
        ("untrusted.processor.detail", "configuration.invalid"),
    ],
)
def test_preview_service_preserves_only_safe_processor_reason(
    monkeypatch,
    processor_reason,
    expected_reason,
) -> None:
    processor = Mock()
    processor.process.return_value = ProcessingResult(
        chunks=[],
        metadata={
            "error": "sensitive processor detail",
            "reason_code": processor_reason,
        },
    )
    monkeypatch.setattr(
        IngestionFactory,
        "get_processor",
        Mock(return_value=processor),
    )
    service = IngestionOrchestrator(
        Mock(),
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )

    with pytest.raises(IngestionPreviewSourceError) as exc_info:
        service.preview_chunking(
            file_path="",
            chunk_size=500,
            chunk_overlap=50,
            segment_identifier="segment",
            source_type=SourceType.DB,
            meta_info={},
            db_config={"connection_id": str(uuid.uuid4())},
        )

    assert exc_info.value.reason_code == expected_reason
    assert "sensitive processor detail" not in repr(exc_info.value)


@pytest.mark.parametrize(
    ("reason_code", "status_code", "response_code"),
    [
        ("resource.hidden", 404, "resource.hidden"),
        (
            "source.temporarily_unavailable",
            503,
            "source.temporarily_unavailable",
        ),
        (
            "knowledge.raw_parser_egress_unavailable",
            409,
            "knowledge.raw_parser_egress_unavailable",
        ),
        ("configuration.invalid", 400, "validation.failed"),
    ],
)
def test_preview_maps_runtime_processor_reason_safely(
    db_session: Session,
    monkeypatch,
    reason_code,
    status_code,
    response_code,
) -> None:
    owner_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    connection_id = uuid.uuid4()
    _insert_user(db_session, owner_id)
    _insert_connection(
        db_session,
        connection_id=connection_id,
        owner_id=owner_id,
    )
    document = _document(connection_id)
    monkeypatch.setattr(
        knowledge_endpoint,
        "_authorized_knowledge_document",
        lambda *args, **kwargs: (
            SimpleNamespace(organization_id=organization_id),
            document,
        ),
    )

    class PreviewService:
        def __init__(self, *_args, **_kwargs):
            pass

        def preview_chunking(self, **_kwargs):
            raise IngestionPreviewSourceError(reason_code)

    monkeypatch.setattr(knowledge_endpoint, "IngestionService", PreviewService)

    with pytest.raises(HTTPException) as exc_info:
        knowledge_endpoint.preview_document_chunking(
            kb_id=uuid.uuid4(),
            document_id=document.id,
            preview_request=_preview_request(connection_id),
            request=_request(),
            x_organization_id=str(organization_id),
            db=db_session,
            current_user=SimpleNamespace(id=owner_id),
        )

    assert exc_info.value.status_code == status_code
    assert exc_info.value.detail["error"]["code"] == response_code


@pytest.mark.asyncio
async def test_process_normalizes_owned_connection_reference(
    db_session: Session,
    monkeypatch,
) -> None:
    owner_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    knowledge_base_id = uuid.uuid4()
    connection_id = uuid.uuid4()
    _insert_user(db_session, owner_id)
    _insert_connection(
        db_session,
        connection_id=connection_id,
        owner_id=owner_id,
    )
    document = _document(connection_id)
    document.meta_info.update(
        {
            "database": "must-not-be-stored-legacy-database",
            "port": 15432,
            "type": "postgres",
            "use_ssh": True,
            "ssh": {"password": "must-not-be-stored-ssh-password"},
            "ssh_port": 10022,
            "ssh_auth_type": "password",
        }
    )
    reference_lock = Mock()

    class FakeConnectionLifecycleService:
        def __init__(self, db):
            assert db is db_session

        def lock_owned_connection_for_reference(self, **kwargs):
            reference_lock(**kwargs)

    captured_commands = []
    captured_unit_of_work = []
    job_id = uuid.uuid4()

    class FakeUseCase:
        def execute(self, command):
            captured_commands.append(command)
            return SimpleNamespace(
                job=SimpleNamespace(job_id=job_id),
                reused=False,
                dispatch_deferred=False,
            )

    def build_use_case(_db, *, unit_of_work=None):
        captured_unit_of_work.append(unit_of_work)
        return FakeUseCase()

    monkeypatch.setattr(
        knowledge_endpoint,
        "_authorized_knowledge_document",
        lambda *args, **kwargs: (
            SimpleNamespace(
                id=knowledge_base_id,
                embedding_model="embedding-model",
                organization_id=organization_id,
            ),
            document,
        ),
    )
    monkeypatch.setattr(
        knowledge_endpoint,
        "build_request_document_ingestion",
        build_use_case,
    )
    monkeypatch.setattr(
        knowledge_endpoint,
        "_ensure_document_ingestion_schema_ready",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        knowledge_endpoint,
        "ConnectionLifecycleService",
        FakeConnectionLifecycleService,
    )

    response = await knowledge_endpoint.process_document.__wrapped__(
        kb_id=knowledge_base_id,
        document_id=document.id,
        preview_request=_preview_request(
            connection_id,
            host="must-not-be-stored.example",
            port=15432,
            database="must-not-be-stored-database",
            username="must-not-be-stored",
            password="must-not-be-stored",
            type="postgres",
            use_ssh=True,
            ssh_host="must-not-be-stored-ssh.example",
            ssh_port=10022,
            ssh_username="must-not-be-stored-ssh-user",
            ssh_auth_type="password",
            join_config={
                "enabled": False,
                "password": "must-not-be-stored-nested",
            },
        ),
        request=_request(),
        x_organization_id=str(uuid.uuid4()),
        db=db_session,
        current_user=SimpleNamespace(id=owner_id),
    )

    assert response["status"] == "processing"
    reference_lock.assert_called_once_with(
        connection_id=connection_id,
        owner_id=owner_id,
    )
    assert isinstance(captured_unit_of_work[0], FakeConnectionLifecycleService)
    assert response["job_id"] == str(job_id)
    assert len(captured_commands) == 1
    settings = captured_commands[0].settings
    assert settings.meta_updates["connection_id"] == str(connection_id)
    assert "connection_id" not in settings.meta_updates["db_config"]
    assert {
        "database",
        "port",
        "type",
        "use_ssh",
        "ssh",
        "ssh_port",
        "ssh_auth_type",
    }.issubset(settings.meta_remove_keys)
    serialized = repr(settings.meta_updates)
    assert "must-not-be-stored" not in serialized
