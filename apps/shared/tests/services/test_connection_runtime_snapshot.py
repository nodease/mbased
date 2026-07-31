from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from apps.shared.db.models.connection import Connection
from apps.shared.db.models.user import User
from apps.shared.services.connection_runtime_snapshot import (
    ConnectionRuntimeSnapshotConfigurationInvalid,
    ConnectionRuntimeSnapshotProvider,
)
from apps.shared.services.connection_use_resolver import (
    ConnectionUseDenied,
    ConnectionUseUnavailable,
)
from apps.shared.utils.encryption import encryption_manager
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


class _TrackingSession(Session):
    def __init__(self, *args, tracker: list["_TrackingSession"], **kwargs):
        super().__init__(*args, **kwargs)
        self.closed_by_provider = False
        tracker.append(self)

    def close(self) -> None:
        self.closed_by_provider = True
        super().close()


@pytest.fixture
def snapshot_store():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    User.__table__.create(engine)
    Connection.__table__.create(engine)
    owner_id = uuid.uuid4()
    connection_id = uuid.uuid4()
    with Session(engine) as session:
        session.execute(
            User.__table__.insert().values(
                id=owner_id,
                email="snapshot-owner@example.test",
                name="Snapshot Owner",
                social_provider="local",
            )
        )
        session.execute(
            Connection.__table__.insert().values(
                id=connection_id,
                user_id=owner_id,
                name="must-not-be-projected",
                type="postgres",
                host="db.example.test",
                port=5432,
                database="application",
                username="runtime-user",
                encrypted_password="opaque-test-ciphertext",
                use_ssh=False,
            )
        )
        session.commit()

    sessions: list[_TrackingSession] = []

    def session_factory() -> Session:
        return _TrackingSession(bind=engine, tracker=sessions)

    try:
        yield session_factory, sessions, connection_id, owner_id
    finally:
        engine.dispose()


def test_provider_returns_immutable_snapshot_after_closing_session(
    snapshot_store,
    monkeypatch,
) -> None:
    session_factory, sessions, connection_id, owner_id = snapshot_store
    monkeypatch.setattr(encryption_manager, "decrypt", lambda _value: "test-value")

    snapshot = ConnectionRuntimeSnapshotProvider(session_factory).load(
        connection_id,
        execution_subject_user_id=owner_id,
    )

    assert len(sessions) == 1
    assert sessions[0].closed_by_provider is True
    assert sessions[0].in_transaction() is False
    assert snapshot.adapter_type == "postgres"
    assert snapshot.to_connector_config() == {
        "host": "db.example.test",
        "port": 5432,
        "database": "application",
        "username": "runtime-user",
        "password": "test-value",
    }
    serialized = repr(snapshot)
    assert "must-not-be-projected" not in serialized
    assert "db.example.test" not in serialized
    assert "runtime-user" not in serialized
    assert "opaque-test-ciphertext" not in serialized
    assert "test-value" not in serialized


@pytest.mark.parametrize("connection_reference", [None, "", "not-a-uuid"])
def test_provider_hides_invalid_reference_and_releases_session(
    snapshot_store,
    connection_reference,
) -> None:
    session_factory, sessions, _connection_id, owner_id = snapshot_store

    with pytest.raises(ConnectionUseDenied):
        ConnectionRuntimeSnapshotProvider(session_factory).load(
            connection_reference,
            execution_subject_user_id=owner_id,
        )

    assert sessions[-1].closed_by_provider is True
    assert sessions[-1].in_transaction() is False


def test_provider_hides_non_owner_and_releases_session(snapshot_store) -> None:
    session_factory, sessions, connection_id, _owner_id = snapshot_store

    with pytest.raises(ConnectionUseDenied):
        ConnectionRuntimeSnapshotProvider(session_factory).load(
            connection_id,
            execution_subject_user_id=uuid.uuid4(),
        )

    assert sessions[-1].closed_by_provider is True
    assert sessions[-1].in_transaction() is False


