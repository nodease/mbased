from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from apps.shared.db.models.agent_builder import AgentBuilderSession

ROOT = Path(__file__).resolve().parents[4]


def _script_directory() -> ScriptDirectory:
    config = Config(str(ROOT / "apps" / "shared" / "alembic.ini"))
    config.set_main_option(
        "script_location", str(ROOT / "apps" / "shared" / "alembic")
    )
    return ScriptDirectory.from_config(config)


def test_protocol_version_model_column_is_nullable():
    column = AgentBuilderSession.__table__.c.protocol_version

    assert column.nullable is True
    assert column.type.length == 32


def test_protocol_and_security_repair_migrations_remain_in_single_head_ancestry():
    scripts = _script_directory()
    heads = scripts.get_heads()

    assert len(heads) == 1
    ancestry = {
        revision.revision for revision in scripts.iterate_revisions(heads[0], "base")
    }
    assert "a30e1f2a43b" in ancestry
    repair_merge = scripts.get_revision("a30e1f2a43b")
    assert set(repair_merge.down_revision) == {"a29d0e1f2a43", "b8e5f4a3c2d2"}
    agent_builder_merge = scripts.get_revision("a29d0e1f2a43")
    assert set(agent_builder_merge.down_revision) == {
        "a28d9e0f1a32",
        "c7f8a9b0d123",
    }
    protocol = scripts.get_revision("a28d9e0f1a32")
    assert protocol.down_revision == "0f4a5b6c7d89"
    assert "agent_builder_session_protocol_version" in protocol.path
