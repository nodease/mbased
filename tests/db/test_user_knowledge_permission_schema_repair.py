from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def _script_directory() -> ScriptDirectory:
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "apps" / "shared" / "alembic.ini"))
    config.set_main_option(
        "script_location", str(root / "apps" / "shared" / "alembic")
    )
    return ScriptDirectory.from_config(config)


def test_user_knowledge_permission_schema_repair_follows_current_head():
    """이미 stamp된 로컬 DB도 KB 직접 권한 스키마를 보정할 수 있어야 한다."""
    script = _script_directory()
    revision = script.get_revision("fd0e1f2a3b4c")

    assert revision is not None
    assert revision.down_revision == "fc9a1b2c3d4e"

    source = Path(revision.path).read_text(encoding="utf-8")
    assert "uq_knowledge_bases_id_organization_id" in source
    assert "user_knowledge_permissions" in source


def test_runtime_migrations_remain_in_single_head_ancestry():
    """후속 migration이 추가돼도 기존 runtime revision은 단일 head 계보에 남아야 한다."""
    script = _script_directory()
    model_routing_revision = script.get_revision("fb8c9d0e1f23")
    external_effect_revision = script.get_revision("fe3f4a5b6c78")
    recommendation_revision = script.get_revision("fc9a1b2c3d4e")
    repair_revision = script.get_revision("fd0e1f2a3b4c")
    security_alert_episode_revision = script.get_revision("fe4a5b6c7d89")
    security_alert_outbox_revision = script.get_revision("c05d6e7f8a90")
    internal_chatbot_revision = script.get_revision("fc9d0e1f2a34")
    index_alignment_revision = script.get_revision("fd3e4f5a6b78")
    configuration_preflight_revision = script.get_revision("0f4a5b6c7d89")

    assert model_routing_revision.down_revision == "fa7b8c9d0e12"
    assert external_effect_revision.down_revision == "b39e0f1a2b43"
    assert recommendation_revision.down_revision == "fe3f4a5b6c78"
    assert repair_revision.down_revision == "fc9a1b2c3d4e"
    assert security_alert_episode_revision.down_revision == "fd0e1f2a3b4c"
    assert security_alert_outbox_revision.down_revision == "fe4a5b6c7d89"
    assert internal_chatbot_revision.down_revision == "c05d6e7f8a90"
    assert index_alignment_revision.down_revision == "fc9d0e1f2a34"
    assert configuration_preflight_revision.down_revision == "fd3e4f5a6b78"

    source = Path(index_alignment_revision.path).read_text(encoding="utf-8")
    assert "ix_team_knowledge_collection_permissions_org" in source
    assert "ix_user_knowledge_collection_permissions_org" in source
    assert "ix_team_knowledge_domain_permissions_effective" in source
    assert "ix_user_knowledge_domain_permissions_effective" in source
    assert source.count("if_exists=True") == 4

    heads = script.get_heads()
    assert len(heads) == 1
    ancestry = {
        revision.revision
        for revision in script.iterate_revisions(heads[0], "base")
    }
    assert "fd0e1f2a3b4c" in ancestry
    assert "c05d6e7f8a90" in ancestry
    assert "fc9d0e1f2a34" in ancestry
    assert "fd3e4f5a6b78" in ancestry
    assert "0f4a5b6c7d89" in ancestry
