from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from apps.shared.db.models.workflow_budget import WorkflowBudget
from apps.shared.services.workflow_budget_execution import (
    evaluate_workflow_budget_execution,
)


class _Query:
    def __init__(self, row):
        self.row = row

    def filter(self, *args):
        return self

    def first(self):
        return self.row


class _Db:
    def __init__(self, budget, usage_logs):
        self.budget = budget
        self.usage_logs = usage_logs
        self.commits = 0

    def query(self, model):
        assert model is WorkflowBudget
        return _Query(self.budget)


class _NestedTransaction:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self

    def __exit__(self, exc_type, _exc, _traceback):
        if exc_type is not None:
            self.db.transaction_aborted = False
            self.db.savepoint_rollbacks += 1
        return False


class _FailingAggregateQuery(_Query):
    def __init__(self, db):
        super().__init__(None)
        self.db = db

    def scalar(self):
        self.db.transaction_aborted = True
        raise RuntimeError("database aggregate failed")


class _FailingAggregateDb:
    def __init__(self, budget):
        self.budget = budget
        self.query_count = 0
        self.savepoint_rollbacks = 0
        self.transaction_aborted = False
        self.state_writes = 0

    def begin_nested(self):
        return _NestedTransaction(self)

    def query(self, _model):
        self.query_count += 1
        if self.query_count == 1:
            return _Query(self.budget)
        return _FailingAggregateQuery(self)

    def write_state(self):
        if self.transaction_aborted:
            raise RuntimeError("transaction still requires rollback")
        self.state_writes += 1


class _BeginNestedFailureDb:
    def begin_nested(self):
        raise RuntimeError("savepoint unavailable")


def test_shared_budget_evaluator_is_side_effect_free_and_uses_kst_month():
    workflow_id = uuid4()
    now = datetime(2026, 7, 10, tzinfo=ZoneInfo("Asia/Seoul"))
    db = _Db(
        SimpleNamespace(
            workflow_id=workflow_id,
            is_enabled=True,
            monthly_budget_usd=Decimal("100"),
        ),
        [
            SimpleNamespace(
                workflow_id=workflow_id,
                organization_id=None,
                prompt_tokens=0,
                completion_tokens=0,
                total_cost=Decimal("101"),
                created_at=now,
                runtime_surface=None,
                status="success",
            )
        ],
    )

    decision = evaluate_workflow_budget_execution(
        db,
        workflow_id=workflow_id,
        now=now,
    )

    assert decision.status == "blocked"
    assert db.commits == 0


def test_failed_budget_query_rolls_back_savepoint_before_caller_state_write():
    workflow_id = uuid4()
    db = _FailingAggregateDb(
        SimpleNamespace(
            workflow_id=workflow_id,
            is_enabled=True,
            monthly_budget_usd=Decimal("100"),
        )
    )

    decision = evaluate_workflow_budget_execution(
        db,
        workflow_id=workflow_id,
        now=datetime(2026, 7, 10, tzinfo=ZoneInfo("Asia/Seoul")),
    )
    db.write_state()

    assert decision.status == "unavailable"
    assert db.savepoint_rollbacks == 1
    assert db.state_writes == 1


def test_savepoint_creation_failure_is_propagated_for_outer_uow_rollback():
    with pytest.raises(RuntimeError, match="savepoint unavailable"):
        evaluate_workflow_budget_execution(
            _BeginNestedFailureDb(),
            workflow_id=uuid4(),
            now=datetime(2026, 7, 10, tzinfo=ZoneInfo("Asia/Seoul")),
        )
