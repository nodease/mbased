from __future__ import annotations

from pathlib import Path

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.dialects.postgresql import BYTEA, JSONB

from apps.memory.adapters.persistence.readiness import (
    REQUIRED_MEMORY_SCHEMA,
    check_memory_schema_readiness_with_inspector,
)
from apps.shared.db import models as shared_models
from apps.shared.db.models.conversation_memory import (
    ConversationAccessGrantRecord,
    ConversationIdempotencyRecord,
    ConversationMemoryEntryRecord,
    ConversationMemorySummaryRecord,
    ConversationPurgeJobRecord,
    ConversationSecretReplayRecord,
    ConversationSessionRecord,
    ConversationTurnRecord,
    MemoryContextLeaseRecord,
    MemoryContextPlanRecord,
    MemoryContextProviderAttemptRecord,
    MemoryDataDependencyRecord,
    MemoryEntryDependencyRecord,
    MemorySummaryDependencyRecord,
    MemorySummaryGenerationJobRecord,
    MemoryTurnDispatchJobRecord,
)
from apps.shared.db.models.workflow_conversation_execution import (
    ConversationWorkflowExecutionAdmissionRecord,
)

ROOT = Path(__file__).resolve().parents[4]

MODELS = {
    ConversationSessionRecord: "conversation_sessions",
    ConversationAccessGrantRecord: "conversation_access_grants",
    ConversationTurnRecord: "conversation_turns",
    ConversationMemoryEntryRecord: "conversation_memory_entries",
    ConversationMemorySummaryRecord: "conversation_memory_summaries",
    MemoryDataDependencyRecord: "memory_data_dependencies",
    MemoryEntryDependencyRecord: "memory_entry_dependencies",
    MemorySummaryDependencyRecord: "memory_summary_dependencies",
    MemoryTurnDispatchJobRecord: "memory_turn_dispatch_jobs",
    MemorySummaryGenerationJobRecord: "memory_summary_generation_jobs",
    MemoryContextPlanRecord: "memory_context_plans",
    MemoryContextLeaseRecord: "memory_context_leases",
    MemoryContextProviderAttemptRecord: "memory_context_provider_attempts",
    ConversationPurgeJobRecord: "conversation_purge_jobs",
    ConversationIdempotencyRecord: "conversation_idempotency_records",
    ConversationSecretReplayRecord: "conversation_secret_replays",
}


def _constraint_names(model, constraint_type) -> set[str]:
    return {
        constraint.name
        for constraint in model.__table__.constraints
        if isinstance(constraint, constraint_type) and constraint.name
    }


def _composite_foreign_keys(model) -> set[tuple[str, ...]]:
    return {
        tuple(element.parent.name for element in constraint.elements)
        for constraint in model.__table__.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }


def _foreign_key(model, name: str) -> ForeignKeyConstraint:
    return next(
        constraint
        for constraint in model.__table__.constraints
        if isinstance(constraint, ForeignKeyConstraint) and constraint.name == name
    )


def _check_constraint(model, name: str) -> CheckConstraint:
    return next(
        constraint
        for constraint in model.__table__.constraints
        if isinstance(constraint, CheckConstraint) and constraint.name == name
    )


def test_all_memory_records_are_registered_without_import_side_effects():
    for model, table_name in MODELS.items():
        assert model.__tablename__ == table_name
        assert getattr(shared_models, model.__name__) is model


def test_session_schema_pins_scope_versions_and_separates_revisions():
    columns = ConversationSessionRecord.__table__.c

    assert {
        "organization_id",
        "app_id",
        "workflow_id",
        "deployment_id",
        "deployment_version",
        "deployment_snapshot_hash",
        "mapping_version",
        "memory_policy_version",
        "memory_contract_version",
        "storage_generation",
        "audience_kind",
        "subject_type",
        "subject_id",
        "lifecycle",
        "lifecycle_revision",
        "content_revision",
        "active_turn_id",
        "next_turn_sequence",
        "idle_expires_at",
        "absolute_expires_at",
    } <= set(columns.keys())
    assert {
        "ck_conv_sessions_binding",
        "ck_conv_sessions_audience_subject",
        "ck_conv_sessions_revisions",
        "ck_conv_sessions_lifecycle_fields",
        "ck_conv_sessions_expiry_order",
    } <= _constraint_names(ConversationSessionRecord, CheckConstraint)
    assert "uq_conv_sessions_id_org" in _constraint_names(
        ConversationSessionRecord,
        UniqueConstraint,
    )
    assert "uq_conv_sessions_grant_binding" in _constraint_names(
        ConversationSessionRecord,
        UniqueConstraint,
    )


