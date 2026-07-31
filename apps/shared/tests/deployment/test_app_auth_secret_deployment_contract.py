from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[4]


def test_compose_requires_explicit_lifecycle_activation():
    compose = (ROOT / "docker/docker-compose.yml").read_text(encoding="utf-8")
    assert (
        "APP_AUTH_SECRET_LIFECYCLE_MODE: ${APP_AUTH_SECRET_LIFECYCLE_MODE:-disabled}"
    ) in compose


def test_helm_gateway_defaults_and_wires_lifecycle_gate():
    chart = ROOT / "infra/helm/moduly"
    values = yaml.safe_load((chart / "values.yaml").read_text(encoding="utf-8"))
    assert values["gateway"]["env"]["APP_AUTH_SECRET_LIFECYCLE_MODE"] == "disabled"

    configmap = (chart / "templates/configmap.yaml").read_text(encoding="utf-8")
    assert "APP_AUTH_SECRET_LIFECYCLE_MODE:" in configmap
    assert ".Values.gateway.env.APP_AUTH_SECRET_LIFECYCLE_MODE" in configmap

    deployment = (chart / "templates/gateway-deployment.yaml").read_text(
        encoding="utf-8"
    )
    assert "- name: APP_AUTH_SECRET_LIFECYCLE_MODE" in deployment
    assert "key: APP_AUTH_SECRET_LIFECYCLE_MODE" in deployment
