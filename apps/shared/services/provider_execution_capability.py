"""Credential-policy resolution and opaque provider execution capabilities.

The module is intentionally owned by the LLM Credentials domain.  It resolves
the credential only from a server-managed deployment policy; workflow graphs,
execution subjects and public callers cannot select or replace it.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_CEILING, Decimal
from typing import Any

from apps.shared.db.models.app import App
from apps.shared.db.models.llm import (
    LLMCredential,
    LLMDeploymentCredentialPolicy,
    LLMModel,
    LLMProvider,
    LLMRelCredentialModel,
    ProviderExecutionCapabilityRecord,
)
from apps.shared.db.models.organization import Organization
from apps.shared.db.models.organization_membership import OrganizationMembership
from apps.shared.db.models.team import (
    Team,
    TeamLLMPermission,
    TeamMembership,
    UserLLMPermission,
)
from apps.shared.db.models.user import User
from apps.shared.db.models.workflow import Workflow
from apps.shared.db.models.workflow_deployment import WorkflowDeployment
from apps.shared.domain.provider_execution_capability import (
    CapabilityBindingError,
    CapabilityPurpose,
    ProviderExecutionBinding,
    ProviderExecutionCapability,
    RuntimeIdentityContext,
    RuntimePrincipal,
)
from apps.shared.domain.workflow_node_location import (
    CanonicalWorkflowNodeLocation,
    ContainerPath,
    WorkflowNodeLocationError,
    find_workflow_node_at_location,
)
from apps.shared.services.outbound_operation_policy import (
    LLM_PROVIDER_CALL,
    require_outbound_operation_profile,
)
from apps.shared.services.permissions import (
    get_effective_llm_credential_auth_state,
    has_llm_credential_permission,
    has_organization_manager_permission,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

CAPABILITY_TTL = timedelta(minutes=5)
_MAX_POLICY_NODE_ID_LENGTH = 255
_LLM_NODE_TYPES = {"llmnode", "llm"}
_DIRECT_CREDENTIAL_FIELDS = {
    "credential_id",
    "credentialId",
    "llm_credential_id",
    "llmCredentialId",
}


class ProviderExecutionPolicyError(ValueError):
    """A sanitized reason suitable for an API or runtime boundary."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class DeploymentCredentialPolicyCommand:
    organization_id: uuid.UUID
    deployment_id: uuid.UUID
    node_id: str
    model_id: uuid.UUID
    credential_id: uuid.UUID
    purpose: CapabilityPurpose = CapabilityPurpose.MAIN_GENERATION
    container_path: ContainerPath = ()


@dataclass(frozen=True, slots=True)
class ProviderExecutionCapabilityIssueCommand:
    binding: ProviderExecutionBinding
    execution_subject: RuntimePrincipal
    billing_principal: RuntimePrincipal
    audit_actor: RuntimePrincipal
    input_token_cap: int
    output_token_cap: int
    cost_cap_microusd: int
    policy_model_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class ProviderExecutionCapabilityAdmissionCommand:
    capability_id: uuid.UUID
    capability_revision: int
    binding: ProviderExecutionBinding
    requested_input_tokens: int
    requested_output_tokens: int
    policy_model_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class ProviderExecutionCredentialLease:
    """Internal-only provider client materialization input.

    ``credential`` is deliberately not serializable and must never cross an
    HTTP response, trace or audit boundary.
    """

    capability: ProviderExecutionCapability
    credential: LLMCredential
    model: LLMModel
    provider: LLMProvider


@dataclass(frozen=True, slots=True)
class DeploymentCredentialPolicyView:
    id: uuid.UUID
    deployment_id: uuid.UUID
    deployment_version: int
    node_id: str
    purpose: CapabilityPurpose
    model_id: uuid.UUID
    credential_id: uuid.UUID
    policy_revision: int
    is_active: bool
    created_at: datetime
    updated_at: datetime
    container_path: ContainerPath = ()


def _deployment_llm_node_data(
    graph_snapshot: Any,
    node_id: str,
    *,
    container_path: ContainerPath = (),
) -> dict[str, Any]:
    if (
        not isinstance(graph_snapshot, dict)
        or not isinstance(node_id, str)
        or not node_id
        or len(node_id) > _MAX_POLICY_NODE_ID_LENGTH
    ):
        raise ProviderExecutionPolicyError("configuration_required")
    try:
        location = CanonicalWorkflowNodeLocation(container_path, node_id)
        node = find_workflow_node_at_location(graph_snapshot, location)
    except WorkflowNodeLocationError as exc:
        raise ProviderExecutionPolicyError("configuration_required") from exc
    if not isinstance(node, dict):
        raise ProviderExecutionPolicyError("configuration_required")
    node_type = str(node.get("type") or "").strip().lower()
    data = node.get("data")
    if node_type not in _LLM_NODE_TYPES or not isinstance(data, dict):
        raise ProviderExecutionPolicyError("configuration_required")
    return data


