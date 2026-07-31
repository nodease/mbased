from types import SimpleNamespace

from apps.gateway.api.v1.endpoints import workflow as workflow_endpoint
from apps.shared.db.models.workflow_run import RunStatus, RunTriggerMode, WorkflowRun


class _RecordingQuery:
    def __init__(self, rows):
        self.rows = rows
        self.operations: list[tuple[str, tuple[str, ...]]] = []

    def options(self, *values):
        self.operations.append(("options", tuple(map(str, values))))
        return self

    def filter(self, *expressions):
        self.operations.append(("filter", tuple(map(str, expressions))))
        return self

    def count(self):
        self.operations.append(("count", ()))
        return len(self.rows)

    def order_by(self, *values):
        self.operations.append(("order_by", tuple(map(str, values))))
        return self

    def offset(self, value):
        self.operations.append(("offset", (str(value),)))
        return self

    def limit(self, value):
        self.operations.append(("limit", (str(value),)))
        return self

    def all(self):
        self.operations.append(("all", ()))
        return self.rows


class _RecordingDb:
    def __init__(self, query):
        self.recording_query = query

    def query(self, model):
        assert model is WorkflowRun
        return self.recording_query


def test_run_comparison_filters_are_applied_before_pagination(monkeypatch):
    query = _RecordingQuery([SimpleNamespace(id="run-1")])
    db = _RecordingDb(query)
    monkeypatch.setattr(
        workflow_endpoint,
        "ensure_workflow_permission",
        lambda *args, **kwargs: None,
    )

    response = workflow_endpoint.get_workflow_runs(
        workflow_id="11111111-1111-1111-1111-111111111111",
        page=1,
        limit=20,
        status=RunStatus.SUCCESS,
        trigger_mode=RunTriggerMode.MANUAL,
        db=db,
        current_user=SimpleNamespace(id="user-1"),
    )

    operation_names = [name for name, _ in query.operations]
    assert response["total"] == 1
    assert response["items"][0].id == "run-1"
    assert operation_names.count("filter") == 3
    assert operation_names.index("count") < operation_names.index("order_by")
    assert operation_names.index("order_by") < operation_names.index("limit")

    filter_text = " ".join(
        expression
        for name, expressions in query.operations
        if name == "filter"
        for expression in expressions
    )
    assert "workflow_runs.workflow_id" in filter_text
    assert "workflow_runs.status" in filter_text
    assert "workflow_runs.trigger_mode" in filter_text
