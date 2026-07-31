import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from apps.gateway.composition.connectors import (
    require_connector_test_security_ready,
)
from apps.gateway.middleware.webhook_query_redaction import (
    WebhookQueryRedactionMiddleware,
)


ROOT = Path(__file__).resolve().parents[4]


@dataclass(frozen=True)
class ConnectorDeploymentContract:
    admission_hmac_environment: str
    sensitive_paths: tuple[str, ...]
    excluded_paths: tuple[str, ...]
    nginx_path_pattern: str
    nginx_ingress_directives: tuple[str, ...]


CONTRACT = ConnectorDeploymentContract(
    admission_hmac_environment="CONNECTOR_TEST_ADMISSION_HMAC_KEY",
    sensitive_paths=(
        "/api/v1/connectors/test",
        "/api/v1/connectors/test/",
    ),
    excluded_paths=(
        "/api/v1/connectors/test/status",
        "/api/v1/connectors/testing",
    ),
    nginx_path_pattern=r"^/api/v1/connectors/test/?$",
    nginx_ingress_directives=(
        "access_log off;",
        "error_log /dev/null;",
        "client_max_body_size 32k;",
        "client_body_timeout 5s;",
        "proxy_request_buffering off;",
    ),
)


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _nginx_connector_block(config: str) -> str:
    marker = f"location ~ {CONTRACT.nginx_path_pattern} {{"
    assert config.count(marker) == 1
    start = config.index(marker)
    end = config.index("location /api", start)
    return config[start:end]


def test_production_admission_hmac_is_wired_across_supported_deployments() -> None:
    environment_name = CONTRACT.admission_hmac_environment
    production_environment = {
        "NODE_ENV": "production",
        "CONNECTOR_EGRESS_PROXY_URL": "http://egress-proxy:3130",
        "CONNECTOR_EGRESS_PROXY_ALLOWED_HOSTS": "egress-proxy",
        "CONNECTOR_EGRESS_POLICY_REVISION": "connector-egress-v1",
        "CONNECTOR_EGRESS_ALLOWED_PORTS": "22,5432",
    }

    with pytest.raises(RuntimeError, match=f"{environment_name} is required"):
        require_connector_test_security_ready(production_environment)

    require_connector_test_security_ready(
        {
            **production_environment,
            environment_name: "x" * 32,
        }
    )

    compose = _read("docker/docker-compose.yml")
    docker_example = _read("docker/.env.example")
    helm_secret = _read("infra/helm/moduly/templates/secrets.yaml")
    helm_gateway = _read("infra/helm/moduly/templates/gateway-deployment.yaml")
    helm_values = _read("infra/helm/moduly/values.yaml")
    helm_local_values = _read("infra/helm/moduly/values-local.yaml")

    compose_binding = f'{environment_name}: "${{{environment_name}:-}}"'
    assert compose.count(compose_binding) == 1
    assert re.findall(
        rf"^{re.escape(environment_name)}=$",
        docker_example,
        flags=re.MULTILINE,
    ) == [environment_name + "="]
    assert 'required "secrets.connectorTestAdmissionHmacKey is required' in (
        helm_secret
    )
    assert "must contain at least 32 bytes" in helm_secret
    assert environment_name in helm_secret
    assert environment_name in helm_gateway
    assert "connectorTestAdmissionHmacKey:" in helm_values
    assert "connectorTestAdmissionHmacKey:" in helm_local_values


def test_sensitive_path_contract_matches_nginx_and_asgi_sanitizer() -> None:
    nginx = _read("docker/nginx/nginx.conf")
    block = _nginx_connector_block(nginx)

    for directive in CONTRACT.nginx_ingress_directives:
        assert directive in block

    for path in CONTRACT.sensitive_paths:
        assert re.fullmatch(CONTRACT.nginx_path_pattern, path)
        assert WebhookQueryRedactionMiddleware._is_connector_test_path(path)

    for path in CONTRACT.excluded_paths:
        assert re.fullmatch(CONTRACT.nginx_path_pattern, path) is None
        assert not WebhookQueryRedactionMiddleware._is_connector_test_path(path)
