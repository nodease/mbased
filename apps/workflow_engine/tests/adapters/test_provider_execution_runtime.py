from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from apps.shared.domain.provider_execution_capability import RuntimePrincipal
from apps.shared.domain.workflow_execution_identity import InvocationSegment
from apps.shared.services.provider_execution_capability import (
    ProviderExecutionPolicyError,
)
from apps.workflow_engine.adapters.provider_execution_capability import (
    CapabilityProviderExecutionAdapter,
    provider_visible_request_bounds,
)
from apps.workflow_engine.application.provider_execution import (
    LLMCredentialNotAvailableError,
    ProviderExecutionAuditActorKind,
    ProviderExecutionConfigurationError,
    ProviderExecutionPreflight,
    ProviderExecutionRequest,
)
from apps.workflow_engine.domain.execution import NodeExecutionControl
from apps.workflow_engine.domain.external_effect import ExternalEffectContext


class _Session:
    def __init__(self) -> None:
        self.commits = 0
        self.closes = 0

    def commit(self) -> None:
        self.commits += 1

    def close(self) -> None:
        self.closes += 1


class _Client:
    def __init__(self, session: _Session) -> None:
        self.session = session
        self.calls = 0

    def invoke_sync(self, *, messages, **parameters):
        assert self.session.commits == 1
        assert self.session.closes == 1
        self.calls += 1
        return {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }

    @staticmethod
    def build_json_schema_response_format(*, name, schema):
        return {
            "type": "json_schema",
            "json_schema": {
                "name": name,
                "strict": True,
                "schema": schema,
            },
        }


def _control(
    *,
    organization_id: uuid.UUID,
    workflow_id: uuid.UUID,
    binding_container_path: tuple[tuple[str, str], ...] = (),
):
    execution_id = uuid.uuid4()
    return NodeExecutionControl(
        execution_id=execution_id,
        invocation_path_prefix=(InvocationSegment("root", "", "workflow"),),
        external_effect_context=ExternalEffectContext(
            organization_id=organization_id,
            app_id=uuid.uuid4(),
            workflow_id=workflow_id,
            execution_id=execution_id,
            node_invocation_id=uuid.uuid4(),
            node_id="llm-1",
        ),
        external_effect_enforced=True,
        binding_container_path=binding_container_path,
    )


def _capability_context(
    *,
    organization_id: uuid.UUID,
    workflow_id: uuid.UUID,
) -> dict:
    return {
        "provider_execution_capability_required": True,
        "provider_execution_capability_limits": {
            "input_token_cap": 10_000,
            "output_token_cap": 100,
            "cost_cap_microusd": 50_000,
        },
        "deployment_id": str(uuid.uuid4()),
        "workflow_version": 2,
        "organization_id": str(organization_id),
        "workflow_id": str(workflow_id),
        "execution_subject": {"type": "user", "id": str(uuid.uuid4())},
    }


