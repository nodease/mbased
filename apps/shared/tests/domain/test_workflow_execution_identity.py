import uuid

import pytest
from apps.shared.domain.workflow_execution_identity import (
    InvocationSegment,
    derive_node_invocation_id,
    ensure_execution_id,
    schedule_execution_id,
)


def test_execution_id_is_preserved_across_retry() -> None:
    execution_id = uuid.uuid4()

    assert ensure_execution_id(execution_id) == execution_id
    assert ensure_execution_id(str(execution_id)) == execution_id


def test_invalid_execution_id_is_rejected() -> None:
    with pytest.raises(ValueError, match="execution identity"):
        ensure_execution_id("not-a-uuid")


def test_node_invocation_is_stable_and_scope_sensitive() -> None:
    execution_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    deployment_id = uuid.uuid4()
    root = InvocationSegment("root", "", str(workflow_id))
    node = InvocationSegment("node", "http-1", "0")

    first = derive_node_invocation_id(execution_id, (root, node))
    retry = derive_node_invocation_id(execution_id, (root, node))
    loop_next = derive_node_invocation_id(
        execution_id,
        (
            root,
            InvocationSegment("loop", "loop-1", "1"),
            node,
        ),
    )
    child = derive_node_invocation_id(
        execution_id,
        (
            root,
            InvocationSegment("subworkflow", "workflow-node-1", str(deployment_id)),
            node,
        ),
    )

    assert first == retry
    assert loop_next != first
    assert child != first


def test_node_invocation_v1_fixed_vector() -> None:
    segments = (
        InvocationSegment("root", "", "00000000-0000-0000-0000-000000000002"),
        InvocationSegment("loop", "loop-1", "0"),
        InvocationSegment(
            "subworkflow",
            "child-1",
            "00000000-0000-0000-0000-000000000003",
        ),
        InvocationSegment("node", "http-1", "0"),
    )

    invocation_id = derive_node_invocation_id(
        "00000000-0000-0000-0000-000000000001",
        segments,
    )

    assert str(invocation_id) == "a22a1bdf-a0eb-5c1d-bf87-e17354a442a6"


@pytest.mark.parametrize(
    "segments",
    [
        (
            InvocationSegment(
                "root",
                "not-empty",
                "00000000-0000-0000-0000-000000000002",
            ),
            InvocationSegment("node", "node-1", "0"),
        ),
        (
            InvocationSegment("root", "", "not-a-uuid"),
            InvocationSegment("node", "node-1", "0"),
        ),
        (
            InvocationSegment(
                "root",
                "",
                "00000000-0000-0000-0000-000000000002",
            ),
            InvocationSegment("loop", "loop-1", "01"),
            InvocationSegment("node", "node-1", "0"),
        ),
        (
            InvocationSegment(
                "root",
                "",
                "00000000-0000-0000-0000-000000000002",
            ),
            InvocationSegment("node", "node-1", "-1"),
        ),
    ],
)
def test_node_invocation_rejects_noncanonical_segments(segments) -> None:
    with pytest.raises(ValueError, match="invocation segment"):
        derive_node_invocation_id(uuid.uuid4(), segments)


def test_schedule_execution_id_is_deterministic() -> None:
    claim_id = uuid.uuid4()

    assert schedule_execution_id(claim_id) == schedule_execution_id(str(claim_id))
