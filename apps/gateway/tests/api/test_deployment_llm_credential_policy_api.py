from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from apps.gateway.api.v1.endpoints import deployment as deployment_endpoint
from apps.shared.domain.provider_execution_capability import CapabilityPurpose
from apps.shared.domain.workflow_node_location import CanonicalWorkflowNodeLocation
from apps.shared.schemas.deployment import DeploymentLLMCredentialPolicyUpsert
from apps.shared.services.provider_execution_capability import (
    DeploymentCredentialPolicyView,
    ProviderExecutionPolicyError,
)


class _Db:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def test_policy_write_uses_active_org_and_returns_safe_projection(monkeypatch):
    organization_id = uuid.uuid4()
    deployment_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    model_id = uuid.uuid4()
    credential_id = uuid.uuid4()
    db = _Db()
    captured = {}
    now = datetime.now(timezone.utc)
    policy = DeploymentCredentialPolicyView(
        id=uuid.uuid4(),
        deployment_id=deployment_id,
        deployment_version=3,
        node_id="llm-1",
        purpose=CapabilityPurpose.MAIN_GENERATION,
        model_id=model_id,
        credential_id=credential_id,
        policy_revision=2,
        is_active=True,
        created_at=now,
        updated_at=now,
        container_path=(("loop", "loop-a"),),
    )

    monkeypatch.setattr(
        deployment_endpoint,
        "resolve_active_organization_id",
        lambda *_args: organization_id,
    )

    def replace(
        _db,
        *,
        actor_id,
        command,
        query_embedding_policy_writes_enabled,
    ):
        captured["actor_id"] = actor_id
        captured["command"] = command
        captured["query_embedding_policy_writes_enabled"] = (
            query_embedding_policy_writes_enabled
        )
        return policy

    monkeypatch.setattr(
        deployment_endpoint.ProviderExecutionCapabilityService,
        "replace_deployment_policy",
        replace,
    )

    result = deployment_endpoint.replace_deployment_llm_credential_policy.__wrapped__(
        deployment_id,
        "llm-1",
        DeploymentLLMCredentialPolicyUpsert(
            model_id=model_id,
            credential_id=credential_id,
            container_path=[{"kind": "loop", "node_id": "loop-a"}],
        ),
        request=object(),
        x_organization_id=str(organization_id),
        db=db,
        current_user=SimpleNamespace(id=actor_id),
    )

    assert db.commits == 1
    assert db.rollbacks == 0
    assert captured["actor_id"] == actor_id
    assert captured["command"].organization_id == organization_id
    assert captured["command"].deployment_id == deployment_id
    assert captured["command"].node_id == "llm-1"
    assert captured["command"].purpose is CapabilityPurpose.MAIN_GENERATION
    assert captured["query_embedding_policy_writes_enabled"] is False
    assert captured["command"].container_path == (("loop", "loop-a"),)
    assert result.model_dump() == {
        "id": policy.id,
        "deployment_id": deployment_id,
        "deployment_version": 3,
        "node_id": "llm-1",
        "container_path": [{"kind": "loop", "node_id": "loop-a"}],
        "purpose": "main_generation",
        "model_id": model_id,
        "credential_id": credential_id,
        "policy_revision": 2,
        "is_active": True,
        "created_at": now,
        "updated_at": now,
    }
    assert "credential_principal_user_id" not in result.model_dump()


def test_policy_audit_metadata_uses_only_safe_policy_identifiers():
    policy_in = DeploymentLLMCredentialPolicyUpsert(
        model_id=uuid.uuid4(),
        credential_id=uuid.uuid4(),
        container_path=[{"kind": "loop", "node_id": "private-loop"}],
    )

    metadata = deployment_endpoint._deployment_llm_policy_audit_metadata(
        {
            "node_id": "private-llm",
            "policy_in": policy_in,
        }
    )

    expected = CanonicalWorkflowNodeLocation(
        (("loop", "private-loop"),),
        "private-llm",
    )
    assert metadata == {
        "node_location_ref": expected.safe_reference,
        "purpose": "main_generation",
        "model_id": str(policy_in.model_id),
    }
    assert "private-loop" not in str(metadata)
    assert "private-llm" not in str(metadata)


def test_policy_request_without_container_path_keeps_root_compatibility():
    policy_in = DeploymentLLMCredentialPolicyUpsert(
        model_id=uuid.uuid4(),
        credential_id=uuid.uuid4(),
    )

    assert policy_in.container_path == []
    assert policy_in.purpose == "main_generation"


