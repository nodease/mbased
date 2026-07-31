"""
Anthropic(Claude)용 LLM 클라이언트.

Anthropic Messages API를 직접 호출합니다.
"""

from typing import Any, Dict, List

import httpx
from apps.shared.services.egress_guard import EgressGuardError

from .base import BaseLLMClient, LLMResponseValidationError


class AnthropicClient(BaseLLMClient):
    """
    Anthropic Claude 클라이언트.

    credentials 예시:
    {
        "apiKey": "sk-ant-...",
        "baseUrl": "https://api.anthropic.com"
    }
    """

    # 현재 Anthropic API 버전
    ANTHROPIC_VERSION = "2023-06-01"

    def __init__(self, model_id: str, credentials: Dict[str, Any]):
        super().__init__(model_id=model_id, credentials=credentials)
        self.api_key = credentials.get("apiKey") or credentials.get("api_key")
        base_url = credentials.get("baseUrl") or credentials.get("base_url")
        if not self.api_key or not base_url:
            raise ValueError("Anthropic credentials에 apiKey/baseUrl가 필요합니다.")
        self._configure_provider_endpoint(base_url)
        self.base_url = self._normalize_base_url(base_url)
        self.messages_url = self.base_url.rstrip("/") + "/v1/messages"

    def _normalize_base_url(self, base_url: str) -> str:
        normalized = base_url.rstrip("/")
        if normalized.endswith("/v1"):
            normalized = normalized[:-3]
        return normalized

    def _build_headers(self) -> Dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": self.ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        }

    async def embed(self, text: str) -> List[float]:
        """
        Anthropic은 임베딩 API를 제공하지 않습니다.
        """
        raise NotImplementedError(
            "Anthropic은 임베딩 API를 제공하지 않습니다. "
            "OpenAI 또는 Google의 임베딩 모델을 사용해주세요."
        )

    async def invoke(self, messages: List[Dict[str, Any]], **kwargs) -> Dict[str, Any]:
        """
        Anthropic Messages API 호출 (비동기).

        Args:
            messages: role/content 형식의 메시지 리스트
            **kwargs: temperature, max_tokens 등 추가 옵션

        Returns:
            OpenAI 호환 형식으로 변환된 응답 JSON 딕셔너리

        Raises:
            ValueError: HTTP 에러/파싱 실패 시
        """
        # Anthropic API는 system 메시지를 별도 파라미터로 받음
        system_content = None
        user_messages = []
        
        for msg in messages:
            if msg.get("role") == "system":
                system_content = msg.get("content", "")
            else:
                user_messages.append(msg)

        payload: Dict[str, Any] = {
            "model": self.model_id,
            "messages": user_messages,
            "max_tokens": kwargs.pop("max_tokens", 4096),  # Anthropic은 max_tokens 필수
        }
        
        if system_content:
            payload["system"] = system_content

        # 허용된 옵션만 전달 (Anthropic은 extra inputs 허용 안 함)
        allowed_keys = {"temperature", "top_p", "top_k", "stop_sequences", "metadata"}
        stop_sequences = kwargs.pop("stop", None)
        if stop_sequences:
            if isinstance(stop_sequences, list):
                stop_sequences = [s for s in stop_sequences if isinstance(s, str) and s]
            if isinstance(stop_sequences, list) and stop_sequences:
                kwargs["stop_sequences"] = stop_sequences

        filtered_kwargs = {k: v for k, v in kwargs.items() if k in allowed_keys}
        if "temperature" in filtered_kwargs and "top_p" in filtered_kwargs:
            # Anthropic 일부 모델은 temperature/top_p 동시 지정 불가
            filtered_kwargs.pop("top_p", None)
        payload.update(filtered_kwargs)

        async with httpx.AsyncClient(
            timeout=60,
            **self._async_http_client_options(),
        ) as client:
            try:
                resp = await client.post(
                    self.messages_url, headers=self._build_headers(), json=payload
                )
            except (httpx.RequestError, EgressGuardError) as exc:
                self._raise_provider_transport_error(exc)

        if resp.status_code >= 400:
            self._raise_provider_http_error(resp.status_code)

        try:
            data = resp.json()
            # Anthropic 응답을 OpenAI 호환 형식으로 변환
            return self._convert_to_openai_format(data)
        except LLMResponseValidationError:
            raise
        except ValueError as exc:
            raise ValueError("Anthropic 응답을 JSON으로 파싱할 수 없습니다.") from exc

    def _convert_to_openai_format(self, anthropic_response: Dict[str, Any]) -> Dict[str, Any]:
        """
        Anthropic 응답을 OpenAI 호환 형식으로 변환합니다.
        
        Anthropic 형식:
        {
            "content": [{"type": "text", "text": "..."}],
            "usage": {"input_tokens": X, "output_tokens": Y}
        }
        
        OpenAI 형식:
        {
            "choices": [{"message": {"role": "assistant", "content": "..."}}],
            "usage": {"prompt_tokens": X, "completion_tokens": Y, "total_tokens": Z}
        }
        """
        # 사용량 변환
        usage = anthropic_response.get("usage")
        normalized_usage: Dict[str, Any] = {}
        if isinstance(usage, dict):
            prompt_tokens = usage.get("input_tokens")
            completion_tokens = usage.get("output_tokens")
            if prompt_tokens is not None:
                normalized_usage["prompt_tokens"] = prompt_tokens
            if completion_tokens is not None:
                normalized_usage["completion_tokens"] = completion_tokens
            if (
                isinstance(prompt_tokens, int)
                and not isinstance(prompt_tokens, bool)
                and isinstance(completion_tokens, int)
                and not isinstance(completion_tokens, bool)
            ):
                normalized_usage["total_tokens"] = prompt_tokens + completion_tokens

        # 콘텐츠 검증 실패 전에도 billing usage는 분리할 수 있어야 한다.
        content_blocks = anthropic_response.get("content", [])
        if not isinstance(content_blocks, list):
            raise LLMResponseValidationError(
                "Anthropic response content is invalid",
                usage=normalized_usage,
            )
        text_content = ""
        for block in content_blocks:
            if not isinstance(block, dict):
                raise LLMResponseValidationError(
                    "Anthropic response content is invalid",
                    usage=normalized_usage,
                )
            if block.get("type") == "text":
                text = block.get("text", "")
                if not isinstance(text, str):
                    raise LLMResponseValidationError(
                        "Anthropic response content is invalid",
                        usage=normalized_usage,
                    )
                text_content += text

        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": text_content
                    },
                    "finish_reason": anthropic_response.get("stop_reason", "stop")
                }
            ],
            "usage": normalized_usage,
        }

    def get_num_tokens(self, messages: List[Dict[str, Any]]) -> int:
        """
        토큰 수 추정 (간단한 문자 기반 추정).

        Anthropic은 tiktoken을 지원하지 않으므로,
        대략적인 추정값을 반환합니다: 문자 수 / 4
        """
        total_chars = 0
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            total_chars += len(role) + len(content)
        
        # 대략 4자당 1토큰으로 추정
        return max(1, total_chars // 4)
