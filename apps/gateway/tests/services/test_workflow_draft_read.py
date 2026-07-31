from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from apps.gateway.services.workflow_service import WorkflowService


@pytest.mark.parametrize("stored_graph", [None, {}])
def test_get_draft_materializes_empty_graph_contract(stored_graph):
    workflow_id = uuid4()
    updated_at = datetime(2026, 7, 15, tzinfo=timezone.utc)
    workflow = SimpleNamespace(
        id=workflow_id,
        graph=stored_graph,
        features=None,
        updated_at=updated_at,
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = workflow

    result = WorkflowService.get_draft(
        db,
        str(workflow_id),
        include_metadata=True,
    )

    assert result["nodes"] == []
    assert result["edges"] == []
    assert result["viewport"] == {"x": 0, "y": 0, "zoom": 1}
    assert result["workflow_id"] == str(workflow_id)
    assert isinstance(result["graph_hash"], str)
    assert result["updated_at"] == updated_at.isoformat()


@pytest.mark.parametrize("stored_graph", [None, {}])
def test_get_draft_without_metadata_preserves_empty_graph_guard(stored_graph):
    workflow_id = uuid4()
    workflow = SimpleNamespace(
        id=workflow_id,
        graph=stored_graph,
        features=None,
        updated_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = workflow

    result = WorkflowService.get_draft(db, str(workflow_id))

    assert result == {}


def test_get_draft_rejects_invalid_nested_graph_hash_with_safe_422():
    workflow_id = uuid4()
    workflow = SimpleNamespace(
        id=workflow_id,
        graph={
            "nodes": [
                {
                    "id": "loop-1",
                    "type": "loopNode",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "subGraph": {
                            "nodes": [
                                {
                                    "id": "nested-1",
                                    "type": "codeNode",
                                    "data": {},
                                }
                            ],
                            "edges": [],
                        }
                    },
                }
            ],
            "edges": [],
        },
        features=None,
        updated_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = workflow

    with pytest.raises(HTTPException) as exc_info:
        WorkflowService.get_draft(
            db,
            str(workflow_id),
            include_metadata=True,
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "workflow.graph_invalid"
