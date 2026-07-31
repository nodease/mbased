import uuid

from apps.gateway.adapters.cache.knowledge_document_ingestion_progress import (
    RedisDocumentIngestionProgressProjection,
)


def test_progress_projection_deletes_document_key(monkeypatch) -> None:
    deleted = []
    monkeypatch.setattr(
        "apps.gateway.adapters.cache.knowledge_document_ingestion_progress.get_redis_client",
        lambda: type("Redis", (), {"delete": lambda _self, key: deleted.append(key)})(),
    )
    document_id = uuid.uuid4()

    RedisDocumentIngestionProgressProjection().clear(document_id)

    assert deleted == [f"knowledge_progress:{document_id}"]


def test_progress_projection_failure_is_advisory(monkeypatch) -> None:
    monkeypatch.setattr(
        "apps.gateway.adapters.cache.knowledge_document_ingestion_progress.get_redis_client",
        lambda: (_ for _ in ()).throw(RuntimeError("redis unavailable")),
    )

    RedisDocumentIngestionProgressProjection().clear(uuid.uuid4())