def test_provider_maps_decryption_failure_without_ciphertext(
    snapshot_store,
    monkeypatch,
) -> None:
    session_factory, sessions, connection_id, owner_id = snapshot_store

    def fail_decrypt(_value: str) -> str:
        raise ValueError("raw crypto detail")

    monkeypatch.setattr(encryption_manager, "decrypt", fail_decrypt)

    with pytest.raises(ConnectionRuntimeSnapshotConfigurationInvalid) as exc_info:
        ConnectionRuntimeSnapshotProvider(session_factory).load(
            connection_id,
            execution_subject_user_id=owner_id,
        )

    assert "raw crypto detail" not in str(exc_info.value)
    assert "opaque-test-ciphertext" not in str(exc_info.value)
    assert sessions[-1].closed_by_provider is True
    assert sessions[-1].in_transaction() is False


def test_provider_maps_session_factory_failure_to_safe_unavailable() -> None:
    def broken_session_factory() -> Session:
        raise RuntimeError("raw pool detail")

    with pytest.raises(ConnectionUseUnavailable) as exc_info:
        ConnectionRuntimeSnapshotProvider(broken_session_factory).load(
            uuid.uuid4(),
            execution_subject_user_id=uuid.uuid4(),
        )

    assert "raw pool detail" not in str(exc_info.value)


def test_provider_releases_session_when_projection_is_cancelled(
    snapshot_store,
    monkeypatch,
) -> None:
    session_factory, sessions, connection_id, owner_id = snapshot_store

    def cancel_projection(_connection) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(
        ConnectionRuntimeSnapshotProvider,
        "_project",
        staticmethod(cancel_projection),
    )

    with pytest.raises(asyncio.CancelledError):
        ConnectionRuntimeSnapshotProvider(session_factory).load(
            connection_id,
            execution_subject_user_id=owner_id,
        )

    assert sessions[-1].closed_by_provider is True
    assert sessions[-1].in_transaction() is False


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("host", None),
        ("database", None),
        ("username", None),
        ("port", 0),
        ("port", 65_536),
    ],
)
def test_provider_rejects_invalid_runtime_fields_without_dialing(
    monkeypatch,
    field_name,
    invalid_value,
) -> None:
    monkeypatch.setattr(encryption_manager, "decrypt", lambda _value: "test-value")
    values = {
        "type": "postgres",
        "host": "db.example.test",
        "port": 5432,
        "database": "application",
        "username": "runtime-user",
        "encrypted_password": "opaque-test-ciphertext",
        "use_ssh": False,
    }
    values[field_name] = invalid_value

    with pytest.raises(ConnectionRuntimeSnapshotConfigurationInvalid):
        ConnectionRuntimeSnapshotProvider._project(
            SimpleNamespace(**values),
        )


def test_snapshot_preserves_stored_database_identifiers(monkeypatch) -> None:
    monkeypatch.setattr(encryption_manager, "decrypt", lambda _value: "test-value")

    snapshot = ConnectionRuntimeSnapshotProvider._project(
        SimpleNamespace(
            type="postgres",
            host="db.example.test",
            port=5432,
            database=" application ",
            username=" runtime-user ",
            encrypted_password="opaque-test-ciphertext",
            use_ssh=False,
        )
    )

    assert snapshot.to_connector_config()["database"] == " application "
    assert snapshot.to_connector_config()["username"] == " runtime-user "


def test_snapshot_preserves_passwordless_ssh_agent_path(monkeypatch) -> None:
    decrypt_calls = []

    def decrypt(value):
        decrypt_calls.append(value)
        return "database-password"

    monkeypatch.setattr(encryption_manager, "decrypt", decrypt)
    snapshot = ConnectionRuntimeSnapshotProvider._project(
        SimpleNamespace(
            type="postgres",
            host="db.example.test",
            port=5432,
            database="application",
            username="runtime-user",
            encrypted_password="opaque-database-ciphertext",
            use_ssh=True,
            ssh_host="ssh.example.test",
            ssh_port=22,
            ssh_username="ssh-user",
            ssh_auth_type="password",
            encrypted_ssh_password=None,
        )
    )

    assert decrypt_calls == ["opaque-database-ciphertext"]
    assert snapshot.to_connector_config()["ssh"]["password"] is None
