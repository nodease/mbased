from types import SimpleNamespace
from typing import get_args

import pytest

from apps.gateway.api.v1.endpoints import permissions as permissions_endpoint
from apps.gateway.services import resource_permission_registry as registry_module
from apps.gateway.services.resource_permission_registry import (
    effective_resource_auth_state,
    permission_model_and_filters,
    registered_resource_types,
    resource_organization_id,
    ResourceTypeNotRegistered,
    ResourceTargetNotFound,
    resource_auth_state_allows,
    resource_permission_spec,
)
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
from apps.shared.schemas.team import (
    ResourcePermissionGrantRequest,
    ResourcePermissionListResponse,
    ResourcePermissionRevokeRequest,
)


def _literal_values(model, field_name: str) -> set[str]:
    return set(get_args(model.model_fields[field_name].annotation))


def test_registry_resource_types_match_public_schemas():
    registry_types = registered_resource_types()

    assert registry_types == _literal_values(
        ResourcePermissionGrantRequest,
        "resource_type",
    )
    assert registry_types == _literal_values(
        ResourcePermissionRevokeRequest,
        "resource_type",
    )
    assert registry_types == _literal_values(
        ResourcePermissionListResponse,
        "resource_type",
    )


def test_registry_mapping_is_immutable():
    with pytest.raises(TypeError):
        registry_module.RESOURCE_PERMISSION_REGISTRY["document"] = (
            resource_permission_spec("workflow")
        )


def test_registry_maps_resource_targets_and_permission_tables():
    workflow = resource_permission_spec("workflow")
    assert workflow.target_model is Workflow
    assert workflow.team_route.model is TeamWorkflowPermission
    assert workflow.user_route.model is UserWorkflowPermission

    knowledge = resource_permission_spec("knowledge_base")
    assert knowledge.target_model is KnowledgeBase
    assert knowledge.team_route.model is TeamKnowledgePermission
    assert knowledge.user_route.model is UserKnowledgePermission

    llm = resource_permission_spec("llm_credential")
    assert llm.target_model is LLMCredential
    assert llm.team_route.model is TeamLLMPermission
    assert llm.user_route.model is UserLLMPermission

    mail = resource_permission_spec("mail_credential")
    assert mail.target_model is MailCredential
    assert mail.team_route.model is TeamMailCredentialPermission
    assert mail.user_route.model is UserMailCredentialPermission


def test_production_permission_api_models_are_resolved_from_registry():
    workflow = resource_permission_spec("workflow")
    assert permissions_endpoint.Workflow is workflow.target_model
    assert permissions_endpoint.TeamWorkflowPermission is workflow.team_route.model
    assert permissions_endpoint.UserWorkflowPermission is workflow.user_route.model

    knowledge = resource_permission_spec("knowledge_base")
    assert permissions_endpoint.KnowledgeBase is knowledge.target_model
    assert permissions_endpoint.TeamKnowledgePermission is knowledge.team_route.model
    assert permissions_endpoint.UserKnowledgePermission is knowledge.user_route.model

    llm = resource_permission_spec("llm_credential")
    assert permissions_endpoint.LLMCredential is llm.target_model
    assert permissions_endpoint.TeamLLMPermission is llm.team_route.model
    assert permissions_endpoint.UserLLMPermission is llm.user_route.model


@pytest.mark.parametrize(
    (
        "resource_type",
        "grantee_type",
        "expected_model",
        "resource_column",
        "grantee_column",
    ),
    [
        (
            "workflow",
            "team",
            TeamWorkflowPermission,
            "workflow_id",
            "team_id",
        ),
        (
            "workflow",
            "user",
            UserWorkflowPermission,
            "workflow_id",
            "user_id",
        ),
        (
            "knowledge_base",
            "team",
            TeamKnowledgePermission,
            "knowledge_base_id",
            "team_id",
        ),
        (
            "knowledge_base",
            "user",
            UserKnowledgePermission,
            "knowledge_base_id",
            "user_id",
        ),
        (
            "llm_credential",
            "team",
            TeamLLMPermission,
            "llm_credential_id",
            "team_id",
        ),
        (
            "llm_credential",
            "user",
            UserLLMPermission,
            "llm_credential_id",
            "user_id",
        ),
        (
            "mail_credential",
            "team",
            TeamMailCredentialPermission,
            "mail_credential_id",
            "team_id",
        ),
        (
            "mail_credential",
            "user",
            UserMailCredentialPermission,
            "mail_credential_id",
            "user_id",
        ),
    ],
)
def test_permission_routes_use_exact_resource_and_grantee_columns(
    resource_type,
    grantee_type,
    expected_model,
    resource_column,
    grantee_column,
):
    model, filters = permission_model_and_filters(
        resource_type=resource_type,
        grantee_type=grantee_type,
        resource_id="resource-1",
        grantee_id="grantee-1",
    )

    assert model is expected_model
    assert filters == {
        resource_column: "resource-1",
        grantee_column: "grantee-1",
    }


