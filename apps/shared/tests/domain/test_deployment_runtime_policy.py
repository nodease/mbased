from enum import Enum

import pytest
from apps.shared.db.models.workflow_deployment import DeploymentType
from apps.shared.db.models.workflow_run import RunTriggerMode
from apps.shared.domain.deployment_runtime_policy import (
    DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
    DEPLOYMENT_API,
    DEPLOYMENT_CHATBOT,
    DEPLOYMENT_INTERNAL_CHATBOT,
    DEPLOYMENT_MCP,
    DEPLOYMENT_SCHEDULE,
    DEPLOYMENT_WEBAPP,
    DEPLOYMENT_WEBHOOK,
    DEPLOYMENT_WIDGET,
    DEPLOYMENT_WORKFLOW_NODE,
    KNOWN_DEPLOYMENT_TYPES,
    KNOWN_RUNTIME_SURFACES,
    KNOWN_TRIGGER_MODES,
    PUBLIC_APP_DEPLOYMENT_TYPES,
    SURFACE_API_SECRET_RUN,
    SURFACE_APP_PUBLIC_RUN,
    SURFACE_AUTHENTICATED_RUN,
    SURFACE_AUTHENTICATED_RUN_INFO,
    SURFACE_PUBLIC_INFO,
    SURFACE_SCHEDULE_RUN,
    SURFACE_WEBHOOK_RUN,
    SURFACE_WORKFLOW_NODE_CHILD_RUN,
    allowed_deployment_types_for_surface,
    deployment_type_value,
    evaluate_deployment_runtime_surface,
    is_deployment_type_allowed_for_surface,
    is_deployment_type_allowed_for_trigger,
    trigger_mode_to_surface,
)


class FakeDeploymentType(str, Enum):
    API = "api"
    WORKFLOW_NODE = "workflow_node"


@pytest.mark.parametrize(
    ("surface", "allowed"),
    [
        (
            SURFACE_PUBLIC_INFO,
            PUBLIC_APP_DEPLOYMENT_TYPES,
        ),
        (
            SURFACE_AUTHENTICATED_RUN_INFO,
            {
                DEPLOYMENT_API,
                DEPLOYMENT_WEBAPP,
                DEPLOYMENT_WIDGET,
                DEPLOYMENT_CHATBOT,
                DEPLOYMENT_INTERNAL_CHATBOT,
                DEPLOYMENT_MCP,
                DEPLOYMENT_SCHEDULE,
                DEPLOYMENT_WEBHOOK,
            },
        ),
        (
            SURFACE_AUTHENTICATED_RUN,
            {
                DEPLOYMENT_API,
                DEPLOYMENT_WEBAPP,
                DEPLOYMENT_WIDGET,
                DEPLOYMENT_CHATBOT,
                DEPLOYMENT_INTERNAL_CHATBOT,
                DEPLOYMENT_MCP,
                DEPLOYMENT_SCHEDULE,
                DEPLOYMENT_WEBHOOK,
            },
        ),
        (SURFACE_API_SECRET_RUN, {DEPLOYMENT_API}),
        (
            SURFACE_APP_PUBLIC_RUN,
            {DEPLOYMENT_WEBAPP, DEPLOYMENT_WIDGET, DEPLOYMENT_CHATBOT},
        ),
        (SURFACE_WEBHOOK_RUN, {DEPLOYMENT_WEBHOOK}),
        (SURFACE_SCHEDULE_RUN, {DEPLOYMENT_SCHEDULE}),
        (SURFACE_WORKFLOW_NODE_CHILD_RUN, {DEPLOYMENT_WORKFLOW_NODE}),
    ],
)
def test_runtime_surface_matrix_allows_only_documented_types(surface, allowed):
    all_types = {
        DEPLOYMENT_API,
        DEPLOYMENT_WEBAPP,
        DEPLOYMENT_WIDGET,
        DEPLOYMENT_CHATBOT,
        DEPLOYMENT_INTERNAL_CHATBOT,
        DEPLOYMENT_MCP,
        DEPLOYMENT_WORKFLOW_NODE,
        DEPLOYMENT_SCHEDULE,
        DEPLOYMENT_WEBHOOK,
    }

    assert allowed_deployment_types_for_surface(surface) == allowed
    for deployment_type in all_types:
        assert is_deployment_type_allowed_for_surface(deployment_type, surface) is (
            deployment_type in allowed
        )


def test_runtime_policy_fails_closed_for_unknown_surface_or_type():
    unknown_surface = evaluate_deployment_runtime_surface(DEPLOYMENT_API, "future")
    assert unknown_surface.allowed is False
    assert unknown_surface.reason == "unknown_surface"

    unknown_type = evaluate_deployment_runtime_surface("future", SURFACE_PUBLIC_INFO)
    assert unknown_type.allowed is False
    assert unknown_type.reason == "unknown_deployment_type"


def test_internal_chatbot_is_allowed_only_on_authenticated_surfaces():
    deployment_type = "internal_chatbot"

    assert is_deployment_type_allowed_for_surface(
        deployment_type,
        SURFACE_AUTHENTICATED_RUN_INFO,
    )
    assert is_deployment_type_allowed_for_surface(
        deployment_type,
        SURFACE_AUTHENTICATED_RUN,
    )
    assert not is_deployment_type_allowed_for_surface(
        deployment_type,
        SURFACE_PUBLIC_INFO,
    )
    assert not is_deployment_type_allowed_for_surface(
        deployment_type,
        SURFACE_APP_PUBLIC_RUN,
    )


