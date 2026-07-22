from pathlib import Path
import re

from scripts.ci.changed_scope import (
    _KNOWLEDGE_POSTGRES_PATTERNS,
    _WORKFLOW_POSTGRES_PATTERNS,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
QUALITY_GATE_PATH = REPOSITORY_ROOT / ".github" / "workflows" / "pr-quality-gate.yml"
HELM_CI_VALUES_PATH = (
    REPOSITORY_ROOT / "tests" / "ci" / "fixtures" / "helm-values-ci.yaml"
)
DOCKERFILE_CI_FIXTURE_PATH = (
    REPOSITORY_ROOT / "tests" / "ci" / "fixtures" / "dockerfile-smoke" / "Dockerfile"
)
COMPOSITE_ACTION_CI_FIXTURE_PATH = (
    REPOSITORY_ROOT
    / "tests"
    / "ci"
    / "fixtures"
    / "composite-action-smoke"
    / "action.yml"
)
KNOWLEDGE_POSTGRES_PATH = (
    REPOSITORY_ROOT / ".github" / "workflows" / "test-knowledge-runtime-postgres.yml"
)
WORKFLOW_POSTGRES_PATH = (
    REPOSITORY_ROOT
    / ".github"
    / "workflows"
    / "test-schedule-dispatch-postgres.yml"
)
PROTECTED_CI_WORKFLOWS = (
    QUALITY_GATE_PATH,
    REPOSITORY_ROOT / ".github" / "workflows" / "pr-ci-control-guard.yml",
    KNOWLEDGE_POSTGRES_PATH,
    WORKFLOW_POSTGRES_PATH,
    REPOSITORY_ROOT / ".github" / "workflows" / "test-agent-builder-postgres.yml",
    REPOSITORY_ROOT / ".github" / "workflows" / "test-memory-postgres.yml",
)
EXTERNAL_ACTION_PATTERN = re.compile(
    r"^\s*(?:-\s*)?uses:\s+(?P<action>[^@\s]+)@(?P<reference>[^\s#]+)"
)


def test_adr_registry_validation_runs_unconditionally_in_scope_job():
    workflow = QUALITY_GATE_PATH.read_text(encoding="utf-8")
    scope_job = workflow.split("\n  scope:\n", maxsplit=1)[1].split(
        "\n  alembic_graph:\n",
        maxsplit=1,
    )[0]

    assert (
        "      - name: Validate ADR registry\n"
        "        run: python -m scripts.ci.check_adr_registry\n"
    ) in scope_job


def test_deployment_validation_is_fail_closed_in_required_gate():
    workflow = QUALITY_GATE_PATH.read_text(encoding="utf-8")

    assert "      deployment_validation:" in workflow
    assert "  deployment_validation:" in workflow
    assert "    name: deployment-config-validation" in workflow
    assert "      - deployment_validation" in workflow
    assert (
        '--conditional "deployment-validation=${{ '
        "needs.scope.outputs.deployment_validation }}=${{ "
        'needs.deployment_validation.result }}"'
    ) in workflow


def test_actionlint_validates_only_changed_workflow_files():
    workflow = QUALITY_GATE_PATH.read_text(encoding="utf-8")

    assert "git diff --name-only --no-renames --diff-filter=ACMR -z" in workflow
    assert "'.github/workflows/*.yml'" in workflow
    assert "'.github/workflows/*.yaml'" in workflow
    assert 'actionlint@v1.7.12 "${workflow_files[@]}"' in workflow


def test_action_reference_matcher_supports_sequence_item_syntax():
    mapping_match = EXTERNAL_ACTION_PATTERN.match("      uses: actions/checkout@v4")
    sequence_match = EXTERNAL_ACTION_PATTERN.match("      - uses: actions/checkout@v4")

    assert mapping_match is not None
    assert sequence_match is not None
    assert sequence_match.group("reference") == "v4"


def test_ci_control_changes_force_all_supported_deployment_validators():
    workflow = QUALITY_GATE_PATH.read_text(encoding="utf-8")
    ci_control_block = workflow.split(
        'if [[ "$CI_CONTROL_CHANGED" == "true" ]]; then',
        maxsplit=1,
    )[1].split("exit 0", maxsplit=1)[0]

    for validator in (
        "actions_validation",
        "helm_validation",
        "support_surface_validation",
        "compose_validation",
        "dockerfile_validation",
    ):
        assert f"emit_boolean {validator} true" in ci_control_block


def test_ci_control_smoke_uses_protected_actionlint_and_dockerfile_fixture():
    workflow = QUALITY_GATE_PATH.read_text(encoding="utf-8")
    actionlint_block = workflow.split(
        "- name: Validate GitHub Actions workflows",
        maxsplit=1,
    )[1].split("- name: Set up Helm", maxsplit=1)[0]

    assert "ci_control_changed: ${{ steps.ci_control.outputs.changed }}" in workflow
    assert (
        "CI_CONTROL_CHANGED: ${{ needs.scope.outputs.ci_control_changed }}" in workflow
    )
    for workflow_path in PROTECTED_CI_WORKFLOWS:
        relative = workflow_path.relative_to(REPOSITORY_ROOT).as_posix()
        assert relative in actionlint_block
    assert "git ls-files -z -- '.github/workflows/*.yml'" not in actionlint_block
    assert DOCKERFILE_CI_FIXTURE_PATH.is_file()
    assert (
        "dockerfile_config_changed: "
        "${{ steps.ci_control.outputs.dockerfile_config_changed }}" in workflow
    )
    assert (
        "DOCKERFILE_CONFIG_CHANGED: "
        "${{ needs.scope.outputs.dockerfile_config_changed }}" in workflow
    )
    assert "tests/ci/fixtures/dockerfile-smoke/Dockerfile" in workflow
    assert "git ls-files -z -- ':(glob)**/Dockerfile'" not in workflow


def test_dockerfile_validation_runs_runtime_asset_contracts():
    workflow = QUALITY_GATE_PATH.read_text(encoding="utf-8")
    contract_step = workflow.split(
        "- name: Run Dockerfile deployment contract tests",
        maxsplit=1,
    )[1].split("\n  knowledge_postgres:", maxsplit=1)[0]

    assert "tests/ci/test_demo_seed_image_contract.py" in contract_step
    assert "tests/ci/test_tokenizer_runtime_contract.py" in contract_step


def test_ci_control_actionlint_smoke_also_validates_changed_workflows():
    workflow = QUALITY_GATE_PATH.read_text(encoding="utf-8")
    actionlint_block = workflow.split(
        "- name: Validate GitHub Actions workflows",
        maxsplit=1,
    )[1].split("- name: Set up Helm", maxsplit=1)[0]

    assert "mapfile -d '' changed_workflow_files" in actionlint_block
    assert 'workflow_files+=("${changed_workflow_files[@]}")' in actionlint_block
    assert "declare -A seen_workflow_files" in actionlint_block
    assert 'actionlint@v1.7.12 "${workflow_files[@]}"' in actionlint_block


def test_composite_action_metadata_uses_pinned_validator_and_fixture():
    workflow = QUALITY_GATE_PATH.read_text(encoding="utf-8")

    assert COMPOSITE_ACTION_CI_FIXTURE_PATH.is_file()
    assert (
        "uses: mpalmer/action-validator@"
        "c994f427b7c42cd5ecb1bc9315cb91c3d7d72e3d # v0.9.0" in workflow
    )
    assert 'version: "0.9.0"' in workflow
    assert "tests/ci/fixtures/composite-action-smoke/action.yml" in workflow
    assert ":(glob).github/actions/**/action.yml" in workflow
    assert ":(glob).github/actions/**/action.yaml" in workflow


def test_trusted_diff_detects_real_dockerfile_changes():
    workflow = QUALITY_GATE_PATH.read_text(encoding="utf-8")
    trusted_diff_block = workflow.split(
        "- name: Detect CI control changes outside the selector",
        maxsplit=1,
    )[1].split("- name: Classify changed paths", maxsplit=1)[0]

    assert (
        "dockerfile_config_changed: "
        "${{ steps.ci_control.outputs.dockerfile_config_changed }}" in workflow
    )
    assert "dockerfile_config_changed=false" in trusted_diff_block
    assert (
        "*/Dockerfile|Dockerfile|*/Dockerfile.*|Dockerfile.*|*.Dockerfile)"
        in trusted_diff_block
    )
    assert (
        'echo "dockerfile_config_changed=$dockerfile_config_changed"'
        in trusted_diff_block
    )
    assert trusted_diff_block.count('>> "$GITHUB_OUTPUT"') == 1
    assert "break" not in trusted_diff_block


def test_protected_postgres_workflows_are_detected_as_ci_control():
    workflow = QUALITY_GATE_PATH.read_text(encoding="utf-8")
    trusted_diff_block = workflow.split(
        "- name: Detect CI control changes outside the selector",
        maxsplit=1,
    )[1].split("- name: Classify changed paths", maxsplit=1)[0]

    for workflow_path in PROTECTED_CI_WORKFLOWS[2:]:
        relative = workflow_path.relative_to(REPOSITORY_ROOT).as_posix()
        assert relative in trusted_diff_block


def test_compose_validation_combines_variant_with_base_file():
    workflow = QUALITY_GATE_PATH.read_text(encoding="utf-8")

    assert "docker-compose.*.yml|docker-compose.*.yaml" in workflow
    assert (
        'docker compose --profile "*" --file "$base_path" --file "$path" '
        "config --quiet" in workflow
    )


def test_helm_validation_schema_checks_rendered_manifests():
    workflow = QUALITY_GATE_PATH.read_text(encoding="utf-8")

    assert "go run github.com/yannh/kubeconform/cmd/kubeconform@v0.7.0" in workflow
    assert "kubeconform/cmd/kubeconform@v0.8.0" not in workflow
    assert "-kubernetes-version 1.31.0" in workflow
    assert '"$rendered_manifest"' in workflow
    assert "kubectl create --dry-run=client" not in workflow


def test_helm_validation_requires_tracked_lock_before_dependency_build():
    workflow = QUALITY_GATE_PATH.read_text(encoding="utf-8")
    helm_block = workflow.split(
        "- name: Validate Helm chart",
        maxsplit=1,
    )[1].split(
        "- name: Reject unsupported or unapproved deployment surface",
        maxsplit=1,
    )[0]

    tracked_lock_guard = 'git ls-files --error-unmatch -- "$chart_lock"'
    dependency_build = "helm dependency build infra/helm/moduly"
    assert tracked_lock_guard in helm_block
    assert '[[ ! -f "$chart_lock" || -L "$chart_lock" ]]' in helm_block
    assert helm_block.index(tracked_lock_guard) < helm_block.index(dependency_build)


def test_helm_validation_runs_targeted_deployment_contract_tests():
    workflow = QUALITY_GATE_PATH.read_text(encoding="utf-8")
    deployment_block = workflow.split(
        "\n  deployment_validation:\n",
        maxsplit=1,
    )[1].split("\n  ci_required:\n", maxsplit=1)[0]

    helm_validation = "needs.scope.outputs.helm_validation == 'true'"
    contract_step = deployment_block.split(
        "- name: Run Helm deployment contract tests",
        maxsplit=1,
    )[1].split(
        "- name: Reject unsupported or unapproved deployment surface",
        maxsplit=1,
    )[0]

    assert helm_validation in contract_step
    assert 'NODEASE_RUN_HELM_INTEGRATION_TESTS: "1"' in contract_step
    assert "tests/ci/test_supported_deployment_surface.py" in contract_step
    assert "tests/ci/test_storage_deployment_contract.py" in contract_step
    assert deployment_block.index("helm dependency build infra/helm/moduly") < (
        deployment_block.index("- name: Run Helm deployment contract tests")
    )


def test_egress_proxy_runtime_contract_runs_only_for_its_selected_scope():
    workflow = QUALITY_GATE_PATH.read_text(encoding="utf-8")
    deployment_block = workflow.split(
        "deployment_validation:",
        maxsplit=1,
    )[1].split("ci_required:", maxsplit=1)[0]
    contract_step = deployment_block.split(
        "- name: Run egress proxy runtime contracts",
        maxsplit=1,
    )[1].split("- name:", maxsplit=1)[0]

    assert "needs.scope.outputs.egress_proxy_validation == 'true'" in contract_step
    assert 'NODEASE_RUN_EGRESS_PROXY_INTEGRATION: "1"' in contract_step
    assert "tests/ci/test_egress_proxy_runtime.py" in contract_step


def test_egress_proxy_scope_uses_pinned_kind_and_calico_network_policy_probe():
    workflow = QUALITY_GATE_PATH.read_text(encoding="utf-8")
    deployment_block = workflow.split(
        "\n  deployment_validation:\n",
        maxsplit=1,
    )[1].split("\n  ci_required:\n", maxsplit=1)[0]
    deployment_header = deployment_block.split("\n    steps:\n", maxsplit=1)[0]

    assert "timeout-minutes: 30" in deployment_header
    assert "sigs.k8s.io/kind@v0.31.0" in deployment_block
    assert (
        "kindest/node:v1.32.11@sha256:"
        "5fc52d52a7b9574015299724bd68f183702956aa4a2116ae75a63cb574b35af8"
    ) in deployment_block
    assert (
        "projectcalico/calico/0ca9d1b93644778cafdf1812f3dda02ac0c361e8/"
        "manifests/calico.yaml"
    ) in deployment_block
    assert "tests/ci/test_egress_proxy_kubernetes.py" in deployment_block
    assert 'NODEASE_RUN_EGRESS_PROXY_KUBERNETES: "1"' in deployment_block


def test_unsupported_deployment_surface_guard_is_wired_into_quality_gate():
    workflow = QUALITY_GATE_PATH.read_text(encoding="utf-8")

    assert "support_surface_validation" in workflow
    assert "python -m scripts.ci.check_supported_deployment_surface" in workflow
    scope_block = workflow.split("\n  scope:\n", maxsplit=1)[1].split(
        "\n  python_lint:\n", maxsplit=1
    )[0]
    assert "Enforce supported deployment execution closure" in scope_block
    assert scope_block.index(
        "python -m scripts.ci.check_supported_deployment_surface"
    ) < scope_block.index("python -m scripts.ci.changed_scope")
    assert "kubernetes_validation" not in workflow
    assert "terraform_validation" not in workflow
    assert "terraform_config_changed" not in workflow


def test_dockerfile_validation_preserves_rename_source_paths():
    workflow = QUALITY_GATE_PATH.read_text(encoding="utf-8")
    dockerfile_block = workflow.split(
        "- name: Validate changed Dockerfiles",
        maxsplit=1,
    )[1].split("ci_required:", maxsplit=1)[0]

    assert (
        'git diff --name-only --no-renames -z "$BASE_SHA" "$HEAD_SHA" --'
        in dockerfile_block
    )
    assert '--build-arg "BUILDKIT_DOCKERFILE_CHECK=error=true"' in dockerfile_block
    assert "Dockerfile|Dockerfile.*|*.Dockerfile)" in dockerfile_block


def test_dockerfile_validation_runs_demo_seed_image_contract():
    workflow = QUALITY_GATE_PATH.read_text(encoding="utf-8")
    deployment_block = workflow.split(
        "deployment_validation:",
        maxsplit=1,
    )[1].split("ci_required:", maxsplit=1)[0]
    helm_validation = "needs.scope.outputs.helm_validation == 'true'"
    dockerfile_validation = "needs.scope.outputs.dockerfile_validation == 'true'"

    for step_name in (
        "Set up Python for deployment contract tests",
        "Install uv for deployment contract tests",
        "Install deployment contract test dependencies",
    ):
        step_block = deployment_block.split(
            f"- name: {step_name}",
            maxsplit=1,
        )[1].split("- name:", maxsplit=1)[0]
        assert helm_validation in step_block
        assert dockerfile_validation in step_block

    contract_step = deployment_block.split(
        "- name: Run Dockerfile deployment contract tests",
        maxsplit=1,
    )[1].split("- name:", maxsplit=1)[0]
    assert dockerfile_validation in contract_step
    assert "tests/ci/test_demo_seed_image_contract.py" in contract_step


def test_helm_validation_registers_chart_dependency_repositories():
    workflow = QUALITY_GATE_PATH.read_text(encoding="utf-8")

    assert "helm repo add bitnami https://charts.bitnami.com/bitnami" in workflow
    assert (
        "helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx"
        in workflow
    )
    assert workflow.index("helm repo add bitnami") < workflow.index(
        "helm dependency build infra/helm/moduly"
    )


def test_helm_static_render_fixture_uses_non_routable_database_host():
    values = HELM_CI_VALUES_PATH.read_text(encoding="utf-8")

    assert 'externalHost: "postgresql.ci.invalid"' in values


def test_knowledge_postgres_workflow_runs_durable_ingestion_contract():
    workflow = KNOWLEDGE_POSTGRES_PATH.read_text(encoding="utf-8")

    assert 'NODEASE_RUN_DISPOSABLE_DB_TEST: "1"' in workflow
    assert (
        "apps/gateway/tests/adapters/db/"
        "test_knowledge_document_ingestion_repository_postgres.py"
    ) in workflow
    assert ".venv-ci-knowledge-ingestion/bin/python -m pytest" in workflow
    assert '-e "apps/gateway[dev]"' in workflow


def test_knowledge_postgres_dev_push_tracks_all_durable_ingestion_services():
    workflow = KNOWLEDGE_POSTGRES_PATH.read_text(encoding="utf-8")
    push_paths = workflow.split("  push:", maxsplit=1)[1].split(
        "permissions:",
        maxsplit=1,
    )[0]

    assert '- "apps/shared/services/knowledge_ingestion_*.py"' in push_paths


def test_knowledge_postgres_dev_push_covers_selector_patterns():
    workflow = KNOWLEDGE_POSTGRES_PATH.read_text(encoding="utf-8")
    push_paths = workflow.split("  push:", maxsplit=1)[1].split(
        "permissions:",
        maxsplit=1,
    )[0]
    configured_paths = set(
        re.findall(r'^\s*- "([^"]+)"', push_paths, flags=re.MULTILINE)
    )

    assert set(_KNOWLEDGE_POSTGRES_PATTERNS) <= configured_paths


def test_workflow_postgres_dev_push_covers_selector_patterns():
    workflow = WORKFLOW_POSTGRES_PATH.read_text(encoding="utf-8")
    push_paths = workflow.split("  push:", maxsplit=1)[1].split(
        "permissions:",
        maxsplit=1,
    )[0]
    configured_paths = set(
        re.findall(r'^\s*- "([^"]+)"', push_paths, flags=re.MULTILINE)
    )

    assert set(_WORKFLOW_POSTGRES_PATTERNS) <= configured_paths


def test_protected_ci_workflows_pin_external_actions_to_commit_shas():
    violations: list[str] = []

    for workflow_path in PROTECTED_CI_WORKFLOWS:
        for line_number, line in enumerate(
            workflow_path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            match = EXTERNAL_ACTION_PATTERN.match(line)
            if match is None:
                continue
            reference = match.group("reference")
            if re.fullmatch(r"[0-9a-f]{40}", reference) is None:
                relative = workflow_path.relative_to(REPOSITORY_ROOT).as_posix()
                violations.append(
                    f"{relative}:{line_number} uses mutable reference {reference}"
                )

    assert violations == []
