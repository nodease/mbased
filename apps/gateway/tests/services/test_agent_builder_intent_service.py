import json
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from apps.gateway.application.agent_builder.intent_usage import (
    AgentBuilderIntentUsageContext,
    AgentBuilderIntentUsageRecordingError,
    AgentBuilderIntentUsageReservation,
)
from apps.gateway.services.agent_builder_intent_service import (
    AgentBuilderIntentExtraction,
    AgentBuilderIntentExtractionError,
    AgentBuilderIntentRuntimeUnavailableError,
    AgentBuilderSemanticEdit,
    LLMAgentBuilderIntentExtractor,
    _safe_knowledge_candidate_context,
    agent_builder_capability_guide,
    safe_intent_extraction_reason,
)
from apps.gateway.services.agent_builder.intent_usage_service import (
    AgentBuilderIntentUsageService,
)
from apps.gateway.services.agent_builder_service import AgentBuilderService
from apps.gateway.services.llm_service import LLMCredentialNotAvailableError
from apps.shared.schemas.agent_builder import AgentBuilderMessageRequest
from apps.shared.services.llm_client.base import LLMResponseValidationError
from apps.shared.services.llm_client.google_client import GoogleClient
from apps.shared.services.llm_client.openai_client import OpenAIClient
from apps.shared.services.workflow_node_catalog import (
    agent_builder_supported_capabilities,
)


class FakeDb:
    pass


class FakeLLMClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def invoke_sync(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        content = self.payload if isinstance(self.payload, str) else json.dumps(self.payload)
        return {"choices": [{"message": {"content": content}}]}


class SequenceFakeLLMClient:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def invoke_sync(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        payload = self.payloads.pop(0)
        return {"choices": [{"message": {"content": json.dumps(payload)}}]}


class SchemaConstrainedFakeLLMClient(FakeLLMClient):
    def build_json_schema_response_format(self, *, name, schema):
        return {
            "type": "json_schema",
            "name": name,
            "schema": schema,
            "strict": True,
        }


class ProviderFailingFakeLLMClient:
    def invoke_sync(self, _messages, **_kwargs):
        raise ValueError("provider raw failure must not escape")


class UsageResponseValidationFailingFakeLLMClient:
    def __init__(self):
        self.calls = 0

    def invoke_sync(self, _messages, **_kwargs):
        self.calls += 1
        raise LLMResponseValidationError(
            "safe provider response validation failure",
            usage={
                "input_tokens": 12,
                "output_tokens": 3,
                "provider_detail": "must-not-cross-boundary",
            },
        )


class UsageSequenceFakeLLMClient:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def invoke_sync(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        payload = self.payloads.pop(0)
        attempt = len(self.calls)
        return {
            "choices": [{"message": {"content": json.dumps(payload)}}],
            "usage": {
                "prompt_tokens": attempt * 10,
                "completion_tokens": attempt * 2,
            },
        }


class CapturingUsageRecorder:
    def __init__(self, error=None, *, reserve_error=None, cancel_error=None):
        self.reservations = []
        self.calls = []
        self.canceled = []
        self.error = error
        self.reserve_error = reserve_error
        self.cancel_error = cancel_error

    def reserve(
        self,
        context,
        *,
        credential_id,
        model_id,
        model_api_id,
        attempt,
    ):
        if self.reserve_error is not None:
            raise self.reserve_error
        reservation = AgentBuilderIntentUsageReservation(
            id=uuid.uuid4(),
            context=context,
            credential_id=credential_id,
            model_id=model_id,
            model_api_id=model_api_id,
            attempt=attempt,
            input_price_1k=Decimal("0.001"),
            output_price_1k=Decimal("0.002"),
        )
        self.reservations.append(reservation)
        return reservation

    def record(self, reservation, sample):
        self.calls.append((reservation.context, sample))
        if self.error is not None:
            raise self.error
        return uuid.uuid4()

    def cancel(self, reservation):
        self.canceled.append(reservation)
        if self.cancel_error is not None:
            raise self.cancel_error


def test_intent_usage_pricing_rejects_negative_model_price():
    model = SimpleNamespace(
        input_price_1k=Decimal("-0.001"),
        output_price_1k=Decimal("0.002"),
    )

    with pytest.raises(AgentBuilderIntentUsageRecordingError):
        AgentBuilderIntentUsageService._prices_for_model(
            model,
            "intent-usage-model",
        )


def _google_client_with_response(monkeypatch, response_payload):
    calls = []

    class MockResponse:
        status_code = 200
        text = ""

        def json(self):
            return response_payload

    class MockAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, url, *, headers, json):
            calls.append({"url": url, "headers": headers, "json": json})
            return MockResponse()

    monkeypatch.setattr(
        "apps.shared.services.llm_client.google_client.httpx.AsyncClient",
        lambda **_kwargs: MockAsyncClient(),
    )
    return (
        GoogleClient(
            model_id="models/gemini-test",
            credentials={
                "apiKey": object(),
                "baseUrl": "https://google.invalid/v1beta/openai",
            },
        ),
        calls,
    )


class FakeIntentExtractor:
    def __init__(self, extraction):
        self.extraction = extraction
        self.calls = []

    def extract(self, *, safe_message, workflow_context):
        self.calls.append(
            {
                "safe_message": safe_message,
                "workflow_context": workflow_context,
            }
        )
        return self.extraction


class FailingIntentExtractor:
    def extract(self, *, safe_message, workflow_context):
        raise AgentBuilderIntentExtractionError("intent extraction failed")


def test_intent_failure_reason_exposes_only_allowlisted_diagnostic_codes():
    semantic = AgentBuilderIntentExtractionError(
        "Agent Builder intent semantic validation failed: "
        "KNOWLEDGE_PLACEMENT_REQUIRED,UNKNOWN_KNOWLEDGE_CANDIDATE_HANDLE"
    )
    unsafe = AgentBuilderIntentExtractionError(
        "provider failed with secret-value and raw payload"
    )

    assert safe_intent_extraction_reason(semantic) == (
        "semantic_validation_failed:KNOWLEDGE_PLACEMENT_REQUIRED,"
        "UNKNOWN_KNOWLEDGE_CANDIDATE_HANDLE"
    )
    assert safe_intent_extraction_reason(unsafe) == "extraction_failed"
    assert "secret-value" not in safe_intent_extraction_reason(unsafe)


def _service(extractor):
    return AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
        intent_extractor=extractor,
    )


def _knowledge_placement():
    return {
        "requirement_id": "kr_1",
        "timing": "after_graph",
        "effect_kind": "binding_only",
        "target_step_id": "step_llm",
    }


def test_llm_intent_extractor_requests_json_and_preserves_step_order():
    client = FakeLLMClient(
        {
            "request_type": "new_workflow",
            "draft_mode": "new_workflow",
            "intent_summary": "웹훅으로 GitHub PR을 받아 검토한 후 댓글을 등록합니다.",
            "ordered_capabilities": [
                "webhook_trigger",
                "github_pr_read",
                "llm",
                "github_pr_comment",
                "answer",
            ],
            "knowledge_required": False,
            "knowledge_topics": [],
            "integration_actions": [
                {
                    "provider": "github",
                    "resource": "pull_request",
                    "operation": "read",
                },
                {
                    "provider": "github",
                    "resource": "pull_request",
                    "operation": "comment",
                },
            ],
            "edit": None,
            "unsupported_requests": [],
        }
    )
    credential_id = uuid.uuid4()
    model_id = uuid.uuid4()
    runtime_calls = []
    extractor = LLMAgentBuilderIntentExtractor(
        db=FakeDb(),
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        credential_id=credential_id,
        model_id=model_id,
        runtime_loader=lambda **kwargs: runtime_calls.append(kwargs)
        or SimpleNamespace(client=client),
    )

    result = extractor.extract(
        safe_message=(
            "새 워크플로우로 웹훅에서 요청을 받고 GitHub PR을 조회한 뒤 "
            "LLM으로 리뷰해서 GitHub PR에 댓글을 등록해줘"
        ),
        workflow_context={"workflow_present": False, "nodes": []},
    )

    assert result.ordered_capabilities == [
        "webhook_trigger",
        "github_pr_read",
        "llm",
        "github_pr_comment",
        "answer",
    ]
    messages, kwargs = client.calls[0]
    assert "json" in str(messages).lower()
    assert kwargs["response_format"]["type"] == "json_object"
    assert kwargs["temperature"] == 0
    assert kwargs["max_tokens"] == 4000
    assert kwargs["request_timeout_seconds"] == 90
    assert runtime_calls[0]["credential_id"] == credential_id
    assert runtime_calls[0]["model_id"] == model_id


def test_llm_intent_extractor_uses_provider_schema_constraint_when_supported():
    client = SchemaConstrainedFakeLLMClient(
        {
            "request_type": "new_workflow",
            "draft_mode": "new_workflow",
            "intent_summary": "웹훅 사내 문서 챗봇 workflow 생성",
            "ordered_capabilities": [
                "webhook_trigger",
                "knowledge_backed_llm",
                "answer",
            ],
            "knowledge_required": True,
            "knowledge_topics": ["사내 문서"],
            "knowledge_placements": [_knowledge_placement()],
            "edit": None,
        }
    )
    extractor = LLMAgentBuilderIntentExtractor(
        db=FakeDb(),
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        runtime_loader=lambda **_kwargs: SimpleNamespace(client=client),
    )

    result = extractor.extract(
        safe_message="웹훅으로 받는 사내 문서 챗봇 워크플로우를 만들어줘",
        workflow_context={"workflow_present": False, "nodes": []},
    )

    response_format = client.calls[0][1]["response_format"]
    assert result.request_type == "new_workflow"
    assert response_format["type"] == "json_schema"
    assert response_format["name"] == "agent_builder_intent"
    assert response_format["strict"] is True
    assert response_format["schema"]["additionalProperties"] is False
    assert set(response_format["schema"]["properties"]) == {
        "request_type",
        "draft_mode",
            "intent_summary",
            "ordered_capabilities",
            "requested_capabilities",
            "parameter_guidance_hints",
        "explicit_parameter_values",
        "knowledge_required",
        "knowledge_topics",
        "knowledge_candidate_handles",
        "knowledge_placements",
        "integration_actions",
        "edit",
        "unsupported_requests",
    }


def test_agent_builder_explicit_value_schema_is_valid_for_openai_strict_output():
    client = OpenAIClient(
        model_id="gpt-5.4",
        credentials={"apiKey": "sk-test", "baseUrl": "https://api.openai.com/v1"},
    )

    response_format = client.build_json_schema_response_format(
        name="agent_builder_intent",
        schema=AgentBuilderIntentExtraction.model_json_schema(),
    )
    explicit_value_schema = response_format["schema"]["$defs"][
        "AgentBuilderExplicitParameterValue"
    ]["properties"]["value"]

    assert explicit_value_schema["anyOf"]
    assert all("type" in branch for branch in explicit_value_schema["anyOf"])


def test_llm_intent_extractor_classifies_provider_failure_without_raw_details():
    extractor = LLMAgentBuilderIntentExtractor(
        db=FakeDb(),
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        runtime_loader=lambda **_kwargs: SimpleNamespace(
            client=ProviderFailingFakeLLMClient()
        ),
    )

    with pytest.raises(AgentBuilderIntentExtractionError) as captured:
        extractor.extract(
            safe_message="입력과 응답 workflow를 만들어줘",
            workflow_context={"workflow_present": False, "nodes": []},
        )

    assert safe_intent_extraction_reason(captured.value) == "provider_call_failed"
    assert "provider raw failure" not in str(captured.value)


def test_llm_intent_extractor_returns_only_catalog_valid_safe_parameter_hints():
    client = FakeLLMClient(
        {
            "request_type": "new_workflow",
            "draft_mode": "new_workflow",
            "intent_summary": "입력을 Slack으로 보냅니다.",
            "ordered_capabilities": ["start_input", "slack_send", "answer"],
            "parameter_guidance_hints": [
                {
                    "step_id": "step_slack",
                    "parameter_key": "channel",
                    "reason": "메시지 전달 위치가 필요합니다.",
                    "input_guidance": "Slack channel ID를 선택하세요.",
                },
                {
                    "step_id": "step_slack",
                    "parameter_key": "unknown",
                    "reason": "알 수 없는 값입니다.",
                    "input_guidance": "값을 입력하세요.",
                },
                {
                    "step_id": "step_slack",
                    "parameter_key": "credential",
                    "reason": "token=secret-value를 사용합니다.",
                    "input_guidance": "credential을 입력하세요.",
                },
            ],
        }
    )
    extractor = LLMAgentBuilderIntentExtractor(
        db=FakeDb(),
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        runtime_loader=lambda **_kwargs: SimpleNamespace(client=client),
    )

    result = extractor.extract(
        safe_message="입력을 Slack으로 보내는 workflow를 만들어줘",
        workflow_context={"workflow_present": False, "nodes": []},
    )

    assert [hint.parameter_key for hint in result.parameter_guidance_hints] == [
        "channel"
    ]
    assert "PARAMETER_GUIDE" in str(client.calls[0][0])


def test_llm_intent_extractor_returns_only_catalog_valid_safe_explicit_values():
    client = FakeLLMClient(
        {
            "request_type": "new_workflow",
            "draft_mode": "new_workflow",
            "intent_summary": "입력을 Slack 채널 C123으로 보냅니다.",
            "ordered_capabilities": ["start_input", "slack_send", "answer"],
            "explicit_parameter_values": [
                {
                    "step_id": "step_slack",
                    "parameter_key": "channel",
                    "value": "C123",
                },
                {
                    "step_id": "step_slack",
                    "parameter_key": "credential",
                    "value": "credential-must-not-be-stored",
                },
                {
                    "step_id": "step_slack",
                    "parameter_key": "unknown",
                    "value": "ignored",
                },
            ],
        }
    )
    extractor = LLMAgentBuilderIntentExtractor(
        db=FakeDb(),
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        runtime_loader=lambda **_kwargs: SimpleNamespace(client=client),
    )

    result = extractor.extract(
        safe_message="입력을 Slack 채널 C123으로 보내는 workflow를 만들어줘",
        workflow_context={"workflow_present": False, "nodes": []},
    )

    assert [
        (item.step_id, item.parameter_key, item.value)
        for item in result.explicit_parameter_values
    ] == [("step_slack", "channel", "C123")]
    assert "credential-must-not-be-stored" not in result.model_dump_json()


def test_service_propagates_validated_explicit_parameter_value_to_structured_request():
    extractor = FakeIntentExtractor(
        AgentBuilderIntentExtraction(
            request_type="new_workflow",
            draft_mode="new_workflow",
            intent_summary="입력을 Slack 채널 C123으로 보냅니다.",
            ordered_capabilities=["start_input", "slack_send", "answer"],
            explicit_parameter_values=[
                {
                    "step_id": "step_slack",
                    "parameter_key": "channel",
                    "value": "C123",
                }
            ],
        )
    )

    structured = _service(extractor)._structure_request(  # noqa: SLF001
        AgentBuilderMessageRequest(
            message="입력을 Slack 채널 C123으로 보내는 workflow를 만들어줘"
        ),
        None,
    )

    assert structured.explicit_parameter_values[0].parameter_key == "channel"
    assert structured.explicit_parameter_values[0].value == "C123"


def test_service_propagates_validated_parameter_hint_to_structured_request():
    extractor = FakeIntentExtractor(
        AgentBuilderIntentExtraction(
            request_type="new_workflow",
            draft_mode="new_workflow",
            intent_summary="입력을 Slack으로 보냅니다.",
            ordered_capabilities=["start_input", "slack_send", "answer"],
            parameter_guidance_hints=[
                {
                    "step_id": "step_slack",
                    "parameter_key": "channel",
                    "reason": "메시지 전달 위치가 필요합니다.",
                    "input_guidance": "Slack channel ID를 선택하세요.",
                }
            ],
        )
    )

    structured = _service(extractor)._structure_request(  # noqa: SLF001
        AgentBuilderMessageRequest(message="입력을 Slack으로 보내줘"),
        None,
    )

    assert structured.parameter_guidance_hints[0].parameter_key == "channel"


def test_service_rejects_github_pr_create_instead_of_silently_using_http():
    extractor = FakeIntentExtractor(
        AgentBuilderIntentExtraction(
            request_type="modify_workflow",
            draft_mode="modify_workflow",
            intent_summary="LLM 뒤에 GitHub Pull Request를 생성합니다.",
            ordered_capabilities=["http_request"],
            integration_actions=[
                {
                    "provider": "github",
                    "resource": "pull_request",
                    "operation": "create",
                }
            ],
            edit=AgentBuilderSemanticEdit(
                placement="after",
                target_reference_type="natural_language_node",
                target_query="LLM",
                target_capabilities=["llm"],
            ),
        )
    )
    svc = _service(extractor)

    structured = svc._structure_request(  # noqa: SLF001
        AgentBuilderMessageRequest(
            message="LLM 뒤에 깃허브로 PR을 올리는 로직을 추가해줘"
        ),
        workflow=SimpleNamespace(
            id=uuid.uuid4(),
            graph={
                "nodes": [
                    {"id": "llm", "type": "llmNode", "data": {"title": "LLM"}},
                    {
                        "id": "answer",
                        "type": "answerNode",
                        "data": {"title": "응답"},
                    },
                ],
                "edges": [
                    {"id": "edge-llm-answer", "source": "llm", "target": "answer"}
                ],
            },
        ),
    )

    assert structured.request_type == "unsupported"
    assert "http_request" not in structured.required_capabilities
    assert structured.unsupported_requests == [
        "GitHub Pull Request 생성은 현재 지원되지 않습니다."
    ]


def test_llm_intent_extractor_repairs_github_comment_mapped_to_http_once():
    base = {
        "request_type": "modify_workflow",
        "draft_mode": "modify_workflow",
        "intent_summary": "LLM 뒤에 GitHub PR 댓글 등록을 추가합니다.",
        "knowledge_required": False,
        "knowledge_topics": [],
        "integration_actions": [
            {
                "provider": "github",
                "resource": "pull_request",
                "operation": "comment",
            }
        ],
        "edit": {
            "placement": "after",
            "target_reference_type": "natural_language_node",
            "target_query": "LLM",
            "target_capabilities": ["llm"],
        },
        "unsupported_requests": [],
    }
    client = SequenceFakeLLMClient(
        [
            {**base, "ordered_capabilities": ["http_request"]},
            {**base, "ordered_capabilities": ["github_pr_comment"]},
        ]
    )
    extractor = LLMAgentBuilderIntentExtractor(
        db=FakeDb(),
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        runtime_loader=lambda **_kwargs: SimpleNamespace(client=client),
    )

    result = extractor.extract(
        safe_message="LLM 뒤에 GitHub PR에 리뷰 댓글을 올리는 노드를 추가해줘",
        workflow_context={
            "workflow_present": True,
            "selected_node_present": False,
            "selected_edge_present": False,
            "nodes": [{"type": "llmNode", "title": "LLM"}],
        },
    )

    assert result.ordered_capabilities == ["github_pr_comment"]
    assert len(client.calls) == 2
    assert "GITHUB_OPERATION_CAPABILITY_MISMATCH" in str(client.calls[1][0])


@pytest.mark.parametrize(
    "message",
    [
        "기존 LLM 노드 뒤에 GitHub PR 생성 노드를 삽입",
        "기존 LLM 노드 뒤에 깃허브 PR 생성 노드를 삽입",
    ],
)
def test_llm_intent_extractor_repairs_missing_github_pr_create_action_once(
    message,
):
    base = {
        "request_type": "modify_workflow",
        "draft_mode": "modify_workflow",
        "intent_summary": "기존 LLM 뒤에 GitHub PR 생성 단계를 삽입합니다.",
        "knowledge_required": False,
        "knowledge_topics": [],
        "knowledge_candidate_handles": [],
        "edit": {
            "placement": "after",
            "target_reference_type": "natural_language_node",
            "target_query": "LLM",
            "target_capabilities": ["llm"],
        },
        "unsupported_requests": [],
    }
    client = SequenceFakeLLMClient(
        [
            {
                **base,
                "ordered_capabilities": ["http_request"],
                "integration_actions": [],
            },
            {
                **base,
                "ordered_capabilities": [],
                "integration_actions": [
                    {
                        "provider": "github",
                        "resource": "pull_request",
                        "operation": "create",
                    }
                ],
            },
        ]
    )
    extractor = LLMAgentBuilderIntentExtractor(
        db=FakeDb(),
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        runtime_loader=lambda **_kwargs: SimpleNamespace(client=client),
    )

    result = extractor.extract(
        safe_message=message,
        workflow_context={
            "workflow_present": True,
            "selected_node_present": False,
            "selected_edge_present": False,
            "nodes": [{"type": "llmNode", "title": "LLM"}],
        },
    )

    assert result.integration_actions[0].operation == "create"
    assert len(client.calls) == 2
    assert "GITHUB_INTEGRATION_ACTION_REQUIRED" in str(client.calls[1][0])

    structured = _service(FakeIntentExtractor(result))._structure_request(  # noqa: SLF001
        AgentBuilderMessageRequest(message=message),
        workflow=SimpleNamespace(
            id=uuid.uuid4(),
            graph={
                "nodes": [
                    {"id": "llm", "type": "llmNode", "data": {"title": "LLM"}},
                    {
                        "id": "answer",
                        "type": "answerNode",
                        "data": {"title": "응답"},
                    },
                ],
                "edges": [
                    {"id": "edge-llm-answer", "source": "llm", "target": "answer"}
                ],
            },
        ),
    )
    assert structured.request_type == "unsupported"
    assert structured.required_capabilities == []
    assert structured.unsupported_requests == [
        "GitHub Pull Request 생성은 현재 지원되지 않습니다."
    ]


def test_llm_intent_extractor_repairs_missing_github_pr_read_action_once():
    base = {
        "request_type": "modify_workflow",
        "draft_mode": "modify_workflow",
        "intent_summary": "기존 LLM 뒤에 GitHub PR 조회 단계를 삽입합니다.",
        "knowledge_required": False,
        "knowledge_topics": [],
        "knowledge_candidate_handles": [],
        "edit": {
            "placement": "after",
            "target_reference_type": "natural_language_node",
            "target_query": "LLM",
            "target_capabilities": ["llm"],
        },
        "unsupported_requests": [],
    }
    client = SequenceFakeLLMClient(
        [
            {
                **base,
                "ordered_capabilities": ["http_request"],
                "integration_actions": [],
            },
            {
                **base,
                "ordered_capabilities": ["github_pr_read"],
                "integration_actions": [
                    {
                        "provider": "github",
                        "resource": "pull_request",
                        "operation": "read",
                    }
                ],
            },
        ]
    )
    extractor = LLMAgentBuilderIntentExtractor(
        db=FakeDb(),
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        runtime_loader=lambda **_kwargs: SimpleNamespace(client=client),
    )

    result = extractor.extract(
        safe_message="기존 LLM 노드 뒤에 GitHub PR 조회 노드를 삽입",
        workflow_context={
            "workflow_present": True,
            "selected_node_present": False,
            "selected_edge_present": False,
            "nodes": [{"type": "llmNode", "title": "LLM"}],
        },
    )

    assert result.ordered_capabilities == ["github_pr_read"]
    assert len(client.calls) == 2
    assert "GITHUB_INTEGRATION_ACTION_REQUIRED" in str(client.calls[1][0])


@pytest.mark.parametrize(
    "message",
    [
        "기존 LLM 노드 뒤에 REST API 호출 노드를 삽입",
        "기존 LLM 노드 뒤에 GitHub API proxy 호출 노드를 삽입",
    ],
)
def test_llm_intent_extractor_does_not_repair_generic_http_request(message):
    client = FakeLLMClient(
        {
            "request_type": "modify_workflow",
            "draft_mode": "modify_workflow",
            "intent_summary": "기존 LLM 뒤에 REST API 호출을 삽입합니다.",
            "ordered_capabilities": ["http_request"],
            "knowledge_required": False,
            "knowledge_topics": [],
            "knowledge_candidate_handles": [],
            "integration_actions": [],
            "edit": {
                "placement": "after",
                "target_reference_type": "natural_language_node",
                "target_query": "LLM",
                "target_capabilities": ["llm"],
            },
            "unsupported_requests": [],
        }
    )
    extractor = LLMAgentBuilderIntentExtractor(
        db=FakeDb(),
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        runtime_loader=lambda **_kwargs: SimpleNamespace(client=client),
    )

    result = extractor.extract(
        safe_message=message,
        workflow_context={
            "workflow_present": True,
            "selected_node_present": False,
            "selected_edge_present": False,
            "nodes": [{"type": "llmNode", "title": "LLM"}],
        },
    )

    assert result.ordered_capabilities == ["http_request"]
    assert len(client.calls) == 1


def test_intent_capability_guide_is_catalog_derived_and_excludes_loop():
    guide = agent_builder_capability_guide()

    assert set(guide) == agent_builder_supported_capabilities()
    assert "loop" not in guide
    assert "webhook_trigger" in guide
    assert "knowledge_backed_llm" in guide
    assert guide["slack_send"]["standalone_creation"] == "allowed"
    assert "슬랙" in guide["slack_send"]["planner_aliases"]


def test_llm_intent_extractor_passes_only_bounded_safe_kb_context():
    raw_kb_id = str(uuid.uuid4())
    context_calls = []

    def load_context(**kwargs):
        context_calls.append(kwargs)
        return [
            {
                "candidate_handle": "rec-safe-1",
                "safe_label": "사내 문서",
                "safe_topics": ["사내 문서", "온보딩"],
                "safe_description": "사내 정책과 절차",
                "runtime_availability": "available",
                "relevance_score": 0.82,
            }
        ]

    client = FakeLLMClient(
        {
            "request_type": "new_workflow",
            "draft_mode": "new_workflow",
            "intent_summary": "사내 문서 챗봇 workflow 생성",
            "ordered_capabilities": [
                "webhook_trigger",
                "knowledge_backed_llm",
                "answer",
            ],
            "knowledge_required": True,
            "knowledge_topics": ["사내 문서"],
            "knowledge_candidate_handles": ["rec-safe-1"],
            "knowledge_placements": [_knowledge_placement()],
            "edit": None,
        }
    )
    extractor = LLMAgentBuilderIntentExtractor(
        db=FakeDb(),
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        runtime_loader=lambda **_kwargs: SimpleNamespace(client=client),
        knowledge_context_loader=load_context,
    )

    result = extractor.extract(
        safe_message="웹훅으로 받는 사내 문서 챗봇 워크플로우를 만들어줘",
        workflow_context={"workflow_present": False, "nodes": []},
    )

    assert context_calls[0]["max_candidates"] == 20
    prompt = str(client.calls[0][0])
    assert "rec-safe-1" in prompt
    assert "사내 문서" in prompt
    assert "온보딩" in prompt
    assert "사내 정책과 절차" in prompt
    assert raw_kb_id not in prompt
    assert "raw_source_path" not in prompt
    assert result.knowledge_candidate_handles == ["rec-safe-1"]


def test_intent_prompt_boundary_excludes_zero_relevance_kb_context():
    assert _safe_knowledge_candidate_context(
        [
            {
                "candidate_handle": "rec-unrelated",
                "safe_label": "Unrelated policy",
                "safe_topics": ["finance"],
                "runtime_availability": "available",
                "relevance_score": 0.0,
            }
        ]
    ) == []


def test_llm_intent_extractor_repairs_unknown_kb_candidate_handle_once():
    invalid = {
        "request_type": "new_workflow",
        "draft_mode": "new_workflow",
        "intent_summary": "사내 문서 챗봇 workflow 생성",
        "ordered_capabilities": ["start_input", "knowledge_backed_llm", "answer"],
        "knowledge_required": True,
        "knowledge_topics": ["사내 문서"],
        "knowledge_candidate_handles": ["rec-not-issued"],
        "knowledge_placements": [_knowledge_placement()],
        "edit": None,
    }
    valid = {**invalid, "knowledge_candidate_handles": ["rec-safe-1"]}
    client = SequenceFakeLLMClient([invalid, valid])
    extractor = LLMAgentBuilderIntentExtractor(
        db=FakeDb(),
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        runtime_loader=lambda **_kwargs: SimpleNamespace(client=client),
        knowledge_context_loader=lambda **_kwargs: [
            {
                "candidate_handle": "rec-safe-1",
                "safe_label": "사내 문서",
                "safe_topics": ["사내 문서"],
                "runtime_availability": "available",
                "relevance_score": 0.8,
            }
        ],
    )

    result = extractor.extract(
        safe_message="사내 문서로 답변하는 workflow를 만들어줘",
        workflow_context={"workflow_present": False, "nodes": []},
    )

    assert result.knowledge_candidate_handles == ["rec-safe-1"]
    assert len(client.calls) == 2
    assert "UNKNOWN_KNOWLEDGE_CANDIDATE_HANDLE" in str(client.calls[1][0])


def test_llm_intent_extractor_repairs_semantically_invalid_result_once():
    client = SequenceFakeLLMClient(
        [
            {
                "request_type": "modify_workflow",
                "draft_mode": "modify_workflow",
                "intent_summary": "provider-only-invalid-summary-marker",
                "ordered_capabilities": [],
                "edit": None,
            },
            {
                "request_type": "new_workflow",
                "draft_mode": "new_workflow",
                "intent_summary": "사내 문서 챗봇 workflow 생성",
                "ordered_capabilities": [
                    "webhook_trigger",
                    "knowledge_backed_llm",
                    "answer",
                ],
                "knowledge_required": True,
                "knowledge_topics": ["사내 문서"],
                "knowledge_placements": [_knowledge_placement()],
                "edit": None,
            },
        ]
    )
    extractor = LLMAgentBuilderIntentExtractor(
        db=FakeDb(),
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        runtime_loader=lambda **_kwargs: SimpleNamespace(client=client),
    )

    result = extractor.extract(
        safe_message="웹훅으로 받는 사내 문서 챗봇 워크플로우를 만들어줘",
        workflow_context={"workflow_present": True, "nodes": []},
    )

    assert result.request_type == "new_workflow"
    assert len(client.calls) == 2
    repair_messages = client.calls[1][0]
    assert len(repair_messages) == 2
    assert "MODIFY_EDIT_REQUIRED" in str(repair_messages)
    assert "MODIFY_CAPABILITY_REQUIRED" in str(repair_messages)
    assert "웹훅으로 받는 사내 문서 챗봇 워크플로우를 만들어줘" in str(
        repair_messages
    )
    assert "explicit existing target" in str(repair_messages)
    assert "direct object is a workflow" in str(repair_messages)
    assert "provider-only-invalid-summary-marker" not in str(repair_messages)


def test_llm_intent_extractor_repairs_reasonless_unsupported_simple_node_flow_once():
    client = SequenceFakeLLMClient(
        [
            {
                "request_type": "unsupported",
                "draft_mode": "new_workflow",
                "intent_summary": "입력 응답 노드 요청",
                "ordered_capabilities": [],
                "unsupported_requests": [],
                "edit": None,
            },
            {
                "request_type": "new_workflow",
                "draft_mode": "new_workflow",
                "intent_summary": "입력을 받아 응답하는 workflow 생성",
                "ordered_capabilities": ["start_input", "answer"],
                "unsupported_requests": [],
                "edit": None,
            },
        ]
    )
    extractor = LLMAgentBuilderIntentExtractor(
        db=FakeDb(),
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        runtime_loader=lambda **_kwargs: SimpleNamespace(client=client),
    )

    result = extractor.extract(
        safe_message="입력 응답 노드를 만들어줘",
        workflow_context={"workflow_present": True, "nodes": []},
    )

    assert result.request_type == "new_workflow"
    assert result.ordered_capabilities == ["start_input", "answer"]
    assert len(client.calls) == 2
    assert "UNSUPPORTED_SUPPORTED_FLOW_CONTRADICTION" in str(client.calls[1][0])
    assert "입력 응답 노드를 만들어줘" in client.calls[0][0][0]["content"]


def test_llm_intent_extractor_repairs_reasoned_unsupported_start_answer_flow_once():
    client = SequenceFakeLLMClient(
        [
            {
                "request_type": "unsupported",
                "draft_mode": "new_workflow",
                "intent_summary": "입력과 출력을 연결할 수 없습니다.",
                "ordered_capabilities": [],
                "unsupported_requests": ["현재 요청을 지원하지 않습니다."],
                "edit": None,
            },
            {
                "request_type": "new_workflow",
                "draft_mode": "new_workflow",
                "intent_summary": "입력과 응답 노드를 생성합니다.",
                "ordered_capabilities": ["start_input", "answer"],
                "unsupported_requests": [],
                "edit": None,
            },
        ]
    )
    extractor = LLMAgentBuilderIntentExtractor(
        db=FakeDb(),
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        runtime_loader=lambda **_kwargs: SimpleNamespace(client=client),
    )

    result = extractor.extract(
        safe_message="입력 출력 노드 생성해줘",
        workflow_context={"workflow_present": True, "nodes": []},
    )

    assert result.request_type == "new_workflow"
    assert result.ordered_capabilities == ["start_input", "answer"]
    assert len(client.calls) == 2
    assert "UNSUPPORTED_SUPPORTED_FLOW_CONTRADICTION" in str(client.calls[1][0])


def test_llm_intent_extractor_repairs_unplaced_start_answer_flow_misclassified_as_modify():
    client = SequenceFakeLLMClient(
        [
            {
                "request_type": "modify_workflow",
                "draft_mode": "modify_workflow",
                "intent_summary": "기존 입력 노드 뒤에 응답을 연결합니다.",
                "ordered_capabilities": ["start_input", "answer"],
                "edit": {
                    "placement": "after",
                    "target_reference_type": "natural_language_node",
                    "target_query": "입력",
                },
            },
            {
                "request_type": "new_workflow",
                "draft_mode": "new_workflow",
                "intent_summary": "입력과 응답 노드를 생성합니다.",
                "ordered_capabilities": ["start_input", "answer"],
                "edit": None,
            },
        ]
    )
    extractor = LLMAgentBuilderIntentExtractor(
        db=FakeDb(),
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        runtime_loader=lambda **_kwargs: SimpleNamespace(client=client),
    )

    result = extractor.extract(
        safe_message="입력 출력 노드를 만들어줘",
        workflow_context={
            "workflow_present": True,
            "nodes": [
                {
                    "id": "existing-input",
                    "type": "startNode",
                    "capabilities": ["start_input"],
                }
            ],
        },
    )

    assert result.request_type == "new_workflow"
    assert result.draft_mode == "new_workflow"
    assert result.ordered_capabilities == ["start_input", "answer"]
    assert len(client.calls) == 2
    assert "UNPLACED_NODE_CREATION_NEW_WORKFLOW_REQUIRED" in str(
        client.calls[1][0]
    )


def test_llm_intent_extractor_repairs_reasoned_unsupported_single_node_creation_once():
    client = SequenceFakeLLMClient(
        [
            {
                "request_type": "unsupported",
                "draft_mode": "new_workflow",
                "intent_summary": "GitHub node request cannot be classified.",
                "ordered_capabilities": [],
                "requested_capabilities": ["github_pr_read"],
                "unsupported_requests": ["Workflow request is required."],
                "edit": None,
            },
            {
                "request_type": "new_workflow",
                "draft_mode": "new_workflow",
                "intent_summary": "GitHub PR 조회 node workflow를 생성합니다.",
                "ordered_capabilities": ["github_pr_read", "answer"],
                "requested_capabilities": ["github_pr_read"],
                "integration_actions": [
                    {
                        "provider": "github",
                        "resource": "pull_request",
                        "operation": "read",
                    }
                ],
                "unsupported_requests": [],
                "edit": None,
            },
        ]
    )
    extractor = LLMAgentBuilderIntentExtractor(
        db=FakeDb(),
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        runtime_loader=lambda **_kwargs: SimpleNamespace(client=client),
    )

    result = extractor.extract(
        safe_message="깃허브 노드 만들어줘",
        workflow_context={"workflow_present": True, "nodes": []},
    )

    assert result.request_type == "new_workflow"
    assert result.ordered_capabilities == ["github_pr_read", "answer"]
    assert result.integration_actions[0].operation == "read"
    assert len(client.calls) == 2
    repair_request = str(client.calls[1][0])
    assert "UNSUPPORTED_SUPPORTED_NODE_CREATION_CONTRADICTION" in repair_request
    assert "named supported node" in repair_request


@pytest.mark.parametrize(
    ("message", "requested_capability"),
    [
        ("메일 처리 완료 노드를 만들어줘", "mail_terminal_acknowledgement"),
        ("반복 노드를 만들어줘", "loop"),
        ("알 수 없는 노드를 만들어줘", "unknown_node"),
    ],
)
def test_llm_intent_extractor_does_not_repair_non_standalone_node_creation(
    message,
    requested_capability,
):
    client = FakeLLMClient(
        {
            "request_type": "unsupported",
            "draft_mode": "new_workflow",
            "intent_summary": "단독 생성할 수 없는 node 요청입니다.",
            "ordered_capabilities": [],
            "requested_capabilities": [requested_capability],
            "unsupported_requests": ["기존 workflow 문맥이 필요합니다."],
            "edit": None,
        }
    )
    extractor = LLMAgentBuilderIntentExtractor(
        db=FakeDb(),
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        runtime_loader=lambda **_kwargs: SimpleNamespace(client=client),
    )

    result = extractor.extract(
        safe_message=message,
        workflow_context={"workflow_present": True, "nodes": []},
    )

    assert result.request_type == "unsupported"
    assert len(client.calls) == 1


def test_llm_intent_extractor_accepts_recognized_unsupported_action_without_repair():
    client = FakeLLMClient(
        {
            "request_type": "unsupported",
            "draft_mode": "new_workflow",
            "intent_summary": "GitHub PR 생성 요청",
            "ordered_capabilities": [],
            "unsupported_requests": [],
            "integration_actions": [
                {
                    "provider": "github",
                    "resource": "pull_request",
                    "operation": "create",
                }
            ],
            "edit": None,
        }
    )
    extractor = LLMAgentBuilderIntentExtractor(
        db=FakeDb(),
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        runtime_loader=lambda **_kwargs: SimpleNamespace(client=client),
    )

    result = extractor.extract(
        safe_message="GitHub PR 생성 노드를 만들어줘",
        workflow_context={"workflow_present": False, "nodes": []},
    )

    assert result.request_type == "unsupported"
    assert result.integration_actions[0].operation == "create"
    assert len(client.calls) == 1


def test_llm_intent_extractor_repairs_missing_knowledge_placement_with_specific_guidance():
    invalid = {
        "request_type": "new_workflow",
        "draft_mode": "new_workflow",
        "intent_summary": "웹훅 사내 문서 챗봇 workflow 생성",
        "ordered_capabilities": [
            "webhook_trigger",
            "knowledge_backed_llm",
            "answer",
        ],
        "knowledge_required": True,
        "knowledge_topics": ["사내 문서"],
        "knowledge_placements": [],
        "edit": None,
    }
    valid = {**invalid, "knowledge_placements": [_knowledge_placement()]}
    client = SequenceFakeLLMClient([invalid, valid])
    extractor = LLMAgentBuilderIntentExtractor(
        db=FakeDb(),
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        runtime_loader=lambda **_kwargs: SimpleNamespace(client=client),
    )

    result = extractor.extract(
        safe_message="웹훅으로 받는 사내 문서 챗봇 워크플로우를 만들어줘",
        workflow_context={"workflow_present": True, "nodes": []},
    )

    assert result.request_type == "new_workflow"
    assert result.knowledge_placements == [
        AgentBuilderIntentExtraction.model_validate(valid).knowledge_placements[0]
    ]
    assert len(client.calls) == 2
    repair_prompt = str(client.calls[1][0])
    assert "KNOWLEDGE_PLACEMENT_REQUIRED" in repair_prompt
    assert "knowledge_required=true requires exactly one" in repair_prompt


def test_llm_intent_extractor_repairs_missing_knowledge_placement():
    invalid = {
        "request_type": "new_workflow",
        "draft_mode": "new_workflow",
        "intent_summary": "사내 문서를 참고하는 workflow 생성",
        "ordered_capabilities": ["start_input", "knowledge_backed_llm", "answer"],
        "knowledge_required": True,
        "knowledge_topics": ["사내 문서"],
        "knowledge_placements": [],
        "edit": None,
    }
    valid = {**invalid, "knowledge_placements": [_knowledge_placement()]}
    client = SequenceFakeLLMClient([invalid, valid])
    extractor = LLMAgentBuilderIntentExtractor(
        db=FakeDb(),
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        runtime_loader=lambda **_kwargs: SimpleNamespace(client=client),
    )

    result = extractor.extract(
        safe_message="사내 문서를 참고하는 workflow를 만들어줘",
        workflow_context={"workflow_present": False, "nodes": []},
    )

    assert result.knowledge_placements[0].target_step_id == "step_llm"
    assert len(client.calls) == 2
    assert "KNOWLEDGE_PLACEMENT_REQUIRED" in str(client.calls[1][0])


def test_llm_intent_extractor_fails_after_one_invalid_repair():
    invalid = {
        "request_type": "modify_workflow",
        "draft_mode": "modify_workflow",
        "intent_summary": "모순된 수정 요청",
        "ordered_capabilities": [],
        "edit": None,
    }
    client = SequenceFakeLLMClient([invalid, invalid])
    extractor = LLMAgentBuilderIntentExtractor(
        db=FakeDb(),
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        runtime_loader=lambda **_kwargs: SimpleNamespace(client=client),
    )

    with pytest.raises(AgentBuilderIntentExtractionError):
        extractor.extract(
            safe_message="workflow를 만들어줘",
            workflow_context={"workflow_present": True, "nodes": []},
        )

    assert len(client.calls) == 2


def test_llm_intent_extractor_does_not_echo_invalid_provider_payload():
    raw_payload = "not-json secret-provider-payload"
    client = FakeLLMClient(raw_payload)
    extractor = LLMAgentBuilderIntentExtractor(
        db=FakeDb(),
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        runtime_loader=lambda **_kwargs: SimpleNamespace(client=client),
    )

    with pytest.raises(AgentBuilderIntentExtractionError) as exc:
        extractor.extract(
            safe_message="입력 노드 뒤에 응답 노드를 추가해줘",
            workflow_context={"workflow_present": True, "nodes": []},
        )

    assert raw_payload not in str(exc.value)
    assert len(client.calls) == 1


def test_llm_intent_extractor_fails_schema_without_repair():
    client = SequenceFakeLLMClient(
        [
            {
                "request_type": "not-a-valid-request-type",
                "draft_mode": "new_workflow",
                "intent_summary": "invalid schema response",
                "ordered_capabilities": [],
                "edit": None,
            },
            {
                "request_type": "new_workflow",
                "draft_mode": "new_workflow",
                "intent_summary": "should not be consumed",
                "ordered_capabilities": ["start_input", "answer"],
                "edit": None,
            },
        ]
    )
    extractor = LLMAgentBuilderIntentExtractor(
        db=FakeDb(),
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        runtime_loader=lambda **_kwargs: SimpleNamespace(client=client),
    )

    with pytest.raises(AgentBuilderIntentExtractionError):
        extractor.extract(
            safe_message="입력 응답 노드를 만들어줘",
            workflow_context={"workflow_present": False, "nodes": []},
        )

    assert len(client.calls) == 1


def test_llm_intent_extractor_records_initial_and_repair_attempts_separately():
    client = UsageSequenceFakeLLMClient(
        [
            {
                "request_type": "modify_workflow",
                "draft_mode": "modify_workflow",
                "intent_summary": "수정 대상을 찾지 못함",
                "ordered_capabilities": [],
                "edit": None,
            },
            {
                "request_type": "new_workflow",
                "draft_mode": "new_workflow",
                "intent_summary": "입력을 응답으로 연결",
                "ordered_capabilities": ["start_input", "answer"],
            },
        ]
    )
    recorder = CapturingUsageRecorder()
    context = AgentBuilderIntentUsageContext(
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        request_id=uuid.uuid4(),
    )
    credential_id = uuid.uuid4()
    model_id = uuid.uuid4()
    extractor = LLMAgentBuilderIntentExtractor(
        db=FakeDb(),
        user_id=context.user_id,
        organization_id=context.organization_id,
        runtime_loader=lambda **_kwargs: SimpleNamespace(
            client=client,
            credential_id=credential_id,
            model_id="model-example",
            model_db_id=model_id,
            organization_id=context.organization_id,
        ),
        usage_recorder=recorder,
    )

    result = extractor.extract(
        safe_message="입력과 응답 노드를 만들어줘",
        workflow_context={"workflow_present": False, "nodes": []},
        usage_context=context,
    )

    assert result.request_type == "new_workflow"
    assert len(recorder.calls) == 2
    assert len(recorder.reservations) == 2
    assert [sample.attempt for _, sample in recorder.calls] == [1, 2]
    assert [sample.prompt_tokens for _, sample in recorder.calls] == [10, 20]
    assert [sample.completion_tokens for _, sample in recorder.calls] == [2, 4]
    assert all(call_context == context for call_context, _ in recorder.calls)
    assert all(sample.credential_id == credential_id for _, sample in recorder.calls)
    assert all(sample.model_id == model_id for _, sample in recorder.calls)


def test_llm_intent_extractor_records_schema_invalid_attempt_without_repair():
    client = UsageSequenceFakeLLMClient(
        [
            {
                "request_type": "invalid",
                "draft_mode": "new_workflow",
                "intent_summary": "invalid schema response",
                "ordered_capabilities": [],
            },
            {
                "request_type": "new_workflow",
                "draft_mode": "new_workflow",
                "intent_summary": "호출되면 안 됨",
                "ordered_capabilities": ["start_input", "answer"],
            },
        ]
    )
    recorder = CapturingUsageRecorder()
    context = AgentBuilderIntentUsageContext(
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        request_id=uuid.uuid4(),
    )
    extractor = LLMAgentBuilderIntentExtractor(
        db=FakeDb(),
        user_id=context.user_id,
        organization_id=context.organization_id,
        runtime_loader=lambda **_kwargs: SimpleNamespace(
            client=client,
            credential_id=uuid.uuid4(),
            model_id="model-example",
            model_db_id=uuid.uuid4(),
            organization_id=context.organization_id,
        ),
        usage_recorder=recorder,
    )

    with pytest.raises(AgentBuilderIntentExtractionError):
        extractor.extract(
            safe_message="입력과 응답 노드를 만들어줘",
            workflow_context={"workflow_present": False, "nodes": []},
            usage_context=context,
        )

    assert len(client.calls) == 1
    assert [sample.attempt for _, sample in recorder.calls] == [1]
    assert [reservation.attempt for reservation in recorder.reservations] == [1]


def test_llm_intent_extractor_records_semantic_failure_before_repair():
    client = UsageSequenceFakeLLMClient(
        [
            {
                "request_type": "modify_workflow",
                "draft_mode": "modify_workflow",
                "intent_summary": "수정 대상을 찾지 못함",
                "ordered_capabilities": [],
                "edit": None,
            },
            {
                "request_type": "new_workflow",
                "draft_mode": "new_workflow",
                "intent_summary": "입력을 응답으로 연결",
                "ordered_capabilities": ["start_input", "answer"],
            },
        ]
    )
    recorder = CapturingUsageRecorder()
    context = AgentBuilderIntentUsageContext(
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        request_id=uuid.uuid4(),
    )
    extractor = LLMAgentBuilderIntentExtractor(
        db=FakeDb(),
        user_id=context.user_id,
        organization_id=context.organization_id,
        runtime_loader=lambda **_kwargs: SimpleNamespace(
            client=client,
            credential_id=uuid.uuid4(),
            model_id="model-example",
            model_db_id=uuid.uuid4(),
            organization_id=context.organization_id,
        ),
        usage_recorder=recorder,
    )

    result = extractor.extract(
        safe_message="새 입력과 응답 흐름을 만들어줘",
        workflow_context={"workflow_present": True, "nodes": []},
        usage_context=context,
    )

    assert result.request_type == "new_workflow"
    assert [sample.attempt for _, sample in recorder.calls] == [1, 2]
    assert [sample.prompt_tokens for _, sample in recorder.calls] == [10, 20]


def test_llm_intent_extractor_does_not_call_repair_when_attempt_two_is_rejected():
    class RejectSecondAttemptUsageRecorder(CapturingUsageRecorder):
        def reserve(self, context, **kwargs):
            if kwargs["attempt"] == 2:
                raise AgentBuilderIntentUsageRecordingError(
                    "intent_usage_recording_failed"
                )
            return super().reserve(context, **kwargs)

    client = UsageSequenceFakeLLMClient(
        [
            {
                "request_type": "unsupported",
                "draft_mode": "new_workflow",
                "intent_summary": "지원 사유 누락",
                "ordered_capabilities": [],
                "unsupported_requests": [],
            },
            {
                "request_type": "unsupported",
                "draft_mode": "new_workflow",
                "intent_summary": "호출되면 안 됨",
                "ordered_capabilities": [],
                "unsupported_requests": ["지원하지 않는 요청"],
            },
        ]
    )
    recorder = RejectSecondAttemptUsageRecorder()
    context = AgentBuilderIntentUsageContext(
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        request_id=uuid.uuid4(),
    )
    extractor = LLMAgentBuilderIntentExtractor(
        db=FakeDb(),
        user_id=context.user_id,
        organization_id=context.organization_id,
        runtime_loader=lambda **_kwargs: SimpleNamespace(
            client=client,
            credential_id=uuid.uuid4(),
            model_id="model-example",
            model_db_id=uuid.uuid4(),
            organization_id=context.organization_id,
        ),
        usage_recorder=recorder,
    )

    with pytest.raises(AgentBuilderIntentUsageRecordingError):
        extractor.extract(
            safe_message="지원 범위를 확인해줘",
            workflow_context={"workflow_present": False, "nodes": []},
            usage_context=context,
        )

    assert len(client.calls) == 1
    assert [sample.attempt for _, sample in recorder.calls] == [1]
    assert [reservation.attempt for reservation in recorder.reservations] == [1]


def test_llm_intent_extractor_does_not_record_when_provider_has_no_response():
    recorder = CapturingUsageRecorder()
    context = AgentBuilderIntentUsageContext(
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        request_id=uuid.uuid4(),
    )
    extractor = LLMAgentBuilderIntentExtractor(
        db=FakeDb(),
        user_id=context.user_id,
        organization_id=context.organization_id,
        runtime_loader=lambda **_kwargs: SimpleNamespace(
            client=ProviderFailingFakeLLMClient(),
            credential_id=uuid.uuid4(),
            model_id="model-example",
            model_db_id=uuid.uuid4(),
            organization_id=context.organization_id,
        ),
        usage_recorder=recorder,
    )

    with pytest.raises(AgentBuilderIntentExtractionError):
        extractor.extract(
            safe_message="입력과 응답 노드를 만들어줘",
            workflow_context={"workflow_present": False, "nodes": []},
            usage_context=context,
        )

    assert recorder.calls == []
    assert len(recorder.reservations) == 1
    assert recorder.canceled == recorder.reservations


def test_llm_intent_extractor_records_usage_before_provider_content_error():
    client = UsageResponseValidationFailingFakeLLMClient()
    recorder = CapturingUsageRecorder()
    context = AgentBuilderIntentUsageContext(
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        request_id=uuid.uuid4(),
    )
    extractor = LLMAgentBuilderIntentExtractor(
        db=FakeDb(),
        user_id=context.user_id,
        organization_id=context.organization_id,
        runtime_loader=lambda **_kwargs: SimpleNamespace(
            client=client,
            credential_id=uuid.uuid4(),
            model_id="model-example",
            model_db_id=uuid.uuid4(),
            organization_id=context.organization_id,
        ),
        usage_recorder=recorder,
    )

    with pytest.raises(
        AgentBuilderIntentExtractionError,
        match="LLM intent response is invalid",
    ):
        extractor.extract(
            safe_message="입력과 응답 노드를 만들어줘",
            workflow_context={"workflow_present": False, "nodes": []},
            usage_context=context,
        )

    assert client.calls == 1
    assert len(recorder.calls) == 1
    _, sample = recorder.calls[0]
    assert sample.prompt_tokens == 12
    assert sample.completion_tokens == 3
    assert recorder.canceled == []


def test_google_intent_extractor_records_usage_before_invalid_content(monkeypatch):
    client, provider_calls = _google_client_with_response(
        monkeypatch,
        {
            "choices": [{"message": {"content": ""}}],
            "usage": {"prompt_tokens": 17, "completion_tokens": 5},
        },
    )
    recorder = CapturingUsageRecorder()
    context = AgentBuilderIntentUsageContext(
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        request_id=uuid.uuid4(),
    )
    extractor = LLMAgentBuilderIntentExtractor(
        db=FakeDb(),
        user_id=context.user_id,
        organization_id=context.organization_id,
        runtime_loader=lambda **_kwargs: SimpleNamespace(
            client=client,
            credential_id=uuid.uuid4(),
            model_id=client.model_id,
            model_db_id=uuid.uuid4(),
            organization_id=context.organization_id,
        ),
        knowledge_context_loader=lambda **_kwargs: [],
        usage_recorder=recorder,
    )

    with pytest.raises(
        AgentBuilderIntentExtractionError,
        match="LLM intent response is invalid",
    ):
        extractor.extract(
            safe_message="입력과 응답 노드를 만들어줘",
            workflow_context={"workflow_present": False, "nodes": []},
            usage_context=context,
        )

    assert len(provider_calls) == 1
    assert len(recorder.calls) == 1
    _, sample = recorder.calls[0]
    assert sample.prompt_tokens == 17
    assert sample.completion_tokens == 5
    assert recorder.canceled == []


def test_google_intent_extractor_fails_without_usage_and_does_not_recall_provider(
    monkeypatch,
):
    client, provider_calls = _google_client_with_response(
        monkeypatch,
        {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "request_type": "new_workflow",
                                "draft_mode": "new_workflow",
                                "intent_summary": "입력과 응답 연결",
                                "ordered_capabilities": ["start_input", "answer"],
                            }
                        )
                    }
                }
            ]
        },
    )
    recorder = CapturingUsageRecorder()
    context = AgentBuilderIntentUsageContext(
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        request_id=uuid.uuid4(),
    )
    extractor = LLMAgentBuilderIntentExtractor(
        db=FakeDb(),
        user_id=context.user_id,
        organization_id=context.organization_id,
        runtime_loader=lambda **_kwargs: SimpleNamespace(
            client=client,
            credential_id=uuid.uuid4(),
            model_id=client.model_id,
            model_db_id=uuid.uuid4(),
            organization_id=context.organization_id,
        ),
        knowledge_context_loader=lambda **_kwargs: [],
        usage_recorder=recorder,
    )

    with pytest.raises(AgentBuilderIntentUsageRecordingError):
        extractor.extract(
            safe_message="입력과 응답 노드를 만들어줘",
            workflow_context={"workflow_present": False, "nodes": []},
            usage_context=context,
        )

    assert len(provider_calls) == 1
    assert recorder.calls == []
    assert recorder.canceled == recorder.reservations


