from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError

from apps.shared.alembic.versions import (
    a9b0c1d2e3f4_add_audit_log_workflow_correlation as revision,
)
from apps.shared.db.models.audit_log import AuditLog
from apps.shared.db.session import engine


def test_audit_log_model_has_nullable_workflow_correlation_fks_and_indexes():
    table = AuditLog.__table__

    assert table.c.workflow_run_id.nullable is True
    assert table.c.workflow_node_run_id.nullable is True
    run_fk = next(iter(table.c.workflow_run_id.foreign_keys))
    node_fk = next(iter(table.c.workflow_node_run_id.foreign_keys))
    assert run_fk.target_fullname == "workflow_runs.id"
    assert node_fk.target_fullname == "workflow_node_runs.id"
    assert run_fk.ondelete == "SET NULL"
    assert node_fk.ondelete == "SET NULL"
    assert {index.name for index in table.indexes} >= {
        "ix_audit_logs_workflow_run_id",
        "ix_audit_logs_workflow_node_run_id",
    }


def test_workflow_correlation_migration_is_additive_and_reversible():
    versions = Path("apps/shared/alembic/versions")
    candidates = [
        path
        for path in versions.glob("*.py")
        if '"workflow_node_run_id"' in path.read_text(encoding="utf-8")
        and '"audit_logs"' in path.read_text(encoding="utf-8")
    ]

    assert revision.down_revision == "a8b9c0d1e2f3"
    assert len(candidates) == 1
    source = candidates[0].read_text(encoding="utf-8")
    assert 'op.add_column(\n        "audit_logs"' in source
    assert 'ondelete="SET NULL"' in source
    assert "JOIN workflows AS workflow" in source
    assert "workflow.organization_id = candidates.organization_id" in source
    assert 'op.drop_column("audit_logs", "workflow_node_run_id")' in source
    assert 'op.drop_column("audit_logs", "workflow_run_id")' in source


def test_workflow_correlation_migration_backfills_only_valid_existing_refs(
    monkeypatch,
):
    schema = f"test_audit_workflow_correlation_{uuid.uuid4().hex}"
    try:
        connection = engine.connect()
    except OperationalError:
        pytest.skip("local PostgreSQL is unavailable; connection details omitted")

    transaction = connection.begin()
    try:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
        connection.execute(
            text(
                "CREATE TABLE workflows ("
                "id UUID PRIMARY KEY, organization_id UUID NULL)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE workflow_runs ("
                "id UUID PRIMARY KEY, workflow_id UUID NOT NULL "
                "REFERENCES workflows(id))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE workflow_node_runs ("
                "id UUID PRIMARY KEY, workflow_run_id UUID NOT NULL "
                "REFERENCES workflow_runs(id))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE audit_logs ("
                "id UUID PRIMARY KEY, audit_metadata JSONB NULL)"
            )
        )
        organization_id = uuid.uuid4()
        other_organization_id = uuid.uuid4()
        workflow_id = uuid.uuid4()
        workflow_run_id = uuid.uuid4()
        workflow_node_run_id = uuid.uuid4()
        valid_audit_id = uuid.uuid4()
        cross_organization_audit_id = uuid.uuid4()
        malformed_audit_id = uuid.uuid4()
        orphan_audit_id = uuid.uuid4()
        orphan_run_id = uuid.uuid4()
        connection.execute(
            text(
                "INSERT INTO workflows (id, organization_id) "
                "VALUES (:id, :organization_id)"
            ),
            {"id": workflow_id, "organization_id": organization_id},
        )
        connection.execute(
            text(
                "INSERT INTO workflow_runs (id, workflow_id) "
                "VALUES (:id, :workflow_id)"
            ),
            {"id": workflow_run_id, "workflow_id": workflow_id},
        )
        connection.execute(
            text(
                "INSERT INTO workflow_node_runs (id, workflow_run_id) "
                "VALUES (:id, :workflow_run_id)"
            ),
            {"id": workflow_node_run_id, "workflow_run_id": workflow_run_id},
        )
        connection.execute(
            text(
                "INSERT INTO audit_logs (id, audit_metadata) VALUES "
                "(:valid_id, jsonb_build_object('organization_id', :organization_id, "
                "'workflow_run_id', :run_id, 'workflow_node_run_id', :node_id)), "
                "(:cross_organization_id, "
                "jsonb_build_object('organization_id', :other_organization_id, "
                "'workflow_run_id', :run_id, 'workflow_node_run_id', :node_id)), "
                "(:malformed_id, jsonb_build_object('workflow_run_id', 'bad-id', "
                "'workflow_node_run_id', 'also-bad')), "
                "(:orphan_audit_id, "
                "jsonb_build_object('organization_id', :organization_id, "
                "'workflow_run_id', :orphan_run_id))"
            ),
            {
                "valid_id": valid_audit_id,
                "cross_organization_id": cross_organization_audit_id,
                "organization_id": str(organization_id),
                "other_organization_id": str(other_organization_id),
                "run_id": str(workflow_run_id),
                "node_id": str(workflow_node_run_id),
                "malformed_id": malformed_audit_id,
                "orphan_audit_id": orphan_audit_id,
                "orphan_run_id": str(orphan_run_id),
            },
        )

        operations = Operations(MigrationContext.configure(connection))
        monkeypatch.setattr(revision, "op", operations)
        revision.upgrade()

        columns = {
            column["name"]: column
            for column in inspect(connection).get_columns("audit_logs", schema=schema)
        }
        assert columns["workflow_run_id"]["nullable"] is True
        assert columns["workflow_node_run_id"]["nullable"] is True
        foreign_keys = {
            item["name"]: item
            for item in inspect(connection).get_foreign_keys(
                "audit_logs", schema=schema
            )
        }
        assert foreign_keys["fk_audit_logs_workflow_run_id"]["options"] == {
            "ondelete": "SET NULL"
        }
        assert foreign_keys["fk_audit_logs_workflow_node_run_id"]["options"] == {
            "ondelete": "SET NULL"
        }
        rows = {
            row["id"]: row
            for row in connection.execute(
                text(
                    "SELECT id, workflow_run_id, workflow_node_run_id "
                    "FROM audit_logs"
                )
            ).mappings()
        }
        assert rows[valid_audit_id]["workflow_run_id"] == workflow_run_id
        assert rows[valid_audit_id]["workflow_node_run_id"] == workflow_node_run_id
        assert rows[cross_organization_audit_id]["workflow_run_id"] is None
        assert rows[cross_organization_audit_id]["workflow_node_run_id"] is None
        assert rows[malformed_audit_id]["workflow_run_id"] is None
        assert rows[malformed_audit_id]["workflow_node_run_id"] is None
        assert rows[orphan_audit_id]["workflow_run_id"] is None

        connection.execute(
            text("DELETE FROM workflow_node_runs WHERE id=:id"),
            {"id": workflow_node_run_id},
        )
        connection.execute(
            text("DELETE FROM workflow_runs WHERE id=:id"),
            {"id": workflow_run_id},
        )
        preserved = connection.execute(
            text(
                "SELECT workflow_run_id, workflow_node_run_id "
                "FROM audit_logs WHERE id=:id"
            ),
            {"id": valid_audit_id},
        ).mappings().one()
        assert preserved["workflow_run_id"] is None
        assert preserved["workflow_node_run_id"] is None

        revision.downgrade()
        downgraded_columns = {
            column["name"]
            for column in inspect(connection).get_columns("audit_logs", schema=schema)
        }
        assert "workflow_run_id" not in downgraded_columns
        assert "workflow_node_run_id" not in downgraded_columns
    finally:
        transaction.rollback()
        connection.close()
