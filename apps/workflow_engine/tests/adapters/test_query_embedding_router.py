from __future__ import annotations

import uuid
from dataclasses import replace

import pytest

from apps.shared.domain.embedding_model_binding import EmbeddingModelBinding
from apps.workflow_engine.adapters.query_embedding import (
    QueryEmbeddingExecutionRuntimeRouter,
)
from apps.workflow_engine.adapters.query_embedding_legacy import (
    LegacyQueryEmbeddingAdapter,
)
from apps.workflow_engine.application.query_embedding_execution import (
    QueryEmbeddingConfigurationError,
    QueryEmbeddingExecutionRequest,
    QueryEmbeddingExecutionResult,
    QueryEmbeddingExecutionService,
    QueryEmbeddingPlan,
    QueryEmbeddingPreflight,
    QueryEmbeddingProviderRequest,
    QueryEmbeddingProviderResult,
)


class _Strategy:
    def __init__(self, name: str) -> None:
        self.name = name
        self.preflight_calls = []
        self.invoke_calls = []

    def preflight(self, request):
        self.preflight_calls.append(request)
        return QueryEmbeddingPlan(
            capability_required=self.name == "capability",
            organization_id=request.organization_id,
            node_id=request.node_id,
            state=self.name,
        )

    def invoke(self, request):
        self.invoke_calls.append(request)
        return QueryEmbeddingProviderResult(vector=(0.1,), input_tokens=1)


def _preflight(*, required):
    return QueryEmbeddingPreflight(
        node_id="llm-1",
        organization_id=uuid.uuid4(),
        legacy_credential_user_id=uuid.uuid4(),
        execution_context={"provider_execution_capability_required": required},
        runtime_control=None,
    )


def test_query_embedding_router_uses_only_server_boolean_strategy():
    legacy = _Strategy("legacy")
    capability = _Strategy("capability")
    router = QueryEmbeddingExecutionRuntimeRouter(
        legacy_strategy=legacy,
        capability_strategy=capability,
    )

    legacy_plan = router.preflight(_preflight(required=False))
    capability_plan = router.preflight(_preflight(required=True))
    result = router.invoke(
        QueryEmbeddingProviderRequest(
            plan=capability_plan,
            model_binding=EmbeddingModelBinding(
                model_id=uuid.uuid4(),
                provider_id=uuid.uuid4(),
                model_identifier="embed-safe",
            ),
            query="query",
        )
    )

    assert result.vector == (0.1,)
    assert len(legacy.preflight_calls) == 1
    assert len(capability.preflight_calls) == 1
    assert capability.invoke_calls[0].plan.state == "capability"
    assert legacy_plan.capability_required is False


@pytest.mark.parametrize("malformed", [None, 0, 1, "true", []])
def test_query_embedding_router_rejects_malformed_activation(malformed):
    router = QueryEmbeddingExecutionRuntimeRouter(
        legacy_strategy=_Strategy("legacy"),
        capability_strategy=_Strategy("capability"),
    )

    with pytest.raises(QueryEmbeddingConfigurationError):
        router.preflight(_preflight(required=malformed))


def test_legacy_query_embedding_closes_selection_session_before_outbound(monkeypatch):
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    binding = EmbeddingModelBinding(
        model_id=uuid.uuid4(),
        provider_id=uuid.uuid4(),
        model_identifier="embed-safe",
    )
    calls = []

    class _Session:
        closed = False

        def close(self):
            self.closed = True

    session = _Session()

    class _Client:
        def embed_sync(self, query):
            assert session.closed is True
            calls.append(query)
            return [0.1, 0.9]

    monkeypatch.setattr(
        "apps.workflow_engine.adapters.query_embedding_legacy."
        "LLMService.get_client_for_model_binding",
        lambda db, principal, model_binding, *, organization_id: (
            calls.append((db, principal, model_binding, organization_id)) or _Client()
        ),
    )
    runtime = LegacyQueryEmbeddingAdapter(session_factory=lambda: session)
    plan = runtime.preflight(
        QueryEmbeddingPreflight(
            node_id="llm-1",
            organization_id=organization_id,
            legacy_credential_user_id=user_id,
            execution_context={"provider_execution_capability_required": False},
            runtime_control=None,
        )
    )

    result = runtime.invoke(
        QueryEmbeddingProviderRequest(
            plan=plan,
            model_binding=binding,
            query="query",
        )
    )

    assert result.vector == (0.1, 0.9)
    assert calls[0] == (session, user_id, binding, organization_id)
    assert calls[1] == "query"


