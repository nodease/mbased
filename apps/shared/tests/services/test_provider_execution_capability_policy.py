from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
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
from apps.shared.domain.provider_execution_capability import (
    CapabilityPurpose,
    ProviderExecutionBinding,
    RuntimePrincipal,
)
from apps.shared.domain.workflow_node_location import CanonicalWorkflowNodeLocation
from apps.shared.services import provider_execution_capability as capability_service
from apps.shared.services.provider_execution_capability import (
    CAPABILITY_TTL,
    DeploymentCredentialPolicyCommand,
    ProviderExecutionCapabilityAdmissionCommand,
    ProviderExecutionCapabilityIssueCommand,
    ProviderExecutionCapabilityService,
    ProviderExecutionPolicyError,
    deployment_llm_node_model_id,
)


def _graph(*, data: dict | None = None) -> dict:
    return {
        "nodes": [
            {
                "id": "llm-1",
                "type": "llmNode",
                "data": data or {"model_id": "gpt-safe"},
            }
        ],
        "edges": [],
    }


def test_policy_reads_only_the_model_identifier_from_an_llm_graph_node():
    assert deployment_llm_node_model_id(_graph(), "llm-1") == "gpt-safe"


def test_policy_resolves_the_exact_nested_location_for_repeated_node_ids():
    graph = {
        "nodes": [
            {
                "id": "llm-1",
                "type": "llmNode",
                "data": {"model_id": "gpt-root"},
            },
            {
                "id": "loop-a",
                "type": "loopNode",
                "data": {
                    "subGraph": {
                        "nodes": [
                            {
                                "id": "llm-1",
                                "type": "llmNode",
                                "data": {"model_id": "gpt-nested"},
                            }
                        ],
                        "edges": [],
                    }
                },
            },
        ],
        "edges": [],
    }

    assert deployment_llm_node_model_id(
        graph,
        "llm-1",
        container_path=(("loop", "loop-a"),),
    ) == "gpt-nested"
    assert deployment_llm_node_model_id(graph, "llm-1") == "gpt-root"


def test_policy_rejects_node_id_that_cannot_be_persisted():
    maximum_node_id = "n" * 255
    graph = _graph()
    graph["nodes"][0]["id"] = maximum_node_id

    assert deployment_llm_node_model_id(graph, maximum_node_id) == "gpt-safe"

    oversized_node_id = maximum_node_id + "n"
    graph["nodes"][0]["id"] = oversized_node_id
    with pytest.raises(ProviderExecutionPolicyError) as exc_info:
        deployment_llm_node_model_id(graph, oversized_node_id)

    assert exc_info.value.code == "configuration_required"


def test_egress_revision_includes_guarded_transport_policy_revision(monkeypatch):
    now = datetime.now(timezone.utc)
    organization_id = uuid.uuid4()
    principal_id = uuid.uuid4()
    provider_id = uuid.uuid4()
    credential_id = uuid.uuid4()
    model_id = uuid.uuid4()
    relation_id = uuid.uuid4()

    monkeypatch.setattr(
        ProviderExecutionCapabilityService,
        "_permission_revision",
        lambda *_args, **_kwargs: "permission-revision",
    )
    transport_revision = {"value": "a" * 64}
    monkeypatch.setattr(
        capability_service,
        "require_outbound_operation_profile",
        lambda _operation_id: SimpleNamespace(revision=transport_revision["value"]),
        raising=False,
    )

    common = {
        "db": object(),
        "organization_id": organization_id,
        "credential": SimpleNamespace(
            id=credential_id,
            organization_id=organization_id,
            provider_id=provider_id,
            is_valid=True,
            updated_at=now,
        ),
        "credential_principal_user_id": principal_id,
        "relation": SimpleNamespace(
            id=relation_id,
            credential_id=credential_id,
            model_id=model_id,
            is_verified=True,
            priority=1,
            created_at=now,
        ),
        "model": SimpleNamespace(
            id=model_id,
            provider_id=provider_id,
            model_id_for_api_call="synthetic-model",
            type="chat",
            is_active=True,
            input_price_1k=Decimal("0.001"),
            output_price_1k=Decimal("0.002"),
            updated_at=now,
        ),
        "provider": SimpleNamespace(
            id=provider_id,
            name="openai",
            base_url="https://provider.example/v1",
            updated_at=now,
        ),
    }

    first = ProviderExecutionCapabilityService._current_revisions(**common)
    transport_revision["value"] = "b" * 64
    second = ProviderExecutionCapabilityService._current_revisions(**common)

    assert first["egress"] != second["egress"]


@pytest.mark.parametrize(
    "data",
    [
        {"model_id": "gpt-safe", "credential_id": "do-not-use"},
        {"model_id": "gpt-safe", "credentialId": "do-not-use"},
        {"model_id": "gpt-safe", "auto_model_routing": True},
        {"model_id": "gpt-safe", "fallback_model_id": "another-model"},
        {"model_id": ""},
    ],
)
def test_policy_rejects_direct_credential_and_implicit_selection(data):
    with pytest.raises(ProviderExecutionPolicyError) as exc_info:
        deployment_llm_node_model_id(_graph(data=data), "llm-1")

    assert exc_info.value.code == "configuration_required"
    assert "do-not-use" not in str(exc_info.value)