def _require_unambiguous_llm_selection(data: dict[str, Any]) -> None:
    if any(field in data for field in _DIRECT_CREDENTIAL_FIELDS):
        raise ProviderExecutionPolicyError("configuration_required")
    if data.get("auto_model_routing") or data.get("fallback_model_id"):
        raise ProviderExecutionPolicyError("configuration_required")


def deployment_llm_node_model_id(
    graph_snapshot: Any,
    node_id: str,
    *,
    container_path: ContainerPath = (),
) -> str:
    """Return the only graph-owned LLM selection: its model API identifier.

    The policy layer does not silently normalize legacy direct credential
    fields. A capability-required deployment must be unambiguous before an
    external provider can be reached.
    """

    data = _deployment_llm_node_data(
        graph_snapshot,
        node_id,
        container_path=container_path,
    )
    _require_unambiguous_llm_selection(data)
    model_id = str(data.get("model_id") or "").strip()
    if not model_id:
        raise ProviderExecutionPolicyError("configuration_required")
    return model_id


def deployment_llm_node_supports_knowledge(
    graph_snapshot: Any,
    node_id: str,
    *,
    container_path: ContainerPath = (),
) -> None:
    data = _deployment_llm_node_data(
        graph_snapshot,
        node_id,
        container_path=container_path,
    )
    _require_unambiguous_llm_selection(data)
    if not data.get("knowledgeBases") and not data.get("knowledgeCollections"):
        raise ProviderExecutionPolicyError("configuration_required")



def _lock_fresh(query: Any) -> Any:
    """Lock rows while replacing any stale identity-map state."""

    return query.populate_existing().with_for_update()


def _safe_json_value(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, tuple):
        return [_safe_json_value(item) for item in value]
    if isinstance(value, list):
        return [_safe_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _safe_json_value(item) for key, item in value.items()}
    return value


