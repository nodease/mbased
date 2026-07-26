from pathlib import Path

import pytest

from scripts.ci.changed_scope import (
    changed_python_files,
    classify_paths,
    normalize_repo_path,
    parse_name_status_z,
)


def test_docs_only_keeps_runtime_jobs_disabled():
    scope = classify_paths(
        [
            "docs/README.md",
            "docs/features/workflow/requirements.md",
            "docs/examples/sample.py",
        ]
    )

    assert scope.docs_only is True
    assert scope.python_lint is False
    assert scope.client is False
    assert scope.gateway_tests is False
    assert scope.workflow_tests is False


def test_client_change_selects_only_client_runtime_job():
    scope = classify_paths(["apps/client/app/dashboard/page.tsx"])

    assert scope.client is True
    assert scope.python_lint is False
    assert scope.gateway_tests is False


def test_memory_change_selects_only_memory_python_and_postgres_contracts():
    scope = classify_paths(["apps/memory/application/lifecycle.py"])

    assert scope.python_lint is True
    assert scope.memory_tests is True
    assert scope.memory_postgres is True
    assert scope.gateway_tests is False
    assert scope.workflow_tests is False
    assert scope.shared_tests is False
    assert scope.client is False
    assert scope.broad_python is False


@pytest.mark.parametrize(
    "path",
    [
        "apps/shared/services/knowledge_ingestion_fencing.py",
        "apps/shared/services/knowledge_ingestion_finalizer.py",
        "apps/shared/services/knowledge_ingestion_outbox.py",
        "apps/shared/services/knowledge_ingestion_outbox_processor.py",
    ],
)
def test_durable_knowledge_ingestion_service_selects_postgres_contract(path: str):
    scope = classify_paths([path])

    assert scope.knowledge_postgres is True


def test_unrelated_shared_change_does_not_expand_to_memory_domain():
    scope = classify_paths(["apps/shared/schemas/organization_membership.py"])

    assert scope.shared_tests is True
    assert scope.gateway_tests is True
    assert scope.workflow_tests is True
    assert scope.memory_tests is False
    assert scope.memory_postgres is False


def test_shared_schema_change_expands_to_consumers_and_root_tests():
    scope = classify_paths(["apps/shared/schemas/organization_membership.py"])

    assert scope.python_lint is True
    assert scope.shared_tests is True
    assert scope.gateway_tests is True
    assert scope.workflow_tests is True
    assert scope.log_tests is True
    assert scope.root_tests is True
    assert scope.broad_python is False
    assert scope.github_outputs()["gateway_job"] == "true"


def test_migration_change_selects_graph_consumers_and_postgres_contracts():
    scope = classify_paths(["apps/shared/alembic/versions/abc_add_table.py"])

    assert scope.shared_tests is True
    assert scope.gateway_tests is True
    assert scope.workflow_tests is True
    assert scope.knowledge_postgres is True
    assert scope.workflow_postgres is True
    assert scope.agent_builder_postgres is True
    assert scope.memory_tests is True
    assert scope.memory_postgres is True


def test_agent_builder_change_selects_agent_builder_postgres():
    scope = classify_paths(
        ["apps/gateway/services/agent_builder/parameter_task_service.py"]
    )

    assert scope.gateway_tests is True
    assert scope.agent_builder_postgres is True
    assert scope.knowledge_postgres is False