@pytest.mark.parametrize(
    "ambiguous_selection",
    [
        {"credential_id": "do-not-use"},
        {"credentialId": "do-not-use"},
        {"auto_model_routing": True},
        {"fallback_model_id": "another-model"},
    ],
)
def test_query_embedding_policy_rejects_ambiguous_graph_selection_before_db(
    ambiguous_selection,
):
    organization_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    app_id = uuid.uuid4()
    data = {
        "model_id": "gpt-safe",
        "knowledgeBases": [{"id": "knowledge-1"}],
        **ambiguous_selection,
    }

    class _Db:
        def query(self, _entity):
            pytest.fail("ambiguous graph selection must fail before DB lookup")

    with pytest.raises(ProviderExecutionPolicyError) as exc_info:
        ProviderExecutionCapabilityService._resolve_policy_selection(
            _Db(),
            deployment=SimpleNamespace(
                app_id=app_id,
                graph_snapshot=_graph(data=data),
            ),
            app=SimpleNamespace(
                id=app_id,
                workflow_id=workflow_id,
                organization_id=organization_id,
            ),
            workflow=SimpleNamespace(
                id=workflow_id,
                organization_id=organization_id,
            ),
            organization_id=organization_id,
            node_id="llm-1",
            model_id=uuid.uuid4(),
            credential_id=uuid.uuid4(),
            credential_principal_user_id=uuid.uuid4(),
            purpose=CapabilityPurpose.QUERY_EMBEDDING,
        )

    assert exc_info.value.code == "configuration_required"
    assert "do-not-use" not in str(exc_info.value)


def test_policy_rejects_duplicate_or_wrong_node_without_disclosure():
    graph = _graph()
    graph["nodes"].append(dict(graph["nodes"][0]))

    with pytest.raises(ProviderExecutionPolicyError) as exc_info:
        deployment_llm_node_model_id(graph, "llm-1")

    assert exc_info.value.code == "configuration_required"


class _ReplacementQuery:
    def __init__(self, rows, lock_order):
        self.rows = rows
        self.lock_order = lock_order

    def filter(self, *_args):
        return self

    def populate_existing(self):
        self.lock_order.append("policy_refresh")
        return self

    def with_for_update(self):
        self.lock_order.append("policy")
        return self

    def all(self):
        return self.rows


class _ReplacementDb:
    def __init__(self, active_rows):
        self.active_rows = active_rows
        self.added = None
        self.flush_states: list[tuple[bool, bool]] = []
        self.lock_order: list[str] = []

    def query(self, *_args):
        return _ReplacementQuery(self.active_rows, self.lock_order)

    def add(self, value):
        self.added = value

    def flush(self):
        self.flush_states.append(
            (self.active_rows[0].is_active, self.added is not None)
        )
        if self.added is not None:
            now = datetime.now(timezone.utc)
            self.added.created_at = now
            self.added.updated_at = now


@pytest.mark.parametrize(
    ("purpose", "query_writes_enabled"),
    [
        (CapabilityPurpose.MAIN_GENERATION, False),
        (CapabilityPurpose.QUERY_EMBEDDING, True),
    ],
)
def test_policy_replacement_flushes_superseded_active_row_before_insert(
    monkeypatch,
    purpose,
    query_writes_enabled,
):
    organization_id = uuid.uuid4()
    deployment_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    model_id = uuid.uuid4()
    credential_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    root_location = CanonicalWorkflowNodeLocation((), "llm-1")
    old_policy = SimpleNamespace(
        is_active=True,
        policy_revision=7,
        node_id="llm-1",
        container_path=[],
        node_location_digest=root_location.digest,
    )
    db = _ReplacementDb([old_policy])

    canonical_calls: list[dict] = []
    manager_checks: list[uuid.UUID] = []
    selection_lock_modes: list[bool | None] = []

    def canonical_deployment(*_args, **kwargs):
        canonical_calls.append(kwargs)
        db.lock_order.append("deployment")
        return (
            SimpleNamespace(id=deployment_id, version=2),
            SimpleNamespace(id=uuid.uuid4()),
            SimpleNamespace(id=workflow_id),
        )

    monkeypatch.setattr(
        ProviderExecutionCapabilityService,
        "_canonical_deployment",
        staticmethod(canonical_deployment),
    )
    monkeypatch.setattr(
        capability_service,
        "has_organization_manager_permission",
        lambda _db, checked_actor_id, _organization_id: (
            manager_checks.append(checked_actor_id) or True
        ),
    )

    def resolve_policy_selection(_cls, *_args, **kwargs):
        db.lock_order.append("authorization")
        selection_lock_modes.append(kwargs.get("lock_authorization_rows"))
        return (
            SimpleNamespace(id=model_id),
            SimpleNamespace(id=credential_id),
            SimpleNamespace(),
            SimpleNamespace(),
        )

    monkeypatch.setattr(
        ProviderExecutionCapabilityService,
        "_resolve_policy_selection",
        classmethod(resolve_policy_selection),
    )

    policy = ProviderExecutionCapabilityService.replace_deployment_policy(
        db,
        actor_id=actor_id,
        command=DeploymentCredentialPolicyCommand(
            organization_id=organization_id,
            deployment_id=deployment_id,
            node_id="llm-1",
            model_id=model_id,
            credential_id=credential_id,
            purpose=purpose,
        ),
        query_embedding_policy_writes_enabled=query_writes_enabled,
    )

    assert old_policy.is_active is False
    assert db.flush_states == [(False, False), (False, True)]
    assert policy.policy_revision == 8
    assert policy.purpose is purpose
    assert policy.credential_id == credential_id
    assert canonical_calls[0]["lock"] is True
    assert db.lock_order == [
        "deployment",
        "policy_refresh",
        "policy",
        "authorization",
    ]
    assert selection_lock_modes == [True]
    assert manager_checks == [actor_id, actor_id]


