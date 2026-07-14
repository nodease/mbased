import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from apps.workflow_engine.adapters.mail_processing_repository import (
    SqlAlchemyMailProcessingRepository,
)
from apps.workflow_engine.application.mail_processing import (
    MailProcessingApplicationError,
)


def _query_result(*, scalar=None, first=None, all_rows=None):
    query = MagicMock()
    query.filter.return_value = query
    query.with_for_update.return_value = query
    query.scalar.return_value = scalar
    query.first.return_value = first
    query.all.return_value = all_rows or []
    return query


def test_message_registration_uses_app_workflow_lifecycle_lock():
    source = __import__("inspect").getsource(
        SqlAlchemyMailProcessingRepository.register_message
    )

    assert "lock_app_workflow_for_admission" in source
    assert "mail.processing_not_available" in source


def test_outcome_unknown_transitions_effect_and_parent_processing_terminally():
    processing_id = uuid.uuid4()
    processing = SimpleNamespace(
        status="processing",
        deployment_id=None,
        safe_reason_code=None,
        completed_at=None,
    )
    effect = SimpleNamespace(
        processing_id=processing_id,
        status="claimed",
        safe_reason_code=None,
        lease_owner_hash="owner",
        lease_expires_at=object(),
        completed_at=None,
    )
    db = MagicMock()
    db.query.side_effect = [
        _query_result(scalar=processing_id),
        _query_result(first=processing),
        _query_result(first=effect),
    ]
    fixed_now = object()
    repository = SqlAlchemyMailProcessingRepository(db, clock=lambda: fixed_now)

    repository.record_draft_outcome_unknown(
        effect_id=uuid.uuid4(),
        safe_reason_code="mail.draft_outcome_unknown",
    )

    assert effect.status == "outcome_unknown"
    assert effect.safe_reason_code == "mail.draft_outcome_unknown"
    assert effect.lease_owner_hash is None
    assert effect.lease_expires_at is None
    assert effect.completed_at is fixed_now
    assert processing.status == "outcome_unknown"
    assert processing.safe_reason_code == "mail.draft_outcome_unknown"
    assert processing.completed_at is fixed_now
    db.commit.assert_called_once_with()


def test_failed_before_effect_retry_delay_aligns_with_first_celery_retry():
    now = datetime.now(timezone.utc)
    processing_id = uuid.uuid4()
    processing = SimpleNamespace(status="processing")
    effect = SimpleNamespace(
        processing_id=processing_id,
        status="claimed",
        safe_reason_code=None,
        lease_owner_hash="owner",
        lease_expires_at=now + timedelta(minutes=5),
        next_attempt_at=None,
        completed_at=None,
    )
    db = MagicMock()
    db.query.side_effect = [
        _query_result(scalar=processing_id),
        _query_result(first=processing),
        _query_result(first=effect),
    ]
    repository = SqlAlchemyMailProcessingRepository(db, clock=lambda: now)

    repository.record_draft_failed_before_effect(
        effect_id=uuid.uuid4(),
        safe_reason_code="mail.draft_provider_unavailable",
    )

    assert effect.status == "failed_before_effect"
    assert effect.next_attempt_at == now + timedelta(seconds=1)
    assert effect.lease_owner_hash is None


def test_succeeded_processing_rejects_new_draft_effect_admission():
    processing = SimpleNamespace(status="succeeded")
    db = MagicMock()
    db.query.side_effect = [
        _query_result(first=processing),
        _query_result(first=None),
    ]
    repository = SqlAlchemyMailProcessingRepository(db)

    with pytest.raises(MailProcessingApplicationError) as exc_info:
        repository.claim_draft_effect(
            processing_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            workflow_id=uuid.uuid4(),
            deployment_id=None,
            node_id="new-draft-node",
            operation_key_hash="a" * 64,
            input_digest="b" * 64,
            lease_owner_hash="c" * 64,
            lease_expires_at=datetime.now(timezone.utc),
            max_attempts=3,
        )

    assert exc_info.value.reason_code == "mail.processing_not_available"
    db.add.assert_not_called()


def test_retry_attempt_exhaustion_transitions_effect_and_parent_terminally():
    now = datetime.now(timezone.utc)
    processing = SimpleNamespace(
        status="processing",
        deployment_id=None,
        safe_reason_code=None,
        completed_at=None,
        lease_owner_hash=None,
        lease_expires_at=None,
    )
    effect = SimpleNamespace(
        id=uuid.uuid4(),
        input_digest="b" * 64,
        status="failed_before_effect",
        attempt_count=3,
        next_attempt_at=now - timedelta(seconds=1),
        safe_reason_code=None,
        completed_at=None,
    )
    db = MagicMock()
    db.query.side_effect = [
        _query_result(first=processing),
        _query_result(first=effect),
    ]
    repository = SqlAlchemyMailProcessingRepository(db, clock=lambda: now)

    admission = repository.claim_draft_effect(
        processing_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        deployment_id=None,
        node_id="draft",
        operation_key_hash="a" * 64,
        input_digest="b" * 64,
        lease_owner_hash="c" * 64,
        lease_expires_at=now + timedelta(minutes=5),
        max_attempts=3,
    )

    assert admission.status == "exhausted"
    assert admission.acquired is False
    assert effect.status == "exhausted"
    assert effect.completed_at == now
    assert processing.status == "failed"
    assert processing.completed_at == now
    db.commit.assert_called_once_with()


