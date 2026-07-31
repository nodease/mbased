from datetime import datetime, timezone
from uuid import uuid4

import pytest
from apps.shared.schemas.workflow import WorkflowDraftRequest, WorkflowMutationContext
from pydantic import ValidationError


def test_workflow_mutation_context_accepts_agent_builder_redo() -> None:
    context = WorkflowMutationContext(
        operation_id=uuid4(),
        action="redo",
        expected_base_graph_hash="a" * 64,
        expected_workflow_updated_at=datetime.now(timezone.utc),
        catalog_version=3,
    )

    assert context.action == "redo"


def test_workflow_draft_save_requires_canonical_cas_metadata() -> None:
    with pytest.raises(ValidationError):
        WorkflowDraftRequest(nodes=[], edges=[])


def test_workflow_draft_save_accepts_canonical_cas_metadata() -> None:
    request = WorkflowDraftRequest(
        nodes=[],
        edges=[],
        expected_graph_hash="b" * 64,
        expected_updated_at=datetime.now(timezone.utc),
    )

    assert request.expected_graph_hash == "b" * 64
    assert request.expected_updated_at.tzinfo is not None
