from __future__ import annotations

import argparse
import re
from collections import defaultdict
from pathlib import Path


_ADR_FILENAME_PATTERN = re.compile(
    r"^ADR-(?P<number>\d{4})-[a-z0-9]+(?:-[a-z0-9]+)*\.md$"
)
_ADR_HEADING_PATTERN = re.compile(r"^# ADR-(?P<number>\d{4}):\s+\S.*$")
_INDEX_ROW_PATTERN = re.compile(
    r"^\| \[ADR-(?P<label_number>\d{4})\]"
    r"\((?P<target>ADR-(?P<target_number>\d{4})-[^)\s]+\.md)\) \|"
)


def _read_utf8(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def find_adr_registry_errors(decisions_dir: Path) -> list[str]:
    errors: list[str] = []
    adr_files = sorted(decisions_dir.glob("ADR-*.md"), key=lambda path: path.name)
    file_numbers: dict[str, list[str]] = defaultdict(list)
    valid_filenames: set[str] = set()

    for adr_file in adr_files:
        filename_match = _ADR_FILENAME_PATTERN.fullmatch(adr_file.name)
        if filename_match is None:
            errors.append(f"invalid ADR filename: {adr_file.name}")
            continue

        filename_number = filename_match.group("number")
        valid_filenames.add(adr_file.name)
        file_numbers[filename_number].append(adr_file.name)

        try:
            content = _read_utf8(adr_file)
        except (OSError, UnicodeError) as exc:
            errors.append(f"{adr_file.name}: cannot read as UTF-8 ({exc})")
            continue

        first_line = content.splitlines()[0] if content else ""
        heading_match = _ADR_HEADING_PATTERN.fullmatch(first_line)
        if heading_match is None:
            errors.append(
                f"{adr_file.name}: invalid H1; expected '# ADR-{filename_number}: <title>'"
            )
        elif heading_match.group("number") != filename_number:
            errors.append(
                f"{adr_file.name}: H1 uses ADR-{heading_match.group('number')}; "
                f"expected ADR-{filename_number}"
            )

    for number, filenames in sorted(file_numbers.items()):
        if len(filenames) > 1:
            errors.append(
                f"duplicate ADR number ADR-{number}: {', '.join(sorted(filenames))}"
            )

    readme_path = decisions_dir / "README.md"
    try:
        readme_lines = _read_utf8(readme_path).splitlines()
    except (OSError, UnicodeError) as exc:
        errors.append(f"README index cannot be read as UTF-8 ({exc})")
        return sorted(errors)

    index_numbers: dict[str, list[str]] = defaultdict(list)
    index_targets: dict[str, list[int]] = defaultdict(list)
    for line_number, line in enumerate(readme_lines, start=1):
        if not line.startswith("| [ADR-"):
            continue

        row_match = _INDEX_ROW_PATTERN.match(line)
        if row_match is None:
            errors.append(f"README.md:{line_number}: invalid ADR index row")
            continue

        label_number = row_match.group("label_number")
        target_number = row_match.group("target_number")
        target = row_match.group("target")
        index_numbers[label_number].append(target)
        index_targets[target].append(line_number)

        if label_number != target_number:
            errors.append(
                f"README index label ADR-{label_number} does not match target {target}"
            )

    for number, targets in sorted(index_numbers.items()):
        if len(targets) > 1:
            errors.append(
                f"README index has duplicate ADR number ADR-{number}: "
                f"{', '.join(sorted(targets))}"
            )

    for filename in sorted(valid_filenames):
        row_count = len(index_targets.get(filename, []))
        if row_count == 0:
            errors.append(f"{filename}: missing README index row")
        elif row_count > 1:
            errors.append(f"{filename}: duplicate README index rows")

    tracked_filenames = {adr_file.name for adr_file in adr_files}
    for target in sorted(index_targets):
        if target not in tracked_filenames:
            errors.append(f"README index target does not exist: {target}")

    return sorted(errors)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate ADR filenames, headings, numbers, and README index"
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    decisions_dir = args.repo_root.resolve() / "docs" / "decisions"
    errors = find_adr_registry_errors(decisions_dir)
    if errors:
        print("ADR registry validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("ADR registry validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