def test_capability_runtime_readmits_final_json_schema_request_before_provider_io():
    organization_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    policy_principal_id = uuid.uuid4()
    model_db_id = uuid.uuid4()
    credential_id = uuid.uuid4()
    provider_id = uuid.uuid4()
    initial_session = _Session()
    final_session = _Session()
    sessions = iter((initial_session, final_session))
    captured: dict = {}
    pricing_revision = "d" * 64

    capability_id = uuid.uuid4()

    class _CapabilityService:
        @staticmethod
        def issue_capability(_db, *, command):
            captured["issue"] = command
            capability = SimpleNamespace(
                id=capability_id,
                revision=3,
                binding=command.binding,
                policy_id=uuid.uuid4(),
                policy_revision=2,
                provider_id=provider_id,
                model_id=model_db_id,
                credential_id=credential_id,
                credential_principal=RuntimePrincipal.user(policy_principal_id),
                permission_revision="a" * 64,
                relation_revision="b" * 64,
                egress_revision="c" * 64,
                pricing_revision=pricing_revision,
                input_token_cap=command.input_token_cap,
                output_token_cap=command.output_token_cap,
                cost_cap_microusd=command.cost_cap_microusd,
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            )
            captured["capability"] = capability
            return capability

        @staticmethod
        def admit_capability(_db, *, command):
            captured.setdefault("admissions", []).append(command)
            return SimpleNamespace(
                credential=SimpleNamespace(id=credential_id),
                provider=SimpleNamespace(
                    id=provider_id,
                    name="provider",
                    base_url="https://catalog.example.test/v1",
                ),
                model=SimpleNamespace(
                    id=model_db_id,
                    model_id_for_api_call="gpt-safe",
                    input_price_1k=Decimal("0.001"),
                    output_price_1k=Decimal("0.002"),
                ),
                capability=captured["capability"],
            )

    client = _Client(initial_session)
    runtime = CapabilityProviderExecutionAdapter(
        session_factory=lambda: next(sessions),
        capability_service=_CapabilityService,
        credential_loader=lambda _credential: {"apiKey": "[REDACTED]"},
        client_factory=lambda **_kwargs: client,
    )
    context = _capability_context(
        organization_id=organization_id,
        workflow_id=workflow_id,
    )
    plan = runtime.preflight(
        ProviderExecutionPreflight(
            node_id="llm-1",
            configured_model_id="gpt-safe",
            auto_model_routing=False,
            fallback_model_id=None,
            knowledge_enabled=False,
            memory_summary_requested=False,
            client_override=None,
            execution_context=context,
            runtime_control=_control(
                organization_id=organization_id,
                workflow_id=workflow_id,
                binding_container_path=(("loop", "loop-a"),),
            ),
        )
    )

    lease = runtime.resolve(
        ProviderExecutionRequest(
            plan=plan,
            model_id="gpt-safe",
            messages=({"role": "user", "content": "safe"},),
            parameters={},
            shared_session=object(),
        )
    )

    with pytest.raises(ProviderExecutionConfigurationError):
        runtime.resolve(
            ProviderExecutionRequest(
                plan=plan,
                model_id="gpt-safe",
                messages=({"role": "user", "content": "safe"},),
                parameters={},
                shared_session=object(),
            )
        )

    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
    }
    assert lease.apply_json_schema_response_format(
        name="workflow_node_output",
        schema=schema,
    )
    response_format = client.build_json_schema_response_format(
        name="workflow_node_output",
        schema=schema,
    )
    final_input_tokens, final_output_tokens, _ = provider_visible_request_bounds(
        messages=({"role": "user", "content": "safe"},),
        parameters={"max_tokens": 100, "response_format": response_format},
        output_token_cap=100,
    )

    assert initial_session.commits == 1
    assert initial_session.closes == 1
    assert final_session.commits == 1
    assert final_session.closes == 1
    assert plan.audit_actor is not None
    assert plan.audit_actor.kind is ProviderExecutionAuditActorKind.USER
    assert plan.audit_actor.reference_id == uuid.UUID(
        context["execution_subject"]["id"]
    )
    assert lease.attribution.model_db_id == model_db_id
    assert lease.attribution.credential_principal_user_id == policy_principal_id
    assert lease.attribution.pricing_snapshot.revision == pricing_revision
    assert lease.attribution.pricing_snapshot.input_price_per_1k == Decimal("0.001")
    assert lease.attribution.pricing_snapshot.output_price_per_1k == Decimal("0.002")
    assert captured["issue"].binding.node_id == "llm-1"
    assert captured["issue"].binding.container_path == (("loop", "loop-a"),)
    assert lease.attribution.usage_context is not None
    assert lease.attribution.usage_context.binding.container_path == (
        ("loop", "loop-a"),
    )
    assert len(captured["admissions"]) == 2
    assert captured["admissions"][1].requested_input_tokens == final_input_tokens
    assert captured["admissions"][1].requested_output_tokens == final_output_tokens
    assert (
        lease.attribution.usage_context.admitted_input_tokens
        == final_input_tokens
    )
    assert (
        lease.attribution.usage_context.admitted_output_tokens
        == final_output_tokens
    )
    assert lease.invoke()["choices"][0]["message"]["content"] == "ok"
    assert client.calls == 1