@pytest.mark.parametrize(
    "path",
    [
        "apps/gateway/api/v1/endpoints/llm.py",
        "apps/gateway/api/v1/endpoints/organization.py",
        "apps/gateway/services/admin_usage_service.py",
        "apps/gateway/services/agent_builder_intent_service.py",
        "apps/gateway/services/app_service.py",
        "apps/gateway/services/llm_service.py",
        "apps/gateway/services/organization_member_service.py",
        "apps/gateway/services/workflow_budget_service.py",
        "apps/shared/db/models/llm.py",
        "apps/shared/domain/llm_usage.py",
        "apps/shared/schemas/organization_membership.py",
        "apps/shared/services/llm_client/anthropic_client.py",
        "apps/shared/services/llm_client/google_client.py",
        "apps/shared/services/llm_client/openai_client.py",
        "apps/gateway/tests/integration/test_agent_builder_intent_usage_postgres.py",
        "apps/gateway/tests/services/test_llm_client_base.py",
        "apps/gateway/tests/services/test_llm_client_openai.py",
        "apps/shared/tests/services/test_anthropic_client.py",
    ],
)
def test_agent_builder_usage_change_selects_agent_builder_postgres(path: str):
    scope = classify_paths([path])

    assert scope.agent_builder_postgres is True


@pytest.mark.parametrize(
    "path",
    [
        "apps/workflow_engine/application/runtime_retrieval/knowledge_candidates.py",
        "apps/workflow_engine/application/rag_retrieval_fanout.py",
        "apps/workflow_engine/adapters/rag_retrieval_connection_acquirer.py",
        "apps/workflow_engine/adapters/rag_retrieval_executor.py",
        "apps/workflow_engine/adapters/rag_retrieval_session.py",
        "apps/workflow_engine/tests/adapters/test_rag_retrieval_session_postgres.py",
    ],
)
def test_knowledge_runtime_change_selects_knowledge_postgres(path: str):
    scope = classify_paths([path])

    assert scope.workflow_tests is True
    assert scope.knowledge_postgres is True
    assert scope.workflow_postgres is False


@pytest.mark.parametrize(
    "path",
    [
        "apps/workflow_engine/workflow/core/workflow_node_factory.py",
        "apps/workflow_engine/workflow/nodes/llm/__init__.py",
        "apps/workflow_engine/adapters/rag_retrieval_connection_acquirer.py",
        "apps/workflow_engine/adapters/rag_retrieval_executor.py",
    ],
)
def test_worker_import_boundary_change_selects_gateway_contract(path: str):
    scope = classify_paths([path])

    assert scope.workflow_tests is True
    assert scope.gateway_tests is True


@pytest.mark.parametrize(
    "path",
    [
        "apps/gateway/adapters/db/knowledge_document_ingestion_repository.py",
        "apps/gateway/application/knowledge_document_ingestion/worker.py",
        "apps/gateway/services/ingestion/job_runner.py",
        "apps/shared/db/models/knowledge.py",
        "apps/shared/domain/knowledge_document_ingestion.py",
        "apps/shared/services/knowledge_document_ingestion_projection.py",
        "apps/gateway/tests/adapters/db/test_knowledge_document_ingestion_repository_postgres.py",
    ],
)
def test_knowledge_ingestion_change_selects_knowledge_postgres(path: str):
    scope = classify_paths([path])

    assert scope.knowledge_postgres is True


def test_schedule_change_selects_workflow_postgres():
    scope = classify_paths(["apps/shared/domain/schedule_dispatch.py"])

    assert scope.shared_tests is True
    assert scope.workflow_postgres is True


def test_provider_node_location_migration_test_selects_workflow_postgres():
    scope = classify_paths(
        ["apps/shared/tests/db/test_provider_execution_node_location_migration.py"]
    )

    assert scope.shared_tests is True
    assert scope.workflow_postgres is True


@pytest.mark.parametrize(
    "path",
    [
        "apps/shared/db/models/llm.py",
        "apps/shared/db/models/provider_usage.py",
        "apps/shared/tests/db/test_query_embedding_capability_migration_postgres.py",
    ],
)
def test_query_embedding_schema_change_selects_workflow_postgres(path: str):
    scope = classify_paths([path])

    assert scope.shared_tests is True
    assert scope.workflow_postgres is True


@pytest.mark.parametrize(
    "path",
    [
        "apps/shared/services/external_effect_trace_capture.py",
        "apps/shared/services/knowledge_ingestion_outbox.py",
        "apps/shared/services/knowledge_ingestion_outbox_processor.py",
        "apps/shared/services/rag_answer_retention.py",
    ],
)
def test_log_system_direct_shared_service_selects_log_tests(path: str):
    scope = classify_paths([path])

    assert scope.shared_tests is True
    assert scope.log_tests is True


