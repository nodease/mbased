import uuid
from datetime import datetime, timedelta, timezone

import pytest
from apps.shared.domain.knowledge_collection_sync import (
    KnowledgeCollectionSyncStateError,
    collection_sync_state_for_job,
    max_job_attempts_for_targets,
    progress_category,
    retry_delay,
    safe_reason_code,
    sync_target_membership_revision,
    sync_target_revision,
    sync_target_snapshot_revision,
    terminal_job_status,
)


@pytest.mark.parametrize(
    "processed,expected",
    [(0, "started"), (1, "started"), (3, "progressing"), (8, "most")],
)
def test_progress_is_categorical(processed, expected) -> None:
    assert (
        progress_category(
            status="running",
            total_count=10,
            completed_count=processed,
            failed_count=0,
            skipped_count=0,
        )
        == expected
    )


def test_terminal_progress_and_status_do_not_expose_exact_child_identity() -> None:
    assert (
        progress_category(
            status="partially_failed",
            total_count=10,
            completed_count=1,
            failed_count=1,
            skipped_count=8,
        )
        == "complete"
    )
    assert terminal_job_status(
        total_count=2, succeeded_count=1, failed_count=0, skipped_count=1
    ) == "partially_failed"
    assert collection_sync_state_for_job("partially_failed") == "stale"


def test_unknown_reason_is_default_deny_projected() -> None:
    assert safe_reason_code("raw connector failure") == "sync.internal_error"
    assert safe_reason_code(None) is None


def test_invalid_counts_and_retry_attempt_fail_closed() -> None:
    with pytest.raises(KnowledgeCollectionSyncStateError):
        progress_category(
            status="running",
            total_count=1,
            completed_count=2,
            failed_count=0,
            skipped_count=0,
        )
    with pytest.raises(KnowledgeCollectionSyncStateError):
        retry_delay(0)


def test_job_attempt_budget_covers_batches_item_retries_and_stale_recovery() -> None:
    assert max_job_attempts_for_targets(1) == 8
    assert max_job_attempts_for_targets(100) == 225
    with pytest.raises(KnowledgeCollectionSyncStateError):
        max_job_attempts_for_targets(101)


def test_target_revision_is_deterministic_and_changes_with_target_state() -> None:
    collection_id = uuid.uuid4()
    knowledge_base_id = uuid.uuid4()
    document_id = uuid.uuid4()
    created_at = datetime(2026, 7, 15, 1, 2, 3, tzinfo=timezone.utc)
    updated_at = created_at + timedelta(minutes=1)
    values = {
        "collection_id": collection_id,
        "collection_item_id": uuid.uuid4(),
        "knowledge_base_id": knowledge_base_id,
        "document_id": document_id,
        "item_rank": 0,
        "item_created_at": created_at,
        "document_updated_at": updated_at,
    }

    first = sync_target_revision(**values)
    assert sync_target_revision(**values) == first
    assert sync_target_revision(**{**values, "item_rank": 1}) != first
    assert (
        sync_target_revision(
            **{**values, "document_updated_at": updated_at + timedelta(seconds=1)}
        )
        != first
    )
    assert sync_target_snapshot_revision(collection_id, [first]) == (
        sync_target_snapshot_revision(collection_id, [first])
    )

    membership_values = {
        key: value for key, value in values.items() if key != "document_updated_at"
    }
    membership = sync_target_membership_revision(**membership_values)
    assert sync_target_membership_revision(**membership_values) == membership
    assert (
        sync_target_membership_revision(
            **{**membership_values, "item_rank": 1}
        )
        != membership
    )


def test_terminal_status_requires_original_total_to_be_fully_reconciled() -> None:
    with pytest.raises(KnowledgeCollectionSyncStateError):
        terminal_job_status(
            total_count=1,
            succeeded_count=0,
            failed_count=0,
            skipped_count=0,
        )
