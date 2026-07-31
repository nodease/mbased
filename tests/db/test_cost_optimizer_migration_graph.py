import importlib.util
from pathlib import Path


def _load_revision_module(filename: str):
    root = Path(__file__).resolve().parents[2]
    revision_path = root / "apps" / "shared" / "alembic" / "versions" / filename
    spec = importlib.util.spec_from_file_location(
        filename.removesuffix(".py"), revision_path
    )
    module = importlib.util.module_from_spec(spec)
    assert spec is not None
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_cost_optimizer_candidate_version_fk_depends_on_llm_node_versions():
    migration = _load_revision_module(
        "f9a0b1c2d3e4_add_cost_optimizer_tables.py"
    )

    assert migration.depends_on == "d0e1f2a3b4c5"