def test_query_policy_write_requires_server_rollout_activation(monkeypatch):
    organization_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    db = object()
    manager_checks = []

    monkeypatch.setattr(
        ProviderExecutionCapabilityService,
        "_canonical_deployment",
        classmethod(
            lambda _cls, *_args, **_kwargs: (
                SimpleNamespace(id=uuid.uuid4(), version=1),
                SimpleNamespace(id=uuid.uuid4()),
                SimpleNamespace(id=uuid.uuid4()),
            )
        ),
    )
    monkeypatch.setattr(
        capability_service,
        "has_organization_manager_permission",
        lambda _db, checked_actor_id, _organization_id: (
            manager_checks.append(checked_actor_id) or True
        ),
    )

    with pytest.raises(ProviderExecutionPolicyError) as exc_info:
        ProviderExecutionCapabilityService.replace_deployment_policy(
            db,
            actor_id=actor_id,
            command=DeploymentCredentialPolicyCommand(
                organization_id=organization_id,
                deployment_id=uuid.uuid4(),
                node_id="llm-1",
                model_id=uuid.uuid4(),
                credential_id=uuid.uuid4(),
                purpose=CapabilityPurpose.QUERY_EMBEDDING,
            ),
            query_embedding_policy_writes_enabled=False,
        )

    assert exc_info.value.code == "query_embedding_rollout_unavailable"
    assert manager_checks == [actor_id]


def test_active_policy_unique_index_is_scoped_to_canonical_node_location():
    index = next(
        index
        for index in LLMDeploymentCredentialPolicy.__table__.indexes
        if index.name == "uq_llm_deploy_credential_policy_active"
    )

    assert [column.name for column in index.columns] == [
        "organization_id",
        "deployment_id",
        "deployment_version",
        "node_location_digest",
    ]
    assert "purpose = 'main_generation'" in str(
        index.dialect_options["postgresql"]["where"]
    )


def test_query_embedding_policy_unique_index_is_scoped_to_node_and_model():
    index = next(
        index
        for index in LLMDeploymentCredentialPolicy.__table__.indexes
        if index.name
        == "uq_llm_deploy_credential_policy_active_query_embedding"
    )

    assert [column.name for column in index.columns] == [
        "organization_id",
        "deployment_id",
        "deployment_version",
        "node_location_digest",
        "model_id",
    ]
    assert "purpose = 'query_embedding'" in str(
        index.dialect_options["postgresql"]["where"]
    )


def test_query_embedding_capability_output_cap_is_database_constrained():
    constraint = next(
        constraint
        for constraint in ProviderExecutionCapabilityRecord.__table__.constraints
        if constraint.name
        == "ck_provider_execution_capability_query_embedding_output"
    )

    assert "purpose <> 'query_embedding' OR output_token_cap = 0" in str(
        constraint.sqltext
    )


@pytest.mark.parametrize(
    ("purpose", "policy_model_id", "output_tokens"),
    [
        (CapabilityPurpose.QUERY_EMBEDDING, None, 0),
        (CapabilityPurpose.QUERY_EMBEDDING, uuid.uuid4(), 1),
        (CapabilityPurpose.MAIN_GENERATION, uuid.uuid4(), 1),
    ],
)
def test_capability_policy_slot_rejects_cross_purpose_scope(
    purpose,
    policy_model_id,
    output_tokens,
):
    with pytest.raises(ProviderExecutionPolicyError) as exc_info:
        ProviderExecutionCapabilityService._validate_capability_policy_slot(
            binding=ProviderExecutionBinding(
                organization_id=uuid.uuid4(),
                workflow_id=uuid.uuid4(),
                deployment_id=uuid.uuid4(),
                deployment_version=1,
                node_id="llm-1",
                node_invocation_id=uuid.uuid4(),
                execution_admission_id=uuid.uuid4(),
                provider_attempt_id=uuid.uuid4(),
                purpose=purpose,
            ),
            policy_model_id=policy_model_id,
            output_tokens=output_tokens,
        )

    assert exc_info.value.code == "configuration_required"


def test_stored_location_digest_mismatch_fails_closed() -> None:
    row = SimpleNamespace(
        node_id="llm-1",
        container_path=[{"kind": "loop", "node_id": "loop-a"}],
        node_location_digest="0" * 64,
    )

    with pytest.raises(ProviderExecutionPolicyError) as exc_info:
        ProviderExecutionCapabilityService._stored_location(row)

    assert exc_info.value.code == "selection_ambiguous"


def test_policy_command_rejects_non_utf8_container_id_as_configuration_required():
    command = DeploymentCredentialPolicyCommand(
        organization_id=uuid.uuid4(),
        deployment_id=uuid.uuid4(),
        node_id="llm-1",
        model_id=uuid.uuid4(),
        credential_id=uuid.uuid4(),
        container_path=(("loop", "\ud800"),),
    )

    with pytest.raises(ProviderExecutionPolicyError) as exc_info:
        ProviderExecutionCapabilityService._command_location(command)

    assert exc_info.value.code == "configuration_required"


def test_request_cost_uses_canonical_pricing_and_rounds_up():
    model = SimpleNamespace(
        input_price_1k=Decimal("0.001001"),
        output_price_1k=Decimal("0.002001"),
    )

    assert ProviderExecutionCapabilityService._request_cost_microusd(
        model,
        input_tokens=1,
        output_tokens=1,
    ) == 4


