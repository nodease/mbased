from pathlib import Path


def test_helm_requires_and_injects_connector_allowed_ports() -> None:
    root = Path(__file__).parents[4]
    gateway_template = (
        root / "infra" / "helm" / "moduly" / "templates" / "gateway-deployment.yaml"
    ).read_text(encoding="utf-8")
    values = (root / "infra" / "helm" / "moduly" / "values.yaml").read_text(
        encoding="utf-8"
    )
    local_values = (root / "infra" / "helm" / "moduly" / "values-local.yaml").read_text(
        encoding="utf-8"
    )
    production_values = (
        root / "infra" / "helm" / "moduly" / "values-production.yaml"
    ).read_text(encoding="utf-8")

    assert 'required "connectorTest.allowedPorts is required"' in gateway_template
    assert "CONNECTOR_TEST_ALLOWED_PORTS" in gateway_template
    assert "allowedPorts:\n    - 5432" in values
    assert "allowedPorts:\n    - 5432" in production_values
    assert "allowedPorts:\n    - 5432" in local_values
    assert "54322" not in local_values
    assert "55432" not in local_values


def test_gateway_image_provides_system_ca_for_strict_postgres_probe() -> None:
    dockerfile = (
        Path(__file__).parents[4] / "docker" / "gateway" / "Dockerfile"
    ).read_text(encoding="utf-8")

    assert "ca-certificates" in dockerfile
    assert "libpq5" in dockerfile


def test_connector_port_profiles_match_their_runtime_network_boundary() -> None:
    root = Path(__file__).parents[4]
    compose = (root / "docker" / "docker-compose.yml").read_text(encoding="utf-8")
    dev_example = (root / "dev" / ".env.example").read_text(encoding="utf-8")
    docker_example = (root / "docker" / ".env.example").read_text(encoding="utf-8")

    assert "CONNECTOR_TEST_ALLOWED_PORTS:-5432}" in compose
    assert "CONNECTOR_TEST_ALLOWED_PORTS=5432,55432" in dev_example
    assert "CONNECTOR_TEST_ALLOWED_PORTS=5432" in docker_example
    assert "54322" not in docker_example
