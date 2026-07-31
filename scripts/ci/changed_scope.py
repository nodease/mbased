from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable


_SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{7,64}$")

_KNOWLEDGE_POSTGRES_PATTERNS = (
    "apps/gateway/adapters/db/knowledge_document_ingestion_repository.py",
    "apps/gateway/adapters/db/sqlalchemy_unit_of_work.py",
    "apps/gateway/application/knowledge_document_ingestion/**",
    "apps/gateway/composition/knowledge_document_ingestion*.py",
    "apps/gateway/knowledge_ingestion_tasks.py",
    "apps/gateway/services/ingestion/job_runner.py",
    "apps/gateway/tests/adapters/db/test_knowledge_document_ingestion_repository_postgres.py",
    "apps/shared/alembic/**",
    "apps/shared/db/models/knowledge.py",
    "apps/shared/domain/knowledge_document_ingestion.py",
    "apps/shared/domain/knowledge_runtime_candidates.py",
    "apps/shared/services/knowledge_document_ingestion_*.py",
    "apps/shared/services/knowledge_ingestion_*.py",
    "apps/shared/services/knowledge_permission_service.py",
    "apps/shared/tests/db/test_knowledge_runtime_snapshot_disposable_postgres.py",
    "apps/shared/tests/domain/test_knowledge_runtime_candidates.py",
    "apps/shared/tests/services/test_knowledge_permission_runtime_bulk.py",
    "apps/workflow_engine/adapters/knowledge_runtime_candidates.py",
    "apps/workflow_engine/adapters/rag_retrieval_connection_acquirer.py",
    "apps/workflow_engine/adapters/rag_retrieval_executor.py",
    "apps/workflow_engine/adapters/rag_retrieval_session.py",
    "apps/workflow_engine/application/rag_retrieval_fanout.py",
    "apps/workflow_engine/application/runtime_retrieval/**",
    "apps/workflow_engine/composition/runtime_retrieval.py",
    "apps/workflow_engine/tests/adapters/test_postgres_knowledge_runtime_candidate_adapter.py",
    "apps/workflow_engine/tests/adapters/test_rag_retrieval_session_postgres.py",
    ".github/workflows/test-knowledge-runtime-postgres.yml",
)

_GATEWAY_WORKFLOW_IMPORT_BOUNDARY_PATTERNS = (
    # Gateway graph validation imports these Workflow definitions without
    # installing worker-only runtime dependencies such as gevent.
    "apps/workflow_engine/adapters/rag_retrieval_*.py",
    "apps/workflow_engine/workflow/core/workflow_node_factory.py",
    "apps/workflow_engine/workflow/nodes/llm/**",
)

_WORKFLOW_POSTGRES_PATTERNS = (
    "apps/gateway/adapters/db/schedule_dispatch_repository.py",
    "apps/gateway/adapters/audit/sqlalchemy_schedule_dispatch_audit.py",
    "apps/gateway/application/deployment/schedule_occurrence.py",
    "apps/gateway/application/deployment/schedule_dispatch.py",
    "apps/gateway/services/scheduler_service.py",
    "apps/shared/alembic/**",
    "apps/shared/celery_app.py",
    "apps/shared/db/models/workflow_node_effect_attempt.py",
    "apps/shared/db/models/llm.py",
    "apps/shared/db/models/provider_usage.py",
    "apps/shared/db/models/schedule_dispatch.py",
    "apps/shared/domain/schedule_dispatch.py",
    "apps/shared/services/schedule_dispatch_*.py",
    "apps/shared/tests/db/test_schedule_dispatch_disposable_postgres.py",
    "apps/shared/tests/db/test_external_effect_disposable_postgres.py",
    "apps/shared/tests/db/test_provider_execution_node_location_migration.py",
    "apps/shared/tests/db/test_query_embedding_capability_migration_postgres.py",
    "apps/workflow_engine/adapters/db/external_effect_repository.py",
    "apps/workflow_engine/application/external_effect.py",
    "apps/workflow_engine/composition/external_effect_readiness.py",
    "apps/workflow_engine/adapters/schedule_dispatch_repository.py",
    "apps/workflow_engine/adapters/schedule_dispatch_audit.py",
    "apps/workflow_engine/application/schedule_dispatch.py",
    "apps/workflow_engine/tasks.py",
    "scripts/check_schedule_dispatch_rollback.py",
    ".github/workflows/test-schedule-dispatch-postgres.yml",
)