def test_request_cost_fails_closed_without_complete_pricing():
    model = SimpleNamespace(input_price_1k=None, output_price_1k=Decimal("0.1"))

    with pytest.raises(ProviderExecutionPolicyError) as exc_info:
        ProviderExecutionCapabilityService._request_cost_microusd(
            model,
            input_tokens=1,
            output_tokens=1,
        )

    assert exc_info.value.code == "configuration_required"


def test_existing_attempt_cannot_replace_runtime_principals():
    organization_id = uuid.uuid4()
    stored_user_id = uuid.uuid4()
    binding = ProviderExecutionBinding(
        organization_id=organization_id,
        workflow_id=uuid.uuid4(),
        deployment_id=uuid.uuid4(),
        deployment_version=1,
        node_id="llm-1",
        node_invocation_id=uuid.uuid4(),
        execution_admission_id=uuid.uuid4(),
        provider_attempt_id=uuid.uuid4(),
        purpose=CapabilityPurpose.MAIN_GENERATION,
    )
    record = SimpleNamespace(
        execution_subject_kind="user",
        execution_subject_id=stored_user_id,
        billing_principal_kind="organization",
        billing_principal_id=organization_id,
        audit_actor_kind="user",
        audit_actor_id=stored_user_id,
    )
    command = ProviderExecutionCapabilityIssueCommand(
        binding=binding,
        execution_subject=RuntimePrincipal.system_actor(),
        billing_principal=RuntimePrincipal.organization(organization_id),
        audit_actor=RuntimePrincipal.system_actor(),
        input_token_cap=100,
        output_token_cap=10,
        cost_cap_microusd=1_000,
    )

    assert not ProviderExecutionCapabilityService._record_matches_issue_identity(
        record,
        command,
    )


def test_admission_locks_policy_before_capability_and_uses_database_clock(monkeypatch):
    organization_id = uuid.uuid4()
    model_id = uuid.uuid4()
    credential_id = uuid.uuid4()
    provider_id = uuid.uuid4()
    principal_id = uuid.uuid4()
    policy_id = uuid.uuid4()
    capability_id = uuid.uuid4()
    binding = ProviderExecutionBinding(
        organization_id=organization_id,
        workflow_id=uuid.uuid4(),
        deployment_id=uuid.uuid4(),
        deployment_version=1,
        node_id="llm-1",
        node_invocation_id=uuid.uuid4(),
        execution_admission_id=uuid.uuid4(),
        provider_attempt_id=uuid.uuid4(),
        purpose=CapabilityPurpose.MAIN_GENERATION,
    )
    order: list[str] = []
    validation_times: list[datetime] = []
    lock_modes: dict[str, bool | None] = {}
    database_now = datetime(2026, 7, 19, 1, 2, 3, tzinfo=timezone.utc)
    root_location = CanonicalWorkflowNodeLocation((), "llm-1")
    policy = SimpleNamespace(
        id=policy_id,
        policy_revision=2,
        model_id=model_id,
        credential_id=credential_id,
        credential_principal_user_id=principal_id,
        node_id="llm-1",
        container_path=[],
        node_location_digest=root_location.digest,
    )
    record = SimpleNamespace(
        policy_id=policy_id,
        policy_revision=2,
        model_id=model_id,
        credential_id=credential_id,
        provider_id=provider_id,
        credential_principal_user_id=principal_id,
        permission_revision="a" * 64,
        relation_revision="b" * 64,
        egress_revision="c" * 64,
        pricing_revision="d" * 64,
    )
    capability = SimpleNamespace(
        input_token_cap=100,
        output_token_cap=10,
        cost_cap_microusd=10_000,
        require_usable=lambda **kwargs: validation_times.append(kwargs["now"]),
    )
    model = SimpleNamespace(
        id=model_id,
        input_price_1k=Decimal("0.001"),
        output_price_1k=Decimal("0.002"),
    )
    credential = SimpleNamespace(id=credential_id)
    provider = SimpleNamespace(id=provider_id)
    relation = SimpleNamespace()

    class _CapabilityQuery:
        def filter(self, *_args):
            return self

        def populate_existing(self):
            order.append("capability_refresh")
            return self

        def with_for_update(self):
            order.append("capability")
            return self

        def one_or_none(self):
            return record

    class _Db:
        def query(self, *_args):
            return _CapabilityQuery()

    def canonical(*_args, **_kwargs):
        order.append("deployment")
        return (
            SimpleNamespace(id=binding.deployment_id, version=1),
            SimpleNamespace(id=uuid.uuid4()),
            SimpleNamespace(id=binding.workflow_id),
        )

    def active_policy(*_args, **_kwargs):
        order.append("policy")
        return policy

    monkeypatch.setattr(
        ProviderExecutionCapabilityService,
        "_canonical_deployment",
        staticmethod(canonical),
    )
    monkeypatch.setattr(
        ProviderExecutionCapabilityService,
        "_assert_binding_matches_deployment",
        staticmethod(lambda *_args, **_kwargs: None),
    )
    monkeypatch.setattr(
        ProviderExecutionCapabilityService,
        "_active_policy_for_binding",
        classmethod(lambda _cls, *_args, **kwargs: active_policy(**kwargs)),
    )
    monkeypatch.setattr(
        ProviderExecutionCapabilityService,
        "_domain_capability",
        staticmethod(lambda _record: capability),
    )
    monkeypatch.setattr(
        ProviderExecutionCapabilityService,
        "_resolve_policy_selection",
        classmethod(
            lambda _cls, *_args, **kwargs: (
                lock_modes.update(
                    selection=kwargs.get("lock_authorization_rows")
                )
                or (model, credential, provider, relation)
            )
        ),
    )
    monkeypatch.setattr(
        ProviderExecutionCapabilityService,
        "_current_revisions",
        classmethod(
            lambda _cls, *_args, **kwargs: (
                lock_modes.update(
                    permission=kwargs.get("lock_permission_rows")
                )
                or {
                    "permission": record.permission_revision,
                    "relation": record.relation_revision,
                    "egress": record.egress_revision,
                    "pricing": record.pricing_revision,
                }
            )
        ),
    )
    monkeypatch.setattr(
        ProviderExecutionCapabilityService,
        "_database_clock_now",
        staticmethod(lambda _db: order.append("database_clock") or database_now),
        raising=False,
    )

    lease = ProviderExecutionCapabilityService.admit_capability(
        _Db(),
        command=ProviderExecutionCapabilityAdmissionCommand(
            capability_id=capability_id,
            capability_revision=1,
            binding=binding,
            requested_input_tokens=10,
            requested_output_tokens=5,
        ),
    )

    assert order == [
        "deployment",
        "policy",
        "capability_refresh",
        "capability",
        "database_clock",
        "database_clock",
    ]
    assert lock_modes == {"selection": True, "permission": True}
    assert validation_times == [database_now, database_now]
    assert lease.credential is credential


