from pathlib import Path

from apps.shared.alembic.versions import (
    b17c8d9e0f12_add_query_embedding_provider_capability as migration,
)


def test_query_embedding_capability_migration_extends_current_provider_head():
    assert migration.revision == "b17c8d9e0f12"
    assert migration.down_revision == "b16c7d8e9f01"


def test_query_embedding_downgrade_guard_is_explicit():
    assert migration.QUERY_EMBEDDING_DOWNGRADE_GUARD == (
        "query_embedding policy, capability, or usage rows must be removed "
        "before downgrade"
    )


def test_query_embedding_downgrade_locks_all_guarded_tables_before_scan():
    statements = []

    class _Bind:
        def execute(self, statement):
            statements.append(str(statement))

    migration._lock_query_embedding_tables(_Bind())

    assert statements == [
        "LOCK TABLE llm_deployment_credential_policies, "
        "provider_execution_capabilities, provider_usage_operations "
        "IN ACCESS EXCLUSIVE MODE"
    ]


def test_query_embedding_postgres_migration_is_connected_to_ci():
    workflow = (
        Path(__file__).resolve().parents[4]
        / ".github"
        / "workflows"
        / "test-schedule-dispatch-postgres.yml"
    ).read_text(encoding="utf-8")

    assert (
        "apps/shared/tests/db/test_query_embedding_capability_migration_postgres.py"
        in workflow
    )
