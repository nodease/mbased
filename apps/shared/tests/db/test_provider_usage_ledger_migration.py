from __future__ import annotations

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_provider_usage_migration_remains_in_the_single_head_lineage() -> None:
    config = Config("apps/shared/alembic.ini")
    config.set_main_option("script_location", "apps/shared/alembic")
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()

    assert len(heads) == 1
    lineage = {
        revision.revision
        for revision in script.iterate_revisions(heads[0], "base")
    }
    assert "b16c7d8e9f01" in lineage

    provider_usage_revision = script.get_revision("b16c7d8e9f01")
    assert provider_usage_revision is not None
    assert "provider_usage" in provider_usage_revision.module.__name__
