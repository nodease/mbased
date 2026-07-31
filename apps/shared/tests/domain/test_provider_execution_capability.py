from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from apps.shared.domain.provider_execution_capability import (
    CapabilityBindingError,
    CapabilityPurpose,
    ProviderExecutionBinding,
    ProviderExecutionCapability,
    RuntimeIdentityContext,
    RuntimePrincipal,
)


def _binding(*, purpose: CapabilityPurpose = CapabilityPurpose.MAIN_GENERATION):
    organization_id = uuid.uuid4()
    return ProviderExecutionBinding(
        organization_id=organization_id,
        workflow_id=uuid.uuid4(),
        deployment_id=uuid.uuid4(),
        deployment_version=3,
        node_id="llm-node-1",
        node_invocation_id=uuid.uuid4(),
        execution_admission_id=uuid.uuid4(),
        provider_attempt_id=uuid.uuid4(),
        purpose=purpose,
    )


def test_capability_requires_the_exact_issue_binding_and_purpose():
    now = datetime(2026, 7, 18, tzinfo=timezone.utc)
    binding = _binding()
    capability = ProviderExecutionCapability.issue(
        capability_id=uuid.uuid4(),
        revision=1,
        binding=binding,
        policy_id=uuid.uuid4(),
        policy_revision=1,
        credential_id=uuid.uuid4(),
        model_id=uuid.uuid4(),
        provider_id=uuid.uuid4(),
        credential_principal=RuntimePrincipal.user(uuid.uuid4()),
        permission_revision="a" * 64,
        relation_revision="b" * 64,
        egress_revision="c" * 64,
        pricing_revision="d" * 64,
        input_token_cap=1024,
        output_token_cap=512,
        cost_cap_microusd=10_000,
        expires_at=now + timedelta(minutes=5),
        now=now,
    )

    capability.require_usable(binding=binding, revision=1, now=now)

    with pytest.raises(CapabilityBindingError):
        capability.require_usable(
            binding=ProviderExecutionBinding(
                **{
                    **binding.as_kwargs(),
                    "purpose": CapabilityPurpose.MEMORY_SUMMARY,
                }
            ),
            revision=1,
            now=now,
        )

    with pytest.raises(CapabilityBindingError):
        capability.require_usable(
            binding=ProviderExecutionBinding(
                **{
                    **binding.as_kwargs(),
                    "container_path": (("loop", "other-loop"),),
                }
            ),
            revision=1,
            now=now,
        )


def test_query_embedding_capability_is_isolated_from_generation_purpose():
    now = datetime(2026, 7, 22, tzinfo=timezone.utc)
    query_binding = _binding(purpose=CapabilityPurpose.QUERY_EMBEDDING)
    capability = ProviderExecutionCapability.issue(
        capability_id=uuid.uuid4(),
        revision=1,
        binding=query_binding,
        policy_id=uuid.uuid4(),
        policy_revision=1,
        credential_id=uuid.uuid4(),
        model_id=uuid.uuid4(),
        provider_id=uuid.uuid4(),
        credential_principal=RuntimePrincipal.user(uuid.uuid4()),
        permission_revision="a" * 64,
        relation_revision="b" * 64,
        egress_revision="c" * 64,
        pricing_revision="d" * 64,
        input_token_cap=256,
        output_token_cap=0,
        cost_cap_microusd=1_000,
        expires_at=now + timedelta(minutes=5),
        now=now,
    )

    capability.require_usable(binding=query_binding, revision=1, now=now)

    with pytest.raises(CapabilityBindingError):
        capability.require_usable(
            binding=ProviderExecutionBinding(
                **{
                    **query_binding.as_kwargs(),
                    "purpose": CapabilityPurpose.MAIN_GENERATION,
                }
            ),
            revision=1,
            now=now,
        )


def test_query_embedding_capability_requires_zero_output_cap():
    now = datetime(2026, 7, 22, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="output token cap"):
        ProviderExecutionCapability.issue(
            capability_id=uuid.uuid4(),
            revision=1,
            binding=_binding(purpose=CapabilityPurpose.QUERY_EMBEDDING),
            policy_id=uuid.uuid4(),
            policy_revision=1,
            credential_id=uuid.uuid4(),
            model_id=uuid.uuid4(),
            provider_id=uuid.uuid4(),
            credential_principal=RuntimePrincipal.user(uuid.uuid4()),
            permission_revision="a" * 64,
            relation_revision="b" * 64,
            egress_revision="c" * 64,
            pricing_revision="d" * 64,
            input_token_cap=256,
            output_token_cap=1,
            cost_cap_microusd=1_000,
            expires_at=now + timedelta(minutes=5),
            now=now,
        )


def test_capability_expiry_and_revision_cannot_be_replayed():
    now = datetime(2026, 7, 18, tzinfo=timezone.utc)
    binding = _binding()
    capability = ProviderExecutionCapability.issue(
        capability_id=uuid.uuid4(),
        revision=7,
        binding=binding,
        policy_id=uuid.uuid4(),
        policy_revision=1,
        credential_id=uuid.uuid4(),
        model_id=uuid.uuid4(),
        provider_id=uuid.uuid4(),
        credential_principal=RuntimePrincipal.user(uuid.uuid4()),
        permission_revision="a" * 64,
        relation_revision="b" * 64,
        egress_revision="c" * 64,
        pricing_revision="d" * 64,
        input_token_cap=1024,
        output_token_cap=512,
        cost_cap_microusd=10_000,
        expires_at=now + timedelta(seconds=1),
        now=now,
    )

    with pytest.raises(CapabilityBindingError):
        capability.require_usable(binding=binding, revision=6, now=now)
    with pytest.raises(CapabilityBindingError):
        capability.require_usable(
            binding=binding,
            revision=7,
            now=now + timedelta(seconds=1),
        )


def test_public_execution_identity_does_not_promote_owner_to_another_principal():
    organization_id = uuid.uuid4()
    identity = RuntimeIdentityContext(
        execution_subject=RuntimePrincipal.anonymous_public(),
        credential_principal=RuntimePrincipal.user(uuid.uuid4()),
        billing_principal=RuntimePrincipal.organization(organization_id),
        audit_actor=RuntimePrincipal.public_actor(),
    )

    assert identity.execution_subject.kind == "anonymous_public"
    assert identity.audit_actor.kind == "public"
    assert identity.credential_principal.kind == "user"
    assert identity.billing_principal.reference_id == organization_id
    assert identity.execution_subject != identity.credential_principal
    assert identity.audit_actor != identity.credential_principal


@pytest.mark.parametrize(
    ("execution_subject", "audit_actor"),
    [
        (RuntimePrincipal.organization(uuid.uuid4()), RuntimePrincipal.system_actor()),
        (RuntimePrincipal.anonymous_public(), RuntimePrincipal.system_actor()),
        (RuntimePrincipal.system_actor(), RuntimePrincipal.public_actor()),
        (RuntimePrincipal.user(uuid.uuid4()), RuntimePrincipal.user(uuid.uuid4())),
    ],
)
def test_runtime_identity_rejects_subject_audit_misattribution(
    execution_subject,
    audit_actor,
):
    with pytest.raises(ValueError):
        RuntimeIdentityContext(
            execution_subject=execution_subject,
            credential_principal=RuntimePrincipal.user(uuid.uuid4()),
            billing_principal=RuntimePrincipal.organization(uuid.uuid4()),
            audit_actor=audit_actor,
        )
