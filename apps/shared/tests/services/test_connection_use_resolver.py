from __future__ import annotations

import uuid
from unittest.mock import Mock

import pytest
from apps.shared.db.models.connection import Connection
from apps.shared.db.models.user import User
from apps.shared.services.connection_use_resolver import (
    ConnectionUseDenied,
    ConnectionUseResolver,
    ConnectionUseUnavailable,
)
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session


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


def test_owner_can_resolve_connection(db_session: Session) -> None:
    owner_id = uuid.uuid4()
    connection_id = uuid.uuid4()
    _insert_user(db_session, owner_id)
    _insert_connection(
        db_session,
        connection_id=connection_id,
        owner_id=owner_id,
    )

    resolved = ConnectionUseResolver(db_session).resolve(
        connection_id,
        execution_subject_user_id=owner_id,
    )

    assert resolved.id == connection_id
    assert resolved.user_id == owner_id


@pytest.mark.parametrize("connection_reference", [None, "", "not-a-uuid"])
def test_invalid_connection_reference_is_resource_hidden(
    db_session: Session,
    connection_reference: object,
) -> None:
    with pytest.raises(ConnectionUseDenied) as exc_info:
        ConnectionUseResolver(db_session).resolve(
            connection_reference,
            execution_subject_user_id=uuid.uuid4(),
        )

    assert exc_info.value.code == "resource.hidden"
    assert "connection" not in str(exc_info.value).lower()


def test_other_users_connection_is_resource_hidden(db_session: Session) -> None:
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

    with pytest.raises(ConnectionUseDenied) as exc_info:
        ConnectionUseResolver(db_session).resolve(
            connection_id,
            execution_subject_user_id=actor_id,
        )

    assert exc_info.value.code == "resource.hidden"
    serialized = repr(exc_info.value)
    assert str(connection_id) not in serialized
    assert "sensitive-connection-label" not in serialized


def test_missing_execution_subject_is_resource_hidden(db_session: Session) -> None:
    with pytest.raises(ConnectionUseDenied) as exc_info:
        ConnectionUseResolver(db_session).resolve(
            uuid.uuid4(),
            execution_subject_user_id=None,
        )

    assert exc_info.value.code == "resource.hidden"


def test_use_resolution_refreshes_rotated_credential(tmp_path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'connection-use.db'}")
    User.__table__.create(engine)
    Connection.__table__.create(engine)
    owner_id = uuid.uuid4()
    connection_id = uuid.uuid4()

    with Session(engine) as setup_session:
        _insert_user(setup_session, owner_id)
        _insert_connection(
            setup_session,
            connection_id=connection_id,
            owner_id=owner_id,
        )

    with Session(engine, expire_on_commit=False) as owner_session:
        initial = ConnectionUseResolver(owner_session).resolve(
            connection_id,
            execution_subject_user_id=owner_id,
        )
        assert initial.encrypted_password == "opaque-ciphertext"
        owner_session.commit()

        with Session(engine) as rotation_session:
            rotation_session.query(Connection).filter(
                Connection.id == connection_id
            ).update(
                {Connection.encrypted_password: "rotated-ciphertext"},
                synchronize_session=False,
            )
            rotation_session.commit()

        resolved = ConnectionUseResolver(owner_session).resolve(
            connection_id,
            execution_subject_user_id=owner_id,
        )

        assert resolved.encrypted_password == "rotated-ciphertext"

    engine.dispose()


def test_lookup_failure_is_normalized_without_backend_detail() -> None:
    db = Mock()
    db.query.side_effect = SQLAlchemyError("sensitive backend detail")

    with pytest.raises(ConnectionUseUnavailable) as exc_info:
        ConnectionUseResolver(db).resolve(
            uuid.uuid4(),
            execution_subject_user_id=uuid.uuid4(),
        )

    assert exc_info.value.code == "connection.reference_unavailable"
    assert "sensitive backend detail" not in repr(exc_info.value)
