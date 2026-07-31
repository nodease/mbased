from pathlib import Path

from scripts.ci.check_adr_registry import find_adr_registry_errors


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DECISIONS_DIR = REPOSITORY_ROOT / "docs" / "decisions"


def _write_adr(decisions_dir: Path, filename: str, heading_number: str) -> None:
    decisions_dir.mkdir(parents=True, exist_ok=True)
    (decisions_dir / filename).write_text(
        f"# ADR-{heading_number}: Test decision\n\nStatus: Accepted\n",
        encoding="utf-8",
    )


def _write_index(decisions_dir: Path, rows: list[str]) -> None:
    decisions_dir.mkdir(parents=True, exist_ok=True)
    (decisions_dir / "README.md").write_text(
        "# Decisions\n\n"
        "| ADR | Status | Topic | Current |\n"
        "| --- | --- | --- | --- |\n"
        + "".join(f"{row}\n" for row in rows),
        encoding="utf-8",
    )


def test_adr_registry_accepts_unique_files_with_matching_index(tmp_path):
    decisions_dir = tmp_path / "docs" / "decisions"
    _write_adr(decisions_dir, "ADR-0001-first-decision.md", "0001")
    _write_adr(decisions_dir, "ADR-0002-second-decision.md", "0002")
    _write_index(
        decisions_dir,
        [
            "| [ADR-0001](ADR-0001-first-decision.md) | Accepted | First | Current |",
            "| [ADR-0002](ADR-0002-second-decision.md) | Accepted | Second | Current |",
        ],
    )

    assert find_adr_registry_errors(decisions_dir) == []


def test_adr_registry_rejects_duplicate_file_and_index_numbers(tmp_path):
    decisions_dir = tmp_path / "docs" / "decisions"
    _write_adr(decisions_dir, "ADR-0001-first-decision.md", "0001")
    _write_adr(decisions_dir, "ADR-0001-second-decision.md", "0001")
    _write_index(
        decisions_dir,
        [
            "| [ADR-0001](ADR-0001-first-decision.md) | Accepted | First | Current |",
            "| [ADR-0001](ADR-0001-second-decision.md) | Accepted | Second | Current |",
        ],
    )

    errors = find_adr_registry_errors(decisions_dir)

    assert any("duplicate ADR number ADR-0001" in error for error in errors)
    assert any("README index has duplicate ADR number ADR-0001" in error for error in errors)


def test_adr_registry_rejects_bad_filename_and_mismatched_heading(tmp_path):
    decisions_dir = tmp_path / "docs" / "decisions"
    _write_adr(decisions_dir, "ADR-1-invalid-number.md", "0001")
    _write_adr(decisions_dir, "ADR-0002-heading-mismatch.md", "0003")
    _write_index(
        decisions_dir,
        [
            "| [ADR-0002](ADR-0002-heading-mismatch.md) | Accepted | Second | Current |",
        ],
    )

    errors = find_adr_registry_errors(decisions_dir)

    assert any("invalid ADR filename: ADR-1-invalid-number.md" == error for error in errors)
    assert any("H1 uses ADR-0003; expected ADR-0002" in error for error in errors)


def test_adr_registry_rejects_missing_duplicate_and_stale_index_targets(tmp_path):
    decisions_dir = tmp_path / "docs" / "decisions"
    _write_adr(decisions_dir, "ADR-0001-first-decision.md", "0001")
    _write_adr(decisions_dir, "ADR-0002-second-decision.md", "0002")
    _write_index(
        decisions_dir,
        [
            "| [ADR-0001](ADR-0001-first-decision.md) | Accepted | First | Current |",
            "| [ADR-0001](ADR-0001-first-decision.md) | Accepted | Duplicate | Current |",
            "| [ADR-0003](ADR-0003-missing-decision.md) | Accepted | Missing | Current |",
        ],
    )

    errors = find_adr_registry_errors(decisions_dir)

    assert any("ADR-0001-first-decision.md: duplicate README index rows" == error for error in errors)
    assert any("ADR-0002-second-decision.md: missing README index row" == error for error in errors)
    assert any("README index target does not exist: ADR-0003-missing-decision.md" == error for error in errors)


def test_adr_registry_rejects_index_label_target_number_mismatch(tmp_path):
    decisions_dir = tmp_path / "docs" / "decisions"
    _write_adr(decisions_dir, "ADR-0001-first-decision.md", "0001")
    _write_index(
        decisions_dir,
        [
            "| [ADR-0002](ADR-0001-first-decision.md) | Accepted | First | Current |",
        ],
    )

    errors = find_adr_registry_errors(decisions_dir)

    assert any(
        "README index label ADR-0002 does not match target ADR-0001-first-decision.md"
        == error
        for error in errors
    )


def test_current_repository_adr_registry_is_valid():
    assert find_adr_registry_errors(DECISIONS_DIR) == []
