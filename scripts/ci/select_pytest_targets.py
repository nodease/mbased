from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable

from scripts.ci.changed_scope import changed_paths_from_git, normalize_repo_path


_EXCLUDED_TEST_PARTS = {"e2e", "evaluation", "load", "manual"}
_POSTGRES_ONLY_TESTS = {
    "apps/gateway/tests/adapters/db/test_knowledge_document_ingestion_repository_postgres.py",
    "apps/gateway/tests/integration/test_agent_builder_intent_usage_postgres.py",
    "apps/gateway/tests/integration/test_agent_builder_model_selection_db.py",
    "apps/gateway/tests/integration/test_agent_builder_primary_workflow_db.py",
    "apps/gateway/tests/integration/test_agent_builder_workflow_cas.py",
    "apps/memory/tests/adapters/test_disposable_postgres.py",
}
_EXPLICIT_CONTRACT_TEST_TARGETS = {
    "gateway": {
        "apps/shared/schemas/organization_membership.py": (
            "apps/gateway/tests/api/test_organizations_api.py",
            "apps/gateway/tests/services/test_organization_member_service.py",
        ),
    },
}
_GENERIC_TOKENS = {
    "adapter",
    "api",
    "base",
    "case",
    "cli",
    "config",
    "endpoint",
    "factory",
    "helper",
    "implementation",
    "main",
    "manager",
    "model",
    "policy",
    "repository",
    "schema",
    "service",
    "task",
    "tasks",
    "test",
    "use",
    "util",
    "utils",
}
_TOKEN_ALIASES = {
    "candidates": "candidate",
    "credentials": "credential",
    "memberships": "member",
    "membership": "member",
    "permissions": "permission",
    "policies": "policy",
    "revisions": "revision",
}


@dataclass(frozen=True)
class ComponentConfig:
    test_root: str
    source_prefix: str | None
    smoke_targets: tuple[str, ...]
    layer_map: dict[str, str]


_COMPONENTS = {
    "gateway": ComponentConfig(
        test_root="apps/gateway/tests",
        source_prefix="apps/gateway/",
        smoke_targets=("apps/gateway/tests/architecture",),
        layer_map={
            "adapters": "adapters",
            "api": "api",
            "application": "application",
            "middleware": "middleware",
            "services": "services",
            "utils": "utils",
        },
    ),
    "workflow_engine": ComponentConfig(
        test_root="apps/workflow_engine/tests",
        source_prefix="apps/workflow_engine/",
        smoke_targets=("apps/workflow_engine/tests/domain",),
        layer_map={
            "adapters": "adapters",
            "application": "application",
            "composition": "composition",
            "domain": "domain",
            "nodes": "nodes",
            "services": "services",
        },
    ),
    "shared": ComponentConfig(
        test_root="apps/shared/tests",
        source_prefix="apps/shared/",
        smoke_targets=("apps/shared/tests/domain",),
        layer_map={
            "audit": "audit",
            "db": "db",
            "deployment": "deployment",
            "domain": "domain",
            "helpers": "helpers",
            "services": "services",
        },
    ),
    "log_system": ComponentConfig(
        test_root="apps/log_system/tests",
        source_prefix="apps/log_system/",
        smoke_targets=("apps/log_system/tests",),
        layer_map={},
    ),
    "sandbox": ComponentConfig(
        test_root="apps/sandbox/tests",
        source_prefix="apps/sandbox/",
        smoke_targets=("apps/sandbox/tests",),
        layer_map={},
    ),
    "memory": ComponentConfig(
        test_root="apps/memory/tests",
        source_prefix="apps/memory/",
        smoke_targets=("apps/memory/tests/architecture",),
        layer_map={
            "adapters": "adapters",
            "application": "application",
            "domain": "domain",
        },
    ),
    "root": ComponentConfig(
        test_root="tests",
        source_prefix=None,
        smoke_targets=("tests/ci", "tests/test_permission_schema.py"),
        layer_map={"db": "db", "services": "services", "ci": "ci"},
    ),
}


def _canonical_token(token: str) -> str:
    if token in _TOKEN_ALIASES:
        return _TOKEN_ALIASES[token]
    if token.endswith("ies") and len(token) > 4:
        return f"{token[:-3]}y"
    if token.endswith("s") and len(token) > 4:
        return token[:-1]
    return token