def test_ci_control_change_selects_smoke_jobs_and_postgres_contracts():
    scope = classify_paths(["scripts/ci/changed_scope.py"])

    assert scope.client is True
    assert scope.gateway_tests is True
    assert scope.workflow_tests is True
    assert scope.shared_tests is True
    assert scope.log_tests is True
    assert scope.sandbox_tests is True
    assert scope.root_tests is True
    assert scope.broad_python is True
    assert scope.knowledge_postgres is True
    assert scope.workflow_postgres is True
    assert scope.agent_builder_postgres is True
    assert scope.memory_tests is True
    assert scope.memory_postgres is True
    assert scope.deployment_validation is True
    assert scope.actions_validation is True
    assert scope.helm_validation is True
    assert scope.support_surface_validation is True
    assert scope.compose_validation is True
    assert scope.dockerfile_validation is True
    assert scope.dockerfile_config_changed is False


def test_trusted_guard_change_is_treated_as_ci_control():
    scope = classify_paths([".github/workflows/pr-ci-control-guard.yml"])

    assert scope.broad_python is True
    assert scope.knowledge_postgres is True
    assert scope.workflow_postgres is True
    assert scope.agent_builder_postgres is True
    assert scope.memory_tests is True
    assert scope.memory_postgres is True


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/test-knowledge-runtime-postgres.yml",
        ".github/workflows/test-schedule-dispatch-postgres.yml",
        ".github/workflows/test-agent-builder-postgres.yml",
        ".github/workflows/test-memory-postgres.yml",
    ],
)
def test_protected_postgres_workflow_change_is_treated_as_ci_control(path: str):
    scope = classify_paths([path])

    assert scope.root_tests is True
    assert scope.broad_python is True
    assert scope.actions_validation is True
    assert scope.knowledge_postgres is True
    assert scope.workflow_postgres is True
    assert scope.agent_builder_postgres is True
    assert scope.memory_postgres is True


def test_deployment_workflow_selects_static_validation_without_runtime_tests():
    scope = classify_paths([".github/workflows/publish-images.yml"])

    assert scope.deployment_validation is True
    assert scope.actions_validation is True
    assert scope.support_surface_validation is True
    assert scope.client is False
    assert scope.gateway_tests is False
    assert scope.broad_python is False


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/deploy-prod.yml",
        ".github/workflows/release-renamed.yaml",
    ],
)
def test_every_workflow_change_selects_support_boundary_guard(path: str):
    scope = classify_paths([path])

    assert scope.deployment_validation is True
    assert scope.actions_validation is True
    assert scope.support_surface_validation is True


@pytest.mark.parametrize(
    "path",
    [
        ".github/actions/deploy/action.yml",
        ".github/actions/release/action.yaml",
    ],
)
def test_every_composite_action_change_selects_support_boundary_guard(path: str):
    scope = classify_paths([path])

    assert scope.deployment_validation is True
    assert scope.actions_validation is True
    assert scope.support_surface_validation is True


@pytest.mark.parametrize(
    ("path", "selected_output"),
    [
        ("infra/helm/moduly/values-production.yaml", "helm_validation"),
        ("docker/docker-compose.yml", "compose_validation"),
        ("docker/docker-compose.connector-demo.yml", "compose_validation"),
        ("docker/gateway/Dockerfile", "dockerfile_validation"),
        ("docker/gateway/Dockerfile.dev", "dockerfile_validation"),
        ("docker/proxy/squid.conf", "egress_proxy_validation"),
        (
            "infra/helm/moduly/templates/proxy-only-networkpolicies.yaml",
            "egress_proxy_validation",
        ),
    ],
)
def test_deployment_config_selects_only_its_static_validator(
    path: str,
    selected_output: str,
):
    scope = classify_paths([path])

    assert scope.deployment_validation is True
    assert getattr(scope, selected_output) is True
    assert scope.client is False
    assert scope.gateway_tests is False
    assert scope.broad_python is False


