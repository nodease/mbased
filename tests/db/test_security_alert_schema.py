from __future__ import annotations

import importlib
from pathlib import Path

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

from apps.shared.db import models as shared_models


EXPECTED_ALERT_CHECKS = {
    "ck_security_alerts_occurrence_count_nonnegative",
    "ck_security_alerts_lifecycle_version_positive",
    "ck_security_alerts_severity",
    "ck_security_alerts_status",
    "ck_security_alerts_resolution_type",
    "ck_security_alerts_timestamp_order",
    "ck_security_alerts_status_fields",
}

EXPECTED_NOTIFICATION_OUTBOX_CHECKS = {
    "ck_security_alert_notification_outbox_status",
    "ck_security_alert_notification_outbox_attempt_nonnegative",
    "ck_security_alert_notification_outbox_max_attempts_positive",
}


def _security_alert_models():
    module = importlib.import_module("apps.shared.db.models.security_alert")
    return (
        module.SecurityAlert,
        module.SecurityAlertAuditEvent,
        module.SecurityAlertReconciliationWatermark,
    )


def _constraint_names(table, constraint_type):
    return {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, constraint_type) and constraint.name
    }


def _security_alert_notification_outbox_model():
    module = importlib.import_module("apps.shared.db.models.security_alert")
    return getattr(module, "SecurityAlertNotificationOutbox")


def _security_alert_reconciliation_receipt_model():
    module = importlib.import_module("apps.shared.db.models.security_alert")
    return getattr(module, "SecurityAlertReconciliationReceipt")


def test_security_alert_models_are_registered_with_expected_table_names():
    alert_model, evidence_model, watermark_model = _security_alert_models()

    assert shared_models.SecurityAlert is alert_model
    assert shared_models.SecurityAlertAuditEvent is evidence_model
    assert shared_models.SecurityAlertReconciliationWatermark is watermark_model
    assert alert_model.__tablename__ == "security_alerts"
    assert evidence_model.__tablename__ == "security_alert_audit_events"
    assert watermark_model.__tablename__ == "security_alert_reconciliation_watermarks"


def test_security_alert_reconciliation_models_track_batch_generations():
    _, _, watermark_model = _security_alert_models()
    receipt_model = _security_alert_reconciliation_receipt_model()

    assert watermark_model.__table__.c.reconciliation_generation.nullable is False
    assert receipt_model.__table__.c.discovered_generation.nullable is False
    assert receipt_model.__table__.c.evaluated_generation.nullable is False


def test_security_alert_model_declares_required_columns_and_defaults():
    alert_model, _, _ = _security_alert_models()
    table = alert_model.__table__

    required_columns = {
        "id",
        "organization_id",
        "subject_actor_id",
        "rule_id",
        "rule_version",
        "severity",
        "status",
        "policy_reason",
        "detection_key",
        "occurrence_count",
        "episode_count",
        "first_detected_at",
        "last_detected_at",
        "last_episode_started_at",
        "lifecycle_version",
        "acknowledged_by",
        "acknowledged_at",
        "resolution_type",
        "resolution_reason",
        "resolved_by",
        "resolved_at",
        "created_at",
        "updated_at",
    }
    assert required_columns == set(table.columns.keys())

    assert table.c.organization_id.nullable is False
    assert table.c.subject_actor_id.nullable is False
    assert not table.c.subject_actor_id.foreign_keys
    assert table.c.policy_reason.nullable is True
    assert table.c.occurrence_count.nullable is False
    assert table.c.occurrence_count.server_default is not None
    assert str(table.c.occurrence_count.server_default.arg) == "0"
    assert table.c.lifecycle_version.nullable is False
    assert table.c.lifecycle_version.server_default is not None
    assert str(table.c.lifecycle_version.server_default.arg) == "1"

    for name in (
        "first_detected_at",
        "last_detected_at",
        "created_at",
        "updated_at",
    ):
        assert table.c[name].type.timezone is True, name


def test_security_alert_tables_declare_required_constraints_and_indexes():
    alert_model, evidence_model, _ = _security_alert_models()
    alert_table = alert_model.__table__
    evidence_table = evidence_model.__table__

    assert EXPECTED_ALERT_CHECKS <= _constraint_names(
        alert_table,
        CheckConstraint,
    )
    assert "uq_security_alert_audit_events_alert_audit" in _constraint_names(
        evidence_table,
        UniqueConstraint,
    )

    active_unique = next(
        index
        for index in alert_table.indexes
        if index.name == "uq_security_alerts_active_detection_key"
    )
    assert active_unique.unique is True
    where = str(active_unique.dialect_options["postgresql"]["where"])
    assert "status" in where
    assert "open" in where
    assert "acknowledged" in where

    organization_fk = next(
        constraint
        for constraint in alert_table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
        and [column.name for column in constraint.columns] == ["organization_id"]
    )
    assert organization_fk.ondelete in {None, "NO ACTION", "RESTRICT"}