def test_access_grant_scope_fk_pins_session_deployment_version_and_audience():
    binding = _foreign_key(
        ConversationAccessGrantRecord,
        "fk_conv_grants_session_binding",
    )
    assert tuple(element.parent.name for element in binding.elements) == (
        "session_id",
        "organization_id",
        "deployment_id",
        "deployment_version",
        "audience_kind",
    )
    assert tuple(element.target_fullname for element in binding.elements) == (
        "conversation_sessions.id",
        "conversation_sessions.organization_id",
        "conversation_sessions.deployment_id",
        "conversation_sessions.deployment_version",
        "conversation_sessions.audience_kind",
    )


def test_turn_schema_enforces_tenant_sequence_request_and_single_active_turn():
    table = ConversationTurnRecord.__table__

    assert {
        "uq_conv_turns_id_session_org",
        "uq_conv_turns_session_sequence",
        "uq_conv_turns_session_request",
    } <= _constraint_names(ConversationTurnRecord, UniqueConstraint)
    assert ("session_id", "organization_id") in _composite_foreign_keys(
        ConversationTurnRecord
    )
    active = {index.name: index for index in table.indexes}["uq_conv_turns_one_active"]
    assert active.unique is True
    assert "pending_dispatch" in str(active.dialect_options["postgresql"]["where"])
    assert "running" in str(active.dialect_options["postgresql"]["where"])


def test_session_active_turn_and_purge_reference_preserve_tenant_scope():
    active_turn_scope = _foreign_key(
        ConversationSessionRecord,
        "fk_conv_sessions_active_turn_scope",
    )
    assert tuple(element.parent.name for element in active_turn_scope.elements) == (
        "active_turn_id",
        "id",
        "organization_id",
    )
    assert tuple(element.target_fullname for element in active_turn_scope.elements) == (
        "conversation_turns.id",
        "conversation_turns.session_id",
        "conversation_turns.organization_id",
    )
    assert active_turn_scope.deferrable is True
    assert active_turn_scope.initially == "DEFERRED"

    purge_scope = _foreign_key(
        ConversationPurgeJobRecord,
        "fk_conv_purge_session_org",
    )
    assert tuple(element.parent.name for element in purge_scope.elements) == (
        "session_id",
        "organization_id",
    )
    assert tuple(element.target_fullname for element in purge_scope.elements) == (
        "conversation_sessions.id",
        "conversation_sessions.organization_id",
    )
    assert purge_scope.deferrable is True
    assert purge_scope.initially == "DEFERRED"


def test_turn_and_dispatch_checks_backstop_terminal_and_fencing_invariants():
    turn_check = str(
        _check_constraint(
            ConversationTurnRecord,
            "ck_conv_turns_status_fields",
        ).sqltext
    )
    assert "assistant_entry_id IS NULL" in turn_check
    assert "status = 'running'" in turn_check
    assert "started_at IS NOT NULL" in turn_check

    dispatch_counter_check = str(
        _check_constraint(
            MemoryTurnDispatchJobRecord,
            "ck_mem_dispatch_counters",
        ).sqltext
    )
    assert "claim_generation = attempt_count" in dispatch_counter_check
    assert "attempt_count <= max_attempts" in dispatch_counter_check

    dispatch_state_check = str(
        _check_constraint(
            MemoryTurnDispatchJobRecord,
            "ck_mem_dispatch_state_fields",
        ).sqltext
    )
    assert "status = 'published'" in dispatch_state_check
    assert "broker_message_id IS NOT NULL" in dispatch_state_check
    assert "status = 'terminal'" in dispatch_state_check
    assert "terminal_at IS NOT NULL" in dispatch_state_check