def test_query_policy_write_passes_active_server_rollout_mode(monkeypatch):
    organization_id = uuid.uuid4()
    deployment_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    model_id = uuid.uuid4()
    credential_id = uuid.uuid4()
    policy_in = DeploymentLLMCredentialPolicyUpsert(
        model_id=model_id,
        credential_id=credential_id,
        purpose="query_embedding",
    )
    captured = {}
    now = datetime.now(timezone.utc)

    monkeypatch.setattr(
        deployment_endpoint,
        "resolve_active_organization_id",
        lambda *_args: organization_id,
    )
    monkeypatch.setattr(
        deployment_endpoint.settings,
        "QUERY_EMBEDDING_POLICY_WRITE_MODE",
        "active",
    )

    def replace(_db, **kwargs):
        captured.update(kwargs)
        return DeploymentCredentialPolicyView(
            id=uuid.uuid4(),
            deployment_id=deployment_id,
            deployment_version=1,
            node_id="llm-1",
            purpose=CapabilityPurpose.QUERY_EMBEDDING,
            model_id=model_id,
            credential_id=credential_id,
            policy_revision=1,
            is_active=True,
            created_at=now,
            updated_at=now,
            container_path=(),
        )

    monkeypatch.setattr(
        deployment_endpoint.ProviderExecutionCapabilityService,
        "replace_deployment_policy",
        replace,
    )
    db = _Db()

    result = deployment_endpoint.replace_deployment_llm_credential_policy.__wrapped__(
        deployment_id,
        "llm-1",
        policy_in,
        request=object(),
        x_organization_id=str(organization_id),
        db=db,
        current_user=SimpleNamespace(id=actor_id),
    )

    assert captured["command"].purpose is CapabilityPurpose.QUERY_EMBEDDING
    assert captured["query_embedding_policy_writes_enabled"] is True
    assert result.purpose == "query_embedding"
    assert db.commits == 1


@pytest.mark.parametrize(
    "extra",
    [
        {"node_location_digest": "0" * 64},
        {"purpose": "memory_summary"},
        {"container_path": [{"kind": "future", "node_id": "loop-a"}]},
        {
            "container_path": [
                {"kind": "loop", "node_id": f"loop-{index}"}
                for index in range(17)
            ]
        },
    ],
)
def test_policy_request_rejects_client_owned_or_noncanonical_location(extra):
    with pytest.raises(ValidationError):
        DeploymentLLMCredentialPolicyUpsert(
            model_id=uuid.uuid4(),
            credential_id=uuid.uuid4(),
            **extra,
        )


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_code"),
    [
        (ProviderExecutionPolicyError("permission_denied"), 403, None),
        (ProviderExecutionPolicyError("resource_not_found"), 404, None),
        (ProviderExecutionPolicyError("configuration_required"), 422, "configuration_required"),
        (ProviderExecutionPolicyError("selection_ambiguous"), 409, "selection_ambiguous"),
        (
            ProviderExecutionPolicyError("query_embedding_rollout_unavailable"),
            503,
            "query_embedding_rollout_unavailable",
        ),
    ],
)
def test_policy_write_maps_only_safe_errors(
    monkeypatch,
    error,
    expected_status,
    expected_code,
):
    db = _Db()
    organization_id = uuid.uuid4()
    monkeypatch.setattr(
        deployment_endpoint,
        "resolve_active_organization_id",
        lambda *_args: organization_id,
    )
    monkeypatch.setattr(
        deployment_endpoint.ProviderExecutionCapabilityService,
        "replace_deployment_policy",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )

    with pytest.raises(HTTPException) as exc_info:
        deployment_endpoint.replace_deployment_llm_credential_policy.__wrapped__(
            uuid.uuid4(),
            "llm-1",
            DeploymentLLMCredentialPolicyUpsert(
                model_id=uuid.uuid4(),
                credential_id=uuid.uuid4(),
            ),
            request=object(),
            x_organization_id=str(organization_id),
            db=db,
            current_user=SimpleNamespace(id=uuid.uuid4()),
        )

    assert exc_info.value.status_code == expected_status
    assert db.commits == 0
    assert db.rollbacks == 1
    if expected_code is not None:
        assert exc_info.value.detail["code"] == expected_code


def test_policy_unique_race_maps_integrity_error_to_safe_conflict(monkeypatch):
    db = _Db()
    organization_id = uuid.uuid4()
    monkeypatch.setattr(
        deployment_endpoint,
        "resolve_active_organization_id",
        lambda *_args: organization_id,
    )
    monkeypatch.setattr(
        deployment_endpoint.ProviderExecutionCapabilityService,
        "replace_deployment_policy",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            IntegrityError("statement omitted", {}, RuntimeError("unique conflict"))
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        deployment_endpoint.replace_deployment_llm_credential_policy.__wrapped__(
            uuid.uuid4(),
            "llm-1",
            DeploymentLLMCredentialPolicyUpsert(
                model_id=uuid.uuid4(),
                credential_id=uuid.uuid4(),
            ),
            request=object(),
            x_organization_id=str(organization_id),
            db=db,
            current_user=SimpleNamespace(id=uuid.uuid4()),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "selection_ambiguous"
    assert db.rollbacks == 1
