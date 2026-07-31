from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[4]


def _script_directory() -> ScriptDirectory:
    config = Config(str(ROOT / "apps" / "shared" / "alembic.ini"))
    config.set_main_option(
        "script_location",
        str(ROOT / "apps" / "shared" / "alembic"),
    )
    return ScriptDirectory.from_config(config)


def test_model_routing_learner_migration_remains_in_the_single_head_ancestry():
    script = _script_directory()
    revision = script.get_revision("c06d7e8f9a15")
    heads = script.get_heads()

    assert revision.down_revision == "ae2f3a4b5c6d"
    assert len(heads) == 1
    ancestry = {
        item.revision for item in script.iterate_revisions(heads[0], "base")
    }
    assert revision.revision in ancestry


def test_model_routing_learner_migration_purges_incompatible_learning_state():
    revision = _script_directory().get_revision("c06d7e8f9a15")
    source = Path(revision.path).read_text(encoding="utf-8")

    assert 'op.execute("DELETE FROM llm_node_model_routing_learning_labels")' in source
    assert "SET active_policy = active_policy - 'learning'" in source
    assert (
        'sa.Column("learner_id", postgresql.UUID(as_uuid=True), nullable=False)'
        in source
    )
    assert 'new_column_name="source_policy_id"' in source
    assert '"source_policy_id",\n        new_column_name="policy_id"' in source
    assert 'ondelete="SET NULL"' in source