def test_llm_intent_extractor_fails_without_usage_and_does_not_recall_provider():
    client = FakeLLMClient(
        {
            "request_type": "new_workflow",
            "draft_mode": "new_workflow",
            "intent_summary": "입력과 응답 연결",
            "ordered_capabilities": ["start_input", "answer"],
        }
    )
    recorder = CapturingUsageRecorder()
    context = AgentBuilderIntentUsageContext(
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        request_id=uuid.uuid4(),
    )
    extractor = LLMAgentBuilderIntentExtractor(
        db=FakeDb(),
        user_id=context.user_id,
        organization_id=context.organization_id,
        runtime_loader=lambda **_kwargs: SimpleNamespace(
            client=client,
            credential_id=uuid.uuid4(),
            model_id="model-example",
            model_db_id=uuid.uuid4(),
            organization_id=context.organization_id,
        ),
        usage_recorder=recorder,
    )

    with pytest.raises(AgentBuilderIntentUsageRecordingError):
        extractor.extract(
            safe_message="입력과 응답 노드를 만들어줘",
            workflow_context={"workflow_present": False, "nodes": []},
            usage_context=context,
        )

    assert len(client.calls) == 1
    assert recorder.calls == []
    assert recorder.canceled == recorder.reservations


def test_llm_intent_extractor_does_not_recall_provider_when_usage_recording_fails():
    client = UsageSequenceFakeLLMClient(
        [
            {
                "request_type": "new_workflow",
                "draft_mode": "new_workflow",
                "intent_summary": "입력을 응답으로 연결",
                "ordered_capabilities": ["start_input", "answer"],
            }
        ]
    )
    recorder = CapturingUsageRecorder(
        AgentBuilderIntentUsageRecordingError("intent_usage_recording_failed")
    )
    context = AgentBuilderIntentUsageContext(
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        request_id=uuid.uuid4(),
    )
    extractor = LLMAgentBuilderIntentExtractor(
        db=FakeDb(),
        user_id=context.user_id,
        organization_id=context.organization_id,
        runtime_loader=lambda **_kwargs: SimpleNamespace(
            client=client,
            credential_id=uuid.uuid4(),
            model_id="model-example",
            model_db_id=uuid.uuid4(),
            organization_id=context.organization_id,
        ),
        usage_recorder=recorder,
    )

    with pytest.raises(AgentBuilderIntentUsageRecordingError):
        extractor.extract(
            safe_message="입력과 응답 노드를 만들어줘",
            workflow_context={"workflow_present": False, "nodes": []},
            usage_context=context,
        )

    assert len(client.calls) == 1
    assert len(recorder.calls) == 1
    assert recorder.canceled == []


