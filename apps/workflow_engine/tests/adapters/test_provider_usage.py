from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from apps.shared.domain.provider_usage_ledger import (
    ProviderUsageLedgerError,
    ProviderUsageOperation,
    ProviderUsageState,
)
from apps.workflow_engine.adapters.provider_usage import (
    PostgresProviderUsageRecorder,
)
from apps.workflow_engine.application.provider_execution import (
    ProviderExecutionAttribution,
    ProviderExecutionBindingSnapshot,
    ProviderExecutionIdentityContext,
    ProviderExecutionPricingSnapshot,
    ProviderExecutionPrincipal,
    ProviderExecutionPrincipalKind,
    ProviderExecutionPurpose,
    ProviderExecutionUsageContext,
)
from apps.workflow_engine.application.provider_usage import (
    ProviderUsageIntent,
    ProviderUsageRecord,
    ProviderUsageRuntimeError,
)
from apps.workflow_engine.services import llm_service as workflow_llm_service
from apps.workflow_engine.services.llm_service import LLMService


class _Session:
    def __init__(self) -> None:
        self.closes = 0

    def close(self) -> None:
        self.closes += 1


def _record(*, attribution: ProviderExecutionAttribution) -> ProviderUsageRecord:
    return ProviderUsageRecord(
        attribution=attribution,
        usage={"prompt_tokens": 3, "completion_tokens": 2},
        workflow_id=uuid.uuid4(),
        workflow_run_id=uuid.uuid4(),
        node_id="llm-1",
    )


def _capability_attribution(
    *,
    organization_id: uuid.UUID,
    model_db_id: uuid.UUID,
    credential_id: uuid.UUID,
    principal_id: uuid.UUID,
) -> ProviderExecutionAttribution:
    capability_id = uuid.uuid4()
    pricing = ProviderExecutionPricingSnapshot(
        revision="d" * 64,
        input_price_per_1k=Decimal("1"),
        output_price_per_1k=Decimal("2"),
    )
    binding = ProviderExecutionBindingSnapshot(
        organization_id=organization_id,
        workflow_id=uuid.uuid4(),
        deployment_id=uuid.uuid4(),
        deployment_version=1,
        node_id="llm-1",
        node_invocation_id=uuid.uuid4(),
        execution_admission_id=uuid.uuid4(),
        provider_attempt_id=uuid.uuid4(),
        purpose=ProviderExecutionPurpose.MAIN_GENERATION,
        container_path=(("loop", "loop-a"),),
    )
    identities = ProviderExecutionIdentityContext(
        execution_subject=ProviderExecutionPrincipal(
            ProviderExecutionPrincipalKind.USER, principal_id
        ),
        credential_principal=ProviderExecutionPrincipal(
            ProviderExecutionPrincipalKind.USER, principal_id
        ),
        billing_principal=ProviderExecutionPrincipal(
            ProviderExecutionPrincipalKind.ORGANIZATION, organization_id
        ),
        audit_actor=ProviderExecutionPrincipal(
            ProviderExecutionPrincipalKind.USER, principal_id
        ),
    )
    context = ProviderExecutionUsageContext(
        binding=binding,
        capability_id=capability_id,
        capability_revision=2,
        capability_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        policy_id=uuid.uuid4(),
        policy_revision=1,
        provider_id=uuid.uuid4(),
        model_id=model_db_id,
        model_api_id="shared-provider-id",
        credential_id=credential_id,
        identities=identities,
        permission_revision="a" * 64,
        relation_revision="b" * 64,
        egress_revision="c" * 64,
        pricing_snapshot=pricing,
        input_token_cap=1_000,
        output_token_cap=100,
        cost_cap_microusd=1_000_000,
        admitted_input_tokens=100,
        admitted_output_tokens=10,
    )
    return ProviderExecutionAttribution(
        credential_id=credential_id,
        credential_principal_user_id=principal_id,
        organization_id=organization_id,
        model_id="shared-provider-id",
        model_db_id=model_db_id,
        capability_id=capability_id,
        capability_revision=2,
        pricing_snapshot=pricing,
        usage_context=context,
    )


