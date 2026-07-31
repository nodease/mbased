from __future__ import annotations

import uuid
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from apps.shared.domain.schedule_dispatch import (
    MODE_CLAIM,
    MODE_DISABLED,
    MODE_DRAIN,
    REASON_BROKER_ENQUEUE_FAILED,
    REASON_BUDGET_BLOCKED,
    REASON_CONFIGURATION_PREFLIGHT_BLOCKED,
    REASON_EXECUTION_OUTCOME_UNKNOWN,
    RESOLUTION_CONFIRMED_COMPLETED,
    SCHEDULE_CONFIGURATION_INVALID,
    STATUS_CANCELED,
    STATUS_DEAD_LETTERED,
    STATUS_DISPATCHING,
    STATUS_ENQUEUED,
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_SUCCEEDED,
    ScheduleDispatchClaimState,
    ScheduleDispatchDomainError,
    ScheduleDispatchSettings,
    ensure_transition_allowed,
    next_dispatch_attempt,
    retry_delay_seconds,
    schedule_dispatch_settings_from_environment,
    schedule_idempotency_key,
    validate_schedule_configuration_error_code,
)

SCHEDULE_ID = uuid.UUID("11111111-2222-3333-4444-555555555555")
ORGANIZATION_ID = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
NOW = datetime(2026, 7, 10, 9, 30, 0, 123456, tzinfo=timezone.utc)


def _key() -> str:
    return schedule_idempotency_key(SCHEDULE_ID, NOW)


def _state(**overrides) -> ScheduleDispatchClaimState:
    values = {
        "status": STATUS_PENDING,
        "idempotency_key": _key(),
        "organization_id": ORGANIZATION_ID,
        "claimed_at": NOW,
    }
    values.update(overrides)
    return ScheduleDispatchClaimState(**values)


def test_schedule_idempotency_key_is_deterministic_golden_vector():
    assert _key() == "schedule:a2527058-9924-5fd8-ae7e-2374b0ed01c3"
    assert _key() == schedule_idempotency_key(SCHEDULE_ID, NOW)


def test_schedule_idempotency_key_normalizes_timezone():
    seoul = NOW.astimezone(ZoneInfo("Asia/Seoul"))
    assert schedule_idempotency_key(SCHEDULE_ID, seoul) == _key()


def test_schedule_idempotency_key_preserves_microseconds():
    assert schedule_idempotency_key(SCHEDULE_ID, NOW + timedelta(microseconds=1)) != _key()


@pytest.mark.parametrize(
    ("current", "maximum", "expected"),
    [
        (0, 5, (1, False)),
        (4, 5, (5, True)),
        (5, 5, (5, True)),
        (6, 5, (6, True)),
    ],
)
def test_next_dispatch_attempt_is_bounded(current, maximum, expected):
    assert next_dispatch_attempt(current, max_attempts=maximum) == expected


@pytest.mark.parametrize(("current", "maximum"), [(-1, 5), (0, 0)])
def test_next_dispatch_attempt_rejects_invalid_values(current, maximum):
    with pytest.raises(ScheduleDispatchDomainError):
        next_dispatch_attempt(current, max_attempts=maximum)


def test_schedule_dispatch_mode_fingerprint_must_match_process_mode():
    with pytest.raises(ScheduleDispatchDomainError, match="fingerprint"):
        schedule_dispatch_settings_from_environment(
            {
                "SCHEDULE_DISPATCH_MODE": "claim",
                "SCHEDULE_DISPATCH_MODE_FINGERPRINT": "disabled",
            }
        )

    settings = schedule_dispatch_settings_from_environment(
        {
            "SCHEDULE_DISPATCH_MODE": "drain",
            "SCHEDULE_DISPATCH_MODE_FINGERPRINT": (
                "v1|drain|5|50|10|50|100|60|120|900|120|5|5|30|180"
            ),
        }
    )
    assert settings.mode == "drain"


