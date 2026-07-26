"""Provider/usage adapter for the fixed Conversation Memory LLM node."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Mapping

from apps.shared.domain.workflow_execution_identity import InvocationSegment
from apps.workflow_engine.application.conversation_memory_execution import (
    ConversationExecutionBinding,
    ConversationProviderPreparation,
    ConversationProviderResult,
)
from apps.workflow_engine.application.provider_execution import (
    ProviderExecutionPlan,
    ProviderExecutionPreflight,
    ProviderExecutionPreparationRequest,
    ProviderExecutionRequest,
    ProviderExecutionRuntime,
    ProviderInvocationNotSentError,
    ProviderInvocationOutcomeUnknownError,
    ProviderInvocationRejectedError,
    ProviderStartCommitRetryableError,
)
from apps.workflow_engine.application.provider_usage import (
    ProviderUsageIntent,
    ProviderUsageRecorder,
    ProviderUsageRuntimeError,
)
from apps.workflow_engine.domain.execution import NodeExecutionControl
from apps.workflow_engine.domain.external_effect import ExternalEffectContext


class ConversationProviderExecutionError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ConversationProviderLimits:
    input_token_cap: int
    output_token_cap: int
    cost_cap_microusd: int

    def __post_init__(self) -> None:
        values = (
            self.input_token_cap,
            self.output_token_cap,
            self.cost_cap_microusd,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in values
        ):
            raise ValueError("conversation provider limits must be positive")


@dataclass(frozen=True, slots=True)
class _PreparedState:
    plan: ProviderExecutionPlan
    prepared: Any


class ConversationMemoryProviderAdapter:
    def __init__(
        self,
        *,
        runtime: ProviderExecutionRuntime,
        usage_recorder: ProviderUsageRecorder,
        limits: ConversationProviderLimits,
    ) -> None:
        self.runtime = runtime
        self.usage_recorder = usage_recorder
        self.limits = limits

    def prepare(
        self,
        *,
        binding: ConversationExecutionBinding,
        admission_id: uuid.UUID,
        execution_id: uuid.UUID,
        node_invocation_id: uuid.UUID,
        node_data: Mapping[str, Any],
        deployment_config: Mapping[str, Any],
    ) -> ConversationProviderPreparation:
        del deployment_config
        model_id = node_data.get("model_id")
        if not isinstance(model_id, str) or not model_id:
            raise ConversationProviderExecutionError(
                "memory.llm_behavior_unsupported"
            )
        control = NodeExecutionControl(
            execution_id=execution_id,
            invocation_path_prefix=(
                InvocationSegment("root", "", str(binding.workflow_id)),
            ),
            external_effect_context=ExternalEffectContext(
                organization_id=binding.organization_id,
                app_id=binding.app_id,
                workflow_id=binding.workflow_id,
                execution_id=execution_id,
                node_invocation_id=node_invocation_id,
                node_id=binding.llm_node_id,
            ),
            external_effect_enforced=True,
        )
        execution_context = {
            "provider_execution_capability_required": True,
            "provider_execution_capability_limits": {
                "input_token_cap": self.limits.input_token_cap,
                "output_token_cap": self.limits.output_token_cap,
                "cost_cap_microusd": self.limits.cost_cap_microusd,
            },
            "provider_execution_audience": "anonymous_public",
            "organization_id": str(binding.organization_id),
            "workflow_id": str(binding.workflow_id),
            "deployment_id": str(binding.deployment_id),
            "workflow_version": binding.deployment_version,
        }
        plan = self.runtime.preflight(
            ProviderExecutionPreflight(
                node_id=binding.llm_node_id,
                configured_model_id=model_id,
                auto_model_routing=False,
                fallback_model_id=None,
                knowledge_enabled=False,
                memory_summary_requested=False,
                client_override=None,
                execution_context=execution_context,
                runtime_control=control,
            )
        )
        prepared = self.runtime.prepare(
            ProviderExecutionPreparationRequest(
                plan=plan,
                model_id=model_id,
                shared_session=None,
            )
        )
        return ConversationProviderPreparation(
            capability_reference=str(prepared.capability_id),
            capability_revision=str(prepared.capability_revision),
            provider_attempt_id=prepared.provider_attempt_id,
            expires_at=prepared.expires_at,
            state=_PreparedState(plan=plan, prepared=prepared),
        )

    def generate(
        self,
        *,
        binding: ConversationExecutionBinding,
        admission_id: uuid.UUID,
        execution_id: uuid.UUID,
        node_invocation_id: uuid.UUID,
        node_data: Mapping[str, Any],
        preparation: ConversationProviderPreparation,
        messages: tuple[Mapping[str, str], ...],
        before_provider_start,
    ) -> ConversationProviderResult:
        del admission_id, execution_id, node_invocation_id
        state = preparation.state
        if not isinstance(state, _PreparedState):
            raise ConversationProviderExecutionError(
                "provider_capability.configuration_required"
            )
        model_id = node_data.get("model_id")
        parameters = node_data.get("parameters", {})
        if not isinstance(model_id, str) or not isinstance(parameters, Mapping):
            raise ConversationProviderExecutionError(
                "memory.llm_behavior_unsupported"
            )
        lease = self.runtime.resolve(
            ProviderExecutionRequest(
                plan=state.plan,
                model_id=model_id,
                messages=tuple(dict(message) for message in messages),
                parameters=dict(parameters),
                shared_session=None,
            )
        )
        attribution = lease.finalize_request()
        if attribution is None or attribution.usage_context is None:
            raise ConversationProviderExecutionError(
                "provider_usage.binding_mismatch"
            )
        try:
            usage_attempt = self.usage_recorder.begin(
                ProviderUsageIntent(
                    attribution=attribution,
                    workflow_id=binding.workflow_id,
                    workflow_run_id=None,
                    node_id=binding.llm_node_id,
                )
            )
        except ProviderUsageRuntimeError as exc:
            if exc.code == "provider_usage.replay_blocked":
                raise ProviderInvocationOutcomeUnknownError() from exc
            raise ConversationProviderExecutionError(exc.code) from exc
        if not usage_attempt.durable or not usage_attempt.operation_reference:
            raise ConversationProviderExecutionError(
                "provider_usage.binding_mismatch"
            )
        current_attribution = lease.revalidate_current_binding()
        if current_attribution != attribution:
            raise ConversationProviderExecutionError(
                "provider_usage.binding_mismatch"
            )
        before_provider_start(usage_attempt.operation_reference)
        try:
            usage_attempt.mark_provider_started()
        except ProviderUsageRuntimeError as exc:
            if exc.code == "provider_usage.replay_blocked":
                raise ProviderInvocationOutcomeUnknownError() from exc
            raise ProviderStartCommitRetryableError() from exc
        try:
            response = lease.invoke()
        except (ProviderInvocationNotSentError, ProviderInvocationRejectedError) as exc:
            try:
                usage_attempt.record_definitive_failure(reason_code=exc.code)
            except ProviderUsageRuntimeError as terminal_error:
                raise ProviderInvocationOutcomeUnknownError() from terminal_error
            raise ConversationProviderExecutionError(exc.code) from exc
        except ProviderInvocationOutcomeUnknownError:
            self._mark_unknown(usage_attempt, "provider_call_failed")
            raise
        except Exception as exc:
            self._mark_unknown(usage_attempt, "provider_call_failed")
            raise ProviderInvocationOutcomeUnknownError() from exc

        try:
            text, usage = _response_projection(response)
        except ProviderInvocationOutcomeUnknownError:
            self._mark_unknown(usage_attempt, "provider_call_failed")
            raise
        return ConversationProviderResult(
            text=text,
            usage=usage,
            state=usage_attempt,
        )

    def record_success(self, result: ConversationProviderResult) -> None:
        usage_attempt = result.state
        if not hasattr(usage_attempt, "record_success"):
            raise ConversationProviderExecutionError(
                "provider_usage.binding_mismatch"
            )
        try:
            latency_value = result.usage.get("latency_ms", 0)
            latency_ms = (
                latency_value
                if isinstance(latency_value, int)
                and not isinstance(latency_value, bool)
                and latency_value >= 0
                else 0
            )
            usage_attempt.record_success(
                usage=result.usage,
                latency_ms=latency_ms,
            )
        except ProviderUsageRuntimeError as exc:
            raise ProviderInvocationOutcomeUnknownError() from exc

    def resume_checkpoint(
        self,
        checkpoint,
        *,
        organization_id: uuid.UUID | None = None,
    ) -> None:
        if organization_id is None:
            raise ConversationProviderExecutionError(
                "provider_usage.binding_mismatch"
            )
        try:
            self.usage_recorder.resume_checkpoint(
                organization_id=organization_id,
                provider_attempt_id=checkpoint.context_attempt_id,
                operation_reference=checkpoint.usage_reference,
            )
        except ProviderUsageRuntimeError as exc:
            raise ConversationProviderExecutionError(exc.code) from exc

    def reconcile_reference_terminal(
        self,
        *,
        organization_id: uuid.UUID,
        provider_attempt_id: uuid.UUID,
        usage_reference: str | None,
    ) -> str:
        try:
            return self.usage_recorder.reconcile_reference_terminal(
                organization_id=organization_id,
                provider_attempt_id=provider_attempt_id,
                operation_reference=usage_reference,
            )
        except ProviderUsageRuntimeError as exc:
            raise ConversationProviderExecutionError(exc.code) from exc

    @staticmethod
    def _mark_unknown(usage_attempt, reason_code: str) -> None:
        try:
            usage_attempt.mark_outcome_unknown(reason_code=reason_code)
        except ProviderUsageRuntimeError:
            return


def _response_projection(response: Mapping[str, Any]) -> tuple[str, Mapping[str, Any]]:
    try:
        text = response["choices"][0]["message"]["content"]
        usage = response["usage"]
    except (KeyError, IndexError, TypeError):
        raise ProviderInvocationOutcomeUnknownError() from None
    if (
        not isinstance(text, str)
        or not text
        or len(text.encode("utf-8")) > 16_384
        or not isinstance(usage, Mapping)
    ):
        raise ProviderInvocationOutcomeUnknownError()
    return text, dict(usage)


__all__ = [
    "ConversationMemoryProviderAdapter",
    "ConversationProviderExecutionError",
    "ConversationProviderLimits",
]
