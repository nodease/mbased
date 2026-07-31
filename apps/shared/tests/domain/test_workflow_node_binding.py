import uuid
from datetime import datetime, timezone

import pytest
from apps.shared.db.models.workflow_deployment import DeploymentType
from apps.shared.domain.workflow_node_binding import (
    WorkflowNodeBinding,
    WorkflowNodeBindingError,
    apply_workflow_node_bindings,
    canonical_snapshot_sha256,
    graph_has_external_effect,
    parse_workflow_node_bindings,
    strip_workflow_node_bindings,
    workflow_node_references,
)
from apps.shared.schemas.deployment import DeploymentResponse

SIDE_EFFECTS = {
    "startNode": "none",
    "httpRequestNode": "external_write",
    "githubNode": "external_write",
    "workflowNode": "local_execution",
    "loopNode": "local_execution",
    "gmailDraftNode": "external_write",
}


def test_loop_aware_reference_and_binding_round_trip() -> None:
    target_app_id = uuid.uuid4()
    graph = {
        "nodes": [
            {
                "id": "loop-1",
                "type": "loopNode",
                "data": {
                    "subGraph": {
                        "nodes": [
                            {
                                "id": "child-1",
                                "type": "workflowNode",
                                "data": {"appId": str(target_app_id)},
                            }
                        ],
                        "edges": [],
                    }
                },
            }
        ],
        "edges": [],
        "_nodease_runtime": {"workflow_node_bindings": {"forged": True}},
    }
    reference = workflow_node_references(graph)[0]
    binding = WorkflowNodeBinding(
        container_path=reference.container_path,
        workflow_node_id=reference.workflow_node_id,
        target_app_id=target_app_id,
        deployment_id=uuid.uuid4(),
        deployment_version=1,
        snapshot_sha256="a" * 64,
    )

    bound = apply_workflow_node_bindings(graph, (binding,))

    assert parse_workflow_node_bindings(bound) == (binding,)
    assert reference.container_path == (("loop", "loop-1"),)
    assert "_nodease_runtime" not in strip_workflow_node_bindings(bound)


def test_binding_parser_rejects_noncanonical_container_path() -> None:
    payload = {
        "_nodease_runtime": {
            "workflow_node_bindings": {
                "version": "workflow-node-bindings.v1",
                "entries": [
                    {
                        "container_path": [{"kind": "loop", "node_id": ""}],
                        "workflow_node_id": "child-1",
                        "target_app_id": str(uuid.uuid4()),
                        "deployment_id": str(uuid.uuid4()),
                        "deployment_version": 1,
                        "snapshot_sha256": "a" * 64,
                    }
                ],
            }
        }
    }

    with pytest.raises(WorkflowNodeBindingError) as exc_info:
        parse_workflow_node_bindings(payload)

    assert exc_info.value.code == "workflow_node.binding_invalid"


def test_binding_constructor_rejects_noncanonical_location() -> None:
    with pytest.raises(WorkflowNodeBindingError) as exc_info:
        WorkflowNodeBinding(
            container_path=(("loop", ""),),
            workflow_node_id="child-1",
            target_app_id=uuid.uuid4(),
            deployment_id=uuid.uuid4(),
            deployment_version=1,
            snapshot_sha256="a" * 64,
        )

    assert exc_info.value.code == "workflow_node.binding_invalid"


def test_snapshot_hash_rejects_nan() -> None:
    with pytest.raises(WorkflowNodeBindingError):
        canonical_snapshot_sha256({"value": float("nan")})


def test_effect_classifier_only_lowers_documented_read_operations() -> None:
    assert not graph_has_external_effect(
        {"nodes": [{"id": "note-1", "type": "note", "data": {}}]},
        SIDE_EFFECTS,
    )
    assert not graph_has_external_effect(
        {"nodes": [{"id": "h", "type": "httpRequestNode", "data": {"method": "GET"}}]},
        SIDE_EFFECTS,
    )
    assert not graph_has_external_effect(
        {"nodes": [{"id": "g", "type": "githubNode", "data": {"action": "get_pr"}}]},
        SIDE_EFFECTS,
    )
    assert graph_has_external_effect(
        {"nodes": [{"id": "h", "type": "httpRequestNode", "data": {"method": "POST"}}]},
        SIDE_EFFECTS,
    )
    assert graph_has_external_effect(
        {"nodes": [{"id": "m", "type": "gmailDraftNode", "data": {}}]},
        SIDE_EFFECTS,
    )
    assert graph_has_external_effect(
        {"nodes": [{"id": "future", "type": "unknownNode", "data": {}}]},
        SIDE_EFFECTS,
    )


def test_effect_classifier_fails_closed_for_malformed_nodes_and_catalog_values() -> None:
    assert graph_has_external_effect(
        {"nodes": [{"type": "httpRequestNode", "data": {"method": "GET"}}]},
        SIDE_EFFECTS,
    )
    assert graph_has_external_effect(
        {"nodes": [{"id": "http", "type": "httpRequestNode", "data": None}]},
        SIDE_EFFECTS,
    )
    assert graph_has_external_effect(
        {"nodes": [{"id": "start", "type": "startNode", "data": {}}]},
        {**SIDE_EFFECTS, "startNode": "invalid"},
    )


def test_deployment_response_hides_server_owned_binding_metadata() -> None:
    response = DeploymentResponse(
        id=uuid.uuid4(),
        app_id=uuid.uuid4(),
        version=1,
        type=DeploymentType.API,
        created_by=uuid.uuid4(),
        created_at=datetime.now(timezone.utc),
        graph_snapshot={
            "nodes": [],
            "edges": [],
            "_nodease_runtime": {
                "workflow_node_bindings": {
                    "version": "workflow-node-bindings.v1",
                    "entries": [],
                }
            },
        },
    )

    serialized = response.model_dump(mode="json")

    assert "_nodease_runtime" not in serialized["graph_snapshot"]