def test_failed_effect_cannot_be_reclaimed_before_durable_retry_time():
    now = datetime.now(timezone.utc)
    processing = SimpleNamespace(status="processing", deployment_id=None)
    effect = SimpleNamespace(
        id=uuid.uuid4(),
        input_digest="b" * 64,
        status="failed_before_effect",
        attempt_count=1,
        next_attempt_at=now + timedelta(seconds=30),
    )
    db = MagicMock()
    db.query.side_effect = [
        _query_result(first=processing),
        _query_result(first=effect),
    ]
    repository = SqlAlchemyMailProcessingRepository(db, clock=lambda: now)

    admission = repository.claim_draft_effect(
        processing_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        deployment_id=None,
        node_id="draft",
        operation_key_hash="a" * 64,
        input_digest="b" * 64,
        lease_owner_hash="c" * 64,
        lease_expires_at=now + timedelta(minutes=5),
        max_attempts=3,
    )

    assert admission.status == "failed_before_effect"
    assert admission.acquired is False
    db.commit.assert_called_once_with()


def test_pending_processing_without_effect_rebinds_to_current_deployment():
    now = datetime.now(timezone.utc)
    old_deployment_id = uuid.uuid4()
    new_deployment_id = uuid.uuid4()
    processing = SimpleNamespace(
        status="pending",
        deployment_id=old_deployment_id,
        attempt_count=0,
    )
    db = MagicMock()
    db.query.side_effect = [
        _query_result(first=processing),
        _query_result(first=None),
    ]
    repository = SqlAlchemyMailProcessingRepository(db, clock=lambda: now)

    admission = repository.claim_draft_effect(
        processing_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        deployment_id=new_deployment_id,
        node_id="draft",
        operation_key_hash="a" * 64,
        input_digest="b" * 64,
        lease_owner_hash="c" * 64,
        lease_expires_at=now + timedelta(minutes=5),
        max_attempts=3,
    )

    assert admission.acquired is True
    assert processing.deployment_id == new_deployment_id


def test_active_processing_cannot_be_taken_over_by_another_deployment():
    processing = SimpleNamespace(
        status="processing",
        deployment_id=uuid.uuid4(),
    )
    effect = SimpleNamespace(
        input_digest="b" * 64,
        status="claimed",
    )
    db = MagicMock()
    db.query.side_effect = [
        _query_result(first=processing),
        _query_result(first=effect),
    ]
    repository = SqlAlchemyMailProcessingRepository(db)

    with pytest.raises(MailProcessingApplicationError) as exc_info:
        repository.claim_draft_effect(
            processing_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            workflow_id=uuid.uuid4(),
            deployment_id=uuid.uuid4(),
            node_id="draft",
            operation_key_hash="a" * 64,
            input_digest="b" * 64,
            lease_owner_hash="c" * 64,
            lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            max_attempts=3,
        )

    assert exc_info.value.reason_code == "mail.processing_deployment_conflict"


def test_completed_acknowledgement_admission_is_idempotent_without_reclaim():
    processing = SimpleNamespace(status="succeeded")
    db = MagicMock()
    db.query.return_value = _query_result(first=processing)
    repository = SqlAlchemyMailProcessingRepository(db)

    admission = repository.require_effects_succeeded(
        processing_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        deployment_id=uuid.uuid4(),
        effect_ids=[uuid.uuid4()],
        required_effect_contract_hash="a" * 64,
        lease_owner_hash="b" * 64,
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )

    assert admission.status == "succeeded"
    assert admission.acquired is False
    db.commit.assert_called_once_with()


def test_active_acknowledgement_lease_blocks_duplicate_admission():
    now = datetime.now(timezone.utc)
    effect_id = uuid.uuid4()
    deployment_id = uuid.uuid4()
    processing = SimpleNamespace(
        status="ack_pending",
        deployment_id=deployment_id,
        required_effect_contract_hash="a" * 64,
        lease_owner_hash="existing-owner",
        lease_expires_at=now + timedelta(minutes=1),
    )
    effect = SimpleNamespace(id=effect_id, status="succeeded")
    db = MagicMock()
    db.query.side_effect = [
        _query_result(first=processing),
        _query_result(all_rows=[effect]),
    ]
    repository = SqlAlchemyMailProcessingRepository(db, clock=lambda: now)

    admission = repository.require_effects_succeeded(
        processing_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        deployment_id=deployment_id,
        effect_ids=[effect_id],
        required_effect_contract_hash="a" * 64,
        lease_owner_hash="new-owner",
        lease_expires_at=now + timedelta(minutes=5),
    )

    assert admission.status == "ack_pending"
    assert admission.acquired is False
    assert processing.lease_owner_hash == "existing-owner"


def test_acknowledgement_failure_releases_lease_for_ack_only_retry():
    processing = SimpleNamespace(
        status="ack_pending",
        safe_reason_code=None,
        lease_owner_hash="owner",
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    db = MagicMock()
    db.query.return_value = _query_result(first=processing)
    repository = SqlAlchemyMailProcessingRepository(db)

    repository.record_acknowledgement_pending(
        processing_id=uuid.uuid4(),
        safe_reason_code="mail.acknowledgement_failed",
    )

    assert processing.safe_reason_code == "mail.acknowledgement_failed"
    assert processing.lease_owner_hash is None
    assert processing.lease_expires_at is None