def test_capability_usage_uses_admission_pricing_snapshot(monkeypatch):
    session = _Session()
    organization_id = uuid.uuid4()
    model_db_id = uuid.uuid4()
    credential_id = uuid.uuid4()
    principal_id = uuid.uuid4()
    captured: dict = {}

    monkeypatch.setattr(
        LLMService,
        "calculate_cost",
        lambda *_args, **_kwargs: pytest.fail(
            "capability usage must not re-read mutable model pricing"
        ),
    )
    monkeypatch.setattr(
        LLMService,
        "log_usage",
        lambda **kwargs: captured.update(usage=kwargs),
    )
    recorder = PostgresProviderUsageRecorder(session_factory=lambda: session)

    cost = recorder.record(
        _record(
            attribution=_capability_attribution(
                organization_id=organization_id,
                model_db_id=model_db_id,
                credential_id=credential_id,
                principal_id=principal_id,
            )
        )
    )

    assert cost == pytest.approx(0.007)
    assert captured["usage"]["cost"] == pytest.approx(0.007)
    assert captured["usage"]["model_db_id"] == model_db_id
    assert captured["usage"]["organization_id"] == organization_id
    assert captured["usage"]["credential_id"] == credential_id
    assert session.closes == 1


def test_durable_usage_attempt_exposes_only_safe_operation_reference():
    service = _LedgerService()
    attribution = _capability_attribution(
        organization_id=uuid.uuid4(),
        model_db_id=uuid.uuid4(),
        credential_id=uuid.uuid4(),
        principal_id=uuid.uuid4(),
    )
    recorder = PostgresProviderUsageRecorder(
        session_factory=_Session,
        ledger_service=service,
    )

    attempt = recorder.begin(_intent_for(attribution))

    assert uuid.UUID(attempt.operation_reference) == service.operation.id


def test_legacy_usage_recorder_preserves_catalog_fallback(monkeypatch):
    session = _Session()
    captured: dict = {}

    def calculate_cost(_db, _model_id, _prompt_tokens, _completion_tokens, **kwargs):
        captured.update(kwargs)
        return 0.25

    monkeypatch.setattr(LLMService, "calculate_cost", calculate_cost)
    monkeypatch.setattr(LLMService, "log_usage", lambda **_kwargs: None)
    recorder = PostgresProviderUsageRecorder(session_factory=lambda: session)

    cost = recorder.record(
        _record(
            attribution=ProviderExecutionAttribution(
                credential_id=uuid.uuid4(),
                credential_principal_user_id=uuid.uuid4(),
                organization_id=uuid.uuid4(),
                model_id="gpt-4o",
                model_db_id=uuid.uuid4(),
            )
        )
    )

    assert cost == 0.25
    assert captured["allow_catalog_fallback"] is True
    assert session.closes == 1


def test_legacy_cost_uses_catalog_when_canonical_row_has_no_prices(monkeypatch):
    model_db_id = uuid.uuid4()
    model = type(
        "Model",
        (),
        {
            "id": model_db_id,
            "input_price_1k": None,
            "output_price_1k": None,
        },
    )()

    class _Query:
        def filter(self, *_values):
            return self

        def first(self):
            return model

    class _Db:
        def query(self, *_entities):
            return _Query()

    calls: list[str] = []
    monkeypatch.setattr(
        workflow_llm_service,
        "calculate_text_token_cost",
        lambda model_id, **_kwargs: calls.append(model_id) or 0.75,
    )

    cost = LLMService.calculate_cost(
        _Db(),
        "gpt-4o",
        1_000,
        1_000,
        model_db_id=model_db_id,
        allow_catalog_fallback=True,
    )

    assert cost == 0.75
    assert calls == ["gpt-4o"]


