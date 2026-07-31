from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from apps.gateway.auth.dependencies import get_current_user
from apps.gateway.main import app
from apps.shared.db.session import get_db


def _system_schedule_trace(trace_id):
    return {
        "id": trace_id,
        "workflow_id": uuid4(),
        "app_id": uuid4(),
        "user_id": None,
        "deployment_id": uuid4(),
        "status": "success",
        "trigger_mode": "scheduler",
        "started_at": datetime.now(timezone.utc),
    }


def test_trace_list_and_detail_return_null_system_schedule_actor():
    client = TestClient(app)
    trace_id = uuid4()
    trace = _system_schedule_trace(trace_id)
    app.dependency_overrides[get_db] = lambda: MagicMock()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uuid4())

    try:
        with patch(
            "apps.gateway.api.v1.endpoints.tracing.TraceQueryService.list_traces",
            return_value={"total": 1, "items": [trace]},
        ):
            list_response = client.get("/api/v1/traces")

        with patch(
            "apps.gateway.api.v1.endpoints.tracing.TraceQueryService.get_trace",
            return_value=trace,
        ):
            detail_response = client.get(f"/api/v1/traces/{trace_id}")
    finally:
        app.dependency_overrides = {}

    assert list_response.status_code == 200
    assert list_response.json()["items"][0]["user_id"] is None
    assert detail_response.status_code == 200
    assert detail_response.json()["user_id"] is None