def test_non_disabled_schedule_dispatch_requires_full_settings_fingerprint():
    with pytest.raises(ScheduleDispatchDomainError, match="fingerprint is required"):
        schedule_dispatch_settings_from_environment(
            {"SCHEDULE_DISPATCH_MODE": "claim"}
        )


def test_schedule_dispatch_fingerprint_covers_non_mode_settings():
    with pytest.raises(ScheduleDispatchDomainError, match="fingerprint"):
        schedule_dispatch_settings_from_environment(
            {
                "SCHEDULE_DISPATCH_MODE": "claim",
                "SCHEDULE_DISPATCH_LEASE_SECONDS": "61",
                "SCHEDULE_DISPATCH_MODE_FINGERPRINT": (
                    "v1|claim|5|50|10|50|100|60|120|900|120|5|5|30|180"
                ),
            }
        )


@pytest.mark.parametrize("value", [datetime(2026, 7, 10), None])
def test_schedule_idempotency_key_rejects_non_aware_datetime(value):
    with pytest.raises(ScheduleDispatchDomainError):
        schedule_idempotency_key(SCHEDULE_ID, value)


def test_schedule_idempotency_key_rejects_non_uuid_schedule_id():
    with pytest.raises(ScheduleDispatchDomainError):
        schedule_idempotency_key(str(SCHEDULE_ID), NOW)


def test_configuration_quarantine_code_is_allowlisted():
    validate_schedule_configuration_error_code(SCHEDULE_CONFIGURATION_INVALID)

    with pytest.raises(ScheduleDispatchDomainError):
        validate_schedule_configuration_error_code("raw parser detail")


def test_claim_state_requires_claimed_at():
    with pytest.raises(ScheduleDispatchDomainError, match="claimed_at is required"):
        _state(claimed_at=None).validate()


def test_configuration_preflight_reason_is_valid_only_for_canceled_claim():
    _state(
        status=STATUS_CANCELED,
        safe_reason_code=REASON_CONFIGURATION_PREFLIGHT_BLOCKED,
        completed_at=NOW,
    ).validate()

    with pytest.raises(ScheduleDispatchDomainError, match="invalid pending reason"):
        _state(
            safe_reason_code=REASON_CONFIGURATION_PREFLIGHT_BLOCKED,
        ).validate()


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (STATUS_PENDING, STATUS_DISPATCHING),
        (STATUS_PENDING, STATUS_CANCELED),
        (STATUS_DISPATCHING, STATUS_ENQUEUED),
        (STATUS_DISPATCHING, STATUS_RUNNING),
        (STATUS_ENQUEUED, STATUS_PENDING),
        (STATUS_ENQUEUED, STATUS_RUNNING),
        (STATUS_RUNNING, STATUS_SUCCEEDED),
        (STATUS_RUNNING, STATUS_DEAD_LETTERED),
    ],
)
def test_allowed_transitions(current, target):
    ensure_transition_allowed(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (STATUS_PENDING, STATUS_RUNNING),
        (STATUS_SUCCEEDED, STATUS_RUNNING),
        (STATUS_CANCELED, STATUS_PENDING),
        (STATUS_DEAD_LETTERED, STATUS_ENQUEUED),
        ("unknown", STATUS_PENDING),
        (STATUS_PENDING, "unknown"),
    ],
)
def test_disallowed_transitions_fail_closed(current, target):
    with pytest.raises(ScheduleDispatchDomainError):
        ensure_transition_allowed(current, target)


@pytest.mark.parametrize(
    ("attempt", "expected"),
    [(1, 5), (2, 10), (3, 20), (6, 160), (7, 300), (20, 300)],
)
def test_retry_delay_is_bounded(attempt, expected):
    assert retry_delay_seconds(attempt) == expected