def _feature_tokens(path: str) -> frozenset[str]:
    stem = PurePosixPath(path).stem.removeprefix("test_")
    tokens = {
        _canonical_token(token)
        for token in re.split(r"[^a-zA-Z0-9]+", stem.lower())
        if token
    }
    return frozenset(token for token in tokens if token not in _GENERIC_TOKENS)


def _is_allowed_test(path: Path, repo_root: Path, config: ComponentConfig) -> bool:
    try:
        relative = path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return False

    if not relative.startswith(f"{config.test_root}/") and relative != config.test_root:
        return False
    if relative in _POSTGRES_ONLY_TESTS:
        return False
    return not any(
        part in _EXCLUDED_TEST_PARTS for part in PurePosixPath(relative).parts
    )


def _test_files(repo_root: Path, config: ComponentConfig) -> list[Path]:
    test_root = repo_root / config.test_root
    if not test_root.is_dir():
        return []
    return sorted(
        path
        for path in test_root.rglob("test_*.py")
        if _is_allowed_test(path, repo_root, config)
    )


def _features_match(source_path: str, test_path: str) -> bool:
    source_stem = PurePosixPath(source_path).stem.lower()
    test_stem = PurePosixPath(test_path).stem.lower().removeprefix("test_")
    if len(source_stem) >= 5 and (source_stem in test_stem or test_stem in source_stem):
        return True

    source_tokens = _feature_tokens(source_path)
    test_tokens = _feature_tokens(test_path)
    if not source_tokens or not test_tokens:
        return False
    if len(source_tokens) == 1:
        return source_tokens <= test_tokens

    overlap = len(source_tokens & test_tokens)
    required = max(2, len(source_tokens) - 1)
    return overlap >= required


def _changed_test_targets(
    changed_paths: Iterable[str], repo_root: Path, config: ComponentConfig
) -> set[str]:
    targets: set[str] = set()
    for raw_path in changed_paths:
        path = normalize_repo_path(raw_path)
        candidate = repo_root / path
        if (
            path.startswith(f"{config.test_root}/")
            and candidate.is_file()
            and candidate.name.startswith("test_")
            and candidate.suffix == ".py"
            and _is_allowed_test(candidate, repo_root, config)
        ):
            targets.add(path)
    return targets


def _explicit_contract_targets(
    component: str,
    changed_paths: Iterable[str],
    repo_root: Path,
    config: ComponentConfig,
) -> set[str]:
    mappings = _EXPLICIT_CONTRACT_TEST_TARGETS.get(component, {})
    changed_path_set = {
        normalize_repo_path(path) for path in changed_paths
    }
    targets: set[str] = set()
    for source, mapped_targets in mappings.items():
        if source not in changed_path_set and changed_path_set.isdisjoint(
            mapped_targets
        ):
            continue

        source_candidate = repo_root / source
        if not source_candidate.is_file():
            raise RuntimeError(
                f"explicit contract source is missing: {source}"
            )
        for target in mapped_targets:
            candidate = repo_root / target
            if not candidate.is_file():
                raise RuntimeError(
                    f"explicit contract test target is missing: {target}"
                )
            if not _is_allowed_test(candidate, repo_root, config):
                raise RuntimeError(
                    f"explicit contract test target is not allowed: {target}"
                )
            targets.add(target)
    return targets


def _matching_feature_targets(
    changed_paths: Iterable[str], test_files: Iterable[Path], repo_root: Path
) -> set[str]:
    source_paths = [
        normalize_repo_path(path)
        for path in changed_paths
        if path.endswith(".py")
        and "/tests/" not in path
        and not path.startswith("tests/")
    ]
    targets: set[str] = set()
    for test_file in test_files:
        relative_test = test_file.relative_to(repo_root).as_posix()
        if any(
            _features_match(source_path, relative_test) for source_path in source_paths
        ):
            targets.add(relative_test)
    return targets


