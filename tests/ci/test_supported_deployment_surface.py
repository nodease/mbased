import subprocess
from pathlib import Path

import pytest
import yaml

from scripts.ci.check_supported_deployment_surface import (
    find_unsupported_deployment_paths,
    tracked_paths,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_repository_contains_only_approved_deployment_surfaces():
    assert (
        find_unsupported_deployment_paths(
            tracked_paths(REPOSITORY_ROOT),
            repo_root=REPOSITORY_ROOT,
            require_complete_workflow_allowlist=True,
        )
        == []
    )


def test_support_guard_rejects_legacy_and_unapproved_surfaces():
    paths = [
        ".github/workflows/deploy-dev-namespace.yml",
        ".github/workflows/deploy-dev-namespace.yaml",
        ".github/workflows/deploy-eks-frontend.yml",
        ".github/workflows/deploy-eks-gateway.yml",
        ".github/workflows/deploy-eks-gateway.yaml",
        ".github/workflows/deploy-eks-logger.yml",
        ".github/workflows/deploy-eks-sandbox.yml",
        ".github/workflows/deploy-eks-worker.yml",
        ".github/workflows/deploy-eks-schedule-coordinated.yml",
        ".github/workflows/deploy-eks-reintroduced.yml",
        ".github/workflows/deploy-prod.yml",
        ".github/workflows/release-renamed.yaml",
        "infra/k8s/namespaces/default/gateway.yaml",
        "infra/terraform/eks.tf",
        "infra/helm/moduly/values.yaml",
        "docker/docker-compose.yml",
    ]

    assert find_unsupported_deployment_paths(paths) == paths[:14]


def test_support_guard_accepts_allowlisted_provider_neutral_workflow(tmp_path):
    workflow_path = ".github/workflows/publish-images.yml"
    target = tmp_path / workflow_path
    target.parent.mkdir(parents=True)
    target.write_text(
        "name: Publish\nsteps:\n  - run: echo provider-neutral\n",
        encoding="utf-8",
    )
    assert (
        find_unsupported_deployment_paths([workflow_path], repo_root=tmp_path) == []
    )


def test_support_guard_accepts_provider_neutral_delegated_script(tmp_path):
    workflow_path = ".github/workflows/publish-images.yml"
    workflow = tmp_path / workflow_path
    script = tmp_path / "scripts" / "publish.sh"
    workflow.parent.mkdir(parents=True)
    script.parent.mkdir(parents=True)
    workflow.write_text(
        "name: Publish\nsteps:\n  - run: ./scripts/publish.sh\n",
        encoding="utf-8",
    )
    script.write_text("docker push example.invalid/image\n", encoding="utf-8")

    assert find_unsupported_deployment_paths(
        [workflow_path],
        repo_root=tmp_path,
    ) == []


def test_support_guard_rejects_provider_specific_delegated_script(tmp_path):
    workflow_path = ".github/workflows/publish-images.yml"
    workflow = tmp_path / workflow_path
    script = tmp_path / "scripts" / "deploy.sh"
    workflow.parent.mkdir(parents=True)
    script.parent.mkdir(parents=True)
    workflow.write_text(
        "name: Publish\nsteps:\n  - run: ./scripts/deploy.sh\n",
        encoding="utf-8",
    )
    script.write_text("aws eks update-kubeconfig --name example\n", encoding="utf-8")

    assert find_unsupported_deployment_paths(
        [workflow_path],
        repo_root=tmp_path,
    ) == [workflow_path]


@pytest.mark.parametrize(
    "delegated_command",
    [
        "./scripts/deploy",
        "scripts/deploy",
        "${{ github.workspace }}/scripts/deploy",
        "$GITHUB_WORKSPACE/scripts/deploy",
    ],
)
def test_support_guard_rejects_provider_specific_extensionless_script_delegation(
    tmp_path,
    delegated_command,
):
    workflow_path = ".github/workflows/publish-images.yml"
    workflow = tmp_path / workflow_path
    script = tmp_path / "scripts" / "deploy"
    workflow.parent.mkdir(parents=True)
    script.parent.mkdir(parents=True)
    workflow.write_text(
        f"name: Publish\nsteps:\n  - run: {delegated_command}\n",
        encoding="utf-8",
    )
    script.write_text("aws eks update-kubeconfig --name example\n", encoding="utf-8")

    assert find_unsupported_deployment_paths(
        [workflow_path],
        repo_root=tmp_path,
    ) == [workflow_path]


def test_support_guard_accepts_provider_neutral_extensionless_script(tmp_path):
    workflow_path = ".github/workflows/publish-images.yml"
    workflow = tmp_path / workflow_path
    script = tmp_path / "scripts" / "publish"
    workflow.parent.mkdir(parents=True)
    script.parent.mkdir(parents=True)
    workflow.write_text(
        "name: Publish\nsteps:\n  - run: ./scripts/publish\n",
        encoding="utf-8",
    )
    script.write_text("echo provider-neutral\n", encoding="utf-8")

    assert find_unsupported_deployment_paths(
        [workflow_path],
        repo_root=tmp_path,
    ) == []


@pytest.mark.parametrize(
    "workspace_prefix",
    [
        "${{ github.workspace }}",
        "$GITHUB_WORKSPACE",
    ],
)
def test_support_guard_rejects_provider_specific_workspace_script_delegation(
    tmp_path,
    workspace_prefix,
):
    workflow_path = ".github/workflows/publish-images.yml"
    workflow = tmp_path / workflow_path
    script = tmp_path / "scripts" / "deploy.sh"
    workflow.parent.mkdir(parents=True)
    script.parent.mkdir(parents=True)
    workflow.write_text(
        f"name: Publish\nsteps:\n  - run: {workspace_prefix}/scripts/deploy.sh\n",
        encoding="utf-8",
    )
    script.write_text("aws eks update-kubeconfig --name example\n", encoding="utf-8")

    assert find_unsupported_deployment_paths(
        [workflow_path],
        repo_root=tmp_path,
    ) == [workflow_path]


def test_support_guard_rejects_provider_specific_nested_delegation(tmp_path):
    workflow_path = ".github/workflows/publish-images.yml"
    workflow = tmp_path / workflow_path
    wrapper = tmp_path / "scripts" / "publish.sh"
    deploy = tmp_path / "scripts" / "deploy.py"
    workflow.parent.mkdir(parents=True)
    wrapper.parent.mkdir(parents=True)
    workflow.write_text(
        "name: Publish\nsteps:\n  - run: bash scripts/publish.sh\n",
        encoding="utf-8",
    )
    wrapper.write_text("python scripts/deploy.py\n", encoding="utf-8")
    deploy.write_text(
        "import subprocess\nsubprocess.run(['eksctl', 'create', 'cluster'])\n",
        encoding="utf-8",
    )

    assert find_unsupported_deployment_paths(
        [workflow_path],
        repo_root=tmp_path,
    ) == [workflow_path]


def test_support_guard_rejects_provider_specific_python_module_delegation(tmp_path):
    workflow_path = ".github/workflows/publish-images.yml"
    workflow = tmp_path / workflow_path
    module = tmp_path / "scripts" / "deploy.py"
    workflow.parent.mkdir(parents=True)
    module.parent.mkdir(parents=True)
    workflow.write_text(
        "name: Publish\nsteps:\n  - run: python -m scripts.deploy\n",
        encoding="utf-8",
    )
    module.write_text("print('account.dkr.ecr.region.amazonaws.com')\n", encoding="utf-8")

    assert find_unsupported_deployment_paths(
        [workflow_path],
        repo_root=tmp_path,
    ) == [workflow_path]


def test_support_guard_rejects_script_delegated_through_local_action(tmp_path):
    workflow_path = ".github/workflows/publish-images.yml"
    action_path = ".github/actions/deploy/action.yml"
    workflow = tmp_path / workflow_path
    action = tmp_path / action_path
    script = action.parent / "deploy.sh"
    workflow.parent.mkdir(parents=True)
    action.parent.mkdir(parents=True)
    workflow.write_text(
        "name: Publish\nsteps:\n  - uses: ./.github/actions/deploy\n",
        encoding="utf-8",
    )
    action.write_text(
        "name: Deploy helper\nruns:\n  using: composite\n  steps:\n"
        "    - shell: bash\n"
        "      run: ${{ github.action_path }}/deploy.sh\n",
        encoding="utf-8",
    )
    script.write_text("eksctl create cluster --name example\n", encoding="utf-8")

    assert find_unsupported_deployment_paths(
        [workflow_path],
        repo_root=tmp_path,
    ) == [workflow_path]


def test_support_guard_rejects_extensionless_script_delegated_through_local_action(
    tmp_path,
):
    workflow_path = ".github/workflows/publish-images.yml"
    action_path = ".github/actions/deploy/action.yml"
    workflow = tmp_path / workflow_path
    action = tmp_path / action_path
    script = action.parent / "deploy"
    workflow.parent.mkdir(parents=True)
    action.parent.mkdir(parents=True)
    workflow.write_text(
        "name: Publish\nsteps:\n  - uses: ./.github/actions/deploy\n",
        encoding="utf-8",
    )
    action.write_text(
        "name: Deploy helper\nruns:\n  using: composite\n  steps:\n"
        "    - shell: bash\n"
        "      run: ${{ github.action_path }}/deploy\n",
        encoding="utf-8",
    )
    script.write_text("eksctl create cluster --name example\n", encoding="utf-8")

    assert find_unsupported_deployment_paths(
        [workflow_path],
        repo_root=tmp_path,
    ) == [workflow_path]


@pytest.mark.parametrize(
    "delegated_command",
    [
        "./scripts/missing.sh",
        "../outside/deploy.sh",
        "./tools/deploy.sh",
    ],
)
def test_support_guard_fails_closed_for_unresolvable_local_delegation(
    tmp_path,
    delegated_command,
):
    workflow_path = ".github/workflows/publish-images.yml"
    workflow = tmp_path / workflow_path
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        f"name: Publish\nsteps:\n  - run: {delegated_command}\n",
        encoding="utf-8",
    )

    assert find_unsupported_deployment_paths(
        [workflow_path],
        repo_root=tmp_path,
    ) == [workflow_path]


