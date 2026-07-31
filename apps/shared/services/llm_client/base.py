"""
LLM 클라이언트의 공통 인터페이스.

각 provider별 클라이언트는 이 추상 클래스를 상속해 구현합니다.
"""

import json
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock
from typing import Any, Callable, Coroutine, Dict, List, Optional

import httpx
from apps.shared.services.egress_guard import EgressGuardError
from apps.shared.services.guarded_http_transport import (
    EgressResponseRejectedError,
    GuardedAsyncHttpTransport,
    GuardedHttpTransport,
)
from apps.shared.services.outbound_operation_policy import (
    LLM_PROVIDER_CALL,
    BoundOutboundOperation,
    require_outbound_operation_profile,
)


def _safe_billing_usage(usage: Any) -> Dict[str, int] | None:
    if not isinstance(usage, dict):
        return None
    prompt_tokens = usage.get("prompt_tokens", usage.get("input_tokens"))
    completion_tokens = usage.get(
        "completion_tokens",
        usage.get("output_tokens"),
    )
    if (
        isinstance(prompt_tokens, bool)
        or not isinstance(prompt_tokens, int)
        or prompt_tokens < 0
        or isinstance(completion_tokens, bool)
        or not isinstance(completion_tokens, int)
        or completion_tokens < 0
    ):
        return None
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
    }


class LLMResponseValidationError(ValueError):
    """Provider output was unusable after a response with optional safe usage."""

    def __init__(self, message: str, *, usage: Any = None):
        super().__init__(message)
        self.usage: Dict[str, int] | None = _safe_billing_usage(usage)


class ProviderFailurePhase(str, Enum):
    BEFORE_SEND = "before_send"
    RESPONSE_RECEIVED = "response_received"
    OUTCOME_UNKNOWN = "outcome_unknown"


class ProviderInvocationError(LLMResponseValidationError):
    """Provider 호출 실패를 원문과 분리한 구조화된 진단 정보와 함께 전달한다."""

    def __init__(
        self,
        message: str,
        *,
        reason_code: str,
        status_code: int | None = None,
        provider_error_code: str | None = None,
        provider_error_param: str | None = None,
        provider_response_status: str | None = None,
        failure_phase: ProviderFailurePhase | None = None,
        usage: Any = None,
    ) -> None:
        super().__init__(message, usage=usage)
        self.reason_code = reason_code
        self.status_code = status_code
        self.provider_error_code = provider_error_code
        self.provider_error_param = provider_error_param
        self.provider_response_status = provider_response_status
        self.failure_phase = failure_phase


class ProviderEndpointUnsupportedError(ProviderInvocationError):
    """A redacted, explicit signal that a provider endpoint is unavailable."""


@dataclass(frozen=True, slots=True)
class EmbeddingProviderResult:
    """Validated provider result without the raw provider response."""

    vector: tuple[float, ...] = field(repr=False)
    input_tokens: int

    def __post_init__(self) -> None:
        if not self.vector or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in self.vector
        ):
            raise LLMResponseValidationError("Embedding result is invalid.")
        if (
            isinstance(self.input_tokens, bool)
            or not isinstance(self.input_tokens, int)
            or self.input_tokens < 0
        ):
            raise LLMResponseValidationError("Embedding usage is invalid.")
        object.__setattr__(self, "vector", tuple(float(value) for value in self.vector))


class PreparedEmbeddingInvocation:
    """Opaque single-use provider request with only safe admission bounds."""

    __slots__ = (
        "_invoke",
        "_lock",
        "_used",
        "canonical_request_bytes",
        "requested_input_tokens",
    )

    def __init__(
        self,
        *,
        canonical_request_bytes: int,
        requested_input_tokens: int,
        invoke: Callable[[], EmbeddingProviderResult],
    ) -> None:
        for value in (canonical_request_bytes, requested_input_tokens):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError("embedding request bounds must be positive integers")
        self.canonical_request_bytes = canonical_request_bytes
        self.requested_input_tokens = requested_input_tokens
        self._invoke = invoke
        self._lock = Lock()
        self._used = False

    def __repr__(self) -> str:
        return "PreparedEmbeddingInvocation(sealed=True, used=%s)" % self._used

    def invoke(self) -> EmbeddingProviderResult:
        with self._lock:
            if self._used:
                raise LLMResponseValidationError(
                    "Prepared embedding invocation was already used."
                )
            self._used = True
        result = self._invoke()
        if not isinstance(result, EmbeddingProviderResult):
            raise LLMResponseValidationError("Embedding result is invalid.")
        return result