def test_security_alert_indexes_cover_each_organization_filter_prefix():
    alert_model, _, _ = _security_alert_models()
    index_prefixes = {
        tuple(column.name for column in index.columns)[:2]
        for index in alert_model.__table__.indexes
    }

    assert {
        ("organization_id", "status"),
        ("organization_id", "severity"),
        ("organization_id", "rule_id"),
        ("organization_id", "subject_actor_id"),
    } <= index_prefixes


def test_security_alert_handler_deletion_is_compatible_with_status_constraint():
    alert_model, _, _ = _security_alert_models()
    table = alert_model.__table__

    for column_name in ("acknowledged_by", "resolved_by"):
        foreign_key = next(iter(table.c[column_name].foreign_keys))
        assert foreign_key.ondelete == "SET NULL"

    status_fields_constraint = next(
        constraint
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
        and constraint.name == "ck_security_alerts_status_fields"
    )
    status_fields_sql = str(status_fields_constraint.sqltext)

    assert "acknowledged_by IS NOT NULL" not in status_fields_sql
    assert "resolved_by IS NOT NULL" not in status_fields_sql


def test_security_alert_evidence_rejects_orphans_and_cascades_with_parents():
    _, evidence_model, _ = _security_alert_models()
    table = evidence_model.__table__

    expected_foreign_keys = {
        "security_alert_id": "security_alerts.id",
        "audit_log_id": "audit_logs.id",
    }
    for column_name, expected_target in expected_foreign_keys.items():
        column = table.c[column_name]
        foreign_key = next(iter(column.foreign_keys))

        assert column.nullable is False
        assert foreign_key.target_fullname == expected_target
        assert foreign_key.ondelete == "CASCADE"


def test_security_alert_migration_defines_upgrade_and_downgrade_for_both_tables():
    versions = Path("apps/shared/alembic/versions")
    candidates = [
        path
        for path in versions.glob("*.py")
        if "security_alerts" in path.read_text(encoding="utf-8")
        and "security_alert_audit_events" in path.read_text(encoding="utf-8")
    ]

    assert len(candidates) == 1
    source = candidates[0].read_text(encoding="utf-8")
    assert "def upgrade" in source
    assert "def downgrade" in source
    assert source.index("drop_table(\"security_alert_audit_events\")") < source.index(
        "drop_table(\"security_alerts\")"
    )


def test_security_alert_watermark_declares_durable_cursor_contract():
    _, _, watermark_model = _security_alert_models()
    table = watermark_model.__table__

    assert set(table.columns.keys()) == {
        "processor_name",
        "activation_started_at",
        "cursor_occurred_at",
        "cursor_audit_log_id",
        "reconciliation_generation",
        "created_at",
        "updated_at",
    }
    assert table.c.processor_name.primary_key is True
    assert table.c.activation_started_at.nullable is False
    assert table.c.cursor_occurred_at.nullable is True
    assert table.c.cursor_audit_log_id.nullable is True
    assert table.c.reconciliation_generation.nullable is False
    assert str(table.c.reconciliation_generation.server_default.arg) == "0"
    assert not table.c.cursor_audit_log_id.foreign_keys
    assert table.c.activation_started_at.type.timezone is True
    assert table.c.cursor_occurred_at.type.timezone is True

    checks = _constraint_names(table, CheckConstraint)
    assert "ck_security_alert_reconciliation_cursor_pair" in checks
    assert "ck_security_alert_reconcile_generation_nonnegative" in checks


def test_security_alert_watermark_migration_adds_cursor_table_and_scan_index():
    path = Path(
        "apps/shared/alembic/versions/"
        "b28d9e0f1a32_add_security_alert_reconciliation_watermark.py"
    )
    source = path.read_text(encoding="utf-8")

    assert 'down_revision: Union[str, Sequence[str], None] = "a17c8d9e0f21"' in source
    assert '"security_alert_reconciliation_watermarks"' in source
    assert '"ix_audit_logs_occurred_at_id"' in source
    assert source.index('drop_index("ix_audit_logs_occurred_at_id"') < source.index(
        'drop_table("security_alert_reconciliation_watermarks")'
    )


