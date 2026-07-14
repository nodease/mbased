import uuid
from types import SimpleNamespace

from apps.shared.db.models.app import App
from apps.shared.db.models.workflow import Workflow
from apps.shared.services.app_lifecycle_admission import (
    lock_app_workflow_for_admission,
)


class _Query:
    def __init__(self, row):
        self.row = row
        self.lock_kwargs = None
        self.refreshed = False

    def filter(self, *expressions):
        return self

    def populate_existing(self):
        self.refreshed = True
        return self

    def with_for_update(self, **kwargs):
        self.lock_kwargs = kwargs
        return self

    def first(self):
        return self.row


class _Db:
    def __init__(self, app, workflow):
        self.rows = {App: app, Workflow: workflow}
        self.models = []
        self.queries = []

    def query(self, model):
        self.models.append(model)
        query = _Query(self.rows[model])
        self.queries.append(query)
        return query


def test_admission_locks_app_then_workflow_with_key_share():
    app_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    app = SimpleNamespace(
        id=app_id,
        workflow_id=workflow_id,
        organization_id=organization_id,
    )
    workflow = SimpleNamespace(
        id=workflow_id,
        app_id=app_id,
        organization_id=organization_id,
    )
    db = _Db(app, workflow)

    result = lock_app_workflow_for_admission(
        db,
        app_id=app_id,
        workflow_id=workflow_id,
        organization_id=organization_id,
    )

    assert result is not None
    assert db.models == [App, Workflow]
    assert [query.lock_kwargs for query in db.queries] == [
        {"key_share": True},
        {"key_share": True},
    ]
    assert all(query.refreshed for query in db.queries)


def test_admission_fails_closed_when_primary_relationship_is_stale():
    app_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    db = _Db(
        SimpleNamespace(
            id=app_id,
            workflow_id=uuid.uuid4(),
            organization_id=organization_id,
        ),
        SimpleNamespace(
            id=workflow_id,
            app_id=app_id,
            organization_id=organization_id,
        ),
    )

    assert (
        lock_app_workflow_for_admission(
            db,
            app_id=app_id,
            workflow_id=workflow_id,
            organization_id=organization_id,
        )
        is None
    )