def test_capability_preflight_rejects_malformed_trusted_container_path() -> None:
    organization_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    runtime = CapabilityProviderExecutionAdapter(
        session_factory=lambda: pytest.fail("invalid path must fail before DB access")
    )

    with pytest.raises(ProviderExecutionConfigurationError):
        runtime.preflight(
            ProviderExecutionPreflight(
                node_id="llm-1",
                configured_model_id="gpt-safe",
                auto_model_routing=False,
                fallback_model_id=None,
                knowledge_enabled=False,
                memory_summary_requested=False,
                client_override=None,
                execution_context=_capability_context(
                    organization_id=organization_id,
                    workflow_id=workflow_id,
                ),
                runtime_control=_control(
                    organization_id=organization_id,
                    workflow_id=workflow_id,
                    binding_container_path=(("future", "container"),),
                ),
            )
        )


@pytest.mark.parametrize(
    ("audience", "expected_kind"),
    [
        ("anonymous_public", ProviderExecutionAuditActorKind.PUBLIC),
        ("system", ProviderExecutionAuditActorKind.SYSTEM),
    ],
)
def test_capability_preflight_preserves_non_user_audit_actor(
    audience,
    expected_kind,
):
    organization_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    context = _capability_context(
        organization_id=organization_id,
        workflow_id=workflow_id,
    )
    context.pop("execution_subject")
    context["provider_execution_audience"] = audience
    runtime = CapabilityProviderExecutionAdapter(
        session_factory=lambda: pytest.fail("preflight must not open a session")
    )

    plan = runtime.preflight(
        ProviderExecutionPreflight(
            node_id="llm-1",
            configured_model_id="gpt-safe",
            auto_model_routing=False,
            fallback_model_id=None,
            knowledge_enabled=False,
            memory_summary_requested=False,
            client_override=None,
            execution_context=context,
            runtime_control=_control(
                organization_id=organization_id,
                workflow_id=workflow_id,
            ),
        )
    )

    assert plan.audit_actor is not None
    assert plan.audit_actor.kind is expected_kind
    assert plan.audit_actor.reference_id is None


def test_capability_runtime_does_not_materialize_client_after_admission_failure():
    organization_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    session = _Session()

    class _CapabilityService:
        @staticmethod
        def issue_capability(_db, *, command):
            return SimpleNamespace(id=uuid.uuid4(), revision=1)

        @staticmethod
        def admit_capability(_db, *, command):
            raise ProviderExecutionPolicyError("capability_stale")

    runtime = CapabilityProviderExecutionAdapter(
        session_factory=lambda: session,
        capability_service=_CapabilityService,
        client_factory=lambda **_kwargs: pytest.fail(
            "provider client must not be materialized"
        ),
    )
    plan = runtime.preflight(
        ProviderExecutionPreflight(
            node_id="llm-1",
            configured_model_id="gpt-safe",
            auto_model_routing=False,
            fallback_model_id=None,
            knowledge_enabled=False,
            memory_summary_requested=False,
            client_override=None,
            execution_context=_capability_context(
                organization_id=organization_id,
                workflow_id=workflow_id,
            ),
            runtime_control=_control(
                organization_id=organization_id,
                workflow_id=workflow_id,
            ),
        )
    )

    with pytest.raises(Exception) as exc_info:
        runtime.resolve(
            ProviderExecutionRequest(
                plan=plan,
                model_id="gpt-safe",
                messages=({"role": "user", "content": "safe"},),
                parameters={"max_tokens": 5},
                shared_session=object(),
            )
        )

    assert getattr(exc_info.value, "reason", None) == (
        "provider_capability_capability_stale"
    )
    assert session.commits == 0
    assert session.closes == 1


