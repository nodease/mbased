from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from apps.shared.domain.embedding_model_binding import EmbeddingModelBinding
from apps.shared.domain.provider_execution_capability import (
    CapabilityPurpose,
    PrincipalKind,
    ProviderExecutionCapability,
    RuntimePrincipal,
)
from apps.shared.domain.workflow_execution_identity import InvocationSegment
from apps.shared.services.llm_client import (
    EmbeddingProviderResult,
    PreparedEmbeddingInvocation,
    ProviderEndpointUnsupportedError,
    ProviderFailurePhase,
    ProviderInvocationError,
)
from apps.workflow_engine.adapters.query_embedding_capability import (
    CapabilityQueryEmbeddingAdapter,
)
from apps.workflow_engine.application.query_embedding_execution import (
    QueryEmbeddingConfigurationError,
    QueryEmbeddingPreflight,
    QueryEmbeddingProviderRequest,
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


class _Attempt:
    durable = True

    def __init__(self, *, session: _Session, start_error: Exception | None = None):
        self._session = session
        self._start_error = start_error
        self.started = 0
        self.successes = []
        self.definitive = []
        self.unknown = []

    def mark_provider_started(self) -> None:
        assert self._session.commits == 1
        assert self._session.closes == 1
        if self._start_error is not None:
            raise self._start_error
        self.started += 1

    def record_success(self, *, usage, latency_ms):
        self.successes.append((dict(usage), latency_ms))
        return 0.0

    def record_definitive_failure(self, *, reason_code):
        self.definitive.append(reason_code)

    def mark_outcome_unknown(self, *, reason_code):
        self.unknown.append(reason_code)


class _Recorder:
    def __init__(self, *, attempt: _Attempt) -> None:
        self.attempt = attempt
        self.requests = []

    def begin(self, request):
        self.requests.append(request)
        return self.attempt


class _Client:
    def __init__(self, *, outcome: object, prepare_error: Exception | None = None) -> None:
        self.outcome = outcome
        self.prepare_error = prepare_error
        self.prepare_calls = []
        self.provider_calls = 0

    def prepare_embedding_invocation(self, query):
        self.prepare_calls.append(query)
        if self.prepare_error is not None:
            raise self.prepare_error

        def invoke():
            self.provider_calls += 1
            if isinstance(self.outcome, Exception):
                raise self.outcome
            return self.outcome

        return PreparedEmbeddingInvocation(
            canonical_request_bytes=len(query.encode("utf-8")) + 32,
            requested_input_tokens=len(query.encode("utf-8")) + 32,
            invoke=invoke,
        )


def _control(*, organization_id: uuid.UUID, workflow_id: uuid.UUID):
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
        binding_container_path=(("loop", "loop-a"),),
        external_effect_enforced=True,
    )


def _context(
    *,
    organization_id: uuid.UUID,
    workflow_id: uuid.UUID,
    audience: str | None = None,
) -> dict:
    context = {
        "provider_execution_capability_required": True,
        "query_embedding_capability_limits": {
            "query_byte_cap": 2_048,
            "input_token_cap": 2_048,
            "cost_cap_microusd": 10_000,
        },
        "deployment_id": str(uuid.uuid4()),
        "workflow_version": 2,
        "organization_id": str(organization_id),
        "workflow_id": str(workflow_id),
        "workflow_run_id": str(uuid.uuid4()),
        "execution_subject": {"type": "user", "id": str(uuid.uuid4())},
    }
    if audience is not None:
        context.pop("execution_subject")
        context["provider_execution_audience"] = audience
    return context


