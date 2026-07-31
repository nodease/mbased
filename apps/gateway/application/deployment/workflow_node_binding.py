from __future__ import annotations

import uuid
from collections.abc import Mapping

from apps.shared.domain.workflow_node_binding import (
    MAX_WORKFLOW_NODE_DEPTH,
    WorkflowNodeBinding,
    WorkflowNodeBindingError,
    apply_workflow_node_bindings,
    canonical_snapshot_sha256,
    graph_has_external_effect,
    parse_workflow_node_bindings,
    workflow_node_references,
)
from apps.shared.domain.workflow_graph import (
    WorkflowGraphValidationError,
    validate_workflow_graph,
)

from .models import WorkflowNodeTargetSnapshot
from .ports import DeploymentPreflightRepository


class WorkflowNodeBindingUseCase:
    def __init__(
        self,
        repository: DeploymentPreflightRepository,
        *,
        organization_id: uuid.UUID | None,
        side_effect_by_node_type: Mapping[str, str],
    ) -> None:
        self.repository = repository
        self.organization_id = organization_id
        self.side_effect_by_node_type = side_effect_by_node_type

    def bind_graph(
        self,
        graph: dict,
        *,
        root_app_id: uuid.UUID,
    ) -> dict:
        self._require_valid_graph(graph)
        bindings: list[WorkflowNodeBinding] = []
        for reference in workflow_node_references(graph):
            target = self.repository.get_workflow_node_target(
                reference.target_app_id,
                self.organization_id,
            )
            self._require_target(target, expected_app_id=reference.target_app_id)
            assert target is not None
            self._validate_target_closure(
                target,
                depth=1,
                visited={root_app_id, reference.target_app_id},
            )
            bindings.append(
                WorkflowNodeBinding(
                    container_path=reference.container_path,
                    workflow_node_id=reference.workflow_node_id,
                    target_app_id=reference.target_app_id,
                    deployment_id=target.deployment_id,
                    deployment_version=target.deployment_version,
                    snapshot_sha256=canonical_snapshot_sha256(
                        target.active_graph_snapshot
                    ),
                )
            )
        return apply_workflow_node_bindings(graph, tuple(bindings))

    def _require_target(
        self,
        target: WorkflowNodeTargetSnapshot | None,
        *,
        expected_app_id: uuid.UUID,
    ) -> None:
        if (
            target is None
            or target.app_id != expected_app_id
            or target.organization_id is None
            or (
                self.organization_id is not None
                and target.organization_id != self.organization_id
            )
            or target.workflow_id is None
            or target.deployment_id is None
            or target.deployment_version is None
            or target.deployment_type != "workflow_node"
            or not target.active_pointer_valid
            or not isinstance(target.active_graph_snapshot, dict)
        ):
            raise WorkflowNodeBindingError("workflow_node.target_unavailable")

    def _validate_target_closure(
        self,
        target: WorkflowNodeTargetSnapshot,
        *,
        depth: int,
        visited: set[uuid.UUID],
    ) -> None:
        if depth > MAX_WORKFLOW_NODE_DEPTH:
            raise WorkflowNodeBindingError("workflow_node.depth_exceeded")
        graph = target.active_graph_snapshot
        self._require_valid_graph(graph)
        references = workflow_node_references(graph)
        if not references:
            return
        parsed = parse_workflow_node_bindings(graph)
        if parsed is None:
            if self._legacy_closure_has_effect(
                graph,
                depth=depth,
                visited=set(visited),
            ):
                raise WorkflowNodeBindingError(
                    "workflow_node.child_redeployment_required"
                )
            return
        expected = {
            (reference.container_path, reference.workflow_node_id): reference
            for reference in references
        }
        actual = {
            (binding.container_path, binding.workflow_node_id): binding
            for binding in parsed
        }
        if set(expected) != set(actual):
            raise WorkflowNodeBindingError("workflow_node.binding_invalid")
        for key, reference in expected.items():
            binding = actual[key]
            if binding.target_app_id != reference.target_app_id:
                raise WorkflowNodeBindingError("workflow_node.binding_invalid")
            if binding.target_app_id in visited:
                raise WorkflowNodeBindingError("workflow_node.cycle_detected")
            child = self.repository.get_workflow_node_deployment(
                binding.target_app_id,
                binding.deployment_id,
                self.organization_id,
            )
            self._validate_bound_target(child, binding)
            assert child is not None
            self._validate_target_closure(
                child,
                depth=depth + 1,
                visited={*visited, binding.target_app_id},
            )

    def _validate_bound_target(
        self,
        target: WorkflowNodeTargetSnapshot | None,
        binding: WorkflowNodeBinding,
    ) -> None:
        self._require_target(target, expected_app_id=binding.target_app_id)
        assert target is not None
        if (
            target.app_id != binding.target_app_id
            or target.deployment_id != binding.deployment_id
            or target.deployment_version != binding.deployment_version
            or canonical_snapshot_sha256(target.active_graph_snapshot)
            != binding.snapshot_sha256
        ):
            raise WorkflowNodeBindingError("workflow_node.binding_mismatch")

    def _legacy_closure_has_effect(
        self,
        graph: dict,
        *,
        depth: int,
        visited: set[uuid.UUID],
    ) -> bool:
        self._require_valid_graph(graph)
        if graph_has_external_effect(graph, self.side_effect_by_node_type):
            return True
        if depth >= MAX_WORKFLOW_NODE_DEPTH:
            return True
        for reference in workflow_node_references(graph):
            if reference.target_app_id in visited:
                return True
            target = self.repository.get_workflow_node_target(
                reference.target_app_id,
                self.organization_id,
            )
            try:
                self._require_target(
                    target,
                    expected_app_id=reference.target_app_id,
                )
            except WorkflowNodeBindingError:
                return True
            assert target is not None
            if self._legacy_closure_has_effect(
                target.active_graph_snapshot,
                depth=depth + 1,
                visited={*visited, reference.target_app_id},
            ):
                return True
        return False

    @staticmethod
    def _require_valid_graph(graph: dict) -> None:
        try:
            validate_workflow_graph(graph)
        except WorkflowGraphValidationError as exc:
            raise WorkflowNodeBindingError("workflow_graph_invalid") from exc
