import uuid
from types import SimpleNamespace

import pytest

from apps.gateway.services.app_lifecycle_lock import (
    AppPrimaryChangedDuringMutationError,
    lock_app_for_lifecycle,
    lock_app_for_workflow_mutation,
)
from apps.shared.db.models.app import App


class _Query:
    def __init__(self, row):
        self.row = row
        self.filters = []
        self.locked = False
        self.refreshed = False

    def filter(self, *expressions):
        self.filters.extend(expressions)
        return self

    def with_for_update(self):
        self.locked = True
        return self

    def populate_existing(self):
        self.refreshed = True
        return self

    def first(self):
        return self.row


class _Db:
    def __init__(self, row):
        self.query_value = _Query(row)

    def query(self, model):
        assert model is App
        return self.query_value


def test_app_lifecycle_lock_filters_scope_and_uses_for_update():
    row = SimpleNamespace(id=uuid.uuid4(), organization_id=uuid.uuid4())
    db = _Db(row)

    result = lock_app_for_lifecycle(
        db,
        row.id,
        organization_id=row.organization_id,
    )

    assert result is row
    assert db.query_value.locked is True
    assert db.query_value.refreshed is True
    assert len(db.query_value.filters) == 2


class _SequencedDb:
    def __init__(self, *rows):
        self.rows = iter(rows)
        self.queries = []

    def query(self, model):
        assert model is App
        query = _Query(next(self.rows))
        self.queries.append(query)
        return query


def test_workflow_mutation_rejects_primary_changed_while_waiting_for_app_lock():
    app_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    old_workflow_id = uuid.uuid4()
    observed = SimpleNamespace(
        id=app_id,
        organization_id=organization_id,
        workflow_id=old_workflow_id,
    )
    locked = SimpleNamespace(
        id=app_id,
        organization_id=organization_id,
        workflow_id=uuid.uuid4(),
    )
    db = _SequencedDb(observed, locked)

    with pytest.raises(AppPrimaryChangedDuringMutationError):
        lock_app_for_workflow_mutation(
            db,
            app_id=app_id,
            workflow_id=old_workflow_id,
            organization_id=organization_id,
        )

    assert db.queries[0].refreshed is True
    assert db.queries[0].locked is False
    assert db.queries[1].refreshed is True
    assert db.queries[1].locked is True


def test_workflow_mutation_keeps_existing_non_primary_workflow_behavior():
    app_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    requested_workflow_id = uuid.uuid4()
    observed = SimpleNamespace(
        id=app_id,
        organization_id=organization_id,
        workflow_id=uuid.uuid4(),
    )
    locked = SimpleNamespace(
        id=app_id,
        organization_id=organization_id,
        workflow_id=uuid.uuid4(),
    )
    db = _SequencedDb(observed, locked)

    result = lock_app_for_workflow_mutation(
        db,
        app_id=app_id,
        workflow_id=requested_workflow_id,
        organization_id=organization_id,
    )

    assert result is locked
