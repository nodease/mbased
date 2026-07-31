from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID
from typing import Literal, cast

from fastapi import HTTPException
from sqlalchemy.orm import Session

from apps.gateway.adapters.db.agent_builder_repository import (
    AgentBuilderRepository,
    AgentBuilderRepositoryError,
)
from apps.gateway.application.agent_builder.graph_mutation_builder import (
    canonical_graph_hash,
)
from apps.gateway.services.audit_records import add_action_audit
from apps.shared.db.models.agent_builder import AgentBuilderRequest, AgentBuilderSession
from apps.shared.db.models.workflow import Workflow
from apps.shared.schemas.agent_builder import (
    GraphMutationAcknowledgementRequest,
    GraphMutationAcknowledgementResponse,
    AgentBuilderParameterTask,
)
from apps.shared.services.permissions import has_workflow_permission
from apps.gateway.application.agent_builder.parameter_tasks import (
    PreparedParameterDecision,
    acknowledge_parameter_binding,
    acknowledge_task_decision,
    refresh_parameter_group_configuration,
    refresh_parameter_group_suggestions,
    reconcile_parameter_group_catalog_tasks,
)
from apps.gateway.application.agent_builder.service import (
    plan_parameter_tasks_for_existing_graph,
)
from apps.gateway.services.agent_builder.parameter_candidates import (
    ParameterCandidateProvider,
)
from apps.gateway.application.agent_builder.condition_branches import (
    reconcile_condition_branch_tasks,
)


def _refresh_parameter_group(group, graph):
    step_node_ids = {
        str(task.step_id): str(task.node_id)
        for task in group.tasks
        if task.step_id and task.node_id
    }
    if step_node_ids:
        catalog_tasks = plan_parameter_tasks_for_existing_graph(
            graph=graph,
            step_node_ids=step_node_ids,
            group_id=group.group_id,
            affected_node_ids=set(step_node_ids.values()),
        )
        group = reconcile_parameter_group_catalog_tasks(group, catalog_tasks)
    group = reconcile_condition_branch_tasks(group, graph)
    group = refresh_parameter_group_configuration(group, graph)
    return refresh_parameter_group_suggestions(group, graph)


@dataclass(frozen=True)
class HistoryBoundaryRestorePlan:
    operation_id: UUID
    pre_run_graph_hash: str
    final_graph_hash: str
    expected_workflow_updated_at: datetime