def test_database_clock_now_uses_wall_clock_timestamp():
    statements: list[object] = []
    database_now = datetime(2026, 7, 19, 1, 2, 3)

    class _Result:
        def scalar_one(self):
            return database_now

    class _Db:
        def execute(self, statement):
            statements.append(statement)
            return _Result()

    result = ProviderExecutionCapabilityService._database_clock_now(_Db())

    assert "clock_timestamp" in str(statements[0])
    assert result == database_now.replace(tzinfo=timezone.utc)


def test_issue_capability_locks_policy_before_attempt_lookup_and_uses_database_clock(
    monkeypatch,
):
    organization_id = uuid.uuid4()
    model_id = uuid.uuid4()
    credential_id = uuid.uuid4()
    provider_id = uuid.uuid4()
    principal_id = uuid.uuid4()
    policy_id = uuid.uuid4()
    binding = ProviderExecutionBinding(
        organization_id=organization_id,
        workflow_id=uuid.uuid4(),
        deployment_id=uuid.uuid4(),
        deployment_version=1,
        node_id="llm-1",
        node_invocation_id=uuid.uuid4(),
        execution_admission_id=uuid.uuid4(),
        provider_attempt_id=uuid.uuid4(),
        purpose=CapabilityPurpose.MAIN_GENERATION,
    )
    database_now = datetime(2026, 7, 19, 2, 0, tzinfo=timezone.utc)
    order: list[str] = []
    root_location = CanonicalWorkflowNodeLocation((), "llm-1")
    policy = SimpleNamespace(
        id=policy_id,
        policy_revision=1,
        model_id=model_id,
        credential_id=credential_id,
        credential_principal_user_id=principal_id,
        node_id="llm-1",
        container_path=[],
        node_location_digest=root_location.digest,
    )
    model = SimpleNamespace(id=model_id)
    credential = SimpleNamespace(id=credential_id)
    provider = SimpleNamespace(id=provider_id)
    relation = SimpleNamespace()

    class _PolicyQuery:
        def filter(self, *_args):
            return self

        def populate_existing(self):
            order.append("policy_refresh")
            return self

        def with_for_update(self):
            order.append("policy_lock")
            return self

        def all(self):
            order.append("policy_read")
            return [policy]

    class _CapabilityQuery:
        def filter(self, *_args):
            return self

        def populate_existing(self):
            order.append("capability_refresh")
            return self

        def with_for_update(self):
            order.append("capability_lock")
            return self

        def one_or_none(self):
            order.append("capability_read")
            return None

    class _Db:
        added = None

        def query(self, entity):
            if entity is LLMDeploymentCredentialPolicy:
                return _PolicyQuery()
            assert entity is ProviderExecutionCapabilityRecord
            return _CapabilityQuery()

        def add(self, record):
            self.added = record

        def flush(self):
            return None

    db = _Db()
    monkeypatch.setattr(
        ProviderExecutionCapabilityService,
        "_canonical_deployment",
        staticmethod(
            lambda *_args, **_kwargs: (
                SimpleNamespace(id=binding.deployment_id, version=1),
                SimpleNamespace(id=uuid.uuid4()),
                SimpleNamespace(id=binding.workflow_id),
            )
        ),
    )
    monkeypatch.setattr(
        ProviderExecutionCapabilityService,
        "_assert_binding_matches_deployment",
        staticmethod(lambda *_args, **_kwargs: None),
    )
    monkeypatch.setattr(
        ProviderExecutionCapabilityService,
        "_validate_issue_principals",
        staticmethod(lambda *_args, **_kwargs: None),
    )
    monkeypatch.setattr(
        ProviderExecutionCapabilityService,
        "_resolve_policy_selection",
        classmethod(
            lambda _cls, *_args, **_kwargs: (
                model,
                credential,
                provider,
                relation,
            )
        ),
    )
    monkeypatch.setattr(
        ProviderExecutionCapabilityService,
        "_current_revisions",
        classmethod(
            lambda _cls, *_args, **_kwargs: {
                "permission": "a" * 64,
                "relation": "b" * 64,
                "egress": "c" * 64,
                "pricing": "d" * 64,
            }
        ),
    )
    monkeypatch.setattr(
        ProviderExecutionCapabilityService,
        "_database_clock_now",
        staticmethod(lambda _db: order.append("database_clock") or database_now),
    )
    monkeypatch.setattr(
        ProviderExecutionCapabilityService,
        "_domain_capability",
        staticmethod(lambda record: record),
    )

    capability = ProviderExecutionCapabilityService.issue_capability(
        db,
        command=ProviderExecutionCapabilityIssueCommand(
            binding=binding,
            execution_subject=RuntimePrincipal.user(principal_id),
            billing_principal=RuntimePrincipal.organization(organization_id),
            audit_actor=RuntimePrincipal.user(principal_id),
            input_token_cap=100,
            output_token_cap=10,
            cost_cap_microusd=1_000,
        ),
    )

    assert capability is db.added
    assert capability.created_at == database_now
    assert capability.updated_at == database_now
    assert capability.expires_at == database_now + CAPABILITY_TTL
    assert order == [
        "policy_refresh",
        "policy_lock",
        "policy_read",
        "capability_refresh",
        "capability_lock",
        "capability_read",
        "database_clock",
    ]


