import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from apps.gateway.services.connection_lifecycle_service import (
    CONNECTION_REFERENCE_LOCK_TIMEOUT_MS,
    ConnectionLifecycleBusy,
    ConnectionLifecycleConflict,
    ConnectionLifecycleHidden,
    ConnectionLifecycleInUse,
    ConnectionLifecycleService,
    ConnectionLifecycleUnavailable,
)
from apps.shared.db.models.connection import Connection
from apps.shared.db.models.knowledge import Document
from apps.shared.db.models.user import User


class _Query:
    def __init__(self, db, entity):
        self.db = db
        self.entity = entity

    def filter(self, *_args):
        return self

    def with_for_update(self):
        self.db.locked_entities.append(self.entity)
        if self.entity is Connection:
            self.db.connection_locked = True
        return self

    def populate_existing(self):
        return self

    def first(self):
        if self.db.query_error is not None:
            raise self.db.query_error
        if self.entity is Connection:
            return self.db.connection
        return self.db.reference


class _Db:
    def __init__(
        self,
        *,
        connection=None,
        reference=None,
        query_error=None,
        flush_error=None,
        commit_error=None,
        dialect_name="sqlite",
    ):
        self.connection = connection
        self.reference = reference
        self.query_error = query_error
        self.flush_error = flush_error
        self.commit_error = commit_error
        self.connection_locked = False
        self.locked_entities = []
        self.deleted = None
        self.flushed = False
        self.committed = False
        self.rolled_back = False
        self.executed = []
        self.bind = SimpleNamespace(
            dialect=SimpleNamespace(name=dialect_name)
        )

    def get_bind(self):
        return self.bind

    def execute(self, statement):
        self.executed.append(str(statement))

    def query(self, entity):
        return _Query(self, entity)

    def delete(self, entity):
        self.deleted = entity

    def commit(self):
        self.committed = True
        if self.commit_error is not None:
            raise self.commit_error

    def flush(self):
        self.flushed = True
        if self.flush_error is not None:
            raise self.flush_error

    def rollback(self):
        self.rolled_back = True


def test_delete_unreferenced_connection_locks_owner_row_and_commits():
    connection = SimpleNamespace(id=uuid.uuid4(), user_id=uuid.uuid4())
    db = _Db(connection=connection)

    ConnectionLifecycleService(db).delete_unreferenced_connection(
        connection_id=connection.id,
        owner_id=connection.user_id,
    )

    assert db.connection_locked is True
    assert db.deleted is connection
    assert db.committed is True
    assert db.rolled_back is False


def test_reference_target_lock_uses_owner_row_without_committing():
    connection = SimpleNamespace(id=uuid.uuid4(), user_id=uuid.uuid4())
    db = _Db(connection=connection)

    locked = ConnectionLifecycleService(db).lock_owned_connection_for_reference(
        connection_id=connection.id,
        owner_id=connection.user_id,
    )

    assert locked is connection
    assert db.connection_locked is True
    assert db.committed is False
    assert db.rolled_back is False


def test_reference_target_lock_sets_bounded_postgres_lock_timeout():
    connection = SimpleNamespace(id=uuid.uuid4(), user_id=uuid.uuid4())
    db = _Db(connection=connection, dialect_name="postgresql")

    ConnectionLifecycleService(db).lock_owned_connection_for_reference(
        connection_id=connection.id,
        owner_id=connection.user_id,
    )

    assert db.executed == [
        f"SET LOCAL lock_timeout = '{CONNECTION_REFERENCE_LOCK_TIMEOUT_MS}ms'"
    ]


def test_reference_mutation_locks_connection_then_document():
    expected_updated_at = datetime.now(timezone.utc)
    connection = SimpleNamespace(id=uuid.uuid4(), user_id=uuid.uuid4())
    document = SimpleNamespace(id=uuid.uuid4(), updated_at=expected_updated_at)
    db = _Db(connection=connection, reference=document)

    locked = ConnectionLifecycleService(
        db
    ).lock_owned_connection_and_document_for_reference(
        connection_id=connection.id,
        owner_id=connection.user_id,
        document_id=document.id,
        expected_document_updated_at=expected_updated_at,
    )

    assert locked is document
    assert db.locked_entities == [Connection, Document]
    assert db.rolled_back is False


def test_reference_mutation_rejects_stale_document_revision():
    expected_updated_at = datetime.now(timezone.utc)
    connection = SimpleNamespace(id=uuid.uuid4(), user_id=uuid.uuid4())
    document = SimpleNamespace(
        id=uuid.uuid4(),
        updated_at=expected_updated_at + timedelta(seconds=1),
    )
    db = _Db(connection=connection, reference=document)

    with pytest.raises(ConnectionLifecycleConflict) as exc_info:
        ConnectionLifecycleService(
            db
        ).lock_owned_connection_and_document_for_reference(
            connection_id=connection.id,
            owner_id=connection.user_id,
            document_id=document.id,
            expected_document_updated_at=expected_updated_at,
        )

    assert exc_info.value.code == "connection.reference_conflict"
    assert exc_info.value.retryable is False
    assert db.rolled_back is True


@pytest.mark.parametrize("sqlstate", ["55P03", "57014", "40P01", "40001"])
def test_reference_mutation_commit_maps_contention_to_retryable_busy(sqlstate):
    original_error = SimpleNamespace(sqlstate=sqlstate)
    db = _Db(
        commit_error=OperationalError("statement", {}, original_error),
        dialect_name="postgresql",
    )

    with pytest.raises(ConnectionLifecycleBusy) as exc_info:
        ConnectionLifecycleService(db).commit_reference_mutation()

    assert exc_info.value.code == "connection.reference_busy"
    assert exc_info.value.retryable is True
    assert sqlstate not in str(exc_info.value)
    assert db.rolled_back is True


