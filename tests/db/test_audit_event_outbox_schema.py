from __future__ import annotations

import importlib
import uuid
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import CheckConstraint, UniqueConstraint, inspect, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from apps.shared.audit import logger as audit_logger
from apps.shared.alembic.versions import (
    a8b9c0d1e2f3_add_audit_event_outbox as revision,
)
from apps.shared.db import models as shared_models
from apps.shared.db.session import engine
from apps.shared.services.audit_event_outbox import AuditEventOutboxProcessor


EXPECTED_CHECKS = {
    "ck_audit_event_outbox_status",
    "ck_audit_event_outbox_attempt_count_nonnegative",
    "ck_audit_event_outbox_max_attempts_positive",
}


def _model():
    module = importlib.import_module("apps.shared.db.models.audit_log")
    return getattr(module, "AuditEventOutbox")


def _constraint_names(table, constraint_type):
    return {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, constraint_type) and constraint.name
    }


def test_audit_event_outbox_model_is_registered_with_durable_delivery_columns():
    model = _model()
    table = model.__table__

    assert shared_models.AuditEventOutbox is model
    assert model.__tablename__ == "audit_event_outbox"
    assert set(table.columns.keys()) == {
        "id",
        "payload",
        "status",
        "owner_token",
        "lease_expires_at",
        "attempt_count",
        "max_attempts",
        "next_retry_at",
        "retryable",
        "safe_reason_code",
        "delivered_at",
        "dead_lettered_at",
        "idempotency_key",
        "created_at",
        "updated_at",
    }
    assert isinstance(table.c.payload.type, JSONB)
    assert table.c.payload.nullable is False
    assert table.c.idempotency_key.nullable is False
    assert not table.c.idempotency_key.foreign_keys
    assert not table.foreign_keys
    assert str(table.c.status.server_default.arg) == "'pending'"
    assert str(table.c.attempt_count.server_default.arg) == "0"
    assert str(table.c.max_attempts.server_default.arg) == "5"
    assert str(table.c.retryable.server_default.arg) == "true"


def test_audit_event_outbox_declares_idempotency_checks_and_due_indexes():
    table = _model().__table__

    assert EXPECTED_CHECKS <= _constraint_names(table, CheckConstraint)
    assert "uq_audit_event_outbox_idempotency_key" in _constraint_names(
        table,
        UniqueConstraint,
    )
    assert {index.name for index in table.indexes} >= {
        "ix_audit_event_outbox_status_retry",
        "ix_audit_event_outbox_lease",
    }


def test_audit_event_outbox_migration_is_additive_and_reversible():
    versions = Path("apps/shared/alembic/versions")
    candidates = [
        path
        for path in versions.glob("*.py")
        if '"audit_event_outbox"' in path.read_text(encoding="utf-8")
    ]

    assert len(candidates) == 1
    source = candidates[0].read_text(encoding="utf-8")
    assert 'down_revision: str | Sequence[str] | None = "a7b8c9d0e1f2"' in source
    assert 'op.create_table(\n        "audit_event_outbox"' in source
    assert 'op.drop_table("audit_event_outbox")' in source


def test_audit_event_outbox_migration_upgrades_and_downgrades_in_postgres(
    monkeypatch,
):
    schema = f"test_audit_outbox_{uuid.uuid4().hex}"
    try:
        connection = engine.connect()
    except OperationalError:
        pytest.skip("local PostgreSQL is unavailable; connection details omitted")

    transaction = connection.begin()
    try:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
        operations = Operations(MigrationContext.configure(connection))
        monkeypatch.setattr(revision, "op", operations)

        revision.upgrade()

        inspector = inspect(connection)
        assert "audit_event_outbox" in inspector.get_table_names(schema=schema)
        columns = {
            column["name"]: column
            for column in inspector.get_columns("audit_event_outbox", schema=schema)
        }
        assert columns["payload"]["nullable"] is False
        assert "pending" in str(columns["status"]["default"])
        assert str(columns["max_attempts"]["default"]) == "5"
        assert {
            index["name"]
            for index in inspector.get_indexes(
                "audit_event_outbox",
                schema=schema,
            )
        } >= {
            "ix_audit_event_outbox_status_retry",
            "ix_audit_event_outbox_lease",
        }
        assert {
            constraint["name"]
            for constraint in inspector.get_unique_constraints(
                "audit_event_outbox",
                schema=schema,
            )
        } >= {"uq_audit_event_outbox_idempotency_key"}

        session = Session(bind=connection)
        try:
            audit_id = audit_logger.record_audit(
                "schedule.execute",
                "action",
                metadata={"organization_id": str(uuid.uuid4())},
                db_session=session,
            )
            session.commit()
        finally:
            session.close()

        stored = connection.execute(
            text(
                "SELECT payload, status, idempotency_key "
                "FROM audit_event_outbox WHERE idempotency_key=:key"
            ),
            {"key": str(audit_id)},
        ).mappings().one()
        assert stored["status"] == "pending"
        assert stored["idempotency_key"] == str(audit_id)
        assert stored["payload"]["id"] == str(audit_id)
        assert not hasattr(audit_logger, "celery_app")

        dispatched = []
        processor_session = Session(bind=connection)
        try:
            result = AuditEventOutboxProcessor(
                processor_session,
                after_commit=dispatched.append,
            ).process_due_events(owner_token="postgres-worker", limit=10)
        finally:
            processor_session.close()

        assert result.processed_count == 1
        assert result.recovered_count == 0
        delivered = connection.execute(
            text(
                "SELECT payload, status, attempt_count, owner_token, delivered_at "
                "FROM audit_event_outbox WHERE idempotency_key=:key"
            ),
            {"key": str(audit_id)},
        ).mappings().one()
        assert delivered["payload"] == {}
        assert delivered["status"] == "succeeded"
        assert delivered["attempt_count"] == 1
        assert delivered["owner_token"] is None
        assert delivered["delivered_at"] is not None
        assert connection.execute(
            text("SELECT count(*) FROM audit_logs WHERE id=:id"),
            {"id": audit_id},
        ).scalar_one() == 1
        assert dispatched == [audit_id]

        revision.downgrade()
        assert "audit_event_outbox" not in inspect(connection).get_table_names(
            schema=schema
        )
    finally:
        transaction.rollback()
        connection.close()