def test_llm_intent_extractor_reserves_usage_before_provider_call():
    client = UsageSequenceFakeLLMClient(
        [
            {
                "request_type": "new_workflow",
                "draft_mode": "new_workflow",
                "intent_summary": "입력과 응답 연결",
                "ordered_capabilities": ["start_input", "answer"],
            }
        ]
    )
    context = AgentBuilderIntentUsageContext(
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        request_id=uuid.uuid4(),
    )
    recorder = CapturingUsageRecorder(
        reserve_error=AgentBuilderIntentUsageRecordingError(
            "intent_usage_recording_failed"
        )
    )
    extractor = LLMAgentBuilderIntentExtractor(
        db=FakeDb(),
        user_id=context.user_id,
        organization_id=context.organization_id,
        runtime_loader=lambda **_kwargs: SimpleNamespace(
            client=client,
            credential_id=uuid.uuid4(),
            model_id="model-example",
            model_db_id=uuid.uuid4(),
            organization_id=context.organization_id,
        ),
        usage_recorder=recorder,
    )

    with pytest.raises(AgentBuilderIntentUsageRecordingError):
        extractor.extract(
            safe_message="입력과 응답 노드를 만들어줘",
            workflow_context={"workflow_present": False, "nodes": []},
            usage_context=context,
        )

    assert client.calls == []
    assert recorder.calls == []


