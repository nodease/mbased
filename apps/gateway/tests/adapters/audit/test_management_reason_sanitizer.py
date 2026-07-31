from types import SimpleNamespace

import pytest

from apps.gateway.adapters.audit.management_reason_sanitizer import (
    ManagementReasonSanitizer,
)
from apps.shared.services.tracing.redaction import RedactionResult


def test_management_reason_uses_fail_closed_baseline_and_redacts_pii_and_secret():
    result = ManagementReasonSanitizer().sanitize(
        "contact person@example.com; token=not-a-real-secret"
    )

    assert "person@example.com" not in result
    assert "not-a-real-secret" not in result
    assert "[REDACTED]" in result


def test_none_reason_does_not_invoke_redaction(monkeypatch):
    monkeypatch.setattr(
        "apps.gateway.adapters.audit.management_reason_sanitizer."
        "TraceRedactionService.redact_payload",
        lambda *args, **kwargs: pytest.fail("redaction must not run"),
    )

    assert ManagementReasonSanitizer().sanitize(None) is None


@pytest.mark.parametrize(
    "result",
    [
        RedactionResult(
            redacted_payload="raw",
            redaction_applied=False,
            pii_detected=False,
            secret_detected=False,
            redaction_metadata={},
            failed=True,
        ),
        SimpleNamespace(failed=False, redacted_payload={"not": "a string"}),
    ],
)
def test_failed_or_non_string_redaction_never_falls_back_to_raw(monkeypatch, result):
    monkeypatch.setattr(
        "apps.gateway.adapters.audit.management_reason_sanitizer."
        "TraceRedactionService.redact_payload",
        lambda *args, **kwargs: result,
    )

    with pytest.raises(ValueError, match="redaction failed"):
        ManagementReasonSanitizer().sanitize("raw input")