def test_entry_and_summary_store_only_protected_content_envelopes():
    for model in (ConversationMemoryEntryRecord, ConversationMemorySummaryRecord):
        columns = model.__table__.c
        forbidden = {
            "content",
            "display_content",
            "model_content",
            "raw_content",
            "prompt",
            "raw_prompt",
        }
        assert forbidden.isdisjoint(columns.keys())
        assert isinstance(columns.display_ciphertext.type, BYTEA)
        assert isinstance(columns.model_ciphertext.type, BYTEA)
        for projection in ("display", "model"):
            assert columns[f"{projection}_key_version"].nullable is True
            assert columns[f"{projection}_format_version"].nullable is True
            assert columns[f"{projection}_content_digest"].nullable is True
            assert columns[f"{projection}_plaintext_byte_length"].nullable is True


def test_access_grant_and_purge_receipt_never_define_raw_secret_columns():
    for model in (ConversationAccessGrantRecord, ConversationPurgeJobRecord):
        column_names = set(model.__table__.c.keys())
        assert {
            "raw_token",
            "token",
            "raw_receipt",
            "receipt",
            "secret",
            "ciphertext",
        }.isdisjoint(column_names)
    assert {
        "verifier_hash",
        "verifier_key_version",
    } <= set(ConversationAccessGrantRecord.__table__.c.keys())
    assert {
        "receipt_verifier_hash",
        "receipt_verifier_key_version",
        "app_id",
        "deployment_id",
        "deployment_version",
        "audience_kind",
    } <= set(ConversationPurgeJobRecord.__table__.c.keys())


def test_secret_replay_is_encrypted_and_bound_to_one_idempotency_result():
    columns = ConversationSecretReplayRecord.__table__.c
    assert {
        "raw_token",
        "token",
        "raw_receipt",
        "receipt",
        "secret",
        "value",
    }.isdisjoint(columns.keys())
    assert isinstance(columns.ciphertext.type, BYTEA)
    assert {
        "idempotency_record_id",
        "key_version",
        "associated_data_digest",
        "expires_at",
    } <= set(columns.keys())
    assert "uq_conv_secret_replay_idempotency" in _constraint_names(
        ConversationSecretReplayRecord,
        UniqueConstraint,
    )
    assert ("idempotency_record_id", "organization_id") in _composite_foreign_keys(
        ConversationSecretReplayRecord
    )


def test_idempotency_record_stores_only_typed_safe_response_snapshot_fields():
    columns = ConversationIdempotencyRecord.__table__.c
    assert {
        "result_lifecycle",
        "result_lifecycle_revision",
        "result_memory_contract_version",
        "result_expires_at",
        "result_previous_lifecycle",
        "result_previous_lifecycle_revision",
    } <= set(columns.keys())
    assert {
        "access_token",
        "purge_receipt",
        "response_payload",
        "raw_response",
    }.isdisjoint(columns.keys())

    assert {
        "authorization_app_id",
        "authorization_verifier_key_version",
        "authorization_verifier_hash",
    } <= set(columns.keys())
    assert "ck_conv_idempotency_authorization_scope" in _constraint_names(
        ConversationIdempotencyRecord,
        CheckConstraint,
    )
    assert "ck_conv_purge_scope_snapshot" in _constraint_names(
        ConversationPurgeJobRecord,
        CheckConstraint,
    )


def test_entry_and_summary_dependencies_preserve_composite_tenant_scope():
    assert {
        ("entry_id", "session_id", "organization_id"),
        ("dependency_id", "organization_id"),
    } <= _composite_foreign_keys(MemoryEntryDependencyRecord)
    assert {
        ("summary_id", "session_id", "organization_id"),
        ("dependency_id", "organization_id"),
    } <= _composite_foreign_keys(MemorySummaryDependencyRecord)


def test_start_turn_child_records_defer_turn_fk_until_uow_commit():
    for model, name in (
        (ConversationMemoryEntryRecord, "fk_mem_entries_turn_session_org"),
        (MemoryTurnDispatchJobRecord, "fk_mem_dispatch_turn_session_org"),
    ):
        constraint = _foreign_key(model, name)
        assert constraint.deferrable is True
        assert constraint.initially == "DEFERRED"


def test_operational_records_are_content_free_and_use_fencing_versions():
    operational = (
        MemoryTurnDispatchJobRecord,
        MemorySummaryGenerationJobRecord,
        MemoryContextLeaseRecord,
        MemoryContextProviderAttemptRecord,
        ConversationPurgeJobRecord,
        ConversationIdempotencyRecord,
        ConversationSecretReplayRecord,
    )
    for model in operational:
        columns = model.__table__.c
        assert "content" not in columns
        assert "raw_content" not in columns
        assert "raw_token" not in columns

    assert "claim_generation" in MemoryTurnDispatchJobRecord.__table__.c
    assert "lease_generation" in MemorySummaryGenerationJobRecord.__table__.c
    assert "claim_generation" in MemoryContextLeaseRecord.__table__.c
    assert "version" in MemoryContextProviderAttemptRecord.__table__.c
    assert "claim_generation" in ConversationPurgeJobRecord.__table__.c