def test_capability_cost_does_not_fallback_without_exact_prices(monkeypatch):
    model = type(
        "Model",
        (),
        {"input_price_1k": None, "output_price_1k": None},
    )()

    class _Query:
        def filter(self, *_values):
            return self

        def first(self):
            return model

    class _Db:
        def query(self, *_entities):
            return _Query()

    monkeypatch.setattr(
        workflow_llm_service,
        "calculate_text_token_cost",
        lambda *_args, **_kwargs: pytest.fail(
            "capability must not use catalog fallback"
        ),
    )

    assert (
        LLMService.calculate_cost(
            _Db(),
            "gpt-4o",
            1_000,
            1_000,
            model_db_id=uuid.uuid4(),
            allow_catalog_fallback=False,
        )
        == 0.0
    )


def test_usage_recorder_closes_session_when_projection_fails(monkeypatch):
    session = _Session()
    monkeypatch.setattr(
        LLMService,
        "calculate_cost",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("failed")),
    )
    recorder = PostgresProviderUsageRecorder(session_factory=lambda: session)

    with pytest.raises(RuntimeError, match="failed"):
        recorder.record(
            _record(
                attribution=ProviderExecutionAttribution(
                    credential_id=uuid.uuid4(),
                    credential_principal_user_id=uuid.uuid4(),
                    organization_id=uuid.uuid4(),
                    model_id="gpt-safe",
                    model_db_id=uuid.uuid4(),
                )
            )
        )

    assert session.closes == 1


class _LedgerService:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.operation: ProviderUsageOperation | None = None

    def record_intent(self, _db, *, command):
        self.calls.append("intent")
        self.operation = ProviderUsageOperation.intent(
            operation_id=uuid.uuid4(),
            snapshot=command.snapshot,
            now=datetime.now(timezone.utc),
        )
        return self.operation

    def mark_provider_started(self, _db, **_kwargs):
        self.calls.append("start")
        assert self.operation is not None
        self.operation = self.operation.mark_provider_started(
            now=datetime.now(timezone.utc)
        )
        return self.operation

    def record_success(self, _db, *, measurement, **_kwargs):
        self.calls.append("success")
        assert self.operation is not None
        self.operation = self.operation.record_success(
            measurement=measurement,
            now=datetime.now(timezone.utc),
        )
        return self.operation

    def mark_outcome_unknown(self, _db, *, reason_code, **_kwargs):
        self.calls.append(f"unknown:{reason_code}")
        assert self.operation is not None
        self.operation = self.operation.mark_outcome_unknown(
            reason_code=reason_code,
            now=datetime.now(timezone.utc),
        )
        return self.operation

    def record_definitive_failure(self, _db, *, reason_code, **_kwargs):
        self.calls.append(f"definitive:{reason_code}")
        assert self.operation is not None
        self.operation = self.operation.record_definitive_failure(
            reason_code=reason_code,
            now=datetime.now(timezone.utc),
        )
        return self.operation

    def project_compatibility_usage(self, _db, **_kwargs):
        self.calls.append("project")
        return object()


def _intent_for(attribution: ProviderExecutionAttribution) -> ProviderUsageIntent:
    assert attribution.usage_context is not None
    return ProviderUsageIntent(
        attribution=attribution,
        workflow_id=attribution.usage_context.binding.workflow_id,
        workflow_run_id=None,
        node_id=attribution.usage_context.binding.node_id,
    )


