import httpx
import pytest
from apps.shared.services.llm_client.base import ProviderInvocationError
from apps.shared.services.llm_client.openai_client import OpenAIClient


def test_responses_http_error_exposes_safe_machine_readable_details():
    client = OpenAIClient(
        model_id="gpt-5.4",
        credentials={"apiKey": "test-key", "baseUrl": "https://example.test/v1"},
    )
    response = httpx.Response(
        400,
        json={
            "error": {
                "message": "The supplied parameter is not supported.",
                "type": "invalid_request_error",
                "param": "reasoning.effort",
                "code": "unsupported_parameter",
            }
        },
    )

    with pytest.raises(ProviderInvocationError) as exc_info:
        client._parse_responses_http_response(response)  # noqa: SLF001

    error = exc_info.value
    assert error.reason_code == "provider_http_error"
    assert error.status_code == 400
    assert error.provider_error_code == "unsupported_parameter"
    assert error.provider_error_param == "reasoning.effort"


def test_responses_incomplete_exposes_status_without_response_content():
    client = OpenAIClient(
        model_id="gpt-5.4",
        credentials={"apiKey": "test-key", "baseUrl": "https://example.test/v1"},
    )

    with pytest.raises(ProviderInvocationError) as exc_info:
        client._convert_responses_response(  # noqa: SLF001
            {"status": "incomplete", "output": []}
        )

    error = exc_info.value
    assert error.reason_code == "responses_incomplete"
    assert error.provider_response_status == "incomplete"
