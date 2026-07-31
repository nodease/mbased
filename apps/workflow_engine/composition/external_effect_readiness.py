from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from apps.shared.services.alembic_readiness import (
    check_alembic_readiness_with_inspector,
)
from apps.workflow_engine.composition.external_effect import (
    external_effect_claim_ttl_seconds,
)
from apps.workflow_engine.domain.external_effect import provider_contract_registry


ROOT_DIR = Path(__file__).resolve().parents[3]
TABLE_NAME = "workflow_node_effect_attempts"


@dataclass(frozen=True)
class RequiredColumnShape:
    type_name: str
    nullable: bool
    timezone: bool | None = None


@dataclass(frozen=True)
class RequiredIndexShape:
    columns: tuple[str, ...]
    predicate: str | None = None


REQUIRED_COLUMN_SHAPES = {
    "id": RequiredColumnShape("UUID", False),
    "organization_id": RequiredColumnShape("UUID", False),
    "app_id": RequiredColumnShape("UUID", False),
    "workflow_id": RequiredColumnShape("UUID", False),
    "execution_id": RequiredColumnShape("UUID", False),
    "node_invocation_id": RequiredColumnShape("UUID", False),
    "workflow_run_id": RequiredColumnShape("UUID", True),
    "node_run_id": RequiredColumnShape("UUID", True),
    "node_id": RequiredColumnShape("VARCHAR(255)", False),
    "operation": RequiredColumnShape("VARCHAR(64)", False),
    "effect_sequence": RequiredColumnShape("INTEGER", False),
    "provider": RequiredColumnShape("VARCHAR(64)", False),
    "provider_contract_version": RequiredColumnShape("VARCHAR(64)", False),
    "provider_replay_capability": RequiredColumnShape("VARCHAR(16)", False),
    "result_reuse_capability": RequiredColumnShape("VARCHAR(16)", False),
    "effect_input_digest": RequiredColumnShape("VARCHAR(64)", False),
    "replay_deadline_at": RequiredColumnShape("TIMESTAMP", True, True),
    "status": RequiredColumnShape("VARCHAR(16)", False),
    "outcome": RequiredColumnShape("VARCHAR(32)", True),
    "replay_decision": RequiredColumnShape("VARCHAR(32)", True),
    "claim_owner": RequiredColumnShape("VARCHAR(64)", True),
    "claim_expires_at": RequiredColumnShape("TIMESTAMP", True, True),
    "claim_generation": RequiredColumnShape("INTEGER", False),
    "key_version": RequiredColumnShape("VARCHAR(32)", True),
    "key_format_version": RequiredColumnShape("VARCHAR(32)", True),
    "idempotency_key_fingerprint": RequiredColumnShape("VARCHAR(64)", True),
    "replay_result": RequiredColumnShape("JSONB", True),
    "provider_status_code": RequiredColumnShape("INTEGER", True),
    "error_code": RequiredColumnShape("VARCHAR(64)", True),
    "provider_started_at": RequiredColumnShape("TIMESTAMP", True, True),
    "terminal_at": RequiredColumnShape("TIMESTAMP", True, True),
    "created_at": RequiredColumnShape("TIMESTAMP", False, True),
    "updated_at": RequiredColumnShape("TIMESTAMP", False, True),
}
REQUIRED_CHECK_SHAPES = {
    "ck_workflow_node_effect_attempts_counters": (
        "effect_sequence",
        "claim_generation",
    ),
    "ck_workflow_node_effect_attempts_enums": (
        "provider_replay_capability",
        "result_reuse_capability",
        "supported",
        "unsupported",
        "unknown",
        "unavailable",
        "effect_outcome_unknown",
        "replay_same_key",
    ),
    "ck_workflow_node_effect_attempts_status_shape": (
        "status",
        "prepared",
        "in_flight",
        "terminal",
        "claim_owner",
        "claim_expires_at",
        "provider_started_at",
        "terminal_at",
    ),
    "ck_workflow_node_effect_attempts_outcome_decision": (
        "outcome",
        "succeeded",
        "failed_before_effect",
        "effect_outcome_unknown",
        "replay_decision",
        "replay_same_key",
        "provider_replay_capability",
        "supported",
    ),
    "ck_workflow_node_effect_attempts_key_contract": (
        "provider_replay_capability",
        "supported",
        "unsupported",
        "unknown",
        "replay_deadline_at",
        "key_version",
        "key_format_version",
        "idempotency_key_fingerprint",
    ),
    "ck_workflow_node_effect_attempts_replay_result": (
        "replay_decision",
        "reuse_result",
        "result_reuse_capability",
        "supported",
        "replay_result",
    ),
    "ck_workflow_node_effect_attempts_provider_summary": (
        "status",
        "terminal",
        "provider_status_code",
        "error_code",
    ),
    "ck_workflow_node_effect_attempts_error_code": (
        "error_code",
        "provider_call_finalize_failed",
        "unexpected_provider_status",
    ),
}
REQUIRED_CHECK_PATTERNS = {
    "ck_workflow_node_effect_attempts_counters": (
        r"\beffect_sequence\s*>=\s*\(?\s*0\b",
        r"\bclaim_generation\s*>\s*\(?\s*0\b",
    ),
}
REQUIRED_UNIQUE_SHAPES = {
    "uq_workflow_node_effect_attempts_slot": (
        "organization_id",
        "execution_id",
        "node_invocation_id",
        "effect_sequence",
    )
}
REQUIRED_INDEX_SHAPES = {
    "ix_workflow_node_effect_attempts_contract": RequiredIndexShape(
        ("provider", "operation", "provider_contract_version")
    ),
    "ix_workflow_node_effect_attempts_hmac_readiness": RequiredIndexShape(
        ("provider", "operation", "provider_contract_version", "key_version"),
        "provider_replay_capability supported status prepared in_flight "
        "replay_decision retry_before_effect replay_same_key",
    ),
    "ix_workflow_node_effect_attempts_workflow_run_id": RequiredIndexShape(
        ("workflow_run_id",)
    ),
    "ix_workflow_node_effect_attempts_node_run_id": RequiredIndexShape(
        ("node_run_id",)
    ),
}
REQUIRED_COLUMNS = frozenset(REQUIRED_COLUMN_SHAPES)
REQUIRED_CHECKS = frozenset(REQUIRED_CHECK_SHAPES)
REQUIRED_UNIQUES = frozenset(REQUIRED_UNIQUE_SHAPES)
REQUIRED_INDEXES = frozenset(REQUIRED_INDEX_SHAPES)