@pytest.mark.parametrize(
    ("allowed_types_by_surface", "surface_by_trigger_mode", "message"),
    [
        ({"future": {DEPLOYMENT_API}}, {}, "surface is unknown"),
        ({SURFACE_PUBLIC_INFO: {"future"}}, {}, "type is unknown"),
        (
            {SURFACE_PUBLIC_INFO: {DEPLOYMENT_API}},
            {"future": SURFACE_PUBLIC_INFO},
            "trigger mode is unknown",
        ),
        (
            {SURFACE_PUBLIC_INFO: {DEPLOYMENT_API}},
            {"api": "future"},
            "trigger surface is unknown",
        ),
        (
            {SURFACE_PUBLIC_INFO: {DEPLOYMENT_API}},
            {"api": SURFACE_API_SECRET_RUN},
            "trigger surface is not configured",
        ),
    ],
)
def test_runtime_policy_injection_rejects_unknown_contract_values(
    allowed_types_by_surface,
    surface_by_trigger_mode,
    message,
):
    with pytest.raises(ValueError, match=message):
        type(DEFAULT_DEPLOYMENT_RUNTIME_POLICY).create(
            allowed_types_by_surface=allowed_types_by_surface,
            surface_by_trigger_mode=surface_by_trigger_mode,
        )


def test_runtime_policy_direct_constructor_rejects_unknown_values():
    policy_type = type(DEFAULT_DEPLOYMENT_RUNTIME_POLICY)
    with pytest.raises(ValueError, match="surface is unknown"):
        policy_type(
            allowed_types_by_surface={
                **DEFAULT_DEPLOYMENT_RUNTIME_POLICY.allowed_types_by_surface,
                "future": frozenset({"future"}),
            },
            surface_by_trigger_mode={
                **DEFAULT_DEPLOYMENT_RUNTIME_POLICY.surface_by_trigger_mode,
                "future": "future",
            },
        )


def test_runtime_policy_direct_constructor_defensively_freezes_mutable_inputs():
    policy_type = type(DEFAULT_DEPLOYMENT_RUNTIME_POLICY)
    allowed_types = {SURFACE_PUBLIC_INFO: {DEPLOYMENT_WEBAPP}}
    trigger_surfaces = {"app": SURFACE_PUBLIC_INFO}

    policy = policy_type(
        allowed_types_by_surface=allowed_types,
        surface_by_trigger_mode=trigger_surfaces,
    )
    allowed_types[SURFACE_PUBLIC_INFO].add(DEPLOYMENT_CHATBOT)
    trigger_surfaces["api"] = SURFACE_PUBLIC_INFO

    assert policy.allowed_types_by_surface[SURFACE_PUBLIC_INFO] == frozenset(
        {DEPLOYMENT_WEBAPP}
    )
    assert policy.surface_by_trigger_mode == {"app": SURFACE_PUBLIC_INFO}


def test_runtime_policy_can_be_replaced_by_explicit_immutable_injection():
    injected_policy = DEFAULT_DEPLOYMENT_RUNTIME_POLICY.with_surface_allowed_types(
        SURFACE_PUBLIC_INFO,
        {DEPLOYMENT_API},
    )

    assert is_deployment_type_allowed_for_surface(
        DEPLOYMENT_API,
        SURFACE_PUBLIC_INFO,
        policy=injected_policy,
    )
    assert not is_deployment_type_allowed_for_surface(
        DEPLOYMENT_WEBAPP,
        SURFACE_PUBLIC_INFO,
        policy=injected_policy,
    )
    assert not is_deployment_type_allowed_for_surface(
        DEPLOYMENT_API,
        SURFACE_PUBLIC_INFO,
    )


def test_runtime_policy_normalizes_enum_like_values():
    assert deployment_type_value(FakeDeploymentType.API) == "api"
    assert is_deployment_type_allowed_for_surface(
        FakeDeploymentType.API,
        SURFACE_API_SECRET_RUN,
    )
    assert not is_deployment_type_allowed_for_surface(
        FakeDeploymentType.WORKFLOW_NODE,
        SURFACE_AUTHENTICATED_RUN,
    )


@pytest.mark.parametrize(
    ("trigger_mode", "surface", "deployment_type"),
    [
        ("api", SURFACE_API_SECRET_RUN, DEPLOYMENT_API),
        ("api_secret", SURFACE_API_SECRET_RUN, DEPLOYMENT_API),
        ("app", SURFACE_APP_PUBLIC_RUN, DEPLOYMENT_CHATBOT),
        ("webhook", SURFACE_WEBHOOK_RUN, DEPLOYMENT_WEBHOOK),
        ("schedule", SURFACE_SCHEDULE_RUN, DEPLOYMENT_SCHEDULE),
        (RunTriggerMode.SCHEDULER, SURFACE_SCHEDULE_RUN, DEPLOYMENT_SCHEDULE),
        ("workflow_node", SURFACE_WORKFLOW_NODE_CHILD_RUN, DEPLOYMENT_WORKFLOW_NODE),
    ],
)
def test_trigger_mode_maps_to_surface(trigger_mode, surface, deployment_type):
    assert trigger_mode_to_surface(trigger_mode) == surface
    assert is_deployment_type_allowed_for_trigger(deployment_type, trigger_mode)


def test_unknown_trigger_mode_fails_closed():
    assert trigger_mode_to_surface("future") is None
    assert not is_deployment_type_allowed_for_trigger(DEPLOYMENT_API, "future")


def test_known_deployment_types_match_canonical_database_enum():
    assert KNOWN_DEPLOYMENT_TYPES == {member.value for member in DeploymentType}


def test_known_policy_contract_sets_match_default_policy():
    assert KNOWN_RUNTIME_SURFACES == set(
        DEFAULT_DEPLOYMENT_RUNTIME_POLICY.allowed_types_by_surface
    )
    assert KNOWN_TRIGGER_MODES == set(
        DEFAULT_DEPLOYMENT_RUNTIME_POLICY.surface_by_trigger_mode
    )