class BaseLLMClient(ABC):
    """
    provider별 클라이언트의 기본 구조를 정의합니다.

    Args:
        model_id: 사용할 모델 식별자 (예: gpt-4o)
        credentials: API 호출에 필요한 자격 정보 딕셔너리
    """

    def __init__(self, model_id: str, credentials: Optional[Dict[str, Any]] = None):
        self.model_id = model_id
        self.credentials = credentials or {}
        self._provider_outbound_operation: BoundOutboundOperation | None = None

    def _configure_provider_endpoint(self, base_url: str) -> None:
        self._provider_outbound_operation = require_outbound_operation_profile(
            LLM_PROVIDER_CALL
        ).bind(base_url)

    @property
    def outbound_policy_revision(self) -> str:
        if self._provider_outbound_operation is None:
            raise RuntimeError("Provider endpoint is not configured")
        return self._provider_outbound_operation.profile.revision

    def _sync_http_client_options(self) -> Dict[str, Any]:
        if self._provider_outbound_operation is None:
            raise RuntimeError("Provider endpoint is not configured")
        return {
            "transport": GuardedHttpTransport(
                operation=self._provider_outbound_operation
            ),
            "follow_redirects": False,
            "trust_env": False,
        }

    def _async_http_client_options(self) -> Dict[str, Any]:
        if self._provider_outbound_operation is None:
            raise RuntimeError("Provider endpoint is not configured")
        return {
            "transport": GuardedAsyncHttpTransport(
                operation=self._provider_outbound_operation
            ),
            "follow_redirects": False,
            "trust_env": False,
        }

    @staticmethod
    def _raise_provider_transport_error(exc: BaseException) -> None:
        if isinstance(exc, EgressResponseRejectedError):
            reason_code = "provider_response_rejected"
            failure_phase = ProviderFailurePhase.OUTCOME_UNKNOWN
        elif isinstance(exc, EgressGuardError):
            reason_code = "provider_egress_denied"
            failure_phase = ProviderFailurePhase.BEFORE_SEND
        elif isinstance(
            exc,
            (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout),
        ):
            reason_code = (
                "provider_timeout"
                if isinstance(exc, (httpx.ConnectTimeout, httpx.PoolTimeout))
                else "provider_connection_failed"
            )
            failure_phase = ProviderFailurePhase.BEFORE_SEND
        elif isinstance(exc, httpx.TimeoutException):
            reason_code = "provider_timeout"
            failure_phase = ProviderFailurePhase.OUTCOME_UNKNOWN
        else:
            reason_code = "provider_connection_failed"
            failure_phase = ProviderFailurePhase.OUTCOME_UNKNOWN
        raise ProviderInvocationError(
            "Provider request failed.",
            reason_code=reason_code,
            failure_phase=failure_phase,
        ) from None

    @staticmethod
    def _raise_provider_http_error(
        status_code: int,
        *,
        provider_error_code: str | None = None,
        provider_error_param: str | None = None,
    ) -> None:
        raise ProviderInvocationError(
            f"Provider request failed (status {status_code}).",
            reason_code="provider_http_error",
            status_code=status_code,
            provider_error_code=provider_error_code,
            provider_error_param=provider_error_param,
            failure_phase=ProviderFailurePhase.RESPONSE_RECEIVED,
        )

    @staticmethod
    def _run_coroutine_sync(
        coro_factory: Callable[[], Coroutine[Any, Any, Any]]
    ) -> Any:
        import asyncio

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return BaseLLMClient._run_in_new_event_loop(coro_factory)

        try:
            from gevent import get_hub, monkey
        except ImportError:
            pass
        else:
            if monkey.is_module_patched("threading"):
                # gevent-patched threading still hits asyncio's running-loop guard.
                return get_hub().threadpool.apply(
                    BaseLLMClient._run_in_new_event_loop, (coro_factory,)
                )

        import threading

        result: List[Any] = []
        errors: List[BaseException] = []

        def runner() -> None:
            try:
                result.append(BaseLLMClient._run_in_new_event_loop(coro_factory))
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
        thread.join()

        if errors:
            raise errors[0]
        return result[0] if result else None

    @staticmethod
    def _run_in_new_event_loop(
        coro_factory: Callable[[], Coroutine[Any, Any, Any]]
    ) -> Any:
        import asyncio

        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(coro_factory())
        finally:
            asyncio.set_event_loop(None)
            loop.close()

    @abstractmethod
    async def invoke(self, messages: List[Dict[str, Any]], **kwargs) -> Dict[str, Any]:
        """
        LLM에 메시지를 전달하고 결과를 반환합니다 (비동기).

        Args:
            messages: role/content 형식의 메시지 리스트
            **kwargs: 추가 옵션 (온도, 토큰 제한 등)

        Returns:
            모델 응답을 담은 딕셔너리
        """
        raise NotImplementedError

    def invoke_sync(self, messages: List[Dict[str, Any]], **kwargs) -> Dict[str, Any]:
        """
        LLM에 메시지를 전달하고 결과를 반환합니다 (동기).

        [GEVENT] gevent 환경에서 사용하기 위한 동기 래퍼.
        기본 구현은 새 이벤트 루프를 생성하여 async invoke를 실행합니다.

        Args:
            messages: role/content 형식의 메시지 리스트
            **kwargs: 추가 옵션 (온도, 토큰 제한 등)

        Returns:
            모델 응답을 담은 딕셔너리
        """
        return self._run_coroutine_sync(lambda: self.invoke(messages, **kwargs))

    @abstractmethod
    def get_num_tokens(self, messages: List[Dict[str, Any]]) -> int:
        """
        메시지 리스트가 소비할 토큰 수를 추정/계산합니다.

        Args:
            messages: role/content 형식의 메시지 리스트

        Returns:
            예상 토큰 수
        """
        raise NotImplementedError

    @abstractmethod
    async def embed(self, text: str) -> List[float]:
        """
        단일 텍스트에 대한 임베딩 벡터를 반환합니다.

        Args:
            text: 임베딩할 텍스트

        Returns:
            float 리스트 형태의 벡터
        """
        raise NotImplementedError

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """
        (선택 구현) 다수 텍스트에 대한 임베딩 벡터 리스트를 반환합니다.
        기본 구현은 embed를 반복 호출합니다.

        Args:
            texts: 임베딩할 텍스트 리스트

        Returns:
            벡터 리스트의 리스트
        """
        return [await self.embed(t) for t in texts]

    def embed_sync(self, text: str) -> List[float]:
        """
        단일 텍스트에 대한 임베딩 벡터를 반환합니다 (동기).

        [GEVENT] gevent 환경에서 사용하기 위한 동기 래퍼.
        기본 구현은 새 이벤트 루프를 생성하여 async embed를 실행합니다.

        Args:
            text: 임베딩할 텍스트

        Returns:
            float 리스트 형태의 벡터
        """
        return self._run_coroutine_sync(lambda: self.embed(text))

    def prepare_embedding_invocation(
        self,
        text: str,
    ) -> PreparedEmbeddingInvocation:
        """Prepare a typed embedding call when the provider supports usage."""

        raise ProviderEndpointUnsupportedError(
            "Typed embedding usage is not supported.",
            reason_code="provider_endpoint_unsupported",
            failure_phase=ProviderFailurePhase.BEFORE_SEND,
        )

    def _seal_embedding_invocation(
        self,
        *,
        text: str,
        invoke: Callable[[], EmbeddingProviderResult],
    ) -> PreparedEmbeddingInvocation:
        if (
            not isinstance(text, str)
            or not text
            or not isinstance(self.model_id, str)
            or not self.model_id
        ):
            raise LLMResponseValidationError("Embedding request is invalid.")
        try:
            encoded = json.dumps(
                {"input": text, "model": self.model_id},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise LLMResponseValidationError("Embedding request is invalid.") from exc
        if not encoded:
            raise LLMResponseValidationError("Embedding request is invalid.")
        bound = len(encoded)
        return PreparedEmbeddingInvocation(
            canonical_request_bytes=bound,
            requested_input_tokens=bound,
            invoke=invoke,
        )

    def embed_batch_sync(self, texts: List[str]) -> List[List[float]]:
        """Synchronous batch fallback for clients without a native batch API."""

        return [self.embed_sync(text) for text in texts]