def test_llm_intent_extractor_reports_permission_aware_runtime_failure():
    def fail_runtime(**_kwargs):
        raise LLMCredentialNotAvailableError(
            "credential_not_available",
            "credential-secret-must-not-leak",
        )

    extractor = LLMAgentBuilderIntentExtractor(
        db=FakeDb(),
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        runtime_loader=fail_runtime,
    )

    with pytest.raises(AgentBuilderIntentRuntimeUnavailableError) as exc:
        extractor.extract(
            safe_message="입력 출력 노드를 생성해줘",
            workflow_context={"workflow_present": False, "nodes": []},
        )

    assert "credential-secret-must-not-leak" not in str(exc.value)


def test_llm_intent_extractor_does_not_misclassify_internal_loader_error():
    def fail_runtime(**_kwargs):
        raise ValueError("internal-db-detail-must-not-leak")

    extractor = LLMAgentBuilderIntentExtractor(
        db=FakeDb(),
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        runtime_loader=fail_runtime,
    )

    with pytest.raises(AgentBuilderIntentExtractionError) as exc:
        extractor.extract(
            safe_message="입력 출력 노드를 생성해줘",
            workflow_context={"workflow_present": False, "nodes": []},
        )

    assert not isinstance(exc.value, AgentBuilderIntentRuntimeUnavailableError)
    assert "internal-db-detail-must-not-leak" not in str(exc.value)