class ExternalEffectMigrationNotReadyError(RuntimeError):
    pass


def require_external_effect_worker_ready(
    engine: Engine,
    *,
    keyring_versions: Collection[str] = (),
) -> None:
    try:
        schema_inspector = inspect(engine)
        script = _script_directory()
        migration = check_alembic_readiness_with_inspector(
            schema_inspector,
            code_heads=script.get_heads(),
            known_revisions=[revision.revision for revision in script.walk_revisions()],
        )
        if not migration.ready or not _required_schema_exists(schema_inspector):
            raise ExternalEffectMigrationNotReadyError(
                "external effect database migration is not ready"
            )
        external_effect_claim_ttl_seconds()
        _require_persisted_contracts(engine, keyring_versions=set(keyring_versions))
    except ExternalEffectMigrationNotReadyError:
        raise
    except Exception:
        raise ExternalEffectMigrationNotReadyError(
            "external effect worker readiness check failed"
        ) from None


def _required_schema_exists(schema_inspector) -> bool:
    try:
        if not schema_inspector.has_table(TABLE_NAME):
            return False
        columns = {
            column.get("name"): column
            for column in schema_inspector.get_columns(TABLE_NAME)
        }
        if not all(
            _column_matches(columns.get(name), shape)
            for name, shape in REQUIRED_COLUMN_SHAPES.items()
        ):
            return False
        checks = {
            constraint.get("name"): constraint
            for constraint in schema_inspector.get_check_constraints(TABLE_NAME)
        }
        if not all(
            _check_matches(
                checks.get(name, {}).get("sqltext"),
                fragments,
                REQUIRED_CHECK_PATTERNS.get(name, ()),
            )
            for name, fragments in REQUIRED_CHECK_SHAPES.items()
        ):
            return False
        uniques = {
            constraint.get("name"): tuple(constraint.get("column_names") or ())
            for constraint in schema_inspector.get_unique_constraints(TABLE_NAME)
        }
        if any(
            uniques.get(name) != columns
            for name, columns in REQUIRED_UNIQUE_SHAPES.items()
        ):
            return False
        indexes = {
            index.get("name"): index
            for index in schema_inspector.get_indexes(TABLE_NAME)
        }
        return all(
            _index_matches(indexes.get(name), shape)
            for name, shape in REQUIRED_INDEX_SHAPES.items()
        )
    except Exception:
        return False


