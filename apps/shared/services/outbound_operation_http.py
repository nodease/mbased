from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any

import httpx
from apps.shared.services.egress_guard import EgressGuardError
from apps.shared.services.guarded_http_transport import (
    EgressResponseRejectedError,
    GuardedAsyncHttpTransport,
    GuardedHttpTransport,
)
from apps.shared.services.outbound_operation_policy import (
    BoundOutboundOperation,
    require_outbound_operation_profile,
)


class OperationHttpFailurePhase(str, Enum):
    BEFORE_SEND = "before_send"
    OUTCOME_UNKNOWN = "outcome_unknown"


class OperationHttpFailure(RuntimeError):
    def __init__(self, reason_code: str, phase: OperationHttpFailurePhase) -> None:
        self.reason_code = reason_code
        self.phase = phase
        super().__init__(reason_code)


@dataclass(frozen=True)
class OperationHttpTimeouts:
    connect_seconds: float
    write_seconds: float
    read_seconds: float
    pool_seconds: float

    def __post_init__(self) -> None:
        for value in (
            self.connect_seconds,
            self.write_seconds,
            self.read_seconds,
            self.pool_seconds,
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError("operation HTTP timeout is invalid")


@dataclass(frozen=True)
class OperationHttpResponse:
    status_code: int
    headers: Mapping[str, str]
    content: bytes
    header_items: tuple[tuple[str, str], ...] = ()

    def json(self) -> Any:
        return json.loads(self.content)

    @property
    def text(self) -> str:
        return self.content.decode("utf-8")

    def header_values(self, name: str) -> tuple[str, ...]:
        lower_name = name.lower()
        if self.header_items:
            return tuple(
                value for key, value in self.header_items if key.lower() == lower_name
            )
        value = self.headers.get(lower_name)
        return () if value is None else (value,)


def _response(response: httpx.Response) -> OperationHttpResponse:
    return OperationHttpResponse(
        status_code=response.status_code,
        headers=MappingProxyType(
            {str(key).lower(): str(value) for key, value in response.headers.items()}
        ),
        content=bytes(response.content),
        header_items=tuple(
            (str(key).lower(), str(value))
            for key, value in response.headers.multi_items()
        ),
    )


def _safe_failure(exc: Exception) -> OperationHttpFailure:
    if isinstance(exc, EgressResponseRejectedError):
        return OperationHttpFailure(
            exc.reason_code,
            OperationHttpFailurePhase.OUTCOME_UNKNOWN,
        )
    if isinstance(exc, EgressGuardError):
        return OperationHttpFailure(
            exc.reason_code,
            OperationHttpFailurePhase.BEFORE_SEND,
        )
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)):
        return OperationHttpFailure(
            "egress.connection_failed",
            OperationHttpFailurePhase.BEFORE_SEND,
        )
    return OperationHttpFailure(
        "egress.request_outcome_unknown",
        OperationHttpFailurePhase.OUTCOME_UNKNOWN,
    )


def _bounded_timeout(
    *,
    operation_limit_seconds: float,
    timeouts: OperationHttpTimeouts | None,
) -> float | httpx.Timeout:
    if timeouts is None:
        return operation_limit_seconds
    if any(
        value > operation_limit_seconds
        for value in (
            timeouts.connect_seconds,
            timeouts.write_seconds,
            timeouts.read_seconds,
            timeouts.pool_seconds,
        )
    ):
        raise OperationHttpFailure(
            "egress.timeout_policy_invalid",
            OperationHttpFailurePhase.BEFORE_SEND,
        )
    return httpx.Timeout(
        connect=timeouts.connect_seconds,
        write=timeouts.write_seconds,
        read=timeouts.read_seconds,
        pool=timeouts.pool_seconds,
    )


