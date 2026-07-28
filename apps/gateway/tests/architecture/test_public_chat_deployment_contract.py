from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
ROLLOUT_MODE = "PUBLIC_CHAT_CONVERSATION_ROLLOUT_MODE"


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_standard_deployments_wire_public_chat_rollout_mode_to_gateway():
    compose = _read("docker/docker-compose.yml")
    values = _read("infra/helm/moduly/values.yaml")
    configmap = _read("infra/helm/moduly/templates/configmap.yaml")
    gateway = _read("infra/helm/moduly/templates/gateway-deployment.yaml")

    assert (
        f'{ROLLOUT_MODE}: "${{{ROLLOUT_MODE}:-compatibility}}"'
        in compose
    )
    assert f'{ROLLOUT_MODE}: "compatibility"' in values
    assert f"{ROLLOUT_MODE}:" in configmap
    assert f".Values.gateway.env.{ROLLOUT_MODE}" in configmap
    assert f"- name: {ROLLOUT_MODE}" in gateway
    assert f"key: {ROLLOUT_MODE}" in gateway


def test_public_chat_proxy_owns_timeout_and_safe_oversize_contract():
    nginx = _read("docker/nginx/nginx.conf")
    location = nginx.split(
        "location ~ ^/api/v1/run-public/[^/]+/chat/?$ {",
        1,
    )[1].split("\n    }", 1)[0]
    oversize = nginx.split(
        "location @public_chat_request_too_large {",
        1,
    )[1].split("\n    }", 1)[0]

    assert "proxy_connect_timeout 5s;" in location
    assert "proxy_send_timeout 610s;" in location
    assert "proxy_read_timeout 610s;" in location
    assert "error_page 413 = @public_chat_request_too_large;" in location
    assert "default_type application/json;" in oversize
    assert 'add_header Cache-Control "no-store" always;' in oversize
    assert 'add_header Referrer-Policy "no-referrer" always;' in oversize
    assert (
        "conversation.request_too_large"
        in oversize
    )
    assert "return 413" in oversize


def test_helm_ingress_timeout_outlives_public_chat_absolute_deadline():
    values = _read("infra/helm/moduly/values.yaml")
    production_values = _read("infra/helm/moduly/values-production.yaml")
    ingress = _read("infra/helm/moduly/templates/ingress.yaml")
    timeout_match = re.search(
        r"publicChatTimeoutSeconds:\s*(\d+)",
        values,
    )

    assert timeout_match is not None
    assert int(timeout_match.group(1)) > 600
    assert "publicChatTimeoutSeconds: 610" in production_values
    assert "ingress.publicChatTimeoutSeconds is required" in ingress
    assert "must be greater than the 600 second public request deadline" in ingress
    assert "nginx.ingress.kubernetes.io/proxy-read-timeout" in ingress
    assert "nginx.ingress.kubernetes.io/proxy-send-timeout" in ingress
    assert "mergeOverwrite" in ingress
    assert "deepCopy (.Values.ingress.annotations | default dict)" in ingress
