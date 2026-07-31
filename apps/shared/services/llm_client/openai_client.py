"""
OpenAI용 LLM 클라이언트.

실제 SDK 대신 HTTP 호출로 동작하며, 응답/에러를 단순 래핑합니다.
"""

import copy
from typing import Any, Dict, List, Optional

import httpx
import tiktoken
from apps.shared.services.egress_guard import EgressGuardError

from .base import (
    BaseLLMClient,
    EmbeddingProviderResult,
    LLMResponseValidationError,
    PreparedEmbeddingInvocation,
    ProviderEndpointUnsupportedError,
    ProviderFailurePhase,
    ProviderInvocationError,
)


class OpenAIClient(BaseLLMClient):
    """
    OpenAI 클라이언트.

    credentials 예시:
    {
        "apiKey": "sk-...",
        "baseUrl": "https://api.openai.com/v1"
    }
    """

    def __init__(self, model_id: str, credentials: Dict[str, Any], provider_name: str = "OpenAI"):
        super().__init__(model_id=model_id, credentials=credentials)
        self.provider_name = provider_name
        self.api_key = credentials.get("apiKey") or credentials.get("api_key")
        self.base_url = credentials.get("baseUrl") or credentials.get("base_url")
        if not self.api_key or not self.base_url:
            raise ValueError(f"{self.provider_name} credentials에 apiKey/baseUrl가 필요합니다.")
        self._configure_provider_endpoint(self.base_url)
        self.chat_url = self.base_url.rstrip("/") + "/chat/completions"
        self.completions_url = self.base_url.rstrip("/") + "/completions"
        self.responses_url = self.base_url.rstrip("/") + "/responses"
        self.embedding_url = self.base_url.rstrip("/") + "/embeddings"
        self._clean_model_id = self.model_id.replace("models/", "").lower()
        # 토큰 계산용 인코더를 인스턴스에 캐시
        self._token_encoder = None

    _STRICT_MAX_COMPLETION_MODELS = {
        "gpt-5.5",
        "gpt-5.5-pro",
        "gpt-5.4-pro",
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.4-nano",
        "gpt-5.2-pro",
        "gpt-5.2",
        "gpt-5.1",
        "gpt-5",
        "gpt-5-mini",
        "gpt-5-nano",
        "o3-pro",
        "o3",
        "o3-mini",
        "o4-mini",
        "o1-pro",
        "o1",
        "gpt-realtime",
        "gpt-realtime-mini",
    }

    _STRICT_TEMPERATURE_ONLY_MODELS = _STRICT_MAX_COMPLETION_MODELS

    _LONG_TIMEOUT_PREFIXES = ("gpt-5", "o1", "o3", "o4")
    _RESPONSES_ENDPOINT_PREFIXES = ("gpt-5", "o1", "o3", "o4")
    _MINIMAL_REASONING_MODEL_PREFIX = "gpt-5"
    # Responses API의 reasoning effort 지원 범위는 모델별로 다르다. 모델
    # 이름 접두사만으로 추정하면 지원하지 않는 값을 보내 400이 날 수 있으므로,
    # 확인된 모델에만 가장 낮은 허용 effort를 명시한다.
    _REASONING_EFFORT_BY_MODEL = {
        "gpt-5": "minimal",
        "gpt-5-mini": "minimal",
        "gpt-5-nano": "minimal",
        "gpt-5.4": "low",
        "gpt-5.4-mini": "low",
        "gpt-5.4-nano": "low",
    }
    _MINIMAL_REASONING_OUTPUT_TOKEN_LIMIT = 1024
    _RESPONSES_UNSUPPORTED_GENERATION_PARAMS = (
        "top_p",
        "presence_penalty",
        "frequency_penalty",
        "stop",
    )
    _JSON_SCHEMA_PREFIXES = (
        "gpt-5",
        "gpt-4.5",
        "gpt-4.1",
        "gpt-4o",
        "o1",
        "o3",
        "o4",
    )
    _LEGACY_COMPLETIONS_PREFIXES = (
        "text-davinci",
        "text-curie",
        "text-babbage",
        "text-ada",
        "davinci",
        "curie",
        "babbage",
        "ada",
    )

    def _get_chat_timeout(self) -> int:
        if self._clean_model_id.startswith(self._LONG_TIMEOUT_PREFIXES):
            return 180
        return 60

    def _should_use_responses_endpoint(self) -> bool:
        return self._clean_model_id.startswith(self._RESPONSES_ENDPOINT_PREFIXES)

    @classmethod
    def _strict_json_schema(cls, schema: Dict[str, Any]) -> Dict[str, Any]:
        strict_schema = copy.deepcopy(schema)

        def normalize(value: Any) -> None:
            if isinstance(value, list):
                for item in value:
                    normalize(item)
                return
            if not isinstance(value, dict):
                return

            value.pop("default", None)
            properties = value.get("properties")
            if value.get("type") == "object" or isinstance(properties, dict):
                if isinstance(properties, dict):
                    value["required"] = list(properties)
                value["additionalProperties"] = False

            for child in value.values():
                normalize(child)

        normalize(strict_schema)
        return strict_schema

    def build_json_schema_response_format(
        self,
        *,
        name: str,
        schema: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        if not self._clean_model_id.startswith(self._JSON_SCHEMA_PREFIXES):
            return None
        strict_schema = self._strict_json_schema(schema)
        definition = {
            "name": name,
            "schema": strict_schema,
            "strict": True,
        }
        if self._should_use_responses_endpoint():
            return {"type": "json_schema", **definition}
        return {"type": "json_schema", "json_schema": definition}

    def _should_try_legacy_completions(self) -> bool:
        return self._clean_model_id.startswith(self._LEGACY_COMPLETIONS_PREFIXES)

    def _uses_minimal_reasoning_default(
        self,
        *,
        response_format: Any,
        max_output_tokens: Any,
    ) -> bool:
        """작은 출력 한도의 GPT-5 응답에서 답변 토큰을 남긴다.

        Responses API의 ``max_output_tokens``에는 사용자에게 보이지 않는 추론
        토큰도 포함된다. JSON 출력은 형식 계약 때문에 짧은 응답으로 끝나는 경우가
        많고, 일반 텍스트도 노드가 1,024 token 이하로 제한하면 추론만 수행한 뒤
        ``incomplete``로 끝날 수 있다. 사용자가 reasoning 수준을 직접 지정하지
        않았을 때만 minimal을 안전 기본값으로 적용한다.
        """
        if not self._clean_model_id.startswith(self._MINIMAL_REASONING_MODEL_PREFIX):
            return False
        if self._response_format_requires_json_input(response_format):
            return True
        try:
            output_limit = int(max_output_tokens)
        except (TypeError, ValueError):
            return False
        return 0 < output_limit <= self._MINIMAL_REASONING_OUTPUT_TOKEN_LIMIT

    def _default_reasoning_effort(
        self,
        *,
        response_format: Any,
        max_output_tokens: Any,
    ) -> str | None:
        """모델별 Responses API가 허용하는 가장 낮은 reasoning effort를 고른다."""

        effort = self._REASONING_EFFORT_BY_MODEL.get(self._clean_model_id)
        if effort is None:
            return None
        if not self._uses_minimal_reasoning_default(
            response_format=response_format,
            max_output_tokens=max_output_tokens,
        ):
            return None
        return effort

    def _uses_strict_generation_params(self) -> bool:
        return (
            self._clean_model_id in self._STRICT_MAX_COMPLETION_MODELS
            or self._clean_model_id.startswith(self._RESPONSES_ENDPOINT_PREFIXES)
        )

    def _normalize_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        normalized = dict(params)
        if self._uses_strict_generation_params():
            if "max_tokens" in normalized:
                normalized.setdefault("max_completion_tokens", normalized["max_tokens"])
                normalized.pop("max_tokens", None)
        if self._uses_strict_generation_params():
            if "temperature" in normalized and normalized["temperature"] != 1:
                normalized["temperature"] = 1
        if self._should_use_responses_endpoint():
            for parameter in self._RESPONSES_UNSUPPORTED_GENERATION_PARAMS:
                normalized.pop(parameter, None)
        return normalized

    def _build_completion_prompt(self, messages: List[Dict[str, Any]]) -> str:
        lines: List[str] = []
        for msg in messages:
            role = str(msg.get("role") or "").strip()
            content = msg.get("content")
            if content is None:
                continue
            if role:
                lines.append(f"{role.capitalize()}: {content}")
            else:
                lines.append(str(content))
        if lines and not lines[-1].startswith("Assistant:"):
            lines.append("Assistant:")
        return "\n".join(lines)

    def _convert_completion_response(self, data: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(data, dict):
            return data
        choices = data.get("choices") or []
        if choices and isinstance(choices, list):
            if "message" in choices[0]:
                return data
            if "text" in choices[0]:
                converted_choices = []
                for c in choices:
                    converted_choices.append(
                        {
                            "message": {
                                "role": "assistant",
                                "content": c.get("text", ""),
                            },
                            "finish_reason": c.get("finish_reason"),
                        }
                    )
                return {
                    "choices": converted_choices,
                    "usage": data.get("usage", {}),
                }
        return data

    def _convert_responses_response(self, data: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(data, dict):
            return data
        if "choices" in data:
            return data

        usage = data.get("usage", {})
        if not isinstance(usage, dict):
            usage = {}
        mapped_usage = dict(usage)
        if "prompt_tokens" not in mapped_usage and "input_tokens" in mapped_usage:
            mapped_usage["prompt_tokens"] = mapped_usage.get("input_tokens", 0)
        if "completion_tokens" not in mapped_usage and "output_tokens" in mapped_usage:
            mapped_usage["completion_tokens"] = mapped_usage.get("output_tokens", 0)
        if (
            "total_tokens" not in mapped_usage
            and "prompt_tokens" in mapped_usage
            and "completion_tokens" in mapped_usage
        ):
            mapped_usage["total_tokens"] = (
                mapped_usage.get("prompt_tokens", 0)
                + mapped_usage.get("completion_tokens", 0)
            )

        response_status = data.get("status")
        if response_status in {"incomplete", "failed", "cancelled"}:
            raise ProviderInvocationError(
                "OpenAI Responses 응답이 완료되지 않았습니다: "
                f"status={response_status}, "
                f"summary={self._summarize_responses_response(data)}",
                reason_code="responses_incomplete",
                provider_response_status=response_status,
                failure_phase=ProviderFailurePhase.RESPONSE_RECEIVED,
                usage=mapped_usage,
            )

        text = ""
        if isinstance(data.get("output_text"), str):
            text = data.get("output_text", "")
        else:
            try:
                output = data.get("output") or []
                if not isinstance(output, list):
                    raise TypeError("output must be a list")
                for item in output:
                    if not isinstance(item, dict):
                        raise TypeError("output item must be an object")
                    item_text = item.get("text")
                    if item_text is not None:
                        if not isinstance(item_text, str):
                            raise TypeError("output text must be a string")
                        text += item_text
                    contents = item.get("content") or []
                    if not isinstance(contents, list):
                        raise TypeError("output content must be a list")
                    for content in contents:
                        if isinstance(content, dict):
                            if content.get("type") in ("output_text", "text"):
                                content_text = content.get("text", "")
                                if not isinstance(content_text, str):
                                    raise TypeError("content text must be a string")
                                text += content_text
                        elif isinstance(content, str):
                            text += content
                        else:
                            raise TypeError("content block must be an object or string")
            except (AttributeError, TypeError) as exc:
                raise LLMResponseValidationError(
                    "OpenAI Responses response content is invalid",
                    usage=mapped_usage,
                ) from exc

        if not text.strip():
            raise ProviderInvocationError(
                "OpenAI Responses 응답에 사용할 수 있는 텍스트가 없습니다: "
                f"summary={self._summarize_responses_response(data)}",
                reason_code="responses_empty_text",
                failure_phase=ProviderFailurePhase.RESPONSE_RECEIVED,
                usage=mapped_usage,
            )

        finish_reason = None
        output_items = data.get("output") or []
        if output_items and isinstance(output_items[0], dict):
            finish_reason = output_items[0].get("finish_reason") or output_items[0].get(
                "status"
            )

        return {
            "choices": [
                {
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": finish_reason,
                }
            ],
            "usage": mapped_usage,
        }

    def _content_to_input_blocks(self, content: Any) -> List[Dict[str, Any]]:
        if content is None:
            return []
        if isinstance(content, str):
            return [{"type": "input_text", "text": content}]
        if isinstance(content, list):
            blocks: List[Dict[str, Any]] = []
            for item in content:
                if isinstance(item, str):
                    blocks.append({"type": "input_text", "text": item})
                elif isinstance(item, dict):
                    item_type = item.get("type")
                    if item_type in ("text", "input_text") and "text" in item:
                        blocks.append({"type": "input_text", "text": item.get("text", "")})
                    else:
                        blocks.append(item)
            return blocks
        return [{"type": "input_text", "text": str(content)}]

    def _build_responses_payload(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        instructions_parts: List[str] = []
        input_items: List[Dict[str, Any]] = []

        for msg in messages:
            role = msg.get("role") or "user"
            blocks = self._content_to_input_blocks(msg.get("content"))

            if role == "system":
                for block in blocks:
                    if block.get("type") == "input_text":
                        instructions_parts.append(str(block.get("text", "")))
                continue

            if blocks:
                input_items.append({"role": role, "content": blocks})

        payload: Dict[str, Any] = {}
        instructions = "\n".join([p for p in instructions_parts if p])
        if instructions:
            payload["instructions"] = instructions

        payload["input"] = input_items if input_items else ""
        return payload

    def _response_format_requires_json_input(self, response_format: Any) -> bool:
        if not isinstance(response_format, dict):
            return False
        return response_format.get("type") in {"json_object", "json_schema"}

    def _input_contains_json_word(self, input_value: Any) -> bool:
        if isinstance(input_value, str):
            return "json" in input_value.casefold()
        if not isinstance(input_value, list):
            return False
        for item in input_value:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            blocks = self._content_to_input_blocks(content)
            if any("json" in str(block.get("text", "")).casefold() for block in blocks):
                return True
        return False

    def _ensure_json_instruction_in_responses_input(
        self,
        responses_payload: Dict[str, Any],
    ) -> None:
        text_options = responses_payload.get("text")
        if not isinstance(text_options, dict):
            return
        response_format = text_options.get("format")
        if not self._response_format_requires_json_input(response_format):
            return

        input_value = responses_payload.get("input")
        if self._input_contains_json_word(input_value):
            return

        json_instruction = {
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": "Return the response as a json object.",
                }
            ],
        }
        if isinstance(input_value, list):
            input_value.append(json_instruction)
        else:
            responses_payload["input"] = [json_instruction]

    def _build_responses_request_payload(
        self,
        payload: Dict[str, Any],
        messages: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        responses_payload = dict(payload)
        responses_payload.pop("messages", None)
        responses_payload.update(self._build_responses_payload(messages))

        if "max_output_tokens" not in responses_payload:
            if "max_completion_tokens" in responses_payload:
                responses_payload["max_output_tokens"] = responses_payload[
                    "max_completion_tokens"
                ]
            elif "max_tokens" in responses_payload:
                responses_payload["max_output_tokens"] = responses_payload["max_tokens"]
        responses_payload.pop("max_completion_tokens", None)
        responses_payload.pop("max_tokens", None)

        response_format = responses_payload.pop("response_format", None)
        if response_format:
            text_options = responses_payload.get("text")
            text_options = dict(text_options) if isinstance(text_options, dict) else {}
            text_options.setdefault("format", response_format)
            responses_payload["text"] = text_options

        reasoning_effort = self._default_reasoning_effort(
            response_format=response_format,
            max_output_tokens=responses_payload.get("max_output_tokens"),
        )
        if "reasoning" not in responses_payload and reasoning_effort is not None:
            responses_payload["reasoning"] = {"effort": reasoning_effort}

        self._ensure_json_instruction_in_responses_input(responses_payload)

        return responses_payload

    async def _invoke_responses_endpoint(
        self,
        client: httpx.AsyncClient,
        payload: Dict[str, Any],
        messages: List[Dict[str, Any]],
        timeout_seconds: int = 60,
    ) -> Dict[str, Any]:
        responses_payload = self._build_responses_request_payload(payload, messages)

        try:
            responses_resp = await client.post(
                self.responses_url,
                headers=self._build_headers(),
                json=responses_payload,
                timeout=timeout_seconds,
            )
        except (httpx.RequestError, EgressGuardError) as exc:
            self._raise_provider_transport_error(exc)

        return self._parse_responses_http_response(responses_resp)

    def _invoke_responses_endpoint_sync(
        self,
        client: httpx.Client,
        payload: Dict[str, Any],
        messages: List[Dict[str, Any]],
        timeout_seconds: int = 60,
    ) -> Dict[str, Any]:
        responses_payload = self._build_responses_request_payload(payload, messages)

        try:
            responses_resp = client.post(
                self.responses_url,
                headers=self._build_headers(),
                json=responses_payload,
                timeout=timeout_seconds,
            )
        except (httpx.RequestError, EgressGuardError) as exc:
            self._raise_provider_transport_error(exc)

        return self._parse_responses_http_response(responses_resp)

    def _invoke_chat_endpoint_sync(
        self,
        client: httpx.Client,
        payload: Dict[str, Any],
        messages: List[Dict[str, Any]],
        timeout_seconds: int,
    ) -> Dict[str, Any]:
        """Gevent worker에서 asyncio loop 없이 chat/completions를 호출한다."""
        try:
            response = client.post(
                self.chat_url,
                headers=self._build_headers(),
                json=payload,
                timeout=timeout_seconds,
            )
        except (httpx.RequestError, EgressGuardError) as exc:
            self._raise_provider_transport_error(exc)

        if response.status_code == 400:
            error_text = (response.text or "").lower()
            retry_payload = dict(payload)
            needs_retry = False
            if "max_tokens" in error_text and "max_completion_tokens" in error_text:
                retry_payload["max_completion_tokens"] = retry_payload.get("max_tokens")
                retry_payload.pop("max_tokens", None)
                needs_retry = True
            if "temperature" in error_text and "only the default" in error_text:
                if "temperature" in retry_payload:
                    retry_payload["temperature"] = 1
                    needs_retry = True
            if "unsupported parameter" in error_text:
                for param in (
                    "temperature",
                    "top_p",
                    "presence_penalty",
                    "frequency_penalty",
                    "stop",
                ):
                    if param in error_text and param in retry_payload:
                        retry_payload.pop(param, None)
                        needs_retry = True
            if needs_retry:
                try:
                    response = client.post(
                        self.chat_url,
                        headers=self._build_headers(),
                        json=retry_payload,
                        timeout=timeout_seconds,
                    )
                except (httpx.RequestError, EgressGuardError) as exc:
                    self._raise_provider_transport_error(exc)

        if response.status_code == 404 and "not a chat model" in (
            response.text or ""
        ).lower():
            return self._invoke_non_chat_model_sync(
                client,
                payload=payload,
                messages=messages,
                timeout_seconds=timeout_seconds,
            )

        if response.status_code >= 400:
            try:
                error_data = response.json()
            except ValueError:
                error_data = {}
            if self._has_error_response(error_data):
                self._raise_error_response(error_data, response.status_code)
            self._raise_provider_http_error(response.status_code)

        try:
            data = response.json()
        except ValueError as exc:
            raise ValueError(
                f"{self.provider_name} 응답을 JSON으로 파싱할 수 없습니다."
            ) from exc
        if self._has_error_response(data):
            error_text = str(data.get("error", {}).get("message", "")).lower()
            if "not a chat model" in error_text:
                return self._invoke_non_chat_model_sync(
                    client,
                    payload=payload,
                    messages=messages,
                    timeout_seconds=timeout_seconds,
                )
            self._raise_error_response(data, response.status_code)
        return data

    def _invoke_non_chat_model_sync(
        self,
        client: httpx.Client,
        *,
        payload: Dict[str, Any],
        messages: List[Dict[str, Any]],
        timeout_seconds: int,
    ) -> Dict[str, Any]:
        try:
            return self._invoke_responses_endpoint_sync(
                client=client,
                payload=payload,
                messages=messages,
                timeout_seconds=timeout_seconds,
            )
        except ProviderEndpointUnsupportedError:
            if not self._should_try_legacy_completions():
                raise

        completion_payload = dict(payload)
        completion_payload.pop("messages", None)
        completion_payload["prompt"] = self._build_completion_prompt(messages)
        if "max_completion_tokens" in completion_payload:
            completion_payload.setdefault(
                "max_tokens", completion_payload["max_completion_tokens"]
            )
            completion_payload.pop("max_completion_tokens", None)
        try:
            response = client.post(
                self.completions_url,
                headers=self._build_headers(),
                json=completion_payload,
                timeout=timeout_seconds,
            )
        except (httpx.RequestError, EgressGuardError) as exc:
            self._raise_provider_transport_error(exc)
        if response.status_code >= 400:
            self._raise_provider_http_error(response.status_code)
        try:
            return self._convert_completion_response(response.json())
        except ValueError as exc:
            raise ValueError(
                f"{self.provider_name} 응답을 JSON으로 파싱할 수 없습니다."
            ) from exc

    def _parse_responses_http_response(
        self, responses_resp: httpx.Response
    ) -> Dict[str, Any]:
        if responses_resp.status_code >= 400:
            try:
                responses_data = responses_resp.json()
            except ValueError:
                responses_data = {}
            if self._has_error_response(responses_data):
                self._raise_error_response(responses_data, responses_resp.status_code)
            if responses_resp.status_code in {404, 405}:
                raise ProviderEndpointUnsupportedError(
                    "Provider endpoint is unavailable.",
                    reason_code="provider_endpoint_unsupported",
                    status_code=responses_resp.status_code,
                    failure_phase=ProviderFailurePhase.RESPONSE_RECEIVED,
                )
            self._raise_provider_http_error(responses_resp.status_code)

        try:
            responses_data = responses_resp.json()
        except ValueError as exc:
            raise ValueError(
                f"{self.provider_name} 응답을 JSON으로 파싱할 수 없습니다."
            ) from exc

        if self._has_error_response(responses_data):
            self._raise_error_response(responses_data, responses_resp.status_code)

        return self._convert_responses_response(responses_data)

    def _summarize_responses_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        summary: Dict[str, Any] = {"model": payload.get("model")}
        instructions = payload.get("instructions")
        summary["instructions_len"] = len(instructions) if isinstance(instructions, str) else 0

        input_data = payload.get("input")
        if isinstance(input_data, str):
            summary["input_type"] = "str"
            summary["input_len"] = len(input_data)
        elif isinstance(input_data, list):
            summary["input_type"] = "list"
            summary["input_items"] = len(input_data)
            roles = []
            block_types: Dict[str, int] = {}
            text_len = 0
            for item in input_data:
                if not isinstance(item, dict):
                    continue
                role = item.get("role")
                if role:
                    roles.append(role)
                contents = item.get("content") or []
                for content in contents:
                    if isinstance(content, dict):
                        ctype = content.get("type")
                        if ctype:
                            block_types[ctype] = block_types.get(ctype, 0) + 1
                        if isinstance(content.get("text"), str):
                            text_len += len(content.get("text", ""))
                    elif isinstance(content, str):
                        text_len += len(content)
            summary["roles"] = sorted(set(roles))
            summary["block_types"] = block_types
            summary["content_text_len"] = text_len
        else:
            summary["input_type"] = type(input_data).__name__

        return summary

    def _summarize_responses_response(self, data: Dict[str, Any]) -> Dict[str, Any]:
        summary: Dict[str, Any] = {}
        output_text = data.get("output_text")
        summary["output_text_len"] = len(output_text) if isinstance(output_text, str) else 0

        output = data.get("output") or []
        summary["output_items"] = len(output) if isinstance(output, list) else 0
        output_types: Dict[str, int] = {}
        content_types: Dict[str, int] = {}
        content_text_len = 0
        if isinstance(output, list):
            for item in output:
                if not isinstance(item, dict):
                    continue
                itype = item.get("type")
                if itype:
                    output_types[itype] = output_types.get(itype, 0) + 1
                contents = item.get("content") or []
                if not isinstance(contents, list):
                    continue
                for content in contents:
                    if isinstance(content, dict):
                        ctype = content.get("type")
                        if ctype:
                            content_types[ctype] = content_types.get(ctype, 0) + 1
                        if isinstance(content.get("text"), str):
                            content_text_len += len(content.get("text", ""))
                    elif isinstance(content, str):
                        content_text_len += len(content)
        summary["output_types"] = output_types
        summary["content_types"] = content_types
        summary["content_text_len"] = content_text_len

        usage = data.get("usage")
        if isinstance(usage, dict):
            summary["usage_keys"] = sorted(list(usage.keys()))
        else:
            summary["usage_keys"] = []

        return summary

    def _has_error_response(self, data: Any) -> bool:
        return isinstance(data, dict) and isinstance(data.get("error"), dict)

    async def _handle_not_chat_model(
        self,
        client: httpx.AsyncClient,
        payload: Dict[str, Any],
        messages: List[Dict[str, Any]],
        error_text: str,
        timeout_seconds: int = 60,
    ) -> Dict[str, Any] | None:
        try:
            return await self._invoke_responses_endpoint(
                client=client,
                payload=payload,
                messages=messages,
                timeout_seconds=timeout_seconds,
            )
        except ProviderEndpointUnsupportedError:
            if not self._should_try_legacy_completions():
                raise

        if "completions" in error_text and self._should_try_legacy_completions():
            completion_payload = dict(payload)
            completion_payload.pop("messages", None)
            completion_payload["prompt"] = self._build_completion_prompt(messages)
            if "max_completion_tokens" in completion_payload:
                completion_payload.setdefault(
                    "max_tokens", completion_payload["max_completion_tokens"]
                )
                completion_payload.pop("max_completion_tokens", None)
            try:
                completion_resp = await client.post(
                    self.completions_url,
                    headers=self._build_headers(),
                    json=completion_payload,
                    timeout=timeout_seconds,
                )
            except (httpx.RequestError, EgressGuardError) as exc:
                self._raise_provider_transport_error(exc)

            if completion_resp.status_code >= 400:
                self._raise_provider_http_error(completion_resp.status_code)

            try:
                return self._convert_completion_response(completion_resp.json())
            except ValueError as exc:
                raise ValueError(
                    f"{self.provider_name} 응답을 JSON으로 파싱할 수 없습니다."
                ) from exc

        return None

    def _raise_error_response(self, data: Dict[str, Any], status_code: int | None = None) -> None:
        error_info = data.get("error") if isinstance(data, dict) else None
        if not isinstance(error_info, dict):
            raise ProviderInvocationError(
                f"{self.provider_name} 호출 실패: Unknown error",
                reason_code="provider_http_error",
                status_code=status_code,
                failure_phase=ProviderFailurePhase.RESPONSE_RECEIVED,
            )
        self._raise_provider_http_error(
            status_code or 500,
            provider_error_code=self._safe_provider_error_token(
                error_info.get("code")
            ),
            provider_error_param=self._safe_provider_error_token(
                error_info.get("param")
            ),
        )

    @staticmethod
    def _safe_provider_error_token(value: Any) -> str | None:
        if not isinstance(value, str) or not value or len(value) > 64:
            return None
        if not all(character.isalnum() or character in "._-" for character in value):
            return None
        return value

    def _build_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _parse_embedding_response(self, data: Dict[str, Any]) -> List[float]:
        try:
            # OpenAI response format: { "data": [ { "embedding": [...] } ] }
            return data["data"][0]["embedding"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ValueError(f"{self.provider_name} 임베딩 응답 파싱 실패") from exc

    def _parse_typed_embedding_response(
        self,
        data: Dict[str, Any],
    ) -> EmbeddingProviderResult:
        try:
            vector = tuple(data["data"][0]["embedding"])
            usage = data["usage"]
            if not isinstance(usage, dict):
                raise TypeError
            input_tokens = usage.get("prompt_tokens", usage.get("input_tokens"))
            if (
                isinstance(input_tokens, bool)
                or not isinstance(input_tokens, int)
                or input_tokens < 0
            ):
                raise TypeError
            total_tokens = usage.get("total_tokens")
            if total_tokens is not None and (
                isinstance(total_tokens, bool)
                or not isinstance(total_tokens, int)
                or total_tokens != input_tokens
            ):
                raise TypeError
            return EmbeddingProviderResult(
                vector=vector,
                input_tokens=input_tokens,
            )
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            raise LLMResponseValidationError(
                "Embedding provider response is invalid."
            ) from exc

    def prepare_embedding_invocation(
        self,
        text: str,
    ) -> PreparedEmbeddingInvocation:
        payload = {"model": self.model_id, "input": text}
        return self._seal_embedding_invocation(
            text=text,
            invoke=lambda: self._invoke_typed_embedding_sync(payload),
        )

    def _invoke_typed_embedding_sync(
        self,
        payload: Dict[str, Any],
    ) -> EmbeddingProviderResult:
        try:
            with httpx.Client(
                timeout=30,
                **self._sync_http_client_options(),
            ) as client:
                response = client.post(
                    self.embedding_url,
                    headers=self._build_headers(),
                    json=payload,
                )
        except (httpx.RequestError, EgressGuardError) as exc:
            self._raise_provider_transport_error(exc)
        if response.status_code >= 400:
            self._raise_provider_http_error(response.status_code)
        try:
            data = response.json()
        except ValueError as exc:
            raise LLMResponseValidationError(
                "Embedding provider response is invalid."
            ) from exc
        if not isinstance(data, dict):
            raise LLMResponseValidationError(
                "Embedding provider response is invalid."
            )
        return self._parse_typed_embedding_response(data)

    async def embed(self, text: str) -> List[float]:
        """
        Embeddings API 호출 (비동기).
        """
        payload = {"model": self.model_id, "input": text}

        async with httpx.AsyncClient(
            timeout=30,
            **self._async_http_client_options(),
        ) as client:
            try:
                resp = await client.post(
                    self.embedding_url,
                    headers=self._build_headers(),
                    json=payload,
                )
            except (httpx.RequestError, EgressGuardError) as exc:
                self._raise_provider_transport_error(exc)

        if resp.status_code >= 400:
            self._raise_provider_http_error(resp.status_code)

        try:
            return self._parse_embedding_response(resp.json())
        except ValueError as exc:
            raise ValueError(f"{self.provider_name} 임베딩 응답 파싱 실패") from exc

    def embed_sync(self, text: str) -> List[float]:
        """
        Embeddings API 호출 (동기).

        Workflow Engine은 gevent 실행 컨텍스트에서 노드를 실행한다. 기본
        async wrapper는 일부 Windows/Celery 워커에서 httpx async 요청이
        timeout까지 완료되지 않는 문제가 있어, worker sync path는
        직접 동기 httpx.Client를 사용한다.
        """
        payload = {"model": self.model_id, "input": text}

        try:
            with httpx.Client(
                timeout=30,
                **self._sync_http_client_options(),
            ) as client:
                resp = client.post(
                    self.embedding_url,
                    headers=self._build_headers(),
                    json=payload,
                )
        except (httpx.RequestError, EgressGuardError) as exc:
            self._raise_provider_transport_error(exc)

        if resp.status_code >= 400:
            self._raise_provider_http_error(resp.status_code)

        try:
            return self._parse_embedding_response(resp.json())
        except ValueError as exc:
            raise ValueError(f"{self.provider_name} 임베딩 응답 파싱 실패") from exc

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """
        OpenAI Embeddings API 배치 호출 (최대 2,048개, 비동기)

        Args:
            texts: 임베딩할 텍스트 리스트

        Returns:
            임베딩 벡터 리스트
        """
        if not texts:
            return []

        if len(texts) > 2048:
            raise ValueError(f"OpenAI batch limit is 2,048, got {len(texts)}")

        payload = {"model": self.model_id, "input": texts}

        async with httpx.AsyncClient(
            timeout=60,
            **self._async_http_client_options(),
        ) as client:
            try:
                resp = await client.post(
                    self.embedding_url,
                    headers=self._build_headers(),
                    json=payload,
                )
            except (httpx.RequestError, EgressGuardError) as exc:
                self._raise_provider_transport_error(exc)

        if resp.status_code >= 400:
            self._raise_provider_http_error(resp.status_code)

        try:
            data = resp.json()
            # OpenAI batch response: { "data": [ {"index": 0, "embedding": [...]}, ... ] }
            # index 순서대로 정렬
            sorted_data = sorted(data["data"], key=lambda x: x["index"])
            return [item["embedding"] for item in sorted_data]
        except (ValueError, KeyError, IndexError) as exc:
            raise ValueError("OpenAI 배치 임베딩 응답 파싱 실패") from exc

    def embed_batch_sync(self, texts: List[str]) -> List[List[float]]:
        """OpenAI Embeddings API 배치 호출 (동기)."""

        if not texts:
            return []
        if len(texts) > 2048:
            raise ValueError(f"OpenAI batch limit is 2,048, got {len(texts)}")

        payload = {"model": self.model_id, "input": texts}
        try:
            with httpx.Client(
                timeout=60,
                **self._sync_http_client_options(),
            ) as client:
                resp = client.post(
                    self.embedding_url,
                    headers=self._build_headers(),
                    json=payload,
                )
        except (httpx.RequestError, EgressGuardError) as exc:
            self._raise_provider_transport_error(exc)

        if resp.status_code >= 400:
            self._raise_provider_http_error(resp.status_code)

        try:
            data = resp.json()
            sorted_data = sorted(data["data"], key=lambda item: item["index"])
            return [item["embedding"] for item in sorted_data]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ValueError("OpenAI 배치 임베딩 응답 파싱 실패") from exc

    def invoke_sync(self, messages: List[Dict[str, Any]], **kwargs) -> Dict[str, Any]:
        """
        LLM 호출 (동기).

        Workflow Engine은 gevent 실행 컨텍스트에서 노드를 실행한다. GPT-5.x
        계열은 Responses endpoint를 사용하므로 worker sync path에서 직접
        동기 httpx.Client를 사용해 async event loop wrapper 의존을 피한다.
        """
        request_timeout_seconds = kwargs.pop("request_timeout_seconds", None)
        payload: Dict[str, Any] = {
            "model": self.model_id,
            "messages": messages,
        }
        payload.update(self._normalize_params(kwargs))
        timeout_seconds = self._get_chat_timeout()
        if isinstance(request_timeout_seconds, (int, float)):
            timeout_seconds = min(
                timeout_seconds,
                max(1, int(request_timeout_seconds)),
            )

        with httpx.Client(
            timeout=60,
            **self._sync_http_client_options(),
        ) as client:
            if self._should_use_responses_endpoint():
                return self._invoke_responses_endpoint_sync(
                    client=client,
                    payload=payload,
                    messages=messages,
                    timeout_seconds=timeout_seconds,
                )
            return self._invoke_chat_endpoint_sync(
                client,
                payload=payload,
                messages=messages,
                timeout_seconds=timeout_seconds,
            )

    async def invoke(self, messages: List[Dict[str, Any]], **kwargs) -> Dict[str, Any]:
        """
        Chat Completions 엔드포인트 호출 (비동기).

        Args:
            messages: role/content 형식의 메시지 리스트
            **kwargs: temperature, max_tokens 등 추가 옵션

        Returns:
            응답 JSON 딕셔너리

        Raises:
            ValueError: HTTP 에러/파싱 실패 시
        """
        payload: Dict[str, Any] = {
            "model": self.model_id,
            "messages": messages,
        }
        payload.update(self._normalize_params(kwargs))
        timeout_seconds = self._get_chat_timeout()

        async with httpx.AsyncClient(
            timeout=60,
            **self._async_http_client_options(),
        ) as client:
            if self._should_use_responses_endpoint():
                return await self._invoke_responses_endpoint(
                    client=client,
                    payload=payload,
                    messages=messages,
                    timeout_seconds=timeout_seconds,
                )

            try:
                resp = await client.post(
                    self.chat_url,
                    headers=self._build_headers(),
                    json=payload,
                    timeout=timeout_seconds,
                )
            except (httpx.RequestError, EgressGuardError) as exc:
                self._raise_provider_transport_error(exc)

            if resp.status_code >= 400:
                if resp.status_code == 400:
                    error_text = (resp.text or "").lower()
                    retry_payload = None
                    needs_retry = False

                    if "max_tokens" in error_text and "max_completion_tokens" in error_text:
                        retry_payload = dict(payload) if retry_payload is None else retry_payload
                        retry_payload["max_completion_tokens"] = retry_payload.get(
                            "max_tokens"
                        )
                        retry_payload.pop("max_tokens", None)
                        needs_retry = True

                    if "temperature" in error_text and "only the default" in error_text:
                        retry_payload = dict(payload) if retry_payload is None else retry_payload
                        if "temperature" in retry_payload:
                            retry_payload["temperature"] = 1
                            needs_retry = True

                    if "unsupported parameter" in error_text:
                        unsupported_params = (
                            "temperature",
                            "top_p",
                            "presence_penalty",
                            "frequency_penalty",
                            "stop",
                        )
                        for param in unsupported_params:
                            if param in error_text:
                                retry_payload = (
                                    dict(payload) if retry_payload is None else retry_payload
                                )
                                if param in retry_payload:
                                    retry_payload.pop(param, None)
                                    needs_retry = True

                    if needs_retry and retry_payload is not None:
                        try:
                            # Re-use client for retry
                            resp = await client.post(
                                self.chat_url,
                                headers=self._build_headers(),
                                json=retry_payload,
                                timeout=timeout_seconds,
                            )
                        except (httpx.RequestError, EgressGuardError) as exc:
                            self._raise_provider_transport_error(exc)

                if resp.status_code == 404:
                    error_text = (resp.text or "").lower()
                    if "not a chat model" in error_text:
                        handled = await self._handle_not_chat_model(
                            client=client,
                            payload=payload,
                            messages=messages,
                            error_text=error_text,
                            timeout_seconds=timeout_seconds,
                        )
                        if handled is not None:
                            return handled

                if resp.status_code >= 400:
                    self._raise_provider_http_error(resp.status_code)

            try:
                data = resp.json()
            except ValueError as exc:
                raise ValueError(f"{self.provider_name} 응답을 JSON으로 파싱할 수 없습니다.") from exc

            if self._has_error_response(data):
                error_text = str(data.get("error", {}).get("message", "")).lower()
                if "not a chat model" in error_text:
                    handled = await self._handle_not_chat_model(
                        client=client,
                        payload=payload,
                        messages=messages,
                        error_text=error_text,
                        timeout_seconds=timeout_seconds,
                    )
                    if handled is not None:
                        return handled
                self._raise_error_response(data, resp.status_code)

            return data

    def get_num_tokens(self, messages: List[Dict[str, Any]]) -> int:
        """
        OpenAI tokenizer(tiktoken) 기반 토큰 수 계산.

        제약/주의:
        - tiktoken이 지원하지 않는 모델명은 cl100k_base로 fallback
        - messages는 role/content 키를 포함한 dict 리스트여야 함
        - 반환값은 최소 1
        """
        if self._token_encoder is None:
            try:
                self._token_encoder = tiktoken.encoding_for_model(self.model_id)
            except Exception:
                self._token_encoder = tiktoken.get_encoding("cl100k_base")

        enc = self._token_encoder

        total_tokens = 0
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            total_tokens += len(enc.encode(role))
            total_tokens += len(enc.encode(content))

        return max(1, total_tokens)