def test_wrong_nested_location_fails_before_provider_client_materialization():
    organization_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    session = _Session()
    approved_path = (("loop", "loop-a"),)
    attempted_path = (("loop", "loop-b"),)
    observed_paths: list[tuple[tuple[str, str], ...]] = []

    class _CapabilityService:
        @staticmethod
        def issue_capability(_db, *, command):
            observed_paths.append(command.binding.container_path)
            if command.binding.container_path != approved_path:
                raise ProviderExecutionPolicyError("configuration_required")
            return SimpleNamespace(id=uuid.uuid4(), revision=1)

        @staticmethod
        def admit_capability(_db, *, command):
            pytest.fail("wrong location must fail before capability admission")

    runtime = CapabilityProviderExecutionAdapter(
        session_factory=lambda: session,
        capability_service=_CapabilityService,
        client_factory=lambda **_kwargs: pytest.fail(
            "provider client must not be materialized for another Loop location"
        ),
    )
    plan = runtime.preflight(
        ProviderExecutionPreflight(
            node_id="llm-1",
            configured_model_id="gpt-safe",
            auto_model_routing=False,
            fallback_model_id=None,
            knowledge_enabled=False,
            memory_summary_requested=False,
            client_override=None,
            execution_context=_capability_context(
                organization_id=organization_id,
                workflow_id=workflow_id,
            ),
            runtime_control=_control(
                organization_id=organization_id,
                workflow_id=workflow_id,
                binding_container_path=attempted_path,
            ),
        )
    )

    with pytest.raises(LLMCredentialNotAvailableError) as exc_info:
        runtime.resolve(
            ProviderExecutionRequest(
                plan=plan,
                model_id="gpt-safe",
                messages=({"role": "user", "content": "safe"},),
                parameters={"max_tokens": 5},
                shared_session=object(),
            )
        )

    assert exc_info.value.reason == "provider_capability_configuration_required"
    assert observed_paths == [attempted_path]
    assert session.commits == 0
    assert session.closes == 1


@pytest.mark.parametrize(
    ("admitted_model_id", "credential_principal", "expected_reason"),
    [
        (
            "different-model",
            RuntimePrincipal.user(uuid.uuid4()),
            "provider_capability_model_mismatch",
        ),
        (
            "gpt-safe",
            RuntimePrincipal.system_actor(),
            "provider_capability_attribution_invalid",
        ),
    ],
)
def test_capability_runtime_rejects_invalid_admission_before_materialization(
    admitted_model_id,
    credential_principal,
    expected_reason,
):
    organization_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    session = _Session()
    materialization_calls = 0

    class _CapabilityService:
        @staticmethod
        def issue_capability(_db, *, command):
            return SimpleNamespace(id=uuid.uuid4(), revision=1)

        @staticmethod
        def admit_capability(_db, *, command):
            return SimpleNamespace(
                credential=SimpleNamespace(id=uuid.uuid4()),
                provider=SimpleNamespace(
                    name="provider",
                    base_url="https://catalog.example.test/v1",
                ),
                model=SimpleNamespace(
                    id=uuid.uuid4(),
                    model_id_for_api_call=admitted_model_id,
                ),
                capability=SimpleNamespace(
                    credential_principal=credential_principal,
                ),
            )

    def credential_loader(_credential):
        nonlocal materialization_calls
        materialization_calls += 1
        return {"apiKey": "[REDACTED]"}

    runtime = CapabilityProviderExecutionAdapter(
        session_factory=lambda: session,
        capability_service=_CapabilityService,
        credential_loader=credential_loader,
        client_factory=lambda **_kwargs: pytest.fail(
            "provider client must not be materialized"
        ),
    )
    plan = runtime.preflight(
        ProviderExecutionPreflight(
            node_id="llm-1",
            configured_model_id="gpt-safe",
            auto_model_routing=False,
            fallback_model_id=None,
            knowledge_enabled=False,
            memory_summary_requested=False,
            client_override=None,
            execution_context=_capability_context(
                organization_id=organization_id,
                workflow_id=workflow_id,
            ),
            runtime_control=_control(
                organization_id=organization_id,
                workflow_id=workflow_id,
            ),
        )
    )

    with pytest.raises(Exception) as exc_info:
        runtime.resolve(
            ProviderExecutionRequest(
                plan=plan,
                model_id="gpt-safe",
                messages=({"role": "user", "content": "safe"},),
                parameters={"max_tokens": 5},
                shared_session=object(),
            )
        )

    assert getattr(exc_info.value, "reason", None) == expected_reason

    assert materialization_calls == 0
    assert session.commits == 0
    assert session.closes == 1