_AGENT_BUILDER_POSTGRES_PATTERNS = (
    "apps/gateway/adapters/db/agent_builder_repository.py",
    "apps/gateway/api/v1/endpoints/llm.py",
    "apps/gateway/api/v1/endpoints/organization.py",
    "apps/gateway/application/agent_builder/**",
    "apps/gateway/composition/agent_builder.py",
    "apps/gateway/services/admin_usage_service.py",
    "apps/gateway/services/agent_builder/**",
    "apps/gateway/services/agent_builder_intent_service.py",
    "apps/gateway/services/agent_builder_service.py",
    "apps/gateway/services/app_service.py",
    "apps/gateway/services/llm_service.py",
    "apps/gateway/services/organization_member_service.py",
    "apps/gateway/services/workflow_budget_service.py",
    "apps/shared/services/llm_client/**",
    "apps/gateway/tests/integration/test_agent_builder_intent_usage_postgres.py",
    "apps/gateway/tests/services/test_llm_client_base.py",
    "apps/gateway/tests/services/test_llm_client_openai.py",
    "apps/shared/tests/services/test_anthropic_client.py",
    "apps/gateway/services/workflow_service.py",
    "apps/gateway/tests/integration/test_agent_builder_model_selection_db.py",
    "apps/gateway/tests/integration/test_agent_builder_primary_workflow_db.py",
    "apps/gateway/tests/integration/test_agent_builder_workflow_cas.py",
    "apps/shared/alembic/**",
    "apps/shared/db/models/agent_builder.py",
    "apps/shared/db/models/llm.py",
    "apps/shared/db/models/workflow.py",
    "apps/shared/domain/llm_usage.py",
    "apps/shared/schemas/agent_builder.py",
    "apps/shared/schemas/organization_membership.py",
    "apps/shared/schemas/workflow.py",
    ".github/workflows/test-agent-builder-postgres.yml",
)

_MEMORY_POSTGRES_PATTERNS = (
    "apps/memory/**",
    "apps/shared/alembic/**",
    "apps/shared/db/models/conversation_memory.py",
    "apps/shared/tests/helpers/disposable_postgres.py",
    ".github/workflows/test-memory-postgres.yml",
)

_MEMORY_SHARED_TEST_PATTERNS = (
    "apps/shared/alembic/**",
    "apps/shared/db/base.py",
    "apps/shared/db/models/__init__.py",
    "apps/shared/db/models/conversation_memory.py",
    "apps/shared/pyproject.toml",
)

_LOG_SYSTEM_SHARED_SERVICE_PATTERNS = (
    "apps/shared/services/external_effect_trace_capture.py",
    "apps/shared/services/knowledge_ingestion_outbox.py",
    "apps/shared/services/knowledge_ingestion_outbox_processor.py",
    "apps/shared/services/rag_answer_retention.py",
)

_DOCUMENTATION_ROOT_FILES = {
    "AGENTS.md",
    "CONTRIBUTING.md",
    "README.md",
}

_DEPLOYMENT_ONLY_PREFIXES = (
    "dev/",
    "docker/",
    "infra/",
)

_DEPLOYMENT_ONLY_WORKFLOWS = (
    ".github/workflows/deploy-",
    ".github/workflows/publish-images.yml",
)

_UNSUPPORTED_DEPLOYMENT_WORKFLOW_STEMS = frozenset({"deploy-dev-namespace"})

_UNSUPPORTED_DEPLOYMENT_WORKFLOW_PREFIXES = ("deploy-eks-",)

_UNSUPPORTED_DEPLOYMENT_PREFIXES = (
    "infra/k8s/",
    "infra/terraform/",
)

_GITHUB_WORKFLOW_SUFFIXES = frozenset({".yml", ".yaml"})

_GITHUB_COMPOSITE_ACTION_NAMES = frozenset({"action.yml", "action.yaml"})

_PROTECTED_CI_WORKFLOW_PATHS = {
    ".github/workflows/pr-ci-control-guard.yml",
    ".github/workflows/pr-quality-gate.yml",
    ".github/workflows/test-knowledge-runtime-postgres.yml",
    ".github/workflows/test-schedule-dispatch-postgres.yml",
    ".github/workflows/test-agent-builder-postgres.yml",
    ".github/workflows/test-memory-postgres.yml",
}

