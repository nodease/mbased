"""Capability-backed RAG query embedding using shared usage and egress guards."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from threading import Lock
from typing import Any, Mapping

from apps.shared.domain.embedding_model_binding import EmbeddingModelBinding
from apps.shared.domain.provider_execution_capability import (
    CapabilityPurpose,
    PrincipalKind,
    ProviderExecutionBinding,
    RuntimePrincipal,
)
from apps.shared.domain.workflow_node_location import CanonicalWorkflowNodeLocation
from apps.shared.services.llm_client import (
    EmbeddingProviderResult,
    LLMResponseValidationError,
    PreparedEmbeddingInvocation,
    ProviderFailurePhase,
    ProviderInvocationError,
    get_llm_client,
)
from apps.shared.services.llm_credential_config import (
    LLMCredentialConfigError,
    load_llm_credential_config,
)
from apps.shared.services.provider_execution_capability import (
    ProviderExecutionCapabilityAdmissionCommand,
    ProviderExecutionCapabilityIssueCommand,
    ProviderExecutionCapabilityService,
    ProviderExecutionPolicyError,
)
from apps.workflow_engine.application.provider_execution import (
    ProviderExecutionAttribution,
    ProviderExecutionBindingSnapshot,
    ProviderExecutionIdentityContext,
    ProviderExecutionPricingSnapshot,
    ProviderExecutionPrincipal,
    ProviderExecutionPrincipalKind,
    ProviderExecutionPurpose,
    ProviderExecutionUsageContext,
)
from apps.workflow_engine.application.provider_usage import (
    ProviderUsageIntent,
    ProviderUsageRecorder,
    ProviderUsageRuntimeError,
)
from apps.workflow_engine.application.query_embedding_execution import (
    QueryEmbeddingConfigurationError,
    QueryEmbeddingPlan,
    QueryEmbeddingPreflight,
    QueryEmbeddingProviderRequest,
    QueryEmbeddingProviderResult,
)
from sqlalchemy.orm import Session


class _ModelInvokeGuard:
    def __init__(self) -> None:
        self._lock = Lock()
        self._model_ids: set[uuid.UUID] = set()

    def consume(self, model_id: uuid.UUID) -> None:
        with self._lock:
            if model_id in self._model_ids:
                raise QueryEmbeddingConfigurationError()
            self._model_ids.add(model_id)


@dataclass(frozen=True, slots=True)
class _CapabilityQueryEmbeddingPlanState:
    organization_id: uuid.UUID
    workflow_id: uuid.UUID
    deployment_id: uuid.UUID
    deployment_version: int
    node_id: str
    container_path: tuple[tuple[str, str], ...]
    node_location_digest: str
    node_invocation_id: uuid.UUID
    execution_admission_id: uuid.UUID
    execution_subject: RuntimePrincipal
    billing_principal: RuntimePrincipal
    audit_actor: RuntimePrincipal
    query_byte_cap: int
    input_token_cap: int
    cost_cap_microusd: int
    workflow_run_id: uuid.UUID | None
    cost_optimizer_candidate_id: uuid.UUID | None
    invoke_guard: _ModelInvokeGuard


class CapabilityQueryEmbeddingAdapter:
    """Authorize, account for, and invoke one embedding model group."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session],
        usage_recorder: ProviderUsageRecorder,
        capability_service: Any = None,
        credential_loader: Callable[[Any], Mapping[str, Any]] | None = None,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._usage_recorder = usage_recorder
        self._capability_service = capability_service or ProviderExecutionCapabilityService
        self._credential_loader = credential_loader or load_llm_credential_config
        self._client_factory = client_factory or get_llm_client

    def preflight(self, request: QueryEmbeddingPreflight) -> QueryEmbeddingPlan:
        if (
            request.execution_context.get("provider_execution_capability_required")
            is not True
            or request.legacy_credential_user_id is not None
            or self._usage_recorder is None
        ):
            raise QueryEmbeddingConfigurationError()
        return QueryEmbeddingPlan(
            capability_required=True,
            organization_id=request.organization_id,
            node_id=request.node_id,
            state=self._resolve_plan_state(request),
        )

    def invoke(
        self,
        request: QueryEmbeddingProviderRequest,
    ) -> QueryEmbeddingProviderResult:
        state = request.plan.state
        binding = request.model_binding
        if (
            not isinstance(state, _CapabilityQueryEmbeddingPlanState)
            or state.organization_id != request.plan.organization_id
            or state.node_id != request.plan.node_id
            or not isinstance(binding, EmbeddingModelBinding)
            or not isinstance(request.query, str)
            or not request.query
        ):
            raise QueryEmbeddingConfigurationError()
        state.invoke_guard.consume(binding.model_id)

        provider_attempt_id = uuid.uuid5(
            state.execution_admission_id,
            (
                "provider_execution:"
                f"{state.node_invocation_id}:{state.node_location_digest}:"
                f"query_embedding:{binding.model_id}"
            ),
        )
        provider_binding = ProviderExecutionBinding(
            organization_id=state.organization_id,
            workflow_id=state.workflow_id,
            deployment_id=state.deployment_id,
            deployment_version=state.deployment_version,
            node_id=state.node_id,
            node_invocation_id=state.node_invocation_id,
            execution_admission_id=state.execution_admission_id,
            provider_attempt_id=provider_attempt_id,
            purpose=CapabilityPurpose.QUERY_EMBEDDING,
            container_path=state.container_path,
        )
        issue_command = ProviderExecutionCapabilityIssueCommand(
            binding=provider_binding,
            execution_subject=state.execution_subject,
            billing_principal=state.billing_principal,
            audit_actor=state.audit_actor,
            input_token_cap=state.input_token_cap,
            output_token_cap=0,
            cost_cap_microusd=state.cost_cap_microusd,
            policy_model_id=binding.model_id,
        )

        db = self._new_session()
        try:
            try:
                capability = self._capability_service.issue_capability(
                    db,
                    command=issue_command,
                )
                provisional = self._capability_service.admit_capability(
                    db,
                    command=ProviderExecutionCapabilityAdmissionCommand(
                        capability_id=capability.id,
                        capability_revision=capability.revision,
                        binding=provider_binding,
                        requested_input_tokens=0,
                        requested_output_tokens=0,
                        policy_model_id=binding.model_id,
                    ),
                )
                client = self._materialize_client(
                    lease=provisional,
                    expected_binding=binding,
                )
                prepared = client.prepare_embedding_invocation(request.query)
                if not isinstance(prepared, PreparedEmbeddingInvocation):
                    raise QueryEmbeddingConfigurationError()
                if (
                    prepared.canonical_request_bytes > state.query_byte_cap
                    or prepared.requested_input_tokens > state.input_token_cap
                ):
                    raise QueryEmbeddingConfigurationError()
                admitted = self._capability_service.admit_capability(
                    db,
                    command=ProviderExecutionCapabilityAdmissionCommand(
                        capability_id=capability.id,
                        capability_revision=capability.revision,
                        binding=provider_binding,
                        requested_input_tokens=prepared.requested_input_tokens,
                        requested_output_tokens=0,
                        policy_model_id=binding.model_id,
                    ),
                )
                attribution = self._attribution(
                    lease=admitted,
                    expected_binding=binding,
                    admitted_input_tokens=prepared.requested_input_tokens,
                    issue_command=issue_command,
                )
            except ProviderExecutionPolicyError as exc:
                raise QueryEmbeddingConfigurationError() from exc
            except QueryEmbeddingConfigurationError:
                raise
            except (LLMCredentialConfigError, LLMResponseValidationError):
                raise QueryEmbeddingConfigurationError() from None
            except Exception:
                raise QueryEmbeddingConfigurationError() from None
            db.commit()
        finally:
            db.close()

        try:
            attempt = self._usage_recorder.begin(
                ProviderUsageIntent(
                    attribution=attribution,
                    workflow_id=state.workflow_id,
                    workflow_run_id=state.workflow_run_id,
                    node_id=state.node_id,
                    cost_optimizer_candidate_id=state.cost_optimizer_candidate_id,
                )
            )
            if not attempt.durable:
                raise ProviderUsageRuntimeError("provider_usage.durable_required")
            attempt.mark_provider_started()
        except Exception:
            raise QueryEmbeddingConfigurationError() from None

        started_at = time.monotonic()
        try:
            provider_result = prepared.invoke()
        except Exception as exc:
            self._terminalize_failure(attempt, exc)
            raise QueryEmbeddingConfigurationError() from None
        latency_ms = max(0, int((time.monotonic() - started_at) * 1000))
        if not isinstance(provider_result, EmbeddingProviderResult):
            self._mark_unknown(attempt, "provider_usage_invalid")
            raise QueryEmbeddingConfigurationError()
        try:
            attempt.record_success(
                usage={
                    "prompt_tokens": provider_result.input_tokens,
                    "completion_tokens": 0,
                    "total_tokens": provider_result.input_tokens,
                },
                latency_ms=latency_ms,
            )
        except Exception:
            raise QueryEmbeddingConfigurationError() from None
        return QueryEmbeddingProviderResult(
            vector=provider_result.vector,
            input_tokens=provider_result.input_tokens,
            latency_ms=latency_ms,
        )

    def _materialize_client(
        self,
        *,
        lease: Any,
        expected_binding: EmbeddingModelBinding,
    ) -> Any:
        model = lease.model
        provider = lease.provider
        credential = lease.credential
        if (
            uuid.UUID(str(model.id)) != expected_binding.model_id
            or uuid.UUID(str(provider.id)) != expected_binding.provider_id
            or model.provider_id != expected_binding.provider_id
            or model.model_id_for_api_call != expected_binding.model_identifier
            or model.type != "embedding"
        ):
            raise QueryEmbeddingConfigurationError()
        config = self._credential_loader(credential)
        api_key = config.get("apiKey")
        provider_name = getattr(provider, "name", None)
        provider_base_url = getattr(provider, "base_url", None)
        if (
            not isinstance(api_key, str)
            or not api_key.strip()
            or not isinstance(provider_name, str)
            or not provider_name.strip()
            or not isinstance(provider_base_url, str)
            or not provider_base_url
            or provider_base_url != provider_base_url.strip()
        ):
            raise QueryEmbeddingConfigurationError()
        return self._client_factory(
            provider=provider_name,
            model_id=expected_binding.model_identifier,
            credentials={"apiKey": api_key, "baseUrl": provider_base_url},
        )

    def _attribution(
        self,
        *,
        lease: Any,
        expected_binding: EmbeddingModelBinding,
        admitted_input_tokens: int,
        issue_command: ProviderExecutionCapabilityIssueCommand,
    ) -> ProviderExecutionAttribution:
        capability = lease.capability
        principal = capability.credential_principal
        try:
            model_id = uuid.UUID(str(lease.model.id))
            provider_id = uuid.UUID(str(lease.provider.id))
            credential_id = uuid.UUID(str(lease.credential.id))
            principal_id = uuid.UUID(str(principal.reference_id))
            pricing = ProviderExecutionPricingSnapshot(
                revision=str(capability.pricing_revision),
                input_price_per_1k=Decimal(str(lease.model.input_price_1k)),
                output_price_per_1k=Decimal(str(lease.model.output_price_1k)),
            )
        except (AttributeError, InvalidOperation, TypeError, ValueError) as exc:
            raise QueryEmbeddingConfigurationError() from exc
        if (
            model_id != expected_binding.model_id
            or provider_id != expected_binding.provider_id
            or lease.model.model_id_for_api_call != expected_binding.model_identifier
            or principal.kind is not PrincipalKind.USER
        ):
            raise QueryEmbeddingConfigurationError()
        identities = ProviderExecutionIdentityContext(
            execution_subject=self._application_principal(
                issue_command.execution_subject
            ),
            credential_principal=ProviderExecutionPrincipal(
                ProviderExecutionPrincipalKind.USER,
                principal_id,
            ),
            billing_principal=self._application_principal(
                issue_command.billing_principal
            ),
            audit_actor=self._application_principal(issue_command.audit_actor),
        )
        usage_context = ProviderExecutionUsageContext(
            binding=ProviderExecutionBindingSnapshot(
                organization_id=capability.binding.organization_id,
                workflow_id=capability.binding.workflow_id,
                deployment_id=capability.binding.deployment_id,
                deployment_version=capability.binding.deployment_version,
                node_id=capability.binding.node_id,
                node_invocation_id=capability.binding.node_invocation_id,
                execution_admission_id=capability.binding.execution_admission_id,
                provider_attempt_id=capability.binding.provider_attempt_id,
                purpose=ProviderExecutionPurpose.QUERY_EMBEDDING,
                container_path=capability.binding.container_path,
            ),
            capability_id=capability.id,
            capability_revision=capability.revision,
            capability_expires_at=capability.expires_at,
            policy_id=capability.policy_id,
            policy_revision=capability.policy_revision,
            provider_id=provider_id,
            model_id=model_id,
            model_api_id=expected_binding.model_identifier,
            credential_id=credential_id,
            identities=identities,
            permission_revision=capability.permission_revision,
            relation_revision=capability.relation_revision,
            egress_revision=capability.egress_revision,
            pricing_snapshot=pricing,
            input_token_cap=capability.input_token_cap,
            output_token_cap=0,
            cost_cap_microusd=capability.cost_cap_microusd,
            admitted_input_tokens=admitted_input_tokens,
            admitted_output_tokens=0,
        )
        return ProviderExecutionAttribution(
            credential_id=credential_id,
            credential_principal_user_id=principal_id,
            organization_id=capability.binding.organization_id,
            model_id=expected_binding.model_identifier,
            model_db_id=model_id,
            capability_id=capability.id,
            capability_revision=capability.revision,
            pricing_snapshot=pricing,
            usage_context=usage_context,
        )

    @staticmethod
    def _application_principal(
        principal: RuntimePrincipal,
    ) -> ProviderExecutionPrincipal:
        try:
            return ProviderExecutionPrincipal(
                ProviderExecutionPrincipalKind(principal.kind.value),
                principal.reference_id,
            )
        except (TypeError, ValueError) as exc:
            raise QueryEmbeddingConfigurationError() from exc

    @staticmethod
    def _terminalize_failure(attempt: Any, exc: Exception) -> None:
        try:
            if isinstance(exc, ProviderInvocationError):
                if exc.failure_phase is ProviderFailurePhase.BEFORE_SEND:
                    attempt.record_definitive_failure(reason_code="provider_not_sent")
                    return
                if (
                    exc.failure_phase is ProviderFailurePhase.RESPONSE_RECEIVED
                    and exc.status_code in {401, 403}
                ):
                    attempt.record_definitive_failure(reason_code="provider_rejected")
                    return
                reason = (
                    "provider_timeout"
                    if exc.reason_code == "provider_timeout"
                    else "provider_call_failed"
                )
                attempt.mark_outcome_unknown(reason_code=reason)
                return
            reason = (
                "provider_usage_invalid"
                if isinstance(exc, LLMResponseValidationError)
                else "provider_call_failed"
            )
            attempt.mark_outcome_unknown(reason_code=reason)
        except Exception:
            return

    @staticmethod
    def _mark_unknown(attempt: Any, reason_code: str) -> None:
        try:
            attempt.mark_outcome_unknown(reason_code=reason_code)
        except Exception:
            return

    def _new_session(self) -> Session:
        try:
            db = self._session_factory()
        except Exception as exc:
            raise QueryEmbeddingConfigurationError() from exc
        if db is None:
            raise QueryEmbeddingConfigurationError()
        return db

    def _resolve_plan_state(
        self,
        request: QueryEmbeddingPreflight,
    ) -> _CapabilityQueryEmbeddingPlanState:
        control = request.runtime_control
        effect = control.external_effect_context if control is not None else None
        if (
            control is None
            or not control.external_effect_enforced
            or effect is None
            or effect.node_id != request.node_id
            or effect.organization_id != request.organization_id
            or not isinstance(control.execution_id, uuid.UUID)
            or effect.execution_id != control.execution_id
        ):
            raise QueryEmbeddingConfigurationError()
        context = request.execution_context
        deployment_id = self._required_uuid(context, "deployment_id")
        organization_id = self._required_uuid(context, "organization_id")
        workflow_id = self._required_uuid(context, "workflow_id")
        try:
            raw_version = context["workflow_version"]
            if isinstance(raw_version, bool):
                raise ValueError
            deployment_version = int(raw_version)
        except (KeyError, TypeError, ValueError) as exc:
            raise QueryEmbeddingConfigurationError() from exc
        if (
            deployment_version < 1
            or organization_id != request.organization_id
            or organization_id != effect.organization_id
            or workflow_id != effect.workflow_id
        ):
            raise QueryEmbeddingConfigurationError()
        limits = context.get("query_embedding_capability_limits")
        if not isinstance(limits, Mapping):
            raise QueryEmbeddingConfigurationError()
        values = (
            limits.get("query_byte_cap"),
            limits.get("input_token_cap"),
            limits.get("cost_cap_microusd"),
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in values
        ):
            raise QueryEmbeddingConfigurationError()
        execution_subject, audit_actor = self._execution_identities(context)
        try:
            location = CanonicalWorkflowNodeLocation(
                control.binding_container_path,
                request.node_id,
            )
        except (TypeError, ValueError) as exc:
            raise QueryEmbeddingConfigurationError() from exc
        return _CapabilityQueryEmbeddingPlanState(
            organization_id=organization_id,
            workflow_id=workflow_id,
            deployment_id=deployment_id,
            deployment_version=deployment_version,
            node_id=request.node_id,
            container_path=control.binding_container_path,
            node_location_digest=location.digest,
            node_invocation_id=effect.node_invocation_id,
            execution_admission_id=control.execution_id,
            execution_subject=execution_subject,
            billing_principal=RuntimePrincipal.organization(organization_id),
            audit_actor=audit_actor,
            query_byte_cap=values[0],
            input_token_cap=values[1],
            cost_cap_microusd=values[2],
            workflow_run_id=self._optional_uuid(context.get("workflow_run_id")),
            cost_optimizer_candidate_id=self._optional_uuid(
                context.get("cost_optimizer_candidate_id")
            ),
            invoke_guard=_ModelInvokeGuard(),
        )

    @staticmethod
    def _required_uuid(context: Mapping[str, Any], field_name: str) -> uuid.UUID:
        try:
            return uuid.UUID(str(context[field_name]))
        except (KeyError, TypeError, ValueError) as exc:
            raise QueryEmbeddingConfigurationError() from exc

    @staticmethod
    def _optional_uuid(value: Any) -> uuid.UUID | None:
        if value in (None, ""):
            return None
        try:
            return uuid.UUID(str(value))
        except (TypeError, ValueError) as exc:
            raise QueryEmbeddingConfigurationError() from exc

    @staticmethod
    def _execution_identities(
        context: Mapping[str, Any],
    ) -> tuple[RuntimePrincipal, RuntimePrincipal]:
        subject = context.get("execution_subject")
        if isinstance(subject, Mapping):
            subject_type = subject.get("subject_type") or subject.get("type")
            subject_id = subject.get("subject_id") or subject.get("id")
            if subject_type != "user":
                raise QueryEmbeddingConfigurationError()
            try:
                user = RuntimePrincipal.user(uuid.UUID(str(subject_id)))
            except (TypeError, ValueError) as exc:
                raise QueryEmbeddingConfigurationError() from exc
            return user, user
        audience = context.get("provider_execution_audience")
        if audience == "anonymous_public":
            return RuntimePrincipal.anonymous_public(), RuntimePrincipal.public_actor()
        if audience == "system":
            system = RuntimePrincipal.system_actor()
            return system, system
        raise QueryEmbeddingConfigurationError()


__all__ = ["CapabilityQueryEmbeddingAdapter"]