def _normalize_sql(value: object) -> str:
    return " ".join(str(value or "").lower().replace('"', "").split())


def _contains_all(value: object, fragments: Collection[str]) -> bool:
    normalized = _normalize_sql(value)
    return bool(normalized) and all(
        _normalize_sql(fragment) in normalized for fragment in fragments
    )


def _check_matches(
    value: object,
    fragments: Collection[str],
    patterns: Collection[str],
) -> bool:
    normalized = _normalize_sql(value)
    return _contains_all(normalized, fragments) and all(
        re.search(pattern, normalized) is not None for pattern in patterns
    )


def _column_matches(column: object, shape: RequiredColumnShape) -> bool:
    if not isinstance(column, dict):
        return False
    actual_type = str(column.get("type") or "").upper()
    if shape.timezone is not None and (
        getattr(column.get("type"), "timezone", None) is not shape.timezone
    ):
        return False
    return (
        actual_type.startswith(shape.type_name)
        and column.get("nullable") is shape.nullable
    )


def _index_matches(index: object, shape: RequiredIndexShape) -> bool:
    if not isinstance(index, dict):
        return False
    if tuple(index.get("column_names") or ()) != shape.columns or bool(
        index.get("unique", False)
    ):
        return False
    if shape.predicate is None:
        return True
    dialect_options = index.get("dialect_options") or {}
    predicate = (
        dialect_options.get("postgresql_where")
        if isinstance(dialect_options, dict)
        else None
    )
    return _contains_all(predicate, shape.predicate.split())


def _require_persisted_contracts(
    engine: Engine,
    *,
    keyring_versions: set[str],
) -> None:
    contracts = provider_contract_registry()
    with engine.connect() as connection:
        persisted_contracts = connection.execute(
            text(
                "SELECT DISTINCT provider, operation, provider_contract_version "
                "FROM workflow_node_effect_attempts"
            )
        ).fetchall()
        required_keys = connection.execute(
            text(
                "SELECT DISTINCT key_version FROM workflow_node_effect_attempts "
                "WHERE provider_replay_capability = 'supported' "
                "AND (status IN ('prepared', 'in_flight') OR "
                "(status = 'terminal' AND replay_decision IN "
                "('retry_before_effect', 'replay_same_key')))"
            )
        ).fetchall()
    for provider, operation, version in persisted_contracts:
        try:
            contracts.get(str(provider), str(operation), str(version))
        except KeyError:
            raise ExternalEffectMigrationNotReadyError(
                "persisted external effect contract is unavailable"
            ) from None
    missing_keys = {
        str(row[0])
        for row in required_keys
        if row[0] is None or str(row[0]) not in keyring_versions
    }
    if missing_keys:
        raise ExternalEffectMigrationNotReadyError(
            "required external effect HMAC key is unavailable"
        )


def _script_directory() -> ScriptDirectory:
    config = Config(str(ROOT_DIR / "apps" / "shared" / "alembic.ini"))
    config.set_main_option(
        "script_location",
        str(ROOT_DIR / "apps" / "shared" / "alembic"),
    )
    return ScriptDirectory.from_config(config)