def test_support_guard_accepts_provider_neutral_composite_action(tmp_path):
    action_path = ".github/actions/deploy/action.yml"
    target = tmp_path / action_path
    target.parent.mkdir(parents=True)
    target.write_text(
        "name: Deploy helper\nruns:\n  using: composite\n  steps:\n"
        "    - shell: bash\n      run: echo provider-neutral\n",
        encoding="utf-8",
    )

    assert find_unsupported_deployment_paths(
        [action_path],
        repo_root=tmp_path,
    ) == []


def test_support_guard_rejects_stale_allowlist_entry_after_workflow_removal():
    tracked_approved_workflows = [
        ".github/workflows/pr-ci-control-guard.yml",
        ".github/workflows/pr-quality-gate.yml",
        ".github/workflows/test-agent-builder-postgres.yml",
        ".github/workflows/test-knowledge-runtime-postgres.yml",
        ".github/workflows/test-memory-postgres.yml",
        ".github/workflows/test-schedule-dispatch-postgres.yml",
    ]

    assert find_unsupported_deployment_paths(
        tracked_approved_workflows,
        require_complete_workflow_allowlist=True,
    ) == [".github/workflows/publish-images.yml"]


def test_support_guard_fails_closed_for_non_utf8_allowlisted_workflow(tmp_path):
    workflow_path = ".github/workflows/publish-images.yml"
    target = tmp_path / workflow_path
    target.parent.mkdir(parents=True)
    target.write_bytes(b"\xff\xfe")

    assert find_unsupported_deployment_paths(
        [workflow_path],
        repo_root=tmp_path,
    ) == [workflow_path]


