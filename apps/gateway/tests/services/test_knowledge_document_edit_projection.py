import json
import uuid
from types import SimpleNamespace

import pytest

from apps.gateway.services.knowledge_document_edit_projection import (
    project_document_edit_config,
)


def _document(*, source_type="FILE", meta_info=None, chunk_size=900, chunk_overlap=90):
    return SimpleNamespace(
        source_type=source_type,
        meta_info=meta_info,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )


def test_file_edit_projection_restores_only_bounded_process_options():
    projected = project_document_edit_config(
        _document(
            meta_info={
                "segment_identifier": "\\n\\n",
                "remove_urls_emails": True,
                "remove_whitespace": False,
                "strategy": "llamaparse",
                "chunking_mode": "hierarchical",
                "selection_mode": "range",
                "chunk_range": "2-7",
                "keyword_filter": "ignored outside keyword mode",
                "internal_runtime_marker": {"value": "not-projected"},
            }
        )
    )

    assert projected == {
        "editable": True,
        "safe_reason_code": None,
        "source_type": "FILE",
        "chunk_size": 900,
        "chunk_overlap": 90,
        "segment_identifier": "\\n\\n",
        "remove_urls_emails": True,
        "remove_whitespace": False,
        "strategy": "llamaparse",
        "chunking_mode": "hierarchical",
        "selection_mode": "range",
        "chunk_range": "2-7",
        "keyword_filter": None,
    }


def test_db_edit_projection_restores_selection_and_opaque_connection_reference():
    connection_id = uuid.uuid4()
    projected = project_document_edit_config(
        _document(
            source_type="DB",
            meta_info={
                "connection_id": str(connection_id),
                "db_config": {
                    "selections": [
                        {
                            "table_name": "employees",
                            "columns": ["id", "display_name"],
                            "sensitive_columns": ["display_name"],
                        }
                    ],
                    "selected_items": {
                        "employees": ["id", "display_name"]
                    },
                    "sensitive_columns": {"employees": ["display_name"]},
                    "aliases": {
                        "employees": {"display_name": "employee"}
                    },
                    "template": "{{employees.display_name}}",
                    "join_config": {
                        "enabled": False,
                        "base_table": "employees",
                        "joins": [],
                    },
                    "connection_detail": None,
                },
            },
        )
    )

    assert projected["editable"] is True
    assert projected["db_config"] == {
        "connection_id": connection_id,
        "selected_items": {"employees": ["id", "display_name"]},
        "sensitive_columns": {"employees": ["display_name"]},
        "aliases": {"employees": {"display_name": "employee"}},
        "template": "{{employees.display_name}}",
        "join_config": {
            "enabled": False,
            "base_table": "employees",
            "joins": [],
        },
    }
    assert "connection_detail" not in projected["db_config"]


def test_db_edit_projection_accepts_bounded_legacy_json_config():
    connection_id = uuid.uuid4()
    projected = project_document_edit_config(
        _document(
            source_type="DB",
            meta_info={
                "db_config": json.dumps(
                    {
                        "connection_id": str(connection_id),
                        "selected_items": {"inventory": ["sku"]},
                    }
                )
            },
        )
    )

    assert projected["editable"] is True
    assert projected["db_config"]["connection_id"] == connection_id
    assert projected["db_config"]["selected_items"] == {"inventory": ["sku"]}


def test_api_edit_projection_returns_presence_summary_without_config_values():
    projected = project_document_edit_config(
        _document(
            source_type="API",
            meta_info={
                "api_config": {
                    "url_encrypted": "opaque-url-ciphertext",
                    "method": "post",
                    "headers_encrypted": "opaque-header-ciphertext",
                    "body_encrypted": None,
                    "safe_label": "HR API source",
                    "raw_payload": None,
                }
            },
        )
    )

    assert projected["editable"] is True
    assert projected["api_config"] == {
        "configured": True,
        "method": "POST",
        "safe_label": "HR API source",
        "has_headers": True,
        "has_body": False,
    }
    serialized = repr(projected)
    assert "opaque-url-ciphertext" not in serialized
    assert "opaque-header-ciphertext" not in serialized
    assert "raw_payload" not in serialized


@pytest.mark.parametrize(
    "document",
    [
        _document(meta_info=["invalid"]),
        _document(chunk_size=100, chunk_overlap=100),
        _document(meta_info={"selection_mode": "range", "chunk_range": "7-2"}),
        _document(source_type="DB", meta_info={"db_config": "[]"}),
        _document(
            source_type="DB",
            meta_info={
                "connection_id": str(uuid.uuid4()),
                "db_config": {
                    "selections": [
                        {"table_name": "a", "columns": ["id"]}
                    ],
                    "selected_items": {"a": ["different"]},
                },
            },
        ),
        _document(source_type="API", meta_info={"api_config": {"method": "GET"}}),
    ],
)
def test_malformed_edit_projection_fails_closed_without_partial_fallback(document):
    assert project_document_edit_config(document) == {
        "editable": False,
        "safe_reason_code": "document.edit_config_unavailable",
        "source_type": document.source_type,
    }


def test_db_edit_projection_rejects_aggregate_item_budget_overflow():
    connection_id = uuid.uuid4()
    selected_items = {
        f"table_{table_index}": [
            f"column_{column_index}" for column_index in range(500)
        ]
        for table_index in range(11)
    }

    projected = project_document_edit_config(
        _document(
            source_type="DB",
            meta_info={
                "connection_id": str(connection_id),
                "db_config": {"selected_items": selected_items},
            },
        )
    )

    assert projected == {
        "editable": False,
        "safe_reason_code": "document.edit_config_unavailable",
        "source_type": "DB",
    }


def test_db_edit_projection_rejects_serialized_response_budget_overflow():
    connection_id = uuid.uuid4()
    aliases = {
        "large_table": {
            f"column_{column_index}": "x" * 512 for column_index in range(500)
        }
    }

    projected = project_document_edit_config(
        _document(
            source_type="DB",
            meta_info={
                "connection_id": str(connection_id),
                "db_config": {"aliases": aliases},
            },
        )
    )

    assert projected == {
        "editable": False,
        "safe_reason_code": "document.edit_config_unavailable",
        "source_type": "DB",
    }