def test_security_alert_reconciliation_receipt_declares_processed_contract():
    receipt_model = _security_alert_reconciliation_receipt_model()
    table = receipt_model.__table__

    assert shared_models.SecurityAlertReconciliationReceipt is receipt_model
    assert table.name == "security_alert_reconciliation_receipts"
    assert set(table.columns.keys()) == {
        "processor_name",
        "audit_log_id",
        "discovered_generation",
        "evaluated_generation",
        "processed_at",
    }
    assert [column.name for column in table.primary_key.columns] == [
        "processor_name",
        "audit_log_id",
    ]
    assert not table.c.audit_log_id.foreign_keys
    assert table.c.discovered_generation.nullable is False
    assert table.c.evaluated_generation.nullable is False
    assert str(table.c.discovered_generation.server_default.arg) == "0"
    assert str(table.c.evaluated_generation.server_default.arg) == "0"
    assert table.c.processed_at.nullable is False
    assert table.c.processed_at.type.timezone is True
    checks = _constraint_names(table, CheckConstraint)
    assert (
        "ck_security_alert_receipt_generations_nonnegative"
        in checks
    )
    assert "ck_security_alert_receipt_evaluation_order" in checks


def test_security_alert_reconciliation_receipt_migration_is_additive():
    path = Path(
        "apps/shared/alembic/versions/"
        "1a5b6c7d8e91_add_security_alert_reconciliation_receipts.py"
    )
    source = path.read_text(encoding="utf-8")

    assert 'down_revision: Union[str, Sequence[str], None] = "0f4a5b6c7d89"' in source
    assert '"security_alert_reconciliation_receipts"' in source
    assert 'drop_table("security_alert_reconciliation_receipts")' in source


def test_security_alert_reconciliation_generation_migration_is_additive():
    path = Path(
        "apps/shared/alembic/versions/"
        "2b6c7d8e9f02_add_security_alert_reconciliation_generations.py"
    )
    source = path.read_text(encoding="utf-8")

    assert 'down_revision: Union[str, Sequence[str], None] = "1a5b6c7d8e91"' in source
    assert '"reconciliation_generation"' in source
    assert '"discovered_generation"' in source
    assert '"evaluated_generation"' in source


def test_security_alert_notification_outbox_declares_durable_delivery_contract():
    outbox_model = _security_alert_notification_outbox_model()
    table = outbox_model.__table__

    assert shared_models.SecurityAlertNotificationOutbox is outbox_model
    assert table.name == "security_alert_notification_outbox"
    assert set(table.columns.keys()) == {
        "id",
        "organization_id",
        "event_type",
        "idempotency_key",
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
        "created_at",
        "updated_at",
    }
    assert str(table.c.attempt_count.server_default.arg) == "0"
    assert str(table.c.max_attempts.server_default.arg) == "5"
    assert EXPECTED_NOTIFICATION_OUTBOX_CHECKS <= _constraint_names(
        table,
        CheckConstraint,
    )
    assert (
        "uq_security_alert_notification_outbox_org_idempotency"
        in _constraint_names(table, UniqueConstraint)
    )
    assert {
        "ix_security_alert_notification_outbox_status_retry",
        "ix_security_alert_notification_outbox_lease",
    } <= {index.name for index in table.indexes}

    organization_fk = next(iter(table.c.organization_id.foreign_keys))
    assert organization_fk.target_fullname == "organization.id"
    assert organization_fk.ondelete in {None, "NO ACTION", "RESTRICT"}
    for name in (
        "lease_expires_at",
        "next_retry_at",
        "delivered_at",
        "dead_lettered_at",
        "created_at",
        "updated_at",
    ):
        assert table.c[name].type.timezone is True, name


def test_security_alert_notification_outbox_has_additive_migration():
    episode_path = Path(
        "apps/shared/alembic/versions/"
        "fe4a5b6c7d89_add_security_alert_episodes.py"
    )
    path = Path(
        "apps/shared/alembic/versions/"
        "c05d6e7f8a90_add_security_alert_notification_outbox.py"
    )
    episode_source = episode_path.read_text(encoding="utf-8")
    source = path.read_text(encoding="utf-8")

    assert 'down_revision: Union[str, Sequence[str], None] = "fd0e1f2a3b4c"' in (
        episode_source
    )
    assert 'down_revision: Union[str, Sequence[str], None] = "fe4a5b6c7d89"' in source
    assert 'create_table(\n        "security_alert_notification_outbox"' in source
    assert 'drop_table("security_alert_notification_outbox")' in source