def test_capability_attempt_commits_intent_and_start_before_terminal_usage() -> None:
    service = _LedgerService()
    sessions: list[_Session] = []

    def new_session():
        session = _Session()
        sessions.append(session)
        return session

    recorder = PostgresProviderUsageRecorder(
        session_factory=new_session,
        ledger_service=service,  # type: ignore[arg-type]
    )
    attribution = _capability_attribution(
        organization_id=uuid.uuid4(),
        model_db_id=uuid.uuid4(),
        credential_id=uuid.uuid4(),
        principal_id=uuid.uuid4(),
    )

    attempt = recorder.begin(_intent_for(attribution))

    assert service.operation is not None
    assert service.operation.snapshot.binding.container_path == (
        ("loop", "loop-a"),
    )
    attempt.mark_provider_started()
    cost = attempt.record_success(
        usage={"prompt_tokens": 3, "completion_tokens": 2},
        latency_ms=10,
    )

    assert attempt.durable is True
    assert service.calls == ["intent", "start", "success", "project"]
    assert cost == pytest.approx(0.007)
    assert all(session.closes == 1 for session in sessions)


@pytest.mark.parametrize(
    "failure_phase",
    ["session_factory", "projection", "session_close"],
)
def test_projection_failure_does_not_override_committed_provider_success(
    failure_phase: str,
) -> None:
    class ProjectionLedger(_LedgerService):
        def project_compatibility_usage(self, _db, **_kwargs):
            self.calls.append("project")
            if failure_phase == "projection":
                raise RuntimeError("projection unavailable")
            return object()

    class ProjectionSession(_Session):
        def close(self) -> None:
            super().close()
            if failure_phase == "session_close":
                raise RuntimeError("projection session close failed")

    service = ProjectionLedger()
    session_count = 0

    def new_session():
        nonlocal session_count
        session_count += 1
        if session_count == 4:
            if failure_phase == "session_factory":
                raise RuntimeError("projection session unavailable")
            return ProjectionSession()
        return _Session()

    recorder = PostgresProviderUsageRecorder(
        session_factory=new_session,
        ledger_service=service,  # type: ignore[arg-type]
    )
    attribution = _capability_attribution(
        organization_id=uuid.uuid4(),
        model_db_id=uuid.uuid4(),
        credential_id=uuid.uuid4(),
        principal_id=uuid.uuid4(),
    )
    attempt = recorder.begin(_intent_for(attribution))
    attempt.mark_provider_started()

    cost = attempt.record_success(
        usage={"prompt_tokens": 3, "completion_tokens": 2},
        latency_ms=10,
    )

    assert cost == pytest.approx(0.007)
    assert service.calls[:3] == ["intent", "start", "success"]


def test_capability_attempt_records_definitive_pre_send_failure() -> None:
    service = _LedgerService()
    recorder = PostgresProviderUsageRecorder(
        session_factory=_Session,
        ledger_service=service,  # type: ignore[arg-type]
    )
    attribution = _capability_attribution(
        organization_id=uuid.uuid4(),
        model_db_id=uuid.uuid4(),
        credential_id=uuid.uuid4(),
        principal_id=uuid.uuid4(),
    )
    attempt = recorder.begin(_intent_for(attribution))
    attempt.mark_provider_started()

    attempt.record_definitive_failure(reason_code="provider_not_sent")

    assert service.calls == ["intent", "start", "definitive:provider_not_sent"]


def test_terminal_commit_failure_is_classified_unknown_without_projection() -> None:
    sessions: list[_Session] = []

    class FailingLedger(_LedgerService):
        def record_success(self, _db, **_kwargs):
            self.calls.append("success")
            raise ProviderUsageLedgerError("provider_usage.terminal_commit_failed")

        def mark_outcome_unknown(self, _db, **kwargs):
            # The failed terminal transaction may still hold the operation row
            # lock.  A follow-up classification must not open against it until
            # that session has been closed (and therefore rolled back).
            assert sessions[2].closes == 1
            return super().mark_outcome_unknown(_db, **kwargs)

    def new_session() -> _Session:
        session = _Session()
        sessions.append(session)
        return session

    service = FailingLedger()
    recorder = PostgresProviderUsageRecorder(
        session_factory=new_session,
        ledger_service=service,  # type: ignore[arg-type]
    )
    attribution = _capability_attribution(
        organization_id=uuid.uuid4(),
        model_db_id=uuid.uuid4(),
        credential_id=uuid.uuid4(),
        principal_id=uuid.uuid4(),
    )
    attempt = recorder.begin(_intent_for(attribution))
    attempt.mark_provider_started()

    with pytest.raises(ProviderUsageRuntimeError) as exc_info:
        attempt.record_success(
            usage={"prompt_tokens": 3, "completion_tokens": 2},
            latency_ms=10,
        )

    assert exc_info.value.code == "provider_usage.outcome_unknown"
    assert service.calls == [
        "intent",
        "start",
        "success",
        "unknown:terminal_record_failed",
    ]


