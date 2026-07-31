from types import SimpleNamespace
from uuid import uuid4

from apps.shared.db.models.workflow_run import TraceVisibilityPolicy, WorkflowRun
from apps.shared.services.tracing.access import TraceAccessService
from apps.shared.services.tracing.query import TraceQueryService


def test_list_traces_applies_visibility_before_limit_without_python_filter(
    monkeypatch,
):
    query = _CaptureTraceQuery()
    db = _CaptureSession(query)
    user = SimpleNamespace(id=uuid4())
    monkeypatch.setattr(
        TraceAccessService,
        "is_system_admin",
        lambda _db, _user: False,
    )
    monkeypatch.setattr(
        TraceAccessService,
        "check_trace_access",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("list query must not filter fetched rows in Python")
        ),
    )
    monkeypatch.setattr(
        TraceQueryService,
        "_metadata_visible_owned_app_ids",
        lambda _db, _user_id: [uuid4()],
    )

    result = TraceQueryService.list_traces(db, user, page=1, limit=20)

    assert result == {
        "total": 0,
        "items": [],
        "has_more": False,
        "total_is_estimated": False,
        "scan_limit_reached": False,
    }
    assert query.count_calls == 1
    assert query.all_calls == 1
    assert query.offset_value == 0
    assert query.limit_value == 20
    assert query.filters
    visibility_sql = str(query.filters[-1])
    assert "workflow_runs.app_id" in visibility_sql
    assert "workflows.app_id" in visibility_sql
    assert "workflow_deployments.app_id" in visibility_sql


def test_trace_visibility_sql_preserves_canonical_app_resolution_precedence(
    monkeypatch,
):
    query = _CaptureTraceQuery()
    db = _CaptureSession(query)
    user = SimpleNamespace(id=uuid4())
    monkeypatch.setattr(
        TraceAccessService,
        "is_system_admin",
        lambda _db, _user: False,
    )
    monkeypatch.setattr(
        TraceQueryService,
        "_metadata_visible_owned_app_ids",
        lambda _db, _user_id: [uuid4()],
    )

    TraceQueryService._apply_trace_visibility_filter(db, query, user)

    visibility_sql = str(query.filters[-1]).lower()
    assert "coalesce" in visibility_sql
    assert visibility_sql.index("workflow_runs.app_id") < visibility_sql.index(
        "workflows.app_id"
    )
    assert visibility_sql.index("workflows.app_id") < visibility_sql.index(
        "workflow_deployments.app_id"
    )


def test_visible_owned_apps_resolve_app_policy_before_global_policy():
    allowed_app_id = uuid4()
    denied_app_id = uuid4()
    organization_id = uuid4()
    policies = [
        SimpleNamespace(
            scope_type="app",
            scope_id=denied_app_id,
            owner_trace_access_enabled=False,
            deny_owner_trace_access=False,
        ),
        SimpleNamespace(
            scope_type="global",
            scope_id=None,
            owner_trace_access_enabled=True,
            deny_owner_trace_access=False,
        ),
    ]
    db = _PolicySession(
        owned_apps=[
            (allowed_app_id, organization_id),
            (denied_app_id, organization_id),
        ],
        policies=policies,
    )

    assert TraceQueryService._metadata_visible_owned_app_ids(db, uuid4()) == [
        allowed_app_id
    ]


class _CaptureSession:
    def __init__(self, query):
        self.query_value = query

    def query(self, model):
        assert model is WorkflowRun
        return self.query_value


class _CaptureTraceQuery:
    def __init__(self):
        self.filters = []
        self.offset_value = None
        self.limit_value = None
        self.count_calls = 0
        self.all_calls = 0

    def filter(self, *conditions):
        self.filters.extend(conditions)
        return self

    def order_by(self, *_args):
        return self

    def count(self):
        self.count_calls += 1
        return 0

    def offset(self, value):
        self.offset_value = value
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def all(self):
        self.all_calls += 1
        return []


class _PolicySession:
    def __init__(self, *, owned_apps, policies):
        self.owned_apps = owned_apps
        self.policies = policies

    def query(self, *models):
        if len(models) == 1 and models[0] is TraceVisibilityPolicy:
            return _RowsQuery(self.policies)
        return _RowsQuery(self.owned_apps)


class _RowsQuery:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *_conditions):
        return self

    def order_by(self, *_columns):
        return self

    def all(self):
        return list(self.rows)
