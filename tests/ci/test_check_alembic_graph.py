from pathlib import Path

import pytest

from scripts.ci.check_alembic_graph import load_revisions, validate_revision_graph


def _revision(path: Path, revision: str, down_revision: str) -> None:
    path.write_text(
        "\n".join(
            [
                f'revision: str = "{revision}"',
                f"down_revision = {down_revision}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_accepts_single_head_with_merge_revision(tmp_path: Path):
    _revision(tmp_path / "001_root.py", "root", "None")
    _revision(tmp_path / "002_left.py", "left", "\"root\"")
    _revision(tmp_path / "003_right.py", "right", "\"root\"")
    _revision(tmp_path / "004_merge.py", "merge", '("left", "right")')

    head = validate_revision_graph(load_revisions(tmp_path))

    assert head == "merge"


def test_rejects_multiple_heads(tmp_path: Path):
    _revision(tmp_path / "001_root.py", "root", "None")
    _revision(tmp_path / "002_left.py", "left", "\"root\"")
    _revision(tmp_path / "003_right.py", "right", "\"root\"")

    with pytest.raises(ValueError, match="expected one Alembic head"):
        validate_revision_graph(load_revisions(tmp_path))


def test_rejects_missing_parent(tmp_path: Path):
    _revision(tmp_path / "001_child.py", "child", "\"missing\"")

    with pytest.raises(ValueError, match="missing parent"):
        validate_revision_graph(load_revisions(tmp_path))


def test_rejects_duplicate_revision(tmp_path: Path):
    _revision(tmp_path / "001_first.py", "duplicate", "None")
    _revision(tmp_path / "002_second.py", "duplicate", "None")

    with pytest.raises(ValueError, match="duplicate Alembic revision"):
        validate_revision_graph(load_revisions(tmp_path))


def test_rejects_cycle(tmp_path: Path):
    _revision(tmp_path / "001_a.py", "a", "\"b\"")
    _revision(tmp_path / "002_b.py", "b", "\"a\"")

    with pytest.raises(ValueError, match="cycle"):
        validate_revision_graph(load_revisions(tmp_path))
