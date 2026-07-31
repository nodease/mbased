from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Iterable

from scripts.ci.changed_scope import (
    is_github_composite_action_path,
    is_github_workflow_path,
    is_unsupported_deployment_path,
    normalize_repo_path,
)


_APPROVED_WORKFLOW_PATHS = frozenset(
    {
        ".github/workflows/pr-ci-control-guard.yml",
        ".github/workflows/pr-quality-gate.yml",
        ".github/workflows/publish-images.yml",
        ".github/workflows/test-agent-builder-postgres.yml",
        ".github/workflows/test-knowledge-runtime-postgres.yml",
        ".github/workflows/test-memory-postgres.yml",
        ".github/workflows/test-schedule-dispatch-postgres.yml",
    }
)

_PROVIDER_SPECIFIC_EXECUTABLE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"aws-actions\s*/\s*configure-aws-credentials",
        r"aws-actions\s*/\s*amazon-ecr-login",
        r"\baws\s+eks\b",
        r"\beksctl\b",
        r"\.dkr\.ecr\.",
        r"eks\.amazonaws\.com",
    )
)

_MAX_GITHUB_EXECUTABLE_BYTES = 1024 * 1024
_MAX_LOCAL_EXECUTION_FILES = 128
_MAX_LOCAL_EXECUTION_DEPTH = 16
_STATIC_LOCAL_PATH_PATTERN = r"(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+"

_LOCAL_USES_PATTERN = re.compile(
    r"^\s*(?:-\s*)?uses:\s*['\"]?(?P<path>\./[^\s'\"#]+)",
    re.MULTILINE,
)
_LOCAL_RELATIVE_EXECUTABLE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_.-])"
    r"(?P<path>(?:\.\.?/)+(?:[A-Za-z0-9_.-]+/)*"
    r"[A-Za-z0-9_.-]+\.(?:py|sh|bash|ps1|js|mjs|cjs))"
    r"(?![A-Za-z0-9_.-])",
    re.IGNORECASE,
)
_LOCAL_RELATIVE_SCRIPT_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_.-])"
    rf"(?P<path>(?:\.\.?/)+scripts/{_STATIC_LOCAL_PATH_PATTERN})"
    r"(?![A-Za-z0-9_./-])",
    re.IGNORECASE,
)
_SCRIPTS_EXECUTABLE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_./-])"
    rf"(?P<path>scripts/{_STATIC_LOCAL_PATH_PATTERN})"
    r"(?![A-Za-z0-9_./-])",
    re.IGNORECASE,
)
_GITHUB_WORKSPACE_EXECUTABLE_PATTERN = re.compile(
    r"(?:\$\{\{\s*github\.workspace\s*\}\}|\$GITHUB_WORKSPACE)"
    rf"/(?P<path>scripts/{_STATIC_LOCAL_PATH_PATTERN})"
    r"(?![A-Za-z0-9_./-])",
    re.IGNORECASE,
)
_PYTHON_MODULE_PATTERN = re.compile(
    r"(?:^|[\s/])python(?:3(?:\.\d+)?)?(?:\.exe)?\s+-m\s+"
    r"(?P<module>scripts(?:\.[A-Za-z_][A-Za-z0-9_]*)+)\b",
    re.IGNORECASE,
)
_ACTION_RUNTIME_PATTERN = re.compile(
    r"^\s*(?:main|pre|post|image):\s*['\"]?(?P<path>[^\s'\"#]+)",
    re.MULTILINE,
)
_GITHUB_ACTION_PATH_PATTERN = re.compile(
    r"(?:\$\{?GITHUB_ACTION_PATH\}?|\$\{\{\s*github\.action_path\s*\}\})/"
    rf"(?P<path>{_STATIC_LOCAL_PATH_PATTERN})"
    r"(?![A-Za-z0-9_./-])",
    re.IGNORECASE,
)


def _read_bounded_utf8(repo_root: Path, path: str) -> str | None:
    root = repo_root.resolve()
    candidate = root
    for part in PurePosixPath(path).parts:
        candidate /= part
        if candidate.is_symlink():
            return None

    if not candidate.is_file():
        return None

    try:
        candidate.resolve().relative_to(root)
        if candidate.stat().st_size > _MAX_GITHUB_EXECUTABLE_BYTES:
            return None
        return candidate.read_text(encoding="utf-8")
    except (OSError, UnicodeError, ValueError):
        return None


def _normalize_static_local_path(raw_path: str) -> str | None:
    candidate = raw_path.replace("\\", "/")
    while candidate.startswith("./"):
        candidate = candidate[2:]
    try:
        return normalize_repo_path(candidate)
    except ValueError:
        return None


def _resolve_local_uses(repo_root: Path, raw_path: str) -> set[str] | None:
    path = _normalize_static_local_path(raw_path)
    if path is None:
        return None
    if is_github_workflow_path(path):
        return {path} if path in _APPROVED_WORKFLOW_PATHS else None
    if not path.startswith(".github/actions/"):
        return None
    if is_github_composite_action_path(path):
        return {path}

    action_dir = repo_root.joinpath(*PurePosixPath(path).parts)
    metadata_paths = {
        f"{path}/{name}"
        for name in ("action.yml", "action.yaml")
        if (action_dir / name).is_file()
    }
    return metadata_paths if len(metadata_paths) == 1 else None