def _runtime(
    *,
    outcome: object | None = None,
    start_error: Exception | None = None,
    prepare_error: Exception | None = None,
):
    session = _Session()
    attempt = _Attempt(session=session, start_error=start_error)
    recorder = _Recorder(attempt=attempt)
    provider_id = uuid.uuid4()
    credential_id = uuid.uuid4()
    principal_id = uuid.uuid4()
    captured = {"admissions": []}
    if outcome is None:
        outcome = EmbeddingProviderResult(vector=(0.25, 0.75), input_tokens=3)
    client = _Client(outcome=outcome, prepare_error=prepare_error)

    def make_capability(command):
        now = datetime.now(timezone.utc)
        return ProviderExecutionCapability.issue(
            capability_id=uuid.uuid4(),
            revision=1,
            binding=command.binding,
            policy_id=uuid.uuid4(),
            policy_revision=2,
            credential_id=credential_id,
            model_id=command.policy_model_id,
            provider_id=provider_id,
            credential_principal=RuntimePrincipal.user(principal_id),
            permission_revision="a" * 64,
            relation_revision="b" * 64,
            egress_revision="c" * 64,
            pricing_revision="d" * 64,
            input_token_cap=command.input_token_cap,
            output_token_cap=0,
            cost_cap_microusd=command.cost_cap_microusd,
            expires_at=now + timedelta(minutes=5),
            now=now,
        )

    class _CapabilityService:
        @staticmethod
        def issue_capability(_db, *, command):
            captured["issue"] = command
            if "capability" not in captured:
                captured["capability"] = make_capability(command)
            return captured["capability"]

        @staticmethod
        def admit_capability(_db, *, command):
            captured["admissions"].append(command)
            capability = captured["capability"]
            return SimpleNamespace(
                capability=capability,
                credential=SimpleNamespace(id=credential_id),
                provider=SimpleNamespace(
                    id=provider_id,
                    name="openai",
                    base_url="https://provider.example.test/v1",
                ),
                model=SimpleNamespace(
                    id=command.policy_model_id,
                    provider_id=provider_id,
                    model_id_for_api_call="embed-safe",
                    type="embedding",
                    input_price_1k=Decimal("0.001"),
                    output_price_1k=Decimal("0"),
                ),
            )

    runtime = CapabilityQueryEmbeddingAdapter(
        session_factory=lambda: session,
        usage_recorder=recorder,
        capability_service=_CapabilityService,
        credential_loader=lambda _credential: {"apiKey": "redacted-test-key"},
        client_factory=lambda **_kwargs: client,
    )
    return runtime, session, recorder, attempt, client, captured, provider_id


def _plan(runtime, *, organization_id, workflow_id, audience=None):
    return runtime.preflight(
        QueryEmbeddingPreflight(
            node_id="llm-1",
            organization_id=organization_id,
            legacy_credential_user_id=None,
            execution_context=_context(
                organization_id=organization_id,
                workflow_id=workflow_id,
                audience=audience,
            ),
            runtime_control=_control(
                organization_id=organization_id,
                workflow_id=workflow_id,
            ),
        )
    )


def test_query_embedding_commits_control_and_usage_fence_before_provider():
    organization_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    runtime, session, recorder, attempt, client, captured, provider_id = _runtime()
    plan = _plan(runtime, organization_id=organization_id, workflow_id=workflow_id)
    model_id = uuid.uuid4()

    result = runtime.invoke(
        QueryEmbeddingProviderRequest(
            plan=plan,
            model_binding=EmbeddingModelBinding(
                model_id=model_id,
                provider_id=provider_id,
                model_identifier="embed-safe",
            ),
            query="bounded query",
        )
    )

    assert result.vector == (0.25, 0.75)
    assert session.commits == 1
    assert session.closes == 1
    assert client.provider_calls == 1
    assert attempt.started == 1
    assert attempt.successes[0][0] == {
        "prompt_tokens": 3,
        "completion_tokens": 0,
        "total_tokens": 3,
    }
    assert captured["issue"].binding.purpose is CapabilityPurpose.QUERY_EMBEDDING
    assert captured["issue"].binding.container_path == (("loop", "loop-a"),)
    assert captured["issue"].policy_model_id == model_id
    assert [item.requested_input_tokens for item in captured["admissions"]] == [
        0,
        len("bounded query".encode("utf-8")) + 32,
    ]
    assert recorder.requests[0].attribution.usage_context is not None


def test_query_embedding_rejects_tampered_plan_scope_before_admission():
    organization_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    runtime, session, recorder, _attempt, client, captured, provider_id = _runtime()
    plan = _plan(runtime, organization_id=organization_id, workflow_id=workflow_id)

    with pytest.raises(QueryEmbeddingConfigurationError):
        runtime.invoke(
            QueryEmbeddingProviderRequest(
                plan=replace(plan, node_id="llm-2"),
                model_binding=EmbeddingModelBinding(
                    model_id=uuid.uuid4(),
                    provider_id=provider_id,
                    model_identifier="embed-safe",
                ),
                query="bounded query",
            )
        )

    assert captured["admissions"] == []
    assert recorder.requests == []
    assert client.prepare_calls == []
    assert client.provider_calls == 0
    assert session.commits == 0
    assert session.closes == 0


def test_provider_start_failure_prevents_embedding_outbound():
    organization_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    runtime, _session, _recorder, _attempt, client, _captured, provider_id = _runtime(
        start_error=RuntimeError("ledger unavailable")
    )
    plan = _plan(runtime, organization_id=organization_id, workflow_id=workflow_id)

    with pytest.raises(QueryEmbeddingConfigurationError):
        runtime.invoke(
            QueryEmbeddingProviderRequest(
                plan=plan,
                model_binding=EmbeddingModelBinding(
                    model_id=uuid.uuid4(),
                    provider_id=provider_id,
                    model_identifier="embed-safe",
                ),
                query="query",
            )
        )

    assert client.provider_calls == 0