def _revision_digest(value: Any) -> str:
    encoded = json.dumps(
        _safe_json_value(value),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ProviderExecutionCapabilityService:
    """Issue and admit short-lived provider scopes from immutable deployments."""

    @classmethod
    def replace_deployment_policy(
        cls,
        db: Session,
        *,
        actor_id: uuid.UUID,
        command: DeploymentCredentialPolicyCommand,
        query_embedding_policy_writes_enabled: bool = False,
    ) -> DeploymentCredentialPolicyView:
        deployment, app, workflow = cls._canonical_deployment(
            db,
            organization_id=command.organization_id,
            deployment_id=command.deployment_id,
            lock=True,
        )
        if not has_organization_manager_permission(
            db, actor_id, command.organization_id
        ):
            raise ProviderExecutionPolicyError("permission_denied")
        if command.purpose not in {
            CapabilityPurpose.MAIN_GENERATION,
            CapabilityPurpose.QUERY_EMBEDDING,
        }:
            raise ProviderExecutionPolicyError("configuration_required")
        if (
            command.purpose is CapabilityPurpose.QUERY_EMBEDDING
            and query_embedding_policy_writes_enabled is not True
        ):
            raise ProviderExecutionPolicyError(
                "query_embedding_rollout_unavailable"
            )

        location = cls._command_location(command)

        active_query = (
            db.query(LLMDeploymentCredentialPolicy)
            .filter(
                LLMDeploymentCredentialPolicy.organization_id
                == command.organization_id,
                LLMDeploymentCredentialPolicy.deployment_id == deployment.id,
                LLMDeploymentCredentialPolicy.deployment_version == deployment.version,
                LLMDeploymentCredentialPolicy.node_location_digest
                == location.digest,
                LLMDeploymentCredentialPolicy.purpose == command.purpose.value,
                LLMDeploymentCredentialPolicy.is_active.is_(True),
            )
        )
        if command.purpose is CapabilityPurpose.QUERY_EMBEDDING:
            active_query = active_query.filter(
                LLMDeploymentCredentialPolicy.model_id == command.model_id
            )
        active_rows = active_query.populate_existing().with_for_update().all()
        if len(active_rows) > 1:
            raise ProviderExecutionPolicyError("selection_ambiguous")
        if active_rows and cls._stored_location(active_rows[0]) != location:
            raise ProviderExecutionPolicyError("selection_ambiguous")

        model, credential, _provider, _relation = cls._resolve_policy_selection(
            db,
            deployment=deployment,
            app=app,
            workflow=workflow,
            organization_id=command.organization_id,
            node_id=command.node_id,
            container_path=command.container_path,
            model_id=command.model_id,
            credential_id=command.credential_id,
            credential_principal_user_id=actor_id,
            purpose=command.purpose,
            lock_authorization_rows=True,
        )
        if not has_organization_manager_permission(
            db, actor_id, command.organization_id
        ):
            raise ProviderExecutionPolicyError("permission_denied")

        if active_rows:
            active_rows[0].is_active = False
            next_revision = active_rows[0].policy_revision + 1
            # The partial unique index covers only active rows.  Flush the
            # superseded row before inserting its replacement so a repeat
            # PUT cannot depend on ORM statement ordering.
            db.flush()
        else:
            next_revision = 1

        policy = LLMDeploymentCredentialPolicy(
            organization_id=command.organization_id,
            workflow_id=workflow.id,
            deployment_id=deployment.id,
            deployment_version=deployment.version,
            node_id=command.node_id,
            container_path=location.to_container_path_payload(),
            node_location_digest=location.digest,
            purpose=command.purpose.value,
            model_id=model.id,
            credential_id=credential.id,
            credential_principal_user_id=actor_id,
            policy_revision=next_revision,
            is_active=True,
        )
        db.add(policy)
        db.flush()
        return cls._policy_view(policy)


    @classmethod
    def list_deployment_policies(
        cls,
        db: Session,
        *,
        actor_id: uuid.UUID,
        organization_id: uuid.UUID,
        deployment_id: uuid.UUID,
    ) -> list[DeploymentCredentialPolicyView]:
        deployment, _app, _workflow = cls._canonical_deployment(
            db,
            organization_id=organization_id,
            deployment_id=deployment_id,
        )
        if not has_organization_manager_permission(db, actor_id, organization_id):
            raise ProviderExecutionPolicyError("permission_denied")
        rows = (
            db.query(LLMDeploymentCredentialPolicy)
            .filter(
                LLMDeploymentCredentialPolicy.organization_id == organization_id,
                LLMDeploymentCredentialPolicy.deployment_id == deployment.id,
                LLMDeploymentCredentialPolicy.deployment_version == deployment.version,
                LLMDeploymentCredentialPolicy.is_active.is_(True),
            )
            .order_by(LLMDeploymentCredentialPolicy.node_location_digest.asc())
            .order_by(
                LLMDeploymentCredentialPolicy.purpose.asc(),
                LLMDeploymentCredentialPolicy.model_id.asc(),
            )
            .all()
        )
        return [cls._policy_view(row) for row in rows]

    @classmethod
    def issue_capability(
        cls,
        db: Session,
        *,
        command: ProviderExecutionCapabilityIssueCommand,
    ) -> ProviderExecutionCapability:
        cls._validate_nonnegative_caps(
            command.input_token_cap,
            command.output_token_cap,
            command.cost_cap_microusd,
        )
        binding = command.binding
        cls._validate_capability_policy_slot(
            binding=binding,
            policy_model_id=command.policy_model_id,
            output_tokens=command.output_token_cap,
        )
        deployment, app, workflow = cls._canonical_deployment(
            db,
            organization_id=binding.organization_id,
            deployment_id=binding.deployment_id,
        )
        cls._assert_binding_matches_deployment(
            binding,
            deployment=deployment,
            workflow=workflow,
        )
        # The locked policy row serializes same-binding issue retries before
        # capability lookup and insert.
        policy = cls._active_policy_for_binding(
            db,
            binding,
            policy_model_id=command.policy_model_id,
        )
        cls._validate_issue_principals(
            command,
            credential_principal_user_id=policy.credential_principal_user_id,
        )
        model, credential, provider, relation = cls._resolve_policy_selection(
            db,
            deployment=deployment,
            app=app,
            workflow=workflow,
            organization_id=binding.organization_id,
            node_id=binding.node_id,
            container_path=binding.container_path,
            model_id=policy.model_id,
            credential_id=policy.credential_id,
            credential_principal_user_id=policy.credential_principal_user_id,
            purpose=binding.purpose,
        )
        revisions = cls._current_revisions(
            db,
            organization_id=binding.organization_id,
            credential=credential,
            credential_principal_user_id=policy.credential_principal_user_id,
            relation=relation,
            model=model,
            provider=provider,
        )

        existing = (
            db.query(ProviderExecutionCapabilityRecord)
            .filter(
                ProviderExecutionCapabilityRecord.organization_id
                == binding.organization_id,
                ProviderExecutionCapabilityRecord.provider_attempt_id
                == binding.provider_attempt_id,
                ProviderExecutionCapabilityRecord.purpose == binding.purpose.value,
            )
            .populate_existing()
            .with_for_update()
            .one_or_none()
        )
        issue_now = cls._database_clock_now(db)
        if existing is not None:
            existing_capability = cls._domain_capability(existing)
            try:
                existing_capability.require_usable(
                    binding=binding,
                    revision=existing.capability_revision,
                    now=issue_now,
                )
            except CapabilityBindingError as exc:
                raise ProviderExecutionPolicyError("capability_stale") from exc
            if (
                existing.input_token_cap != command.input_token_cap
                or existing.output_token_cap != command.output_token_cap
                or existing.cost_cap_microusd != command.cost_cap_microusd
            ):
                raise ProviderExecutionPolicyError("capability_attempt_reused")
            if not cls._record_matches_issue_identity(existing, command):
                raise ProviderExecutionPolicyError("capability_attempt_reused")
            if (
                existing.policy_id != policy.id
                or existing.policy_revision != policy.policy_revision
                or existing.model_id != model.id
                or existing.credential_id != credential.id
                or existing.provider_id != provider.id
                or existing.credential_principal_user_id
                != policy.credential_principal_user_id
                or any(
                    getattr(existing, f"{name}_revision") != revision
                    for name, revision in revisions.items()
                )
            ):
                raise ProviderExecutionPolicyError("capability_stale")
            return existing_capability

        binding_location = CanonicalWorkflowNodeLocation(
            binding.container_path,
            binding.node_id,
        )
        record = ProviderExecutionCapabilityRecord(
            organization_id=binding.organization_id,
            policy_id=policy.id,
            workflow_id=binding.workflow_id,
            deployment_id=binding.deployment_id,
            deployment_version=binding.deployment_version,
            node_id=binding.node_id,
            container_path=binding_location.to_container_path_payload(),
            node_location_digest=binding_location.digest,
            node_invocation_id=binding.node_invocation_id,
            execution_admission_id=binding.execution_admission_id,
            provider_attempt_id=binding.provider_attempt_id,
            purpose=binding.purpose.value,
            provider_id=provider.id,
            model_id=model.id,
            credential_id=credential.id,
            credential_principal_user_id=policy.credential_principal_user_id,
            execution_subject_kind=command.execution_subject.kind.value,
            execution_subject_id=command.execution_subject.reference_id,
            billing_principal_kind=command.billing_principal.kind.value,
            billing_principal_id=command.billing_principal.reference_id,
            audit_actor_kind=command.audit_actor.kind.value,
            audit_actor_id=command.audit_actor.reference_id,
            capability_revision=1,
            policy_revision=policy.policy_revision,
            permission_revision=revisions["permission"],
            relation_revision=revisions["relation"],
            egress_revision=revisions["egress"],
            pricing_revision=revisions["pricing"],
            input_token_cap=command.input_token_cap,
            output_token_cap=command.output_token_cap,
            cost_cap_microusd=command.cost_cap_microusd,
            state="active",
            created_at=issue_now,
            updated_at=issue_now,
            expires_at=issue_now + CAPABILITY_TTL,
        )
        db.add(record)
        db.flush()
        return cls._domain_capability(record)

    @classmethod
    def admit_capability(
        cls,
        db: Session,
        *,
        command: ProviderExecutionCapabilityAdmissionCommand,
    ) -> ProviderExecutionCredentialLease:
        cls._validate_nonnegative_caps(
            command.requested_input_tokens,
            command.requested_output_tokens,
        )
        cls._validate_capability_policy_slot(
            binding=command.binding,
            policy_model_id=command.policy_model_id,
            output_tokens=command.requested_output_tokens,
        )
        deployment, app, workflow = cls._canonical_deployment(
            db,
            organization_id=command.binding.organization_id,
            deployment_id=command.binding.deployment_id,
        )
        cls._assert_binding_matches_deployment(
            command.binding,
            deployment=deployment,
            workflow=workflow,
        )
        policy = cls._active_policy_for_binding(
            db,
            command.binding,
            policy_model_id=command.policy_model_id,
        )
        record = (
            db.query(ProviderExecutionCapabilityRecord)
            .filter(ProviderExecutionCapabilityRecord.id == command.capability_id)
            .populate_existing()
            .with_for_update()
            .one_or_none()
        )
        if record is None:
            raise ProviderExecutionPolicyError("capability_stale")
        capability = cls._domain_capability(record)
        admission_started_at = cls._database_clock_now(db)
        try:
            capability.require_usable(
                binding=command.binding,
                revision=command.capability_revision,
                now=admission_started_at,
            )
        except CapabilityBindingError as exc:
            raise ProviderExecutionPolicyError("capability_stale") from exc
        if (
            command.requested_input_tokens > capability.input_token_cap
            or command.requested_output_tokens > capability.output_token_cap
        ):
            raise ProviderExecutionPolicyError("capability_stale")
        if (
            policy.id != record.policy_id
            or policy.policy_revision != record.policy_revision
        ):
            raise ProviderExecutionPolicyError("capability_stale")
        model, credential, provider, relation = cls._resolve_policy_selection(
            db,
            deployment=deployment,
            app=app,
            workflow=workflow,
            organization_id=command.binding.organization_id,
            node_id=command.binding.node_id,
            container_path=command.binding.container_path,
            model_id=policy.model_id,
            credential_id=policy.credential_id,
            credential_principal_user_id=policy.credential_principal_user_id,
            purpose=command.binding.purpose,
            lock_authorization_rows=True,
        )
        if (
            record.model_id != model.id
            or record.credential_id != credential.id
            or record.provider_id != provider.id
            or record.credential_principal_user_id
            != policy.credential_principal_user_id
        ):
            raise ProviderExecutionPolicyError("capability_stale")
        revisions = cls._current_revisions(
            db,
            organization_id=command.binding.organization_id,
            credential=credential,
            credential_principal_user_id=policy.credential_principal_user_id,
            relation=relation,
            model=model,
            provider=provider,
            lock_permission_rows=True,
        )
        if any(
            getattr(record, f"{name}_revision") != revision
            for name, revision in revisions.items()
        ):
            raise ProviderExecutionPolicyError("capability_stale")
        requested_cost_microusd = cls._request_cost_microusd(
            model,
            input_tokens=command.requested_input_tokens,
            output_tokens=command.requested_output_tokens,
        )
        if requested_cost_microusd > capability.cost_cap_microusd:
            raise ProviderExecutionPolicyError("capability_stale")
        final_now = cls._database_clock_now(db)
        try:
            capability.require_usable(
                binding=command.binding,
                revision=command.capability_revision,
                now=final_now,
            )
        except CapabilityBindingError as exc:
            raise ProviderExecutionPolicyError("capability_stale") from exc
        return ProviderExecutionCredentialLease(
            capability=capability,
            credential=credential,
            model=model,
            provider=provider,
        )

    @staticmethod
    def _command_location(
        command: DeploymentCredentialPolicyCommand,
    ) -> CanonicalWorkflowNodeLocation:
        try:
            return CanonicalWorkflowNodeLocation(
                command.container_path,
                command.node_id,
            )
        except WorkflowNodeLocationError as exc:
            raise ProviderExecutionPolicyError("configuration_required") from exc

    @staticmethod
    def _stored_location(
        row: LLMDeploymentCredentialPolicy | ProviderExecutionCapabilityRecord,
        *,
        error_code: str = "selection_ambiguous",
    ) -> CanonicalWorkflowNodeLocation:
        try:
            location = CanonicalWorkflowNodeLocation.from_container_path_payload(
                container_path=row.container_path,
                node_id=row.node_id,
            )
        except (AttributeError, WorkflowNodeLocationError) as exc:
            raise ProviderExecutionPolicyError(error_code) from exc
        if row.node_location_digest != location.digest:
            raise ProviderExecutionPolicyError(error_code)
        return location

    @staticmethod
    def _policy_view(
        policy: LLMDeploymentCredentialPolicy,
    ) -> DeploymentCredentialPolicyView:
        return DeploymentCredentialPolicyView(
            id=policy.id,
            deployment_id=policy.deployment_id,
            deployment_version=policy.deployment_version,
            node_id=policy.node_id,
            purpose=CapabilityPurpose(policy.purpose),
            container_path=ProviderExecutionCapabilityService._stored_location(
                policy
            ).container_path,
            model_id=policy.model_id,
            credential_id=policy.credential_id,
            policy_revision=policy.policy_revision,
            is_active=policy.is_active,
            created_at=policy.created_at,
            updated_at=policy.updated_at,
        )

    @staticmethod
    def _database_clock_now(db: Session) -> datetime:
        value = db.execute(select(func.clock_timestamp())).scalar_one()
        if not isinstance(value, datetime):
            raise ProviderExecutionPolicyError("capability_stale")
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _validate_nonnegative_caps(*caps: int) -> None:
        if any(
            isinstance(cap, bool) or not isinstance(cap, int) or cap < 0
            for cap in caps
        ):
            raise ProviderExecutionPolicyError("configuration_required")

    @staticmethod
    def _validate_capability_policy_slot(
        *,
        binding: ProviderExecutionBinding,
        policy_model_id: uuid.UUID | None,
        output_tokens: int,
    ) -> None:
        if binding.purpose is CapabilityPurpose.QUERY_EMBEDDING:
            if not isinstance(policy_model_id, uuid.UUID) or output_tokens != 0:
                raise ProviderExecutionPolicyError("configuration_required")
            return
        if policy_model_id is not None:
            raise ProviderExecutionPolicyError("configuration_required")

    @staticmethod
    def _request_cost_microusd(
        model: LLMModel,
        *,
        input_tokens: int,
        output_tokens: int,
    ) -> int:
        if model.input_price_1k is None or model.output_price_1k is None:
            raise ProviderExecutionPolicyError("configuration_required")
        try:
            input_price = Decimal(str(model.input_price_1k))
            output_price = Decimal(str(model.output_price_1k))
        except Exception as exc:
            raise ProviderExecutionPolicyError("configuration_required") from exc
        if input_price < 0 or output_price < 0:
            raise ProviderExecutionPolicyError("configuration_required")
        microusd = (
            (Decimal(input_tokens) * input_price)
            + (Decimal(output_tokens) * output_price)
        ) * Decimal(1_000_000) / Decimal(1_000)
        return int(microusd.to_integral_value(rounding=ROUND_CEILING))

    @staticmethod
    def _validate_issue_principals(
        command: ProviderExecutionCapabilityIssueCommand,
        *,
        credential_principal_user_id: uuid.UUID,
    ) -> None:
        try:
            RuntimeIdentityContext(
                execution_subject=command.execution_subject,
                credential_principal=RuntimePrincipal.user(
                    credential_principal_user_id
                ),
                billing_principal=command.billing_principal,
                audit_actor=command.audit_actor,
            )
        except ValueError as exc:
            raise ProviderExecutionPolicyError("permission_denied") from exc
        if (
            command.billing_principal.reference_id
            != command.binding.organization_id
        ):
            raise ProviderExecutionPolicyError("permission_denied")

    @staticmethod
    def _record_matches_issue_identity(
        record: ProviderExecutionCapabilityRecord,
        command: ProviderExecutionCapabilityIssueCommand,
    ) -> bool:
        return (
            record.execution_subject_kind
            == command.execution_subject.kind.value
            and record.execution_subject_id == command.execution_subject.reference_id
            and record.billing_principal_kind
            == command.billing_principal.kind.value
            and record.billing_principal_id == command.billing_principal.reference_id
            and record.audit_actor_kind == command.audit_actor.kind.value
            and record.audit_actor_id == command.audit_actor.reference_id
        )

    @staticmethod
    def _canonical_deployment(
        db: Session,
        *,
        organization_id: uuid.UUID,
        deployment_id: uuid.UUID,
        lock: bool = False,
    ) -> tuple[WorkflowDeployment, App, Workflow]:
        deployment_query = db.query(WorkflowDeployment).filter(
            WorkflowDeployment.id == deployment_id
        )
        if lock:
            deployment_query = _lock_fresh(deployment_query)
        deployment = deployment_query.one_or_none()
        if deployment is None:
            raise ProviderExecutionPolicyError("resource_not_found")
        app = db.query(App).filter(App.id == deployment.app_id).one_or_none()
        if app is None or app.organization_id != organization_id:
            raise ProviderExecutionPolicyError("resource_not_found")
        workflow = (
            db.query(Workflow).filter(Workflow.id == app.workflow_id).one_or_none()
        )
        if workflow is None or workflow.organization_id != organization_id:
            raise ProviderExecutionPolicyError("resource_not_found")
        return deployment, app, workflow

    @staticmethod
    def _assert_binding_matches_deployment(
        binding: ProviderExecutionBinding,
        *,
        deployment: WorkflowDeployment,
        workflow: Workflow,
    ) -> None:
        if (
            binding.deployment_version != deployment.version
            or binding.workflow_id != workflow.id
        ):
            raise ProviderExecutionPolicyError("configuration_required")

    @classmethod
    def _active_policy_for_binding(
        cls,
        db: Session,
        binding: ProviderExecutionBinding,
        *,
        policy_model_id: uuid.UUID | None = None,
    ) -> LLMDeploymentCredentialPolicy:
        location = CanonicalWorkflowNodeLocation(
            binding.container_path,
            binding.node_id,
        )
        policy_purpose = (
            CapabilityPurpose.MAIN_GENERATION
            if binding.purpose is CapabilityPurpose.MEMORY_SUMMARY
            else binding.purpose
        )
        query = (
            db.query(LLMDeploymentCredentialPolicy)
            .filter(
                LLMDeploymentCredentialPolicy.organization_id
                == binding.organization_id,
                LLMDeploymentCredentialPolicy.deployment_id == binding.deployment_id,
                LLMDeploymentCredentialPolicy.deployment_version
                == binding.deployment_version,
                LLMDeploymentCredentialPolicy.workflow_id == binding.workflow_id,
                LLMDeploymentCredentialPolicy.node_location_digest
                == location.digest,
                LLMDeploymentCredentialPolicy.purpose == policy_purpose.value,
                LLMDeploymentCredentialPolicy.is_active.is_(True),
            )
        )
        if binding.purpose is CapabilityPurpose.QUERY_EMBEDDING:
            query = query.filter(
                LLMDeploymentCredentialPolicy.model_id == policy_model_id
            )
        rows = query.populate_existing().with_for_update().all()
        if not rows:
            raise ProviderExecutionPolicyError("configuration_required")
        if len(rows) != 1:
            raise ProviderExecutionPolicyError("selection_ambiguous")
        if cls._stored_location(rows[0]) != location:
            raise ProviderExecutionPolicyError("selection_ambiguous")
        return rows[0]

    @classmethod
    def _resolve_policy_selection(
        cls,
        db: Session,
        *,
        deployment: WorkflowDeployment,
        app: App,
        workflow: Workflow,
        organization_id: uuid.UUID,
        node_id: str,
        container_path: ContainerPath = (),
        model_id: uuid.UUID,
        credential_id: uuid.UUID,
        credential_principal_user_id: uuid.UUID,
        purpose: CapabilityPurpose = CapabilityPurpose.MAIN_GENERATION,
        lock_authorization_rows: bool = False,
    ) -> tuple[LLMModel, LLMCredential, LLMProvider, LLMRelCredentialModel]:
        if (
            app.organization_id != organization_id
            or workflow.organization_id != organization_id
            or workflow.id != app.workflow_id
            or deployment.app_id != app.id
        ):
            raise ProviderExecutionPolicyError("resource_not_found")
        graph_model_id: str | None = None
        if purpose is CapabilityPurpose.QUERY_EMBEDDING:
            deployment_llm_node_supports_knowledge(
                deployment.graph_snapshot,
                node_id,
                container_path=container_path,
            )
        else:
            graph_model_id = deployment_llm_node_model_id(
                deployment.graph_snapshot,
                node_id,
                container_path=container_path,
            )
        model_query = db.query(LLMModel).filter(LLMModel.id == model_id)
        if lock_authorization_rows:
            model_query = _lock_fresh(model_query)
        model = model_query.one_or_none()
        expected_type = (
            "embedding"
            if purpose is CapabilityPurpose.QUERY_EMBEDDING
            else "chat"
        )
        if (
            model is None
            or not model.is_active
            or model.type != expected_type
            or (
                graph_model_id is not None
                and model.model_id_for_api_call != graph_model_id
            )
        ):
            raise ProviderExecutionPolicyError("configuration_required")
        provider_query = db.query(LLMProvider).filter(
            LLMProvider.id == model.provider_id
        )
        if lock_authorization_rows:
            provider_query = _lock_fresh(provider_query)
        provider = provider_query.one_or_none()
        credential_query = (
            db.query(LLMCredential)
            .filter(
                LLMCredential.id == credential_id,
                LLMCredential.organization_id == organization_id,
                LLMCredential.is_valid.is_(True),
                LLMCredential.provider_id == model.provider_id,
            )
        )
        if lock_authorization_rows:
            credential_query = _lock_fresh(credential_query)
        credential = credential_query.one_or_none()
        if provider is None or credential is None:
            raise ProviderExecutionPolicyError("configuration_required")
        relation_query = (
            db.query(LLMRelCredentialModel)
            .filter(
                LLMRelCredentialModel.credential_id == credential.id,
                LLMRelCredentialModel.model_id == model.id,
                LLMRelCredentialModel.is_verified.is_(True),
            )
        )
        if lock_authorization_rows:
            relation_query = _lock_fresh(relation_query)
        relations = relation_query.all()
        if len(relations) != 1:
            raise ProviderExecutionPolicyError("relation_unavailable")
        if lock_authorization_rows:
            cls._permission_revision(
                db,
                organization_id=organization_id,
                credential_id=credential.id,
                credential_principal_user_id=credential_principal_user_id,
                lock_rows=True,
            )
        if not has_llm_credential_permission(
            db,
            credential_principal_user_id,
            credential.id,
            "use",
            organization_id=organization_id,
        ):
            raise ProviderExecutionPolicyError("permission_denied")
        return model, credential, provider, relations[0]

    @classmethod
    def _current_revisions(
        cls,
        db: Session,
        *,
        organization_id: uuid.UUID,
        credential: LLMCredential,
        credential_principal_user_id: uuid.UUID,
        relation: LLMRelCredentialModel,
        model: LLMModel,
        provider: LLMProvider,
        lock_permission_rows: bool = False,
    ) -> dict[str, str]:
        return {
            "permission": cls._permission_revision(
                db,
                organization_id=organization_id,
                credential_id=credential.id,
                credential_principal_user_id=credential_principal_user_id,
                lock_rows=lock_permission_rows,
            ),
            "relation": _revision_digest(
                {
                    "credential": {
                        "id": credential.id,
                        "organization_id": credential.organization_id,
                        "provider_id": credential.provider_id,
                        "is_valid": credential.is_valid,
                        "updated_at": credential.updated_at,
                    },
                    "relation": {
                        "id": relation.id,
                        "credential_id": relation.credential_id,
                        "model_id": relation.model_id,
                        "is_verified": relation.is_verified,
                        "priority": relation.priority,
                        "created_at": relation.created_at,
                    },
                    "model": {
                        "id": model.id,
                        "provider_id": model.provider_id,
                        "model_id_for_api_call": model.model_id_for_api_call,
                        "type": model.type,
                        "is_active": model.is_active,
                        "updated_at": model.updated_at,
                    },
                }
            ),
            "egress": _revision_digest(
                {
                    "provider_id": provider.id,
                    "provider_name": provider.name,
                    "base_url": provider.base_url,
                    "updated_at": provider.updated_at,
                    "transport_policy_revision": require_outbound_operation_profile(
                        LLM_PROVIDER_CALL
                    ).revision,
                }
            ),
            "pricing": _revision_digest(
                {
                    "model_id": model.id,
                    "input_price_1k": model.input_price_1k,
                    "output_price_1k": model.output_price_1k,
                    "updated_at": model.updated_at,
                }
            ),
        }

    @staticmethod
    def _permission_revision(
        db: Session,
        *,
        organization_id: uuid.UUID,
        credential_id: uuid.UUID,
        credential_principal_user_id: uuid.UUID,
        lock_rows: bool = False,
    ) -> str:
        """Fingerprint all rows that can affect a credential-use decision.

        Admission still calls ``has_llm_credential_permission`` through the
        canonical resolver.  This digest additionally rejects a capability
        when a permitted principal's source changes but remains permissive.
        """

        organization_query = db.query(Organization).filter(
            Organization.id == organization_id
        )
        if lock_rows:
            organization_query = _lock_fresh(organization_query)
        organization = organization_query.one_or_none()
        user_query = db.query(User).filter(User.id == credential_principal_user_id)
        if lock_rows:
            user_query = _lock_fresh(user_query)
        user = user_query.one_or_none()
        membership_query = (
            db.query(OrganizationMembership)
            .filter(
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.user_id == credential_principal_user_id,
            )
        )
        if lock_rows:
            membership_query = _lock_fresh(membership_query)
        membership = membership_query.one_or_none()
        direct_query = (
            db.query(UserLLMPermission)
            .filter(
                UserLLMPermission.grantee_organization_id == organization_id,
                UserLLMPermission.user_id == credential_principal_user_id,
                UserLLMPermission.llm_credential_id == credential_id,
            )
        )
        if lock_rows:
            direct_query = _lock_fresh(direct_query)
        direct_rows = direct_query.all()
        team_query = (
            db.query(TeamLLMPermission, TeamMembership, Team)
            .join(
                TeamMembership,
                TeamMembership.team_id == TeamLLMPermission.team_id,
            )
            .join(Team, Team.id == TeamLLMPermission.team_id)
            .filter(
                TeamLLMPermission.grantee_organization_id == organization_id,
                TeamLLMPermission.llm_credential_id == credential_id,
                TeamMembership.grantee_organization_id == organization_id,
                TeamMembership.user_id == credential_principal_user_id,
                Team.organization_id == organization_id,
            )
        )
        if lock_rows:
            team_query = _lock_fresh(team_query)
        team_rows = team_query.all()
        effective_state = get_effective_llm_credential_auth_state(
            db,
            credential_principal_user_id,
            credential_id,
            organization_id=organization_id,
        )
        return _revision_digest(
            {
                "effective_state": effective_state,
                "organization": None
                if organization is None
                else {
                    "id": organization.id,
                    "is_active": organization.is_active,
                    "created_by": organization.created_by,
                    "managed_by": organization.managed_by,
                    "deactivated_at": organization.deactivated_at,
                    "updated_at": organization.updated_at,
                },
                "user": None
                if user is None
                else {
                    "id": user.id,
                    "deactivated_at": user.deactivated_at,
                    "updated_at": user.updated_at,
                },
                "membership": None
                if membership is None
                else {
                    "id": membership.id,
                    "membership_state": membership.membership_state,
                    "organization_auth_state": membership.organization_auth_state,
                    "updated_at": membership.updated_at,
                    "flags": membership.flags,
                },
                "direct": sorted(
                    (
                        {
                            "id": row.id,
                            "auth_state": row.auth_state,
                            "assigned_at": row.assigned_at,
                            "flags": row.flags,
                        }
                        for row in direct_rows
                    ),
                    key=lambda row: str(row["id"]),
                ),
                "team": sorted(
                    (
                        {
                            "permission_id": permission.id,
                            "permission_auth_state": permission.auth_state,
                            "permission_assigned_at": permission.assigned_at,
                            "permission_flags": permission.flags,
                            "membership_id": member.id,
                            "membership_assigned_at": member.assigned_at,
                            "membership_flags": member.flags,
                            "team_id": team.id,
                            "team_active": team.is_active,
                            "team_updated_at": team.updated_at,
                        }
                        for permission, member, team in team_rows
                    ),
                    key=lambda row: (str(row["permission_id"]), str(row["membership_id"])),
                ),
            }
        )

    @staticmethod
    def _domain_capability(
        record: ProviderExecutionCapabilityRecord,
    ) -> ProviderExecutionCapability:
        try:
            purpose = CapabilityPurpose(record.purpose)
            capability = ProviderExecutionCapability.issue(
                capability_id=record.id,
                revision=record.capability_revision,
                binding=ProviderExecutionBinding(
                    organization_id=record.organization_id,
                    workflow_id=record.workflow_id,
                    deployment_id=record.deployment_id,
                    deployment_version=record.deployment_version,
                    node_id=record.node_id,
                    node_invocation_id=record.node_invocation_id,
                    execution_admission_id=record.execution_admission_id,
                    provider_attempt_id=record.provider_attempt_id,
                    purpose=purpose,
                    container_path=ProviderExecutionCapabilityService._stored_location(
                        record,
                        error_code="capability_stale",
                    ).container_path,
                ),
                policy_id=record.policy_id,
                policy_revision=record.policy_revision,
                credential_id=record.credential_id,
                model_id=record.model_id,
                provider_id=record.provider_id,
                credential_principal=RuntimePrincipal.user(
                    record.credential_principal_user_id
                ),
                permission_revision=record.permission_revision,
                relation_revision=record.relation_revision,
                egress_revision=record.egress_revision,
                pricing_revision=record.pricing_revision,
                input_token_cap=record.input_token_cap,
                output_token_cap=record.output_token_cap,
                cost_cap_microusd=record.cost_cap_microusd,
                expires_at=record.expires_at,
                now=record.created_at,
            )
        except (TypeError, ValueError) as exc:
            raise ProviderExecutionPolicyError("capability_stale") from exc
        if record.state != "active":
            return capability.revoke(now=record.revoked_at or record.updated_at)
        return capability


__all__ = [
    "CAPABILITY_TTL",
    "DeploymentCredentialPolicyCommand",
    "DeploymentCredentialPolicyView",
    "ProviderExecutionCapabilityAdmissionCommand",
    "ProviderExecutionCapabilityIssueCommand",
    "ProviderExecutionCapabilityService",
    "ProviderExecutionCredentialLease",
    "ProviderExecutionPolicyError",
    "deployment_llm_node_model_id",
]