def test_egress_proxy_ci_test_keeps_ci_control_smoke_coverage() -> None:
    scope = classify_paths(["tests/ci/test_egress_proxy_kubernetes.py"])

    assert scope.egress_proxy_validation is True
    assert scope.deployment_validation is True
    assert scope.client is True
    assert scope.gateway_tests is True
    assert scope.broad_python is True


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/deploy-dev-namespace.yml",
        ".github/workflows/deploy-dev-namespace.yaml",
        ".github/workflows/deploy-eks-gateway.yml",
        ".github/workflows/deploy-eks-gateway.yaml",
        ".github/workflows/deploy-eks-schedule-coordinated.yml",
        ".github/workflows/deploy-eks-reintroduced.yml",
        "infra/k8s/ingress.yaml",
        "infra/terraform/eks.tf",
    ],
)
def test_removed_eks_surface_selects_support_boundary_guard(path: str):
    scope = classify_paths([path])

    assert scope.deployment_validation is True
    assert scope.support_surface_validation is True
    assert scope.client is False
    assert scope.gateway_tests is False
    assert scope.broad_python is False


def test_dockerfile_change_is_distinct_from_ci_control_smoke_selection():
    scope = classify_paths(
        [
            ".github/workflows/pr-quality-gate.yml",
            "docker/gateway/Dockerfile",
        ]
    )

    assert scope.dockerfile_validation is True
    assert scope.dockerfile_config_changed is True


def test_unrelated_workflow_still_fails_closed_with_postgres_change():
    scope = classify_paths(
        [
            "apps/shared/domain/schedule_dispatch.py",
            ".github/workflows/custom-runtime-check.yml",
        ]
    )

    assert scope.workflow_postgres is True
    assert scope.broad_python is True
    assert scope.client is True


def test_unknown_path_fails_closed_to_broad_smoke_jobs():
    scope = classify_paths(["new_runtime/bootstrap.conf"])

    assert scope.client is True
    assert scope.broad_python is True
    assert scope.root_tests is True


def test_empty_diff_fails_closed():
    scope = classify_paths([])

    assert scope.client is True
    assert scope.broad_python is True
    assert scope.knowledge_postgres is True
    assert scope.workflow_postgres is True
    assert scope.agent_builder_postgres is True
    assert scope.memory_tests is True
    assert scope.memory_postgres is True


def test_name_status_parser_preserves_both_sides_of_rename():
    raw = b"M\0docs/README.md\0R100\0apps/gateway/old.py\0apps/shared/new.py\0"

    assert parse_name_status_z(raw) == [
        "docs/README.md",
        "apps/gateway/old.py",
        "apps/shared/new.py",
    ]


def test_python_file_selection_excludes_deleted_files(tmp_path: Path):
    existing = tmp_path / "apps" / "shared" / "service.py"
    existing.parent.mkdir(parents=True)
    existing.write_text("VALUE = 1\n", encoding="utf-8")

    assert changed_python_files(
        ["apps/shared/service.py", "apps/shared/deleted.py", "docs/README.md"],
        tmp_path,
    ) == ["apps/shared/service.py"]


@pytest.mark.parametrize(
    "path",
    ["/etc/passwd", "C:/Windows/system.ini", "../outside.py"],
)
def test_path_normalization_rejects_paths_outside_repository(path: str):
    with pytest.raises(ValueError):
        normalize_repo_path(path)


def test_python_file_selection_rejects_symlink(tmp_path: Path):
    target = tmp_path / "target.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    link = tmp_path / "apps" / "shared" / "linked.py"
    link.parent.mkdir(parents=True)
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is not available")

    with pytest.raises(ValueError, match="must not be a symlink"):
        changed_python_files(["apps/shared/linked.py"], tmp_path)
