from types import SimpleNamespace

import pytest

from apps.workflow_engine.composition import external_effect_readiness as readiness


class _ColumnType:
    def __init__(self, name, *, timezone=None):
        self.name = name
        self.timezone = timezone

    def __str__(self):
        return self.name


class _Inspector:
    def has_table(self, _table_name):
        return True

    def get_columns(self, _table_name):
        return [
            {
                "name": name,
                "nullable": shape.nullable,
                "type": _ColumnType(
                    shape.type_name,
                    timezone=shape.timezone,
                ),
            }
            for name, shape in readiness.REQUIRED_COLUMN_SHAPES.items()
        ]

    def get_check_constraints(self, _table_name):
        constraints = [
            {"name": name, "sqltext": " ".join(fragments)}
            for name, fragments in readiness.REQUIRED_CHECK_SHAPES.items()
        ]
        for constraint in constraints:
            if constraint["name"] == "ck_workflow_node_effect_attempts_counters":
                constraint["sqltext"] = "effect_sequence >= 0 AND claim_generation > 0"
        return constraints

    def get_unique_constraints(self, _table_name):
        return [
            {"name": name, "column_names": list(columns)}
            for name, columns in readiness.REQUIRED_UNIQUE_SHAPES.items()
        ]

    def get_indexes(self, _table_name):
        return [
            {
                "name": name,
                "column_names": list(shape.columns),
                "unique": False,
                "dialect_options": {"postgresql_where": shape.predicate or ""},
            }
            for name, shape in readiness.REQUIRED_INDEX_SHAPES.items()
        ]


def _prepare(monkeypatch):
    monkeypatch.setattr(readiness, "inspect", lambda _engine: _Inspector())
    monkeypatch.setattr(
        readiness,
        "check_alembic_readiness_with_inspector",
        lambda *args, **kwargs: SimpleNamespace(ready=True),
    )
    monkeypatch.setattr(
        readiness,
        "_script_directory",
        lambda: SimpleNamespace(get_heads=lambda: ["head"], walk_revisions=lambda: []),
    )
    monkeypatch.setattr(readiness, "external_effect_claim_ttl_seconds", lambda: 630)


def test_worker_readiness_accepts_complete_schema_and_known_contracts(monkeypatch):
    _prepare(monkeypatch)
    monkeypatch.setattr(readiness, "_require_persisted_contracts", lambda *a, **k: None)

    readiness.require_external_effect_worker_ready(object())


def test_worker_readiness_requires_allowlisted_error_constraint():
    assert "ck_workflow_node_effect_attempts_error_code" in readiness.REQUIRED_CHECKS


def test_worker_readiness_rejects_missing_schema(monkeypatch):
    _prepare(monkeypatch)
    monkeypatch.setattr(readiness, "_required_schema_exists", lambda _inspector: False)

    with pytest.raises(readiness.ExternalEffectMigrationNotReadyError):
        readiness.require_external_effect_worker_ready(object())


def test_schema_shape_rejects_correct_names_with_wrong_definitions():
    class WrongShapeInspector(_Inspector):
        def get_columns(self, table_name):
            columns = super().get_columns(table_name)
            columns[0] = {**columns[0], "nullable": True, "type": "TEXT"}
            return columns

        def get_check_constraints(self, _table_name):
            return [
                {"name": name, "sqltext": "TRUE"}
                for name in readiness.REQUIRED_CHECK_SHAPES
            ]

        def get_unique_constraints(self, _table_name):
            return [
                {"name": name, "column_names": ["wrong"]}
                for name in readiness.REQUIRED_UNIQUE_SHAPES
            ]

        def get_indexes(self, _table_name):
            return [
                {
                    "name": name,
                    "column_names": ["wrong"],
                    "unique": False,
                    "dialect_options": {"postgresql_where": "TRUE"},
                }
                for name in readiness.REQUIRED_INDEX_SHAPES
            ]

    assert readiness._required_schema_exists(WrongShapeInspector()) is False


def test_schema_shape_rejects_reversed_counter_check_operators():
    class ReversedCounterInspector(_Inspector):
        def get_check_constraints(self, table_name):
            constraints = super().get_check_constraints(table_name)
            for constraint in constraints:
                if constraint["name"] == "ck_workflow_node_effect_attempts_counters":
                    constraint["sqltext"] = (
                        "effect_sequence < 0 AND claim_generation <= 0"
                    )
            return constraints

    assert readiness._required_schema_exists(ReversedCounterInspector()) is False


def test_schema_shape_rejects_timestamp_without_timezone():
    class NaiveTimestampInspector(_Inspector):
        def get_columns(self, table_name):
            columns = super().get_columns(table_name)
            for column in columns:
                if column["name"] == "claim_expires_at":
                    column["type"] = _ColumnType("TIMESTAMP", timezone=False)
            return columns

    assert readiness._required_schema_exists(NaiveTimestampInspector()) is False


def test_shared_celery_app_does_not_register_external_effect_readiness():
    source = (readiness.ROOT_DIR / "apps" / "shared" / "celery_app.py").read_text(
        encoding="utf-8"
    )

    assert "require_external_effect_worker_ready" not in source


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self, result_sets):
        self.result_sets = iter(result_sets)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, _statement):
        return _Rows(next(self.result_sets))


class _Engine:
    def __init__(self, result_sets):
        self.result_sets = result_sets

    def connect(self):
        return _Connection(self.result_sets)


def test_persisted_unknown_contract_fails_readiness():
    engine = _Engine(
        [
            [("unknown", "unknown.operation", "unknown.v1")],
            [],
        ]
    )

    with pytest.raises(readiness.ExternalEffectMigrationNotReadyError):
        readiness._require_persisted_contracts(engine, keyring_versions=set())


def test_replayable_supported_attempt_requires_original_key_version():
    engine = _Engine(
        [
            [],
            [("retired-v1",)],
        ]
    )

    with pytest.raises(readiness.ExternalEffectMigrationNotReadyError):
        readiness._require_persisted_contracts(
            engine,
            keyring_versions={"active-v2"},
        )