@pytest.mark.parametrize(
    "usage",
    [
        {"prompt_tokens": "3", "completion_tokens": 2},
        {"completion_tokens": 2},
        {"prompt_tokens": 3},
        {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 6},
    ],
)
def test_malformed_provider_usage_is_durable_unknown_without_projection(
    usage: dict[str, object],
) -> None:
    service = _LedgerService()
    recorder = PostgresProviderUsageRecorder(
        session_factory=_Session,
        ledger_service=service,  # type: ignore[arg-type]
    )
    attribution = _capability_attribution(
        organization_id=uuid.uuid4(),
        model_db_id=uuid.uuid4(),
        credential_id=uuid.uuid4(),
        principal_id=uuid.uuid4(),
    )
    attempt = recorder.begin(_intent_for(attribution))
    attempt.mark_provider_started()

    with pytest.raises(ProviderUsageRuntimeError) as exc_info:
        attempt.record_success(
            usage=usage,
            latency_ms=10,
        )

    assert exc_info.value.code == "provider_usage.outcome_unknown"
    assert service.calls == [
        "intent",
        "start",
        "unknown:provider_usage_invalid",
    ]


def test_provider_usage_above_admitted_tokens_is_durable_unknown() -> None:
    service = _LedgerService()
    recorder = PostgresProviderUsageRecorder(
        session_factory=_Session,
        ledger_service=service,  # type: ignore[arg-type]
    )
    attribution = _capability_attribution(
        organization_id=uuid.uuid4(),
        model_db_id=uuid.uuid4(),
        credential_id=uuid.uuid4(),
        principal_id=uuid.uuid4(),
    )
    attempt = recorder.begin(_intent_for(attribution))
    attempt.mark_provider_started()

    with pytest.raises(ProviderUsageRuntimeError) as exc_info:
        attempt.record_success(
            usage={"prompt_tokens": 3, "completion_tokens": 11},
            latency_ms=10,
        )

    assert exc_info.value.code == "provider_usage.outcome_unknown"
    assert service.calls == [
        "intent",
        "start",
        "unknown:provider_usage_invalid",
    ]


def test_existing_started_operation_blocks_provider_replay() -> None:
    class ReplayLedger(_LedgerService):
        def record_intent(self, _db, *, command):
            operation = super().record_intent(_db, command=command)
            self.operation = operation.mark_provider_started(
                now=datetime.now(timezone.utc)
            )
            return self.operation

    service = ReplayLedger()
    recorder = PostgresProviderUsageRecorder(
        session_factory=_Session,
        ledger_service=service,  # type: ignore[arg-type]
    )
    attribution = _capability_attribution(
        organization_id=uuid.uuid4(),
        model_db_id=uuid.uuid4(),
        credential_id=uuid.uuid4(),
        principal_id=uuid.uuid4(),
    )

    with pytest.raises(ProviderUsageRuntimeError) as exc_info:
        recorder.begin(_intent_for(attribution))

    assert exc_info.value.code == "provider_usage.replay_blocked"


