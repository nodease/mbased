from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import httpx

from apps.shared.services.egress_guard import (
    EgressGuardError,
    EgressGuardPolicy,
    OutboundEgressGuard,
)
from apps.shared.services.guarded_http_transport import (
    EgressResponseRejectedError,
    GuardedHttpTransport,
    GuardedNetworkBackend,
)
from apps.workflow_engine.application.outbound_http import (
    OutboundHttpError,
    OutboundHttpFailurePhase,
    OutboundHttpRequest,
    OutboundHttpResponse,
)

_ALLOWED_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
_ALLOWED_PORTS = frozenset({80, 443})
_TRANSIENT_EGRESS_CODES = frozenset({"egress.dns_resolution_failed"})


def generic_http_egress_policy() -> EgressGuardPolicy:
    return EgressGuardPolicy(
        allowed_schemes=frozenset({"http", "https"}),
        allowed_methods=_ALLOWED_METHODS,
        allowed_ports=_ALLOWED_PORTS,
        deny_private_networks=True,
        deny_url_credentials=True,
        max_redirects=0,
        timeout_seconds=30.0,
        max_header_count=50,
        max_header_name_bytes=128,
        max_header_value_bytes=8192,
        max_header_total_bytes=16 * 1024,
        max_request_bytes=1024 * 1024,
        max_response_bytes=10 * 1024 * 1024,
        force_identity_encoding=True,
        allow_compressed_response=False,
        validate_peer_ip=True,
    )


class GuardedHttpxOutboundAdapter:
    def __init__(
        self,
        *,
        guard: OutboundEgressGuard | None = None,
        transport_factory: Callable[[OutboundEgressGuard], httpx.BaseTransport]
        | None = None,
    ) -> None:
        self._guard = guard or OutboundEgressGuard(generic_http_egress_policy())
        self._transport_factory = transport_factory or GuardedHttpTransport

    def send(self, request: OutboundHttpRequest) -> OutboundHttpResponse:
        response_received = False
        try:
            self._validate_request(request)
            headers = list(request.headers)
            if self._guard.policy.force_identity_encoding:
                headers.append(("Accept-Encoding", "identity"))

            transport = self._transport_factory(self._guard)
            timeout = httpx.Timeout(request.timeout_seconds)
            with httpx.Client(
                transport=transport,
                timeout=timeout,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                kwargs: dict[str, Any] = {
                    "method": request.method,
                    "url": request.url,
                    "headers": headers,
                }
                if request.body_mode == "json":
                    kwargs["json"] = request.json_body
                else:
                    kwargs["content"] = None
                with client.stream(**kwargs) as response:
                    response_received = True
                    self._guard.validate_response_headers(response.headers)
                    content = self._read_capped(response)
                    return OutboundHttpResponse(
                        status_code=response.status_code,
                        headers=tuple(response.headers.multi_items()),
                        content=content,
                    )
        except OutboundHttpError:
            raise
        except EgressResponseRejectedError:
            raise OutboundHttpError(
                "response_lost",
                phase=OutboundHttpFailurePhase.OUTCOME_UNKNOWN,
            ) from None
        except EgressGuardError as exc:
            if response_received:
                raise OutboundHttpError(
                    "response_lost",
                    phase=OutboundHttpFailurePhase.OUTCOME_UNKNOWN,
                ) from None
            raise OutboundHttpError(
                (
                    "connection_failed"
                    if exc.reason_code in _TRANSIENT_EGRESS_CODES
                    else "invalid_prepared_request"
                ),
                phase=OutboundHttpFailurePhase.BEFORE_SEND,
                retryable_before_send=exc.reason_code in _TRANSIENT_EGRESS_CODES,
            ) from None
        except (httpx.InvalidURL, httpx.UnsupportedProtocol, httpx.LocalProtocolError):
            raise OutboundHttpError(
                "invalid_prepared_request",
                phase=OutboundHttpFailurePhase.BEFORE_SEND,
            ) from None
        except (httpx.ConnectTimeout, httpx.ConnectError, httpx.PoolTimeout):
            raise OutboundHttpError(
                "connection_failed",
                phase=OutboundHttpFailurePhase.BEFORE_SEND,
                retryable_before_send=True,
            ) from None
        except httpx.RequestError:
            raise OutboundHttpError(
                "response_lost",
                phase=OutboundHttpFailurePhase.OUTCOME_UNKNOWN,
            ) from None

    def _validate_request(self, request: OutboundHttpRequest) -> None:
        if request.body_mode not in {"json", "no_body", "json_null_no_body"}:
            raise EgressGuardError("egress.unsupported_request_body")
        if not 0 < request.timeout_seconds <= self._guard.policy.timeout_seconds:
            raise EgressGuardError("egress.invalid_timeout")
        self._guard.validate_method(request.method)
        try:
            has_fragment = bool(urlsplit(request.url).fragment)
        except (TypeError, ValueError) as exc:
            raise EgressGuardError("egress.invalid_url") from exc
        if has_fragment:
            raise EgressGuardError("egress.invalid_url")

        header_names = [name.lower() for name, _value in request.headers]
        if {"accept-encoding", "proxy-authorization"} & set(header_names):
            raise EgressGuardError("egress.invalid_header")
        validated_headers = self._guard.validate_request_header_items(
            request.headers,
            reject_hop_by_hop=True,
        )
        if validated_headers != request.headers:
            raise EgressGuardError("egress.invalid_header")
        try:
            for name, value in validated_headers:
                name.encode("ascii")
                value.encode("ascii")
        except UnicodeEncodeError as exc:
            raise EgressGuardError("egress.invalid_header") from exc
        self._guard.validate_request_body(
            json_body=request.json_body if request.body_mode == "json" else None,
            data=None,
        )
        self._guard.validate_url(request.url)

    def _read_capped(self, response: httpx.Response) -> bytes:
        if response.is_stream_consumed:
            content = response.content
            if len(content) > self._guard.policy.max_response_bytes:
                raise EgressGuardError("egress.response_too_large")
            return content
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_raw():
            total += len(chunk)
            if total > self._guard.policy.max_response_bytes:
                raise EgressGuardError("egress.response_too_large")
            chunks.append(chunk)
        return b"".join(chunks)


__all__ = [
    "GuardedHttpTransport",
    "GuardedHttpxOutboundAdapter",
    "GuardedNetworkBackend",
    "generic_http_egress_policy",
]
