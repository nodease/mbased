from datetime import datetime, timezone

import pytest
from apps.shared.db.models.knowledge import RAGAnswerRun
from apps.shared.schemas.knowledge import KnowledgeRAGRecommendedOptions
from apps.shared.schemas.rag import (
    DocumentPreviewRequest,
    MetadataFilter,
    RAGAgentAnswerRequest,
    SearchQuery,
    TagFilter,
)
from apps.shared.services.rag_filters import (
    build_keyword_filter_clause,
    build_sqlalchemy_filter_conditions,
    normalize_metadata_filter,
)
from pydantic import ValidationError
from sqlalchemy import CheckConstraint


def test_knowledge_rag_recommended_options_use_retrieval_defaults():
    options = KnowledgeRAGRecommendedOptions()

    assert options.scoreThreshold == 0.3
    assert options.topK == 5


def test_search_query_accepts_metadata_filter_contract():
    query = SearchQuery(
        query="policy",
        top_k=20,
        metadata_filter=MetadataFilter(
            classification=["INTERNAL", "confidential"],
            tags=TagFilter(mode="contains_all", values=["policy", "hr"]),
            source_type=["file"],
            effective_at=datetime(2026, 6, 30, tzinfo=timezone.utc),
        ),
        hierarchy_mode="flat",
    )

    assert query.metadata_filter.classification == ["internal", "confidential"]
    assert query.metadata_filter.tags.values == ["policy", "hr"]
    assert query.metadata_filter.source_type == ["FILE"]


def test_search_query_rejects_duplicate_shortcuts():
    with pytest.raises(ValidationError):
        SearchQuery(
            query="policy",
            metadata_filter=MetadataFilter(classification=["internal"]),
            classification_filter=["public"],
        )


def test_metadata_filter_rejects_free_form_keys():
    with pytest.raises(ValidationError):
        MetadataFilter(hierarchy_mode="parent_child")


def test_document_preview_request_accepts_chunking_mode_alias():
    request = DocumentPreviewRequest(chunkingMode="hierarchical")

    assert request.chunking_mode == "hierarchical"
    assert request.model_dump(by_alias=True)["chunkingMode"] == "hierarchical"


def test_search_query_rejects_top_k_over_cap():
    with pytest.raises(ValidationError):
        SearchQuery(query="policy", top_k=21)


def test_rag_agent_answer_request_requires_explicit_model_and_credential():
    with pytest.raises(ValidationError) as exc:
        RAGAgentAnswerRequest(
            knowledge_base_id="00000000-0000-0000-0000-000000000001",
            query="policy",
            generation_model_id="00000000-0000-0000-0000-000000000002",
        )

    assert "credential_id" in str(exc.value)


def test_rag_agent_answer_request_accepts_top_k_for_service_validation():
    base = {
        "knowledge_base_id": "00000000-0000-0000-0000-000000000001",
        "query": "policy",
        "generation_model_id": "00000000-0000-0000-0000-000000000002",
        "credential_id": "00000000-0000-0000-0000-000000000003",
    }

    request = RAGAgentAnswerRequest(**base, top_k=9)

    assert request.top_k == 9


def test_rag_agent_answer_request_leaves_correlation_id_for_service_validation():
    base = {
        "knowledge_base_id": "00000000-0000-0000-0000-000000000001",
        "query": "policy",
        "generation_model_id": "00000000-0000-0000-0000-000000000002",
        "credential_id": "00000000-0000-0000-0000-000000000003",
    }

    assert (
        RAGAgentAnswerRequest(**base, correlation_id="contains space").correlation_id
        == "contains space"
    )
    assert (
        RAGAgentAnswerRequest(
            **base,
            correlation_id="trace-sk-testSecretValue",
        ).correlation_id
        == "trace-sk-testSecretValue"
    )

    request = RAGAgentAnswerRequest(**base, correlation_id="trace:rag-1")

    assert request.top_k == 8
    assert request.correlation_id == "trace:rag-1"


