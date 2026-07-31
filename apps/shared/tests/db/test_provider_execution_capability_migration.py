from apps.shared.alembic.versions import (
    ad1e2f3a4b5c_add_provider_execution_capability as migration,
)


def test_provider_execution_capability_migration_follows_rebased_dev_head():
    assert migration.revision == "ad1e2f3a4b5c"
    assert migration.down_revision == "b05c6d7e8f94"
