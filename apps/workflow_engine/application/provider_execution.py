"""Application contracts for one Workflow LLM provider execution."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping, Protocol

from apps.workflow_engine.domain.execution import NodeExecutionControl
from apps.shared.domain.workflow_node_location import (
    CanonicalWorkflowNodeLocation,
    ContainerPath,
)


class ProviderExecutionConfigurationError(ValueError):
    """Fail-closed error for an incomplete provider execution contract."""

    code = "provider_capability.configuration_required"


class ProviderInvocationOutcomeUnknownError(RuntimeError):
    """Provider I/O completed far enough that automatic replay is unsafe."""

    code = "provider_outcome_unknown"
    failure_phase = "outcome_unknown"

    def __init__(self) -> None:
        super().__init__(self.code)


class ProviderStartCommitRetryableError(RuntimeError):
    """Usage start was not confirmed; retry must reconcile before any send."""

    code = "provider_usage.start_commit_retryable"

    def __init__(self) -> None:
        super().__init__(self.code)


class ProviderInvocationNotSentError(RuntimeError):
    """The provider request definitively did not cross the outbound boundary."""

    code = "provider_not_sent"
    failure_phase = "before_send"

    def __init__(self) -> None:
        super().__init__(self.code)


class ProviderInvocationRejectedError(RuntimeError):
    """The provider definitively rejected the request without billable work."""

    code = "provider_rejected"
    failure_phase = "response_received"

    def __init__(self) -> None:
        super().__init__(self.code)


class LLMCredentialNotAvailableError(ValueError):
    """Safe provider credential-selection failure with structured attribution."""

    def __init__(
        self,
        reason: str,
        message: str,
        *,
        credential_id: uuid.UUID | None = None,
        model_id: str | None = None,
        organization_id: uuid.UUID | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.credential_id = credential_id
        self.model_id = model_id
        self.organization_id = organization_id


class ProviderExecutionAuditActorKind(str, Enum):
    USER = "user"
    SYSTEM = "system"
    PUBLIC = "public"


class ProviderExecutionPrincipalKind(str, Enum):
    USER = "user"
    ORGANIZATION = "organization"
    ANONYMOUS_PUBLIC = "anonymous_public"
    PUBLIC = "public"
    SYSTEM = "system"


class ProviderExecutionPurpose(str, Enum):
    MAIN_GENERATION = "main_generation"
    MEMORY_SUMMARY = "memory_summary"
    QUERY_EMBEDDING = "query_embedding"


@dataclass(frozen=True, slots=True)
class ProviderExecutionPrincipal:
    kind: ProviderExecutionPrincipalKind
    reference_id: uuid.UUID | None = None

    def __post_init__(self) -> None:
        referenced = {
            ProviderExecutionPrincipalKind.USER,
            ProviderExecutionPrincipalKind.ORGANIZATION,
        }
        if self.kind in referenced and not isinstance(self.reference_id, uuid.UUID):
            raise ValueError("referenced provider principal requires a UUID")
        if self.kind not in referenced and self.reference_id is not None:
            raise ValueError("unreferenced provider principal cannot carry a UUID")


@dataclass(frozen=True, slots=True)
class ProviderExecutionIdentityContext:
    execution_subject: ProviderExecutionPrincipal
    credential_principal: ProviderExecutionPrincipal
    billing_principal: ProviderExecutionPrincipal
    audit_actor: ProviderExecutionPrincipal

    def __post_init__(self) -> None:
        if self.execution_subject.kind not in {
            ProviderExecutionPrincipalKind.USER,
            ProviderExecutionPrincipalKind.ANONYMOUS_PUBLIC,
            ProviderExecutionPrincipalKind.SYSTEM,
        }:
            raise ValueError("execution subject kind is invalid")
        if self.credential_principal.kind is not ProviderExecutionPrincipalKind.USER:
            raise ValueError("credential principal must be a user")
        if (
            self.billing_principal.kind
            is not ProviderExecutionPrincipalKind.ORGANIZATION
        ):
            raise ValueError("billing principal must be an organization")
        expected_actor = {
            ProviderExecutionPrincipalKind.USER: ProviderExecutionPrincipalKind.USER,
            ProviderExecutionPrincipalKind.ANONYMOUS_PUBLIC: (
                ProviderExecutionPrincipalKind.PUBLIC
            ),
            ProviderExecutionPrincipalKind.SYSTEM: ProviderExecutionPrincipalKind.SYSTEM,
        }[self.execution_subject.kind]
        if self.audit_actor.kind is not expected_actor:
            raise ValueError("audit actor does not match execution subject")
        if (
            self.execution_subject.kind is ProviderExecutionPrincipalKind.USER
            and self.execution_subject.reference_id != self.audit_actor.reference_id
        ):
            raise ValueError("user audit actor must match execution subject")


@dataclass(frozen=True, slots=True)
class ProviderExecutionBindingSnapshot:
    organization_id: uuid.UUID
    workflow_id: uuid.UUID
    deployment_id: uuid.UUID
    deployment_version: int
    node_id: str
    node_invocation_id: uuid.UUID
    execution_admission_id: uuid.UUID
    provider_attempt_id: uuid.UUID
    purpose: ProviderExecutionPurpose
    container_path: ContainerPath = ()

    def __post_init__(self) -> None:
        if self.deployment_version < 1:
            raise ValueError("deployment version must be positive")
        if not self.node_id or len(self.node_id) > 255:
            raise ValueError("provider execution node id is invalid")
        CanonicalWorkflowNodeLocation(self.container_path, self.node_id)


@dataclass(frozen=True, slots=True)
class ProviderExecutionAuditActor:
    """Redaction-safe actor identity for provider permission-denial audit."""

    kind: ProviderExecutionAuditActorKind
    reference_id: uuid.UUID | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ProviderExecutionAuditActorKind):
            raise ValueError("audit actor kind is invalid")
        if self.kind is ProviderExecutionAuditActorKind.USER:
            if not isinstance(self.reference_id, uuid.UUID):
                raise ValueError("user audit actor requires a UUID reference")
        elif self.reference_id is not None:
            raise ValueError("non-user audit actor cannot carry a reference")


@dataclass(frozen=True, slots=True)
class ProviderExecutionPreflight:
    """Inputs available before Knowledge or provider-backed work starts."""

    node_id: str
    configured_model_id: str
    auto_model_routing: bool
    fallback_model_id: str | None
    knowledge_enabled: bool
    memory_summary_requested: bool
    client_override: Any | None
    execution_context: Mapping[str, Any]
    runtime_control: NodeExecutionControl | None


@dataclass(frozen=True, slots=True)
class ProviderExecutionPlan:
    """Opaque preflight result consumed by the same runtime adapter."""

    fixed_model_id: str | None
    allow_legacy_memory_summary: bool
    audit_actor: ProviderExecutionAuditActor | None = None
    routing_metadata: Mapping[str, Any] = field(default_factory=dict)
    state: object = field(default=None, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class ProviderExecutionRequest:
    plan: ProviderExecutionPlan
    model_id: str
    messages: tuple[Mapping[str, Any], ...]
    parameters: Mapping[str, Any]
    shared_session: Any | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class ProviderExecutionPreparationRequest:
    """Prepare a capability before Memory materializes provider-visible text."""

    plan: ProviderExecutionPlan
    model_id: str
    shared_session: Any | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class ProviderExecutionPreparation:
    capability_id: uuid.UUID
    capability_revision: int
    provider_attempt_id: uuid.UUID
    expires_at: datetime

    def __post_init__(self) -> None:
        if self.capability_revision < 1:
            raise ValueError("provider capability revision must be positive")
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise ValueError("provider capability expiry must be timezone-aware")


@dataclass(frozen=True, slots=True)
class ProviderExecutionPricingSnapshot:
    """Immutable non-secret rates approved by capability admission."""

    revision: str
    input_price_per_1k: Decimal
    output_price_per_1k: Decimal

    def __post_init__(self) -> None:
        if len(self.revision) != 64 or any(
            char not in "0123456789abcdef" for char in self.revision
        ):
            raise ValueError("pricing revision must be a SHA-256 digest")
        for rate in (self.input_price_per_1k, self.output_price_per_1k):
            if not isinstance(rate, Decimal) or not rate.is_finite() or rate < 0:
                raise ValueError("pricing rates must be finite non-negative decimals")


@dataclass(frozen=True, slots=True)
class ProviderExecutionUsageContext:
    """Immutable, redaction-safe result of final capability admission."""

    binding: ProviderExecutionBindingSnapshot
    capability_id: uuid.UUID
    capability_revision: int
    capability_expires_at: datetime
    policy_id: uuid.UUID
    policy_revision: int
    provider_id: uuid.UUID
    model_id: uuid.UUID
    model_api_id: str
    credential_id: uuid.UUID
    identities: ProviderExecutionIdentityContext
    permission_revision: str
    relation_revision: str
    egress_revision: str
    pricing_snapshot: ProviderExecutionPricingSnapshot
    input_token_cap: int
    output_token_cap: int
    cost_cap_microusd: int
    admitted_input_tokens: int
    admitted_output_tokens: int

    def __post_init__(self) -> None:
        if self.capability_revision < 1 or self.policy_revision < 1:
            raise ValueError("provider usage revisions must be positive")
        if not self.model_api_id or len(self.model_api_id) > 255:
            raise ValueError("provider model API id is invalid")
        if (
            self.identities.billing_principal.reference_id
            != self.binding.organization_id
        ):
            raise ValueError("billing principal must match organization")
        for value in (
            self.permission_revision,
            self.relation_revision,
            self.egress_revision,
        ):
            if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
                raise ValueError("provider usage revision must be a SHA-256 digest")
        values = (
            self.input_token_cap,
            self.output_token_cap,
            self.cost_cap_microusd,
            self.admitted_input_tokens,
            self.admitted_output_tokens,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in values
        ):
            raise ValueError("provider usage bounds must be non-negative integers")
        if (
            self.admitted_input_tokens > self.input_token_cap
            or self.admitted_output_tokens > self.output_token_cap
        ):
            raise ValueError("admitted provider usage exceeds capability bounds")
        if (
            self.binding.purpose is ProviderExecutionPurpose.QUERY_EMBEDDING
            and (self.output_token_cap != 0 or self.admitted_output_tokens != 0)
        ):
            raise ValueError("query embedding output usage must be zero")
        if (
            self.capability_expires_at.tzinfo is None
            or self.capability_expires_at.utcoffset() is None
        ):
            raise ValueError("capability expiry must be timezone-aware")


@dataclass(frozen=True, slots=True)
class ProviderExecutionAttribution:
    """Safe runtime identity; it never contains credential material."""

    credential_id: uuid.UUID
    credential_principal_user_id: uuid.UUID
    organization_id: uuid.UUID
    model_id: str
    model_db_id: uuid.UUID | None
    capability_id: uuid.UUID | None = None
    capability_revision: int | None = None
    pricing_snapshot: ProviderExecutionPricingSnapshot | None = None
    usage_context: ProviderExecutionUsageContext | None = None

    def __post_init__(self) -> None:
        if self.capability_id is None:
            if (
                self.capability_revision is not None
                or self.pricing_snapshot is not None
                or self.usage_context is not None
            ):
                raise ValueError("legacy attribution cannot carry capability state")
            return
        if (
            not isinstance(self.capability_id, uuid.UUID)
            or not isinstance(self.model_db_id, uuid.UUID)
            or not isinstance(self.capability_revision, int)
            or isinstance(self.capability_revision, bool)
            or self.capability_revision < 1
            or not isinstance(self.pricing_snapshot, ProviderExecutionPricingSnapshot)
            or not isinstance(self.usage_context, ProviderExecutionUsageContext)
        ):
            raise ValueError("capability attribution requires revision and pricing")
        context = self.usage_context
        if (
            context.capability_id != self.capability_id
            or context.capability_revision != self.capability_revision
            or context.binding.organization_id != self.organization_id
            or context.credential_id != self.credential_id
            or context.model_id != self.model_db_id
            or context.model_api_id != self.model_id
            or context.pricing_snapshot != self.pricing_snapshot
            or context.identities.credential_principal.reference_id
            != self.credential_principal_user_id
        ):
            raise ValueError("capability attribution does not match usage context")


class ProviderInvocationLease(Protocol):
    @property
    def attribution(self) -> ProviderExecutionAttribution | None: ...

    def apply_json_schema_response_format(
        self,
        *,
        name: str,
        schema: Mapping[str, Any],
    ) -> bool: ...

    def finalize_request(self) -> ProviderExecutionAttribution | None: ...

    def revalidate_current_binding(self) -> ProviderExecutionAttribution | None: ...

    def invoke(self) -> Mapping[str, Any]: ...


class ProviderExecutionRuntime(Protocol):
    def preflight(
        self,
        request: ProviderExecutionPreflight,
    ) -> ProviderExecutionPlan: ...

    def prepare(
        self,
        request: ProviderExecutionPreparationRequest,
    ) -> ProviderExecutionPreparation: ...

    def resolve(
        self,
        request: ProviderExecutionRequest,
    ) -> ProviderInvocationLease: ...


__all__ = [
    "LLMCredentialNotAvailableError",
    "ProviderExecutionAttribution",
    "ProviderExecutionAuditActor",
    "ProviderExecutionAuditActorKind",
    "ProviderExecutionConfigurationError",
    "ProviderExecutionBindingSnapshot",
    "ProviderExecutionIdentityContext",
    "ProviderExecutionPlan",
    "ProviderExecutionPreflight",
    "ProviderExecutionPreparation",
    "ProviderExecutionPreparationRequest",
    "ProviderExecutionPricingSnapshot",
    "ProviderExecutionPrincipal",
    "ProviderExecutionPrincipalKind",
    "ProviderExecutionPurpose",
    "ProviderExecutionRequest",
    "ProviderExecutionRuntime",
    "ProviderInvocationNotSentError",
    "ProviderInvocationOutcomeUnknownError",
    "ProviderInvocationRejectedError",
    "ProviderStartCommitRetryableError",
    "ProviderExecutionUsageContext",
    "ProviderInvocationLease",
]
