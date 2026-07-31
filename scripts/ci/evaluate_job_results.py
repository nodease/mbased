from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Iterable


_VALID_RESULTS = {"success", "failure", "cancelled", "skipped"}


@dataclass(frozen=True)
class ConditionalResult:
    name: str
    selected: bool
    result: str


def evaluate_results(
    required: Iterable[tuple[str, str]],
    conditional: Iterable[ConditionalResult],
) -> list[str]:
    errors: list[str] = []
    for name, result in required:
        if result != "success":
            errors.append(f"required job {name} ended with {result or 'missing'}")

    for item in conditional:
        if item.result not in _VALID_RESULTS:
            errors.append(f"conditional job {item.name} has invalid result {item.result!r}")
            continue
        if item.selected and item.result != "success":
            errors.append(f"selected job {item.name} ended with {item.result}")
        if not item.selected and item.result not in {"skipped", "success"}:
            errors.append(f"unselected job {item.name} ended with {item.result}")
    return errors


def _parse_required(value: str) -> tuple[str, str]:
    name, separator, result = value.partition("=")
    if not separator or not name:
        raise argparse.ArgumentTypeError("required result must be NAME=RESULT")
    return name, result


def _parse_conditional(value: str) -> ConditionalResult:
    parts = value.split("=", maxsplit=2)
    if len(parts) != 3 or not parts[0]:
        raise argparse.ArgumentTypeError(
            "conditional result must be NAME=true|false=RESULT"
        )
    selected_raw = parts[1].lower()
    if selected_raw not in {"true", "false"}:
        raise argparse.ArgumentTypeError("selected value must be true or false")
    return ConditionalResult(parts[0], selected_raw == "true", parts[2])


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate required GitHub Actions jobs")
    parser.add_argument("--required", action="append", type=_parse_required, default=[])
    parser.add_argument(
        "--conditional", action="append", type=_parse_conditional, default=[]
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    errors = evaluate_results(args.required, args.conditional)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("All required CI jobs completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