def test_query_embedding_issue_validates_query_embedding_policy_selection(monkeypatch):
    organization_id = uuid.uuid4()
    principal_id = uuid.uuid4()
    model_id = uuid.uuid4()
    credential_id = uuid.uuid4()
    provider_id = uuid.uuid4()
    binding = ProviderExecutionBinding(
        organization_id=organization_id,
        workflow_id=uuid.uuid4(),
        deployment_id=uuid.uuid4(),
        deployment_version=1,
        node_id="llm-1",
        node_invocation_id=uuid.uuid4(),
        execution_admission_id=uuid.uuid4(),
        provider_attempt_id=uuid.uuid4(),
        purpose=CapabilityPurpose.QUERY_EMBEDDING,
    )
    policy = SimpleNamespace(
        id=uuid.uuid4(),
        policy_revision=1,
        model_id=model_id,
        credential_id=credential_id,
        credential_principal_user_id=principal_id,
    )
    selection_purposes: list[CapabilityPurpose] = []

    class _CapabilityQuery:
        def filter(self, *_args):
            return self

        def populate_existing(self):
            return self

        def with_for_update(self):
            return self

        def one_or_none(self):
            return None

    class _Db:
        def query(self, entity):
            assert entity is ProviderExecutionCapabilityRecord
            return _CapabilityQuery()

        def add(self, _record):
            return None

        def flush(self):
            return None

    monkeypatch.setattr(
        ProviderExecutionCapabilityService,
        "_canonical_deployment",
        staticmethod(
            lambda *_args, **_kwargs: (
                SimpleNamespace(id=binding.deployment_id, version=1),
                SimpleNamespace(id=uuid.uuid4()),
                SimpleNamespace(id=binding.workflow_id),
            )
        ),
    )
    monkeypatch.setattr(
        ProviderExecutionCapabilityService,
        "_assert_binding_matches_deployment",
        staticmethod(lambda *_args, **_kwargs: None),
    )
    monkeypatch.setattr(
        ProviderExecutionCapabilityService,
        "_active_policy_for_binding",
        classmethod(lambda _cls, *_args, **_kwargs: policy),
    )
    monkeypatch.setattr(
        ProviderExecutionCapabilityService,
        "_validate_issue_principals",
        staticmethod(lambda *_args, **_kwargs: None),
    )
    monkeypatch.setattr(
        ProviderExecutionCapabilityService,
        "_resolve_policy_selection",
        classmethod(
            lambda _cls, *_args, **kwargs: (
                selection_purposes.append(kwargs["purpose"])
                or (
                    SimpleNamespace(id=model_id),
                    SimpleNamespace(id=credential_id),
                    SimpleNamespace(id=provider_id),
                    SimpleNamespace(),
                )
            )
        ),
    )
    monkeypatch.setattr(
        ProviderExecutionCapabilityService,
        "_current_revisions",
        classmethod(
            lambda _cls, *_args, **_kwargs: {
                "permission": "a" * 64,
                "relation": "b" * 64,
                "egress": "c" * 64,
                "pricing": "d" * 64,
            }
        ),
    )
    monkeypatch.setattr(
        ProviderExecutionCapabilityService,
        "_database_clock_now",
        staticmethod(lambda _db: datetime(2026, 7, 22, tzinfo=timezone.utc)),
    )
    monkeypatch.setattr(
        ProviderExecutionCapabilityService,
        "_domain_capability",
        staticmethod(lambda record: record),
    )

    ProviderExecutionCapabilityService.issue_capability(
        _Db(),
        command=ProviderExecutionCapabilityIssueCommand(
            binding=binding,
            execution_subject=RuntimePrincipal.user(principal_id),
            billing_principal=RuntimePrincipal.organization(organization_id),
            audit_actor=RuntimePrincipal.user(principal_id),
            input_token_cap=100,
            output_token_cap=0,
            cost_cap_microusd=1_000,
            policy_model_id=model_id,
        ),
    )

    assert selection_purposes == [CapabilityPurpose.QUERY_EMBEDDING]