def test_checkpoint_recovery_classifies_started_usage_without_provider_replay() -> None:
    organization_id = uuid.uuid4()
    provider_attempt_id = uuid.uuid4()
    operation_id = uuid.uuid4()
    record = type(
        "Operation",
        (),
        {
            "id": operation_id,
            "organization_id": organization_id,
            "provider_attempt_id": provider_attempt_id,
            "purpose": "main_generation",
            "state": ProviderUsageState.PROVIDER_STARTED.value,
            "state_version": 2,
        },
    )()

    class _Query:
        def filter(self, *_criteria):
            return self

        def one_or_none(self):
            return record

    class _RecoverySession(_Session):
        def query(self, _model):
            return _Query()

    class _RecoveryLedger:
        def __init__(self):
            self.calls = []

        def mark_outcome_unknown(self, _db, **kwargs):
            self.calls.append(kwargs)

    ledger = _RecoveryLedger()
    recorder = PostgresProviderUsageRecorder(
        session_factory=_RecoverySession,
        ledger_service=ledger,  # type: ignore[arg-type]
    )

    recorder.resume_checkpoint(
        organization_id=organization_id,
        provider_attempt_id=provider_attempt_id,
        operation_reference=str(operation_id),
    )

    assert ledger.calls == [
        {
            "operation_id": operation_id,
            "expected_state_version": 2,
            "reason_code": "terminal_record_failed",
        }
    ]


def test_reference_reconciliation_preserves_unsent_intent_fact() -> None:
    organization_id = uuid.uuid4()
    provider_attempt_id = uuid.uuid4()
    operation_id = uuid.uuid4()
    record = type(
        "Operation",
        (),
        {
            "id": operation_id,
            "organization_id": organization_id,
            "provider_attempt_id": provider_attempt_id,
            "purpose": ProviderExecutionPurpose.MAIN_GENERATION.value,
            "state": ProviderUsageState.INTENT.value,
            "state_version": 1,
        },
    )()

    class _Query:
        def filter(self, *_criteria):
            return self

        def one_or_none(self):
            return record

    class _RecoverySession(_Session):
        def query(self, _model):
            return _Query()

    class _RecoveryLedger:
        def mark_outcome_unknown(self, *_args, **_kwargs):
            pytest.fail("INTENT must remain the canonical pre-send fact")

    recorder = PostgresProviderUsageRecorder(
        session_factory=_RecoverySession,
        ledger_service=_RecoveryLedger(),  # type: ignore[arg-type]
    )

    outcome = recorder.reconcile_reference_terminal(
        organization_id=organization_id,
        provider_attempt_id=provider_attempt_id,
        operation_reference=str(operation_id),
    )

    assert outcome == "not_started"


def test_reference_reconciliation_classifies_started_usage_with_allowed_reason() -> None:
    organization_id = uuid.uuid4()
    provider_attempt_id = uuid.uuid4()
    operation_id = uuid.uuid4()
    record = type(
        "Operation",
        (),
        {
            "id": operation_id,
            "organization_id": organization_id,
            "provider_attempt_id": provider_attempt_id,
            "purpose": ProviderExecutionPurpose.MAIN_GENERATION.value,
            "state": ProviderUsageState.PROVIDER_STARTED.value,
            "state_version": 2,
        },
    )()

    class _Query:
        def filter(self, *_criteria):
            return self

        def one_or_none(self):
            return record

    class _RecoverySession(_Session):
        def query(self, _model):
            return _Query()

    class _RecoveryLedger:
        def __init__(self) -> None:
            self.calls = []

        def mark_outcome_unknown(self, _db, **kwargs):
            self.calls.append(kwargs)

    ledger = _RecoveryLedger()
    recorder = PostgresProviderUsageRecorder(
        session_factory=_RecoverySession,
        ledger_service=ledger,  # type: ignore[arg-type]
    )

    outcome = recorder.reconcile_reference_terminal(
        organization_id=organization_id,
        provider_attempt_id=provider_attempt_id,
        operation_reference=str(operation_id),
    )

    assert outcome == "outcome_unknown"
    assert ledger.calls == [
        {
            "operation_id": operation_id,
            "expected_state_version": 2,
            "reason_code": "stale_provider_started",
        }
    ]
