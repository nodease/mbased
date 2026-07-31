from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_gateway_and_worker_receive_the_same_mail_credential_keyring_contract():
    deployment_files = [
        "infra/helm/moduly/templates/gateway-deployment.yaml",
        "infra/helm/moduly/templates/worker-deployment.yaml",
    ]

    for relative_path in deployment_files:
        content = _read(relative_path)
        assert "MAIL_CREDENTIAL_ENCRYPTION_KEYS" in content
        assert "MAIL_CREDENTIAL_ACTIVE_KEY_VERSION" in content


def test_local_compose_passes_mail_keyring_to_gateway_and_worker():
    compose = _read("docker/docker-compose.yml")
    keyring_lines = [
        line
        for line in compose.splitlines()
        if line.strip().startswith("MAIL_CREDENTIAL_ENCRYPTION_KEYS:")
    ]
    active_version_lines = [
        line
        for line in compose.splitlines()
        if line.strip().startswith("MAIL_CREDENTIAL_ACTIVE_KEY_VERSION:")
    ]

    assert len(keyring_lines) == 2
    assert len(active_version_lines) == 2


def test_helm_secret_declares_mail_keyring_without_embedding_a_key_value():
    secret_template = _read("infra/helm/moduly/templates/secrets.yaml")
    values = _read("infra/helm/moduly/values.yaml")
    production_values = _read("infra/helm/moduly/values-production.yaml")

    assert "MAIL_CREDENTIAL_ENCRYPTION_KEYS:" in secret_template
    assert 'mailCredentialEncryptionKeys: ""' in values
    assert 'mailCredentialEncryptionKeys: ""' in production_values
