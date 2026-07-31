"""Registry for generic resource permission routing.

The registry owns resource-type metadata, not mutation orchestration. Services
use it to avoid falling through to an unrelated permission table when a new or
mistyped resource type appears.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping

from sqlalchemy.orm import Session

from apps.shared.db.models.knowledge import KnowledgeBase
from apps.shared.db.models.llm import LLMCredential
from apps.shared.db.models.mail_credential import MailCredential
from apps.shared.db.models.team import (
    TeamKnowledgePermission,
    TeamLLMPermission,
    TeamMailCredentialPermission,
    TeamWorkflowPermission,
    UserKnowledgePermission,
    UserLLMPermission,
    UserMailCredentialPermission,
    UserWorkflowPermission,
)
from apps.shared.db.models.workflow import Workflow
from apps.shared.permissions import (
    knowledge_base_auth_state_allows,
    llm_credential_auth_state_allows,
    mail_credential_auth_state_allows,
    workflow_auth_state_allows,
)
from apps.shared.services.permissions import (
    get_effective_knowledge_base_auth_state,
    get_effective_llm_credential_auth_state,
    get_effective_mail_credential_auth_state,
    get_effective_workflow_auth_state,
)


class ResourcePermissionRegistryError(Exception):
    """Base registry error."""


class ResourceTypeNotRegistered(ResourcePermissionRegistryError):
    def __init__(self, resource_type: str):
        super().__init__(resource_type)
        self.resource_type = resource_type


class ResourceTargetNotFound(ResourcePermissionRegistryError):
    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


@dataclass(frozen=True)
class PermissionRoute:
    model: Any
    resource_column: str
    grantee_column: str

    def filters(self, *, resource_id: Any, grantee_id: Any) -> dict[str, Any]:
        return {
            self.resource_column: resource_id,
            self.grantee_column: grantee_id,
        }


@dataclass(frozen=True)
class ResourcePermissionSpec:
    resource_type: str
    target_model: Any
    not_found_detail: str
    team_route: PermissionRoute
    user_route: PermissionRoute
    auth_state_resolver: Callable[..., str]
    auth_state_allows: Callable[[Any, str], bool]
    active_filter: Callable[[Any], Any] | None = None

    def permission_route(self, grantee_type: str) -> PermissionRoute:
        if grantee_type == "team":
            return self.team_route
        if grantee_type == "user":
            return self.user_route
        raise ResourceTypeNotRegistered(f"{self.resource_type}:{grantee_type}")


RESOURCE_PERMISSION_REGISTRY: Mapping[str, ResourcePermissionSpec] = MappingProxyType(
    {
        "workflow": ResourcePermissionSpec(
            resource_type="workflow",
            target_model=Workflow,
            not_found_detail="Workflow not found",
            team_route=PermissionRoute(
                model=TeamWorkflowPermission,
                resource_column="workflow_id",
                grantee_column="team_id",
            ),
            user_route=PermissionRoute(
                model=UserWorkflowPermission,
                resource_column="workflow_id",
                grantee_column="user_id",
            ),
            auth_state_resolver=lambda db, user_id, resource_id, *, organization_id: (
                get_effective_workflow_auth_state(
                    db,
                    user_id,
                    resource_id,
                    organization_id=organization_id,
                )
            ),
            auth_state_allows=lambda auth_state, action: workflow_auth_state_allows(
                auth_state,
                action,
            ),
        ),
        "knowledge_base": ResourcePermissionSpec(
            resource_type="knowledge_base",
            target_model=KnowledgeBase,
            not_found_detail="Knowledge Base not found",
            team_route=PermissionRoute(
                model=TeamKnowledgePermission,
                resource_column="knowledge_base_id",
                grantee_column="team_id",
            ),
            user_route=PermissionRoute(
                model=UserKnowledgePermission,
                resource_column="knowledge_base_id",
                grantee_column="user_id",
            ),
            auth_state_resolver=lambda db, user_id, resource_id, *, organization_id: (
                get_effective_knowledge_base_auth_state(
                    db,
                    user_id,
                    resource_id,
                    organization_id=organization_id,
                )
            ),
            auth_state_allows=lambda auth_state, action: (
                knowledge_base_auth_state_allows(
                    auth_state,
                    action,
                )
            ),
            active_filter=lambda model: model.lifecycle_state == "active",
        ),
        "llm_credential": ResourcePermissionSpec(
            resource_type="llm_credential",
            target_model=LLMCredential,
            not_found_detail="Credential not found",
            team_route=PermissionRoute(
                model=TeamLLMPermission,
                resource_column="llm_credential_id",
                grantee_column="team_id",
            ),
            user_route=PermissionRoute(
                model=UserLLMPermission,
                resource_column="llm_credential_id",
                grantee_column="user_id",
            ),
            auth_state_resolver=lambda db, user_id, resource_id, *, organization_id: (
                get_effective_llm_credential_auth_state(
                    db,
                    user_id,
                    resource_id,
                    organization_id=organization_id,
                )
            ),
            auth_state_allows=lambda auth_state, action: (
                llm_credential_auth_state_allows(
                    auth_state,
                    action,
                )
            ),
        ),
        "mail_credential": ResourcePermissionSpec(
            resource_type="mail_credential",
            target_model=MailCredential,
            not_found_detail="Mail credential not found",
            team_route=PermissionRoute(
                model=TeamMailCredentialPermission,
                resource_column="mail_credential_id",
                grantee_column="team_id",
            ),
            user_route=PermissionRoute(
                model=UserMailCredentialPermission,
                resource_column="mail_credential_id",
                grantee_column="user_id",
            ),
            auth_state_resolver=lambda db, user_id, resource_id, *, organization_id: (
                get_effective_mail_credential_auth_state(
                    db, user_id, resource_id, organization_id=organization_id
                )
            ),
            auth_state_allows=lambda auth_state, action: (
                mail_credential_auth_state_allows(auth_state, action)
            ),
            active_filter=lambda model: model.status == "active",
        ),
    }
)


def registered_resource_types() -> frozenset[str]:
    return frozenset(RESOURCE_PERMISSION_REGISTRY)


def resource_permission_spec(resource_type: str) -> ResourcePermissionSpec:
    try:
        return RESOURCE_PERMISSION_REGISTRY[resource_type]
    except KeyError as exc:
        raise ResourceTypeNotRegistered(resource_type) from exc


def resource_organization_id(
    db: Session,
    resource_type: str,
    resource_id: Any,
) -> Any:
    spec = resource_permission_spec(resource_type)
    query = db.query(spec.target_model).filter(spec.target_model.id == resource_id)
    if spec.active_filter is not None:
        query = query.filter(spec.active_filter(spec.target_model))
    resource = query.first()
    if not resource:
        raise ResourceTargetNotFound(spec.not_found_detail)
    return resource.organization_id


def effective_resource_auth_state(
    db: Session,
    *,
    resource_type: str,
    user_id: Any,
    resource_id: Any,
    organization_id: Any,
) -> str:
    spec = resource_permission_spec(resource_type)
    return spec.auth_state_resolver(
        db,
        user_id,
        resource_id,
        organization_id=organization_id,
    )


def resource_auth_state_allows(
    resource_type: str,
    auth_state: str,
    action: str,
) -> bool:
    spec = resource_permission_spec(resource_type)
    return spec.auth_state_allows(auth_state, action)


def permission_model_and_filters(
    *,
    resource_type: str,
    grantee_type: str,
    resource_id: Any,
    grantee_id: Any,
) -> tuple[Any, dict[str, Any]]:
    route = resource_permission_spec(resource_type).permission_route(grantee_type)
    return route.model, route.filters(
        resource_id=resource_id,
        grantee_id=grantee_id,
    )