def test_unsupported_embedding_provider_fails_before_usage_intent_or_outbound():
    organization_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    runtime, session, recorder, _attempt, client, _captured, provider_id = _runtime(
        prepare_error=ProviderEndpointUnsupportedError(
            "safe",
            reason_code="provider_endpoint_unsupported",
            failure_phase=ProviderFailurePhase.BEFORE_SEND,
        )
    )
    plan = _plan(runtime, organization_id=organization_id, workflow_id=workflow_id)

    with pytest.raises(QueryEmbeddingConfigurationError):
        runtime.invoke(
            QueryEmbeddingProviderRequest(
                plan=plan,
                model_binding=EmbeddingModelBinding(
                    model_id=uuid.uuid4(),
                    provider_id=provider_id,
                    model_identifier="embed-safe",
                ),
                query="query",
            )
        )

    assert recorder.requests == []
    assert client.provider_calls == 0
    assert session.commits == 0
    assert session.closes == 1


@pytest.mark.parametrize(
    ("audience", "subject_kind", "audit_kind"),
    [
        (
            "anonymous_public",
            PrincipalKind.ANONYMOUS_PUBLIC,
            PrincipalKind.PUBLIC,
        ),
        ("system", PrincipalKind.SYSTEM, PrincipalKind.SYSTEM),
    ],
)
def test_non_user_execution_preserves_subject_and_audit_principal(
    audience,
    subject_kind,
    audit_kind,
):
    organization_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    runtime, _session, _recorder, _attempt, _client, captured, provider_id = (
        _runtime()
    )
    plan = _plan(
        runtime,
        organization_id=organization_id,
        workflow_id=workflow_id,
        audience=audience,
    )

    runtime.invoke(
        QueryEmbeddingProviderRequest(
            plan=plan,
            model_binding=EmbeddingModelBinding(
                model_id=uuid.uuid4(),
                provider_id=provider_id,
                model_identifier="embed-safe",
            ),
            query="query",
        )
    )

    assert captured["issue"].execution_subject.kind is subject_kind
    assert captured["issue"].execution_subject.reference_id is None
    assert captured["issue"].audit_actor.kind is audit_kind
    assert captured["issue"].audit_actor.reference_id is None


@pytest.mark.parametrize(
    ("error", "definitive", "unknown"),
    [
        (
            ProviderInvocationError(
                "safe",
                reason_code="provider_connection_failed",
                failure_phase=ProviderFailurePhase.BEFORE_SEND,
            ),
            ["provider_not_sent"],
            [],
        ),
        (
            ProviderInvocationError(
                "safe",
                reason_code="provider_http_error",
                status_code=401,
                failure_phase=ProviderFailurePhase.RESPONSE_RECEIVED,
            ),
            ["provider_rejected"],
            [],
        ),
        (
            ProviderInvocationError(
                "safe",
                reason_code="provider_timeout",
                failure_phase=ProviderFailurePhase.OUTCOME_UNKNOWN,
            ),
            [],
            ["provider_timeout"],
        ),
    ],
)
def test_provider_failure_is_terminalized_without_raw_error(
    error,
    definitive,
    unknown,
):
    organization_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    runtime, _session, _recorder, attempt, _client, _captured, provider_id = _runtime(
        outcome=error
    )
    plan = _plan(runtime, organization_id=organization_id, workflow_id=workflow_id)

    with pytest.raises(QueryEmbeddingConfigurationError) as exc_info:
        runtime.invoke(
            QueryEmbeddingProviderRequest(
                plan=plan,
                model_binding=EmbeddingModelBinding(
                    model_id=uuid.uuid4(),
                    provider_id=provider_id,
                    model_identifier="embed-safe",
                ),
                query="query-sentinel",
            )
        )

    assert exc_info.value.__cause__ is None
    assert attempt.definitive == definitive
    assert attempt.unknown == unknown
    assert "query-sentinel" not in repr(exc_info.value)


def test_same_model_cannot_be_invoked_twice_in_one_plan():
    organization_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    runtime, _session, _recorder, _attempt, client, _captured, provider_id = _runtime()
    plan = _plan(runtime, organization_id=organization_id, workflow_id=workflow_id)
    binding = EmbeddingModelBinding(
        model_id=uuid.uuid4(),
        provider_id=provider_id,
        model_identifier="embed-safe",
    )
    request = QueryEmbeddingProviderRequest(plan=plan, model_binding=binding, query="q")

    runtime.invoke(request)
    with pytest.raises(QueryEmbeddingConfigurationError):
        runtime.invoke(request)

    assert client.provider_calls == 1