def test_capability_runtime_hides_config_failure_and_does_not_commit():
    organization_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    session = _Session()

    class _CapabilityService:
        @staticmethod
        def issue_capability(_db, *, command):
            return SimpleNamespace(id=uuid.uuid4(), revision=1)

        @staticmethod
        def admit_capability(_db, *, command):
            return SimpleNamespace(
                credential=SimpleNamespace(id=uuid.uuid4()),
                provider=SimpleNamespace(
                    name="provider",
                    base_url="https://catalog.example.test/v1",
                ),
                model=SimpleNamespace(
                    id=uuid.uuid4(),
                    model_id_for_api_call="gpt-safe",
                    input_price_1k=Decimal("0.001"),
                    output_price_1k=Decimal("0.002"),
                ),
                capability=SimpleNamespace(
                    credential_principal=RuntimePrincipal.user(uuid.uuid4()),
                    pricing_revision="d" * 64,
                ),
            )

    runtime = CapabilityProviderExecutionAdapter(
        session_factory=lambda: session,
        capability_service=_CapabilityService,
        credential_loader=lambda _credential: (_ for _ in ()).throw(
            ValueError("internal")
        ),
        client_factory=lambda **_kwargs: pytest.fail(
            "provider client must not be materialized"
        ),
    )
    plan = runtime.preflight(
        ProviderExecutionPreflight(
            node_id="llm-1",
            configured_model_id="gpt-safe",
            auto_model_routing=False,
            fallback_model_id=None,
            knowledge_enabled=False,
            memory_summary_requested=False,
            client_override=None,
            execution_context=_capability_context(
                organization_id=organization_id,
                workflow_id=workflow_id,
            ),
            runtime_control=_control(
                organization_id=organization_id,
                workflow_id=workflow_id,
            ),
        )
    )

    with pytest.raises(Exception) as exc_info:
        runtime.resolve(
            ProviderExecutionRequest(
                plan=plan,
                model_id="gpt-safe",
                messages=({"role": "user", "content": "safe"},),
                parameters={"max_tokens": 5},
                shared_session=object(),
            )
        )

    assert getattr(exc_info.value, "reason", None) == "credential_config_invalid"
    assert exc_info.value.__cause__ is None
    assert session.commits == 0
    assert session.closes == 1


def test_capability_generation_preflight_leaves_rag_to_query_embedding_port():
    organization_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    calls = 0

    def session_factory():
        nonlocal calls
        calls += 1
        return _Session()

    runtime = CapabilityProviderExecutionAdapter(session_factory=session_factory)

    plan = runtime.preflight(
        ProviderExecutionPreflight(
            node_id="llm-1",
            configured_model_id="gpt-safe",
            auto_model_routing=False,
            fallback_model_id=None,
            knowledge_enabled=True,
            memory_summary_requested=False,
            client_override=None,
            execution_context=_capability_context(
                organization_id=organization_id,
                workflow_id=workflow_id,
            ),
            runtime_control=_control(
                organization_id=organization_id,
                workflow_id=workflow_id,
            ),
        )
    )

    assert plan.fixed_model_id == "gpt-safe"
    assert calls == 0


def test_capability_runtime_rejects_shared_workflow_session():
    organization_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    shared_session = _Session()
    runtime = CapabilityProviderExecutionAdapter(
        session_factory=lambda: shared_session,
    )
    plan = runtime.preflight(
        ProviderExecutionPreflight(
            node_id="llm-1",
            configured_model_id="gpt-safe",
            auto_model_routing=False,
            fallback_model_id=None,
            knowledge_enabled=False,
            memory_summary_requested=False,
            client_override=None,
            execution_context=_capability_context(
                organization_id=organization_id,
                workflow_id=workflow_id,
            ),
            runtime_control=_control(
                organization_id=organization_id,
                workflow_id=workflow_id,
            ),
        )
    )

    with pytest.raises(ProviderExecutionConfigurationError):
        runtime.resolve(
            ProviderExecutionRequest(
                plan=plan,
                model_id="gpt-safe",
                messages=({"role": "user", "content": "safe"},),
                parameters={},
                shared_session=shared_session,
            )
        )

    assert shared_session.commits == 0
    assert shared_session.closes == 0


@pytest.mark.parametrize(
    "parameters",
    [
        {"model": "unapproved", "max_tokens": 5},
        {"n": 2, "max_tokens": 5},
        {"best_of": 2, "max_tokens": 5},
        {"max_completion_tokens": 5},
        {"max_output_tokens": 5},
    ],
)
def test_provider_visible_request_bounds_rejects_override_surfaces(parameters):
    with pytest.raises(ProviderExecutionConfigurationError):
        provider_visible_request_bounds(
            messages=({"role": "user", "content": "safe"},),
            parameters=parameters,
            output_token_cap=10,
        )
