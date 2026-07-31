from pathlib import Path
from types import SimpleNamespace

import pytest

from apps.gateway import knowledge_worker


ROOT = Path(__file__).parents[4]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_compose_worker_uses_gateway_image_and_only_knowledge_queue() -> None:
    compose = _read("docker/docker-compose.yml")
    section = compose.split("  knowledge_worker:", 1)[1].split(
        "  # Workflow Engine", 1
    )[0]

    assert "docker/gateway/Dockerfile" in section
    assert "apps.gateway.knowledge_worker:app" in section
    assert "--queues=knowledge" in section
    assert "--concurrency=2" in section
    assert "uvicorn" not in section
    assert "disable: true" in section
    assert "gateway:\n        condition: service_healthy" in section


def test_helm_worker_is_migration_first_and_has_bounded_concurrency() -> None:
    template = _read("infra/helm/moduly/templates/knowledge-worker-deployment.yaml")
    gateway_template = _read("infra/helm/moduly/templates/gateway-deployment.yaml")
    storage_template = _read("infra/helm/moduly/templates/knowledge-storage-pvc.yaml")
    service_account_template = _read("infra/helm/moduly/templates/serviceaccount.yaml")
    helpers = _read("infra/helm/moduly/templates/_helpers.tpl")
    values = _read("infra/helm/moduly/values.yaml")
    production = _read("infra/helm/moduly/values-production.yaml")
    local = _read("infra/helm/moduly/values-local.yaml")

    assert ".Values.knowledgeWorker.enabled" in template
    assert ".Values.gateway.image.repository" in template
    assert "apps.gateway.knowledge_worker:app" in template
    assert "--queues=knowledge" in template
    assert "--concurrency={{ .Values.knowledgeWorker.concurrency }}" in template
    assert "--hostname=knowledge@%h" in template
    assert "initContainers:" in template
    assert "apps.gateway.knowledge_worker_readiness" in template
    assert "readinessProbe:" in template
    assert "apps.gateway.knowledge_worker_health" in template
    assert "livenessProbe:" not in template
    assert 'include "moduly.validateKnowledgeWorker" .' in template
    assert 'define "moduly.validateKnowledgeWorker"' in helpers
    assert (
        "knowledge worker requires the bundled Celery Beat recovery scheduler"
        in helpers
    )
    assert 'claimName: {{ include "moduly.knowledgeUploadClaimName" . }}' in template
    assert "mountPath: /app/uploads" in template
    assert "mountPath: /app/uploads" in gateway_template
    assert "fsGroup: {{ .Values.knowledgeWorker.localStorage.fsGroup }}" in template
    assert (
        "fsGroup: {{ .Values.knowledgeWorker.localStorage.fsGroup }}"
        in gateway_template
    )
    assert "kind: PersistentVolumeClaim" in storage_template
    assert ".Values.serviceAccount.create" in service_account_template
    assert 'include "moduly.serviceAccountName" .' in service_account_template
    assert "knowledgeWorker:\n  enabled: false" in values
    assert "knowledgeWorker:\n  enabled: true" in production
    assert "beat:\n  enabled: true" in production
    assert "knowledgeWorker:\n  enabled: true" in local
    assert "localStorage:\n    enabled: true" in local


def test_knowledge_worker_receives_llm_keyring_for_init_and_runtime() -> None:
    compose = _read("docker/docker-compose.yml")
    compose_section = compose.split("  knowledge_worker:", 1)[1].split(
        "  # Workflow Engine", 1
    )[0]
    template = _read("infra/helm/moduly/templates/knowledge-worker-deployment.yaml")
    init_container = template.split("initContainers:", 1)[1].split(
        "      containers:", 1
    )[0]
    worker_container = template.split("- name: knowledge-worker", 1)[1]

    assert "LLM_CREDENTIAL_ENCRYPTION_KEYS" in compose_section
    assert "LLM_CREDENTIAL_ACTIVE_KEY_VERSION" in compose_section
    assert "ENCRYPTION_KEY" in init_container
    assert "LLM_CREDENTIAL_ENCRYPTION_KEYS" in init_container
    assert "LLM_CREDENTIAL_ACTIVE_KEY_VERSION" in init_container
    assert "LLM_CREDENTIAL_ENCRYPTION_KEYS" in worker_container
    assert "LLM_CREDENTIAL_ACTIVE_KEY_VERSION" in worker_container


def test_shared_celery_routes_and_recovers_knowledge_jobs() -> None:
    celery_source = _read("apps/shared/celery_app.py")
    worker_source = _read("apps/gateway/knowledge_worker.py")

    assert '"knowledge.*": {"queue": "knowledge"}' in celery_source
    assert '"task": "knowledge.document_ingestion.recover"' in celery_source
    assert 'os.environ.setdefault("CELERY_WORKER_ROLE", "knowledge")' in worker_source
    assert "require_knowledge_document_ingestion_ready" in worker_source


def test_knowledge_worker_checks_egress_and_keyring_before_schema_readiness(
    monkeypatch,
) -> None:
    calls = []
    monkeypatch.setattr(
        knowledge_worker,
        "require_outbound_proxy_security_ready",
        lambda: calls.append("egress"),
    )
    monkeypatch.setattr(
        knowledge_worker,
        "require_llm_credential_keyring_ready",
        lambda: calls.append("keyring"),
    )
    monkeypatch.setattr(
        knowledge_worker,
        "require_knowledge_document_ingestion_ready",
        lambda *_args, **_kwargs: calls.append("schema"),
    )

    knowledge_worker._require_readiness()

    assert calls == ["egress", "keyring", "schema"]


def test_knowledge_worker_child_process_revalidates_egress_and_keyring(
    monkeypatch,
) -> None:
    calls = []
    monkeypatch.setattr(
        knowledge_worker,
        "engine",
        SimpleNamespace(dispose=lambda: calls.append("dispose")),
    )
    monkeypatch.setattr(
        knowledge_worker,
        "require_llm_credential_keyring_ready",
        lambda: calls.append("keyring"),
    )
    monkeypatch.setattr(
        knowledge_worker,
        "require_outbound_proxy_security_ready",
        lambda: calls.append("egress"),
    )

    knowledge_worker.initialize_knowledge_worker_process()

    assert calls == ["dispose", "egress", "keyring"]


def test_worker_readiness_bootstep_propagates_startup_failure(monkeypatch) -> None:
    def fail_readiness() -> None:
        raise RuntimeError("safe readiness failure")

    monkeypatch.setattr(knowledge_worker, "_require_readiness", fail_readiness)

    with pytest.raises(RuntimeError, match="safe readiness failure"):
        step = knowledge_worker.KnowledgeSchemaReadinessStep(parent=object())
        step.start(object())
