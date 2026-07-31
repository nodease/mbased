from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_gateway_and_workers_receive_the_same_llm_credential_keyring_contract():
    deployment_files = [
        "infra/helm/moduly/templates/gateway-deployment.yaml",
        "infra/helm/moduly/templates/worker-deployment.yaml",
        "infra/helm/moduly/templates/knowledge-worker-deployment.yaml",
    ]

    for relative_path in deployment_files:
        content = _read(relative_path)
        assert "LLM_CREDENTIAL_ENCRYPTION_KEYS" in content
        assert "LLM_CREDENTIAL_ACTIVE_KEY_VERSION" in content


def test_local_compose_passes_llm_keyring_to_gateway_and_all_consuming_workers():
    compose = _read("docker/docker-compose.yml")
    keyring_lines = [
        line
        for line in compose.splitlines()
        if line.strip().startswith("LLM_CREDENTIAL_ENCRYPTION_KEYS:")
    ]
    active_version_lines = [
        line
        for line in compose.splitlines()
        if line.strip().startswith("LLM_CREDENTIAL_ACTIVE_KEY_VERSION:")
    ]

    assert len(keyring_lines) == 3
    assert len(active_version_lines) == 3


def test_rotation_cli_is_packaged_in_gateway_image_and_documented_after_startup():
    dockerfile = _read("docker/gateway/Dockerfile")
    readme = _read("README.md")

    assert (
        "COPY scripts/rotate_llm_credentials.py "
        "/app/scripts/rotate_llm_credentials.py" in dockerfile
    )
    assert readme.index("### LLM credential rotation") > readme.index("### Setup & Run")
    assert "exec -T gateway" in readme
    assert "python /app/scripts/rotate_llm_credentials.py --batch-size 100" in readme
    assert "python /app/scripts/rotate_llm_credentials.py --check" in readme
    assert "kubectl -n <namespace> exec deployment/<release>-gateway --" in readme


def test_helm_secret_declares_llm_keyring_without_embedding_a_key_value():
    secret_template = _read("infra/helm/moduly/templates/secrets.yaml")
    values = _read("infra/helm/moduly/values.yaml")
    production_values = _read("infra/helm/moduly/values-production.yaml")

    assert "LLM_CREDENTIAL_ENCRYPTION_KEYS:" in secret_template
    assert 'llmCredentialEncryptionKeys: ""' in values
    assert 'llmCredentialEncryptionKeys: ""' in production_values


def test_log_system_manifests_do_not_receive_llm_keyring():
    logger_files = [
        "infra/helm/moduly/templates/logger-deployment.yaml",
    ]

    for relative_path in logger_files:
        assert "LLM_CREDENTIAL_ENCRYPTION_KEYS" not in _read(relative_path)
