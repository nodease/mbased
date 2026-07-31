import pytest

from apps.gateway import knowledge_worker_readiness
from apps.gateway.knowledge_worker_readiness import wait_for_readiness
from apps.shared.services.knowledge_document_ingestion_schema_readiness import (
    KnowledgeDocumentIngestionSchemaNotReady,
    KnowledgeDocumentIngestionSchemaReadiness,
)


NOT_READY = KnowledgeDocumentIngestionSchemaNotReady(
    KnowledgeDocumentIngestionSchemaReadiness(
        ready=False,
        reason_code="knowledge.ingestion_table_missing",
    )
)


def test_readiness_wait_retries_until_schema_is_visible() -> None:
    attempts = 0
    sleeps = []

    def check() -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise NOT_READY

    wait_for_readiness(
        check=check,
        timeout_seconds=10,
        interval_seconds=2,
        clock=lambda: 0,
        sleep=sleeps.append,
    )

    assert attempts == 3
    assert sleeps == [2, 2]


def test_readiness_wait_fails_closed_after_deadline() -> None:
    times = iter([0, 1])

    with pytest.raises(KnowledgeDocumentIngestionSchemaNotReady):
        wait_for_readiness(
            check=lambda: (_ for _ in ()).throw(NOT_READY),
            timeout_seconds=1,
            interval_seconds=1,
            clock=lambda: next(times),
            sleep=lambda _seconds: None,
        )


def test_readiness_checks_proxy_and_llm_keyring_before_schema(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        knowledge_worker_readiness,
        "require_outbound_proxy_security_ready",
        lambda: calls.append("egress"),
    )
    monkeypatch.setattr(
        knowledge_worker_readiness,
        "require_connector_tcp_proxy_security_ready",
        lambda: calls.append("connector-egress"),
    )
    monkeypatch.setattr(
        knowledge_worker_readiness,
        "require_llm_credential_keyring_ready",
        lambda: calls.append("keyring"),
    )
    monkeypatch.setattr(
        knowledge_worker_readiness,
        "require_knowledge_document_ingestion_ready",
        lambda *_args, **_kwargs: calls.append("schema"),
    )

    knowledge_worker_readiness._require_readiness()

    assert calls == ["egress", "connector-egress", "keyring", "schema"]


@pytest.mark.parametrize(
    ("timeout_seconds", "interval_seconds"),
    [(0, 1), (1, 0)],
)
def test_readiness_wait_rejects_invalid_timing(
    timeout_seconds: float,
    interval_seconds: float,
) -> None:
    with pytest.raises(ValueError, match="readiness timing must be positive"):
        wait_for_readiness(
            timeout_seconds=timeout_seconds,
            interval_seconds=interval_seconds,
        )
