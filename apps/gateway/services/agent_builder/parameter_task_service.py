from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from apps.gateway.adapters.db.agent_builder_repository import (
    AgentBuilderRepository,
    AgentBuilderRepositoryError,
)
from apps.gateway.application.agent_builder.condition_branches import (
    build_condition_branch_operations,
    condition_branch_decision_issues,
    is_condition_branch_task,
    prepare_condition_cases_data,
)
from apps.gateway.application.agent_builder.graph_mutation_builder import (
    GraphMutationBuilder,
)
from apps.gateway.application.agent_builder.parameter_tasks import (
    ParameterTaskConflict,
    ParameterTaskSafetyError,
    apply_local_task_decision,
    cancel_parameter_group,
    prepare_task_decision,
    previous_reopenable_task_id,
    recommendation_matches_canonical_graph,
    validate_direct_set_value,
)
from apps.gateway.services.audit_records import add_action_audit
from apps.gateway.services.app_service import AppService
from apps.gateway.services.llm_service import LLMService
from apps.shared.audit.actions import AuditAction
from apps.shared.db.models.app import App
from apps.shared.db.models.agent_builder import AgentBuilderRequest, AgentBuilderSession
from apps.shared.db.models.mail_credential import MailCredential, MAIL_CREDENTIAL_ACTIVE
from apps.shared.db.models.workflow import Workflow
from apps.shared.schemas.agent_builder import (
    AgentBuilderParameterGroup,
    AgentBuilderParameterGroupCancelRequest,
    AgentBuilderParameterGroupCancelResponse,
    AgentBuilderParameterTaskDecisionRequest,
    AgentBuilderParameterTaskDecisionResponse,
    GraphMutationCompletionContext,
    GraphMutationSafeEnvelope,
    ParameterCredentialValue,
    ParameterSecretValue,
    ParameterVariableSelectorListValue,
    ParameterVariableSelectorValue,
)
from apps.shared.services.permissions import has_workflow_permission
from apps.shared.services.permissions import has_mail_credential_permission
from apps.shared.services.workflow_node_catalog import validate_node_parameter_update
from apps.shared.services.workflow_node_catalog import derive_node_configuration_state
from apps.shared.services.workflow_node_catalog import apply_node_parameter_value
from apps.shared.services.workflow_node_catalog import remove_node_parameter_value
from apps.gateway.application.agent_builder.parameter_suggestions import (
    ParameterSuggestionError,
    ParameterSuggestionResolver,
)


def _decision_value(payload: AgentBuilderParameterTaskDecisionRequest):
    value = payload.value
    if value is None:
        return None
    if isinstance(value, ParameterVariableSelectorValue):
        return list(value.value_selector)
    if isinstance(value, ParameterVariableSelectorListValue):
        return [list(selection.value_selector) for selection in value.selections]
    if isinstance(value, ParameterCredentialValue):
        return str(value.credential_id)
    if hasattr(value, "resource_id"):
        return str(value.resource_id)
    return value.value


def _canonical_fingerprint(payload: dict[str, Any]) -> str:
    try:
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, UnicodeError) as exc:
        raise HTTPException(status_code=400, detail="invalid_decision") from exc
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _decision_payload_fingerprint(
    task_id: UUID,
    payload: AgentBuilderParameterTaskDecisionRequest,
) -> str:
    value_payload = (
        payload.value.model_dump(mode="json") if payload.value is not None else None
    )
    return _canonical_fingerprint(
        {
            "task_id": str(task_id),
            "action": payload.action,
            "expected_task_version": payload.expected_task_version,
            "value": value_payload,
        }
    )


def _cancel_payload_fingerprint(
    group_id: UUID,
    payload: AgentBuilderParameterGroupCancelRequest,
) -> str:
    return _canonical_fingerprint(
        {
            "group_id": str(group_id),
            "action": "cancel",
            "expected_task_id": str(payload.expected_task_id),
            "expected_task_version": payload.expected_task_version,
        }
    )