_COMPOSE_FILE_NAME_PATTERN = re.compile(
    r"^(?:docker-)?compose(?:\.[A-Za-z0-9_-]+)*\.ya?ml$"
)
_EGRESS_PROXY_PATH_PREFIXES = (
    "docker/proxy/",
    "tests/ci/fixtures/egress-proxy/",
)
_EGRESS_PROXY_PATHS = frozenset(
    {
        "docker/docker-compose.yml",
        "infra/helm/moduly/files/squid.conf",
        "infra/helm/moduly/templates/_helpers.tpl",
        "infra/helm/moduly/templates/configmap.yaml",
        "infra/helm/moduly/templates/egress-proxy-configmap.yaml",
        "infra/helm/moduly/templates/egress-proxy-deployment.yaml",
        "infra/helm/moduly/templates/egress-proxy-pdb.yaml",
        "infra/helm/moduly/templates/egress-proxy-service.yaml",
        "infra/helm/moduly/templates/gateway-deployment.yaml",
        "infra/helm/moduly/templates/knowledge-worker-deployment.yaml",
        "infra/helm/moduly/templates/proxy-only-networkpolicies.yaml",
        "infra/helm/moduly/templates/worker-deployment.yaml",
        "infra/helm/moduly/templates/worker-networkpolicy.yaml",
        "infra/helm/moduly/values-local.yaml",
        "infra/helm/moduly/values-production.yaml",
        "infra/helm/moduly/values.yaml",
        "tests/ci/fixtures/helm-values-ci.yaml",
        "tests/ci/test_egress_proxy_kubernetes.py",
        "tests/ci/test_egress_proxy_runtime.py",
    }
)


@dataclass
class ChangeScope:
    docs_only: bool = False
    python_lint: bool = False
    client: bool = False
    gateway_tests: bool = False
    workflow_tests: bool = False
    shared_tests: bool = False
    log_tests: bool = False
    sandbox_tests: bool = False
    root_tests: bool = False
    memory_tests: bool = False
    knowledge_postgres: bool = False
    workflow_postgres: bool = False
    agent_builder_postgres: bool = False
    memory_postgres: bool = False
    deployment_validation: bool = False
    actions_validation: bool = False
    helm_validation: bool = False
    support_surface_validation: bool = False
    compose_validation: bool = False
    egress_proxy_validation: bool = False
    dockerfile_validation: bool = False
    dockerfile_config_changed: bool = False
    broad_python: bool = False

    def enable_python_smoke(self) -> None:
        self.gateway_tests = True
        self.workflow_tests = True
        self.shared_tests = True
        self.log_tests = True
        self.sandbox_tests = True
        self.root_tests = True
        self.memory_tests = True
        self.broad_python = True

    def enable_deployment_smoke(self) -> None:
        self.deployment_validation = True
        self.actions_validation = True
        self.helm_validation = True
        self.support_surface_validation = True
        self.compose_validation = True
        self.egress_proxy_validation = True
        self.dockerfile_validation = True

    def github_outputs(self) -> dict[str, str]:
        outputs = {
            name: "true" if value else "false" for name, value in asdict(self).items()
        }
        outputs["gateway_job"] = (
            "true" if self.gateway_tests or self.root_tests else "false"
        )
        return outputs


def normalize_repo_path(raw_path: str) -> str:
    candidate = raw_path.replace("\\", "/")
    if candidate.startswith("/") or re.match(r"^[a-zA-Z]:/", candidate):
        raise ValueError(f"repository path must be relative: {raw_path!r}")
    normalized = candidate.strip("/")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"invalid repository path: {raw_path!r}")
    return path.as_posix()


def parse_name_status_z(raw: bytes) -> list[str]:
    tokens = raw.decode("utf-8").split("\0")
    if tokens and not tokens[-1]:
        tokens.pop()

    paths: list[str] = []
    index = 0
    while index < len(tokens):
        status = tokens[index]
        index += 1
        if not status:
            raise ValueError("git diff returned an empty status")

        if status[0] in {"R", "C"}:
            if index + 1 >= len(tokens):
                raise ValueError(f"git diff returned an incomplete {status} record")
            paths.extend((tokens[index], tokens[index + 1]))
            index += 2
        else:
            if index >= len(tokens):
                raise ValueError(f"git diff returned an incomplete {status} record")
            paths.append(tokens[index])
            index += 1

    return list(dict.fromkeys(normalize_repo_path(path) for path in paths))


def changed_paths_from_git(base: str, head: str, repo_root: Path) -> list[str]:
    for revision in (base, head):
        if not _SHA_PATTERN.fullmatch(revision):
            raise ValueError(f"revision must be a commit SHA: {revision!r}")

    completed = subprocess.run(
        [
            "git",
            "diff",
            "--name-status",
            "-z",
            "--find-renames",
            base,
            head,
            "--",
        ],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )
    return parse_name_status_z(completed.stdout)