@pytest.mark.parametrize("attempt", [0, -1])
def test_retry_delay_rejects_non_positive_attempt(attempt):
    with pytest.raises(ScheduleDispatchDomainError):
        retry_delay_seconds(attempt)


@pytest.mark.parametrize(
    ("mode", "claims", "processes"),
    [
        (MODE_DISABLED, False, False),
        (MODE_DRAIN, False, True),
        (MODE_CLAIM, True, True),
    ],
)
def test_settings_mode_capabilities(mode, claims, processes):
    settings = ScheduleDispatchSettings(mode=mode)
    assert settings.claims_new_occurrences is claims
    assert settings.processes_existing_claims is processes


def test_settings_are_immutable():
    settings = ScheduleDispatchSettings()
    with pytest.raises(FrozenInstanceError):
        settings.mode = MODE_CLAIM


def test_settings_factory_defaults_to_disabled_and_reads_explicit_values():
    assert schedule_dispatch_settings_from_environment({}).mode == MODE_DISABLED

    settings = schedule_dispatch_settings_from_environment(
        {
            "SCHEDULE_DISPATCH_MODE": "claim",
            "SCHEDULE_DISPATCH_POLL_SECONDS": "7",
            "SCHEDULE_DISPATCH_MAX_ATTEMPTS": "8",
            "SCHEDULE_DISPATCH_MODE_FINGERPRINT": (
                "v1|claim|7|50|10|50|100|60|120|900|120|8|5|30|180"
            ),
        }
    )

    assert settings.mode == MODE_CLAIM
    assert settings.poll_seconds == 7
    assert settings.max_attempts == 8


def test_settings_factory_rejects_invalid_integer_without_fallback():
    with pytest.raises(ScheduleDispatchDomainError):
        schedule_dispatch_settings_from_environment(
            {"SCHEDULE_DISPATCH_POLL_SECONDS": "not-an-integer"}
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"mode": "legacy"},
        {"poll_seconds": 0},
        {"occurrence_batch_size": 501},
        {"dispatch_batch_size": 0},
        {"recovery_batch_size": 501},
        {"cleanup_batch_size": 1001},
        {"lease_seconds": 4},
        {"delivery_timeout_seconds": 29},
        {"execution_deadline_seconds": 599},
        {"workflow_run_visibility_timeout_seconds": 1801},
        {"max_attempts": 21},
        {"retry_base_seconds": 0},
        {"retention_days": 366},
        {"dead_letter_retention_days": 29},
        {"retention_days": 200, "dead_letter_retention_days": 100},
    ],
)
def test_settings_reject_out_of_range_values(overrides):
    with pytest.raises(ScheduleDispatchDomainError):
        ScheduleDispatchSettings(**overrides)


def test_pending_claim_state_accepts_bounded_retry_fields():
    state = _state(
        attempt_count=1,
        celery_task_id=_key(),
        enqueued_at=NOW,
        next_attempt_at=NOW + timedelta(seconds=5),
        safe_reason_code=REASON_BROKER_ENQUEUE_FAILED,
    )
    state.validate()


@pytest.mark.parametrize(
    ("status", "fields"),
    [
        (STATUS_PENDING, {}),
        (
            STATUS_DISPATCHING,
            {
                "lease_owner": "gateway",
                "lease_expires_at": NOW + timedelta(minutes=1),
                "celery_task_id": _key(),
            },
        ),
        (
            STATUS_ENQUEUED,
            {
                "celery_task_id": _key(),
                "enqueued_at": NOW,
                "lease_expires_at": NOW + timedelta(minutes=2),
            },
        ),
    ],
)
def test_pre_admission_claim_rejects_workflow_run_correlation(status, fields):
    with pytest.raises(ScheduleDispatchDomainError):
        _state(
            status=status,
            workflow_run_id=uuid.uuid4(),
            **fields,
        ).validate()


