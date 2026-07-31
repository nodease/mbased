from pathlib import Path
import subprocess
import sys

import pytest

from scripts.ci.select_pytest_targets import select_pytest_targets


def _write(repo: Path, relative: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("def test_placeholder():\n    pass\n", encoding="utf-8")


def test_selector_module_entrypoint_is_runnable():
    repo_root = Path(__file__).resolve().parents[2]

    completed = subprocess.run(
        [sys.executable, "-m", "scripts.ci.select_pytest_targets", "--help"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_selects_changed_test_file_directly(tmp_path: Path):
    target = "apps/gateway/tests/services/test_mail_credential_service.py"
    _write(tmp_path, target)
    _write(tmp_path, "apps/gateway/tests/architecture/test_boundaries.py")

    assert select_pytest_targets("gateway", [target], tmp_path) == [
        "apps/gateway/tests/architecture",
        target,
    ]


@pytest.mark.parametrize(
    "target",
    [
        "apps/gateway/tests/adapters/db/test_knowledge_document_ingestion_repository_postgres.py",
        "apps/gateway/tests/integration/test_agent_builder_intent_usage_postgres.py",
        "apps/gateway/tests/integration/test_agent_builder_workflow_cas.py",
    ],
)
def test_gateway_selector_excludes_postgres_only_tests(
    tmp_path: Path,
    target: str,
):
    _write(tmp_path, target)
    _write(tmp_path, "apps/gateway/tests/architecture/test_boundaries.py")

    assert select_pytest_targets("gateway", [target], tmp_path) == [
        "apps/gateway/tests/architecture"
    ]


def test_selects_explicit_api_and_feature_consumers_for_shared_response_schema(
    tmp_path: Path,
):
    source = "apps/shared/schemas/organization_membership.py"
    api_target = "apps/gateway/tests/api/test_organizations_api.py"
    service_target = (
        "apps/gateway/tests/services/test_organization_member_service.py"
    )
    _write(tmp_path, source)
    _write(tmp_path, api_target)
    _write(tmp_path, service_target)
    _write(tmp_path, "apps/gateway/tests/architecture/test_boundaries.py")

    assert select_pytest_targets(
        "gateway",
        [source],
        tmp_path,
    ) == ["apps/gateway/tests/architecture", api_target, service_target]


def test_explicit_contract_mapping_fails_closed_when_consumer_is_missing(
    tmp_path: Path,
):
    source = "apps/shared/schemas/organization_membership.py"
    _write(tmp_path, source)
    _write(
        tmp_path,
        "apps/gateway/tests/services/test_organization_member_service.py",
    )
    _write(tmp_path, "apps/gateway/tests/architecture/test_boundaries.py")

    with pytest.raises(RuntimeError, match="explicit contract test target is missing"):
        select_pytest_targets(
            "gateway",
            [source],
            tmp_path,
        )


def test_explicit_contract_mapping_validates_a_changed_consumer_path(
    tmp_path: Path,
):
    source = "apps/shared/schemas/organization_membership.py"
    missing_api_target = "apps/gateway/tests/api/test_organizations_api.py"
    _write(tmp_path, source)
    _write(
        tmp_path,
        "apps/gateway/tests/services/test_organization_member_service.py",
    )
    _write(tmp_path, "apps/gateway/tests/architecture/test_boundaries.py")

    with pytest.raises(RuntimeError, match="explicit contract test target is missing"):
        select_pytest_targets(
            "gateway",
            [missing_api_target],
            tmp_path,
        )


def test_explicit_contract_mapping_validates_a_changed_source_path(
    tmp_path: Path,
):
    missing_source = "apps/shared/schemas/organization_membership.py"
    _write(tmp_path, "apps/gateway/tests/api/test_organizations_api.py")
    _write(
        tmp_path,
        "apps/gateway/tests/services/test_organization_member_service.py",
    )
    _write(tmp_path, "apps/gateway/tests/architecture/test_boundaries.py")

    with pytest.raises(RuntimeError, match="explicit contract source is missing"):
        select_pytest_targets(
            "gateway",
            [missing_source],
            tmp_path,
        )


def test_falls_back_to_component_layer_when_no_feature_test_exists(tmp_path: Path):
    _write(tmp_path, "apps/gateway/tests/services/test_existing_service.py")
    _write(tmp_path, "apps/gateway/tests/architecture/test_boundaries.py")

    assert select_pytest_targets(
        "gateway",
        ["apps/gateway/services/new_capability.py"],
        tmp_path,
    ) == [
        "apps/gateway/tests/architecture",
        "apps/gateway/tests/services",
    ]


def test_broad_mode_uses_smoke_targets_instead_of_full_service_suite(tmp_path: Path):
    _write(
        tmp_path,
        "apps/gateway/tests/architecture/test_access_management_import_boundaries.py",
    )
    _write(tmp_path, "apps/gateway/tests/services/test_large_suite.py")

    assert select_pytest_targets(
        "gateway",
        ["scripts/ci/changed_scope.py"],
        tmp_path,
        broad=True,
    ) == ["apps/gateway/tests/architecture"]


def test_shared_schema_without_direct_match_uses_schema_test_group(tmp_path: Path):
    _write(tmp_path, "apps/shared/tests/test_rag_schema.py")
    _write(tmp_path, "apps/shared/tests/services/test_permissions.py")

    assert select_pytest_targets(
        "shared",
        ["apps/shared/schemas/new_contract.py"],
        tmp_path,
    ) == ["apps/shared/tests/test_rag_schema.py"]


def test_root_selector_excludes_evaluation_and_load_tests(tmp_path: Path):
    _write(tmp_path, "tests/ci/test_changed_scope.py")
    _write(tmp_path, "tests/evaluation/test_rag_baseline.py")
    _write(tmp_path, "tests/load/test_load_runtime.py")

    assert select_pytest_targets(
        "root",
        [
            "tests/ci/test_changed_scope.py",
            "tests/evaluation/test_rag_baseline.py",
            "tests/load/test_load_runtime.py",
        ],
        tmp_path,
    ) == ["tests/ci/test_changed_scope.py"]


def test_memory_selector_runs_changed_layer_and_architecture_but_not_postgres(
    tmp_path: Path,
):
    domain_test = "apps/memory/tests/domain/test_conversation.py"
    architecture_test = "apps/memory/tests/architecture/test_boundaries.py"
    postgres_test = "apps/memory/tests/adapters/test_disposable_postgres.py"
    _write(tmp_path, domain_test)
    _write(tmp_path, architecture_test)
    _write(tmp_path, postgres_test)

    assert select_pytest_targets(
        "memory",
        ["apps/memory/domain/conversation.py"],
        tmp_path,
    ) == ["apps/memory/tests/architecture", domain_test]


def test_rejects_unknown_component(tmp_path: Path):
    with pytest.raises(ValueError, match="unsupported component"):
        select_pytest_targets("unknown", [], tmp_path)