def _resolve_python_module(repo_root: Path, module: str) -> set[str] | None:
    module_path = module.replace(".", "/")
    candidates = (f"{module_path}.py", f"{module_path}/__main__.py")
    existing = {
        path
        for path in candidates
        if repo_root.joinpath(*PurePosixPath(path).parts).is_file()
    }
    return existing or None


def _add_allowed_script_reference(references: set[str], raw_path: str) -> bool:
    path = _normalize_static_local_path(raw_path)
    if path is None or not path.startswith("scripts/"):
        return False
    references.add(path)
    return True


def _delegated_execution_paths(
    repo_root: Path,
    source_path: str,
    content: str,
) -> set[str] | None:
    references: set[str] = set()

    for match in _LOCAL_USES_PATTERN.finditer(content):
        resolved = _resolve_local_uses(repo_root, match.group("path"))
        if resolved is None:
            return None
        references.update(resolved)

    for pattern in (
        _LOCAL_RELATIVE_EXECUTABLE_PATTERN,
        _LOCAL_RELATIVE_SCRIPT_PATTERN,
        _GITHUB_WORKSPACE_EXECUTABLE_PATTERN,
        _SCRIPTS_EXECUTABLE_PATTERN,
    ):
        for match in pattern.finditer(content):
            if not _add_allowed_script_reference(references, match.group("path")):
                return None

    for match in _PYTHON_MODULE_PATTERN.finditer(content):
        resolved = _resolve_python_module(repo_root, match.group("module"))
        if resolved is None:
            return None
        references.update(resolved)

    if is_github_composite_action_path(source_path):
        action_dir = PurePosixPath(source_path).parent
        for match in _ACTION_RUNTIME_PATTERN.finditer(content):
            raw_path = match.group("path")
            if raw_path.startswith("docker://"):
                continue
            runtime_path = _normalize_static_local_path(
                (action_dir / raw_path).as_posix()
            )
            if runtime_path is None:
                return None
            references.add(runtime_path)
        for match in _GITHUB_ACTION_PATH_PATTERN.finditer(content):
            runtime_path = _normalize_static_local_path(
                (action_dir / match.group("path")).as_posix()
            )
            if runtime_path is None:
                return None
            references.add(runtime_path)

    return references


def _github_executable_content_is_unsupported(repo_root: Path, path: str) -> bool:
    pending = [(normalize_repo_path(path), 0)]
    visited: set[str] = set()

    while pending:
        candidate_path, depth = pending.pop()
        if candidate_path in visited:
            continue
        if depth > _MAX_LOCAL_EXECUTION_DEPTH:
            return True
        visited.add(candidate_path)
        if len(visited) > _MAX_LOCAL_EXECUTION_FILES:
            return True

        content = _read_bounded_utf8(repo_root, candidate_path)
        if content is None:
            return True
        if any(
            pattern.search(content) is not None
            for pattern in _PROVIDER_SPECIFIC_EXECUTABLE_PATTERNS
        ):
            return True

        delegated_paths = _delegated_execution_paths(
            repo_root,
            candidate_path,
            content,
        )
        if delegated_paths is None:
            return True
        pending.extend((delegated_path, depth + 1) for delegated_path in delegated_paths)

    return False


def find_unsupported_deployment_paths(
    paths: Iterable[str],
    *,
    repo_root: Path | None = None,
    require_complete_workflow_allowlist: bool = False,
) -> list[str]:
    normalized_paths = list(
        dict.fromkeys(normalize_repo_path(raw_path) for raw_path in paths)
    )
    unsupported: list[str] = []
    for path in normalized_paths:
        if is_unsupported_deployment_path(path):
            unsupported.append(path)
            continue
        if is_github_workflow_path(path) and (
            path not in _APPROVED_WORKFLOW_PATHS
            or (
                repo_root is not None
                and _github_executable_content_is_unsupported(repo_root, path)
            )
        ):
            unsupported.append(path)
            continue
        if (
            is_github_composite_action_path(path)
            and repo_root is not None
            and _github_executable_content_is_unsupported(repo_root, path)
        ):
            unsupported.append(path)

    if require_complete_workflow_allowlist:
        tracked_workflows = {
            path for path in normalized_paths if is_github_workflow_path(path)
        }
        unsupported.extend(sorted(_APPROVED_WORKFLOW_PATHS - tracked_workflows))
    return list(dict.fromkeys(unsupported))


def tracked_paths(repo_root: Path) -> list[str]:
    completed = subprocess.run(
        ["git", "ls-files", "-z", "--"],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )
    return [
        path
        for path in completed.stdout.decode("utf-8").split("\0")
        if path
    ]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Reject deployment surfaces outside the supported boundary"
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    repo_root = args.repo_root.resolve()
    unsupported = find_unsupported_deployment_paths(
        tracked_paths(repo_root),
        repo_root=repo_root,
        require_complete_workflow_allowlist=True,
    )
    if unsupported:
        print(
            "Unsupported or unapproved deployment surface is tracked; "
            "use Docker Compose or the provider-neutral Helm chart:"
        )
        for path in unsupported:
            print(f"- {path}")
        return 1
    print("Supported deployment surface check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