class GraphMutationLifecycleService:
    def __init__(
        self,
        db: Session,
        *,
        user_id: UUID,
        organization_id: UUID,
        repository: AgentBuilderRepository | None = None,
    ) -> None:
        self.db = db
        self.user_id = user_id
        self.organization_id = organization_id
        self.repository = repository or AgentBuilderRepository()

    @staticmethod
    def _stored_datetime(value: object) -> datetime:
        if isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(str(value))
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=409,
                detail="history_boundary_incomplete",
            ) from exc

    def _history_boundary_context(
        self,
        session_id: UUID,
    ) -> tuple[Workflow, AgentBuilderRequest, dict[str, object]]:
        session = (
            self.db.query(AgentBuilderSession)
            .filter(
                AgentBuilderSession.id == session_id,
                AgentBuilderSession.user_id == self.user_id,
                AgentBuilderSession.organization_id == self.organization_id,
            )
            .with_for_update()
            .first()
        )
        if session is None or session.workflow_id is None:
            raise HTTPException(status_code=404, detail="session_not_found")
        workflow = (
            self.db.query(Workflow)
            .filter(
                Workflow.id == session.workflow_id,
                Workflow.organization_id == self.organization_id,
            )
            .with_for_update()
            .first()
        )
        if workflow is None:
            raise HTTPException(status_code=404, detail="workflow_not_found")
        if not has_workflow_permission(
            self.db,
            self.user_id,
            workflow.id,
            "write",
            organization_id=self.organization_id,
        ):
            raise HTTPException(status_code=403, detail="Forbidden")
        requests = (
            self.db.query(AgentBuilderRequest)
            .filter(AgentBuilderRequest.session_id == session.id)
            .order_by(AgentBuilderRequest.created_at.desc())
            .with_for_update()
            .all()
        )
        for request_row in requests:
            boundary = self.repository.load_history_boundary(request_row)
            if boundary is not None and str(boundary.get("workflow_id")) == str(
                workflow.id
            ):
                return workflow, request_row, boundary
        raise HTTPException(status_code=404, detail="history_boundary_not_found")

    def _validated_restore_plan(
        self,
        workflow: Workflow,
        boundary: dict[str, object],
    ) -> HistoryBoundaryRestorePlan:
        latest = boundary.get("latest_final_graph")
        pre_run = boundary.get("pre_run_snapshot")
        if (
            boundary.get("status") != "completed"
            or boundary.get("pending_operation_id") is not None
            or not isinstance(latest, dict)
            or not isinstance(pre_run, dict)
        ):
            detail = (
                "history_boundary_incomplete"
                if boundary.get("status") == "active"
                else "acknowledgement_required"
            )
            raise HTTPException(status_code=409, detail=detail)
        final_graph_hash = str(latest.get("graph_hash") or "")
        pre_run_graph_hash = str(pre_run.get("graph_hash") or "")
        updated_at = self._stored_datetime(latest.get("workflow_updated_at"))
        if (
            canonical_graph_hash(workflow.graph) != final_graph_hash
            or workflow.updated_at != updated_at
        ):
            raise HTTPException(status_code=409, detail="stale_graph")
        try:
            operation_id = UUID(str(boundary["operation_id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=409,
                detail="history_boundary_incomplete",
            ) from exc
        return HistoryBoundaryRestorePlan(
            operation_id=operation_id,
            pre_run_graph_hash=pre_run_graph_hash,
            final_graph_hash=final_graph_hash,
            expected_workflow_updated_at=updated_at,
        )

    def reopen_last_parameter_task(
        self,
        session_id: UUID,
    ) -> AgentBuilderParameterTask | None:
        workflow, request_row, boundary = self._history_boundary_context(session_id)
        self._validated_restore_plan(workflow, boundary)
        if boundary.get("status") != "completed":
            return None
        return self.repository.last_reopenable_parameter_task(request_row)

    def prepare_history_boundary_restore(
        self,
        session_id: UUID,
    ) -> HistoryBoundaryRestorePlan:
        workflow, _request_row, boundary = self._history_boundary_context(session_id)
        return self._validated_restore_plan(workflow, boundary)

    def acknowledge(
        self,
        session_id: UUID,
        operation_id: UUID,
        payload: GraphMutationAcknowledgementRequest,
    ) -> GraphMutationAcknowledgementResponse:
        session = (
            self.db.query(AgentBuilderSession)
            .filter(
                AgentBuilderSession.id == session_id,
                AgentBuilderSession.user_id == self.user_id,
                AgentBuilderSession.organization_id == self.organization_id,
            )
            .with_for_update()
            .first()
        )
        if session is None or session.workflow_id is None:
            raise HTTPException(status_code=404, detail="session_not_found")
        workflow = (
            self.db.query(Workflow)
            .filter(
                Workflow.id == session.workflow_id,
                Workflow.organization_id == self.organization_id,
            )
            .with_for_update()
            .first()
        )
        if workflow is None:
            raise HTTPException(status_code=404, detail="workflow_not_found")
        if workflow.id != payload.workflow_id:
            raise HTTPException(status_code=409, detail="acknowledgement_mismatch")
        if not has_workflow_permission(
            self.db,
            self.user_id,
            workflow.id,
            "write",
            organization_id=self.organization_id,
        ):
            raise HTTPException(status_code=403, detail="Forbidden")
        if (
            canonical_graph_hash(workflow.graph) != payload.graph_hash
            or workflow.updated_at != payload.updated_at
        ):
            raise HTTPException(status_code=409, detail="acknowledgement_mismatch")
        requests = (
            self.db.query(AgentBuilderRequest)
            .filter(AgentBuilderRequest.session_id == session.id)
            .order_by(AgentBuilderRequest.created_at.desc())
            .with_for_update()
            .all()
        )
        request_row = None
        existing_envelope = None
        for candidate in requests:
            try:
                existing_envelope = self.repository.find_envelope(
                    candidate, operation_id
                )
                request_row = candidate
                break
            except AgentBuilderRepositoryError:
                continue
        if request_row is None:
            raise HTTPException(status_code=404, detail="operation_not_found")
        try:
            was_acknowledged = (
                existing_envelope is not None
                and existing_envelope.get("status") == "acknowledged"
            )
            acknowledged = self.repository.acknowledge_envelope(
                request_row,
                operation_id=operation_id,
                result_graph_hash=payload.graph_hash,
                workflow_updated_at=payload.updated_at,
            )
            completion_context = acknowledged.get("completion_context") or {}
            parameter_task_id = completion_context.get("parameter_task_id")
            knowledge_resolution_id = completion_context.get(
                "knowledge_resolution_id"
            )
            parameter_group = None
            next_task_id = None
            if was_acknowledged:
                if parameter_task_id:
                    parameter_group = self.repository.load_parameter_group_for_task(
                        request_row, UUID(str(parameter_task_id))
                    )
                elif knowledge_resolution_id:
                    parameter_group = self.repository.load_latest_parameter_group(
                        request_row
                    )
                elif acknowledged.get("kind") in {
                    "initial_graph",
                    "graph_edit",
                    "replace_workflow",
                }:
                    parameter_group = self.repository.load_latest_parameter_group(
                        request_row
                    )
                next_task = (
                    next(
                        (
                            task
                            for task in parameter_group.tasks
                            if task.status == "active"
                        ),
                        None,
                    )
                    if parameter_group is not None
                    else None
                )
                next_task_id = next_task.task_id if next_task is not None else None
                self.db.commit()
                return GraphMutationAcknowledgementResponse(
                    operation_id=operation_id,
                    operation_status="acknowledged",
                    graph_hash=payload.graph_hash,
                    updated_at=payload.updated_at,
                    parameter_group=ParameterCandidateProvider(
                        self.db,
                        user_id=self.user_id,
                        organization_id=self.organization_id,
                    ).enrich_group(parameter_group, graph=workflow.graph),
                    completed_task_id=(
                        UUID(str(parameter_task_id)) if parameter_task_id else None
                    ),
                    completed_knowledge_resolution_id=(
                        str(knowledge_resolution_id)
                        if knowledge_resolution_id
                        else None
                    ),
                    next_task_id=next_task_id,
                )
            if parameter_task_id:
                pending = self.repository.find_pending_task_decision(
                    request_row, operation_id
                )
                if pending is None:
                    raise AgentBuilderRepositoryError(
                        "pending parameter decision not found"
                    )
                group = self.repository.load_parameter_group_for_task(
                    request_row, UUID(str(parameter_task_id))
                )
                decision = PreparedParameterDecision(
                    operation_id=operation_id,
                    task_id=UUID(str(parameter_task_id)),
                    action=cast(
                        Literal["set", "clear", "defer", "skip", "previous"],
                        str(pending["action"]),
                    ),
                    expected_task_version=int(pending["expected_task_version"]),
                    awaiting_persistence_ack=True,
                    graph_data_patch=None,
                )
                tasks = acknowledge_task_decision(group.tasks, decision)
                terminal = {"completed", "skipped", "deferred", "canceled"}
                group = group.model_copy(
                    update={
                        "tasks": tasks,
                        "status": (
                            "completed"
                            if all(task.status in terminal for task in tasks)
                            else "active"
                        ),
                    }
                )
                group = _refresh_parameter_group(group, workflow.graph)
                self.repository.store_parameter_group(request_row, group)
                parameter_group = group
                next_task = next(
                    (task for task in group.tasks if task.status == "active"), None
                )
                next_task_id = next_task.task_id if next_task is not None else None
                self.repository.remove_pending_task_decision(
                    request_row, operation_id
                )
            elif acknowledged.get("kind") in {
                "initial_graph",
                "graph_edit",
                "replace_workflow",
            }:
                parameter_group = self.repository.activate_latest_parameter_group(
                    request_row
                )
                if parameter_group is not None:
                    parameter_group = _refresh_parameter_group(
                        parameter_group,
                        workflow.graph,
                    )
                    self.repository.store_parameter_group(
                        request_row,
                        parameter_group,
                    )
                    next_task = next(
                        (
                            task
                            for task in parameter_group.tasks
                            if task.status == "active"
                        ),
                        None,
                    )
                    next_task_id = (
                        next_task.task_id if next_task is not None else None
                    )
            if knowledge_resolution_id:
                self.repository.acknowledge_knowledge_resolution(
                    request_row,
                    resolution_id=str(knowledge_resolution_id),
                    operation_id=operation_id,
                )
                group = self.repository.load_latest_parameter_group(request_row)
                if group is not None:
                    tasks = acknowledge_parameter_binding(
                        group.tasks,
                        affected_node_ids={
                            str(node_id)
                            for node_id in acknowledged.get("affected_node_ids") or []
                        },
                        parameter_key="knowledgeBases",
                    )
                    terminal = {"completed", "skipped", "deferred", "canceled"}
                    group = group.model_copy(
                        update={
                            "tasks": tasks,
                            "status": (
                                "completed"
                                if all(task.status in terminal for task in tasks)
                                else "active"
                            ),
                        }
                    )
                    group = _refresh_parameter_group(group, workflow.graph)
                    self.repository.store_parameter_group(request_row, group)
                    parameter_group = group
                    next_task = next(
                        (task for task in group.tasks if task.status == "active"), None
                    )
                    next_task_id = (
                        next_task.task_id if next_task is not None else None
                    )
            add_action_audit(
                self.db,
                "agent_builder.graph_mutation.acknowledged",
                self.user_id,
                "workflow",
                workflow.id,
                organization_id=self.organization_id,
                metadata={
                    "operation_id": operation_id,
                    "graph_hash": payload.graph_hash,
                    "catalog_version": acknowledged.get("catalog_version"),
                },
            )
            self.db.commit()
        except AgentBuilderRepositoryError as exc:
            self.db.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return GraphMutationAcknowledgementResponse(
            operation_id=operation_id,
            operation_status="acknowledged",
            graph_hash=payload.graph_hash,
            updated_at=payload.updated_at,
            parameter_group=ParameterCandidateProvider(
                self.db,
                user_id=self.user_id,
                organization_id=self.organization_id,
            ).enrich_group(parameter_group, graph=workflow.graph),
            completed_task_id=(
                UUID(str(parameter_task_id)) if parameter_task_id else None
            ),
            completed_knowledge_resolution_id=(
                str(knowledge_resolution_id) if knowledge_resolution_id else None
            ),
            next_task_id=next_task_id,
        )
