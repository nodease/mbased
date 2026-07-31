from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from apps.gateway.application.agent_builder.graph_mutation_builder import (
    canonical_graph_hash,
    materialize_candidate_graph,
    validate_candidate_graph,
)
from apps.shared.schemas.agent_builder import GraphMutationSafeEnvelope
from apps.shared.schemas.workflow import WorkflowDraftRequest


class WorkflowMutationConflict(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ValidatedWorkflowMutation:
    operation_id: UUID
    graph: dict[str, Any]
    graph_hash: str


def _normalized_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _stored_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _normalized_datetime(value)
    if not isinstance(value, str):
        return None
    try:
        return _normalized_datetime(datetime.fromisoformat(value))
    except ValueError:
        return None


class WorkflowDraftCASService:
    @staticmethod
    def validate_expected_draft_state(
        *,
        workflow: Any,
        request: WorkflowDraftRequest,
    ) -> None:
        if request.expected_graph_hash != canonical_graph_hash(workflow.graph):
            raise WorkflowMutationConflict("stale_graph")
        if _normalized_datetime(request.expected_updated_at) != _normalized_datetime(
            workflow.updated_at
        ):
            raise WorkflowMutationConflict("stale_graph")

    @staticmethod
    def _candidate_graph(request: WorkflowDraftRequest) -> dict[str, Any]:
        return materialize_candidate_graph({
            "nodes": [node.model_dump(mode="python") for node in request.nodes],
            "edges": [edge.model_dump(mode="python") for edge in request.edges],
            "viewport": (
                request.viewport.model_dump(mode="python")
                if request.viewport is not None
                else None
            ),
        })

    @staticmethod
    def validate_candidate(
        *,
        workflow: Any,
        request: WorkflowDraftRequest,
        envelope: GraphMutationSafeEnvelope,
    ) -> ValidatedWorkflowMutation:
        if envelope.status not in {"pending_apply", "pending_save"}:
            raise WorkflowMutationConflict("operation_not_applicable")
        context = request.mutation_context
        if context is None:
            raise WorkflowMutationConflict("mutation_context_required")
        if context.operation_id != envelope.operation_id:
            raise WorkflowMutationConflict("operation_mismatch")
        if str(workflow.id) != str(envelope.workflow_id):
            raise WorkflowMutationConflict("workflow_mismatch")
        current_hash = canonical_graph_hash(workflow.graph)
        if (
            context.expected_base_graph_hash != current_hash
            or envelope.base_graph_hash != current_hash
        ):
            raise WorkflowMutationConflict("stale_graph")
        current_updated_at = _normalized_datetime(workflow.updated_at)
        if (
            _normalized_datetime(context.expected_workflow_updated_at)
            != current_updated_at
            or _normalized_datetime(envelope.expected_workflow_updated_at)
            != current_updated_at
        ):
            raise WorkflowMutationConflict("stale_workflow_updated_at")

        graph = WorkflowDraftCASService._candidate_graph(request)
        validate_candidate_graph(graph)
        result_hash = canonical_graph_hash(graph)
        if result_hash != envelope.expected_result_graph_hash:
            raise WorkflowMutationConflict("result_graph_hash_mismatch")
        return ValidatedWorkflowMutation(
            operation_id=envelope.operation_id,
            graph=graph,
            graph_hash=result_hash,
        )

    @staticmethod
    def validate_saved_retry(
        *,
        workflow: Any,
        request: WorkflowDraftRequest,
        envelope: GraphMutationSafeEnvelope,
    ) -> ValidatedWorkflowMutation:
        if envelope.status not in {"pending_ack", "acknowledged"}:
            raise WorkflowMutationConflict("operation_not_saved")
        context = request.mutation_context
        if context is None:
            raise WorkflowMutationConflict("mutation_context_required")
        if context.operation_id != envelope.operation_id:
            raise WorkflowMutationConflict("operation_mismatch")
        if str(workflow.id) != str(envelope.workflow_id):
            raise WorkflowMutationConflict("workflow_mismatch")
        if context.expected_base_graph_hash != envelope.base_graph_hash:
            raise WorkflowMutationConflict("saved_retry_mismatch")
        if (
            _normalized_datetime(context.expected_workflow_updated_at)
            != _normalized_datetime(envelope.expected_workflow_updated_at)
        ):
            raise WorkflowMutationConflict("saved_retry_mismatch")
        graph = WorkflowDraftCASService._candidate_graph(request)
        validate_candidate_graph(graph)
        candidate_hash = canonical_graph_hash(graph)
        current_hash = canonical_graph_hash(workflow.graph)
        if (
            envelope.result_graph_hash is None
            or candidate_hash != envelope.expected_result_graph_hash
            or current_hash != envelope.result_graph_hash
            or envelope.result_graph_hash != envelope.expected_result_graph_hash
            or _normalized_datetime(workflow.updated_at)
            != _normalized_datetime(envelope.saved_workflow_updated_at)
        ):
            raise WorkflowMutationConflict("saved_retry_mismatch")
        return ValidatedWorkflowMutation(
            operation_id=envelope.operation_id,
            graph=graph,
            graph_hash=current_hash,
        )

    @staticmethod
    def validate_revert_candidate(
        *,
        workflow: Any,
        request: WorkflowDraftRequest,
        envelope: GraphMutationSafeEnvelope,
    ) -> ValidatedWorkflowMutation:
        if envelope.kind not in {"initial_graph", "graph_edit", "replace_workflow"}:
            raise WorkflowMutationConflict("operation_not_history_boundary")
        context = request.mutation_context
        if context is None or context.action != "revert":
            raise WorkflowMutationConflict("revert_context_required")
        if context.operation_id != envelope.operation_id:
            raise WorkflowMutationConflict("operation_mismatch")
        if str(workflow.id) != str(envelope.workflow_id):
            raise WorkflowMutationConflict("workflow_mismatch")
        if envelope.status not in {"acknowledged", "reverted"} or not (
            envelope.result_graph_hash and envelope.saved_workflow_updated_at
        ):
            raise WorkflowMutationConflict("operation_not_acknowledged")
        current_hash = canonical_graph_hash(workflow.graph)
        if (
            current_hash != envelope.result_graph_hash
            or context.expected_base_graph_hash != current_hash
        ):
            raise WorkflowMutationConflict("stale_graph")
        if (
            _normalized_datetime(context.expected_workflow_updated_at)
            != _normalized_datetime(workflow.updated_at)
            or _normalized_datetime(envelope.saved_workflow_updated_at)
            != _normalized_datetime(workflow.updated_at)
        ):
            raise WorkflowMutationConflict("stale_workflow_updated_at")
        graph = WorkflowDraftCASService._candidate_graph(request)
        validate_candidate_graph(graph)
        result_hash = canonical_graph_hash(graph)
        if result_hash != envelope.base_graph_hash:
            raise WorkflowMutationConflict("revert_graph_hash_mismatch")
        return ValidatedWorkflowMutation(
            operation_id=envelope.operation_id,
            graph=graph,
            graph_hash=result_hash,
        )

    @staticmethod
    def validate_reverted_retry(
        *,
        workflow: Any,
        request: WorkflowDraftRequest,
        boundary: dict[str, Any],
    ) -> ValidatedWorkflowMutation:
        context = request.mutation_context
        if context is None or context.action != "revert":
            raise WorkflowMutationConflict("revert_context_required")
        if boundary.get("status") != "reverted":
            raise WorkflowMutationConflict("history_boundary_not_reverted")
        try:
            operation_id = UUID(str(boundary["operation_id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise WorkflowMutationConflict("history_boundary_invalid") from exc
        if context.operation_id != operation_id:
            raise WorkflowMutationConflict("operation_mismatch")
        if str(workflow.id) != str(boundary.get("workflow_id")):
            raise WorkflowMutationConflict("workflow_mismatch")
        pre_run = boundary.get("pre_run_snapshot")
        latest_final = boundary.get("latest_final_graph")
        if not isinstance(pre_run, dict) or not isinstance(latest_final, dict):
            raise WorkflowMutationConflict("history_boundary_incomplete")
        current_hash = canonical_graph_hash(workflow.graph)
        if (
            current_hash != pre_run.get("graph_hash")
            or context.expected_base_graph_hash != latest_final.get("graph_hash")
        ):
            raise WorkflowMutationConflict("stale_graph")
        if (
            _normalized_datetime(context.expected_workflow_updated_at)
            != _stored_datetime(latest_final.get("workflow_updated_at"))
        ):
            raise WorkflowMutationConflict("stale_workflow_updated_at")
        graph = WorkflowDraftCASService._candidate_graph(request)
        validate_candidate_graph(graph)
        result_hash = canonical_graph_hash(graph)
        if result_hash != pre_run.get("graph_hash"):
            raise WorkflowMutationConflict("revert_graph_hash_mismatch")
        return ValidatedWorkflowMutation(
            operation_id=operation_id,
            graph=graph,
            graph_hash=current_hash,
        )

    @staticmethod
    def validate_history_action_retry(
        *,
        workflow: Any,
        request: WorkflowDraftRequest,
        boundary: dict[str, Any],
    ) -> ValidatedWorkflowMutation | None:
        context = request.mutation_context
        receipt = boundary.get("last_persisted_action")
        if (
            context is None
            or context.action not in {"revert", "redo"}
            or not isinstance(receipt, dict)
            or str(receipt.get("operation_id")) != str(context.operation_id)
            or receipt.get("action") != context.action
        ):
            return None
        graph = WorkflowDraftCASService._candidate_graph(request)
        validate_candidate_graph(graph)
        candidate_hash = canonical_graph_hash(graph)
        if (
            receipt.get("candidate_graph_hash") != candidate_hash
            or receipt.get("expected_base_graph_hash")
            != context.expected_base_graph_hash
            or _stored_datetime(receipt.get("expected_workflow_updated_at"))
            != _normalized_datetime(context.expected_workflow_updated_at)
        ):
            return None
        current_hash = canonical_graph_hash(workflow.graph)
        if current_hash != receipt.get("result_graph_hash"):
            raise WorkflowMutationConflict("stale_graph")
        if _normalized_datetime(workflow.updated_at) != _stored_datetime(
            receipt.get("result_workflow_updated_at")
        ):
            raise WorkflowMutationConflict("stale_workflow_updated_at")
        return ValidatedWorkflowMutation(
            operation_id=context.operation_id,
            graph=graph,
            graph_hash=current_hash,
        )

    @staticmethod
    def validate_redo_candidate(
        *,
        workflow: Any,
        request: WorkflowDraftRequest,
        boundary: dict[str, Any],
    ) -> ValidatedWorkflowMutation:
        context = request.mutation_context
        if context is None or context.action != "redo":
            raise WorkflowMutationConflict("redo_context_required")
        if boundary.get("status") != "reverted":
            raise WorkflowMutationConflict("history_boundary_not_reverted")
        try:
            operation_id = UUID(str(boundary["operation_id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise WorkflowMutationConflict("history_boundary_invalid") from exc
        if context.operation_id != operation_id:
            raise WorkflowMutationConflict("operation_mismatch")
        if str(workflow.id) != str(boundary.get("workflow_id")):
            raise WorkflowMutationConflict("workflow_mismatch")
        pre_run = boundary.get("pre_run_snapshot")
        latest_final = boundary.get("latest_final_graph")
        if not isinstance(pre_run, dict) or not isinstance(latest_final, dict):
            raise WorkflowMutationConflict("history_boundary_incomplete")
        if _stored_datetime(latest_final.get("workflow_updated_at")) is None:
            raise WorkflowMutationConflict("history_boundary_incomplete")
        current_hash = canonical_graph_hash(workflow.graph)
        if (
            current_hash != pre_run.get("graph_hash")
            or context.expected_base_graph_hash != current_hash
        ):
            raise WorkflowMutationConflict("stale_graph")
        if (
            _normalized_datetime(context.expected_workflow_updated_at)
            != _normalized_datetime(workflow.updated_at)
        ):
            raise WorkflowMutationConflict("stale_workflow_updated_at")
        graph = WorkflowDraftCASService._candidate_graph(request)
        validate_candidate_graph(graph)
        result_hash = canonical_graph_hash(graph)
        if result_hash != latest_final.get("graph_hash"):
            raise WorkflowMutationConflict("redo_graph_hash_mismatch")
        return ValidatedWorkflowMutation(
            operation_id=operation_id,
            graph=graph,
            graph_hash=result_hash,
        )
