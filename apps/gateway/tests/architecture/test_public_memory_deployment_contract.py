from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
SECURITY_KEYS = (
    "MEMORY_PUBLIC_CAPABILITY_HMAC_KEY",
    "MEMORY_PUBLIC_REPLAY_ENCRYPTION_KEY",
    "MEMORY_PUBLIC_ADMISSION_HMAC_KEY",
)


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_public_memory_is_explicitly_disabled_in_standard_deployment_defaults():
    compose = _read("docker/docker-compose.yml")
    values = _read("infra/helm/moduly/values.yaml")
    production_values = _read("infra/helm/moduly/values-production.yaml")

    assert "MEMORY_PUBLIC_CONVERSATION_ENABLED:-false" in compose
    assert "MEMORY_PUBLIC_PURGE_WORKER_READY:-false" in compose
    assert "memoryPublicConversation:\n  enabled: false" in values
    assert "purgeWorkerReady: false" in values
    assert "memoryPublicConversation:\n  enabled: false" in production_values
    assert "purgeWorkerReady: false" in production_values


def test_enabled_helm_and_compose_paths_wire_three_independent_secret_names():
    compose = _read("docker/docker-compose.yml")
    values = _read("infra/helm/moduly/values.yaml")
    secret_template = _read("infra/helm/moduly/templates/secrets.yaml")
    gateway_template = _read("infra/helm/moduly/templates/gateway-deployment.yaml")

    for key in SECURITY_KEYS:
        assert key in compose
        assert key in secret_template
        assert key in gateway_template
    for value_name in (
        "memoryPublicCapabilityHmacKey",
        "memoryPublicReplayEncryptionKey",
        "memoryPublicAdmissionHmacKey",
    ):
        assert f'{value_name}: ""' in values
        assert f"secrets.{value_name} is required" in secret_template
    assert "MEMORY_PUBLIC_REPLAY_BACKUP_ERASURE_MODE" in compose
    assert "MEMORY_PUBLIC_REPLAY_BACKUP_ERASURE_MODE" in gateway_template


def test_standard_deployment_paths_support_bounded_public_memory_key_rotation():
    compose = _read("docker/docker-compose.yml")
    values = _read("infra/helm/moduly/values.yaml")
    production_values = _read("infra/helm/moduly/values-production.yaml")
    secret_template = _read("infra/helm/moduly/templates/secrets.yaml")
    gateway_template = _read("infra/helm/moduly/templates/gateway-deployment.yaml")

    for environment_name in (
        "MEMORY_PUBLIC_CAPABILITY_HMAC_KEYS",
        "MEMORY_PUBLIC_CAPABILITY_HMAC_PRIMARY_VERSION",
        "MEMORY_PUBLIC_REPLAY_ENCRYPTION_KEYS",
        "MEMORY_PUBLIC_REPLAY_ENCRYPTION_PRIMARY_VERSION",
    ):
        assert environment_name in compose
        assert environment_name in gateway_template

    for value_name in (
        "capabilityHmacPrimaryVersion",
        "replayEncryptionPrimaryVersion",
        "memoryPublicCapabilityHmacKeys",
        "memoryPublicReplayEncryptionKeys",
    ):
        assert value_name in values
        assert value_name in production_values
    assert "MEMORY_PUBLIC_CAPABILITY_HMAC_KEYS" in secret_template
    assert "MEMORY_PUBLIC_REPLAY_ENCRYPTION_KEYS" in secret_template


def test_runtime_images_include_memory_and_helm_schedules_replay_retention():
    gateway_dockerfile = _read("docker/gateway/Dockerfile")
    logger_dockerfile = _read("docker/log_system/Dockerfile")
    values = _read("infra/helm/moduly/values.yaml")
    beat_template = _read("infra/helm/moduly/templates/beat-deployment.yaml")

    assert "COPY apps/memory /app/apps/memory" in gateway_dockerfile
    assert "COPY apps/memory /app/apps/memory" in logger_dockerfile
    assert "beat:\n  enabled: true" in values
    assert "default .Values.worker.image.repository" in beat_template
    assert "- apps.shared.celery_app:celery_app" in beat_template
    assert "- beat" in beat_template


def test_standard_proxy_paths_preserve_client_network_for_public_admission():
    compose = _read("docker/docker-compose.yml")
    nginx = _read("docker/nginx/nginx.conf")
    helm_helpers = _read("infra/helm/moduly/templates/_helpers.tpl")

    assert "AUTH_LOGIN_TRUSTED_PROXY_CIDRS:-172.16.0.0/12" in compose
    assert "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for" in nginx
    assert "validateLoginTrustedProxy" in helm_helpers
    assert "AUTH_LOGIN_TRUSTED_PROXY_CIDRS is required" in helm_helpers
