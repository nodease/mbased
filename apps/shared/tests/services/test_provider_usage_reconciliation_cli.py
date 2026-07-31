from __future__ import annotations

import json
import uuid
from pathlib import Path
from types import SimpleNamespace

from apps.shared.domain.provider_usage_ledger import ProviderUsageState
from scripts import reconcile_provider_usage


class _Session:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_success_reconciliation_cli_uses_exact_scope_version_and_measurement(
    monkeypatch,
    capsys,
) -> None:
    organization_id = uuid.uuid4()
    operation_id = uuid.uuid4()
    session = _Session()
    calls: list[dict[str, object]] = []

    class _Service:
        def reconcile_success(self, _db, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                id=operation_id,
                state=ProviderUsageState.SUCCEEDED,
                state_version=4,
            )

    monkeypatch.setattr(
        reconcile_provider_usage,
        "_arguments",
        lambda: SimpleNamespace(
            organization_id=organization_id,
            operation_id=operation_id,
            expected_state_version=3,
            resolution="succeeded",
            prompt_tokens=10,
            completion_tokens=5,
            total_cost_microusd=2_000,
            latency_ms=42,
            reason_code=None,
        ),
    )
    monkeypatch.setattr(reconcile_provider_usage, "SessionLocal", lambda: session)
    monkeypatch.setattr(
        reconcile_provider_usage,
        "ProviderUsageLedgerService",
        _Service,
    )

    assert reconcile_provider_usage.main() == 0

    assert calls == [
        {
            "organization_id": organization_id,
            "operation_id": operation_id,
            "expected_state_version": 3,
            "measurement": reconcile_provider_usage.ProviderUsageMeasurement(
                prompt_tokens=10,
                completion_tokens=5,
                total_cost_microusd=2_000,
                latency_ms=42,
            ),
        }
    ]
    assert session.closed is True
    output = json.loads(capsys.readouterr().out)
    assert output == {
        "status": "complete",
        "operation_id": str(operation_id),
        "state": "succeeded",
        "state_version": 4,
    }


def test_definitive_failure_cli_never_accepts_or_replays_provider_payload(
    monkeypatch,
) -> None:
    organization_id = uuid.uuid4()
    operation_id = uuid.uuid4()
    session = _Session()
    calls: list[dict[str, object]] = []

    class _Service:
        def reconcile_definitive_failure(self, _db, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                id=operation_id,
                state=ProviderUsageState.FAILED_DEFINITIVE,
                state_version=5,
            )

    monkeypatch.setattr(
        reconcile_provider_usage,
        "_arguments",
        lambda: SimpleNamespace(
            organization_id=organization_id,
            operation_id=operation_id,
            expected_state_version=4,
            resolution="failed_definitive",
            prompt_tokens=None,
            completion_tokens=None,
            total_cost_microusd=None,
            latency_ms=None,
            reason_code="provider_not_sent",
        ),
    )
    monkeypatch.setattr(reconcile_provider_usage, "SessionLocal", lambda: session)
    monkeypatch.setattr(
        reconcile_provider_usage,
        "ProviderUsageLedgerService",
        _Service,
    )

    assert reconcile_provider_usage.main() == 0

    assert calls == [
        {
            "organization_id": organization_id,
            "operation_id": operation_id,
            "expected_state_version": 4,
            "reason_code": "provider_not_sent",
        }
    ]
    assert session.closed is True
    source = reconcile_provider_usage.__file__
    assert source is not None
    source_text = Path(source).read_text(encoding="utf-8")
    for forbidden in ("raw_request", "raw_response", "api_key", "credential"):
        assert forbidden not in source_text


def test_cli_rejects_mixed_resolution_arguments_before_opening_a_session(
    monkeypatch,
    capsys,
) -> None:
    opened = False

    def _session():
        nonlocal opened
        opened = True
        return _Session()

    monkeypatch.setattr(
        reconcile_provider_usage,
        "_arguments",
        lambda: SimpleNamespace(
            organization_id=uuid.uuid4(),
            operation_id=uuid.uuid4(),
            expected_state_version=3,
            resolution="failed_definitive",
            prompt_tokens=10,
            completion_tokens=None,
            total_cost_microusd=None,
            latency_ms=None,
            reason_code="provider_not_sent",
        ),
    )
    monkeypatch.setattr(reconcile_provider_usage, "SessionLocal", _session)

    assert reconcile_provider_usage.main() == 3
    assert opened is False
    assert json.loads(capsys.readouterr().out) == {"status": "failed"}


def test_cli_sanitizes_session_open_failure(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        reconcile_provider_usage,
        "_arguments",
        lambda: SimpleNamespace(
            organization_id=uuid.uuid4(),
            operation_id=uuid.uuid4(),
            expected_state_version=3,
            resolution="failed_definitive",
            prompt_tokens=None,
            completion_tokens=None,
            total_cost_microusd=None,
            latency_ms=None,
            reason_code="provider_not_sent",
        ),
    )

    def _session():
        raise RuntimeError("raw provider evidence must not be printed")

    monkeypatch.setattr(reconcile_provider_usage, "SessionLocal", _session)

    assert reconcile_provider_usage.main() == 3
    assert json.loads(capsys.readouterr().out) == {"status": "failed"}