def test_legacy_query_embedding_rejects_tampered_plan_scope_before_db():
    organization_id = uuid.uuid4()
    session_calls = []
    runtime = LegacyQueryEmbeddingAdapter(
        session_factory=lambda: session_calls.append(object()),
    )
    plan = runtime.preflight(
        QueryEmbeddingPreflight(
            node_id="llm-1",
            organization_id=organization_id,
            legacy_credential_user_id=uuid.uuid4(),
            execution_context={"provider_execution_capability_required": False},
            runtime_control=None,
        )
    )

    with pytest.raises(QueryEmbeddingConfigurationError):
        runtime.invoke(
            QueryEmbeddingProviderRequest(
                plan=replace(plan, organization_id=uuid.uuid4()),
                model_binding=EmbeddingModelBinding(
                    model_id=uuid.uuid4(),
                    provider_id=uuid.uuid4(),
                    model_identifier="embed-safe",
                ),
                query="query",
            )
        )

    assert session_calls == []


class _Projection:
    def __init__(self, values):
        self.values = values
        self.calls = []

    def project(self, **kwargs):
        self.calls.append(kwargs)
        return self.values


class _Provider:
    def __init__(self, *, failures=None):
        self.failures = failures or set()
        self.calls = []

    def preflight(self, request):
        return QueryEmbeddingPlan(
            capability_required=True,
            organization_id=request.organization_id,
            node_id=request.node_id,
            state=object(),
        )

    def invoke(self, request):
        self.calls.append(request)
        if request.model_binding.model_id in self.failures:
            raise RuntimeError("provider payload must not escape")
        return QueryEmbeddingProviderResult(
            vector=(float(len(self.calls)),),
            input_tokens=2,
        )


def test_execution_service_groups_same_model_once_and_keeps_model_boundaries():
    organization_id = uuid.uuid4()
    kb_a, kb_b, kb_c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    binding_a = EmbeddingModelBinding(
        model_id=uuid.uuid4(),
        provider_id=uuid.uuid4(),
        model_identifier="embed-a",
    )
    binding_b = EmbeddingModelBinding(
        model_id=uuid.uuid4(),
        provider_id=uuid.uuid4(),
        model_identifier="embed-b",
    )
    projection = _Projection({kb_a: binding_a, kb_b: binding_a, kb_c: binding_b})
    provider = _Provider()
    service = QueryEmbeddingExecutionService(
        model_projection=projection,
        provider_runtime=provider,
    )

    result = service.execute(
        QueryEmbeddingExecutionRequest(
            plan=QueryEmbeddingPlan(
                capability_required=True,
                organization_id=organization_id,
                node_id="llm-1",
                state=object(),
            ),
            organization_id=organization_id,
            node_id="llm-1",
            knowledge_base_ids=(str(kb_a), str(kb_b), str(kb_c)),
            failure_policy="safe_no_result",
            query="query",
        )
    )

    assert len(provider.calls) == 2
    assert result.vectors_by_knowledge_base[str(kb_a)] == (1.0,)
    assert result.vectors_by_knowledge_base[str(kb_b)] == (1.0,)
    assert result.vectors_by_knowledge_base[str(kb_c)] == (2.0,)
    assert result.failed_count == 0


def test_execution_service_checks_deadline_before_each_provider_invocation():
    organization_id = uuid.uuid4()
    kb_a, kb_b = uuid.uuid4(), uuid.uuid4()
    binding_a = EmbeddingModelBinding(
        model_id=uuid.uuid4(),
        provider_id=uuid.uuid4(),
        model_identifier="embed-a",
    )
    binding_b = EmbeddingModelBinding(
        model_id=uuid.uuid4(),
        provider_id=uuid.uuid4(),
        model_identifier="embed-b",
    )
    projection = _Projection({kb_a: binding_a, kb_b: binding_b})
    provider = _Provider()
    service = QueryEmbeddingExecutionService(
        model_projection=projection,
        provider_runtime=provider,
    )
    guard_calls = []

    class _DeadlineExpired(RuntimeError):
        pass

    def deadline_guard():
        guard_calls.append(len(guard_calls) + 1)
        if len(guard_calls) == 2:
            raise _DeadlineExpired("conversation.request_expired")

    with pytest.raises(_DeadlineExpired):
        service.execute(
            QueryEmbeddingExecutionRequest(
                plan=QueryEmbeddingPlan(
                    capability_required=True,
                    organization_id=organization_id,
                    node_id="llm-1",
                    state=object(),
                ),
                organization_id=organization_id,
                node_id="llm-1",
                knowledge_base_ids=(str(kb_a), str(kb_b)),
                failure_policy="safe_no_result",
                query="query",
                deadline_guard=deadline_guard,
            )
        )

    assert guard_calls == [1, 2]
    assert len(provider.calls) == 1
    assert provider.calls[0].model_binding == binding_a


