from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class Revision:
    revision: str
    parents: tuple[str, ...]
    path: Path


def _assignment_value(module: ast.Module, name: str) -> object:
    for node in module.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
                return ast.literal_eval(node.value)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name and node.value:
                return ast.literal_eval(node.value)
    raise ValueError(f"missing {name}")


def _normalize_parents(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (tuple, list)) and all(isinstance(item, str) for item in value):
        return tuple(value)
    raise ValueError(f"unsupported down_revision value: {value!r}")


def load_revisions(versions_dir: Path) -> list[Revision]:
    if not versions_dir.is_dir():
        raise ValueError(f"Alembic versions directory does not exist: {versions_dir}")

    revisions: list[Revision] = []
    for path in sorted(versions_dir.glob("*.py")):
        if path.name == "__init__.py":
            continue
        try:
            module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            revision_value = _assignment_value(module, "revision")
            if not isinstance(revision_value, str) or not revision_value:
                raise ValueError(f"invalid revision value: {revision_value!r}")
            parents = _normalize_parents(_assignment_value(module, "down_revision"))
        except (SyntaxError, ValueError) as exc:
            raise ValueError(f"cannot parse Alembic revision {path}: {exc}") from exc
        revisions.append(Revision(revision_value, parents, path))

    if not revisions:
        raise ValueError(f"no Alembic revisions found in {versions_dir}")
    return revisions


def validate_revision_graph(revisions: Iterable[Revision]) -> str:
    by_id: dict[str, Revision] = {}
    for item in revisions:
        if item.revision in by_id:
            raise ValueError(
                f"duplicate Alembic revision {item.revision}: "
                f"{by_id[item.revision].path} and {item.path}"
            )
        by_id[item.revision] = item

    for item in by_id.values():
        for parent in item.parents:
            if parent not in by_id:
                raise ValueError(
                    f"Alembic revision {item.revision} references missing parent {parent}"
                )

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(revision_id: str) -> None:
        if revision_id in visiting:
            raise ValueError(f"Alembic revision cycle detected at {revision_id}")
        if revision_id in visited:
            return
        visiting.add(revision_id)
        for parent in by_id[revision_id].parents:
            visit(parent)
        visiting.remove(revision_id)
        visited.add(revision_id)

    for revision_id in by_id:
        visit(revision_id)

    parent_ids = {parent for item in by_id.values() for parent in item.parents}
    heads = sorted(set(by_id) - parent_ids)
    if len(heads) != 1:
        raise ValueError(f"expected one Alembic head, found {len(heads)}: {heads}")
    return heads[0]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate the Alembic revision graph")
    parser.add_argument(
        "--versions-dir",
        type=Path,
        default=Path("apps/shared/alembic/versions"),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    revisions = load_revisions(args.versions_dir)
    head = validate_revision_graph(revisions)
    print(f"Alembic revision graph is valid with one head: {head}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