def test_service_normalizes_llm_intent_with_catalog_allowlist_and_llm_order():
    extractor = FakeIntentExtractor(
        AgentBuilderIntentExtraction(
            request_type="new_workflow",
            draft_mode="new_workflow",
            intent_summary="웹훅 GitHub PR 리뷰 댓글 workflow",
            ordered_capabilities=[
                "webhook_trigger",
                "github_pr_read",
                "llm",
                "github_pr_comment",
                "answer",
            ],
            integration_actions=[
                {
                    "provider": "github",
                    "resource": "pull_request",
                    "operation": "read",
                },
                {
                    "provider": "github",
                    "resource": "pull_request",
                    "operation": "comment",
                },
            ],
        )
    )
    svc = _service(extractor)

    structured = svc._structure_request(  # noqa: SLF001
        AgentBuilderMessageRequest(
            message=(
                "새 워크플로우로 웹훅에서 요청을 받고 GitHub PR을 조회한 뒤 "
                "LLM으로 리뷰해서 GitHub PR에 댓글을 등록해줘"
            )
        ),
        workflow=None,
    )

    assert [step.capability for step in structured.planned_steps] == [
        "webhook_trigger",
        "github_pr_read",
        "knowledge_backed_llm",
        "github_pr_comment",
        "answer",
    ]
    entry, body = svc._ordered_preview_capabilities(structured)  # noqa: SLF001
    assert entry == "webhook_trigger"
    assert body == [
        "github_pr_read",
        "knowledge_backed_llm",
        "github_pr_comment",
    ]


