"""Typed, opaque provider execution capability contracts.

The LLM Credentials domain owns these values.  They deliberately carry only
safe identifiers and revisions; credential material and a provider request are
never part of the capability.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum

from apps.shared.domain.workflow_node_location import (
    CanonicalWorkflowNodeLocation,
    ContainerPath,
)


class CapabilityPurpose(str, Enum):
    MAIN_GENERATION = "main_generation"
    MEMORY_SUMMARY = "memory_summary"
    QUERY_EMBEDDING = "query_embedding"


class PrincipalKind(str, Enum):
    USER = "user"
    ORGANIZATION = "organization"
    ANONYMOUS_PUBLIC = "anonymous_public"
    PUBLIC = "public"
    SYSTEM = "system"


class ProviderExecutionCapabilityError(ValueError):
    code = "provider_capability.invalid"


class CapabilityBindingError(ProviderExecutionCapabilityError):
    code = "provider_capability.binding_mismatch"


@dataclass(frozen=True, slots=True)
class RuntimePrincipal:
    """One typed runtime identity; it is never inferred from another role."""

    kind: PrincipalKind
    reference_id: uuid.UUID | None = None

    def __post_init__(self) -> None:
        requires_reference = {PrincipalKind.USER, PrincipalKind.ORGANIZATION}
        if self.kind in requires_reference and self.reference_id is None:
            raise ValueError(f"{self.kind.value} principal requires a reference")
        if self.kind not in requires_reference and self.reference_id is not None:
            raise ValueError(f"{self.kind.value} principal cannot carry a reference")

    @classmethod
    def user(cls, user_id: uuid.UUID) -> "RuntimePrincipal":
        return cls(PrincipalKind.USER, user_id)

    @classmethod
    def organization(cls, organization_id: uuid.UUID) -> "RuntimePrincipal":
        return cls(PrincipalKind.ORGANIZATION, organization_id)

    @classmethod
    def anonymous_public(cls) -> "RuntimePrincipal":
        return cls(PrincipalKind.ANONYMOUS_PUBLIC)

    @classmethod
    def public_actor(cls) -> "RuntimePrincipal":
        return cls(PrincipalKind.PUBLIC)

    @classmethod
    def system_actor(cls) -> "RuntimePrincipal":
        return cls(PrincipalKind.SYSTEM)


@dataclass(frozen=True, slots=True)
class RuntimeIdentityContext:
    """Separates data access, credential use, billing and audit attribution."""

    execution_subject: RuntimePrincipal
    credential_principal: RuntimePrincipal
    billing_principal: RuntimePrincipal
    audit_actor: RuntimePrincipal

    def __post_init__(self) -> None:
        allowed_execution_subjects = {
            PrincipalKind.USER,
            PrincipalKind.ANONYMOUS_PUBLIC,
            PrincipalKind.SYSTEM,
        }
        if self.execution_subject.kind not in allowed_execution_subjects:
            raise ValueError("execution subject kind is not supported")
        if self.credential_principal.kind is not PrincipalKind.USER:
            raise ValueError("credential principal must be a server-derived user")
        if self.billing_principal.kind is not PrincipalKind.ORGANIZATION:
            raise ValueError("billing principal must be an organization")
        expected_audit_actor = {
            PrincipalKind.USER: PrincipalKind.USER,
            PrincipalKind.ANONYMOUS_PUBLIC: PrincipalKind.PUBLIC,
            PrincipalKind.SYSTEM: PrincipalKind.SYSTEM,
        }[self.execution_subject.kind]
        if self.audit_actor.kind is not expected_audit_actor:
            raise ValueError("audit actor does not match execution subject")
        if (
            self.execution_subject.kind is PrincipalKind.USER
            and self.audit_actor.reference_id != self.execution_subject.reference_id
        ):
            raise ValueError("user audit actor must match execution subject")


@dataclass(frozen=True, slots=True)
class ProviderExecutionBinding:
    organization_id: uuid.UUID
    workflow_id: uuid.UUID
    deployment_id: uuid.UUID
    deployment_version: int
    node_id: str
    node_invocation_id: uuid.UUID
    execution_admission_id: uuid.UUID
    provider_attempt_id: uuid.UUID
    purpose: CapabilityPurpose
    container_path: ContainerPath = ()

    def __post_init__(self) -> None:
        if self.deployment_version < 1:
            raise ValueError("deployment version must be positive")
        if not self.node_id or len(self.node_id) > 255:
            raise ValueError("node id is invalid")
        CanonicalWorkflowNodeLocation(self.container_path, self.node_id)

    def as_kwargs(self) -> dict[str, object]:
        return {
            "organization_id": self.organization_id,
            "workflow_id": self.workflow_id,
            "deployment_id": self.deployment_id,
            "deployment_version": self.deployment_version,
            "node_id": self.node_id,
            "node_invocation_id": self.node_invocation_id,
            "execution_admission_id": self.execution_admission_id,
            "provider_attempt_id": self.provider_attempt_id,
            "purpose": self.purpose,
            "container_path": self.container_path,
        }


@dataclass(frozen=True, slots=True)
class ProviderExecutionCapability:
    """Opaque durable identity and exact scope of one provider operation."""

    id: uuid.UUID
    revision: int
    binding: ProviderExecutionBinding
    policy_id: uuid.UUID
    policy_revision: int
    credential_id: uuid.UUID
    model_id: uuid.UUID
    provider_id: uuid.UUID
    credential_principal: RuntimePrincipal
    permission_revision: str
    relation_revision: str
    egress_revision: str
    pricing_revision: str
    input_token_cap: int
    output_token_cap: int
    cost_cap_microusd: int
    expires_at: datetime
    revoked_at: datetime | None = None

    @classmethod
    def issue(
        cls,
        *,
        capability_id: uuid.UUID,
        revision: int,
        binding: ProviderExecutionBinding,
        policy_id: uuid.UUID,
        policy_revision: int,
        credential_id: uuid.UUID,
        model_id: uuid.UUID,
        provider_id: uuid.UUID,
        credential_principal: RuntimePrincipal,
        permission_revision: str,
        relation_revision: str,
        egress_revision: str,
        pricing_revision: str,
        input_token_cap: int,
        output_token_cap: int,
        cost_cap_microusd: int,
        expires_at: datetime,
        now: datetime,
    ) -> "ProviderExecutionCapability":
        if revision < 1 or policy_revision < 1:
            raise ValueError("capability and policy revisions must be positive")
        if credential_principal.kind is not PrincipalKind.USER:
            raise ValueError("credential principal must be a user")
        for value, name in (
            (permission_revision, "permission_revision"),
            (relation_revision, "relation_revision"),
            (egress_revision, "egress_revision"),
            (pricing_revision, "pricing_revision"),
        ):
            if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
                raise ValueError(f"{name} must be a SHA-256 digest")
        if input_token_cap < 0 or output_token_cap < 0 or cost_cap_microusd < 0:
            raise ValueError("capability caps cannot be negative")
        if (
            binding.purpose is CapabilityPurpose.QUERY_EMBEDDING
            and output_token_cap != 0
        ):
            raise ValueError("query embedding output token cap must be zero")
        if expires_at <= now:
            raise ValueError("capability expiry must be future dated")
        return cls(
            id=capability_id,
            revision=revision,
            binding=binding,
            policy_id=policy_id,
            policy_revision=policy_revision,
            credential_id=credential_id,
            model_id=model_id,
            provider_id=provider_id,
            credential_principal=credential_principal,
            permission_revision=permission_revision,
            relation_revision=relation_revision,
            egress_revision=egress_revision,
            pricing_revision=pricing_revision,
            input_token_cap=input_token_cap,
            output_token_cap=output_token_cap,
            cost_cap_microusd=cost_cap_microusd,
            expires_at=expires_at,
        )

    def require_usable(
        self,
        *,
        binding: ProviderExecutionBinding,
        revision: int,
        now: datetime,
    ) -> None:
        if self.revision != revision or self.binding != binding:
            raise CapabilityBindingError()
        if self.revoked_at is not None or now >= self.expires_at:
            raise CapabilityBindingError()

    def revoke(self, *, now: datetime) -> "ProviderExecutionCapability":
        if self.revoked_at is not None:
            return self
        return replace(self, revoked_at=now)


__all__ = [
    "CapabilityBindingError",
    "CapabilityPurpose",
    "PrincipalKind",
    "ProviderExecutionBinding",
    "ProviderExecutionCapability",
    "ProviderExecutionCapabilityError",
    "RuntimeIdentityContext",
    "RuntimePrincipal",
]