def test_support_guard_fails_closed_for_non_utf8_composite_action(tmp_path):
    action_path = ".github/actions/deploy/action.yaml"
    target = tmp_path / action_path
    target.parent.mkdir(parents=True)
    target.write_bytes(b"\xff\xfe")

    assert find_unsupported_deployment_paths(
        [action_path],
        repo_root=tmp_path,
    ) == [action_path]


@pytest.mark.parametrize(
    "provider_specific_step",
    [
        "uses: aws-actions/configure-aws-credentials@v4",
        "uses: aws-actions/amazon-ecr-login@v2",
        "run: aws eks update-kubeconfig --name example",
        "run: eksctl create cluster --name example",
        "run: docker push account.dkr.ecr.region.amazonaws.com/image",
        "run: echo eks.amazonaws.com/role-arn",
    ],
)
def test_support_guard_rejects_provider_specific_content_in_allowlisted_workflow(
    tmp_path,
    provider_specific_step,
):
    workflow_path = ".github/workflows/publish-images.yml"
    target = tmp_path / workflow_path
    target.parent.mkdir(parents=True)
    target.write_text(
        f"name: Publish\nsteps:\n  - {provider_specific_step}\n",
        encoding="utf-8",
    )

    assert find_unsupported_deployment_paths(
        [workflow_path],
        repo_root=tmp_path,
    ) == [workflow_path]


