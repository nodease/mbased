from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[4]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_production_values_keep_browser_and_server_api_origins_separate():
    values = yaml.safe_load(_read("infra/helm/moduly/values-production.yaml"))

    public_origin = values["frontend"]["env"].get("NEXT_PUBLIC_API_URL")
    server_origin = values["frontend"]["env"].get("API_URL")
    configmap = _read("infra/helm/moduly/templates/configmap.yaml")

    assert public_origin in {None, ""}
    assert server_origin in {None, ""}
    assert '{{ printf "http://%s-gateway:%d"' in configmap


def test_local_container_profiles_use_same_origin_browser_api():
    compose = yaml.safe_load(_read("docker/docker-compose.yml"))
    frontend = compose["services"]["frontend"]
    build_args = frontend["build"].get("args", {})
    environment = frontend.get("environment", {})
    local_values = yaml.safe_load(_read("infra/helm/moduly/values-local.yaml"))

    assert build_args.get("NEXT_PUBLIC_API_URL") in {None, ""}
    assert environment.get("NEXT_PUBLIC_API_URL") in {None, ""}
    assert environment["API_URL"].startswith("http://")
    assert local_values["frontend"]["env"].get("NEXT_PUBLIC_API_URL") in {
        None,
        "",
    }
    assert local_values["frontend"]["env"]["API_URL"].startswith("http://")


def test_production_values_use_https_only_credentialed_cors():
    values = yaml.safe_load(_read("infra/helm/moduly/values-production.yaml"))
    origins = [
        origin.strip()
        for origin in values["gateway"]["env"]["CORS_ORIGINS"].split(",")
    ]

    assert origins
    assert all(origin.startswith("https://") for origin in origins)
    assert all("localhost" not in origin and "127.0.0.1" not in origin for origin in origins)


def test_default_helm_values_use_one_development_cors_profile():
    source = _read("infra/helm/moduly/values.yaml")
    values = yaml.safe_load(source)
    environment = values["gateway"]["env"]
    origins = environment["CORS_ORIGINS"].split(",")

    assert source.count('    NODE_ENV: "') == 1
    assert environment["NODE_ENV"] == "development"
    assert all(origin.startswith("http://") for origin in origins)
    assert all(
        "localhost" in origin or "127.0.0.1" in origin for origin in origins
    )


def test_local_dev_script_explicitly_selects_development_environment():
    source = _read("scripts/dev.sh")

    assert "export NODE_ENV=development" in source


def test_helm_does_not_fallback_public_api_url_to_cluster_http():
    configmap = _read("infra/helm/moduly/templates/configmap.yaml")
    deployment = _read("infra/helm/moduly/templates/frontend-deployment.yaml")

    assert 'NEXT_PUBLIC_API_URL: {{ printf "http://' not in configmap
    assert "{{- if .Values.frontend.env.NEXT_PUBLIC_API_URL }}" in deployment
    assert "optional: true" in deployment


def test_production_reference_keeps_ingress_disabled_until_operator_configures_https():
    values = yaml.safe_load(_read("infra/helm/moduly/values-production.yaml"))
    ingress = values["ingress"]

    assert ingress["enabled"] is False
    assert ingress["className"] == ""
    assert ingress["annotations"] == {}
    assert ingress["hosts"] == []
    assert ingress["tls"] == []


def test_helm_ingress_routes_same_origin_api_directly_to_gateway():
    values = yaml.safe_load(_read("infra/helm/moduly/values.yaml"))

    assert values["ingress"]["hosts"]
    for host in values["ingress"]["hosts"]:
        routes = {route["path"]: route["backend"] for route in host["paths"]}
        assert routes["/api"] == "gateway"
        assert routes["/"] == "frontend"


def test_docker_entrypoint_routes_same_origin_api_directly_to_gateway():
    nginx = _read("docker/nginx/nginx.conf")

    assert "location /api {" in nginx
    assert "proxy_pass http://gateway:8000;" in nginx


@pytest.mark.parametrize(
    "relative_path",
    [
        "apps/gateway/services/ingestion/processors/api_processor.py",
        "apps/gateway/services/ingestion/processors/file_processor.py",
        "apps/gateway/services/knowledge_document_content_service.py",
        "apps/shared/services/embedding_service.py",
        "apps/workflow_engine/workflow/nodes/file_extraction/file_extraction_node.py",
    ],
)
def test_mba_178_consumers_do_not_create_direct_http_clients(relative_path):
    source = _read(relative_path)

    assert "requests.get" not in source
    assert "requests.post" not in source
    assert "httpx.Client(" not in source
    assert "httpx.AsyncClient(" not in source
    assert "openai.OpenAI(" not in source


@pytest.mark.parametrize(
    "relative_path",
    [
        "apps/gateway/services/llm_service.py",
        "apps/workflow_engine/services/llm_service.py",
    ],
)
def test_model_discovery_uses_the_registered_guarded_operation(relative_path):
    source = _read(relative_path)

    assert "requests.get" not in source
    assert "safe_http_request(" in source
    assert "operation_id=LLM_MODEL_DISCOVERY" in source
