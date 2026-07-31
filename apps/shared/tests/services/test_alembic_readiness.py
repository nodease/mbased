import inspect as py_inspect

from apps.shared.services import alembic_readiness as readiness


class FakeBind:
    def __init__(self, revisions):
        self.revisions = revisions

    def execute(self, _stmt):
        return self

    def fetchall(self):
        return [(revision,) for revision in self.revisions]


class FakeConnection:
    def __init__(self, revisions):
        self.revisions = revisions

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        return False

    def execute(self, _stmt):
        return self

    def fetchall(self):
        return [(revision,) for revision in self.revisions]


class FakeEngineBind:
    def __init__(self, revisions):
        self.revisions = revisions

    def connect(self):
        return FakeConnection(self.revisions)


class FakeInspector:
    def __init__(self, *, has_version_table=True, revisions=None):
        self.has_version_table = has_version_table
        self.bind = FakeBind(revisions or [])

    def has_table(self, table_name):
        assert table_name == "alembic_version"
        return self.has_version_table


class FakeEngineInspector(FakeInspector):
    def __init__(self, *, has_version_table=True, revisions=None):
        super().__init__(has_version_table=has_version_table, revisions=revisions)
        self.bind = FakeEngineBind(revisions or [])


class FailingInspector:
    def has_table(self, _table_name):
        raise RuntimeError("postgres://secret-host/raw failure")


def test_evaluate_alembic_readiness_accepts_current_single_head():
    result = readiness.evaluate_alembic_readiness(
        code_heads=["head-1"],
        database_revisions=["head-1"],
        known_revisions=["base", "head-1"],
    )

    assert result.ready is True
    assert result.code_heads == ["head-1"]
    assert result.database_revisions == ["head-1"]


def test_evaluate_alembic_readiness_reports_missing_version_table():
    result = readiness.evaluate_alembic_readiness(
        code_heads=["head-1"],
        database_revisions=[],
        known_revisions=["head-1"],
        has_version_table=False,
    )

    assert result.ready is False
    assert result.missing_version_table is True
    assert result.database_behind is False


def test_evaluate_alembic_readiness_reports_database_behind_code_head():
    result = readiness.evaluate_alembic_readiness(
        code_heads=["head-2"],
        database_revisions=["head-1"],
        known_revisions=["head-1", "head-2"],
    )

    assert result.ready is False
    assert result.database_behind is True
    assert result.split_heads is False


def test_evaluate_alembic_readiness_reports_split_code_heads():
    result = readiness.evaluate_alembic_readiness(
        code_heads=["head-2", "head-3"],
        database_revisions=["head-2"],
        known_revisions=["head-1", "head-2", "head-3"],
    )

    assert result.ready is False
    assert result.split_heads is True
    assert result.database_behind is True


def test_evaluate_alembic_readiness_reports_unknown_database_revision():
    result = readiness.evaluate_alembic_readiness(
        code_heads=["head-2"],
        database_revisions=["unknown-head"],
        known_revisions=["head-1", "head-2"],
    )

    assert result.ready is False
    assert result.unknown_database_revisions == ["unknown-head"]


def test_check_alembic_readiness_with_inspector_reads_version_rows():
    result = readiness.check_alembic_readiness_with_inspector(
        FakeInspector(revisions=["head-1"]),
        code_heads=["head-1"],
        known_revisions=["head-1"],
    )

    assert result.ready is True


def test_check_alembic_readiness_with_engine_bind_reads_version_rows():
    result = readiness.check_alembic_readiness_with_inspector(
        FakeEngineInspector(revisions=["head-1"]),
        code_heads=["head-1"],
        known_revisions=["head-1"],
    )

    assert result.ready is True


def test_check_alembic_readiness_with_inspector_handles_missing_table():
    result = readiness.check_alembic_readiness_with_inspector(
        FakeInspector(has_version_table=False),
        code_heads=["head-1"],
        known_revisions=["head-1"],
    )

    assert result.ready is False
    assert result.missing_version_table is True


def test_check_alembic_readiness_rejects_unsafe_version_table_identifier():
    result = readiness.check_alembic_readiness_with_inspector(
        FakeInspector(revisions=["head-1"]),
        code_heads=["head-1"],
        known_revisions=["head-1"],
        version_table="alembic_version; DROP TABLE users",
    )

    assert result.ready is False
    assert result.reason == "invalid_version_table"


def test_check_alembic_readiness_returns_safe_reason_on_failure(caplog):
    result = readiness.check_alembic_readiness_with_inspector(
        FailingInspector(),
        code_heads=["head-1"],
        known_revisions=["head-1"],
    )

    assert result.ready is False
    assert result.reason == "alembic_introspection_failed"
    assert "secret-host" not in caplog.text
    assert "raw failure" not in caplog.text


def test_alembic_readiness_helper_has_no_http_or_runtime_dependency():
    source = py_inspect.getsource(readiness)

    assert "fastapi" not in source.lower()
    assert "apps.gateway" not in source
    assert "apps.workflow_engine" not in source