def test_execution_service_zero_candidates_skips_projection_and_provider():
    organization_id = uuid.uuid4()
    projection = _Projection({})
    provider = _Provider()
    service = QueryEmbeddingExecutionService(
        model_projection=projection,
        provider_runtime=provider,
    )

    result = service.execute(
        QueryEmbeddingExecutionRequest(
            plan=QueryEmbeddingPlan(
                capability_required=True,
                organization_id=organization_id,
                node_id="llm-1",
            ),
            organization_id=organization_id,
            node_id="llm-1",
            knowledge_base_ids=(),
            failure_policy="safe_no_result",
            query="query",
        )
    )

    assert result.failed_count == 0
    assert projection.calls == []
    assert provider.calls == []


@pytest.mark.parametrize("mismatch", ["organization", "node"])
def test_execution_service_rejects_plan_scope_mismatch_before_projection(mismatch):
    plan_organization_id = uuid.uuid4()
    request_organization_id = (
        uuid.uuid4() if mismatch == "organization" else plan_organization_id
    )
    plan_node_id = "llm-1"
    request_node_id = "llm-2" if mismatch == "node" else plan_node_id
    projection = _Projection({})
    provider = _Provider()
    service = QueryEmbeddingExecutionService(
        model_projection=projection,
        provider_runtime=provider,
    )

    with pytest.raises(QueryEmbeddingConfigurationError):
        service.execute(
            QueryEmbeddingExecutionRequest(
                plan=QueryEmbeddingPlan(
                    capability_required=True,
                    organization_id=plan_organization_id,
                    node_id=plan_node_id,
                ),
                organization_id=request_organization_id,
                node_id=request_node_id,
                knowledge_base_ids=(str(uuid.uuid4()),),
                failure_policy="safe_no_result",
                query="query",
            )
        )

    assert projection.calls == []
    assert provider.calls == []


def test_execution_service_safe_partial_and_fail_node_are_explicit():
    organization_id = uuid.uuid4()
    kb_a, kb_b = uuid.uuid4(), uuid.uuid4()
    binding_a = EmbeddingModelBinding(
        model_id=uuid.uuid4(),
        provider_id=uuid.uuid4(),
        model_identifier="embed-a",
    )
    binding_b = EmbeddingModelBinding(
        model_id=uuid.uuid4(),
        provider_id=uuid.uuid4(),
        model_identifier="embed-b",
    )
    projection = _Projection({kb_a: binding_a, kb_b: binding_b})
    provider = _Provider(failures={binding_b.model_id})
    service = QueryEmbeddingExecutionService(
        model_projection=projection,
        provider_runtime=provider,
    )
    plan = QueryEmbeddingPlan(
        capability_required=True,
        organization_id=organization_id,
        node_id="llm-1",
    )

    safe_result = service.execute(
        QueryEmbeddingExecutionRequest(
            plan=plan,
            organization_id=organization_id,
            node_id="llm-1",
            knowledge_base_ids=(str(kb_a), str(kb_b)),
            failure_policy="safe_no_result",
            query="query",
        )
    )
    assert set(safe_result.vectors_by_knowledge_base) == {str(kb_a)}
    assert safe_result.failed_count == 1

    with pytest.raises(QueryEmbeddingConfigurationError) as exc_info:
        service.execute(
            QueryEmbeddingExecutionRequest(
                plan=plan,
                organization_id=organization_id,
                node_id="llm-1",
                knowledge_base_ids=(str(kb_b),),
                failure_policy="fail_node",
                query="query-sentinel",
            )
        )
    assert exc_info.value.__cause__ is None


@pytest.mark.parametrize("invalid_value", [float("nan"), float("inf"), -float("inf")])
def test_query_embedding_result_rejects_non_finite_vector_values(invalid_value):
    with pytest.raises(QueryEmbeddingConfigurationError):
        QueryEmbeddingProviderResult(vector=(0.1, invalid_value), input_tokens=1)


def test_query_embedding_result_repr_redacts_raw_vector():
    result = QueryEmbeddingProviderResult(
        vector=(0.123456789, 0.987654321),
        input_tokens=2,
    )

    assert "0.123456789" not in repr(result)
    assert "0.987654321" not in repr(result)


def test_query_embedding_execution_objects_redact_query_kb_ids_and_vectors():
    knowledge_base_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    vector_value = 0.123456789
    request = QueryEmbeddingExecutionRequest(
        plan=QueryEmbeddingPlan(
            capability_required=True,
            organization_id=organization_id,
            node_id="llm-1",
        ),
        organization_id=organization_id,
        node_id="llm-1",
        knowledge_base_ids=(str(knowledge_base_id),),
        failure_policy="safe_no_result",
        query="query-sentinel",
    )
    result = QueryEmbeddingExecutionResult(
        vectors_by_knowledge_base={str(knowledge_base_id): (vector_value,)},
        bindings_by_knowledge_base={},
        failed_count=1,
    )

    request_repr = repr(request)
    result_repr = repr(result)
    assert "query-sentinel" not in request_repr
    assert str(knowledge_base_id) not in request_repr
    assert str(knowledge_base_id) not in result_repr
    assert str(vector_value) not in result_repr
    assert "failed_count=1" not in result_repr
