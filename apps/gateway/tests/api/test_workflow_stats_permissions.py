import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from apps.gateway.api.v1.endpoints import workflow as workflow_endpoint


def test_workflow_stats_preserves_permission_http_exception(monkeypatch):
    workflow_id = str(uuid.uuid4())
    user = SimpleNamespace(id=uuid.uuid4())

    def deny(db, current_user, checked_workflow_id, action):
        assert checked_workflow_id == workflow_id
        assert action == "read"
        raise HTTPException(status_code=403, detail="Forbidden")

    monkeypatch.setattr(workflow_endpoint, "ensure_workflow_permission", deny)

    with pytest.raises(HTTPException) as exc_info:
        workflow_endpoint.get_workflow_stats(workflow_id, db=object(), current_user=user)

    assert exc_info.value.status_code == 403