def test_context_plan_contains_references_and_digest_but_no_materialized_text():
    columns = MemoryContextPlanRecord.__table__.c

    assert isinstance(columns.ordered_references.type, JSONB)
    assert {
        "content_digest",
        "authorization_revision_set_digest",
        "policy_version",
        "expires_at",
    } <= set(columns.keys())
    assert {
        "raw_context",
        "materialized_context",
        "prompt",
    }.isdisjoint(columns.keys())


def test_memory_migration_is_additive_reversible_and_descends_from_current_head():
    migration = (
        ROOT
        / "apps"
        / "shared"
        / "alembic"
        / "versions"
        / "ab1c2d3e4f50_add_conversation_memory_foundation.py"
    )
    source = migration.read_text(encoding="utf-8")

    assert 'revision: str = "ab1c2d3e4f50"' in source
    assert 'down_revision: str | Sequence[str] | None = "aa0b1c2d3e4f"' in source
    assert 'name="uq_conv_sessions_grant_binding"' in source
    assert 'name="fk_conv_grants_session_binding"' in source
    for table_name in MODELS.values():
        if table_name == "conversation_secret_replays":
            continue
        assert f'"{table_name}"' in source
        assert f'op.drop_table("{table_name}")' in source
    assert "workflow_runs" not in source
    assert "workflow_node_runs" not in source
    assert "op.drop_column" not in source


def test_public_capability_replay_migration_contract():
    migration = (
        ROOT
        / "apps"
        / "shared"
        / "alembic"
        / "versions"
        / "ac1d2e3f4a50_add_public_conversation_capability_replay.py"
    )
    source = migration.read_text(encoding="utf-8")

    assert '"conversation_secret_replays"' in source
    assert "uq_conv_idempotency_id_org" in source
    assert "ALTER TYPE audit_actor_type ADD VALUE IF NOT EXISTS 'public'" in source
    assert "audit_event_outbox" in source
    assert "payload ->> 'actor_type' = 'public'" in source
    assert source.index("audit_event_outbox") < source.index(
        'op.drop_table("conversation_secret_replays")'
    )
    assert "raw_token" not in source


def test_public_purge_scope_snapshot_migration_extends_the_replay_revision():
    migration = (
        ROOT
        / "apps"
        / "shared"
        / "alembic"
        / "versions"
        / "ad2e3f4a5b61_add_public_purge_scope_snapshot.py"
    )
    source = migration.read_text(encoding="utf-8")

    assert 'revision: str = "ad2e3f4a5b61"' in source
    assert 'down_revision: str | Sequence[str] | None = "ac1d2e3f4a50"' in source
    for column in ("deployment_id", "deployment_version", "audience_kind"):
        assert f'sa.Column("{column}"' in source
        assert f'op.drop_column("conversation_purge_jobs", "{column}")' in source
    assert "ck_conv_purge_scope_snapshot" in source


def test_public_idempotency_result_snapshot_migration_is_additive_and_reversible():
    migration = (
        ROOT
        / "apps"
        / "shared"
        / "alembic"
        / "versions"
        / "ae3f4a5b6c72_add_public_idempotency_result_snapshot.py"
    )
    source = migration.read_text(encoding="utf-8")

    assert 'revision: str = "ae3f4a5b6c72"' in source
    assert 'down_revision: str | Sequence[str] | None = "ad2e3f4a5b61"' in source
    for column in (
        "result_lifecycle",
        "result_lifecycle_revision",
        "result_memory_contract_version",
        "result_expires_at",
        "result_previous_lifecycle",
        "result_previous_lifecycle_revision",
    ):
        assert f'sa.Column("{column}"' in source
        assert (
            f'op.drop_column("conversation_idempotency_records", "{column}")' in source
        )
    assert "access_token" not in source
    assert "purge_receipt" not in source


