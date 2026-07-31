import pytest
from apps.shared.domain.knowledge_document_ingestion import (
    KnowledgeDocumentIngestionStateError,
)
from apps.shared.services import knowledge_document_ingestion_schema_readiness as ready


class FakeInspector:
    def __init__(
        self,
        *,
        has_table=True,
        columns=(),
        indexes=(),
        unique_constraints=(),
    ) -> None:
        self._has_table = has_table
        self._columns = columns
        self._indexes = indexes
        self._unique_constraints = unique_constraints

    def has_table(self, table_name):
        assert table_name == ready.TABLE_NAME
        return self._has_table

    def get_columns(self, table_name):
        assert table_name == ready.TABLE_NAME
        return [{"name": value} for value in self._columns]

    def get_indexes(self, table_name):
        assert table_name == ready.TABLE_NAME
        return [{"name": value} for value in self._indexes]

    def get_unique_constraints(self, table_name):
        assert table_name == ready.TABLE_NAME
        return [{"name": value} for value in self._unique_constraints]


def test_readiness_accepts_complete_schema(monkeypatch) -> None:
    monkeypatch.setattr(
        ready,
        "inspect",
        lambda _engine: FakeInspector(
            columns=ready.REQUIRED_COLUMNS,
            indexes=ready.REQUIRED_INDEXES,
            unique_constraints=ready.REQUIRED_UNIQUE_CONSTRAINTS,
        ),
    )

    result = ready.inspect_knowledge_document_ingestion_schema(object())

    assert result.ready is True
    assert result.reason_code is None


def test_readiness_reports_stable_missing_schema_without_raw_error(monkeypatch) -> None:
    monkeypatch.setattr(
        ready,
        "inspect",
        lambda _engine: FakeInspector(columns={"id"}, indexes=set()),
    )

    result = ready.inspect_knowledge_document_ingestion_schema(object())

    assert result.ready is False
    assert result.reason_code == "knowledge.ingestion_schema_incomplete"
    assert result.missing_columns == tuple(sorted(ready.REQUIRED_COLUMNS - {"id"}))
    assert result.missing_indexes == tuple(sorted(ready.REQUIRED_INDEXES))
    assert result.missing_unique_constraints == tuple(
        sorted(ready.REQUIRED_UNIQUE_CONSTRAINTS)
    )


def test_readiness_fails_safe_on_introspection_error(monkeypatch, caplog) -> None:
    def fail(_engine):
        raise RuntimeError("postgresql://private-host/raw-error")

    monkeypatch.setattr(ready, "inspect", fail)

    result = ready.inspect_knowledge_document_ingestion_schema(object())

    assert result.reason_code == "knowledge.ingestion_schema_introspection_failed"
    assert "private-host" not in caplog.text


def test_worker_settings_are_validated_before_schema_use(monkeypatch) -> None:
    monkeypatch.setattr(ready, "inspect", lambda _engine: FakeInspector())

    with pytest.raises(KnowledgeDocumentIngestionStateError):
        ready.require_knowledge_document_ingestion_ready(
            object(),
            lease_seconds=30,
            heartbeat_seconds=30,
        )