def test_reference_mutation_commit_redacts_database_failure():
    db = _Db(commit_error=SQLAlchemyError("raw database detail"))

    with pytest.raises(ConnectionLifecycleUnavailable) as exc_info:
        ConnectionLifecycleService(db).commit_reference_mutation()

    assert "raw database detail" not in str(exc_info.value)
    assert db.rolled_back is True


@pytest.mark.parametrize("sqlstate", ["55P03", "57014", "40P01", "40001"])
def test_reference_mutation_flush_maps_contention_to_retryable_busy(sqlstate):
    original_error = SimpleNamespace(sqlstate=sqlstate)
    db = _Db(
        flush_error=OperationalError("statement", {}, original_error),
        dialect_name="postgresql",
    )

    with pytest.raises(ConnectionLifecycleBusy) as exc_info:
        ConnectionLifecycleService(db).flush()

    assert exc_info.value.code == "connection.reference_busy"
    assert sqlstate not in str(exc_info.value)
    assert db.rolled_back is True


def test_reference_mutation_commit_rolls_back_when_cancelled():
    db = _Db(commit_error=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        ConnectionLifecycleService(db).commit_reference_mutation()

    assert db.rolled_back is True


def test_delete_connection_hides_missing_or_other_owner_resource():
    db = _Db(connection=None)

    with pytest.raises(ConnectionLifecycleHidden):
        ConnectionLifecycleService(db).delete_unreferenced_connection(
            connection_id=uuid.uuid4(),
            owner_id=uuid.uuid4(),
        )

    assert db.deleted is None
    assert db.rolled_back is True


def test_delete_connection_rejects_document_reference_without_mutation():
    connection = SimpleNamespace(id=uuid.uuid4(), user_id=uuid.uuid4())
    db = _Db(connection=connection, reference=(uuid.uuid4(),))

    with pytest.raises(ConnectionLifecycleInUse):
        ConnectionLifecycleService(db).delete_unreferenced_connection(
            connection_id=connection.id,
            owner_id=connection.user_id,
        )

    assert db.deleted is None
    assert db.committed is False
    assert db.rolled_back is True


def test_delete_connection_redacts_database_failure():
    db = _Db(query_error=SQLAlchemyError("raw database detail"))

    with pytest.raises(ConnectionLifecycleUnavailable) as exc_info:
        ConnectionLifecycleService(db).delete_unreferenced_connection(
            connection_id=uuid.uuid4(),
            owner_id=uuid.uuid4(),
        )

    assert "raw database detail" not in str(exc_info.value)
    assert db.rolled_back is True


@pytest.mark.parametrize("sqlstate", ["55P03", "57014", "40P01", "40001"])
def test_delete_connection_maps_postgres_contention_to_retryable_busy(sqlstate):
    original_error = SimpleNamespace(sqlstate=sqlstate)
    db = _Db(
        query_error=OperationalError(
            "statement",
            {},
            original_error,
        ),
        dialect_name="postgresql",
    )

    with pytest.raises(ConnectionLifecycleBusy) as exc_info:
        ConnectionLifecycleService(db).delete_unreferenced_connection(
            connection_id=uuid.uuid4(),
            owner_id=uuid.uuid4(),
        )

    assert exc_info.value.code == "connection.reference_busy"
    assert exc_info.value.retryable is True
    assert sqlstate not in str(exc_info.value)
    assert db.rolled_back is True


def test_reference_lock_rolls_back_when_wait_is_cancelled():
    db = _Db(query_error=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        ConnectionLifecycleService(db).lock_owned_connection_for_reference(
            connection_id=uuid.uuid4(),
            owner_id=uuid.uuid4(),
        )

    assert db.rolled_back is True


def test_lock_hold_observation_uses_only_coarse_bucket(caplog):
    caplog.set_level("INFO")

    ConnectionLifecycleService._record_lock_hold(float("inf"))

    assert "Connection reference lock hold_bucket=lt_100ms" in caplog.text
    assert "connection_id" not in caplog.text


def test_lock_hold_observer_records_outer_transaction_end_after_flush(caplog):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    User.__table__.create(engine)
    Connection.__table__.create(engine)
    owner_id = uuid.uuid4()
    connection_id = uuid.uuid4()
    with Session(engine) as setup_db:
        setup_db.execute(
            User.__table__.insert().values(
                id=owner_id,
                email="hold-observer@example.test",
                name="Hold Observer",
                social_provider="local",
            )
        )
        setup_db.execute(
            Connection.__table__.insert().values(
                id=connection_id,
                user_id=owner_id,
                name="Before",
                type="postgres",
                host="db.example.test",
                port=5432,
                database="application",
                username="runtime-user",
                encrypted_password="opaque-test-ciphertext",
                use_ssh=False,
            )
        )
        setup_db.commit()

    caplog.set_level("INFO")
    with Session(engine) as db:
        connection = ConnectionLifecycleService(
            db
        ).lock_owned_connection_for_reference(
            connection_id=connection_id,
            owner_id=owner_id,
        )
        connection.name = "After"
        db.commit()

    assert "Connection reference lock hold_bucket=" in caplog.text
    engine.dispose()