def test_public_replay_authorization_scope_migration_is_additive_and_reversible():
    migrations = list(
        (ROOT / "apps" / "shared" / "alembic" / "versions").glob(
            "*_add_public_replay_authorization_scope.py"
        )
    )

    assert len(migrations) == 1
    source = migrations[0].read_text(encoding="utf-8")
    compact_source = "".join(source.split())
    for table, columns in {
        "conversation_purge_jobs": ("app_id",),
        "conversation_idempotency_records": (
            "authorization_app_id",
            "authorization_verifier_key_version",
            "authorization_verifier_hash",
        ),
    }.items():
        for column in columns:
            assert f'sa.Column("{column}"' in compact_source
            assert f'op.drop_column("{table}","{column}"' in compact_source
    assert "ck_conv_purge_scope_snapshot" in source
    assert "ck_conv_idempotency_authorization_scope" in source
    assert "ix_conv_idempotency_authorized_replay" in source
    assert "down_revision ==" not in source
    assert "AS grant" not in source
    assert "AS access_grant" in source


class _Inspector:
    def __init__(self, schema: dict[str, set[str]]) -> None:
        self.schema = schema

    def has_table(self, name: str) -> bool:
        return name in self.schema

    def get_columns(self, name: str):
        return [{"name": column} for column in self.schema[name]]


def test_schema_readiness_requires_every_foundation_table_and_column():
    ready = check_memory_schema_readiness_with_inspector(
        _Inspector(
            {name: set(columns) for name, columns in REQUIRED_MEMORY_SCHEMA.items()}
        )
    )
    assert ready.ready is True

    missing = {
        name: set(columns)
        for name, columns in REQUIRED_MEMORY_SCHEMA.items()
        if name != "memory_context_leases"
    }
    result = check_memory_schema_readiness_with_inspector(_Inspector(missing))

    assert result.ready is False
    assert result.missing_tables == ["memory_context_leases"]
    assert result.reason is None


def test_schema_readiness_requires_public_idempotency_result_snapshot_columns():
    snapshot_columns = {
        "result_lifecycle",
        "result_lifecycle_revision",
        "result_memory_contract_version",
        "result_expires_at",
        "result_previous_lifecycle",
        "result_previous_lifecycle_revision",
    }

    assert (
        snapshot_columns <= REQUIRED_MEMORY_SCHEMA["conversation_idempotency_records"]
    )
    assert snapshot_columns.isdisjoint(REQUIRED_MEMORY_SCHEMA["conversation_turns"])


def test_schema_readiness_requires_runtime_admission_fence():
    assert {
        "id",
        "organization_id",
        "dispatch_id",
        "session_id",
        "turn_id",
        "execution_id",
        "state",
        "version",
        "lease_generation",
        "lease_deadline",
    } <= REQUIRED_MEMORY_SCHEMA["conversation_workflow_execution_admissions"]


def test_schema_readiness_requires_stable_public_replay_scope_columns():
    assert "app_id" in REQUIRED_MEMORY_SCHEMA["conversation_purge_jobs"]
    assert {
        "authorization_app_id",
        "authorization_verifier_key_version",
        "authorization_verifier_hash",
    } <= REQUIRED_MEMORY_SCHEMA["conversation_idempotency_records"]

    schema = {name: set(columns) for name, columns in REQUIRED_MEMORY_SCHEMA.items()}
    schema["conversation_purge_jobs"].remove("app_id")
    result = check_memory_schema_readiness_with_inspector(_Inspector(schema))

    assert result.ready is False
    assert result.missing_columns == {"conversation_purge_jobs": ["app_id"]}


def test_workflow_admission_retention_is_bounded_without_blocking_deployment_delete():
    table = ConversationWorkflowExecutionAdmissionRecord.__table__
    state_fields = str(
        _check_constraint(
            ConversationWorkflowExecutionAdmissionRecord,
            "ck_conv_workflow_admission_state_fields",
        ).sqltext
    )

    assert "retention_expires_at" in table.c
    assert table.c.deployment_id.foreign_keys == set()
    assert "retention_expires_at > terminal_at" in state_fields
    assert "retention_expires_at IS NULL" in state_fields
    assert "ix_conv_workflow_admission_retention" in {
        index.name for index in table.indexes
    }
    assert "retention_expires_at" in REQUIRED_MEMORY_SCHEMA[
        "conversation_workflow_execution_admissions"
    ]
