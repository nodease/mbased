from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from apps.gateway.application.deployment.review_schedule_outcome import (
    ScheduleOutcomeReviewError,
    ScheduleOutcomeReviewUseCase,
)
from apps.gateway.application.deployment.schedule_models import (
    ScheduleOutcomeReviewSnapshot,
    ScheduleRollbackBlockers,
)
from apps.gateway.application.deployment.schedule_rollback_preflight import (
    ScheduleRollbackPreflightUseCase,
)
from apps.shared.domain.schedule_dispatch import (
    REASON_EXECUTION_OUTCOME_UNKNOWN,
    RESOLUTION_CONFIRMED_COMPLETED,
    STATUS_DEAD_LETTERED,
)

NOW = datetime(2026, 7, 11, tzinfo=timezone.utc)


class _Repository:
    def __init__(self, claim):
        self.claim = claim
        self.reviewed = []
        self.blockers = ScheduleRollbackBlockers(0, 0)

    def database_now(self):
        return NOW

    def lock_outcome_review_claim(self, claim_id):
        return self.claim if self.claim and self.claim.claim_id == claim_id else None

    def mark_outcome_reviewed(self, claim_id, **kwargs):
        self.reviewed.append((claim_id, kwargs))

    def count_rollback_blockers(self):
        return self.blockers


class _Audit:
    def __init__(self):
        self.audit_id = uuid.uuid4()
        self.calls = []

    def record_outcome_reviewed(self, **kwargs):
        self.calls.append(kwargs)
        return self.audit_id


class _Uow:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _claim(**overrides):
    values = {
        "claim_id": uuid.uuid4(),
        "organization_id": uuid.uuid4(),
        "status": STATUS_DEAD_LETTERED,
        "safe_reason_code": REASON_EXECUTION_OUTCOME_UNKNOWN,
        "outcome_reviewed_at": None,
    }
    values.update(overrides)
    return ScheduleOutcomeReviewSnapshot(**values)


def test_outcome_review_records_audit_and_acknowledgment_in_one_uow():
    claim = _claim()
    repository = _Repository(claim)
    audit = _Audit()
    uow = _Uow()

    result = ScheduleOutcomeReviewUseCase().review(
        repository=repository,
        audit=audit,
        uow=uow,
        claim_id=claim.claim_id,
        resolution=RESOLUTION_CONFIRMED_COMPLETED,
        operation_correlation_id="github-run:12345",
    )

    assert result.audit_id == audit.audit_id
    assert uow.commits == 1
    assert uow.rollbacks == 0
    assert audit.calls == [
        {
            "organization_id": claim.organization_id,
            "claim_id": claim.claim_id,
            "operation_correlation_id": "github-run:12345",
            "outcome_resolution_code": RESOLUTION_CONFIRMED_COMPLETED,
        }
    ]
    assert repository.reviewed == [
        (
            claim.claim_id,
            {
                "reviewed_at": NOW,
                "audit_id": audit.audit_id,
                "resolution": RESOLUTION_CONFIRMED_COMPLETED,
            },
        )
    ]
    assert claim.status == STATUS_DEAD_LETTERED


@pytest.mark.parametrize(
    "claim",
    [
        None,
        _claim(status="succeeded"),
        _claim(outcome_reviewed_at=NOW),
    ],
)
def test_outcome_review_rejects_missing_nonreviewable_or_reviewed_claim(claim):
    repository = _Repository(claim)
    uow = _Uow()
    claim_id = claim.claim_id if claim else uuid.uuid4()

    with pytest.raises(ScheduleOutcomeReviewError):
        ScheduleOutcomeReviewUseCase().review(
            repository=repository,
            audit=_Audit(),
            uow=uow,
            claim_id=claim_id,
            resolution=RESOLUTION_CONFIRMED_COMPLETED,
            operation_correlation_id=f"k8s-job:{uuid.uuid4()}",
        )

    assert uow.rollbacks == 1
    assert repository.reviewed == []


@pytest.mark.parametrize(
    ("resolution", "correlation"),
    [
        ("succeeded", "github-run:1"),
        (RESOLUTION_CONFIRMED_COMPLETED, "incident raw note"),
    ],
)
def test_outcome_review_rejects_unallowlisted_operation_input(
    resolution, correlation
):
    with pytest.raises(ValueError):
        ScheduleOutcomeReviewUseCase().review(
            repository=_Repository(_claim()),
            audit=_Audit(),
            uow=_Uow(),
            claim_id=uuid.uuid4(),
            resolution=resolution,
            operation_correlation_id=correlation,
        )


def test_rollback_preflight_returns_db_blockers_without_mutation():
    repository = _Repository(_claim())
    repository.blockers = ScheduleRollbackBlockers(2, 1)

    result = ScheduleRollbackPreflightUseCase().evaluate(repository=repository)

    assert result.ready is False
    assert result.nonterminal_claims == 2
    assert result.unreviewed_outcome_unknown_claims == 1
