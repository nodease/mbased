from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from apps.gateway.services.app_service import AppService
from apps.gateway.services.llm_service import LLMService
from apps.shared.db.models.app import App
from apps.shared.db.models.mail_credential import MAIL_CREDENTIAL_ACTIVE, MailCredential
from apps.shared.db.models.workflow import Workflow
from apps.shared.schemas.agent_builder import (
    AgentBuilderParameterCandidate,
    AgentBuilderParameterGroup,
    AgentBuilderParameterTask,
)
from apps.shared.services.permissions import (
    has_mail_credential_permission,
    has_workflow_permission,
)


class ParameterCandidateProvider:
    """Builds safe, permission-filtered reference choices for parameter tasks."""

    def __init__(
        self,
        db: Session,
        *,
        user_id: UUID,
        organization_id: UUID,
    ) -> None:
        self.db = db
        self.user_id = user_id
        self.organization_id = organization_id

    def enrich_group(
        self,
        group: AgentBuilderParameterGroup | None,
        *,
        graph: dict[str, Any] | None = None,
    ) -> AgentBuilderParameterGroup | None:
        if group is None:
            return None
        nodes = {
            str(node.get("id")): node
            for node in (graph or {}).get("nodes") or []
            if isinstance(node, dict) and node.get("id") is not None
        }
        tasks = []
        for task in group.tasks:
            node = nodes.get(task.node_id)
            node_data = (
                node.get("data")
                if isinstance(node, dict) and isinstance(node.get("data"), dict)
                else {}
            )
            candidates = self.for_task(task, node_data=node_data)
            update: dict[str, Any] = {"candidates": candidates}
            if self._reference_is_unavailable(task, nodes, candidates):
                update.update(
                    {
                        "status": "invalid",
                        "configuration_state": "unresolved",
                    }
                )
            tasks.append(task.model_copy(update=update))
        return group.model_copy(
            update={"tasks": tasks}
        )

    def _reference_is_unavailable(
        self,
        task: AgentBuilderParameterTask,
        nodes: dict[str, dict[str, Any]],
        candidates: list[AgentBuilderParameterCandidate],
    ) -> bool:
        if task.input_type not in {"resource_ref", "credential_ref"}:
            return False
        node = nodes.get(task.node_id)
        if node is None:
            return False
        value = (node.get("data") or {}).get(task.parameter_key)
        if value is None or value == "":
            return False
        candidate_ids = {str(candidate.candidate_id) for candidate in candidates}
        candidate_reference_values = {
            str(candidate.reference_value)
            for candidate in candidates
            if candidate.reference_value not in {None, ""}
        }
        if str(value) in candidate_ids or str(value) in candidate_reference_values:
            return False
        if task.node_type == "llmNode" and task.parameter_key in {
            "model_id",
            "fallback_model_id",
        }:
            return not self._model_runtime_value_is_available(value, candidate_ids)
        return True

    def _model_runtime_value_is_available(
        self,
        value: Any,
        candidate_ids: set[str],
    ) -> bool:
        return any(
            str(option.model.id) in candidate_ids
            and str(option.model.model_id_for_api_call) == str(value)
            for group in LLMService.get_agent_builder_model_option_groups(
                self.db,
                self.user_id,
                self.organization_id,
            )
            for option in group.options
        )

    def for_task(
        self,
        task: AgentBuilderParameterTask,
        *,
        node_data: dict[str, Any] | None = None,
    ) -> list[AgentBuilderParameterCandidate]:
        if task.input_type not in {"resource_ref", "credential_ref"}:
            return []
        if task.node_type == "llmNode" and task.parameter_key == "model_id":
            return self._model_candidates()
        if task.node_type == "llmNode" and task.parameter_key == "fallback_model_id":
            primary_model = (node_data or {}).get("model_id")
            return self._model_candidates(excluded_runtime_value=primary_model)
        if task.node_type == "workflowNode" and task.parameter_key == "workflowId":
            return self._workflow_candidates()
        if task.node_type == "workflowNode" and task.parameter_key == "appId":
            return self._app_candidates()
        if (
            task.input_type == "credential_ref"
            and task.node_type in {"mailNode", "gmailDraftNode"}
        ):
            return self._mail_credential_candidates(node_type=task.node_type)
        return []

    def _model_candidates(
        self,
        *,
        excluded_runtime_value: Any = None,
    ) -> list[AgentBuilderParameterCandidate]:
        candidates = [
            AgentBuilderParameterCandidate(
                candidate_id=option.model.id,
                kind="resource_ref",
                label=option.model.name,
                description=group.provider_name,
                reference_value=option.model.model_id_for_api_call,
            )
            for group in LLMService.get_agent_builder_model_option_groups(
                self.db,
                self.user_id,
                self.organization_id,
            )
            for option in group.options
        ]
        if excluded_runtime_value in {None, ""}:
            return candidates
        return [
            candidate
            for candidate in candidates
            if str(candidate.candidate_id) != str(excluded_runtime_value)
            and str(candidate.reference_value) != str(excluded_runtime_value)
        ]

    def _workflow_candidates(self) -> list[AgentBuilderParameterCandidate]:
        workflows = (
            self.db.query(Workflow)
            .filter(Workflow.organization_id == self.organization_id)
            .all()
        )
        return [
            AgentBuilderParameterCandidate(
                candidate_id=workflow.id,
                kind="resource_ref",
                label=f"Workflow {str(workflow.id)[:8]}",
            )
            for workflow in workflows
            if has_workflow_permission(
                self.db,
                self.user_id,
                workflow.id,
                "read",
                organization_id=self.organization_id,
            )
        ]

    def _app_candidates(self) -> list[AgentBuilderParameterCandidate]:
        apps = (
            self.db.query(App)
            .filter(App.organization_id == self.organization_id)
            .all()
        )
        return [
            AgentBuilderParameterCandidate(
                candidate_id=app.id,
                kind="resource_ref",
                label=app.name,
                description=app.description or "",
            )
            for app in apps
            if AppService.access_denial_status(
                self.db,
                app,
                self.user_id,
                "read",
            )
            is None
        ]

    def _mail_credential_candidates(
        self,
        *,
        node_type: str,
    ) -> list[AgentBuilderParameterCandidate]:
        credentials = (
            self.db.query(MailCredential)
            .filter(
                MailCredential.organization_id == self.organization_id,
                MailCredential.status == MAIL_CREDENTIAL_ACTIVE,
            )
            .all()
        )
        return [
            AgentBuilderParameterCandidate(
                candidate_id=credential.id,
                kind="credential_ref",
                label=credential.credential_name,
                description=credential.provider,
            )
            for credential in credentials
            if (
                node_type != "gmailDraftNode"
                or (
                    credential.provider == "gmail"
                    and credential.auth_type == "oauth2"
                )
            )
            if has_mail_credential_permission(
                self.db,
                self.user_id,
                credential.id,
                "use",
                organization_id=self.organization_id,
            )
        ]
