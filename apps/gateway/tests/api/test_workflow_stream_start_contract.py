import json

from apps.shared import pubsub
from apps.gateway.api.v1.endpoints.workflow import (
    _serialize_workflow_sse_event,
    _stream_workflow_events,
    _workflow_stream_started_event,
)


def test_workflow_stream_start_event_exposes_only_run_identifier() -> None:
    event = _workflow_stream_started_event("11111111-1111-1111-1111-111111111111")

    assert event == {
        "type": "workflow_start",
        "data": {"run_id": "11111111-1111-1111-1111-111111111111"},
    }


def test_workflow_stream_start_event_uses_real_sse_record_delimiter() -> None:
    started = _serialize_workflow_sse_event(
        _workflow_stream_started_event("11111111-1111-1111-1111-111111111111")
    )
    next_event = _serialize_workflow_sse_event(
        {"type": "node_start", "data": {"node_id": "start"}}
    )

    records = [record for record in (started + next_event).split("\n\n") if record]

    assert len(records) == 2
    assert json.loads(records[0].removeprefix("data: "))["type"] == "workflow_start"
    assert json.loads(records[1].removeprefix("data: "))["type"] == "node_start"
    assert "\\n" not in started


def test_workflow_stream_enqueues_task_before_yielding_run_id(monkeypatch) -> None:
    actions: list[str] = []

    class FakePubSub:
        def subscribe(self, channel: str) -> None:
            assert channel == "workflow:run-1"
            actions.append("subscribed")

        def listen(self):
            return iter(())

        def unsubscribe(self, _channel: str) -> None:
            actions.append("unsubscribed")

        def close(self) -> None:
            actions.append("closed")

    class FakeRedis:
        def pubsub(self) -> FakePubSub:
            return FakePubSub()

    monkeypatch.setattr(pubsub, "get_redis_client", lambda: FakeRedis())

    def publish_task(*_args, **_kwargs) -> None:
        actions.append("enqueued")

    monkeypatch.setattr(
        "apps.gateway.api.v1.endpoints.workflow.send_workflow_task",
        publish_task,
    )

    events = _stream_workflow_events(
        external_run_id="run-1",
        celery=object(),
        graph={"nodes": [], "edges": []},
        user_input={},
        execution_context={},
    )

    first_event = next(events)
    events.close()

    assert actions[:2] == ["subscribed", "enqueued"]
    assert json.loads(first_event.removeprefix("data: ").strip())["type"] == "workflow_start"