def _layer_fallback_targets(
    component: str,
    changed_paths: Iterable[str],
    repo_root: Path,
    config: ComponentConfig,
) -> set[str]:
    targets: set[str] = set()
    if component == "root":
        for raw_path in changed_paths:
            path = PurePosixPath(normalize_repo_path(raw_path))
            if path.parts[:1] != ("tests",):
                continue
            for part in path.parts[1:]:
                if part in config.layer_map:
                    target = repo_root / config.test_root / config.layer_map[part]
                    if target.exists():
                        targets.add(target.relative_to(repo_root).as_posix())
                    break
        return targets

    assert config.source_prefix is not None
    for raw_path in changed_paths:
        path = normalize_repo_path(raw_path)
        if not path.startswith(config.source_prefix) or "/tests/" in path:
            continue
        parts = PurePosixPath(path).parts
        matched_layer = False
        for part in parts:
            if component == "shared" and part == "schemas":
                schema_tests = sorted(
                    (repo_root / config.test_root).glob("test_*schema*.py")
                )
                targets.update(
                    test_path.relative_to(repo_root).as_posix()
                    for test_path in schema_tests
                    if _is_allowed_test(test_path, repo_root, config)
                )
                matched_layer = True
                break
            if part in config.layer_map:
                target = repo_root / config.test_root / config.layer_map[part]
                if target.exists():
                    targets.add(target.relative_to(repo_root).as_posix())
                matched_layer = True
                break
        if not matched_layer and path.endswith(("pyproject.toml", "uv.lock")):
            targets.update(_existing_smoke_targets(repo_root, config))
    return targets


def _existing_smoke_targets(repo_root: Path, config: ComponentConfig) -> set[str]:
    return {target for target in config.smoke_targets if (repo_root / target).exists()}


def _collapse_nested_targets(targets: Iterable[str]) -> list[str]:
    ordered = sorted(
        set(targets), key=lambda value: (len(PurePosixPath(value).parts), value)
    )
    collapsed: list[str] = []
    for target in ordered:
        target_path = PurePosixPath(target)
        if any(
            parent == target_path or parent in target_path.parents
            for parent in map(PurePosixPath, collapsed)
        ):
            continue
        collapsed.append(target)
    return collapsed


def select_pytest_targets(
    component: str,
    changed_paths: Iterable[str],
    repo_root: Path,
    *,
    broad: bool = False,
) -> list[str]:
    if component not in _COMPONENTS:
        raise ValueError(f"unsupported component: {component}")

    config = _COMPONENTS[component]
    paths = [normalize_repo_path(path) for path in changed_paths]
    test_files = _test_files(repo_root, config)

    targets = _changed_test_targets(paths, repo_root, config)
    targets.update(
        _explicit_contract_targets(component, paths, repo_root, config)
    )
    targets.update(_matching_feature_targets(paths, test_files, repo_root))

    if broad:
        targets.update(_existing_smoke_targets(repo_root, config))
    elif not targets:
        targets.update(_layer_fallback_targets(component, paths, repo_root, config))

    if not targets:
        targets.update(_existing_smoke_targets(repo_root, config))
    if component in {"gateway", "memory"}:
        targets.update(_existing_smoke_targets(repo_root, config))

    valid_targets = {
        target
        for target in targets
        if (repo_root / target).exists()
        and _is_allowed_test(repo_root / target, repo_root, config)
    }
    if not valid_targets:
        raise RuntimeError(f"no safe pytest targets found for {component}")
    return _collapse_nested_targets(valid_targets)


def _parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized not in {"true", "false"}:
        raise argparse.ArgumentTypeError("expected true or false")
    return normalized == "true"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Select safe pytest targets for a PR")
    parser.add_argument("--component", choices=sorted(_COMPONENTS), required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--broad", type=_parse_bool, default=False)
    parser.add_argument("--run-with")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    repo_root = args.repo_root.resolve()
    changed_paths = changed_paths_from_git(args.base, args.head, repo_root)
    targets = select_pytest_targets(
        args.component,
        changed_paths,
        repo_root,
        broad=args.broad,
    )
    print(json.dumps({"component": args.component, "targets": targets}, sort_keys=True))

    if not args.run_with:
        return 0

    completed = subprocess.run(
        [args.run_with, "-m", "pytest", *targets, "-q"],
        cwd=repo_root,
        check=False,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