def test_policy_selection_locks_runtime_authorization_rows(monkeypatch):
    organization_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    app_id = uuid.uuid4()
    provider_id = uuid.uuid4()
    model_id = uuid.uuid4()
    credential_id = uuid.uuid4()
    principal_id = uuid.uuid4()
    model = SimpleNamespace(
        id=model_id,
        provider_id=provider_id,
        model_id_for_api_call="gpt-safe",
        type="chat",
        is_active=True,
    )
    provider = SimpleNamespace(id=provider_id)
    credential = SimpleNamespace(
        id=credential_id,
        organization_id=organization_id,
        provider_id=provider_id,
        is_valid=True,
    )
    relation = SimpleNamespace(
        credential_id=credential_id,
        model_id=model_id,
        is_verified=True,
    )
    rows = {
        LLMModel: [model],
        LLMProvider: [provider],
        LLMCredential: [credential],
        LLMRelCredentialModel: [relation],
    }
    refreshed: list[type] = []
    locked: list[type] = []
    permission_lock_modes: list[bool] = []

    class _Query:
        def __init__(self, entity):
            self.entity = entity

        def filter(self, *_args):
            return self

        def populate_existing(self):
            refreshed.append(self.entity)
            return self

        def with_for_update(self):
            locked.append(self.entity)
            return self

        def one_or_none(self):
            values = rows[self.entity]
            return values[0] if values else None

        def all(self):
            return rows[self.entity]

    class _Db:
        def query(self, entity):
            return _Query(entity)

    monkeypatch.setattr(
        capability_service,
        "deployment_llm_node_model_id",
        lambda *_args, **_kwargs: "gpt-safe",
    )
    monkeypatch.setattr(
        capability_service,
        "has_llm_credential_permission",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        ProviderExecutionCapabilityService,
        "_permission_revision",
        staticmethod(
            lambda *_args, **kwargs: (
                permission_lock_modes.append(kwargs["lock_rows"]) or "a" * 64
            )
        ),
    )

    selection = ProviderExecutionCapabilityService._resolve_policy_selection(
        _Db(),
        deployment=SimpleNamespace(app_id=app_id, graph_snapshot={}),
        app=SimpleNamespace(
            id=app_id,
            workflow_id=workflow_id,
            organization_id=organization_id,
        ),
        workflow=SimpleNamespace(id=workflow_id, organization_id=organization_id),
        organization_id=organization_id,
        node_id="llm-1",
        model_id=model_id,
        credential_id=credential_id,
        credential_principal_user_id=principal_id,
        lock_authorization_rows=True,
    )

    assert selection == (model, credential, provider, relation)
    assert refreshed == [LLMModel, LLMProvider, LLMCredential, LLMRelCredentialModel]
    assert locked == [LLMModel, LLMProvider, LLMCredential, LLMRelCredentialModel]
    assert permission_lock_modes == [True]


def test_generation_policy_rejects_embedding_model_before_provider_selection(
    monkeypatch,
):
    organization_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    app_id = uuid.uuid4()
    model_id = uuid.uuid4()
    model = SimpleNamespace(
        id=model_id,
        provider_id=uuid.uuid4(),
        model_id_for_api_call="gpt-safe",
        type="embedding",
        is_active=True,
    )

    class _Query:
        def filter(self, *_args):
            return self

        def one_or_none(self):
            return model

    class _Db:
        def query(self, entity):
            if entity is not LLMModel:
                pytest.fail("embedding model must be rejected before related rows")
            return _Query()

    monkeypatch.setattr(
        capability_service,
        "deployment_llm_node_model_id",
        lambda *_args, **_kwargs: "gpt-safe",
    )

    with pytest.raises(ProviderExecutionPolicyError) as exc_info:
        ProviderExecutionCapabilityService._resolve_policy_selection(
            _Db(),
            deployment=SimpleNamespace(app_id=app_id, graph_snapshot={}),
            app=SimpleNamespace(
                id=app_id,
                workflow_id=workflow_id,
                organization_id=organization_id,
            ),
            workflow=SimpleNamespace(
                id=workflow_id,
                organization_id=organization_id,
            ),
            organization_id=organization_id,
            node_id="llm-1",
            model_id=model_id,
            credential_id=uuid.uuid4(),
            credential_principal_user_id=uuid.uuid4(),
        )

    assert exc_info.value.code == "configuration_required"


def test_query_embedding_policy_accepts_only_embedding_model_on_knowledge_node(
    monkeypatch,
):
    organization_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    app_id = uuid.uuid4()
    provider_id = uuid.uuid4()
    model_id = uuid.uuid4()
    credential_id = uuid.uuid4()
    principal_id = uuid.uuid4()
    model = SimpleNamespace(
        id=model_id,
        provider_id=provider_id,
        model_id_for_api_call="embed-safe",
        type="embedding",
        is_active=True,
    )
    provider = SimpleNamespace(id=provider_id)
    credential = SimpleNamespace(
        id=credential_id,
        organization_id=organization_id,
        provider_id=provider_id,
        is_valid=True,
    )
    relation = SimpleNamespace(
        credential_id=credential_id,
        model_id=model_id,
        is_verified=True,
    )
    rows = {
        LLMModel: [model],
        LLMProvider: [provider],
        LLMCredential: [credential],
        LLMRelCredentialModel: [relation],
    }

    class _Query:
        def __init__(self, entity):
            self.entity = entity

        def filter(self, *_args):
            return self

        def one_or_none(self):
            values = rows[self.entity]
            return values[0] if values else None

        def all(self):
            return rows[self.entity]

    class _Db:
        def query(self, entity):
            return _Query(entity)

    monkeypatch.setattr(
        capability_service,
        "has_llm_credential_permission",
        lambda *_args, **_kwargs: True,
    )

    selection = ProviderExecutionCapabilityService._resolve_policy_selection(
        _Db(),
        deployment=SimpleNamespace(
            app_id=app_id,
            graph_snapshot=_graph(
                data={
                    "model_id": "gpt-safe",
                    "knowledgeBases": [{"id": str(uuid.uuid4())}],
                }
            ),
        ),
        app=SimpleNamespace(
            id=app_id,
            workflow_id=workflow_id,
            organization_id=organization_id,
        ),
        workflow=SimpleNamespace(id=workflow_id, organization_id=organization_id),
        organization_id=organization_id,
        node_id="llm-1",
        model_id=model_id,
        credential_id=credential_id,
        credential_principal_user_id=principal_id,
        purpose=CapabilityPurpose.QUERY_EMBEDDING,
    )

    assert selection == (model, credential, provider, relation)