def _task_decision_record_conflicts(
    record: dict[str, Any],
    *,
    task_id: UUID,
    action: str,
    expected_task_version: int,
    payload_fingerprint: str,
    allow_legacy_missing_version: bool = False,
) -> bool:
    record_version = record.get("expected_task_version")
    version_mismatch = (
        record_version not in {None, expected_task_version}
        if allow_legacy_missing_version
        else record_version != expected_task_version
    )
    return (
        record.get("task_id") != str(task_id)
        or record.get("action") != action
        or version_mismatch
        or (
            "payload_fingerprint" in record
            and record.get("payload_fingerprint") != payload_fingerprint
        )
    )


def _group_status(group: AgentBuilderParameterGroup) -> str:
    terminal = {"completed", "skipped", "deferred"}
    return "completed" if all(task.status in terminal for task in group.tasks) else "active"


def apply_parameter_value_to_node_data(
    node_type: str,
    parameter_key: str,
    node_data: dict,
    value,
) -> dict:
    return apply_node_parameter_value(node_type, parameter_key, node_data, value)


class ParameterTaskService:
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

    def _resolve_reference_value(self, task, resource_id: UUID) -> Any:
        if task.input_type == "credential_ref":
            if task.node_type not in {"mailNode", "gmailDraftNode"}:
                raise HTTPException(status_code=400, detail="invalid_decision")
            credential = (
                self.db.query(MailCredential)
                .filter(
                    MailCredential.id == resource_id,
                    MailCredential.organization_id == self.organization_id,
                    MailCredential.status == MAIL_CREDENTIAL_ACTIVE,
                )
                .first()
            )
            if credential is None or not has_mail_credential_permission(
                self.db,
                self.user_id,
                credential.id,
                "use",
                organization_id=self.organization_id,
            ):
                raise HTTPException(status_code=403, detail="permission_denied")
            if task.node_type == "gmailDraftNode" and (
                credential.provider != "gmail" or credential.auth_type != "oauth2"
            ):
                raise HTTPException(status_code=400, detail="invalid_decision")
            return str(credential.id)

        if task.node_type == "llmNode" and task.parameter_key in {
            "model_id",
            "fallback_model_id",
        }:
            options = [
                option
                for group in LLMService.get_agent_builder_model_option_groups(
                    self.db, self.user_id, self.organization_id
                )
                for option in group.options
            ]
            selected = next(
                (option for option in options if option.model.id == resource_id),
                None,
            )
            if selected is None:
                raise HTTPException(status_code=403, detail="permission_denied")
            return selected.model.model_id_for_api_call

        if task.node_type == "workflowNode" and task.parameter_key == "workflowId":
            target = (
                self.db.query(Workflow)
                .filter(
                    Workflow.id == resource_id,
                    Workflow.organization_id == self.organization_id,
                )
                .first()
            )
            if target is None or not has_workflow_permission(
                self.db,
                self.user_id,
                target.id,
                "read",
                organization_id=self.organization_id,
            ):
                raise HTTPException(status_code=403, detail="permission_denied")
            return str(target.id)

        if task.node_type == "workflowNode" and task.parameter_key == "appId":
            app = (
                self.db.query(App)
                .filter(
                    App.id == resource_id,
                    App.organization_id == self.organization_id,
                )
                .first()
            )
            if app is None or AppService.access_denial_status(
                self.db, app, self.user_id, "read"
            ) is not None:
                raise HTTPException(status_code=403, detail="permission_denied")
            return str(app.id)

        raise HTTPException(status_code=400, detail="invalid_decision")

    def _validated_decision_value(
        self,
        task,
        payload: AgentBuilderParameterTaskDecisionRequest,
    ) -> Any:
        decision_value = _decision_value(payload)
        if payload.action != "set" or payload.value is None:
            return decision_value
        if isinstance(payload.value, ParameterCredentialValue):
            return self._resolve_reference_value(task, payload.value.credential_id)
        if hasattr(payload.value, "resource_id"):
            return self._resolve_reference_value(task, payload.value.resource_id)
        return decision_value

    def _validate_workflow_node_pair(self, node_data: dict[str, Any]) -> None:
        workflow_value = node_data.get("workflowId")
        app_value = node_data.get("appId")
        if workflow_value is None or app_value is None:
            return
        if any(
            isinstance(value, str) and not value.strip()
            for value in (workflow_value, app_value)
        ):
            return
        try:
            workflow_id = UUID(str(workflow_value))
            app_id = UUID(str(app_value))
        except (TypeError, ValueError, AttributeError) as exc:
            raise HTTPException(status_code=400, detail="invalid_decision") from exc

        workflow = (
            self.db.query(Workflow)
            .filter(
                Workflow.id == workflow_id,
                Workflow.organization_id == self.organization_id,
            )
            .first()
        )
        app = (
            self.db.query(App)
            .filter(
                App.id == app_id,
                App.organization_id == self.organization_id,
            )
            .first()
        )
        if (
            workflow is None
            or app is None
            or not has_workflow_permission(
                self.db,
                self.user_id,
                workflow.id,
                "read",
                organization_id=self.organization_id,
            )
            or AppService.access_denial_status(self.db, app, self.user_id, "read")
            is not None
        ):
            raise HTTPException(status_code=403, detail="permission_denied")
        if app.workflow_id != workflow.id:
            raise HTTPException(status_code=400, detail="invalid_decision")

    def _normalize_workflow_node_pair(
        self,
        node_data: dict[str, Any],
        *,
        changed_parameter_key: str,
    ) -> dict[str, Any]:
        normalized = dict(node_data)
        app_value = normalized.get("appId")
        if (
            changed_parameter_key != "appId"
            or app_value is None
            or (isinstance(app_value, str) and not app_value.strip())
        ):
            self._validate_workflow_node_pair(normalized)
            return normalized

        try:
            app_id = UUID(str(app_value))
        except (TypeError, ValueError, AttributeError) as exc:
            raise HTTPException(status_code=400, detail="invalid_decision") from exc

        app = (
            self.db.query(App)
            .filter(
                App.id == app_id,
                App.organization_id == self.organization_id,
            )
            .first()
        )
        if app is None or AppService.access_denial_status(
            self.db, app, self.user_id, "read"
        ) is not None:
            raise HTTPException(status_code=403, detail="permission_denied")

        workflow = (
            self.db.query(Workflow)
            .filter(
                Workflow.id == app.workflow_id,
                Workflow.organization_id == self.organization_id,
            )
            .first()
        )
        if workflow is None or not has_workflow_permission(
            self.db,
            self.user_id,
            workflow.id,
            "read",
            organization_id=self.organization_id,
        ):
            raise HTTPException(status_code=403, detail="permission_denied")

        normalized["workflowId"] = str(workflow.id)
        return normalized

    def cancel_group(
        self,
        session_id: UUID,
        group_id: UUID,
        payload: AgentBuilderParameterGroupCancelRequest,
    ) -> AgentBuilderParameterGroupCancelResponse:
        session = (
            self.db.query(AgentBuilderSession)
            .filter(
                AgentBuilderSession.id == session_id,
                AgentBuilderSession.user_id == self.user_id,
                AgentBuilderSession.organization_id == self.organization_id,
            )
            .first()
        )
        if session is None or session.workflow_id is None:
            raise HTTPException(status_code=404, detail="resource_not_found")
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
            raise HTTPException(status_code=404, detail="resource_not_found")
        if not has_workflow_permission(
            self.db,
            self.user_id,
            workflow.id,
            "write",
            organization_id=self.organization_id,
        ):
            raise HTTPException(status_code=403, detail="permission_denied")
        request_rows = (
            self.db.query(AgentBuilderRequest)
            .filter(AgentBuilderRequest.session_id == session.id)
            .order_by(AgentBuilderRequest.created_at.desc())
            .with_for_update()
            .all()
        )
        cancel_fingerprint = _cancel_payload_fingerprint(group_id, payload)
        for request_row in request_rows:
            existing = self.repository.find_parameter_group_cancellation_record(
                request_row, payload.operation_id
            )
            if existing is not None:
                if (
                    existing.get("group_id") != str(group_id)
                    or (
                        "expected_task_id" in existing
                        and existing.get("expected_task_id")
                        != str(payload.expected_task_id)
                    )
                    or (
                        "expected_task_version" in existing
                        and existing.get("expected_task_version")
                        != payload.expected_task_version
                    )
                    or (
                        "payload_fingerprint" in existing
                        and existing.get("payload_fingerprint") != cancel_fingerprint
                    )
                ):
                    raise HTTPException(status_code=409, detail="task_conflict")
                try:
                    existing_group = self.repository.load_parameter_group(
                        request_row, group_id
                    )
                except AgentBuilderRepositoryError:
                    raise HTTPException(
                        status_code=409, detail="task_conflict"
                    ) from None
                return AgentBuilderParameterGroupCancelResponse(
                    operation_id=payload.operation_id,
                    parameter_group=existing_group,
                )
            try:
                group = self.repository.load_parameter_group(request_row, group_id)
            except AgentBuilderRepositoryError:
                continue
            try:
                canceled = cancel_parameter_group(
                    group,
                    expected_task_id=payload.expected_task_id,
                    expected_task_version=payload.expected_task_version,
                )
            except ParameterTaskConflict as exc:
                raise HTTPException(status_code=409, detail="task_conflict") from exc
            self.repository.store_parameter_group(request_row, canceled)
            self.repository.store_parameter_group_cancellation(
                request_row,
                operation_id=payload.operation_id,
                group_id=group_id,
                expected_task_id=payload.expected_task_id,
                expected_task_version=payload.expected_task_version,
                payload_fingerprint=cancel_fingerprint,
            )
            add_action_audit(
                self.db,
                AuditAction.AGENT_BUILDER_PARAMETER_GROUP_CANCELED,
                self.user_id,
                "workflow",
                workflow.id,
                organization_id=self.organization_id,
                metadata={
                    "session_id": str(session.id),
                    "group_id": str(group_id),
                    "operation_id": str(payload.operation_id),
                    "expected_task_id": str(payload.expected_task_id),
                },
            )
            self.db.commit()
            return AgentBuilderParameterGroupCancelResponse(
                operation_id=payload.operation_id,
                parameter_group=canceled,
            )
        raise HTTPException(status_code=404, detail="resource_not_found")

    def decide(
        self,
        session_id: UUID,
        task_id: UUID,
        payload: AgentBuilderParameterTaskDecisionRequest,
    ) -> AgentBuilderParameterTaskDecisionResponse:
        session = (
            self.db.query(AgentBuilderSession)
            .filter(
                AgentBuilderSession.id == session_id,
                AgentBuilderSession.user_id == self.user_id,
                AgentBuilderSession.organization_id == self.organization_id,
            )
            .first()
        )
        if session is None or session.workflow_id is None:
            raise HTTPException(status_code=404, detail="resource_not_found")
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
            raise HTTPException(status_code=404, detail="resource_not_found")
        if not has_workflow_permission(
            self.db,
            self.user_id,
            workflow.id,
            "write",
            organization_id=self.organization_id,
        ):
            raise HTTPException(status_code=403, detail="permission_denied")
        request_rows = (
            self.db.query(AgentBuilderRequest)
            .filter(AgentBuilderRequest.session_id == session.id)
            .order_by(AgentBuilderRequest.created_at.desc())
            .with_for_update()
            .all()
        )
        request_row = None
        group = None
        for candidate in request_rows:
            try:
                group = self.repository.load_parameter_group_for_task(candidate, task_id)
                request_row = candidate
                break
            except AgentBuilderRepositoryError:
                continue
        if request_row is None or group is None:
            raise HTTPException(status_code=404, detail="resource_not_found")
        if payload.action == "set" and isinstance(payload.value, ParameterSecretValue):
            raise HTTPException(status_code=400, detail="secret_forbidden")
        decision_fingerprint = _decision_payload_fingerprint(task_id, payload)
        prior_local = self.repository.find_local_task_decision(
            request_row, payload.operation_id
        )
        if prior_local is not None:
            if _task_decision_record_conflicts(
                prior_local,
                task_id=task_id,
                action=payload.action,
                expected_task_version=payload.expected_task_version,
                payload_fingerprint=decision_fingerprint,
                allow_legacy_missing_version=True,
            ):
                raise HTTPException(status_code=409, detail="task_conflict")
            current = next(task for task in group.tasks if task.task_id == task_id)
            if prior_local.get("result") == "invalid":
                return AgentBuilderParameterTaskDecisionResponse(
                    task=current,
                    graph_mutation=None,
                    next_task_id=None,
                    group_status=group.status,
                    awaiting_persistence_ack=False,
                    validation_issues=list(
                        prior_local.get("validation_issues") or []
                    ),
                )
            next_task_id = (
                previous_reopenable_task_id(group.tasks, task_id)
                if payload.action == "previous"
                else next(
                    (
                        task.task_id
                        for task in group.tasks
                        if task.status == "active"
                    ),
                    None,
                )
            )
            return AgentBuilderParameterTaskDecisionResponse(
                task=current,
                graph_mutation=None,
                next_task_id=next_task_id,
                group_status=group.status,
                awaiting_persistence_ack=False,
            )
        task = next(task for task in group.tasks if task.task_id == task_id)
        prior_pending = self.repository.find_pending_task_decision(
            request_row, payload.operation_id
        )
        if prior_pending is not None:
            if _task_decision_record_conflicts(
                prior_pending,
                task_id=task_id,
                action=payload.action,
                expected_task_version=payload.expected_task_version,
                payload_fingerprint=decision_fingerprint,
            ):
                raise HTTPException(status_code=409, detail="task_conflict")
            return AgentBuilderParameterTaskDecisionResponse(
                task=task,
                graph_mutation=None,
                next_task_id=None,
                group_status=group.status,
                awaiting_persistence_ack=True,
            )
        if group.status in {"pending_save", "pending_ack"}:
            raise HTTPException(status_code=409, detail="task_conflict")
        pending_for_version = self.repository.find_pending_task_decision_for_task_version(
            request_row,
            task_id=task_id,
            expected_task_version=payload.expected_task_version,
        )
        if (
            pending_for_version is not None
            and pending_for_version.get("operation_id") != str(payload.operation_id)
        ):
            raise HTTPException(status_code=409, detail="task_conflict")
        if payload.action == "set" and payload.value is not None:
            if payload.value.kind != task.input_type:
                raise HTTPException(status_code=400, detail="invalid_decision")
            if isinstance(payload.value, ParameterVariableSelectorValue):
                try:
                    ParameterSuggestionResolver().validate_selection(
                        graph=workflow.graph or {"nodes": [], "edges": []},
                        target_node_id=task.node_id,
                        parameter_key=task.parameter_key,
                        suggestion_id=payload.value.suggestion_id,
                        value_selector=list(payload.value.value_selector),
                    )
                except ParameterSuggestionError as exc:
                    raise HTTPException(
                        status_code=400, detail="invalid_decision"
                    ) from exc
            if isinstance(payload.value, ParameterVariableSelectorListValue):
                resolver = ParameterSuggestionResolver()
                try:
                    for selection in payload.value.selections:
                        resolver.validate_selection(
                            graph=workflow.graph or {"nodes": [], "edges": []},
                            target_node_id=task.node_id,
                            parameter_key=task.parameter_key,
                            suggestion_id=selection.suggestion_id,
                            value_selector=list(selection.value_selector),
                        )
                except ParameterSuggestionError as exc:
                    raise HTTPException(
                        status_code=400, detail="invalid_decision"
                    ) from exc
        if payload.action == "confirm":
            if task.resolution_source is None or not (
                recommendation_matches_canonical_graph(task, workflow.graph)
            ):
                raise HTTPException(status_code=409, detail="task_conflict")
        decision_value = self._validated_decision_value(task, payload)
        if payload.action == "set":
            if is_condition_branch_task(task):
                validation_issues = condition_branch_decision_issues(
                    graph=workflow.graph or {"nodes": [], "edges": []},
                    task=task,
                    value=decision_value,
                )
            else:
                task_node = next(
                    (
                        node
                        for node in (workflow.graph or {}).get("nodes") or []
                        if isinstance(node, dict)
                        and str(node.get("id")) == task.node_id
                    ),
                    None,
                )
                node_data = (
                    task_node.get("data")
                    if isinstance(task_node, dict)
                    and isinstance(task_node.get("data"), dict)
                    else {}
                )
                try:
                    validation_issues = validate_direct_set_value(
                        node_type=task.node_type,
                        parameter_key=task.parameter_key,
                        task_input_type=task.input_type,
                        value=decision_value,
                    )
                except ParameterTaskSafetyError as exc:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "secret_forbidden"
                            if str(exc) == "secret_forbidden"
                            else "invalid_decision"
                        ),
                    ) from exc
                if not validation_issues:
                    validation_issues = validate_node_parameter_update(
                        task.node_type,
                        task.parameter_key,
                        node_data,
                        decision_value,
                    )
            if validation_issues:
                validation_issue_payload = [
                    {
                        "code": issue,
                        "parameter_key": task.parameter_key,
                    }
                    for issue in validation_issues
                ]
                invalid_tasks = [
                    item.model_copy(
                        update={
                            "status": "invalid",
                            "task_version": item.task_version + 1,
                        }
                    )
                    if item.task_id == task_id
                    else item
                    for item in group.tasks
                ]
                invalid_group = group.model_copy(
                    update={"status": "active", "tasks": invalid_tasks}
                )
                self.repository.store_parameter_group(request_row, invalid_group)
                self.repository.store_local_task_decision(
                    request_row,
                    operation_id=payload.operation_id,
                    task_id=task.task_id,
                    action="set",
                    expected_task_version=payload.expected_task_version,
                    payload_fingerprint=decision_fingerprint,
                    result="invalid",
                    validation_issues=validation_issue_payload,
                )
                add_action_audit(
                    self.db,
                    AuditAction.AGENT_BUILDER_PARAMETER_DECISION_RECORDED,
                    self.user_id,
                    "workflow",
                    workflow.id,
                    organization_id=self.organization_id,
                    metadata={
                        "session_id": str(session.id),
                        "group_id": str(group.group_id),
                        "task_id": str(task.task_id),
                        "parameter_key": task.parameter_key,
                        "action": "set",
                        "reason": "catalog_validation_failed",
                    },
                    status="failure",
                )
                self.db.commit()
                invalid_task = next(
                    item for item in invalid_tasks if item.task_id == task_id
                )
                return AgentBuilderParameterTaskDecisionResponse(
                    task=invalid_task,
                    graph_mutation=None,
                    next_task_id=None,
                    group_status=invalid_group.status,
                    awaiting_persistence_ack=False,
                    validation_issues=validation_issue_payload,
                )
        try:
            decision = prepare_task_decision(
                tasks=group.tasks,
                task_id=task_id,
                operation_id=payload.operation_id,
                expected_task_version=payload.expected_task_version,
                action=payload.action,
                value=decision_value,
            )
        except ParameterTaskConflict as exc:
            raise HTTPException(status_code=409, detail="task_conflict") from exc

        if decision.action == "previous":
            return AgentBuilderParameterTaskDecisionResponse(
                task=task,
                graph_mutation=None,
                next_task_id=previous_reopenable_task_id(group.tasks, task_id),
                group_status=group.status,
                awaiting_persistence_ack=False,
            )

        if not decision.awaiting_persistence_ack:
            tasks = apply_local_task_decision(group.tasks, decision)
            group_with_tasks = group.model_copy(update={"tasks": tasks})
            updated_group = group_with_tasks.model_copy(
                update={"status": _group_status(group_with_tasks)}
            )
            self.repository.store_parameter_group(request_row, updated_group)
            self.repository.store_local_task_decision(
                request_row,
                operation_id=payload.operation_id,
                task_id=task.task_id,
                action=decision.action,
                expected_task_version=decision.expected_task_version,
                payload_fingerprint=decision_fingerprint,
            )
            add_action_audit(
                self.db,
                AuditAction.AGENT_BUILDER_PARAMETER_DECISION_RECORDED,
                self.user_id,
                "workflow",
                workflow.id,
                organization_id=self.organization_id,
                metadata={
                    "session_id": str(session.id),
                    "group_id": str(group.group_id),
                    "task_id": str(task.task_id),
                    "parameter_key": task.parameter_key,
                    "action": decision.action,
                    "requires_acknowledgement": False,
                },
            )
            self.db.commit()
            current = next(task for task in tasks if task.task_id == task_id)
            next_task_id = (
                previous_reopenable_task_id(tasks, task_id)
                if decision.action == "previous"
                else next(
                    (
                        task.task_id
                        for task in tasks
                        if task.status == "active"
                    ),
                    None,
                )
            )
            return AgentBuilderParameterTaskDecisionResponse(
                task=current,
                graph_mutation=None,
                next_task_id=next_task_id,
                group_status=updated_group.status,
                awaiting_persistence_ack=False,
            )

        graph = workflow.graph or {"nodes": [], "edges": []}
        if decision.action == "set" and is_condition_branch_task(task):
            try:
                operations = build_condition_branch_operations(
                    graph=graph,
                    task=task,
                    value=decision.graph_data_patch[task.parameter_key],
                )
            except ValueError as exc:
                raise HTTPException(status_code=409, detail="stale_graph") from exc
        else:
            node = next(
                (
                    item
                    for item in graph.get("nodes") or []
                    if str(item.get("id"))
                    == next(
                        task.node_id for task in group.tasks if task.task_id == task_id
                    )
                ),
                None,
            )
            if node is None:
                raise HTTPException(status_code=409, detail="stale_graph")
            data = dict(node.get("data") or {})
            if decision.action == "set":
                data = apply_parameter_value_to_node_data(
                    task.node_type,
                    task.parameter_key,
                    data,
                    decision.graph_data_patch[task.parameter_key],
                )
                data["_deferred_parameters"] = [
                    key
                    for key in data.get("_deferred_parameters", [])
                    if key != task.parameter_key
                ]
            elif decision.action == "clear":
                data = remove_node_parameter_value(
                    task.node_type,
                    task.parameter_key,
                    data,
                )
                data["_deferred_parameters"] = [
                    key
                    for key in data.get("_deferred_parameters", [])
                    if key != task.parameter_key
                ]
            else:
                data.pop(task.parameter_key, None)
                data["_deferred_parameters"] = list(
                    dict.fromkeys(
                        [*data.get("_deferred_parameters", []), task.parameter_key]
                    )
                )
            if task.node_type == "conditionNode" and task.parameter_key == "cases":
                data = prepare_condition_cases_data(data)
            else:
                if task.node_type == "workflowNode":
                    data = self._normalize_workflow_node_pair(
                        data,
                        changed_parameter_key=task.parameter_key,
                    )
                data["configuration_state"] = derive_node_configuration_state(
                    task.node_type, data
                )
            operations = [
                {
                    "op": "replace_node_data",
                    "node_id": task.node_id,
                    "data": data,
                }
            ]
        mutation = GraphMutationBuilder().build(
            operation_id=payload.operation_id,
            kind="parameter_update",
            generation_mode="configure_and_generate",
            workflow_id=workflow.id,
            base_graph=graph,
            expected_workflow_updated_at=workflow.updated_at,
            operations=operations,
            completion_context=GraphMutationCompletionContext(
                parameter_task_id=task.task_id
            ),
        )
        self.repository.store_envelope(
            request_row,
            GraphMutationSafeEnvelope.from_mutation(mutation),
        )
        self.repository.store_pending_task_decision(
            request_row,
            operation_id=payload.operation_id,
            task_id=task.task_id,
            action=decision.action,
            expected_task_version=decision.expected_task_version,
            payload_fingerprint=decision_fingerprint,
        )
        pending_group = group.model_copy(update={"status": "pending_ack"})
        self.repository.store_parameter_group(request_row, pending_group)
        add_action_audit(
            self.db,
            AuditAction.AGENT_BUILDER_PARAMETER_DECISION_RECORDED,
            self.user_id,
            "workflow",
            workflow.id,
            organization_id=self.organization_id,
            metadata={
                "session_id": str(session.id),
                "group_id": str(group.group_id),
                "task_id": str(task.task_id),
                "parameter_key": task.parameter_key,
                "action": decision.action,
                "operation_id": str(payload.operation_id),
                "requires_acknowledgement": True,
            },
        )
        self.db.commit()
        return AgentBuilderParameterTaskDecisionResponse(
            task=task,
            graph_mutation=mutation,
            next_task_id=None,
            group_status=pending_group.status,
            awaiting_persistence_ack=True,
        )
