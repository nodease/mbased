import inspect as py_inspect
import json
from dataclasses import asdict

from apps.shared.services import knowledge_schema_readiness as readiness


class FakeInspector:
    def __init__(self, columns_by_table):
        self.columns_by_table = columns_by_table

    def has_table(self, table_name):
        return table_name in self.columns_by_table

    def get_columns(self, table_name):
        return [{"name": name} for name in self.columns_by_table.get(table_name, [])]


class FakeDb:
    def get_bind(self):
        return object()


def test_schema_readiness_reports_missing_columns(monkeypatch):
    monkeypatch.setattr(
        readiness,
        "inspect",
        lambda _bind: FakeInspector({"knowledge_bases": {"organization_id"}}),
    )

    result = readiness.check_knowledge_schema_readiness(
        FakeDb(),
        {
            "knowledge_bases": {
                "organization_id",
                "sync_state",
                "lifecycle_state",
            },
            "document_chunks": {"id"},
        },
    )

    assert result.ready is False
    assert result.reason is None
    assert result.missing_tables == ["document_chunks"]
    assert result.missing_columns == {
        "document_chunks": ["id"],
        "knowledge_bases": ["lifecycle_state", "sync_state"],
    }


def test_schema_readiness_reports_ready_when_required_columns_exist(monkeypatch):
    monkeypatch.setattr(
        readiness,
        "inspect",
        lambda _bind: FakeInspector(
            {
                "knowledge_bases": {"organization_id", "sync_state"},
                "document_chunks": {"id", "document_id"},
            }
        ),
    )

    result = readiness.check_knowledge_schema_readiness(
        FakeDb(),
        {
            "knowledge_bases": {"organization_id", "sync_state"},
            "document_chunks": {"id", "document_id"},
        },
    )

    assert result.ready is True
    assert result.missing_columns == {}
    assert result.missing_tables == []
    assert result.reason is None


def test_schema_readiness_reports_missing_table_columns_in_stable_order(monkeypatch):
    monkeypatch.setattr(
        readiness,
        "inspect",
        lambda _bind: FakeInspector({"knowledge_bases": {"organization_id"}}),
    )

    result = readiness.check_knowledge_schema_readiness(
        FakeDb(),
        {
            "document_chunks": {"knowledge_base_id", "document_id", "id"},
            "knowledge_bases": {"sync_state", "organization_id"},
        },
    )

    assert result.ready is False
    assert result.missing_tables == ["document_chunks"]
    assert result.missing_columns == {
        "document_chunks": ["document_id", "id", "knowledge_base_id"],
        "knowledge_bases": ["sync_state"],
    }


def test_schema_readiness_handles_empty_required_columns(monkeypatch):
    monkeypatch.setattr(
        readiness,
        "inspect",
        lambda _bind: FakeInspector({"knowledge_bases": {"id"}}),
    )

    result = readiness.check_knowledge_schema_readiness(FakeDb(), {})

    assert result.ready is True
    assert result.missing_columns == {}
    assert result.missing_tables == []
    assert result.reason is None


def test_schema_readiness_result_is_json_safe_on_failure(monkeypatch):
    def fail_inspection(_bind):
        raise RuntimeError("secret raw driver failure")

    monkeypatch.setattr(readiness, "inspect", fail_inspection)

    result = readiness.check_knowledge_schema_readiness(
        FakeDb(),
        {"knowledge_bases": {"sync_state"}},
    )

    dumped = json.dumps(asdict(result), ensure_ascii=False)

    assert "schema_introspection_failed" in dumped
    assert "secret raw driver failure" not in dumped


def test_schema_readiness_helper_has_no_http_layer_dependency():
    source = py_inspect.getsource(readiness)

    assert "fastapi" not in source.lower()
    assert "HTTPException" not in source
    assert "Request" not in source


def test_schema_readiness_returns_safe_reason_on_introspection_failure(
    monkeypatch,
    caplog,
):
    def fail_inspection(_bind):
        raise RuntimeError("secret raw driver failure")

    monkeypatch.setattr(readiness, "inspect", fail_inspection)

    result = readiness.check_knowledge_schema_readiness(
        FakeDb(),
        {"knowledge_bases": {"sync_state"}},
    )

    assert result.ready is False
    assert result.missing_columns == {}
    assert result.missing_tables == []
    assert result.reason == "schema_introspection_failed"
    assert "secret raw driver failure" not in caplog.text


def test_table_has_column_returns_false_on_introspection_failure(monkeypatch, caplog):
    def fail_inspection(_bind):
        raise RuntimeError("secret raw driver failure")

    monkeypatch.setattr(readiness, "inspect", fail_inspection)

    assert readiness.table_has_column(FakeDb(), "knowledge_bases", "sync_state") is False
    assert "secret raw driver failure" not in caplog.text
