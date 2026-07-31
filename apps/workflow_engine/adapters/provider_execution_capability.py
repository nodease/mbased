"""Capability-authorized provider execution strategy."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from threading import Lock
from typing import Any, Callable, Mapping

from sqlalchemy.orm import Session

from apps.shared.domain.provider_execution_capability import (
    CapabilityPurpose,
    PrincipalKind,
    ProviderExecutionBinding,
    RuntimeIdentityContext,
    RuntimePrincipal,
)
from apps.shared.domain.provider_usage_ledger import ProviderUsageIntentSnapshot
from apps.shared.services.llm_client import get_llm_client
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
from apps.workflow_engine.adapters.provider_invocation import (
    ProviderClientInvocationLease,
)
from apps.workflow_engine.application.provider_execution import (
    LLMCredentialNotAvailableError,
    ProviderExecutionAttribution,
    ProviderExecutionAuditActor,
    ProviderExecutionAuditActorKind,
    ProviderExecutionBindingSnapshot,
    ProviderExecutionConfigurationError,
    ProviderExecutionIdentityContext,
    ProviderExecutionPlan,
    ProviderExecutionPreflight,
    ProviderExecutionPricingSnapshot,
    ProviderExecutionPrincipal,
    ProviderExecutionPrincipalKind,
    ProviderExecutionPurpose,
    ProviderExecutionRequest,
    ProviderExecutionUsageContext,
)


class _SingleUseResolveGuard:
    def __init__(self) -> None:
        self._lock = Lock()
        self._consumed = False

    def consume(self) -> None:
        with self._lock:
            if self._consumed:
                raise ProviderExecutionConfigurationError()
            self._consumed = True


@dataclass(frozen=True, slots=True)
class _CapabilityPlanState:
    issue_command: ProviderExecutionCapabilityIssueCommand
    configured_model_id: str
    resolve_guard: _SingleUseResolveGuard


def provider_visible_request_bounds(
    *,
    messages: tuple[Mapping[str, Any], ...],
    parameters: Mapping[str, Any],
    output_token_cap: int,
) -> tuple[int, int, dict[str, Any]]:
    """Normalize and conservatively bound the complete provider request."""

    normalized = dict(parameters)
    if "model" in normalized:
        raise ProviderExecutionConfigurationError()

    for field_name in ("n", "best_of"):
        value = normalized.get(field_name, 1)
        if isinstance(value, bool) or not isinstance(value, int) or value != 1:
            raise ProviderExecutionConfigurationError()

    if {"max_completion_tokens", "max_output_tokens"}.intersection(normalized):
        raise ProviderExecutionConfigurationError()

    output_tokens = normalized.get("max_tokens")
    if output_tokens is None:
        if isinstance(output_token_cap, bool) or output_token_cap <= 0:
            raise ProviderExecutionConfigurationError()
        output_tokens = output_token_cap
        normalized["max_tokens"] = output_tokens
    if (
        isinstance(output_tokens, bool)
        or not isinstance(output_tokens, int)
        or output_tokens <= 0
    ):
        raise ProviderExecutionConfigurationError()

    try:
        encoded = json.dumps(
            {
                "messages": [dict(message) for message in messages],
                "parameters": normalized,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProviderExecutionConfigurationError() from exc
    if not encoded:
        raise ProviderExecutionConfigurationError()
    return len(encoded), output_tokens, normalized


def provider_usage_intent_snapshot(
    context: ProviderExecutionUsageContext,
) -> ProviderUsageIntentSnapshot:
    """Translate the Workflow-owned usage context at the capability boundary."""

    binding = context.binding
    identities = context.identities
    return ProviderUsageIntentSnapshot(
        binding=ProviderExecutionBinding(
            organization_id=binding.organization_id,
            workflow_id=binding.workflow_id,
            deployment_id=binding.deployment_id,
            deployment_version=binding.deployment_version,
            node_id=binding.node_id,
            node_invocation_id=binding.node_invocation_id,
            execution_admission_id=binding.execution_admission_id,
            provider_attempt_id=binding.provider_attempt_id,
            purpose=CapabilityPurpose(binding.purpose.value),
            container_path=binding.container_path,
        ),
        capability_id=context.capability_id,
        capability_revision=context.capability_revision,
        policy_id=context.policy_id,
        policy_revision=context.policy_revision,
        provider_id=context.provider_id,
        model_id=context.model_id,
        model_api_id=context.model_api_id,
        credential_id=context.credential_id,
        identities=RuntimeIdentityContext(
            execution_subject=_shared_principal(identities.execution_subject),
            credential_principal=_shared_principal(
                identities.credential_principal
            ),
            billing_principal=_shared_principal(identities.billing_principal),
            audit_actor=_shared_principal(identities.audit_actor),
        ),
        permission_revision=context.permission_revision,
        relation_revision=context.relation_revision,
        egress_revision=context.egress_revision,
        pricing_revision=context.pricing_snapshot.revision,
        input_price_per_1k=str(context.pricing_snapshot.input_price_per_1k),
        output_price_per_1k=str(context.pricing_snapshot.output_price_per_1k),
        input_token_cap=context.input_token_cap,
        output_token_cap=context.output_token_cap,
        cost_cap_microusd=context.cost_cap_microusd,
        admitted_input_tokens=context.admitted_input_tokens,
        admitted_output_tokens=context.admitted_output_tokens,
        expires_at=context.capability_expires_at,
    )


def _shared_principal(
    principal: ProviderExecutionPrincipal,
) -> RuntimePrincipal:
    return RuntimePrincipal(
        PrincipalKind(principal.kind.value),
        principal.reference_id,
    )


class CapabilityProviderExecutionAdapter:
    """Translate Workflow execution controls into authoritative capability use."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session],
        capability_service: Any = None,
        credential_loader: Callable[[Any], Mapping[str, Any]] | None = None,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._capability_service = (
            capability_service
            if capability_service is not None
            else ProviderExecutionCapabilityService
        )
        self._credential_loader = credential_loader or load_llm_credential_config
        self._client_factory = client_factory or get_llm_client

    def preflight(
        self,
        request: ProviderExecutionPreflight,
    ) -> ProviderExecutionPlan:
        if (
            request.execution_context.get("provider_execution_capability_required")
            is not True
            or request.auto_model_routing
            or request.fallback_model_id
            or request.client_override is not None
        ):
            raise ProviderExecutionConfigurationError()
        command = self._issue_command(request)
        return ProviderExecutionPlan(
            fixed_model_id=request.configured_model_id,
            allow_legacy_memory_summary=False,
            audit_actor=self._application_audit_actor(command.audit_actor),
            routing_metadata={"provider_execution_capability": "required"},
            state=_CapabilityPlanState(
                issue_command=command,
                configured_model_id=request.configured_model_id,
                resolve_guard=_SingleUseResolveGuard(),
            ),
        )

    def resolve(
        self,
        request: ProviderExecutionRequest,
    ) -> ProviderClientInvocationLease:
        state = request.plan.state
        if not isinstance(state, _CapabilityPlanState):
            raise ProviderExecutionConfigurationError()
        if request.model_id != state.configured_model_id:
            raise ProviderExecutionConfigurationError()
        state.resolve_guard.consume()
        issue_command = state.issue_command
        input_tokens, output_tokens, parameters = provider_visible_request_bounds(
            messages=request.messages,
            parameters=request.parameters,
            output_token_cap=issue_command.output_token_cap,
        )
        db = self._new_isolated_session(request.shared_session)
        try:
            try:
                capability = self._capability_service.issue_capability(
                    db,
                    command=issue_command,
                )
                lease = self._capability_service.admit_capability(
                    db,
                    command=ProviderExecutionCapabilityAdmissionCommand(
                        capability_id=capability.id,
                        capability_revision=capability.revision,
                        binding=issue_command.binding,
                        requested_input_tokens=input_tokens,
                        requested_output_tokens=output_tokens,
                    ),
                )
            except ProviderExecutionPolicyError as exc:
                raise LLMCredentialNotAvailableError(
                    f"provider_capability_{exc.code}",
                    "Provider execution capability is not available.",
                    organization_id=issue_command.binding.organization_id,
                ) from exc

            admitted_model_id = getattr(lease.model, "model_id_for_api_call", None)
            if admitted_model_id != request.model_id:
                raise LLMCredentialNotAvailableError(
                    "provider_capability_model_mismatch",
                    "Provider execution capability is not available.",
                    organization_id=issue_command.binding.organization_id,
                )
            principal = getattr(lease.capability, "credential_principal", None)
            try:
                credential_id = uuid.UUID(str(lease.credential.id))
                model_db_id = uuid.UUID(str(lease.model.id))
                principal_id = uuid.UUID(str(principal.reference_id))
                pricing_snapshot = ProviderExecutionPricingSnapshot(
                    revision=str(lease.capability.pricing_revision),
                    input_price_per_1k=Decimal(str(lease.model.input_price_1k)),
                    output_price_per_1k=Decimal(str(lease.model.output_price_1k)),
                )
            except (AttributeError, InvalidOperation, TypeError, ValueError):
                raise LLMCredentialNotAvailableError(
                    "provider_capability_attribution_invalid",
                    "Provider execution capability is not available.",
                    organization_id=issue_command.binding.organization_id,
                ) from None
            if principal.kind is not PrincipalKind.USER:
                raise LLMCredentialNotAvailableError(
                    "provider_capability_attribution_invalid",
                    "Provider execution capability is not available.",
                    organization_id=issue_command.binding.organization_id,
                )

            try:
                config = self._credential_loader(lease.credential)
                api_key = config.get("apiKey")
                base_url = lease.provider.base_url
                provider_name = lease.provider.name
                if (
                    not isinstance(api_key, str)
                    or not api_key.strip()
                    or not isinstance(base_url, str)
                    or not base_url
                    or base_url != base_url.strip()
                    or not isinstance(provider_name, str)
                    or not provider_name.strip()
                ):
                    raise ValueError("invalid provider materialization input")
                client = self._client_factory(
                    provider=provider_name,
                    model_id=admitted_model_id,
                    credentials={"apiKey": api_key, "baseUrl": base_url},
                )
            except (LLMCredentialConfigError, TypeError, ValueError, AttributeError):
                raise LLMCredentialNotAvailableError(
                    "credential_config_invalid",
                    "Provider execution capability is not available.",
                    organization_id=issue_command.binding.organization_id,
                ) from None
            except Exception:
                raise LLMCredentialNotAvailableError(
                    "provider_client_initialization_failed",
                    "Provider execution capability is not available.",
                    organization_id=issue_command.binding.organization_id,
                ) from None

            attribution = ProviderExecutionAttribution(
                credential_id=credential_id,
                credential_principal_user_id=principal_id,
                organization_id=issue_command.binding.organization_id,
                model_id=admitted_model_id,
                model_db_id=model_db_id,
                capability_id=capability.id,
                capability_revision=capability.revision,
                pricing_snapshot=pricing_snapshot,
                usage_context=ProviderExecutionUsageContext(
                    binding=ProviderExecutionBindingSnapshot(
                        organization_id=capability.binding.organization_id,
                        workflow_id=capability.binding.workflow_id,
                        deployment_id=capability.binding.deployment_id,
                        deployment_version=capability.binding.deployment_version,
                        node_id=capability.binding.node_id,
                        node_invocation_id=capability.binding.node_invocation_id,
                        execution_admission_id=(
                            capability.binding.execution_admission_id
                        ),
                        provider_attempt_id=capability.binding.provider_attempt_id,
                        purpose=ProviderExecutionPurpose(
                            capability.binding.purpose.value
                        ),
                        container_path=capability.binding.container_path,
                    ),
                    capability_id=capability.id,
                    capability_revision=capability.revision,
                    capability_expires_at=capability.expires_at,
                    policy_id=capability.policy_id,
                    policy_revision=capability.policy_revision,
                    provider_id=capability.provider_id,
                    model_id=capability.model_id,
                    model_api_id=admitted_model_id,
                    credential_id=capability.credential_id,
                    identities=ProviderExecutionIdentityContext(
                        execution_subject=self._application_principal(
                            issue_command.execution_subject
                        ),
                        credential_principal=self._application_principal(principal),
                        billing_principal=self._application_principal(
                            issue_command.billing_principal
                        ),
                        audit_actor=self._application_principal(
                            issue_command.audit_actor
                        ),
                    ),
                    permission_revision=capability.permission_revision,
                    relation_revision=capability.relation_revision,
                    egress_revision=capability.egress_revision,
                    pricing_snapshot=pricing_snapshot,
                    input_token_cap=capability.input_token_cap,
                    output_token_cap=capability.output_token_cap,
                    cost_cap_microusd=capability.cost_cap_microusd,
                    admitted_input_tokens=input_tokens,
                    admitted_output_tokens=output_tokens,
                ),
            )
            db.commit()
        finally:
            db.close()

        return ProviderClientInvocationLease(
            client=client,
            messages=request.messages,
            parameters=parameters,
            attribution=attribution,
            request_revalidator=lambda *, messages, parameters: (
                self._readmit_final_request(
                    shared_session=request.shared_session,
                    issue_command=issue_command,
                    attribution=attribution,
                    messages=messages,
                    parameters=parameters,
                )
            ),
        )

    def _readmit_final_request(
        self,
        *,
        shared_session: Any | None,
        issue_command: ProviderExecutionCapabilityIssueCommand,
        attribution: ProviderExecutionAttribution,
        messages: tuple[Mapping[str, Any], ...],
        parameters: Mapping[str, Any],
    ) -> ProviderExecutionAttribution:
        usage_context = attribution.usage_context
        if usage_context is None or attribution.capability_id is None:
            raise ProviderExecutionConfigurationError()
        input_tokens, output_tokens, _ = provider_visible_request_bounds(
            messages=messages,
            parameters=parameters,
            output_token_cap=usage_context.output_token_cap,
        )
        db = self._new_isolated_session(shared_session)
        try:
            try:
                lease = self._capability_service.admit_capability(
                    db,
                    command=ProviderExecutionCapabilityAdmissionCommand(
                        capability_id=attribution.capability_id,
                        capability_revision=attribution.capability_revision,
                        binding=issue_command.binding,
                        requested_input_tokens=input_tokens,
                        requested_output_tokens=output_tokens,
                    ),
                )
            except ProviderExecutionPolicyError as exc:
                raise LLMCredentialNotAvailableError(
                    f"provider_capability_{exc.code}",
                    "Provider execution capability is not available.",
                    organization_id=issue_command.binding.organization_id,
                ) from exc
            self._require_same_admission(
                lease=lease,
                attribution=attribution,
                issue_command=issue_command,
            )
            db.commit()
        finally:
            db.close()
        return replace(
            attribution,
            usage_context=replace(
                usage_context,
                admitted_input_tokens=input_tokens,
                admitted_output_tokens=output_tokens,
            ),
        )

    @staticmethod
    def _require_same_admission(
        *,
        lease: Any,
        attribution: ProviderExecutionAttribution,
        issue_command: ProviderExecutionCapabilityIssueCommand,
    ) -> None:
        usage_context = attribution.usage_context
        pricing_snapshot = attribution.pricing_snapshot
        try:
            capability = lease.capability
            principal = capability.credential_principal
            matches = (
                uuid.UUID(str(capability.id)) == attribution.capability_id
                and capability.revision == attribution.capability_revision
                and capability.binding == issue_command.binding
                and uuid.UUID(str(lease.credential.id)) == attribution.credential_id
                and uuid.UUID(str(lease.provider.id)) == usage_context.provider_id
                and uuid.UUID(str(lease.model.id)) == attribution.model_db_id
                and lease.model.model_id_for_api_call == attribution.model_id
                and principal.kind is PrincipalKind.USER
                and uuid.UUID(str(principal.reference_id))
                == attribution.credential_principal_user_id
                and str(capability.pricing_revision) == pricing_snapshot.revision
                and Decimal(str(lease.model.input_price_1k))
                == pricing_snapshot.input_price_per_1k
                and Decimal(str(lease.model.output_price_1k))
                == pricing_snapshot.output_price_per_1k
            )
        except (AttributeError, InvalidOperation, TypeError, ValueError):
            matches = False
        if not matches:
            raise LLMCredentialNotAvailableError(
                "provider_capability_attribution_invalid",
                "Provider execution capability is not available.",
                organization_id=issue_command.binding.organization_id,
            )

    def _new_isolated_session(self, shared_session: Any | None) -> Session:
        try:
            db = self._session_factory()
        except Exception as exc:
            raise ProviderExecutionConfigurationError() from exc
        if db is None or db is shared_session:
            raise ProviderExecutionConfigurationError()
        return db

    @staticmethod
    def _application_audit_actor(
        principal: RuntimePrincipal,
    ) -> ProviderExecutionAuditActor:
        try:
            kind = ProviderExecutionAuditActorKind(principal.kind.value)
            return ProviderExecutionAuditActor(
                kind=kind,
                reference_id=principal.reference_id,
            )
        except ValueError as exc:
            raise ProviderExecutionConfigurationError() from exc

    @staticmethod
    def _application_principal(
        principal: RuntimePrincipal,
    ) -> ProviderExecutionPrincipal:
        try:
            return ProviderExecutionPrincipal(
                kind=ProviderExecutionPrincipalKind(principal.kind.value),
                reference_id=principal.reference_id,
            )
        except ValueError as exc:
            raise ProviderExecutionConfigurationError() from exc

    @staticmethod
    def _required_uuid(
        context: Mapping[str, Any],
        field_name: str,
    ) -> uuid.UUID:
        try:
            return uuid.UUID(str(context[field_name]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderExecutionConfigurationError() from exc

    def _issue_command(
        self,
        request: ProviderExecutionPreflight,
    ) -> ProviderExecutionCapabilityIssueCommand:
        control = request.runtime_control
        effect_context = (
            control.external_effect_context if control is not None else None
        )
        if (
            control is None
            or not control.external_effect_enforced
            or effect_context is None
            or effect_context.node_id != request.node_id
        ):
            raise ProviderExecutionConfigurationError()

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
            raise ProviderExecutionConfigurationError() from exc
        if (
            deployment_version < 1
            or organization_id != effect_context.organization_id
            or workflow_id != effect_context.workflow_id
        ):
            raise ProviderExecutionConfigurationError()

        execution_subject, audit_actor = self._execution_identities(context)
        caps = context.get("provider_execution_capability_limits")
        if not isinstance(caps, Mapping):
            raise ProviderExecutionConfigurationError()
        values = (
            caps.get("input_token_cap"),
            caps.get("output_token_cap"),
            caps.get("cost_cap_microusd"),
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in values
        ):
            raise ProviderExecutionConfigurationError()
        input_cap, output_cap, cost_cap = values
        provider_attempt_id = uuid.uuid5(
            control.execution_id,
            f"provider_execution:{effect_context.node_invocation_id}:main_generation",
        )
        try:
            binding = ProviderExecutionBinding(
                organization_id=effect_context.organization_id,
                workflow_id=effect_context.workflow_id,
                deployment_id=deployment_id,
                deployment_version=deployment_version,
                node_id=request.node_id,
                node_invocation_id=effect_context.node_invocation_id,
                execution_admission_id=control.execution_id,
                provider_attempt_id=provider_attempt_id,
                purpose=CapabilityPurpose.MAIN_GENERATION,
                container_path=control.binding_container_path,
            )
        except (TypeError, ValueError) as exc:
            raise ProviderExecutionConfigurationError() from exc
        return ProviderExecutionCapabilityIssueCommand(
            binding=binding,
            execution_subject=execution_subject,
            billing_principal=RuntimePrincipal.organization(
                effect_context.organization_id
            ),
            audit_actor=audit_actor,
            input_token_cap=input_cap,
            output_token_cap=output_cap,
            cost_cap_microusd=cost_cap,
        )

    @staticmethod
    def _execution_identities(
        context: Mapping[str, Any],
    ) -> tuple[RuntimePrincipal, RuntimePrincipal]:
        subject = context.get("execution_subject")
        if isinstance(subject, Mapping):
            subject_type = subject.get("subject_type") or subject.get("type")
            subject_id = subject.get("subject_id") or subject.get("id")
            if subject_type != "user":
                raise ProviderExecutionConfigurationError()
            try:
                user = RuntimePrincipal.user(uuid.UUID(str(subject_id)))
            except (TypeError, ValueError) as exc:
                raise ProviderExecutionConfigurationError() from exc
            return user, user
        audience = context.get("provider_execution_audience")
        if audience == "anonymous_public":
            return RuntimePrincipal.anonymous_public(), RuntimePrincipal.public_actor()
        if audience == "system":
            system = RuntimePrincipal.system_actor()
            return system, system
        raise ProviderExecutionConfigurationError()


__all__ = [
    "CapabilityProviderExecutionAdapter",
    "provider_usage_intent_snapshot",
    "provider_visible_request_bounds",
]
