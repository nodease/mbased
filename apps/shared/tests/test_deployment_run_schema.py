from uuid import uuid4

import pytest
from apps.shared.schemas.deployment import AuthenticatedDeploymentRunRequest
from pydantic import ValidationError


def test_authenticated_deployment_run_request_separates_conversation_control():
    client_id = uuid4()

    request = AuthenticatedDeploymentRunRequest(
        inputs={
            "question": "hello",
            "conversation_id": "business-value",
            "memory_mode": "business-value",
        },
        conversation={"client_id": client_id},
    )

    assert request.inputs["conversation_id"] == "business-value"
    assert request.inputs["memory_mode"] == "business-value"
    assert request.conversation is not None
    assert request.conversation.client_id == client_id


@pytest.mark.parametrize(
    "conversation",
    [
        {"client_id": "not-a-uuid"},
        {"client_id": str(uuid4()), "unexpected": True},
        {},
    ],
)
def test_authenticated_deployment_run_request_rejects_invalid_conversation_control(
    conversation,
):
    with pytest.raises(ValidationError):
        AuthenticatedDeploymentRunRequest(
            inputs={},
            conversation=conversation,
        )


def test_authenticated_deployment_run_request_rejects_unknown_top_level_field():
    with pytest.raises(ValidationError):
        AuthenticatedDeploymentRunRequest.model_validate(
            {"inputs": {}, "unexpected": True}
        )
