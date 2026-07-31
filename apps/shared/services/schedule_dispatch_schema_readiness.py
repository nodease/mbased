from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from apps.shared.domain.schedule_dispatch import ScheduleDispatchSettings
from apps.shared.services.alembic_readiness import (
    AlembicReadinessResult,
    check_alembic_readiness_with_inspector,
)
from sqlalchemy import inspect
from sqlalchemy.engine import Engine

ROOT_DIR = Path(__file__).resolve().parents[3]


class ScheduleDispatchMigrationNotReadyError(RuntimeError):
    pass


REQUIRED_SCHEDULE_DISPATCH_SCHEMA = {
    "schedule_dispatch_claims": {
        "id",
        "schedule_id",
        "organization_id",
        "deployment_id",
        "scheduled_for",
        "idempotency_key",
        "status",
        "lease_owner",
        "lease_expires_at",
        "attempt_count",
        "next_attempt_at",
        "celery_task_id",
        "claimed_at",
        "workflow_run_id",
        "workflow_run_missing_reported_at",
        "execution_deadline_at",
        "safe_reason_code",
        "outcome_reviewed_at",
        "outcome_review_audit_id",
        "outcome_resolution_code",
        "enqueued_at",
        "started_at",
        "completed_at",
        "created_at",
        "updated_at",
    },
    "workflow_runs": {"id", "user_id", "trigger_mode", "workflow_task_id"},
    "schedules": {
        "id",
        "deployment_id",
        "cron_expression",
        "timezone",
        "next_run_at",
        "configuration_error_code",
    },
    "workflow_deployments": {
        "id",
        "app_id",
        "type",
        "graph_snapshot",
        "created_by",
        "is_active",
    },
    "apps": {"id", "workflow_id", "organization_id", "active_deployment_id"},
    "workflows": {"id", "app_id", "organization_id"},
}

REQUIRED_SCHEDULE_DISPATCH_CHECKS = {
    "ck_schedule_dispatch_claims_status",
    "ck_schedule_dispatch_claims_attempt_count",
    "ck_schedule_dispatch_claims_task_id",
    "ck_schedule_dispatch_claims_next_attempt_status",
    "ck_schedule_dispatch_claims_preadmission_run",
    "ck_schedule_dispatch_claims_status_fields",
    "ck_schedule_dispatch_claims_safe_reason",
    "ck_schedule_dispatch_claims_outcome_review",
    "ck_schedule_dispatch_claims_timestamp_order",
}

REQUIRED_SCHEDULE_DISPATCH_UNIQUES = {
    "uq_schedule_dispatch_claims_occurrence",
    "uq_schedule_dispatch_claims_idempotency_key",
    "uq_schedule_dispatch_claims_workflow_run_id",
}


def schedule_dispatch_alembic_readiness(
    schema_inspector,
    *,
    script_directory: ScriptDirectory | None = None,
) -> AlembicReadinessResult:
    script = script_directory or _script_directory()
    return check_alembic_readiness_with_inspector(
        schema_inspector,
        code_heads=script.get_heads(),
        known_revisions=[revision.revision for revision in script.walk_revisions()],
    )


def require_schedule_dispatch_migration_ready(
    engine: Engine,
    *,
    settings: ScheduleDispatchSettings,
) -> None:
    if not settings.processes_existing_claims:
        return
    schema_inspector = inspect(engine)
    result = schedule_dispatch_alembic_readiness(schema_inspector)
    if not result.ready or not required_schedule_dispatch_schema_exists(
        schema_inspector
    ):
        raise ScheduleDispatchMigrationNotReadyError(
            "database migration is not ready for schedule dispatch"
        )


def required_schedule_dispatch_schema_exists(schema_inspector) -> bool:
    try:
        for table_name, required_columns in REQUIRED_SCHEDULE_DISPATCH_SCHEMA.items():
            if not schema_inspector.has_table(table_name):
                return False
            actual_columns = {
                column["name"] for column in schema_inspector.get_columns(table_name)
            }
            if not required_columns <= actual_columns:
                return False
        actual_checks = {
            constraint.get("name")
            for constraint in schema_inspector.get_check_constraints(
                "schedule_dispatch_claims"
            )
        }
        if not REQUIRED_SCHEDULE_DISPATCH_CHECKS <= actual_checks:
            return False
        actual_uniques = {
            constraint.get("name")
            for constraint in schema_inspector.get_unique_constraints(
                "schedule_dispatch_claims"
            )
        }
        if not REQUIRED_SCHEDULE_DISPATCH_UNIQUES <= actual_uniques:
            return False
        return True
    except Exception:
        return False


def _script_directory() -> ScriptDirectory:
    config = Config(str(ROOT_DIR / "apps" / "shared" / "alembic.ini"))
    config.set_main_option(
        "script_location",
        str(ROOT_DIR / "apps" / "shared" / "alembic"),
    )
    return ScriptDirectory.from_config(config)