def test_rag_answer_run_model_contract_matches_documented_statuses_and_indexes():
    table = RAGAnswerRun.__table__
    status_constraint = next(
        constraint
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
        and constraint.name == "ck_rag_answer_runs_status"
    )
    status_sql = str(status_constraint.sqltext)

    for status in (
        "requested",
        "running",
        "completed",
        "failed",
        "cancelled",
        "blocked",
    ):
        assert status in status_sql

    indexes = {
        index.name: [column.name for column in index.columns]
        for index in table.indexes
    }
    assert indexes["ix_rag_answer_runs_org_correlation_created"] == [
        "organization_id",
        "correlation_id",
        "created_at",
    ]
    assert indexes["ix_rag_answer_runs_retention_expires_at"] == [
        "retention_expires_at"
    ]

    assert table.c.organization_id.nullable is False
    assert table.c.user_id.nullable is True
    assert table.c.knowledge_base_id.nullable is True
    assert table.c.generation_model_id.nullable is True
    assert table.c.generation_credential_id.nullable is True
    assert next(iter(table.c.user_id.foreign_keys)).ondelete == "SET NULL"
    assert next(iter(table.c.knowledge_base_id.foreign_keys)).ondelete == "SET NULL"
    assert next(iter(table.c.generation_model_id.foreign_keys)).ondelete == "SET NULL"
    assert (
        next(iter(table.c.generation_credential_id.foreign_keys)).ondelete
        == "SET NULL"
    )


def test_tag_filter_rejects_contract_limit_violations():
    assert TagFilter(values=["Policy", "policy"]).values == ["policy"]

    with pytest.raises(ValidationError):
        TagFilter(values=[f"tag-{index}" for index in range(21)])

    with pytest.raises(ValidationError):
        TagFilter(values=["x" * 65])


def test_normalized_metadata_filter_audit_summary_is_redaction_safe():
    effective_at = datetime(2026, 6, 30, tzinfo=timezone.utc)
    normalized = normalize_metadata_filter(
        classification_filter=["confidential"],
        tags=TagFilter(mode="contains_any", values=["policy", "secret-key"]),
        source_type=["DB"],
        effective_at=effective_at,
    )

    assert normalized.audit_summary() == {
        "classification": ["confidential"],
        "tags": {"mode": "contains_any", "count": 2},
        "source_type": ["DB"],
        "effective_at": effective_at.isoformat(),
    }


def test_tag_filter_builders_compare_lowercase_tag_values():
    normalized = normalize_metadata_filter(
        tags=TagFilter(mode="contains_any", values=["Policy", "policy"])
    )

    assert normalized.tags.values == ("policy",)
    keyword_clause = build_keyword_filter_clause(normalized)
    sqlalchemy_conditions = build_sqlalchemy_filter_conditions(normalized)

    assert any("lower(tag.value)" in fragment for fragment in keyword_clause.fragments)
    assert any("lower(tag.value)" in str(condition) for condition in sqlalchemy_conditions)
    assert any("jsonb_typeof" in fragment for fragment in keyword_clause.fragments)
    assert any("jsonb_typeof" in str(condition) for condition in sqlalchemy_conditions)


def test_effective_at_filter_builders_do_not_cast_malformed_metadata_dates():
    effective_at = datetime(2026, 6, 30, tzinfo=timezone.utc)
    normalized = normalize_metadata_filter(effective_at=effective_at)

    keyword_clause = build_keyword_filter_clause(normalized)
    sqlalchemy_conditions = build_sqlalchemy_filter_conditions(normalized)

    keyword_sql = " ".join(keyword_clause.fragments)
    sqlalchemy_sql = " ".join(str(condition) for condition in sqlalchemy_conditions)

    assert "::timestamptz" not in keyword_sql
    assert "::timestamptz" not in sqlalchemy_sql
    assert "metadata_effective_pattern" in keyword_sql
    assert "metadata_effective_pattern" in sqlalchemy_sql
    assert keyword_clause.params["metadata_effective_at"] == "2026-06-30T00:00:00+00:00"


def test_effective_at_filter_normalizes_to_utc_iso_string():
    effective_at = datetime(2026, 6, 30, 9, 0, 0, tzinfo=timezone.utc)
    normalized = normalize_metadata_filter(effective_at=effective_at)

    keyword_clause = build_keyword_filter_clause(normalized)

    assert keyword_clause.params["metadata_effective_at"] == "2026-06-30T09:00:00+00:00"
    assert r"\+00:00" in keyword_clause.params["metadata_effective_pattern"]