class OperationHttpSession:
    def __init__(
        self,
        *,
        operation: BoundOutboundOperation,
        client_factory: Callable[..., httpx.Client],
        timeout: float | httpx.Timeout,
    ) -> None:
        self._operation = operation
        self._client_factory = client_factory
        self._timeout = timeout
        self._is_open = False
        self._client_context: httpx.Client | None = None
        self._client: httpx.Client | None = None

    def __enter__(self) -> OperationHttpSession:
        if self._is_open:
            raise OperationHttpFailure(
                "egress.session_already_open",
                OperationHttpFailurePhase.BEFORE_SEND,
            )
        self._is_open = True
        return self

    def _ensure_client(self) -> httpx.Client:
        if self._client is not None:
            return self._client
        try:
            self._client_context = self._client_factory(
                transport=GuardedHttpTransport(operation=self._operation),
                timeout=self._timeout,
                follow_redirects=False,
                trust_env=False,
            )
            self._client = self._client_context.__enter__()
            return self._client
        except OperationHttpFailure:
            self._client_context = None
            self._client = None
            raise
        except Exception as exc:
            self._client_context = None
            self._client = None
            raise _safe_failure(exc) from None

    def __exit__(self, exc_type, exc, traceback) -> bool:
        client_context = self._client_context
        self._is_open = False
        self._client_context = None
        self._client = None
        if client_context is None:
            return False
        try:
            client_context.__exit__(exc_type, exc, traceback)
            return False
        except Exception as close_exc:
            if exc is not None:
                return False
            raise _safe_failure(close_exc) from None

    def request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str] | None = None,
        json_body: Any | None = None,
        form_data: Mapping[str, Any] | None = None,
        query_params: Mapping[str, Any] | None = None,
    ) -> OperationHttpResponse:
        if not self._is_open:
            raise OperationHttpFailure(
                "egress.session_not_open",
                OperationHttpFailurePhase.BEFORE_SEND,
            )
        try:
            self._operation.validate_url_policy(url)
            client = self._ensure_client()
            response = client.request(
                method,
                url,
                headers=headers,
                json=json_body,
                data=form_data,
                params=query_params,
            )
            return _response(response)
        except OperationHttpFailure:
            raise
        except Exception as exc:
            raise _safe_failure(exc) from None


class OperationHttpRequester:
    def __init__(
        self,
        *,
        client_factory: Callable[..., httpx.Client] = httpx.Client,
    ) -> None:
        self._client_factory = client_factory

    def open_session(
        self,
        *,
        operation_id: str,
        approved_endpoint: str,
        timeouts: OperationHttpTimeouts | None = None,
    ) -> OperationHttpSession:
        try:
            profile = require_outbound_operation_profile(operation_id)
            operation = profile.bind(approved_endpoint)
            return OperationHttpSession(
                operation=operation,
                client_factory=self._client_factory,
                timeout=_bounded_timeout(
                    operation_limit_seconds=profile.policy.timeout_seconds,
                    timeouts=timeouts,
                ),
            )
        except OperationHttpFailure:
            raise
        except Exception as exc:
            raise _safe_failure(exc) from None

    def request(
        self,
        *,
        operation_id: str,
        approved_endpoint: str,
        method: str,
        url: str,
        headers: Mapping[str, str] | None = None,
        json_body: Any | None = None,
        form_data: Mapping[str, Any] | None = None,
        query_params: Mapping[str, Any] | None = None,
        timeouts: OperationHttpTimeouts | None = None,
    ) -> OperationHttpResponse:
        with self.open_session(
            operation_id=operation_id,
            approved_endpoint=approved_endpoint,
            timeouts=timeouts,
        ) as session:
            return session.request(
                method=method,
                url=url,
                headers=headers,
                json_body=json_body,
                form_data=form_data,
                query_params=query_params,
            )


class AsyncOperationHttpRequester:
    def __init__(
        self,
        *,
        client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient,
    ) -> None:
        self._client_factory = client_factory

    async def request(
        self,
        *,
        operation_id: str,
        approved_endpoint: str,
        method: str,
        url: str,
        headers: Mapping[str, str] | None = None,
        json_body: Any | None = None,
        form_data: Mapping[str, Any] | None = None,
        query_params: Mapping[str, Any] | None = None,
        timeouts: OperationHttpTimeouts | None = None,
    ) -> OperationHttpResponse:
        try:
            profile = require_outbound_operation_profile(operation_id)
            operation = profile.bind(approved_endpoint)
            operation.validate_url_policy(url)
            transport = GuardedAsyncHttpTransport(operation=operation)
            async with self._client_factory(
                transport=transport,
                timeout=_bounded_timeout(
                    operation_limit_seconds=profile.policy.timeout_seconds,
                    timeouts=timeouts,
                ),
                follow_redirects=False,
                trust_env=False,
            ) as client:
                response = await client.request(
                    method,
                    url,
                    headers=headers,
                    json=json_body,
                    data=form_data,
                    params=query_params,
                )
                return _response(response)
        except OperationHttpFailure:
            raise
        except Exception as exc:
            raise _safe_failure(exc) from None


__all__ = [
    "AsyncOperationHttpRequester",
    "OperationHttpFailure",
    "OperationHttpFailurePhase",
    "OperationHttpRequester",
    "OperationHttpResponse",
    "OperationHttpSession",
    "OperationHttpTimeouts",
]
