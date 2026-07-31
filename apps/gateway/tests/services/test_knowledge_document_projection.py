import pytest

from apps.gateway.services.knowledge_document_projection import (
    project_safe_document_error,
    project_safe_document_metadata,
    project_safe_document_progress,
    project_safe_document_progress_message,
    project_safe_document_status,
)


def test_document_metadata_projection_returns_only_allowlisted_safe_fields():
    projected = project_safe_document_metadata(
        {
            "progress": 45,
            "processing_progress": 45.5,
            "processing_current_step": "Processing chunks.",
            "processing_enqueued_at": "2026-07-13T09:00:00+00:00",
            "processing_started_at": "2026-07-13T09:00:01Z",
            "processing_progress_updated_at": "2026-07-13T09:00:02+00:00",
            "processing_recovered_from_timeout": False,
            "remove_urls_emails": True,
            "remove_whitespace": False,
            "chunking_mode": "HIERARCHICAL",
            "strategy": "general",
            "upload_method": "direct",
            "cost_estimate": {"pages": 2, "credits": 2, "cost_usd": 0.006},
            "api_config": {
                "url_encrypted": None,
                "headers_encrypted": None,
                "body_encrypted": None,
            },
            "connection_id": None,
            "source_identity_id": None,
            "source_connector_ref": None,
            "db_config": {"connection_id": None},
            "unknown_nested": {"field": "not-projected"},
        }
    )

    assert projected == {
        "progress": 45,
        "processing_progress": 45.5,
        "processing_enqueued_at": "2026-07-13T09:00:00+00:00",
        "processing_started_at": "2026-07-13T09:00:01Z",
        "processing_progress_updated_at": "2026-07-13T09:00:02+00:00",
        "processing_recovered_from_timeout": False,
        "remove_urls_emails": True,
        "remove_whitespace": False,
        "chunking_mode": "hierarchical",
        "strategy": "general",
        "upload_method": "direct",
        "cost_estimate": {"pages": 2, "credits": 2, "cost_usd": 0.006},
    }


def test_document_metadata_projection_omits_invalid_values_without_fallback():
    projected = project_safe_document_metadata(
        {
            "progress": True,
            "processing_progress": 101,
            "processing_current_step": ["unexpected"],
            "processing_enqueued_at": "2026-07-13T09:00:00",
            "processing_started_at": "not-a-timestamp",
            "processing_progress_updated_at": "x" * 65,
            "processing_recovered_from_timeout": "false",
            "remove_urls_emails": 1,
            "remove_whitespace": None,
            "chunking_mode": "unknown",
            "strategy": "custom",
            "upload_method": "other",
            "cost_estimate": {
                "pages": -1,
                "credits": float("inf"),
                "cost_usd": float("nan"),
            },
        }
    )

    assert projected == {}


def test_document_metadata_projection_accepts_partial_bounded_cost_estimate():
    projected = project_safe_document_metadata(
        {
            "cost_estimate": {
                "pages": 3,
                "credits": "3",
                "cost_usd": 0.009,
                "unknown": 10,
            }
        }
    )

    assert projected == {"cost_estimate": {"pages": 3, "cost_usd": 0.009}}


def test_document_metadata_projection_rejects_non_mapping_input():
    assert project_safe_document_metadata(["unexpected"]) == {}


def test_document_metadata_projection_does_not_return_persisted_step_text():
    projected = project_safe_document_metadata(
        {"processing_current_step": "legacy-step-marker", "progress": 10}
    )

    assert projected == {"progress": 10}
    assert "legacy-step-marker" not in repr(projected)


def test_document_error_projection_returns_only_fixed_public_messages():
    failed = project_safe_document_error(
        "failed",
        "legacy-internal-exception-marker",
    )
    completed = project_safe_document_error(
        "completed",
        "legacy-completion-warning-marker",
    )

    assert failed == "Document processing failed. You can retry the document."
    assert completed == "Document processing completed with a notice."
    assert "legacy-internal-exception-marker" not in failed
    assert "legacy-completion-warning-marker" not in completed
    assert project_safe_document_error("processing", "legacy-marker") is None


def test_document_status_and_message_projection_fail_closed_for_unknown_status():
    assert project_safe_document_status("PROCESSING") == "processing"
    assert project_safe_document_status("legacy-status-marker") == "failed"
    assert project_safe_document_progress_message("legacy-status-marker") == (
        "Document processing failed. You can retry the document."
    )


@pytest.mark.parametrize(
    ("status", "redis_progress", "expected"),
    [
        ("processing", b"55", 55),
        ("processing", "not-a-number", 0),
        ("processing", -1, 0),
        ("processing", 101, 0),
        ("processing", "1000", 0),
        ("processing", True, 0),
        ("pending", 100, 0),
        ("waiting_for_approval", b"100", 0),
        ("completed", "not-a-number", 100),
        ("failed", 75, 0),
    ],
)
def test_document_progress_projection_is_bounded(status, redis_progress, expected):
    assert project_safe_document_progress(status, redis_progress) == expected
