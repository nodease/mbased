"""Opaque provider client lease used by Workflow execution adapters."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Mapping

from apps.shared.services.llm_client.base import (
    ProviderFailurePhase,
    ProviderInvocationError,
)
from apps.workflow_engine.application.provider_execution import (
    ProviderExecutionAttribution,
    ProviderExecutionConfigurationError,
    ProviderInvocationNotSentError,
    ProviderInvocationOutcomeUnknownError,
    ProviderInvocationRejectedError,
)


class ProviderClientInvocationLease:
    """Keep the SDK client private while exposing one bounded invocation."""

    def __init__(
        self,
        *,
        client: Any,
        messages: tuple[Mapping[str, Any], ...],
        parameters: Mapping[str, Any],
        attribution: ProviderExecutionAttribution | None,
        request_revalidator: Callable[..., ProviderExecutionAttribution | None]
        | None = None,
    ) -> None:
        self._client = client
        self._messages = deepcopy(tuple(dict(message) for message in messages))
        self._parameters = deepcopy(dict(parameters))
        self._attribution = attribution
        self._request_revalidator = request_revalidator
        self._invoked = False
        self._invalidated = False
        self._json_schema_applied = False

    @property
    def attribution(self) -> ProviderExecutionAttribution | None:
        return self._attribution

    def apply_json_schema_response_format(
        self,
        *,
        name: str,
        schema: Mapping[str, Any],
    ) -> bool:
        """지원 provider에만 node JSON schema를 엄격한 응답 형식으로 전달한다."""
        if self._invoked or self._invalidated or self._json_schema_applied:
            raise ProviderExecutionConfigurationError()
        builder = getattr(self._client, "build_json_schema_response_format", None)
        if not callable(builder):
            return False
        try:
            response_format = builder(name=name, schema=dict(schema))
        except Exception:
            return False
        if not isinstance(response_format, dict):
            return False
        final_parameters = deepcopy(self._parameters)
        final_parameters["response_format"] = deepcopy(response_format)
        if self._request_revalidator is not None:
            try:
                attribution = self._request_revalidator(
                    messages=deepcopy(self._messages),
                    parameters=deepcopy(final_parameters),
                )
            except Exception:
                self._invalidated = True
                raise
            self._attribution = attribution
        self._parameters = final_parameters
        self._json_schema_applied = True
        return True

    def invoke(self) -> Mapping[str, Any]:
        if self._invoked or self._invalidated:
            raise ProviderExecutionConfigurationError()
        # Consume before I/O so an unknown provider outcome cannot be replayed.
        self._invoked = True
        try:
            return self._client.invoke_sync(
                messages=deepcopy(list(self._messages)),
                **deepcopy(self._parameters),
            )
        except ProviderInvocationError as exc:
            if exc.failure_phase is ProviderFailurePhase.OUTCOME_UNKNOWN:
                raise ProviderInvocationOutcomeUnknownError() from exc
            if exc.failure_phase is ProviderFailurePhase.BEFORE_SEND:
                raise ProviderInvocationNotSentError() from exc
            if (
                exc.failure_phase is ProviderFailurePhase.RESPONSE_RECEIVED
                and exc.status_code in {401, 403}
            ):
                raise ProviderInvocationRejectedError() from exc
            raise


__all__ = ["ProviderClientInvocationLease"]
