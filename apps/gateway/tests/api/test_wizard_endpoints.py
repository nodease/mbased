import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from apps.gateway.auth.dependencies import get_current_user
from apps.gateway.main import app
from apps.gateway.services.llm_service import (
    LLMCredentialNotAvailableError,
    LLMService,
)
from apps.shared.db.session import get_db


class _FakeWizardClient:
    async def invoke(self, *args, **kwargs):
        return {
            "choices": [
                {
                    "message": {
                        "content": 'def main(inputs):\n    return {"result": "ok"}'
                    }
                }
            ]
        }


@pytest.fixture
def gateway_client():
    user = SimpleNamespace(id=uuid.uuid4())
    app.dependency_overrides[get_db] = lambda: SimpleNamespace()
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        yield TestClient(app), user
    finally:
        app.dependency_overrides = {}


CHECK_CREDENTIAL_ROUTES = [
    "/api/v1/prompt-wizard/check-credentials",
    "/api/v1/code-wizard/check-credentials",
    "/api/v1/template-wizard/check-credentials",
]

POST_WIZARD_CASES = [
    (
        "/api/v1/prompt-wizard/improve",
        {"prompt_type": "user", "original_prompt": "Hello"},
        "improved_prompt",
        "prompt_wizard",
    ),
    (
        "/api/v1/code-wizard/generate",
        {"description": "Return ok", "input_variables": []},
        "generated_code",
        "code_wizard",
    ),
    (
        "/api/v1/template-wizard/improve",
        {
            "template_type": "email",
            "original_template": "Hello {{ user_name }}",
            "registered_variables": ["user_name"],
        },
        "improved_template",
        "template_wizard",
    ),
]


@pytest.mark.parametrize("path", CHECK_CREDENTIAL_ROUTES)
def test_wizard_check_credentials_passes_query_organization_id(
    monkeypatch,
    gateway_client,
    path,
):
    client, user = gateway_client
    organization_id = uuid.uuid4()
    seen = {}

    def has_runtime(db, user_id, provider_model_map, organization_id=None):
        seen["user_id"] = user_id
        seen["organization_id"] = organization_id
        return True

    monkeypatch.setattr(
        LLMService,
        "has_wizard_runtime_credential",
        staticmethod(has_runtime),
    )

    response = client.get(f"{path}?organization_id={organization_id}")

    assert response.status_code == 200
    assert response.json() == {"has_credentials": True}
    assert seen == {"user_id": user.id, "organization_id": organization_id}


@pytest.mark.parametrize("path", CHECK_CREDENTIAL_ROUTES)
def test_wizard_check_credentials_returns_false_when_runtime_unavailable(
    monkeypatch,
    gateway_client,
    path,
):
    client, _user = gateway_client

    monkeypatch.setattr(
        LLMService,
        "has_wizard_runtime_credential",
        staticmethod(lambda *args, **kwargs: False),
    )

    response = client.get(path)

    assert response.status_code == 200
    assert response.json() == {"has_credentials": False}


@pytest.mark.parametrize("path,payload,response_key,expected_surface", POST_WIZARD_CASES)
def test_wizard_post_passes_body_organization_id(
    monkeypatch,
    gateway_client,
    path,
    payload,
    response_key,
    expected_surface,
):
    client, user = gateway_client
    organization_id = uuid.uuid4()
    seen = {}

    def get_runtime(
        db,
        user_id,
        provider_model_map,
        *,
        organization_id=None,
        runtime_surface="wizard",
        audit_on_failure=True,
    ):
        seen["user_id"] = user_id
        seen["organization_id"] = organization_id
        seen["runtime_surface"] = runtime_surface
        return SimpleNamespace(client=_FakeWizardClient())

    monkeypatch.setattr(
        LLMService,
        "get_wizard_client_for_user",
        staticmethod(get_runtime),
    )

    response = client.post(path, json={**payload, "organization_id": str(organization_id)})

    assert response.status_code == 200
    assert response_key in response.json()
    assert seen["user_id"] == user.id
    assert seen["organization_id"] == organization_id
    assert seen["runtime_surface"] == expected_surface


@pytest.mark.parametrize("path,payload,_response_key,_expected_surface", POST_WIZARD_CASES)
def test_wizard_post_runtime_failure_keeps_credentials_required_response(
    monkeypatch,
    gateway_client,
    path,
    payload,
    _response_key,
    _expected_surface,
):
    client, _user = gateway_client

    def get_runtime(*args, **kwargs):
        raise LLMCredentialNotAvailableError(
            "credential_use_denied",
            "LLM credential use 권한이 필요합니다.",
        )

    monkeypatch.setattr(
        LLMService,
        "get_wizard_client_for_user",
        staticmethod(get_runtime),
    )

    response = client.post(path, json=payload)

    assert response.status_code == 400
    assert response.json()["detail"] == {
        "message": "LLM credential use 권한이 필요합니다.",
        "credentials_required": True,
        "reason": "credential_use_denied",
    }
