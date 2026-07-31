import logging

import pytest
from apps.shared.services.schedule_dispatch_observability import (
    emit_schedule_dispatch_signal,
)


def test_schedule_dispatch_signal_uses_only_low_cardinality_fields(caplog):
    logger = logging.getLogger("test.schedule.dispatch")

    with caplog.at_level(logging.INFO, logger=logger.name):
        emit_schedule_dispatch_signal(
            logger,
            "schedule_enqueue_failure_total",
            status="pending",
            reason="broker_enqueue_failed",
            mode="claim",
            value=2,
        )

    message = caplog.messages[-1]
    assert "event=schedule_enqueue_failure_total" in message
    assert "value=2" in message
    assert "status=pending" in message
    assert "reason=broker_enqueue_failed" in message
    assert "mode=claim" in message
    assert "claim_id" not in message
    assert "organization_id" not in message


def test_schedule_dispatch_signal_drops_unknown_event_and_invalid_value():
    logger = logging.getLogger("test.schedule.dispatch")

    assert emit_schedule_dispatch_signal(logger, "schedule.raw_payload") is False
    assert (
        emit_schedule_dispatch_signal(
            logger,
            "schedule_claim_created_total",
            value=-1,
        )
        is False
    )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("status", "claim-id-controlled-value"),
        ("reason", "raw-provider-error"),
        ("mode", "tenant-specific-mode"),
    ),
)
def test_schedule_dispatch_signal_rejects_unbounded_label_values(
    field,
    value,
):
    logger = logging.getLogger("test.schedule.dispatch")

    assert (
        emit_schedule_dispatch_signal(
            logger,
            "schedule_claim_dead_letter_total",
            **{field: value},
        )
        is False
    )


def test_schedule_dispatch_signal_backend_failure_does_not_escape():
    class _FailingLogger:
        def info(self, *_args, **_kwargs):
            raise RuntimeError("backend unavailable")

    assert (
        emit_schedule_dispatch_signal(
            _FailingLogger(),
            "schedule_claim_created_total",
            mode="claim",
        )
        is False
    )