def test_configure_mode_adds_after_graph_knowledge_selection_for_plain_llm():
    extractor = FakeIntentExtractor(
        AgentBuilderIntentExtraction(
            request_type="new_workflow",
            draft_mode="new_workflow",
            intent_summary="Input is summarized by an LLM and returned.",
            ordered_capabilities=["llm"],
            knowledge_required=False,
        )
    )
    svc = _service(extractor)

    structured = svc._structure_request(  # noqa: SLF001
        AgentBuilderMessageRequest(message="Create input, LLM, and answer nodes"),
        workflow=None,
    )

    assert [step.capability for step in structured.planned_steps] == [
        "start_input",
        "knowledge_backed_llm",
        "answer",
    ]
    assert structured.knowledge_requirements[0].target_step_ref == "step_llm"
    assert structured.knowledge_placements[0].model_dump() == {
        "requirement_id": "kr_1",
        "timing": "after_graph",
        "effect_kind": "binding_only",
        "target_step_id": "step_llm",
        "knowledge_step_id": None,
        "upstream_step_id": None,
        "downstream_step_id": None,
        "empty_selection_bridge": None,
    }


def test_production_normalizer_keeps_explicit_workflow_creation_new_with_existing_context():
    extractor = FakeIntentExtractor(
        AgentBuilderIntentExtraction(
            request_type="new_workflow",
            draft_mode="new_workflow",
            intent_summary="웹훅으로 받는 사내 문서 챗봇 workflow",
            ordered_capabilities=[
                "webhook_trigger",
                "knowledge_backed_llm",
                "answer",
            ],
            knowledge_required=True,
            knowledge_topics=["사내 문서"],
            knowledge_candidate_handles=["rec-safe-1"],
            knowledge_placements=[_knowledge_placement()],
            edit=None,
        )
    )
    svc = _service(extractor)
    workflow = SimpleNamespace(
        id=uuid.uuid4(),
        graph={
            "nodes": [
                {"id": "existing", "type": "startNode", "data": {"title": "기존 입력"}}
            ],
            "edges": [],
        },
    )

    structured = svc._structure_request(  # noqa: SLF001
        AgentBuilderMessageRequest(
            message="웹훅으로 받는 사내 문서 챗봇 워크플로우를 만들어줘"
        ),
        workflow=workflow,
    )

    assert structured.request_type == "new_workflow"
    assert structured.draft_mode == "new_workflow"
    assert [step.capability for step in structured.planned_steps] == [
        "webhook_trigger",
        "knowledge_backed_llm",
        "answer",
    ]
    assert structured.edit_operations == []
    assert structured.knowledge_requirements[0].suggested_candidate_handles == [
        "rec-safe-1"
    ]


def test_service_uses_llm_edit_structure_for_create_wording_without_regex():
    extractor = FakeIntentExtractor(
        AgentBuilderIntentExtraction(
            request_type="modify_workflow",
            draft_mode="modify_workflow",
            intent_summary="GitHub PR 댓글 등록 뒤에 LLM 노드를 삽입합니다.",
            ordered_capabilities=["llm"],
            edit=AgentBuilderSemanticEdit(
                operation="insert",
                placement="after",
                target_reference_type="natural_language_node",
                target_query="GitHub PR 댓글 등록",
                target_capabilities=["github_pr_comment"],
            ),
        )
    )
    svc = _service(extractor)
    workflow = SimpleNamespace(
        id=uuid.uuid4(),
        graph={
            "nodes": [
                {
                    "id": "github-comment",
                    "type": "githubNode",
                    "data": {"title": "GitHub PR 댓글 등록", "action": "comment_pr"},
                }
            ],
            "edges": [],
        },
    )

    structured = svc._structure_request(  # noqa: SLF001
        AgentBuilderMessageRequest(
            message="github pr 댓글 등록 뒤에 llm 노드 생성해줘"
        ),
        workflow=workflow,
    )

    assert structured.draft_mode == "modify_workflow"
    assert structured.required_capabilities == ["llm", "knowledge_base"]
    assert [step.capability for step in structured.planned_steps] == [
        "knowledge_backed_llm"
    ]
    assert structured.edit_operations[0].placement == "after"
    assert structured.edit_operations[0].target.capabilities == [
        "github_pr_comment"
    ]
    assert structured.edit_operations[0].target.node_types == ["githubNode"]


def test_service_rejects_schema_valid_modify_without_semantic_edit():
    svc = _service(
        FakeIntentExtractor(
            AgentBuilderIntentExtraction(
                request_type="modify_workflow",
                draft_mode="modify_workflow",
                intent_summary="workflow 생성",
                ordered_capabilities=[],
                edit=None,
            )
        )
    )

    with pytest.raises(AgentBuilderIntentExtractionError):
        svc._structure_request(  # noqa: SLF001
            AgentBuilderMessageRequest(message="workflow를 만들어줘"),
            workflow=SimpleNamespace(graph={"nodes": [], "edges": []}),
        )


def test_service_normalizes_explicit_replace_mode_as_complete_workflow():
    svc = _service(
        FakeIntentExtractor(
            AgentBuilderIntentExtraction(
                request_type="modify_workflow",
                draft_mode="replace_workflow",
                intent_summary="workflow 전체 교체",
                ordered_capabilities=["llm"],
                edit=None,
            )
        )
    )

    structured = svc._structure_request(  # noqa: SLF001
        AgentBuilderMessageRequest(message="현재 workflow를 새 흐름으로 교체해줘"),
        workflow=SimpleNamespace(graph={"nodes": [], "edges": []}),
    )

    assert structured.request_type == "modify_workflow"
    assert structured.draft_mode == "replace_workflow"
    assert [step.capability for step in structured.planned_steps] == [
        "start_input",
        "knowledge_backed_llm",
        "answer",
    ]
    assert structured.edit_operations == []


def test_service_rejects_selected_edge_edit_without_selected_edge_context():
    svc = _service(
        FakeIntentExtractor(
            AgentBuilderIntentExtraction(
                request_type="modify_workflow",
                draft_mode="modify_workflow",
                intent_summary="선택 연결에 LLM 삽입",
                ordered_capabilities=["llm"],
                edit=AgentBuilderSemanticEdit(
                    placement="between",
                    target_reference_type="selected_edge",
                ),
            )
        )
    )

    with pytest.raises(AgentBuilderIntentExtractionError):
        svc._structure_request(  # noqa: SLF001
            AgentBuilderMessageRequest(message="선택한 연결에 LLM을 추가해줘"),
            workflow=SimpleNamespace(graph={"nodes": [], "edges": []}),
        )


def test_service_rejects_llm_capability_outside_catalog_allowlist():
    extractor = FakeIntentExtractor(
        AgentBuilderIntentExtraction(
            request_type="new_workflow",
            draft_mode="new_workflow",
            intent_summary="임의 shell 실행 workflow",
            ordered_capabilities=["start_input", "shell_execute", "answer"],
        )
    )
    svc = _service(extractor)

    structured = svc._structure_request(  # noqa: SLF001
        AgentBuilderMessageRequest(message="shell을 실행하는 workflow를 만들어줘"),
        workflow=None,
    )

    assert structured.request_type == "unsupported"
    assert "shell_execute" not in structured.required_capabilities
    assert structured.risk_flags == ["unsupported_capability"]


