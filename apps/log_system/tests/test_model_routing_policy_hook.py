"""FR-011 운영 표본 집계 hook의 완료 시점 경계 테스트."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from apps.log_system import tasks as log_tasks
from apps.shared.db.models.workflow_run import RunStatus


def test_running_or_completed_node_does_not_enqueue_policy_run_record(monkeypatch):
    sent = []
    monkeypatch.setattr(
        log_tasks.celery_app,
        "send_task",
        lambda *args, **kwargs: sent.append((args, kwargs)),
    )

    log_tasks._schedule_model_routing_run_record(
        SimpleNamespace(
            status=RunStatus.RUNNING,
            id=uuid4(),
        )
    )

    assert sent == []


@pytest.mark.parametrize("status", [RunStatus.SUCCESS, RunStatus.FAILED])
def test_terminal_workflow_run_enqueues_policy_run_record(monkeypatch, status):
    sent = []
    workflow_run_id = uuid4()
    monkeypatch.setattr(
        log_tasks.celery_app,
        "send_task",
        lambda *args, **kwargs: sent.append((args, kwargs)),
    )

    log_tasks._schedule_model_routing_run_record(
        SimpleNamespace(
            status=status,
            id=workflow_run_id,
        )
    )

    assert sent == [
        (
            ("workflow.model_routing.record_run",),
            {"args": [str(workflow_run_id)]},
        )
    ]


def test_late_llm_node_log_requeues_policy_record_after_workflow_is_terminal(monkeypatch):
    workflow_run_id = uuid4()
    workflow_run = SimpleNamespace(
        id=workflow_run_id,
        status=RunStatus.SUCCESS,
    )

    class _Query:
        def __init__(self, result):
            self.result = result

        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return self.result

    class _Session:
        def query(self, *entities):
            return _Query(workflow_run)

    sent = []
    monkeypatch.setattr(
        log_tasks.celery_app,
        "send_task",
        lambda *args, **kwargs: sent.append((args, kwargs)),
    )

    log_tasks._schedule_model_routing_run_record_after_llm_node_log(
        _Session(),
        workflow_run_id,
    )

    assert sent == [
        (
            ("workflow.model_routing.record_run",),
            {"args": [str(workflow_run_id)]},
        )
    ]


def test_late_llm_node_log_does_not_requeue_before_workflow_is_terminal(monkeypatch):
    workflow_run_id = uuid4()
    sent = []

    class _Query:
        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return SimpleNamespace(id=workflow_run_id, status=RunStatus.RUNNING)

    class _Session:
        def query(self, *entities):
            return _Query()

    monkeypatch.setattr(
        log_tasks.celery_app,
        "send_task",
        lambda *args, **kwargs: sent.append((args, kwargs)),
    )

    log_tasks._schedule_model_routing_run_record_after_llm_node_log(
        _Session(),
        workflow_run_id,
    )

    assert sent == []


def test_terminal_workflow_status_is_written_into_successful_llm_trace():
    """정책 refresh 전에 terminal workflow 결과를 LLM trace의 downstream summary로 확정한다."""

    class _Query:
        def filter(self, *args, **kwargs):
            return self

        def all(self):
            return [node_run]

    class _Session:
        def query(self, *args, **kwargs):
            return _Query()

    node_run = SimpleNamespace(
        node_type="llmNode",
        trace_metadata={
            "llm": {
                "selected_model": "gpt-4.1-mini",
                "decision_factors": {
                    "classification_status": "matched",
                    "difficulty": "balanced",
                    "confidence": 0.77,
                    "match_score": 4,
                },
            }
        },
    )

    log_tasks._finalize_llm_downstream_status(
        _Session(),
        workflow_run_id=uuid4(),
        downstream_status="failed",
    )

    assert node_run.trace_metadata["llm"]["selected_model"] == "gpt-4.1-mini"
    assert node_run.trace_metadata["llm"]["downstream_status"] == "failed"
    assert node_run.trace_metadata["llm"]["decision_factors"] == {
        "classification_status": "matched",
        "difficulty": "balanced",
        "confidence": 0.77,
        "match_score": 4,
    }