def test_registry_fails_closed_for_unknown_resource_type_or_grantee_type():
    with pytest.raises(ResourceTypeNotRegistered):
        resource_permission_spec("document")

    with pytest.raises(ResourceTypeNotRegistered):
        permission_model_and_filters(
            resource_type="knowledge_base",
            grantee_type="organization",
            resource_id="kb-1",
            grantee_id="org-1",
        )


@pytest.mark.parametrize(
    ("resource_type", "resolver_name", "allows_name"),
    [
        (
            "workflow",
            "get_effective_workflow_auth_state",
            "workflow_auth_state_allows",
        ),
        (
            "knowledge_base",
            "get_effective_knowledge_base_auth_state",
            "knowledge_base_auth_state_allows",
        ),
        (
            "llm_credential",
            "get_effective_llm_credential_auth_state",
            "llm_credential_auth_state_allows",
        ),
        (
            "mail_credential",
            "get_effective_mail_credential_auth_state",
            "mail_credential_auth_state_allows",
        ),
    ],
)
def test_registry_spec_owns_auth_resolver_and_action_policy(
    monkeypatch,
    resource_type,
    resolver_name,
    allows_name,
):
    calls = []
    allow_calls = []

    monkeypatch.setattr(
        registry_module,
        resolver_name,
        lambda db, user_id, resource_id, *, organization_id: (
            calls.append((db, user_id, resource_id, organization_id)) or "manager"
        ),
    )
    monkeypatch.setattr(
        registry_module,
        allows_name,
        lambda auth_state, action: allow_calls.append((auth_state, action)) or True,
    )

    assert (
        effective_resource_auth_state(
            "db",
            resource_type=resource_type,
            user_id="user-1",
            resource_id="resource-1",
            organization_id="org-1",
        )
        == "manager"
    )
    assert calls == [("db", "user-1", "resource-1", "org-1")]
    assert resource_auth_state_allows(resource_type, "manager", "manage")
    assert allow_calls == [("manager", "manage")]


class _CaptureQuery:
    def __init__(self, result):
        self.result = result
        self.expressions = []

    def filter(self, *expressions):
        self.expressions.extend(expressions)
        return self

    def first(self):
        return self.result


class _CaptureDb:
    def __init__(self, result):
        self.result = result
        self.queried_model = None
        self.captured_query = None

    def query(self, model):
        self.queried_model = model
        self.captured_query = _CaptureQuery(self.result)
        return self.captured_query


def _expression_values(query):
    values = {}
    for expression in query.expressions:
        key = getattr(getattr(expression, "left", None), "key", None)
        right = getattr(expression, "right", None)
        values[key] = right.value if hasattr(right, "value") else right
    return values


@pytest.mark.parametrize(
    ("resource_type", "target_model", "expects_active_filter"),
    [
        ("workflow", Workflow, False),
        ("knowledge_base", KnowledgeBase, True),
        ("llm_credential", LLMCredential, False),
    ],
)
def test_resource_organization_lookup_uses_registered_target_and_active_filter(
    resource_type,
    target_model,
    expects_active_filter,
):
    db = _CaptureDb(SimpleNamespace(organization_id="org-1"))

    assert resource_organization_id(db, resource_type, "resource-1") == "org-1"
    assert db.queried_model is target_model
    values = _expression_values(db.captured_query)
    assert values["id"] == "resource-1"
    if expects_active_filter:
        assert values["lifecycle_state"] == "active"
    else:
        assert "lifecycle_state" not in values


def test_resource_organization_lookup_preserves_registered_not_found_detail():
    db = _CaptureDb(None)

    with pytest.raises(ResourceTargetNotFound) as exc_info:
        resource_organization_id(db, "knowledge_base", "missing-kb")

    assert exc_info.value.detail == "Knowledge Base not found"
