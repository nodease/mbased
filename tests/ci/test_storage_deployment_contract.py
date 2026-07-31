import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (REPOSITORY_ROOT / path).read_text(encoding="utf-8")


def _load(path: str) -> dict:
    return yaml.safe_load(_read(path))


def _render_helm(
    *,
    values_files: tuple[str, ...],
    set_values: tuple[str, ...] = (),
    set_json_values: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    if os.getenv("NODEASE_RUN_HELM_INTEGRATION_TESTS") != "1":
        pytest.skip("Helm integration contract is owned by deployment validation")

    helm = shutil.which("helm")
    if helm is None:
        pytest.fail("Deployment validation enabled the Helm contract without Helm")

    command = [
        helm,
        "template",
        "nodease-knowledge-worker-contract",
        str(REPOSITORY_ROOT / "infra" / "helm" / "moduly"),
    ]
    for values_file in values_files:
        command.extend(("-f", str(REPOSITORY_ROOT / values_file)))
    for value in set_values:
        command.extend(("--set", value))
    for value in set_json_values:
        command.extend(("--set-json", value))

    return subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _rendered_manifests(completed: subprocess.CompletedProcess[str]) -> list[dict]:
    assert completed.returncode == 0, "Helm Knowledge worker contract render failed"
    return [
        document
        for document in yaml.safe_load_all(completed.stdout)
        if isinstance(document, dict)
    ]


def _deployment_by_component(manifests: list[dict], component: str) -> dict:
    matching = [
        manifest
        for manifest in manifests
        if manifest.get("kind") == "Deployment"
        and manifest.get("metadata", {})
        .get("labels", {})
        .get("app.kubernetes.io/component")
        == component
    ]
    assert len(matching) == 1
    return matching[0]


def test_default_helm_values_render_local_profile_without_proxy_coordinates():
    completed = _render_helm(
        values_files=(),
        set_values=(
            "secrets.connectorTestAdmissionHmacKey="
            "ci-static-render-placeholder-32-bytes",
        ),
    )
    manifests = _rendered_manifests(completed)

    assert not any(
        manifest.get("metadata", {})
        .get("labels", {})
        .get("app.kubernetes.io/component")
        == "egress-proxy"
        for manifest in manifests
    )
    for component in ("gateway", "worker"):
        deployment = _deployment_by_component(manifests, component)
        container = next(
            item
            for item in deployment["spec"]["template"]["spec"]["containers"]
            if item["name"] == component
        )
        environment = {item["name"]: item for item in container.get("env", [])}
        assert environment["OUTBOUND_TRANSPORT_MODE"]["value"] == (
            "direct_pinned_internal_or_dedicated"
        )


def test_direct_gateway_example_uses_supported_storage_contract():
    example_lines = _read("dev/.env.example").splitlines()

    assert any(
        line.strip() in {"STORAGE_TYPE=LOCAL", "STORAGE_TYPE=CLOUD"}
        for line in example_lines
    ), "Direct Gateway example must select a supported storage mode"
    assert not any(
        re.search(r"\bPROD\b", line) for line in example_lines
    ), "Legacy PROD storage mode must not be documented"


def test_helm_storage_configuration_has_one_root_authority():
    defaults = _load("infra/helm/moduly/values.yaml")
    production = _load("infra/helm/moduly/values-production.yaml")

    assert defaults["storage"] == {
        "type": "LOCAL",
        "bucketName": "",
        "region": "",
    }
    assert production["storage"] == {
        "type": "CLOUD",
        "bucketName": "",
        "region": "",
    }

    for values in (defaults, production):
        assert "STORAGE_TYPE" not in values["gateway"]["env"]
        assert "S3_BUCKET_NAME" not in values["gateway"]["env"]
        assert "AWS_REGION" not in values["gateway"]["env"]
        assert "S3_BUCKET_NAME" not in values["worker"]["env"]
        assert "AWS_REGION" not in values["worker"]["env"]


def test_production_profile_enables_a_bounded_knowledge_worker_and_beat():
    defaults = _load("infra/helm/moduly/values.yaml")
    production = _load("infra/helm/moduly/values-production.yaml")
    template = _read(
        "infra/helm/moduly/templates/knowledge-worker-deployment.yaml"
    )
    helpers = _read("infra/helm/moduly/templates/_helpers.tpl")

    assert defaults["knowledgeWorker"]["enabled"] is False
    assert production["knowledgeWorker"] == {
        "enabled": True,
        "replicaCount": 2,
        "concurrency": 2,
    }
    assert production["beat"]["enabled"] is True
    assert 'include "moduly.validateKnowledgeWorker" .' in template
    assert 'define "moduly.validateKnowledgeWorker"' in helpers
    assert "knowledge worker requires the bundled Celery Beat recovery scheduler" in helpers
    assert "knowledgeWorker.replicaCount must be a positive integer" in helpers
    assert "knowledgeWorker.concurrency must be a positive integer" in helpers


def test_helm_storage_templates_use_root_authority_and_render_validation():
    helpers = _read("infra/helm/moduly/templates/_helpers.tpl")
    configmap = _read("infra/helm/moduly/templates/configmap.yaml")

    assert 'define "moduly.validateStorage"' in helpers
    assert "storage.type must be LOCAL or CLOUD" in helpers
    assert "storage.bucketName is required when storage.type is CLOUD" in helpers
    assert "storage.region is required when storage.type is CLOUD" in helpers
    assert "legacy component storage keys are unsupported" in helpers
    for legacy_key in ("STORAGE_TYPE", "S3_BUCKET_NAME", "AWS_REGION"):
        assert f'hasKey .Values.gateway.env "{legacy_key}"' in helpers
    for legacy_key in ("S3_BUCKET_NAME", "AWS_REGION"):
        assert f'hasKey .Values.worker.env "{legacy_key}"' in helpers
    assert 'include "moduly.validateStorage" .' in configmap
    assert 'include "moduly.storageType" .' in configmap
    assert '.Values.storage.bucketName | trim | quote' in configmap
    assert '.Values.storage.region | trim | quote' in configmap
    assert 'default "ap-northeast-2"' not in configmap

    for template_name in (
        "gateway-deployment.yaml",
        "worker-deployment.yaml",
        "knowledge-worker-deployment.yaml",
        "knowledge-storage-pvc.yaml",
        "NOTES.txt",
    ):
        template = _read(f"infra/helm/moduly/templates/{template_name}")
        assert ".Values.gateway.env.STORAGE_TYPE" not in template
        assert ".Values.gateway.env.S3_BUCKET_NAME" not in template
        assert ".Values.worker.env.S3_BUCKET_NAME" not in template


def test_schedule_dispatch_validation_uses_global_render_boundary():
    validation = _read(
        "infra/helm/moduly/templates/schedule-dispatch-validation.yaml"
    )

    assert 'include "moduly.validateScheduleDispatchMode" .' in validation
    for template_name in (
        "gateway-deployment.yaml",
        "worker-deployment.yaml",
    ):
        template = _read(f"infra/helm/moduly/templates/{template_name}")
        assert 'include "moduly.validateScheduleDispatchMode" .' not in template


@pytest.mark.parametrize("mode", ["claim", "drain"])
def test_helm_rejects_schedule_dispatch_when_workloads_are_disabled(mode: str):
    if os.getenv("NODEASE_RUN_HELM_INTEGRATION_TESTS") != "1":
        pytest.skip("Helm integration contract is owned by deployment validation")

    helm = shutil.which("helm")
    if helm is None:
        pytest.fail("Deployment validation enabled the Helm contract without Helm")

    completed = subprocess.run(
        [
            helm,
            "template",
            "nodease-schedule-contract",
            str(REPOSITORY_ROOT / "infra" / "helm" / "moduly"),
            "-f",
            str(REPOSITORY_ROOT / "tests" / "ci" / "fixtures" / "helm-values-ci.yaml"),
            "--set",
            "gateway.enabled=false",
            "--set",
            "worker.enabled=false",
            "--set",
            f"scheduleDispatch.mode={mode}",
        ],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode != 0
    assert (
        "non-disabled schedule dispatch is unsupported"
        in f"{completed.stdout}\n{completed.stderr}"
    )


def test_cloud_only_storage_env_references_share_one_template_condition():
    cloud_block_pattern = re.compile(
        r'\{\{- if eq \$storageType "CLOUD" \}\}(.*?)\{\{- end \}\}',
        re.DOTALL,
    )

    for template_name in (
        "gateway-deployment.yaml",
        "worker-deployment.yaml",
        "knowledge-worker-deployment.yaml",
    ):
        template = _read(f"infra/helm/moduly/templates/{template_name}")
        cloud_blocks = cloud_block_pattern.findall(template)
        assert any(
            "name: S3_BUCKET_NAME" in block and "name: AWS_REGION" in block
            for block in cloud_blocks
        ), f"{template_name} must gate both CLOUD-only environment references"


@pytest.mark.parametrize(
    ("values_files", "expected_cloud_env"),
    [
        (("tests/ci/fixtures/helm-values-ci.yaml",), False),
        (
            (
                "infra/helm/moduly/values-production.yaml",
                "tests/ci/fixtures/helm-values-ci.yaml",
            ),
            True,
        ),
    ],
)
def test_rendered_storage_consumers_reference_existing_configmap_keys(
    values_files: tuple[str, ...],
    expected_cloud_env: bool,
):
    if os.getenv("NODEASE_RUN_HELM_INTEGRATION_TESTS") != "1":
        pytest.skip("Helm integration contract is owned by deployment validation")

    helm = shutil.which("helm")
    if helm is None:
        pytest.fail("Deployment validation enabled the Helm contract without Helm")

    command = [
        helm,
        "template",
        "nodease-storage-contract",
        str(REPOSITORY_ROOT / "infra" / "helm" / "moduly"),
        "--set",
        "knowledgeWorker.enabled=true",
    ]
    if not expected_cloud_env:
        command.extend(("--set", "knowledgeWorker.localStorage.enabled=true"))
    for values_file in values_files:
        command.extend(("-f", str(REPOSITORY_ROOT / values_file)))

    completed = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert completed.returncode == 0, "Helm storage contract render failed"

    manifests = [
        document
        for document in yaml.safe_load_all(completed.stdout)
        if isinstance(document, dict)
    ]
    configmaps = {
        manifest["metadata"]["name"]: manifest.get("data", {})
        for manifest in manifests
        if manifest.get("kind") == "ConfigMap"
    }
    storage_consumers = {
        "gateway": None,
        "worker": None,
        "knowledge-worker": None,
    }

    for manifest in manifests:
        if manifest.get("kind") != "Deployment":
            continue
        for container in (
            manifest.get("spec", {})
            .get("template", {})
            .get("spec", {})
            .get("containers", [])
        ):
            container_name = container.get("name")
            if container_name not in storage_consumers:
                continue
            env_by_name = {item.get("name"): item for item in container.get("env", [])}
            storage_consumers[container_name] = env_by_name

            expected_names = {"STORAGE_TYPE"}
            if expected_cloud_env:
                expected_names.update({"S3_BUCKET_NAME", "AWS_REGION"})
            assert expected_names <= env_by_name.keys()
            if not expected_cloud_env:
                assert "S3_BUCKET_NAME" not in env_by_name
                assert "AWS_REGION" not in env_by_name

            for env_name in expected_names:
                reference = env_by_name[env_name]["valueFrom"]["configMapKeyRef"]
                assert reference["name"] in configmaps
                assert reference["key"] in configmaps[reference["name"]]

    assert all(value is not None for value in storage_consumers.values())


def test_ci_renders_complete_cloud_config_and_rejects_each_invalid_boundary():
    fixture = _load("tests/ci/fixtures/helm-values-ci.yaml")
    workflow = _read(".github/workflows/pr-quality-gate.yml")

    assert fixture["storage"] == {
        "bucketName": "ci-static-render-placeholder",
        "region": "region-ci-1",
    }
    assert "assert_invalid_storage_configuration" in workflow
    assert "storage.type=REMOTE" in workflow
    assert "storage.bucketName=" in workflow
    assert "storage.region=" in workflow
    assert "gateway.env.STORAGE_TYPE=CLOUD" in workflow
    assert "worker.env.S3_BUCKET_NAME=legacy-bucket" in workflow


def test_rendered_production_worker_has_consumer_and_recovery_closure():
    completed = _render_helm(
        values_files=(
            "infra/helm/moduly/values-production.yaml",
            "tests/ci/fixtures/helm-values-ci.yaml",
        )
    )
    manifests = _rendered_manifests(completed)
    worker = _deployment_by_component(manifests, "knowledge-worker")
    beat = _deployment_by_component(manifests, "beat")

    assert worker["spec"]["replicas"] == 2
    pod_spec = worker["spec"]["template"]["spec"]
    assert pod_spec["serviceAccountName"]
    assert [container["name"] for container in pod_spec["initContainers"]] == [
        "wait-for-knowledge-schema"
    ]

    container = next(
        item for item in pod_spec["containers"] if item["name"] == "knowledge-worker"
    )
    assert container["args"] == [
        "-A",
        "apps.gateway.knowledge_worker:app",
        "worker",
        "--loglevel=info",
        "--queues=knowledge",
        "--hostname=knowledge@%h",
        "--concurrency=2",
        "--prefetch-multiplier=1",
    ]
    assert container["readinessProbe"]["exec"]["command"] == [
        "python",
        "-m",
        "apps.gateway.knowledge_worker_health",
    ]
    assert container["readinessProbe"]["timeoutSeconds"] > 2
    assert "livenessProbe" not in container

    env_by_name = {item["name"]: item for item in container["env"]}
    assert {"STORAGE_TYPE", "S3_BUCKET_NAME", "AWS_REGION"} <= env_by_name.keys()
    assert "volumeMounts" not in container
    assert not any(
        volume.get("name") == "knowledge-uploads"
        for volume in pod_spec.get("volumes", [])
    )
    assert not any(manifest.get("kind") == "PersistentVolumeClaim" for manifest in manifests)

    assert beat["spec"]["replicas"] == 1
    assert beat["spec"]["strategy"] == {"type": "Recreate"}


@pytest.mark.parametrize(
    ("override", "expected_message"),
    [
        (
            "beat.enabled=false",
            "knowledge worker requires the bundled Celery Beat recovery scheduler",
        ),
        (
            "knowledgeWorker.replicaCount=0",
            "knowledgeWorker.replicaCount must be a positive integer",
        ),
        (
            "knowledgeWorker.concurrency=0",
            "knowledgeWorker.concurrency must be a positive integer",
        ),
    ],
)
def test_production_worker_rejects_invalid_operational_boundaries(
    override: str,
    expected_message: str,
):
    completed = _render_helm(
        values_files=(
            "infra/helm/moduly/values-production.yaml",
            "tests/ci/fixtures/helm-values-ci.yaml",
        ),
        set_values=(override,),
    )

    assert completed.returncode != 0
    assert expected_message in f"{completed.stdout}\n{completed.stderr}"


@pytest.mark.parametrize(
    ("override", "expected_message"),
    [
        (
            "egressProxy.service.httpsPort=43128",
            "egressProxy.service.httpsPort must be 3128",
        ),
        (
            "egressProxy.service.httpCompatiblePort=43129",
            "egressProxy.service.httpCompatiblePort must be 3129",
        ),
        (
            "egressProxy.service.connectorTcpPort=43130",
            "egressProxy.service.connectorTcpPort must be 3130",
        ),
    ],
)
def test_helm_rejects_proxy_listener_port_overrides(
    override: str,
    expected_message: str,
):
    completed = _render_helm(
        values_files=("tests/ci/fixtures/helm-values-ci.yaml",),
        set_values=(override,),
    )

    assert completed.returncode != 0
    assert expected_message in f"{completed.stdout}\n{completed.stderr}"


@pytest.mark.parametrize(
    ("overrides", "expected_message"),
    [
        (
            ("egressProxy.connectorAllowedPorts={22,22}",),
            "egressProxy.connectorAllowedPorts must contain 1 to 16 unique ports",
        ),
        (
            ("connectorTest.allowedPorts={15432}",),
            "connectorTest.allowedPorts must be included in egressProxy.connectorAllowedPorts",
        ),
    ],
)
def test_helm_rejects_invalid_connector_proxy_port_contract(
    overrides: tuple[str, ...],
    expected_message: str,
):
    completed = _render_helm(
        values_files=("tests/ci/fixtures/helm-values-ci.yaml",),
        set_values=overrides,
    )

    assert completed.returncode != 0
    assert expected_message in f"{completed.stdout}\n{completed.stderr}"


def test_helm_renders_deployment_managed_connector_proxy_port() -> None:
    completed = _render_helm(
        values_files=("tests/ci/fixtures/helm-values-ci.yaml",),
        set_values=(
            "egressProxy.connectorAllowedPorts={22,5432,15432}",
            "connectorTest.allowedPorts={5432,15432}",
        ),
    )

    assert completed.returncode == 0, f"{completed.stdout}\n{completed.stderr}"
    assert 'value: "22,5432,15432"' in completed.stdout
    assert "port: 15432" in completed.stdout


@pytest.mark.parametrize(
    ("override", "expected_message"),
    [
        (
            "gateway.enabled=false",
            "proxy-only frontend requires the bundled Gateway",
        ),
        (
            "frontend.env.API_URL=http://alternate-gateway.internal:8000",
            "proxy-only frontend API_URL must target the bundled Gateway",
        ),
    ],
)
def test_helm_rejects_frontend_backend_without_matching_proxy_only_egress(
    override: str,
    expected_message: str,
) -> None:
    completed = _render_helm(
        values_files=("tests/ci/fixtures/helm-values-ci.yaml",),
        set_values=(override,),
    )

    assert completed.returncode != 0
    assert expected_message in f"{completed.stdout}\n{completed.stderr}"


def test_helm_accepts_explicit_bundled_gateway_frontend_api_url() -> None:
    completed = _render_helm(
        values_files=("tests/ci/fixtures/helm-values-ci.yaml",),
        set_values=(
            "frontend.env.API_URL="
            "http://nodease-knowledge-worker-contract-moduly-gateway:8000",
        ),
    )

    assert completed.returncode == 0, f"{completed.stdout}\n{completed.stderr}"


@pytest.mark.parametrize(
    ("overrides", "empty_list_override", "expected_message"),
    [
        (
            (
                "worker.enabled=false",
                "postgresql.enabled=false",
            ),
            "worker.networkPolicy.externalDatabaseCidrs=[]",
            "worker.networkPolicy.externalDatabaseCidrs is required",
        ),
        (
            (
                "worker.enabled=false",
                "redis.enabled=false",
            ),
            "worker.networkPolicy.externalRedisCidrs=[]",
            "worker.networkPolicy.externalRedisCidrs is required",
        ),
    ],
)
def test_helm_rejects_missing_external_dependency_cidrs_when_worker_is_disabled(
    overrides: tuple[str, ...],
    empty_list_override: str,
    expected_message: str,
) -> None:
    completed = _render_helm(
        values_files=("tests/ci/fixtures/helm-values-ci.yaml",),
        set_values=overrides,
        set_json_values=(empty_list_override,),
    )

    assert completed.returncode != 0
    assert expected_message in f"{completed.stdout}\n{completed.stderr}"


@pytest.mark.parametrize(
    "external_database_cidrs",
    [
        '["0.0.0.0/1"]',
        '["0.0.0.0/1","128.0.0.0/1"]',
        '["93.184.216.0/24"]',
        '["10.0.0.0/7"]',
        '["fc::/7"]',
        '["2000::/3"]',
        '["::/1","8000::/1"]',
        '["127.0.0.1/32"]',
        '["169.254.169.254/32"]',
        '["ff00::1/128"]',
        '["0:0:0:0:0:0:0:0/128"]',
        '["0:0:0:0:0:0:0:1/128"]',
        '["::ffff:c000:0280/128"]',
        '["0:0:0:0:0:ffff:c000:0280/128"]',
        '["999.1.1.1/32"]',
        '[":2001:db8:0:0:0:0:0:1/128"]',
        '["2001:::1/128"]',
    ],
)
def test_helm_rejects_broad_or_unsafe_external_dependency_cidrs(
    external_database_cidrs: str,
) -> None:
    completed = _render_helm(
        values_files=("tests/ci/fixtures/helm-values-ci.yaml",),
        set_json_values=(
            f"worker.networkPolicy.externalDatabaseCidrs={external_database_cidrs}",
        ),
    )

    assert completed.returncode != 0
    assert (
        "worker.networkPolicy external dependency CIDRs must use valid private "
        "networks or exact public hosts" in f"{completed.stdout}\n{completed.stderr}"
    )


@pytest.mark.parametrize(
    "external_database_cidrs",
    [
        '["10.0.0.0/8"]',
        '["172.16.0.0/12"]',
        '["192.168.0.0/16"]',
        '["fc00::/7"]',
        '["93.184.216.34/32"]',
        '["2001:db8::1/128"]',
    ],
)
def test_helm_accepts_private_networks_and_exact_public_dependency_hosts(
    external_database_cidrs: str,
) -> None:
    completed = _render_helm(
        values_files=("tests/ci/fixtures/helm-values-ci.yaml",),
        set_json_values=(
            f"worker.networkPolicy.externalDatabaseCidrs={external_database_cidrs}",
        ),
    )

    assert completed.returncode == 0, f"{completed.stdout}\n{completed.stderr}"
