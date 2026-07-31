from __future__ import annotations

import copy
import hashlib
import json
import time
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Any, Mapping
from urllib.parse import urlsplit

import httpx

from apps.workflow_engine.application.outbound_http import (
    OutboundHttpError,
    OutboundHttpFailurePhase,
    OutboundHttpPort,
    OutboundHttpRequest,
    OutboundHttpResponse,
)
from apps.workflow_engine.domain.external_effect import (
    EffectInvocationFailure,
    EffectOutcome,
    ExternalEffectError,
    PreparedEffectRequest,
    PreparedProviderCall,
    ProviderContractProfile,
    ProviderContractRegistry,
    ProviderInvocationResult,
    ProviderReplayCapability,
    canonical_json_pointer_parts,
    provider_contract_registry,
)


_REQUEST_CANONICAL_DOMAIN_V1 = "nodease.generic-http-request.v1"


def _explicit_port_from_url(url: str) -> int | None:
    try:
        authority = urlsplit(url).netloc.rsplit("@", 1)[-1]
    except (TypeError, ValueError):
        return None

    if authority.startswith("["):
        closing_bracket = authority.find("]")
        if closing_bracket < 0:
            return None
        port_separator = authority[closing_bracket + 1 :]
        if not port_separator.startswith(":"):
            return None
        port_text = port_separator[1:]
    else:
        if authority.count(":") != 1:
            return None
        _host, _separator, port_text = authority.rpartition(":")

    if not port_text.isascii() or not port_text.isdecimal():
        return None
    return int(port_text)


def _length_delimited_field(name: str, value: str | bytes) -> bytes:
    name_bytes = name.encode("utf-8")
    value_bytes = value if isinstance(value, bytes) else value.encode("utf-8")
    if len(name_bytes) >= 2**32 or len(value_bytes) >= 2**32:
        raise ValueError("canonical request field is too large")
    return (
        len(name_bytes).to_bytes(4, "big")
        + name_bytes
        + len(value_bytes).to_bytes(4, "big")
        + value_bytes
    )


@dataclass(frozen=True)
class GenericHttpRequest:
    method: str
    url: str
    headers: Mapping[str, str]
    body: str | None
    timeout_seconds: float
    slack_mode: bool = False


@dataclass(frozen=True)
class PreparedGenericHttpRequest:
    method: str
    url: str
    headers: tuple[tuple[str, str], ...]
    body_mode: str
    json_body: Any
    timeout_seconds: float
    slack_mode: bool = False


