from __future__ import annotations

import copy
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from apps.shared.db.models.agent_builder import AgentBuilderRequest, AgentBuilderSession
from apps.shared.schemas.agent_builder import GraphMutationSafeEnvelope
from apps.shared.schemas.agent_builder import (
    AgentBuilderParameterGroup,
    AgentBuilderParameterTask,
)


class AgentBuilderRepositoryError(ValueError):
    pass


class AgentBuilderRepository:
    _HISTORY_BOUNDARY_KEY = "workflow_history_boundary"
    _STRUCTURAL_KINDS = {"initial_graph", "graph_edit", "replace_workflow"}
    _PENDING_GROUP_STATUSES = {"pending_save", "pending_ack"}
    _TERMINAL_TASK_STATUSES = {"completed", "skipped", "deferred", "canceled"}

    @staticmethod
    def _payload(request_row: Any) -> dict[str, Any]:
        payload = request_row.response_payload
        return copy.deepcopy(payload) if isinstance(payload, dict) else {}

    @staticmethod
    def _iso_datetime(value: datetime | str) -> str:
        return value.isoformat() if isinstance(value, datetime) else str(value)

    @classmethod
    def attach_history_boundary_payload(
        cls,
        payload: dict[str, Any],
        envelope: GraphMutationSafeEnvelope,
    ) -> dict[str, Any]:
        """Attach safe hash/timestamp metadata without persisting a graph payload."""
        updated = copy.deepcopy(payload)
        operation_id = str(envelope.operation_id)
        boundary = updated.get(cls._HISTORY_BOUNDARY_KEY)
        if envelope.kind in cls._STRUCTURAL_KINDS:
            if isinstance(boundary, dict):
                if str(boundary.get("operation_id")) != operation_id:
                    raise AgentBuilderRepositoryError(
                        "history boundary already exists"
                    )
                return updated
            updated[cls._HISTORY_BOUNDARY_KEY] = {
                "operation_id": operation_id,
                "workflow_id": str(envelope.workflow_id),
                "kind": envelope.kind,
                "status": envelope.status,
                "pre_run_snapshot": {
                    "graph_hash": envelope.base_graph_hash,
                    "workflow_updated_at": cls._iso_datetime(
                        envelope.expected_workflow_updated_at
                    ),
                },
                "latest_final_graph": None,
                "pending_operation_id": operation_id,
                "member_operation_ids": [operation_id],
            }
            return updated
        if not isinstance(boundary, dict):
            return updated
        if str(boundary.get("workflow_id")) != str(envelope.workflow_id):
            raise AgentBuilderRepositoryError("history boundary workflow mismatch")
        members = list(boundary.get("member_operation_ids") or [])
        if operation_id not in members:
            members.append(operation_id)
        updated[cls._HISTORY_BOUNDARY_KEY] = {
            **boundary,
            "status": envelope.status,
            "pending_operation_id": operation_id,
            "member_operation_ids": members,
        }
        return updated

    def load_history_boundary(self, request_row: Any) -> dict[str, Any] | None:
        boundary = self._payload(request_row).get(self._HISTORY_BOUNDARY_KEY)
        return copy.deepcopy(boundary) if isinstance(boundary, dict) else None

    def _replace_history_boundary(
        self,
        request_row: Any,
        replacement: dict[str, Any],
    ) -> dict[str, Any]:
        payload = self._payload(request_row)
        payload[self._HISTORY_BOUNDARY_KEY] = copy.deepcopy(replacement)
        request_row.response_payload = payload
        return copy.deepcopy(replacement)

    def advance_history_boundary_final(
        self,
        request_row: Any,
        *,
        graph_hash: str,
        workflow_updated_at: datetime,
    ) -> dict[str, Any]:
        boundary = self.load_history_boundary(request_row)
        if boundary is None:
            raise AgentBuilderRepositoryError("history boundary not found")
        latest_final = boundary.get("latest_final_graph")
        if not isinstance(latest_final, dict):
            raise AgentBuilderRepositoryError("history boundary is incomplete")
        return self._replace_history_boundary(
            request_row,
            {
                **boundary,
                "latest_final_graph": {
                    **latest_final,
                    "graph_hash": graph_hash,
                    "workflow_updated_at": workflow_updated_at.isoformat(),
                },
            },
        )

    def record_history_boundary_action(
        self,
        request_row: Any,
        *,
        operation_id: UUID,
        action: str,
        candidate_graph_hash: str,
        expected_base_graph_hash: str,
        expected_workflow_updated_at: datetime,
        result_graph_hash: str,
        result_workflow_updated_at: datetime,
    ) -> dict[str, Any]:
        boundary = self.load_history_boundary(request_row)
        if boundary is None or str(boundary.get("operation_id")) != str(operation_id):
            raise AgentBuilderRepositoryError("history boundary not found")
        return self._replace_history_boundary(
            request_row,
            {
                **boundary,
                "last_persisted_action": {
                    "operation_id": str(operation_id),
                    "action": action,
                    "candidate_graph_hash": candidate_graph_hash,
                    "expected_base_graph_hash": expected_base_graph_hash,
                    "expected_workflow_updated_at": expected_workflow_updated_at.isoformat(),
                    "result_graph_hash": result_graph_hash,
                    "result_workflow_updated_at": result_workflow_updated_at.isoformat(),
                },
            },
        )

    def _set_boundary_operation_status(
        self,
        request_row: Any,
        *,
        operation_id: UUID,
        status: str,
        pending_graph_hash: str | None = None,
        pending_workflow_updated_at: datetime | None = None,
    ) -> None:
        boundary = self.load_history_boundary(request_row)
        if boundary is None or str(operation_id) not in set(
            boundary.get("member_operation_ids") or []
        ):
            return
        replacement = {
            **boundary,
            "status": status,
            "pending_operation_id": str(operation_id),
        }
        if pending_graph_hash is not None:
            replacement["pending_final_graph"] = {
                "graph_hash": pending_graph_hash,
                "workflow_updated_at": self._iso_datetime(
                    pending_workflow_updated_at
                ),
                "operation_id": str(operation_id),
            }
        self._replace_history_boundary(request_row, replacement)

    def synchronize_history_boundary_state(self, request_row: Any) -> None:
        boundary = self.load_history_boundary(request_row)
        if boundary is None or boundary.get("status") == "reverted":
            return
        payload = self._payload(request_row)
        members = set(boundary.get("member_operation_ids") or [])
        member_envelopes = [
            item
            for item in payload.get("operation_envelopes") or []
            if isinstance(item, dict)
            and str(item.get("operation_id")) in members
        ]
        blocked = next(
            (
                item
                for item in reversed(member_envelopes)
                if item.get("status") == "blocked"
            ),
            None,
        )
        pending = next(
            (
                item
                for item in reversed(member_envelopes)
                if item.get("status")
                in {"pending_apply", "pending_save", "pending_ack"}
            ),
            None,
        )
        groups = [
            item
            for item in payload.get("parameter_groups") or []
            if isinstance(item, dict)
        ]
        resolutions = [
            item
            for item in payload.get("knowledge_resolutions") or []
            if isinstance(item, dict)
        ]
        completed_resolution_ids = {
            str(item.get("resolution_id"))
            for item in resolutions
            if item.get("status") == "completed" and item.get("resolution_id")
        }
        pending_knowledge_ids = {
            str(option.get("resolution_id"))
            for option in payload.get("clarification_options") or []
            if isinstance(option, dict)
            and option.get("type") == "knowledge_base"
            and option.get("resolution_id")
            and str(option.get("resolution_id")) not in completed_resolution_ids
        }
        direct_resolution = payload.get("knowledge_resolution")
        direct_candidates = (
            direct_resolution.get("candidates") or []
            if isinstance(direct_resolution, dict)
            else []
        )
        if (
            isinstance(direct_resolution, dict)
            and direct_resolution.get("resolution_id")
            and not (direct_resolution.get("selected") or [])
            and str(direct_resolution.get("resolution_id"))
            not in completed_resolution_ids
        ):
            pending_knowledge_ids.add(str(direct_resolution["resolution_id"]))
        pending_knowledge_ids.update(
            str(candidate.get("resolution_id"))
            for candidate in direct_candidates
            if isinstance(candidate, dict)
            and candidate.get("resolution_id")
            and str(candidate.get("resolution_id"))
            not in completed_resolution_ids
        )
        group_tasks = [
            task
            for group in groups
            for task in group.get("tasks") or []
            if isinstance(task, dict)
        ]
        if blocked is not None or any(
            group.get("status") in {"blocked", "canceled"} for group in groups
        ) or any(
            resolution.get("status") in {"blocked", "canceled"}
            for resolution in resolutions
        ) or any(task.get("status") == "canceled" for task in group_tasks):
            status = "blocked"
            pending_operation_id = (
                str(blocked.get("operation_id")) if blocked is not None else None
            )
        elif pending is not None:
            status = str(pending.get("status"))
            pending_operation_id = str(pending.get("operation_id"))
        elif any(
            group.get("status") in {"pending_save", "pending_ack"}
            for group in groups
        ) or any(
            resolution.get("status") == "pending_ack"
            for resolution in resolutions
        ):
            status = "pending_ack"
            pending_operation_id = None
        elif pending_knowledge_ids or any(
            task.get("status") in {"pending", "active", "invalid"}
            for task in group_tasks
        ) or any(group.get("status") == "active" for group in groups):
            status = "active"
            pending_operation_id = None
        elif (
            not member_envelopes
            or any(item.get("status") != "acknowledged" for item in member_envelopes)
            or not isinstance(boundary.get("latest_final_graph"), dict)
        ):
            status = "pending_ack"
            pending_operation_id = None
        else:
            status = "completed"
            pending_operation_id = None
        replacement = {
            **boundary,
            "status": status,
            "pending_operation_id": pending_operation_id,
        }
        if pending_operation_id is None:
            replacement.pop("pending_final_graph", None)
        self._replace_history_boundary(request_row, replacement)
        if hasattr(request_row, "status"):
            public_status = None
            if status == "completed":
                public_status = "completed"
            elif getattr(request_row, "status", None) == "completed":
                public_status = (
                    "clarification_required"
                    if pending_knowledge_ids
                    else "parameter_configuration"
                    if groups
                    else "graph_mutation_ready"
                )
            if public_status is not None:
                latest_payload = self._payload(request_row)
                latest_payload["status"] = public_status
                request_row.response_payload = latest_payload
                request_row.status = public_status

    def _record_boundary_acknowledgement(
        self,
        request_row: Any,
        *,
        envelope: dict[str, Any],
        result_graph_hash: str,
        workflow_updated_at: datetime,
    ) -> None:
        boundary = self.load_history_boundary(request_row)
        operation_id = str(envelope.get("operation_id"))
        members = list(boundary.get("member_operation_ids") or []) if boundary else []
        if boundary is None or operation_id not in set(members):
            return
        latest = boundary.get("latest_final_graph")
        latest_operation_id = (
            str(latest.get("operation_id")) if isinstance(latest, dict) else None
        )
        if (
            latest_operation_id in members
            and latest_operation_id != operation_id
            and members.index(latest_operation_id) > members.index(operation_id)
        ):
            self.synchronize_history_boundary_state(request_row)
            return
        self._replace_history_boundary(
            request_row,
            {
                **boundary,
                "latest_final_graph": {
                    "graph_hash": result_graph_hash,
                    "workflow_updated_at": workflow_updated_at.isoformat(),
                    "operation_id": operation_id,
                },
                "pending_operation_id": None,
            },
        )
        self.synchronize_history_boundary_state(request_row)

    def history_boundary_envelope(
        self,
        request_row: Any,
        operation_id: UUID,
    ) -> dict[str, Any]:
        boundary = self.load_history_boundary(request_row)
        if boundary is None or str(boundary.get("operation_id")) != str(operation_id):
            raise AgentBuilderRepositoryError("history boundary not found")
        envelope = self.find_envelope(request_row, operation_id)
        latest = boundary.get("latest_final_graph")
        if not isinstance(latest, dict):
            return envelope
        return {
            **envelope,
            "result_graph_hash": latest.get("graph_hash"),
            "saved_workflow_updated_at": latest.get("workflow_updated_at"),
        }

    def store_envelope(
        self,
        request_row: Any,
        envelope: GraphMutationSafeEnvelope,
    ) -> dict[str, Any]:
        payload = self._payload(request_row)
        envelopes = list(payload.get("operation_envelopes") or [])
        operation_id = str(envelope.operation_id)
        existing = next(
            (
                item
                for item in envelopes
                if isinstance(item, dict)
                and str(item.get("operation_id")) == operation_id
            ),
            None,
        )
        serialized = envelope.model_dump(mode="json")
        if "operations" in serialized:
            raise AgentBuilderRepositoryError("typed operations cannot be persisted")
        if existing is not None:
            if existing != serialized:
                raise AgentBuilderRepositoryError("operation id already exists")
            request_row.response_payload = self.attach_history_boundary_payload(
                payload,
                envelope,
            )
            return existing
        envelopes.append(serialized)
        payload["operation_envelopes"] = envelopes
        request_row.response_payload = self.attach_history_boundary_payload(
            payload,
            envelope,
        )
        return serialized

    def find_envelope(self, request_row: Any, operation_id: UUID) -> dict[str, Any]:
        payload = self._payload(request_row)
        for item in payload.get("operation_envelopes") or []:
            if isinstance(item, dict) and str(item.get("operation_id")) == str(
                operation_id
            ):
                return item
        raise AgentBuilderRepositoryError("operation envelope not found")

    def _replace_envelope(
        self, request_row: Any, operation_id: UUID, replacement: dict[str, Any]
    ) -> dict[str, Any]:
        payload = self._payload(request_row)
        envelopes = list(payload.get("operation_envelopes") or [])
        replaced = False
        for index, item in enumerate(envelopes):
            if isinstance(item, dict) and str(item.get("operation_id")) == str(
                operation_id
            ):
                envelopes[index] = copy.deepcopy(replacement)
                replaced = True
                break
        if not replaced:
            raise AgentBuilderRepositoryError("operation envelope not found")
        payload["operation_envelopes"] = envelopes
        request_row.response_payload = payload
        return copy.deepcopy(replacement)

    def mark_envelope_saved(
        self,
        request_row: Any,
        *,
        operation_id: UUID,
        result_graph_hash: str,
        workflow_updated_at: datetime,
    ) -> dict[str, Any]:
        envelope = self.find_envelope(request_row, operation_id)
        if envelope.get("status") != "pending_save":
            raise AgentBuilderRepositoryError("operation cannot be saved")
        if result_graph_hash != envelope.get("expected_result_graph_hash"):
            raise AgentBuilderRepositoryError("saved graph hash mismatch")
        saved = {
            **envelope,
            "status": "pending_ack",
            "result_graph_hash": result_graph_hash,
            "saved_workflow_updated_at": workflow_updated_at.isoformat(),
        }
        saved = self._replace_envelope(request_row, operation_id, saved)
        self._set_boundary_operation_status(
            request_row,
            operation_id=operation_id,
            status="pending_ack",
            pending_graph_hash=result_graph_hash,
            pending_workflow_updated_at=workflow_updated_at,
        )
        return saved

    def mark_envelope_pending_save(
        self,
        request_row: Any,
        operation_id: UUID,
    ) -> dict[str, Any]:
        envelope = self.find_envelope(request_row, operation_id)
        if envelope.get("status") == "pending_save":
            return envelope
        if envelope.get("status") != "pending_apply":
            raise AgentBuilderRepositoryError("operation cannot be saved")
        pending_save = {**envelope, "status": "pending_save"}
        pending_save = self._replace_envelope(
            request_row,
            operation_id,
            pending_save,
        )
        self._set_boundary_operation_status(
            request_row,
            operation_id=operation_id,
            status="pending_save",
        )
        return pending_save

    def acknowledge_envelope(
        self,
        request_row: Any,
        *,
        operation_id: UUID,
        result_graph_hash: str,
        workflow_updated_at: datetime,
    ) -> dict[str, Any]:
        envelope = self.find_envelope(request_row, operation_id)
        expected_updated_at = workflow_updated_at.isoformat()
        if envelope.get("status") == "acknowledged":
            if (
                envelope.get("result_graph_hash") == result_graph_hash
                and envelope.get("saved_workflow_updated_at") == expected_updated_at
            ):
                self._record_boundary_acknowledgement(
                    request_row,
                    envelope=envelope,
                    result_graph_hash=result_graph_hash,
                    workflow_updated_at=workflow_updated_at,
                )
                return envelope
            raise AgentBuilderRepositoryError("acknowledgement mismatch")
        if envelope.get("status") != "pending_ack":
            raise AgentBuilderRepositoryError("operation is not saved")
        if (
            envelope.get("result_graph_hash") != result_graph_hash
            or envelope.get("saved_workflow_updated_at") != expected_updated_at
        ):
            raise AgentBuilderRepositoryError("acknowledgement mismatch")
        acknowledged = {**envelope, "status": "acknowledged"}
        acknowledged = self._replace_envelope(
            request_row,
            operation_id,
            acknowledged,
        )
        self._record_boundary_acknowledgement(
            request_row,
            envelope=acknowledged,
            result_graph_hash=result_graph_hash,
            workflow_updated_at=workflow_updated_at,
        )
        return acknowledged

    def mark_envelope_reverted(
        self, request_row: Any, operation_id: UUID
    ) -> dict[str, Any]:
        envelope = self.find_envelope(request_row, operation_id)
        if envelope.get("status") == "reverted":
            return envelope
        if envelope.get("status") != "acknowledged":
            raise AgentBuilderRepositoryError("operation is not acknowledged")
        reverted = {**envelope, "status": "reverted"}
        reverted = self._replace_envelope(request_row, operation_id, reverted)
        boundary = self.load_history_boundary(request_row)
        if boundary is not None and str(boundary.get("operation_id")) == str(
            operation_id
        ):
            self._replace_history_boundary(
                request_row,
                {
                    **boundary,
                    "status": "reverted",
                    "pending_operation_id": None,
                },
            )
        return reverted

    def mark_envelope_blocked(
        self,
        request_row: Any,
        operation_id: UUID,
        *,
        reason: str,
    ) -> dict[str, Any]:
        envelope = self.find_envelope(request_row, operation_id)
        if envelope.get("status") == "blocked":
            return envelope
        if envelope.get("status") not in {"pending_apply", "pending_save"}:
            raise AgentBuilderRepositoryError("operation cannot be blocked")
        blocked = {**envelope, "status": "blocked", "blocked_reason": reason}
        blocked = self._replace_envelope(request_row, operation_id, blocked)
        self.synchronize_history_boundary_state(request_row)
        return blocked

    def recover_blocked_completion_state(
        self,
        request_row: Any,
        envelope: dict[str, Any],
    ) -> AgentBuilderParameterGroup | None:
        """Release state reserved by an operation whose typed payload was lost."""
        payload = self._payload(request_row)
        kind = str(envelope.get("kind") or "")
        context = envelope.get("completion_context") or {}
        groups = list(payload.get("parameter_groups") or [])

        if kind == "parameter_update":
            task_id = context.get("parameter_task_id")
            if not task_id:
                raise AgentBuilderRepositoryError(
                    "parameter recovery completion context is missing"
                )
            group = self.load_parameter_group_for_task(
                request_row, UUID(str(task_id))
            )
            tasks = [task.model_copy(deep=True) for task in group.tasks]
            target_index = next(
                (
                    index
                    for index, task in enumerate(tasks)
                    if str(task.task_id) == str(task_id)
                ),
                None,
            )
            if target_index is None:
                raise AgentBuilderRepositoryError("parameter task not found")
            tasks = [
                task.model_copy(update={"status": "pending"})
                if task.status == "active" and index != target_index
                else task
                for index, task in enumerate(tasks)
            ]
            target = tasks[target_index]
            tasks[target_index] = target.model_copy(update={"status": "active"})
            recovered = group.model_copy(update={"status": "active", "tasks": tasks})
            self.store_parameter_group(request_row, recovered)
            self.remove_pending_task_decision(
                request_row, UUID(str(envelope["operation_id"]))
            )
            return recovered

        if kind in self._STRUCTURAL_KINDS and context.get(
            "knowledge_resolution_id"
        ):
            self._mark_knowledge_resolution_unapplied(
                request_row,
                str(context["knowledge_resolution_id"]),
            )
            self._release_history_boundary_for_lost_operation(
                request_row,
                str(envelope.get("operation_id")),
            )
            payload = self._payload(request_row)
            groups = list(payload.get("parameter_groups") or [])

        if kind in {"initial_graph", "graph_edit", "replace_workflow"}:
            if groups:
                group = AgentBuilderParameterGroup.model_validate(groups[-1])
                if group.status in {"pending_save", "pending_ack"}:
                    group = group.model_copy(update={"status": "blocked"})
                    self.store_parameter_group(request_row, group)
                return group
            return None

        if kind == "knowledge_binding":
            resolution_id = context.get("knowledge_resolution_id")
            if not resolution_id:
                raise AgentBuilderRepositoryError(
                    "knowledge recovery completion context is missing"
                )
            self._mark_knowledge_resolution_unapplied(
                request_row,
                str(resolution_id),
            )

        return (
            AgentBuilderParameterGroup.model_validate(groups[-1])
            if groups
            else None
        )

    def _mark_knowledge_resolution_unapplied(
        self,
        request_row: Any,
        resolution_id: str,
    ) -> None:
        payload = self._payload(request_row)
        resolutions = list(payload.get("knowledge_resolutions") or [])
        for index, item in enumerate(resolutions):
            if not isinstance(item, dict):
                continue
            if str(item.get("resolution_id")) == str(resolution_id):
                resolutions[index] = {**item, "status": "unapplied"}
                payload["knowledge_resolutions"] = resolutions
                request_row.response_payload = payload
                return
        raise AgentBuilderRepositoryError("knowledge resolution not found")

    def _release_history_boundary_for_lost_operation(
        self,
        request_row: Any,
        operation_id: str,
    ) -> None:
        payload = self._payload(request_row)
        boundary = payload.get(self._HISTORY_BOUNDARY_KEY)
        if not isinstance(boundary, dict):
            return
        members = [
            str(member)
            for member in boundary.get("member_operation_ids") or []
            if str(member) != operation_id
        ]
        if str(boundary.get("operation_id")) == operation_id:
            payload.pop(self._HISTORY_BOUNDARY_KEY, None)
        else:
            replacement = {
                **boundary,
                "member_operation_ids": members,
                "pending_operation_id": None,
            }
            replacement.pop("pending_final_graph", None)
            payload[self._HISTORY_BOUNDARY_KEY] = replacement
        request_row.response_payload = payload

    def load_request_for_operation(
        self,
        db: Session,
        *,
        operation_id: UUID,
        workflow_id: UUID,
        organization_id: UUID,
        user_id: UUID,
        for_update: bool = False,
    ) -> tuple[AgentBuilderRequest, dict[str, Any]]:
        query = (
            db.query(AgentBuilderRequest)
            .join(
                AgentBuilderSession,
                AgentBuilderSession.id == AgentBuilderRequest.session_id,
            )
            .filter(
                AgentBuilderSession.workflow_id == workflow_id,
                AgentBuilderRequest.organization_id == organization_id,
                AgentBuilderRequest.user_id == user_id,
            )
            .order_by(AgentBuilderRequest.created_at.desc())
        )
        if for_update:
            query = query.with_for_update()
        for request_row in query.all():
            try:
                envelope = self.find_envelope(request_row, operation_id)
                boundary = self.load_history_boundary(request_row)
                if (
                    boundary is not None
                    and str(boundary.get("operation_id")) == str(operation_id)
                    and boundary.get("status") != "reverted"
                ):
                    envelope = self.history_boundary_envelope(
                        request_row,
                        operation_id,
                    )
                return request_row, envelope
            except AgentBuilderRepositoryError:
                continue
        raise AgentBuilderRepositoryError("operation envelope not found")

    def store_parameter_group(
        self,
        request_row: Any,
        group: AgentBuilderParameterGroup,
    ) -> dict[str, Any]:
        payload = self._payload(request_row)
        groups = list(payload.get("parameter_groups") or [])
        group = self._normalize_pending_parameter_group(group)
        serialized = group.model_dump(mode="json")
        for index, item in enumerate(groups):
            if isinstance(item, dict) and str(item.get("group_id")) == str(
                group.group_id
            ):
                groups[index] = serialized
                break
        else:
            groups.append(serialized)
        payload["parameter_groups"] = groups
        request_row.response_payload = payload
        self.synchronize_history_boundary_state(request_row)
        return serialized

    def _normalize_pending_parameter_group(
        self,
        group: AgentBuilderParameterGroup,
    ) -> AgentBuilderParameterGroup:
        if group.status not in self._PENDING_GROUP_STATUSES:
            return group
        tasks = [
            task.model_copy(update={"status": "pending"})
            if task.status == "active"
            else task
            for task in group.tasks
        ]
        return group.model_copy(update={"tasks": tasks})

    def load_parameter_group_for_task(
        self,
        request_row: Any,
        task_id: UUID,
    ) -> AgentBuilderParameterGroup:
        payload = self._payload(request_row)
        for item in payload.get("parameter_groups") or []:
            if not isinstance(item, dict):
                continue
            group = AgentBuilderParameterGroup.model_validate(item)
            if any(task.task_id == task_id for task in group.tasks):
                return group
        raise AgentBuilderRepositoryError("parameter task not found")

    def load_parameter_group(
        self,
        request_row: Any,
        group_id: UUID,
    ) -> AgentBuilderParameterGroup:
        payload = self._payload(request_row)
        for item in payload.get("parameter_groups") or []:
            if not isinstance(item, dict):
                continue
            group = AgentBuilderParameterGroup.model_validate(item)
            if group.group_id == group_id:
                return group
        raise AgentBuilderRepositoryError("parameter group not found")

    def load_latest_parameter_group(
        self, request_row: Any
    ) -> AgentBuilderParameterGroup | None:
        payload = self._payload(request_row)
        groups = [
            item
            for item in payload.get("parameter_groups") or []
            if isinstance(item, dict)
        ]
        return (
            AgentBuilderParameterGroup.model_validate(groups[-1])
            if groups
            else None
        )

    def last_reopenable_parameter_task(
        self,
        request_row: Any,
    ) -> AgentBuilderParameterTask | None:
        """Return the last editable terminal task without changing persisted state."""
        group = self.load_latest_parameter_group(request_row)
        if group is None or group.status != "completed":
            return None
        candidates = [
            task
            for task in group.tasks
            if task.status in {"completed", "skipped", "deferred"}
        ]
        return max(candidates, key=lambda task: task.stable_order, default=None)

    def find_parameter_group_cancellation(
        self,
        request_row: Any,
        operation_id: UUID,
    ) -> UUID | None:
        item = self.find_parameter_group_cancellation_record(
            request_row, operation_id
        )
        if item is not None:
            return UUID(str(item["group_id"]))
        return None

    def find_parameter_group_cancellation_record(
        self,
        request_row: Any,
        operation_id: UUID,
    ) -> dict[str, Any] | None:
        payload = self._payload(request_row)
        for item in payload.get("parameter_group_cancellations") or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("operation_id")) == str(operation_id):
                return dict(item)
        return None

    def store_parameter_group_cancellation(
        self,
        request_row: Any,
        *,
        operation_id: UUID,
        group_id: UUID,
        expected_task_id: UUID | None = None,
        expected_task_version: int | None = None,
        payload_fingerprint: str | None = None,
    ) -> None:
        payload = self._payload(request_row)
        cancellations = list(payload.get("parameter_group_cancellations") or [])
        existing = next(
            (
                item
                for item in cancellations
                if isinstance(item, dict)
                and str(item.get("operation_id")) == str(operation_id)
            ),
            None,
        )
        serialized = {"operation_id": str(operation_id), "group_id": str(group_id)}
        if expected_task_id is not None:
            serialized["expected_task_id"] = str(expected_task_id)
        if expected_task_version is not None:
            serialized["expected_task_version"] = expected_task_version
        if payload_fingerprint is not None:
            serialized["payload_fingerprint"] = payload_fingerprint
        if existing is not None:
            if existing != serialized:
                raise AgentBuilderRepositoryError("operation id already exists")
            return
        cancellations.append(serialized)
        payload["parameter_group_cancellations"] = cancellations
        request_row.response_payload = payload

    def activate_latest_parameter_group(
        self, request_row: Any
    ) -> AgentBuilderParameterGroup | None:
        payload = self._payload(request_row)
        groups = list(payload.get("parameter_groups") or [])
        if not groups:
            return None
        group = AgentBuilderParameterGroup.model_validate(groups[-1])
        if group.status in self._PENDING_GROUP_STATUSES:
            tasks = [
                task.model_copy(update={"status": "pending"})
                if task.status == "active"
                else task
                for task in group.tasks
            ]
            active_index = next(
                (
                    index
                    for index, task in sorted(
                        enumerate(tasks),
                        key=lambda item: item[1].stable_order,
                    )
                    if task.status == "pending"
                ),
                None,
            )
            if active_index is not None:
                tasks[active_index] = tasks[active_index].model_copy(
                    update={"status": "active"}
                )
            group = group.model_copy(
                update={
                    "tasks": tasks,
                    "status": (
                        "completed"
                        if all(
                            task.status in self._TERMINAL_TASK_STATUSES
                            for task in tasks
                        )
                        else "active"
                    )
                }
            )
            groups[-1] = group.model_dump(mode="json")
            payload["parameter_groups"] = groups
            request_row.response_payload = payload
            self.synchronize_history_boundary_state(request_row)
        return group

    def revert_completion_state(
        self,
        request_row: Any,
        envelope: dict[str, Any],
    ) -> AgentBuilderParameterGroup | None:
        payload = self._payload(request_row)
        groups = list(payload.get("parameter_groups") or [])
        kind = str(envelope.get("kind") or "")
        if kind not in self._STRUCTURAL_KINDS:
            raise AgentBuilderRepositoryError("operation is not history boundary")
        resolutions = list(payload.get("knowledge_resolutions") or [])
        if resolutions:
            payload["knowledge_resolutions"] = [
                {**item, "status": "canceled"}
                if isinstance(item, dict) and item.get("status") != "canceled"
                else item
                for item in resolutions
            ]
        payload["clarification_options"] = []
        payload["clarification_questions"] = []
        payload["pending_task_decisions"] = []

        if not groups:
            request_row.response_payload = payload
            return None
        canceled_groups: list[dict[str, Any]] = []
        for item in groups:
            existing_group = AgentBuilderParameterGroup.model_validate(item)
            canceled_group = existing_group.model_copy(
                update={
                    "status": "canceled",
                    "tasks": [
                        task
                        if task.status == "canceled"
                        else task.model_copy(
                            update={
                                "status": "canceled",
                                "task_version": task.task_version + 1,
                            }
                        )
                        for task in existing_group.tasks
                    ],
                }
            )
            canceled_groups.append(canceled_group.model_dump(mode="json"))
        groups = canceled_groups
        group = AgentBuilderParameterGroup.model_validate(groups[-1])
        payload["parameter_groups"] = groups
        request_row.response_payload = payload
        self.synchronize_history_boundary_state(request_row)
        return group

    def store_pending_task_decision(
        self,
        request_row: Any,
        *,
        operation_id: UUID,
        task_id: UUID,
        action: str,
        expected_task_version: int,
        payload_fingerprint: str | None = None,
    ) -> dict[str, Any]:
        payload = self._payload(request_row)
        decisions = list(payload.get("pending_task_decisions") or [])
        serialized: dict[str, Any] = {
            "operation_id": str(operation_id),
            "task_id": str(task_id),
            "action": action,
            "expected_task_version": expected_task_version,
        }
        if payload_fingerprint is not None:
            serialized["payload_fingerprint"] = payload_fingerprint
        for item in decisions:
            if isinstance(item, dict) and item.get("operation_id") == str(operation_id):
                if item != serialized:
                    raise AgentBuilderRepositoryError("operation id already exists")
                return item
        decisions.append(serialized)
        payload["pending_task_decisions"] = decisions
        request_row.response_payload = payload
        return serialized

    def store_knowledge_resolution(
        self,
        request_row: Any,
        *,
        resolution_id: str,
        operation_id: UUID,
        timing: str,
        selected_candidate_ids: list[str],
        selected_collection_handles: list[str] | None = None,
        selected_kb_handles: list[str] | None = None,
        status: str = "pending_ack",
    ) -> dict[str, Any]:
        payload = self._payload(request_row)
        resolutions = list(payload.get("knowledge_resolutions") or [])
        serialized = {
            "resolution_id": resolution_id,
            "operation_id": str(operation_id),
            "timing": timing,
            "selected_candidate_ids": sorted(set(selected_candidate_ids)),
            "selected_collection_handles": sorted(
                set(selected_collection_handles or [])
            ),
            "selected_kb_handles": sorted(set(selected_kb_handles or [])),
            "status": status,
        }
        for index, item in enumerate(resolutions):
            if not isinstance(item, dict):
                continue
            if str(item.get("resolution_id")) != resolution_id:
                continue
            if item == serialized:
                return item
            if item.get("status") == "unapplied":
                if (
                    not item.get("selection_invalidated")
                    and (
                        item.get("timing") != timing
                        or list(item.get("selected_candidate_ids") or [])
                        != serialized["selected_candidate_ids"]
                        or list(item.get("selected_collection_handles") or [])
                        != serialized["selected_collection_handles"]
                        or list(item.get("selected_kb_handles") or [])
                        != serialized["selected_kb_handles"]
                    )
                ):
                    raise AgentBuilderRepositoryError(
                        "knowledge resolution retry payload differs"
                    )
                old_operation_id = str(item.get("operation_id") or "")
                boundary = self.load_history_boundary(request_row)
                if boundary is not None and old_operation_id:
                    members = [
                        member
                        for member in boundary.get("member_operation_ids") or []
                        if str(member) != old_operation_id
                    ]
                    replacement = {
                        **boundary,
                        "member_operation_ids": members,
                        "status": "active",
                        "pending_operation_id": None,
                    }
                    replacement.pop("pending_final_graph", None)
                    self._replace_history_boundary(request_row, replacement)
                resolutions[index] = serialized
                payload = self._payload(request_row)
                payload["knowledge_resolutions"] = resolutions
                request_row.response_payload = payload
                self.synchronize_history_boundary_state(request_row)
                return serialized
            raise AgentBuilderRepositoryError(
                "knowledge resolution is already submitted"
            )
        else:
            resolutions.append(serialized)
        payload["knowledge_resolutions"] = resolutions
        request_row.response_payload = payload
        return serialized

    def find_knowledge_resolution(
        self,
        request_row: Any,
        resolution_id: str,
    ) -> dict[str, Any] | None:
        payload = self._payload(request_row)
        return next(
            (
                copy.deepcopy(item)
                for item in payload.get("knowledge_resolutions") or []
                if isinstance(item, dict)
                and str(item.get("resolution_id")) == str(resolution_id)
            ),
            None,
        )

    def acknowledge_knowledge_resolution(
        self,
        request_row: Any,
        *,
        resolution_id: str,
        operation_id: UUID,
    ) -> dict[str, Any]:
        payload = self._payload(request_row)
        resolutions = list(payload.get("knowledge_resolutions") or [])
        for index, item in enumerate(resolutions):
            if not isinstance(item, dict):
                continue
            if str(item.get("resolution_id")) != resolution_id:
                continue
            if str(item.get("operation_id")) != str(operation_id):
                raise AgentBuilderRepositoryError("knowledge operation mismatch")
            completed = {**item, "status": "completed"}
            resolutions[index] = completed
            payload["knowledge_resolutions"] = resolutions
            clarification_options = list(payload.get("clarification_options") or [])
            remaining_options = [
                option
                for option in clarification_options
                if not isinstance(option, dict)
                or str(option.get("resolution_id")) != resolution_id
            ]
            if len(remaining_options) != len(clarification_options):
                payload["clarification_options"] = remaining_options
                if not remaining_options:
                    payload["clarification_questions"] = []
            request_row.response_payload = payload
            self.synchronize_history_boundary_state(request_row)
            return completed
        raise AgentBuilderRepositoryError("knowledge resolution not found")

    def find_pending_task_decision(
        self, request_row: Any, operation_id: UUID
    ) -> dict[str, Any] | None:
        payload = self._payload(request_row)
        return next(
            (
                item
                for item in payload.get("pending_task_decisions") or []
                if isinstance(item, dict)
                and item.get("operation_id") == str(operation_id)
            ),
            None,
        )

    def find_pending_task_decision_for_task_version(
        self,
        request_row: Any,
        *,
        task_id: UUID,
        expected_task_version: int,
    ) -> dict[str, Any] | None:
        payload = self._payload(request_row)
        return next(
            (
                item
                for item in payload.get("pending_task_decisions") or []
                if isinstance(item, dict)
                and item.get("task_id") == str(task_id)
                and item.get("expected_task_version") == expected_task_version
            ),
            None,
        )

    def find_local_task_decision(
        self, request_row: Any, operation_id: UUID
    ) -> dict[str, Any] | None:
        payload = self._payload(request_row)
        return next(
            (
                item
                for item in payload.get("local_task_decisions") or []
                if isinstance(item, dict)
                and item.get("operation_id") == str(operation_id)
            ),
            None,
        )

    def store_local_task_decision(
        self,
        request_row: Any,
        *,
        operation_id: UUID,
        task_id: UUID,
        action: str,
        expected_task_version: int | None = None,
        payload_fingerprint: str | None = None,
        result: str | None = None,
        validation_issues: list[dict[str, Any]] | None = None,
    ) -> None:
        payload = self._payload(request_row)
        decisions = list(payload.get("local_task_decisions") or [])
        serialized: dict[str, Any] = {
            "operation_id": str(operation_id),
            "task_id": str(task_id),
            "action": action,
        }
        if expected_task_version is not None:
            serialized["expected_task_version"] = expected_task_version
        if payload_fingerprint is not None:
            serialized["payload_fingerprint"] = payload_fingerprint
        if result is not None:
            serialized["result"] = result
        if validation_issues is not None:
            serialized["validation_issues"] = copy.deepcopy(validation_issues)
        existing = next(
            (
                item
                for item in decisions
                if isinstance(item, dict)
                and item.get("operation_id") == str(operation_id)
            ),
            None,
        )
        if existing is not None:
            if existing != serialized:
                raise AgentBuilderRepositoryError("operation id already exists")
            return
        decisions.append(serialized)
        payload["local_task_decisions"] = decisions
        request_row.response_payload = payload

    def remove_pending_task_decision(
        self, request_row: Any, operation_id: UUID
    ) -> None:
        payload = self._payload(request_row)
        payload["pending_task_decisions"] = [
            item
            for item in payload.get("pending_task_decisions") or []
            if not (
                isinstance(item, dict)
                and item.get("operation_id") == str(operation_id)
            )
        ]
        request_row.response_payload = payload
