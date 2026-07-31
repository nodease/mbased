import logging
import uuid
from types import SimpleNamespace

import pytest
from apps.shared.services.tracing.observability import TraceObservabilityService
from apps.shared.services.tracing.payload import (
    TracePayloadDecryptionError,
    TracePayloadService,
)
from apps.shared.services.tracing.policy import (
    ResolvedRedactionPolicy,
    ResolvedRetentionPolicy,
)


def test_payload_records_store_redacted_copy_without_raw_by_default():
    records = TracePayloadService.prepare_payload_records(
        [{"payload_kind": "input", "payload": {"email": "person@example.com"}}],
        redaction_policy=ResolvedRedactionPolicy(),
        retention_policy=ResolvedRetentionPolicy(),
        default_scope="trace",
    )

    assert len(records) == 1
    assert records[0]["redacted_payload"]["email"] == "[REDACTED]"
    assert records[0]["raw_payload_encrypted"] is None
    assert "raw_payload_hash" not in records[0]
    assert records[0]["storage_mode"] == "redacted_only"


def test_payload_records_are_append_only_envelopes_with_distinct_ids():
    records = TracePayloadService.prepare_payload_records(
        [
            {"payload_kind": "output", "payload": {"value": "first"}},
            {"payload_kind": "output", "payload": {"value": "second"}},
        ],
        redaction_policy=ResolvedRedactionPolicy(),
        retention_policy=ResolvedRetentionPolicy(),
        default_scope="span",
    )

    assert len(records) == 2
    assert records[0]["id"] != records[1]["id"]
    assert records[0]["sequence"] == 1
    assert records[1]["sequence"] == 2


def test_prompt_completion_disabled_stores_metadata_only():
    records = TracePayloadService.prepare_payload_records(
        [{"payload_kind": "prompt", "payload": {"messages": ["hello"]}}],
        redaction_policy=ResolvedRedactionPolicy(
            prompt_completion_storage_enabled=False
        ),
        retention_policy=ResolvedRetentionPolicy(),
        default_scope="span",
    )

    assert records[0]["redacted_payload"] is None
    assert records[0]["raw_payload_encrypted"] is None
    assert records[0]["storage_mode"] == "metadata_only"


def test_empty_payload_summary_reports_metadata_only():
    summary = TracePayloadService.summarize_payload_records([])

    assert summary["payload_storage_mode"] == "metadata_only"
    assert summary["redaction_applied"] is False


def test_secret_payload_never_gets_raw_storage_even_when_policy_allows_raw():
    records = TracePayloadService.prepare_payload_records(
        [{"payload_kind": "http_request", "payload": {"Authorization": "Bearer abc"}}],
        redaction_policy=ResolvedRedactionPolicy(
            raw_payload_storage_enabled=True,
            store_redacted_copy_only=False,
        ),
        retention_policy=ResolvedRetentionPolicy(),
        default_scope="span",
    )

    assert records[0]["secret_detected"] is True
    assert records[0]["raw_payload_encrypted"] is None
    assert records[0]["redacted_payload"]["Authorization"] == "[REDACTED]"


def test_raw_payload_decryption_failure_logs_and_increments_metric(
    monkeypatch, caplog
):
    from apps.shared.utils.encryption import encryption_manager

    def fail_decrypt(encrypted_text):
        raise ValueError("복호화 실패")

    payload = SimpleNamespace(
        id=uuid.uuid4(),
        workflow_run_id=uuid.uuid4(),
        workflow_node_run_id=uuid.uuid4(),
        payload_kind="prompt",
        scope="span",
        storage_mode="raw_and_redacted",
        raw_payload_encrypted="encrypted-value",
    )
    monkeypatch.setattr(encryption_manager, "decrypt", fail_decrypt)
    TraceObservabilityService.reset_local_counters()
    caplog.set_level(
        logging.ERROR, logger="apps.shared.services.tracing.observability"
    )

    with pytest.raises(TracePayloadDecryptionError):
        TracePayloadService.apply_view(payload, "raw")

    assert (
        TraceObservabilityService.get_local_counter(
            "raw_payload_decryption_failed", "prompt", "span"
        )
        == 1
    )
    assert "tracing.raw_payload_decryption_failed" in caplog.text
    assert "encrypted-value" not in caplog.text
    assert "복호화 실패" not in caplog.text


def test_raw_payload_encryption_failure_logs_and_keeps_redacted_only(
    monkeypatch, caplog
):
    from apps.shared.utils.encryption import encryption_manager

    def fail_encrypt(plain_text):
        raise ValueError("암호화 실패")

    monkeypatch.setenv("ENCRYPTION_KEY", "exists-but-invalid-for-test")
    monkeypatch.setattr(encryption_manager, "encrypt", fail_encrypt)
    TraceObservabilityService.reset_local_counters()
    caplog.set_level(
        logging.ERROR, logger="apps.shared.services.tracing.observability"
    )

    records = TracePayloadService.prepare_payload_records(
        [{"payload_kind": "output", "payload": {"value": "secret raw value"}}],
        redaction_policy=ResolvedRedactionPolicy(
            raw_payload_storage_enabled=True,
            store_redacted_copy_only=False,
        ),
        retention_policy=ResolvedRetentionPolicy(),
        default_scope="span",
        default_node_run_id=uuid.uuid4(),
    )

    assert records[0]["raw_payload_encrypted"] is None
    assert records[0]["storage_mode"] == "redacted_only"
    assert (
        TraceObservabilityService.get_local_counter(
            "raw_payload_encryption_failed", "output", "span"
        )
        == 1
    )
    assert "tracing.raw_payload_encryption_failed" in caplog.text
    assert "secret raw value" not in caplog.text
    assert "암호화 실패" not in caplog.text