class GenericHttpEffectAdapter:
    _REQUEST_SEMANTICS = frozenset({"generic_http.request.v1"})
    _RESPONSE_SEMANTICS = frozenset({"generic_http.response.v1"})
    _REPLAY_PROJECTION_SEMANTICS = frozenset({"generic_http.replay_projection.v1"})

    def __init__(
        self,
        *,
        slack_mode: bool = False,
        outbound_http: OutboundHttpPort | None = None,
        contracts: ProviderContractRegistry | None = None,
        active_profile: ProviderContractProfile | None = None,
        historical_profiles: Iterable[ProviderContractProfile] = (),
    ) -> None:
        expected_provider = "slack" if slack_mode else "generic_http"
        expected_operation = (
            "slack.http.request" if slack_mode else "generic_http.request"
        )
        historical_profiles = tuple(historical_profiles)
        if contracts is not None and (
            active_profile is not None or historical_profiles
        ):
            raise ValueError("HTTP adapter contract sources cannot be mixed")
        if contracts is None and (active_profile is not None or historical_profiles):
            active_profile = active_profile or provider_contract_registry().active(
                expected_provider,
                expected_operation,
            )
            contracts = ProviderContractRegistry(
                (active_profile, *historical_profiles),
                active_versions={
                    (expected_provider, expected_operation): (
                        active_profile.contract_version
                    )
                },
            )
        contracts = contracts or provider_contract_registry()
        self._profile = contracts.active(expected_provider, expected_operation)
        profiles = contracts.profiles_for(expected_provider, expected_operation)
        if any(
            profile.provider != expected_provider
            or profile.operation != expected_operation
            for profile in profiles
        ):
            raise ValueError("HTTP adapter profile does not match its operation")
        self._profiles_by_version: dict[str, ProviderContractProfile] = {}
        for profile in profiles:
            if profile.request_semantics not in self._REQUEST_SEMANTICS:
                raise ValueError("unsupported HTTP request semantics")
            if profile.response_semantics not in self._RESPONSE_SEMANTICS:
                raise ValueError("unsupported HTTP response semantics")
            if (
                profile.replay_projection_semantics is not None
                and profile.replay_projection_semantics
                not in self._REPLAY_PROJECTION_SEMANTICS
            ):
                raise ValueError("unsupported HTTP replay projection semantics")
            existing = self._profiles_by_version.get(profile.contract_version)
            if existing is not None and existing != profile:
                raise ValueError("HTTP contract version has conflicting definitions")
            self._profiles_by_version[profile.contract_version] = profile
        self.slack_mode = slack_mode
        self.outbound_http = outbound_http
        self.trace_metadata: dict[str, Any] = {}

    @property
    def profile(self) -> ProviderContractProfile:
        return self._profile

    def _require_known_profile(
        self,
        profile: ProviderContractProfile,
    ) -> ProviderContractProfile:
        known = self._profiles_by_version.get(profile.contract_version)
        if known != profile:
            raise ValueError("unsupported HTTP provider profile")
        return known

    @staticmethod
    def _json_pointer_parts(pointer: str) -> tuple[str, ...]:
        return canonical_json_pointer_parts(pointer)

    @classmethod
    def _body_key_conflicts(cls, body: Any, pointer: str) -> bool:
        parts = cls._json_pointer_parts(pointer)
        current = body
        for part in parts[:-1]:
            if not isinstance(current, dict):
                return True
            if part not in current:
                return False
            current = current[part]
        return not isinstance(current, dict) or parts[-1] in current

    @classmethod
    def _inject_body_key(cls, body: Any, pointer: str, key: str) -> Any:
        if not isinstance(body, dict):
            raise ValueError("body key transport requires a JSON object")
        result = copy.deepcopy(body)
        parts = cls._json_pointer_parts(pointer)
        current = result
        for part in parts[:-1]:
            child = current.get(part)
            if child is None:
                child = {}
                current[part] = child
            if not isinstance(child, dict):
                raise ValueError("body key JSON Pointer cannot be injected")
            current = child
        if parts[-1] in current:
            raise ValueError("reserved provider idempotency field")
        current[parts[-1]] = key
        return result

    @staticmethod
    def _canonical_request_v1(
        request: GenericHttpRequest,
        profile: ProviderContractProfile,
    ) -> tuple[PreparedGenericHttpRequest, bytes, str | None]:
        def reject_non_finite_json(value: str) -> None:
            raise ValueError(f"non-finite JSON value: {value}")

        body_mode = "no_body"
        parsed_body: Any = None
        canonical_body: Any = None
        error_code = None
        if request.body not in {None, ""}:
            body_mode = "json"
            try:
                parsed_body = json.loads(
                    request.body,
                    parse_constant=reject_non_finite_json,
                )
            except (json.JSONDecodeError, ValueError, TypeError):
                body_mode = "invalid_json"
                error_code = "invalid_prepared_request"
            else:
                if parsed_body is None:
                    body_mode = "json_null_no_body"
                canonical_body = parsed_body
        try:
            parsed_url = httpx.URL(request.url)
            if parsed_url.scheme not in {"http", "https"} or not parsed_url.host:
                error_code = "invalid_prepared_request"
            effective_headers = [
                (str(key).lower(), str(value))
                for key, value in httpx.Headers(request.headers).multi_items()
            ]
        except (httpx.InvalidURL, ValueError, TypeError):
            effective_headers = [
                (str(key).lower(), str(value)) for key, value in request.headers.items()
            ]
            error_code = "invalid_prepared_request"
        if body_mode in {"json", "json_null_no_body"}:
            effective_headers = [
                (key, value)
                for key, value in effective_headers
                if key.lower() != "content-type"
            ]
            if body_mode == "json":
                effective_headers.append(("content-type", "application/json"))
        if (
            profile.provider_replay is ProviderReplayCapability.SUPPORTED
            and profile.key_transport == "header"
            and profile.key_field
            and any(
                key.lower() == profile.key_field.lower()
                for key, _value in effective_headers
            )
        ):
            error_code = error_code or "provider_key_field_conflict"
        if (
            profile.provider_replay is ProviderReplayCapability.SUPPORTED
            and profile.key_transport == "body"
            and profile.key_field
        ):
            try:
                if body_mode != "json" or GenericHttpEffectAdapter._body_key_conflicts(
                    parsed_body,
                    profile.key_field,
                ):
                    error_code = error_code or "provider_key_field_conflict"
            except ValueError:
                error_code = error_code or "provider_key_field_conflict"
        canonical_headers = sorted(effective_headers, key=lambda item: item[0])
        prepared = PreparedGenericHttpRequest(
            method=request.method.upper(),
            url=request.url,
            headers=tuple(effective_headers),
            body_mode=body_mode,
            json_body=copy.deepcopy(parsed_body),
            timeout_seconds=request.timeout_seconds,
            slack_mode=request.slack_mode,
        )
        if body_mode == "no_body":
            canonical_body_bytes = b""
        elif body_mode == "invalid_json":
            canonical_body_bytes = (request.body or "").encode("utf-8")
        else:
            canonical_body_bytes = json.dumps(
                canonical_body,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        canonical_fields: list[tuple[str, str | bytes]] = [
            ("domain", _REQUEST_CANONICAL_DOMAIN_V1),
            ("method", request.method.upper()),
            ("target", request.url),
            ("header_count", str(len(canonical_headers))),
        ]
        for header_name, header_value in canonical_headers:
            canonical_fields.extend(
                (
                    ("header_name", header_name),
                    ("header_value", header_value),
                )
            )
        canonical_fields.extend(
            (
                ("body_mode", body_mode),
                ("body", canonical_body_bytes),
            )
        )
        canonical_bytes = b"".join(
            _length_delimited_field(name, value) for name, value in canonical_fields
        )
        return prepared, canonical_bytes, error_code

    def prepare_effect(
        self,
        payload: Any,
        *,
        profile: ProviderContractProfile | None = None,
    ) -> PreparedEffectRequest:
        profile = profile or self.profile
        profile = self._require_known_profile(profile)
        if profile.request_semantics != "generic_http.request.v1":
            raise ValueError("unsupported HTTP request semantics")
        if not isinstance(payload, GenericHttpRequest):
            raise ExternalEffectError(
                "external_effect.invalid_request",
                retryable=False,
            )
        prepared_request, canonical, error_code = self._canonical_request_v1(
            payload,
            profile,
        )
        digest = hashlib.sha256(canonical).hexdigest()
        return PreparedEffectRequest(
            request=prepared_request,
            effect_input_digest=digest,
            profile=profile,
            error_code=error_code,
        )

    def finalize_provider_call(
        self,
        prepared: PreparedEffectRequest,
        idempotency_key: str | None,
    ) -> PreparedProviderCall:
        request = prepared.request
        profile = prepared.profile or self.profile
        profile = self._require_known_profile(profile)
        if not isinstance(request, PreparedGenericHttpRequest):
            raise ValueError("invalid prepared HTTP request")
        headers = list(request.headers)
        if profile.provider_replay is ProviderReplayCapability.SUPPORTED:
            field = profile.key_field
            if not field or not idempotency_key:
                raise ValueError("missing provider idempotency key")
            if profile.key_transport == "header":
                if any(key.lower() == field.lower() for key, _value in headers):
                    raise ValueError("reserved provider idempotency field")
                headers.append((field, idempotency_key))
                request = replace(request, headers=tuple(headers))
            elif profile.key_transport == "body":
                request = replace(
                    request,
                    json_body=self._inject_body_key(
                        request.json_body,
                        field,
                        idempotency_key,
                    ),
                )
            else:
                raise ValueError("unsupported provider key transport")
        elif idempotency_key is not None:
            raise ValueError("system key is forbidden for this provider")
        return PreparedProviderCall(
            request=request,
            idempotency_key=idempotency_key,
            profile=profile,
        )

    def invoke_effect(self, call: PreparedProviderCall) -> ProviderInvocationResult:
        profile = self._require_known_profile(call.profile)
        if profile.response_semantics != "generic_http.response.v1":
            raise ValueError("unsupported HTTP response semantics")
        request = call.request
        if not isinstance(request, PreparedGenericHttpRequest):
            raise EffectInvocationFailure(
                outcome=EffectOutcome.FAILED_BEFORE_EFFECT,
                error_code="invalid_prepared_request",
                retry_before_effect=False,
            )
        started = time.perf_counter()
        if self.outbound_http is None:
            self._set_trace(request, None, started)
            raise EffectInvocationFailure(
                outcome=EffectOutcome.FAILED_BEFORE_EFFECT,
                error_code="invalid_prepared_request",
                retry_before_effect=False,
            )
        try:
            response = self.outbound_http.send(
                OutboundHttpRequest(
                    method=request.method,
                    url=request.url,
                    headers=request.headers,
                    body_mode=request.body_mode,
                    json_body=copy.deepcopy(request.json_body),
                    timeout_seconds=request.timeout_seconds,
                )
            )
        except OutboundHttpError as exc:
            self._set_trace(request, None, started)
            if exc.phase is OutboundHttpFailurePhase.BEFORE_SEND:
                raise EffectInvocationFailure(
                    outcome=EffectOutcome.FAILED_BEFORE_EFFECT,
                    error_code=exc.code,
                    retry_before_effect=exc.retryable_before_send,
                ) from None
            raise EffectInvocationFailure(
                outcome=EffectOutcome.EFFECT_OUTCOME_UNKNOWN,
                error_code=exc.code,
            ) from None

        self._set_trace(request, response, started)
        response_body = response.json_or_text()
        output = {
            "status": response.status_code,
            "data": response_body,
            "headers": dict(httpx.Headers(response.headers)),
        }
        return ProviderInvocationResult(
            output, provider_status_code=response.status_code
        )

    def invoke_read_only(self, request: GenericHttpRequest) -> ProviderInvocationResult:
        prepared = self.prepare_effect(request)
        call = self.finalize_provider_call(prepared, None)
        return self.invoke_effect(call)

    def replay_projection(
        self,
        output: Any,
        *,
        profile: ProviderContractProfile,
    ) -> Any:
        profile = self._require_known_profile(profile)
        if profile.replay_projection_semantics != "generic_http.replay_projection.v1":
            raise ValueError("unsupported HTTP replay projection semantics")
        return copy.deepcopy(output)

    def _set_trace(
        self,
        request: PreparedGenericHttpRequest,
        response: OutboundHttpResponse | None,
        started: float,
    ) -> None:
        latency_ms = int((time.perf_counter() - started) * 1000)
        request_size = 0
        if request.body_mode == "json":
            request_size = len(
                json.dumps(
                    request.json_body,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            )
        response_size = len(response.content) if response is not None else 0
        status_code = getattr(response, "status_code", None)
        if request.slack_mode:
            self.trace_metadata = {
                "http": {
                    "method": request.method,
                    "operation": "slack.http.request",
                    "status_code": status_code,
                    "latency_ms": latency_ms,
                    "request_size": request_size,
                    "response_size": response_size,
                    "retry_count": 0,
                }
            }
            return
        try:
            parsed = httpx.URL(request.url)
            hostname = parsed.host
            explicit_port = _explicit_port_from_url(request.url)
            trace_port = explicit_port if explicit_port is not None else parsed.port
            if trace_port is not None:
                hostname = (
                    f"[{hostname}]:{trace_port}"
                    if ":" in hostname
                    else f"{hostname}:{trace_port}"
                )
            path = parsed.path or "/"
        except (httpx.InvalidURL, ValueError, TypeError):
            hostname = ""
            path = "/"
        self.trace_metadata = {
            "http": {
                "method": request.method,
                "host": hostname,
                "path": path,
                "status_code": status_code,
                "latency_ms": latency_ms,
                "request_size": request_size,
                "response_size": response_size,
                "retry_count": 0,
            }
        }