def test_running_claim_requires_admission_correlation():
    with pytest.raises(ScheduleDispatchDomainError):
        _state(
            status=STATUS_RUNNING,
            lease_owner="worker",
            celery_task_id=_key(),
            enqueued_at=NOW,
            started_at=NOW,
            execution_deadline_at=NOW + timedelta(minutes=15),
        ).validate()


def test_succeeded_claim_requires_run_and_timestamps():
    state = _state(
        status=STATUS_SUCCEEDED,
        celery_task_id=_key(),
        workflow_run_id=uuid.uuid4(),
        enqueued_at=NOW,
        started_at=NOW,
        completed_at=NOW,
    )
    state.validate()


def test_canceled_claim_rejects_missing_reason():
    with pytest.raises(ScheduleDispatchDomainError):
        _state(status=STATUS_CANCELED, completed_at=NOW).validate()


def test_canceled_claim_accepts_safe_reason():
    _state(
        status=STATUS_CANCELED,
        completed_at=NOW,
        safe_reason_code=REASON_BUDGET_BLOCKED,
    ).validate()


def test_outcome_review_requires_all_fields_and_unknown_outcome():
    with pytest.raises(ScheduleDispatchDomainError):
        _state(
            status=STATUS_DEAD_LETTERED,
            completed_at=NOW,
            safe_reason_code=REASON_EXECUTION_OUTCOME_UNKNOWN,
            outcome_reviewed_at=NOW,
        ).validate()


def test_reviewed_outcome_unknown_is_valid_and_not_replayable():
    state = _state(
        status=STATUS_DEAD_LETTERED,
        celery_task_id=_key(),
        workflow_run_id=uuid.uuid4(),
        enqueued_at=NOW,
        started_at=NOW,
        completed_at=NOW,
        safe_reason_code=REASON_EXECUTION_OUTCOME_UNKNOWN,
        outcome_reviewed_at=NOW,
        outcome_review_audit_id=uuid.uuid4(),
        outcome_resolution_code=RESOLUTION_CONFIRMED_COMPLETED,
    )
    state.validate()
    with pytest.raises(ScheduleDispatchDomainError):
        ensure_transition_allowed(state.status, STATUS_ENQUEUED)


@pytest.mark.parametrize(
    "reason",
    ["execution_failed_after_admission", "execution_outcome_unknown"],
)
def test_post_admission_dead_letter_requires_complete_run_correlation(reason):
    with pytest.raises(ScheduleDispatchDomainError):
        _state(
            status=STATUS_DEAD_LETTERED,
            workflow_run_id=uuid.uuid4(),
            completed_at=NOW,
            safe_reason_code=reason,
        ).validate()


@pytest.mark.parametrize(
    "reason",
    ["budget_evaluation_failed", "enqueue_attempts_exhausted"],
)
def test_pre_admission_dead_letter_rejects_workflow_run_correlation(reason):
    with pytest.raises(ScheduleDispatchDomainError):
        _state(
            status=STATUS_DEAD_LETTERED,
            celery_task_id=_key(),
            workflow_run_id=uuid.uuid4(),
            enqueued_at=NOW,
            started_at=NOW,
            completed_at=NOW,
            safe_reason_code=reason,
        ).validate()


def test_claim_rejects_mismatched_celery_task_id():
    with pytest.raises(ScheduleDispatchDomainError):
        _state(celery_task_id="schedule:not-the-key").validate()


def test_claim_rejects_missing_organization_provenance():
    with pytest.raises(ScheduleDispatchDomainError):
        _state(organization_id=None).validate()


def test_claim_rejects_non_monotonic_timestamps():
    with pytest.raises(ScheduleDispatchDomainError):
        _state(
            status=STATUS_SUCCEEDED,
            celery_task_id=_key(),
            workflow_run_id=uuid.uuid4(),
            enqueued_at=NOW + timedelta(seconds=2),
            started_at=NOW + timedelta(seconds=1),
            completed_at=NOW + timedelta(seconds=3),
        ).validate()
