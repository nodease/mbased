"""
HTTP Request Node 최소 테스트 [GEVENT] Sync 버전

[이 테스트 파일이 검증하는 것들]
1. 데이터 포장 검증: 노드가 URL, 헤더, 바디를 올바르게 조립하는지 확인합니다.
2. 로직 검증:
   - 변수가 URL/헤더에 잘 치환되어 들어가는지 ({{ .. }} -> 값)
   - 인증(Bearer/API Key) 설정 시 헤더가 자동으로 잘 붙는지
   - Body가 있을 때 Content-Type이 자동으로 붙는지
3. 응답 처리 검증: 라이브러리가 준 응답(Mock)을 노드 출력 포맷에 맞게 잘 변환하는지

* 주의: 실제 인터넷으로 요청을 보내지는 않습니다 (Mock 사용).
"""

import os
import sys
import uuid
from unittest.mock import patch

import pytest

# Add project root to sys.path
sys.path.append(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from apps.workflow_engine.workflow.nodes.base.entities import NodeStatus
from apps.workflow_engine.domain.execution import NodeExecutionControl
from apps.workflow_engine.domain.external_effect import (
    EffectInvocationFailure,
    ExternalEffectError,
)
from apps.workflow_engine.adapters.providers.generic_http import (
    GenericHttpEffectAdapter,
)
from apps.workflow_engine.application.outbound_http import (
    OutboundHttpError,
    OutboundHttpFailurePhase,
    OutboundHttpRequest,
    OutboundHttpResponse,
)
from apps.workflow_engine.workflow.nodes.http import (
    HttpRequestNode,
    HttpRequestNodeData,
)
from apps.workflow_engine.workflow.nodes.http.entities import HttpMethod, HttpVariable


class _CaptureOutboundHttp:
    def __init__(
        self,
        *,
        status_code=200,
        json_content=b"{}",
        headers=(("content-type", "application/json"),),
        error: OutboundHttpError | None = None,
    ) -> None:
        self.response = OutboundHttpResponse(
            status_code=status_code,
            headers=tuple(headers),
            content=json_content,
        )
        self.error = error
        self.requests: list[OutboundHttpRequest] = []

    def send(self, request: OutboundHttpRequest) -> OutboundHttpResponse:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.response


def _adapter(port: _CaptureOutboundHttp) -> GenericHttpEffectAdapter:
    return GenericHttpEffectAdapter(outbound_http=port)


def test_http_node_basic_get():
    """기본 GET 요청 테스트"""
    # Given
    node_data = HttpRequestNodeData(
        title="GET 요청",
        method=HttpMethod.GET,
        url="https://api.example.com/users",
        timeout=5000,
        referenced_variables=[],
    )
    node = HttpRequestNode(id="http-1", data=node_data)

    # When
    port = _CaptureOutboundHttp(
        json_content=b'{"id":1,"name":"John"}',
    )

    with patch(
        "apps.workflow_engine.workflow.nodes.http.http_node.build_generic_http_effect_adapter",
        return_value=_adapter(port),
    ):
        outputs = node.execute({})

    # Then
    assert port.requests[0].method == "GET"
    assert port.requests[0].url == "https://api.example.com/users"
    assert port.requests[0].headers == ()
    assert port.requests[0].body_mode == "no_body"
    assert outputs["status"] == 200
    assert outputs["data"]["id"] == 1
    assert node.status == NodeStatus.COMPLETED


def test_legacy_read_only_http_without_publisher_identity_remains_compatible():
    node = HttpRequestNode(
        id="http-1",
        data=HttpRequestNodeData(
            title="Legacy GET",
            method=HttpMethod.GET,
            url="https://api.example.com/users",
            referenced_variables=[],
        ),
    )
    control = NodeExecutionControl(
        execution_id=uuid.uuid4(),
        invocation_path_prefix=(),
        external_effect_context=None,
        external_effect_enforced=False,
    )
    port = _CaptureOutboundHttp(json_content=b'{"ok":true}')

    with patch(
        "apps.workflow_engine.workflow.nodes.http.http_node.build_generic_http_effect_adapter",
        return_value=_adapter(port),
    ):
        output = node.execute({}, runtime_control=control)

    assert output["data"] == {"ok": True}
    assert len(port.requests) == 1


def test_legacy_mutating_http_without_publisher_identity_is_blocked():
    node = HttpRequestNode(
        id="http-1",
        data=HttpRequestNodeData(
            title="Legacy POST",
            method=HttpMethod.POST,
            url="https://api.example.com/items",
            body='{"value": 1}',
            referenced_variables=[],
        ),
    )
    control = NodeExecutionControl(
        execution_id=uuid.uuid4(),
        invocation_path_prefix=(),
        external_effect_context=None,
        external_effect_enforced=False,
    )

    port = _CaptureOutboundHttp()
    with patch(
        "apps.workflow_engine.workflow.nodes.http.http_node.build_generic_http_effect_adapter",
        return_value=_adapter(port),
    ), pytest.raises(ExternalEffectError) as captured:
        node.execute({}, runtime_control=control)

    assert captured.value.code == "external_effect.identity_invalid"
    assert port.requests == []


def test_failed_http_provider_call_keeps_safe_trace_summary_without_payload():
    node = HttpRequestNode(
        id="http-1",
        data=HttpRequestNodeData(
            title="POST",
            method=HttpMethod.POST,
            url="https://api.example.com/items",
            body='{"opaque":"request"}',
            referenced_variables=[],
        ),
    )
    port = _CaptureOutboundHttp(
        error=OutboundHttpError(
            "response_lost",
            phase=OutboundHttpFailurePhase.OUTCOME_UNKNOWN,
        )
    )

    with patch(
        "apps.workflow_engine.workflow.nodes.http.http_node.build_generic_http_effect_adapter",
        return_value=_adapter(port),
    ), pytest.raises(EffectInvocationFailure):
        node.execute({})

    assert node._trace_metadata["http"]["method"] == "POST"
    assert node._trace_metadata["http"]["response_size"] == 0
    assert "opaque provider failure" not in str(node._trace_metadata)
    assert "opaque" not in str(node._trace_metadata)


def test_http_node_post_with_body():
    """POST 요청 with Body 테스트"""
    # Given
    node_data = HttpRequestNodeData(
        title="POST 요청",
        method=HttpMethod.POST,
        url="https://api.example.com/posts",
        body='{"title": "New Post"}',
        timeout=5000,
        referenced_variables=[],
    )
    node = HttpRequestNode(id="http-1", data=node_data)

    # When
    port = _CaptureOutboundHttp(status_code=201, json_content=b'{"id":101}')

    with patch(
        "apps.workflow_engine.workflow.nodes.http.http_node.build_generic_http_effect_adapter",
        return_value=_adapter(port),
    ):
        outputs = node.execute({})

    # Then
    assert port.requests[0].method == "POST"
    assert port.requests[0].json_body == {"title": "New Post"}
    assert outputs["status"] == 201


def test_http_node_bearer_auth():
    """Bearer Token 인증 테스트"""
    # Given
    node_data = HttpRequestNodeData(
        title="Bearer 인증",
        method=HttpMethod.GET,
        url="https://api.example.com/protected",
        authType="bearer",
        authConfig={"token": "my-token"},
        timeout=5000,
        referenced_variables=[],
    )
    node = HttpRequestNode(id="http-1", data=node_data)

    # When
    port = _CaptureOutboundHttp(json_content=b'{"data":"protected"}')

    with patch(
        "apps.workflow_engine.workflow.nodes.http.http_node.build_generic_http_effect_adapter",
        return_value=_adapter(port),
    ):
        outputs = node.execute({})

    # Then
    headers = dict(port.requests[0].headers)
    assert headers["authorization"] == "Bearer my-token"
    assert outputs["status"] == 200


def test_http_node_variable_substitution():
    """변수 치환 테스트"""
    # Given
    node_data = HttpRequestNodeData(
        title="변수 치환",
        method=HttpMethod.GET,
        url="https://api.example.com/users/{{ userId }}",  # Jinja2 템플릿 사용
        timeout=5000,
        referenced_variables=[
            HttpVariable(name="userId", value_selector=["Start", "userId"])
        ],
    )
    node = HttpRequestNode(id="http-1", data=node_data)

    # When
    port = _CaptureOutboundHttp(json_content=b'{"id":123}')

    with patch(
        "apps.workflow_engine.workflow.nodes.http.http_node.build_generic_http_effect_adapter",
        return_value=_adapter(port),
    ):
        # Start 노드의 출력을 inputs로 전달
        inputs = {"Start": {"userId": "123"}}
        outputs = node.execute(inputs)

    # Then
    assert port.requests[0].url == "https://api.example.com/users/123"
    assert outputs["status"] == 200
