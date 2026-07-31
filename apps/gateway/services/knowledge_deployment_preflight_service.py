"""Compatibility facade for the deployment preflight application boundary."""

from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy.orm import Session

from apps.gateway.application.deployment.errors import DeploymentPreflightBlocked
from apps.gateway.application.deployment.models import DeploymentPreflightResult
from apps.gateway.application.deployment.preflight import DeploymentPreflightUseCase
from apps.gateway.composition.deployment import build_deployment_preflight_use_case
from apps.shared.db.models.workflow_deployment import DeploymentType
from apps.shared.schemas.deployment import (
    DeploymentPreflightAudience,
    DeploymentPreflightNodeResult,
    DeploymentPreflightRequiredAction,
    DeploymentPreflightResponse,
    DeploymentPreflightSummary,
)


class KnowledgeDeploymentPreflightService:
    """Preserve the legacy service API while delegating policy to the use case."""

    def __init__(
        self,
        db: Session,
        *,
        organization_id: uuid.UUID | None,
        principal_id: uuid.UUID | None = None,
        candidate_graphs_by_app_id: dict[uuid.UUID, dict] | None = None,
        candidate_deployment_types_by_app_id: dict[
            uuid.UUID, DeploymentType
        ] | None = None,
    ) -> None:
        candidate_types = {
            app_id: _deployment_type_value(deployment_type)
            for app_id, deployment_type in (
                candidate_deployment_types_by_app_id or {}
            ).items()
        }
        self.use_case = build_deployment_preflight_use_case(
            db,
            organization_id=organization_id,
            principal_id=principal_id,
            candidate_graphs_by_app_id=candidate_graphs_by_app_id,
            candidate_deployment_types_by_app_id=candidate_types,
        )

    def preview(
        self,
        *,
        deployment_type: DeploymentType,
        graph_snapshot: dict,
        audience_hint: DeploymentPreflightAudience | None = None,
        is_active: bool = True,
    ) -> DeploymentPreflightResponse:
        result = self.use_case.preview(
            deployment_type=_deployment_type_value(deployment_type),
            graph_snapshot=graph_snapshot,
            audience_hint=audience_hint,
            is_active=is_active,
        )
        return _response_schema(result)

    def enforce_active_publish(
        self,
        *,
        deployment_type: DeploymentType,
        graph_snapshot: dict,
    ) -> DeploymentPreflightResponse:
        try:
            result = self.use_case.enforce_active_publish(
                deployment_type=_deployment_type_value(deployment_type),
                graph_snapshot=graph_snapshot,
            )
        except DeploymentPreflightBlocked as exc:
            self._raise_http_blocked(
                exc,
                code="deployment.preflight.blocked",
                message="Deployment preflight blocked activation",
            )
        return _response_schema(result)

    def enforce_inactive_save(
        self,
        *,
        deployment_type: DeploymentType,
        graph_snapshot: dict,
    ) -> DeploymentPreflightResponse:
        try:
            result = self.use_case.enforce_inactive_save(
                deployment_type=_deployment_type_value(deployment_type),
                graph_snapshot=graph_snapshot,
            )
        except DeploymentPreflightBlocked as exc:
            self._raise_http_blocked(
                exc,
                code="deployment.preflight.blocked",
                message="Deployment preflight blocked inactive save",
            )
        return _response_schema(result)

    def enforce_authenticated_run(
        self,
        *,
        graph_snapshot: dict,
    ) -> DeploymentPreflightResponse:
        try:
            result = self.use_case.enforce_authenticated_run(
                graph_snapshot=graph_snapshot,
            )
        except DeploymentPreflightBlocked as exc:
            self._raise_http_blocked(
                exc,
                code="workflow.configuration_preflight.blocked",
                message="Workflow configuration preflight blocked execution",
            )
        return _response_schema(result)

    @staticmethod
    def _raise_http_blocked(
        exc: DeploymentPreflightBlocked,
        *,
        code: str,
        message: str,
    ) -> None:
        response = _response_schema(exc.result)
        raise HTTPException(
            status_code=409,
            detail={
                "error": {
                    "code": code,
                    "message": message,
                    "reason_code": response.safe_summary.blocked_reason,
                    "required_actions": [
                        action.action for action in response.required_actions
                    ],
                    "preflight": response.model_dump(mode="json"),
                }
            },
        ) from exc

    @staticmethod
    def server_derived_audience(
        deployment_type: DeploymentType,
    ) -> DeploymentPreflightAudience:
        return DeploymentPreflightUseCase.server_derived_audience(
            _deployment_type_value(deployment_type)
        )


def _deployment_type_value(deployment_type: DeploymentType | str) -> str:
    return (
        deployment_type.value
        if isinstance(deployment_type, DeploymentType)
        else str(deployment_type)
    )


def _response_schema(result: DeploymentPreflightResult) -> DeploymentPreflightResponse:
    return DeploymentPreflightResponse(
        status=result.status,
        audience=result.audience,
        safe_summary=DeploymentPreflightSummary(
            blocked_reason=result.safe_summary.blocked_reason,
            affected_node_count=result.safe_summary.affected_node_count,
            affected_kb_count_bucket=result.safe_summary.affected_kb_count_bucket,
            affected_collection_count_bucket=(
                result.safe_summary.affected_collection_count_bucket
            ),
            candidate_budget_limited=(
                result.safe_summary.candidate_budget_limited
            ),
        ),
        required_actions=[
            DeploymentPreflightRequiredAction(
                action=action.action,
                label=action.label,
            )
            for action in result.required_actions
        ],
        warnings=list(result.warnings),
        nodes=[
            DeploymentPreflightNodeResult(
                node_id=node.node_id,
                node_type=node.node_type,
                status=node.status,
                reason_codes=list(node.reason_codes),
                knowledge_base_count_bucket=node.knowledge_base_count_bucket,
                knowledge_collection_count_bucket=(
                    node.knowledge_collection_count_bucket
                ),
                candidate_budget_limited=node.candidate_budget_limited,
            )
            for node in result.nodes
        ],
    )


def deployment_preflight_blocked_http_exception(
    result: DeploymentPreflightResult,
) -> HTTPException:
    response = _response_schema(result)
    return HTTPException(
        status_code=409,
        detail={
            "error": {
                "code": "deployment.preflight.blocked",
                "message": "Deployment preflight blocked activation",
                "reason_code": response.safe_summary.blocked_reason,
                "required_actions": [
                    action.action for action in response.required_actions
                ],
                "preflight": response.model_dump(mode="json"),
            }
        },
    )