def test_service_redacts_secret_before_sending_message_to_intent_extractor():
    extractor = FakeIntentExtractor(
        AgentBuilderIntentExtraction(
            request_type="new_workflow",
            draft_mode="new_workflow",
            intent_summary="HTTP workflow",
            ordered_capabilities=["start_input", "http_request", "answer"],
        )
    )
    svc = _service(extractor)

    svc._structure_request(  # noqa: SLF001
        AgentBuilderMessageRequest(
            message="api_key=secret-value HTTP 요청 workflow를 만들어줘"
        ),
        workflow=None,
    )

    assert "secret-value" not in extractor.calls[0]["safe_message"]
    assert "[redacted]" in extractor.calls[0]["safe_message"]


def test_service_does_not_fall_back_to_regex_when_llm_extraction_fails():
    svc = _service(FailingIntentExtractor())

    with pytest.raises(AgentBuilderIntentExtractionError):
        svc._structure_request(  # noqa: SLF001
            AgentBuilderMessageRequest(message="llm 뒤에 응답 노드를 추가해줘"),
            workflow=SimpleNamespace(
                id=uuid.uuid4(),
                graph={"nodes": [], "edges": []},
            ),
        )


def test_service_requires_intent_extractor_instead_of_implicit_regex_fallback():
    svc = AgentBuilderService(
        FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )

    with pytest.raises(AgentBuilderIntentRuntimeUnavailableError):
        svc._structure_request(  # noqa: SLF001
            AgentBuilderMessageRequest(message="입력 출력 노드를 생성해줘"),
            workflow=None,
        )


def test_target_resolver_prefers_github_action_role_over_shared_node_type():
    extractor = FakeIntentExtractor(
        AgentBuilderIntentExtraction(
            request_type="modify_workflow",
            draft_mode="modify_workflow",
            intent_summary="GitHub 댓글 노드 뒤에 LLM을 추가합니다.",
            ordered_capabilities=["llm"],
            edit=AgentBuilderSemanticEdit(
                placement="after",
                target_reference_type="natural_language_node",
                target_query="GitHub",
                target_capabilities=["github_pr_comment"],
            ),
        )
    )
    svc = _service(extractor)
    workflow = SimpleNamespace(
        id=uuid.uuid4(),
        graph={
            "nodes": [
                {
                    "id": "github-read",
                    "type": "githubNode",
                    "data": {"title": "GitHub PR 조회", "action": "get_pr"},
                },
                {
                    "id": "github-comment",
                    "type": "githubNode",
                    "data": {"title": "GitHub PR 댓글 등록", "action": "comment_pr"},
                },
                {"id": "answer", "type": "answerNode", "data": {"title": "응답"}},
            ],
            "edges": [
                {
                    "id": "edge-comment-answer",
                    "source": "github-comment",
                    "target": "answer",
                }
            ],
        },
    )
    request = AgentBuilderMessageRequest(
        message="github pr 댓글 등록 뒤에 llm 노드 생성해줘"
    )
    structured = svc._structure_request(request, workflow)  # noqa: SLF001

    resolution = svc._resolve_edit_target(  # noqa: SLF001
        structured,
        workflow=workflow,
        selected_node_id=None,
        selected_edge_id=None,
    )

    assert resolution["status"] == "resolved"
    assert resolution["node_id"] == "github-comment"


def test_llm_structured_answer_insertion_rewires_existing_graph():
    extractor = FakeIntentExtractor(
        AgentBuilderIntentExtraction(
            request_type="modify_workflow",
            draft_mode="modify_workflow",
            intent_summary="LLM 뒤에 응답 노드를 추가합니다.",
            ordered_capabilities=["answer"],
            edit=AgentBuilderSemanticEdit(
                placement="after",
                target_reference_type="natural_language_node",
                target_query="LLM",
                target_capabilities=["llm"],
            ),
        )
    )
    svc = _service(extractor)
    workflow = SimpleNamespace(
        id=uuid.uuid4(),
        graph={
            "nodes": [
                {"id": "llm", "type": "llmNode", "data": {"title": "LLM"}},
                {
                    "id": "github-comment",
                    "type": "githubNode",
                    "data": {"title": "GitHub PR 댓글 등록", "action": "comment_pr"},
                },
            ],
            "edges": [
                {
                    "id": "edge-llm-comment",
                    "source": "llm",
                    "target": "github-comment",
                }
            ],
        },
    )
    request = AgentBuilderMessageRequest(message="llm 뒤에 응답 노드 하나 추가")
    structured = svc._structure_request(request, workflow)  # noqa: SLF001
    resolution = svc._resolve_edit_target(  # noqa: SLF001
        structured,
        workflow=workflow,
        selected_node_id=None,
        selected_edge_id=None,
    )

    preview = svc._build_preview_graph(  # noqa: SLF001
        structured,
        workflow=workflow,
        kb_bindings=[],
        target_resolution=resolution,
    )

    generated = [
        node for node in preview["nodes"] if str(node["id"]).startswith("agent-")
    ]
    assert [node["type"] for node in generated] == ["answerNode"]
    answer_id = generated[0]["id"]
    edge_pairs = {(edge["source"], edge["target"]) for edge in preview["edges"]}
    assert ("llm", "github-comment") not in edge_pairs
    assert ("llm", answer_id) in edge_pairs
    assert (answer_id, "github-comment") in edge_pairs


def test_natural_language_direct_edge_target_resolves_exactly_one_edge():
    extractor = FakeIntentExtractor(
        AgentBuilderIntentExtraction(
            request_type="modify_workflow",
            draft_mode="modify_workflow",
            intent_summary="Diff와 LLM 사이에 Code node를 삽입합니다.",
            ordered_capabilities=["code_execution"],
            edit=AgentBuilderSemanticEdit(
                placement="between",
                target_reference_type="natural_language_edge",
                source_query="Diff",
                destination_query="LLM",
            ),
        )
    )
    svc = _service(extractor)
    workflow = SimpleNamespace(
        id=uuid.uuid4(),
        graph={
            "nodes": [
                {"id": "diff", "type": "codeNode", "data": {"title": "Diff"}},
                {"id": "llm", "type": "llmNode", "data": {"title": "LLM"}},
            ],
            "edges": [{"id": "edge-diff-llm", "source": "diff", "target": "llm"}],
        },
    )

    structured = svc._structure_request(  # noqa: SLF001
        AgentBuilderMessageRequest(message="Diff와 LLM 사이에 Code node를 넣어줘"),
        workflow,
    )
    resolution = svc._resolve_edit_target(  # noqa: SLF001
        structured,
        workflow=workflow,
        selected_node_id=None,
        selected_edge_id=None,
    )

    assert resolution == {
        "status": "resolved",
        "operation_id": "edit_1",
        "placement": "between",
        "edge_id": "edge-diff-llm",
        "node_id": None,
        "source_node_id": "diff",
        "destination_node_id": "llm",
        "replaced_edge_ids": ["edge-diff-llm"],
    }


def test_natural_language_edge_resolves_start_and_answer_aliases():
    extractor = FakeIntentExtractor(
        AgentBuilderIntentExtraction(
            request_type="modify_workflow",
            draft_mode="modify_workflow",
            intent_summary="입력과 응답 사이에 LLM node를 삽입합니다.",
            ordered_capabilities=["llm"],
            edit=AgentBuilderSemanticEdit(
                placement="between",
                target_reference_type="natural_language_edge",
                source_query="입력 노드",
                destination_query="응답 노드",
            ),
        )
    )
    svc = _service(extractor)
    workflow = SimpleNamespace(
        id=uuid.uuid4(),
        graph={
            "nodes": [
                {"id": "start", "type": "startNode", "data": {"title": "Start node"}},
                {"id": "answer", "type": "answerNode", "data": {"title": "Answer node"}},
            ],
            "edges": [{"id": "edge-start-answer", "source": "start", "target": "answer"}],
        },
    )

    structured = svc._structure_request(  # noqa: SLF001
        AgentBuilderMessageRequest(message="입력 응답 노드 사이에 LLM 노드 생성"),
        workflow,
    )
    resolution = svc._resolve_edit_target(  # noqa: SLF001
        structured,
        workflow=workflow,
        selected_node_id=None,
        selected_edge_id=None,
    )

    assert resolution["status"] == "resolved"
    assert resolution["edge_id"] == "edge-start-answer"
    assert resolution["source_node_id"] == "start"
    assert resolution["destination_node_id"] == "answer"


@pytest.mark.parametrize(
    "edges",
    [
        [],
        [
            {"id": "edge-diff-llm-1", "source": "diff", "target": "llm"},
            {"id": "edge-diff-llm-2", "source": "diff", "target": "llm"},
        ],
    ],
)
def test_natural_language_direct_edge_target_requires_one_edge(edges):
    extractor = FakeIntentExtractor(
        AgentBuilderIntentExtraction(
            request_type="modify_workflow",
            draft_mode="modify_workflow",
            intent_summary="Diff와 LLM 사이에 Code node를 삽입합니다.",
            ordered_capabilities=["code_execution"],
            edit=AgentBuilderSemanticEdit(
                placement="between",
                target_reference_type="natural_language_edge",
                source_query="Diff",
                destination_query="LLM",
            ),
        )
    )
    svc = _service(extractor)
    workflow = SimpleNamespace(
        id=uuid.uuid4(),
        graph={
            "nodes": [
                {"id": "diff", "type": "codeNode", "data": {"title": "Diff"}},
                {"id": "llm", "type": "llmNode", "data": {"title": "LLM"}},
            ],
            "edges": edges,
        },
    )
    structured = svc._structure_request(  # noqa: SLF001
        AgentBuilderMessageRequest(message="Diff와 LLM 사이에 Code node를 넣어줘"),
        workflow,
    )

    resolution = svc._resolve_edit_target(  # noqa: SLF001
        structured,
        workflow=workflow,
        selected_node_id=None,
        selected_edge_id=None,
    )

    assert resolution["status"] == "clarification_required"
    assert resolution["options"] == []