def test_permission_revision_locks_every_existing_permission_source(monkeypatch):
    refreshed: list[tuple[type, ...]] = []
    locked: list[tuple[type, ...]] = []

    class _Query:
        def __init__(self, entities):
            self.entities = entities

        def filter(self, *_args):
            return self

        def join(self, *_args):
            return self

        def populate_existing(self):
            refreshed.append(self.entities)
            return self

        def with_for_update(self):
            locked.append(self.entities)
            return self

        def one_or_none(self):
            return None

        def all(self):
            return []

    class _Db:
        def query(self, *entities):
            return _Query(entities)

    monkeypatch.setattr(
        capability_service,
        "get_effective_llm_credential_auth_state",
        lambda *_args, **_kwargs: "none",
    )

    revision = ProviderExecutionCapabilityService._permission_revision(
        _Db(),
        organization_id=uuid.uuid4(),
        credential_id=uuid.uuid4(),
        credential_principal_user_id=uuid.uuid4(),
        lock_rows=True,
    )

    assert len(revision) == 64
    assert refreshed == [
        (Organization,),
        (User,),
        (OrganizationMembership,),
        (UserLLMPermission,),
        (TeamLLMPermission, TeamMembership, Team),
    ]
    assert locked == [
        (Organization,),
        (User,),
        (OrganizationMembership,),
        (UserLLMPermission,),
        (TeamLLMPermission, TeamMembership, Team),
    ]


def test_permission_revision_fingerprints_user_and_organization_state(monkeypatch):
    organization_id = uuid.uuid4()
    principal_id = uuid.uuid4()
    credential_id = uuid.uuid4()
    created_at = datetime.now(timezone.utc)
    organization = SimpleNamespace(
        id=organization_id,
        is_active=True,
        created_by=principal_id,
        managed_by=None,
        deactivated_at=None,
        updated_at=created_at,
    )
    user = SimpleNamespace(
        id=principal_id,
        deactivated_at=None,
        updated_at=created_at,
    )

    class _Query:
        def __init__(self, entities):
            self.entities = entities

        def filter(self, *_args):
            return self

        def join(self, *_args):
            return self

        def one_or_none(self):
            if self.entities == (Organization,):
                return organization
            if self.entities == (User,):
                return user
            return None

        def all(self):
            return []

    class _Db:
        def query(self, *entities):
            return _Query(entities)

    monkeypatch.setattr(
        capability_service,
        "get_effective_llm_credential_auth_state",
        lambda *_args, **_kwargs: "manager",
    )

    active_revision = ProviderExecutionCapabilityService._permission_revision(
        _Db(),
        organization_id=organization_id,
        credential_id=credential_id,
        credential_principal_user_id=principal_id,
    )
    organization.is_active = False
    inactive_organization_revision = (
        ProviderExecutionCapabilityService._permission_revision(
            _Db(),
            organization_id=organization_id,
            credential_id=credential_id,
            credential_principal_user_id=principal_id,
        )
    )
    organization.is_active = True
    user.deactivated_at = created_at
    inactive_user_revision = ProviderExecutionCapabilityService._permission_revision(
        _Db(),
        organization_id=organization_id,
        credential_id=credential_id,
        credential_principal_user_id=principal_id,
    )

    assert inactive_organization_revision != active_revision
    assert inactive_user_revision != active_revision


def test_policy_and_capability_are_cascaded_with_deleted_deployment_control():
    def ondelete_for(table, targets):
        constraint = next(
            constraint
            for constraint in table.foreign_key_constraints
            if tuple(element.target_fullname for element in constraint.elements) == targets
        )
        return constraint.ondelete

    assert ondelete_for(
        LLMDeploymentCredentialPolicy.__table__,
        ("workflows.id",),
    ) == "CASCADE"
    assert ondelete_for(
        LLMDeploymentCredentialPolicy.__table__,
        ("workflow_deployments.id",),
    ) == "CASCADE"
    assert ondelete_for(
        ProviderExecutionCapabilityRecord.__table__,
        (
            "llm_deployment_credential_policies.id",
            "llm_deployment_credential_policies.organization_id",
        ),
    ) == "CASCADE"
    assert ondelete_for(
        ProviderExecutionCapabilityRecord.__table__,
        ("workflows.id",),
    ) == "CASCADE"
    assert ondelete_for(
        ProviderExecutionCapabilityRecord.__table__,
        ("workflow_deployments.id",),
    ) == "CASCADE"