@pytest.mark.parametrize(
    "provider_specific_step",
    [
        "uses: aws-actions/configure-aws-credentials@v4",
        "uses: aws-actions/amazon-ecr-login@v2",
        "run: aws eks update-kubeconfig --name example",
        "run: eksctl create cluster --name example",
        "run: docker push account.dkr.ecr.region.amazonaws.com/image",
        "run: echo eks.amazonaws.com/role-arn",
    ],
)
def test_support_guard_rejects_provider_specific_content_in_composite_action(
    tmp_path,
    provider_specific_step,
):
    action_path = ".github/actions/deploy/action.yml"
    target = tmp_path / action_path
    target.parent.mkdir(parents=True)
    target.write_text(
        "name: Deploy helper\nruns:\n  using: composite\n  steps:\n"
        f"    - shell: bash\n      {provider_specific_step}\n",
        encoding="utf-8",
    )

    assert find_unsupported_deployment_paths(
        [action_path],
        repo_root=tmp_path,
    ) == [action_path]


def test_production_values_are_provider_neutral():
    values = (
        REPOSITORY_ROOT / "infra" / "helm" / "moduly" / "values-production.yaml"
    ).read_text(encoding="utf-8")

    forbidden_markers = (
        "AWS EKS",
        ".dkr.ecr.",
        "eks.amazonaws.com/role-arn",
        "alb.ingress.kubernetes.io",
        'storageClass: "gp3"',
        "912894834396",
        "moduly-ai.cloud",
    )
    for marker in forbidden_markers:
        assert marker not in values
    assert "Provider-neutral production reference" in values
    assert "enabled: false  # Operator must explicitly configure ingress." in values


def test_image_publisher_chart_and_helm_defaults_use_nodease_repository_contract():
    publisher = (
        REPOSITORY_ROOT / ".github" / "workflows" / "publish-images.yml"
    ).read_text(encoding="utf-8")
    chart = yaml.safe_load(
        (REPOSITORY_ROOT / "infra" / "helm" / "moduly" / "Chart.yaml").read_text(
            encoding="utf-8"
        )
    )
    values = (
        REPOSITORY_ROOT / "infra" / "helm" / "moduly" / "values.yaml"
    ).read_text(encoding="utf-8")

    assert "ghcr.io/nodease" in publisher
    assert "ghcr.io/jungle-scope" not in publisher
    assert chart["home"] == "https://github.com/nodease/mbased"
    assert chart["sources"] == ["https://github.com/nodease/mbased"]
    assert "ghcr.io/nodease" in values
    assert "ghcr.io/jungle-scope" not in values


@pytest.mark.parametrize(
    "secret_path",
    [
        "apps/infra/k8s/secrets.yaml",
        "infra/k8s/secrets.yaml",
    ],
)
def test_legacy_kubernetes_secret_paths_remain_ignored(secret_path):
    completed = subprocess.run(
        ["git", "check-ignore", "--no-index", "--quiet", "--", secret_path],
        cwd=REPOSITORY_ROOT,
        check=False,
    )

    assert completed.returncode == 0


def test_storage_consumers_use_the_chart_service_account_contract():
    production_values = yaml.safe_load(
        (
            REPOSITORY_ROOT
            / "infra"
            / "helm"
            / "moduly"
            / "values-production.yaml"
        ).read_text(encoding="utf-8")
    )
    service_account = (
        REPOSITORY_ROOT
        / "infra"
        / "helm"
        / "moduly"
        / "templates"
        / "serviceaccount.yaml"
    ).read_text(encoding="utf-8")

    assert "serviceAccount" not in production_values["gateway"]
    assert production_values["serviceAccount"] == {
        "create": True,
        "annotations": {},
        "name": "",
    }
    assert ".Values.serviceAccount.annotations" in service_account

    for template_name in (
        "gateway-deployment.yaml",
        "worker-deployment.yaml",
        "knowledge-worker-deployment.yaml",
    ):
        deployment = (
            REPOSITORY_ROOT
            / "infra"
            / "helm"
            / "moduly"
            / "templates"
            / template_name
        ).read_text(encoding="utf-8")
        assert 'serviceAccountName: {{ include "moduly.serviceAccountName" . }}' in (
            deployment
        )