def _matches_any(path: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def _is_documentation_path(path: str) -> bool:
    return (
        path.startswith("docs/")
        or path.startswith("docs_old/")
        or path in _DOCUMENTATION_ROOT_FILES
        or path.endswith(".md")
    )


def _is_deployment_only_path(path: str) -> bool:
    return path.startswith(_DEPLOYMENT_ONLY_PREFIXES) or path.startswith(
        _DEPLOYMENT_ONLY_WORKFLOWS
    )


def _is_ci_control_path(path: str) -> bool:
    return (
        path in _PROTECTED_CI_WORKFLOW_PATHS
        or path.startswith(".github/actions/")
        or path.startswith("scripts/ci/")
        or path.startswith("tests/ci/")
    )


def _is_dockerfile_path(path: str) -> bool:
    name = PurePosixPath(path).name
    return (
        name == "Dockerfile"
        or name.startswith("Dockerfile.")
        or name.endswith(".Dockerfile")
    )


def is_github_workflow_path(raw_path: str) -> bool:
    path = normalize_repo_path(raw_path)
    workflow_path = PurePosixPath(path)
    return (
        workflow_path.parent == PurePosixPath(".github/workflows")
        and workflow_path.suffix.lower() in _GITHUB_WORKFLOW_SUFFIXES
    )


def is_github_composite_action_path(raw_path: str) -> bool:
    path = PurePosixPath(normalize_repo_path(raw_path))
    return (
        len(path.parts) >= 3
        and path.parts[:2] == (".github", "actions")
        and path.name.lower() in _GITHUB_COMPOSITE_ACTION_NAMES
    )


def is_unsupported_deployment_path(raw_path: str) -> bool:
    path = normalize_repo_path(raw_path)
    workflow_path = PurePosixPath(path)
    if is_github_workflow_path(path) and (
        workflow_path.stem in _UNSUPPORTED_DEPLOYMENT_WORKFLOW_STEMS
        or workflow_path.stem.startswith(_UNSUPPORTED_DEPLOYMENT_WORKFLOW_PREFIXES)
    ):
        return True
    return path.startswith(_UNSUPPORTED_DEPLOYMENT_PREFIXES)


def _select_deployment_validation(path: str, scope: ChangeScope) -> None:
    if is_github_workflow_path(path):
        scope.actions_validation = True
        # The support-surface guard owns an allowlist and content inspection,
        # so every executable workflow change must select it regardless of name.
        scope.support_surface_validation = True
    elif is_github_composite_action_path(path):
        scope.actions_validation = True
        # A workflow can delegate its provider-specific steps to a local action.
        scope.support_surface_validation = True
    elif path.startswith((".github/workflows/", ".github/actions/")):
        scope.actions_validation = True
    if (
        path.startswith("infra/helm/")
        or path == "tests/ci/fixtures/helm-values-ci.yaml"
    ):
        scope.helm_validation = True
    if is_unsupported_deployment_path(path):
        scope.support_surface_validation = True
    if _COMPOSE_FILE_NAME_PATTERN.fullmatch(PurePosixPath(path).name):
        scope.compose_validation = True
    if path in _EGRESS_PROXY_PATHS or path.startswith(_EGRESS_PROXY_PATH_PREFIXES):
        scope.egress_proxy_validation = True
    if _is_dockerfile_path(path):
        scope.dockerfile_validation = True
        scope.dockerfile_config_changed = True

    scope.deployment_validation = any(
        (
            scope.actions_validation,
            scope.helm_validation,
            scope.support_surface_validation,
            scope.compose_validation,
            scope.egress_proxy_validation,
            scope.dockerfile_validation,
        )
    )


def classify_paths(raw_paths: Iterable[str]) -> ChangeScope:
    paths = list(dict.fromkeys(normalize_repo_path(path) for path in raw_paths))
    scope = ChangeScope(
        docs_only=bool(paths) and all(_is_documentation_path(path) for path in paths)
    )

    if not paths:
        scope.client = True
        scope.enable_python_smoke()
        scope.knowledge_postgres = True
        scope.workflow_postgres = True
        scope.agent_builder_postgres = True
        scope.memory_postgres = True
        return scope

    for path in paths:
        _select_deployment_validation(path, scope)

        if _matches_any(path, _KNOWLEDGE_POSTGRES_PATTERNS):
            scope.knowledge_postgres = True
        if _matches_any(path, _GATEWAY_WORKFLOW_IMPORT_BOUNDARY_PATTERNS):
            scope.gateway_tests = True
        if _matches_any(path, _WORKFLOW_POSTGRES_PATTERNS):
            scope.workflow_postgres = True
        if _matches_any(path, _AGENT_BUILDER_POSTGRES_PATTERNS):
            scope.agent_builder_postgres = True
        if _matches_any(path, _MEMORY_POSTGRES_PATTERNS):
            scope.memory_postgres = True

        if _is_documentation_path(path):
            continue
        if path.endswith(".py"):
            scope.python_lint = True
        if _is_deployment_only_path(path):
            continue

        if _is_ci_control_path(path):
            scope.enable_deployment_smoke()
            scope.client = True
            scope.enable_python_smoke()
            scope.knowledge_postgres = True
            scope.workflow_postgres = True
            scope.agent_builder_postgres = True
            scope.memory_postgres = True
            continue

        if path.startswith("apps/client/"):
            scope.client = True
            continue

        if path.startswith("apps/memory/"):
            scope.memory_tests = True
            continue

        if path.startswith("apps/gateway/"):
            scope.gateway_tests = True
            continue

        if path.startswith("apps/workflow_engine/"):
            scope.workflow_tests = True
            continue

        if path.startswith("apps/log_system/"):
            scope.log_tests = True
            continue

        if path.startswith("apps/sandbox/"):
            scope.sandbox_tests = True
            continue

        if path.startswith("apps/shared/"):
            scope.shared_tests = True
            scope.gateway_tests = True
            scope.workflow_tests = True

            if _matches_any(path, _MEMORY_SHARED_TEST_PATTERNS):
                scope.memory_tests = True

            if path.startswith(("apps/shared/db/", "apps/shared/schemas/")):
                scope.root_tests = True
                scope.log_tests = True
            if path.startswith("apps/shared/alembic/"):
                scope.root_tests = True
                scope.log_tests = True
                scope.knowledge_postgres = True
                scope.workflow_postgres = True
                scope.memory_postgres = True
            if _matches_any(path, _LOG_SYSTEM_SHARED_SERVICE_PATTERNS):
                scope.log_tests = True
            if any(
                marker in path
                for marker in ("audit", "tracing", "security_alert", "celery")
            ):
                scope.log_tests = True
            continue

        if path.startswith("tests/"):
            if path.startswith(("tests/e2e/", "tests/evaluation/", "tests/load/")):
                continue
            scope.root_tests = True
            continue

        if path.startswith("scripts/") and path.endswith(".py"):
            scope.root_tests = True
            continue

        if path.startswith(".github/workflows/"):
            # Protected workflows are handled as CI control above. Other non-deploy
            # workflow changes keep broad runtime smoke coverage.
            scope.client = True
            scope.enable_python_smoke()
            continue

        if path in {".gitattributes", ".gitignore"}:
            continue

        # Unknown executable/configuration paths must not silently lose coverage.
        scope.client = True
        scope.enable_python_smoke()

    return scope


def changed_python_files(paths: Iterable[str], repo_root: Path) -> list[str]:
    candidates = []
    resolved_root = repo_root.resolve()
    for raw_path in paths:
        path = normalize_repo_path(raw_path)
        candidate = repo_root / path
        if not path.endswith(".py") or not candidate.is_file():
            continue
        if candidate.is_symlink():
            raise ValueError(f"changed Python path must not be a symlink: {path}")
        try:
            candidate.resolve().relative_to(resolved_root)
        except ValueError as exc:
            raise ValueError(f"changed Python path escapes repository: {path}") from exc
        candidates.append(path)
    return sorted(set(candidates))


def _write_github_outputs(output_path: Path, scope: ChangeScope) -> None:
    with output_path.open("a", encoding="utf-8", newline="\n") as output:
        for name, value in scope.github_outputs().items():
            output.write(f"{name}={value}\n")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Classify pull request change scope")
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--python-files0", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    repo_root = args.repo_root.resolve()
    paths = changed_paths_from_git(args.base, args.head, repo_root)

    if args.python_files0:
        files = changed_python_files(paths, repo_root)
        sys.stdout.buffer.write(
            b"".join(path.encode("utf-8") + b"\0" for path in files)
        )
        return 0

    scope = classify_paths(paths)
    if args.github_output:
        _write_github_outputs(args.github_output, scope)
    print(json.dumps({"changed_paths": paths, "scope": asdict(scope)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
