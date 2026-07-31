from __future__ import annotations

import uuid
from unittest.mock import Mock

import pytest
from apps.shared.db.models.connection import Connection
from apps.shared.db.models.user import User
from apps.shared.services.connection_runtime_snapshot import (
    ConnectionRuntimeSnapshot,
    ConnectionRuntimeSnapshotProvider,
)
from apps.shared.services.connection_use_resolver import ConnectionUseUnavailable
from apps.shared.services.ingestion.processors.db_processor import DbProcessor
from apps.shared.utils.encryption import encryption_manager
from sqlalchemy import create_engine, update
from sqlalchemy.orm import Session, sessionmaker


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


def _source_config(connection_id: object) -> dict[str, object]:
    return {
        "connection_id": connection_id,
        "selections": [
            {
                "table_name": "safe_table",
                "columns": ["id"],
                "sensitive_columns": [],
            }
        ],
    }


def _processor(db_session: Session, *, user_id: uuid.UUID) -> DbProcessor:
    snapshot_provider = ConnectionRuntimeSnapshotProvider(
        sessionmaker(bind=db_session.get_bind())
    )
    return DbProcessor(
        db_session=db_session,
        user_id=user_id,
        connection_snapshot_provider=snapshot_provider,
    )


def _assert_safe_denial(result) -> None:
    assert result.chunks == []
    assert result.metadata["error_code"] == "configuration_invalid"
    assert result.metadata["reason_code"] == "resource.hidden"
    serialized = repr(result.model_dump())
    assert "sensitive-connection-label" not in serialized
    assert "db.internal.example" not in serialized
    assert "service-user" not in serialized
    assert "opaque-ciphertext" not in serialized


def test_other_users_connection_is_denied_before_adapter_creation(
    db_session: Session,
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
    processor = _processor(db_session, user_id=actor_id)
    connector_factory = Mock()
    processor._get_connector = connector_factory

    result = processor.process(_source_config(connection_id))

    _assert_safe_denial(result)
    connector_factory.assert_not_called()


@pytest.mark.parametrize("connection_reference", [None, "", "not-a-uuid"])
def test_invalid_connection_reference_is_denied_before_adapter_creation(
    db_session: Session,
    connection_reference: object,
) -> None:
    processor = _processor(db_session, user_id=uuid.uuid4())
    connector_factory = Mock()
    processor._get_connector = connector_factory

    result = processor.process(_source_config(connection_reference))

    _assert_safe_denial(result)
    connector_factory.assert_not_called()


def test_connection_lookup_failure_is_safe_and_does_not_create_adapter() -> None:
    snapshot_provider = Mock()
    snapshot_provider.load.side_effect = ConnectionUseUnavailable()
    processor = DbProcessor(
        db_session=Mock(),
        user_id=uuid.uuid4(),
        connection_snapshot_provider=snapshot_provider,
    )
    connector_factory = Mock()
    processor._get_connector = connector_factory

    result = processor.process(_source_config(uuid.uuid4()))

    assert result.chunks == []
    assert result.metadata == {
        "error": "Connection lookup unavailable",
        "error_code": "temporarily_unavailable",
        "reason_code": "source.temporarily_unavailable",
    }
    assert "sensitive backend detail" not in repr(result.model_dump())
    connector_factory.assert_not_called()


def test_deleted_connection_is_denied_before_adapter_creation(
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
    db_session.execute(
        Connection.__table__.delete().where(Connection.id == connection_id)
    )
    db_session.commit()
    processor = _processor(db_session, user_id=owner_id)
    connector_factory = Mock()
    processor._get_connector = connector_factory

    result = processor.process(_source_config(connection_id))

    _assert_safe_denial(result)
    connector_factory.assert_not_called()


def test_ownership_change_is_denied_before_adapter_creation(
    db_session: Session,
) -> None:
    owner_id = uuid.uuid4()
    new_owner_id = uuid.uuid4()
    connection_id = uuid.uuid4()
    _insert_user(db_session, owner_id)
    _insert_user(db_session, new_owner_id)
    _insert_connection(
        db_session,
        connection_id=connection_id,
        owner_id=owner_id,
    )
    db_session.execute(
        update(Connection)
        .where(Connection.id == connection_id)
        .values(user_id=new_owner_id)
    )
    db_session.commit()
    processor = _processor(db_session, user_id=owner_id)
    connector_factory = Mock()
    processor._get_connector = connector_factory

    result = processor.process(_source_config(connection_id))

    _assert_safe_denial(result)
    connector_factory.assert_not_called()


def test_credential_decryption_failure_does_not_dial_adapter(
    db_session: Session,
    monkeypatch,
) -> None:
    owner_id = uuid.uuid4()
    connection_id = uuid.uuid4()
    _insert_user(db_session, owner_id)
    _insert_connection(
        db_session,
        connection_id=connection_id,
        owner_id=owner_id,
    )
    connector = Mock()
    processor = _processor(db_session, user_id=owner_id)
    processor._get_connector = Mock(return_value=connector)
    monkeypatch.setattr(
        encryption_manager,
        "decrypt",
        Mock(side_effect=ValueError("simulated decrypt failure")),
    )

    result = processor.process(_source_config(connection_id))

    assert result.chunks == []
    assert result.metadata == {
        "error": "Connection configuration unavailable",
        "error_code": "configuration_invalid",
        "reason_code": "configuration.invalid",
    }
    connector.fetch_data.assert_not_called()
    assert "opaque-ciphertext" not in repr(result.model_dump())


def test_owner_path_uses_connection_without_exposing_connection_detail(
    db_session: Session,
    monkeypatch,
) -> None:
    owner_id = uuid.uuid4()
    connection_id = uuid.uuid4()
    _insert_user(db_session, owner_id)
    _insert_connection(
        db_session,
        connection_id=connection_id,
        owner_id=owner_id,
    )
    connector = Mock()
    connector.fetch_data.return_value = [{"id": 1}]
    processor = _processor(db_session, user_id=owner_id)
    processor._get_connector = Mock(return_value=connector)
    monkeypatch.setattr(encryption_manager, "decrypt", Mock(return_value="test-value"))

    result = processor.process(_source_config(connection_id))

    assert result.metadata == {"source_type": "DB"}
    assert len(result.chunks) == 1
    serialized = repr(result.model_dump())
    assert str(connection_id) not in serialized
    assert "sensitive-connection-label" not in serialized
    assert "db.internal.example" not in serialized
    assert "service-user" not in serialized
    assert "opaque-ciphertext" not in serialized
    connector.fetch_data.assert_called_once()


def test_processor_creates_connector_only_after_snapshot_provider_returns() -> None:
    state = {"snapshot_complete": False}
    snapshot = ConnectionRuntimeSnapshot(
        adapter_type="postgres",
        host="db.example.test",
        port=5432,
        database="application",
        username="runtime-user",
        password="test-value",
    )
    snapshot_provider = Mock()

    def load_snapshot(*_args, **_kwargs):
        state["snapshot_complete"] = True
        return snapshot

    snapshot_provider.load.side_effect = load_snapshot
    connector = Mock()
    connector.fetch_data.return_value = []
    processor = DbProcessor(
        db_session=Mock(),
        user_id=uuid.uuid4(),
        connection_snapshot_provider=snapshot_provider,
    )

    def build_connector(_adapter_type):
        assert state["snapshot_complete"] is True
        return connector

    processor._get_connector = Mock(side_effect=build_connector)

    result = processor.process(_source_config(uuid.uuid4()))

    assert result.metadata == {"source_type": "DB"}
    processor._get_connector.assert_called_once_with("postgres")
    connector.fetch_data.assert_called_once()
