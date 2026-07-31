import pytest
from apps.shared.services.ingestion.hierarchical_chunker import (
    HierarchicalChunker,
    HierarchicalChunkerConfig,
    filter_hierarchical_chunks,
)
from apps.shared.services.rag_hierarchy import (
    RAGHierarchyError,
    chunking_fingerprint_hash,
    parent_overlap_size,
    parent_target_size,
    validate_chunking_request,
)


def test_validate_chunking_rejects_db_hierarchy():
    with pytest.raises(RAGHierarchyError) as exc:
        validate_chunking_request(
            chunking_mode="hierarchical",
            source_type="DB",
            selection_mode="all",
        )

    assert exc.value.reason == "unsupported_chunking_mode_for_source"


def test_validate_chunking_rejects_hierarchy_range_selection():
    with pytest.raises(RAGHierarchyError) as exc:
        validate_chunking_request(
            chunking_mode="hierarchical",
            source_type="FILE",
            selection_mode="range",
        )

    assert exc.value.reason == "invalid_chunking_selection"


def test_chunking_fingerprint_changes_when_mode_changes():
    base = {
        "chunking_mode": "flat",
        "segment_identifier": "\n\n",
        "selection_mode": "all",
    }
    flat = chunking_fingerprint_hash(
        meta_info=base,
        chunk_size=1000,
        chunk_overlap=200,
        source_type="FILE",
    )
    hierarchical = chunking_fingerprint_hash(
        meta_info={**base, "chunking_mode": "hierarchical"},
        chunk_size=1000,
        chunk_overlap=200,
        source_type="FILE",
    )

    assert flat != hierarchical


def test_parent_chunk_internal_defaults_follow_child_settings():
    target_size = parent_target_size(1000)

    assert target_size == 4000
    assert parent_target_size(100) == 2000
    assert parent_target_size(3000) == 8000
    assert parent_overlap_size(200, target_size) == 400


def test_hierarchical_chunker_preserves_hierarchy_columns_outside_metadata():
    chunker = HierarchicalChunker(
        HierarchicalChunkerConfig(
            child_chunk_size=30,
            child_chunk_overlap=0,
            segment_identifier="\n\n",
            parent_target_size=80,
        )
    )

    chunks = chunker.build(
        [
            {
                "content": "Alpha policy text. " * 6,
                "metadata": {
                    "heading": "Policy",
                    "classification": "internal",
                    "chunk_level": "flat",
                },
            }
        ]
    )

    assert any(chunk["chunk_level"] == "parent" for chunk in chunks)
    assert any(chunk["chunk_level"] == "child" for chunk in chunks)
    child = next(chunk for chunk in chunks if chunk["chunk_level"] == "child")
    assert child["parent_ref"]
    assert child["section_path"] == ["Policy"]
    assert child["metadata"] == {"classification": "internal"}


def test_filter_hierarchical_chunks_keeps_only_matching_child_parent_group():
    chunks = [
        {"chunk_level": "parent", "local_ref": "p1", "content": "Parent 1"},
        {
            "chunk_level": "child",
            "parent_ref": "p1",
            "content": "needle evidence",
        },
        {"chunk_level": "parent", "local_ref": "p2", "content": "Parent 2"},
        {"chunk_level": "child", "parent_ref": "p2", "content": "other"},
    ]

    filtered = filter_hierarchical_chunks(
        chunks,
        selection_mode="keyword",
        keyword_filter="needle",
    )

    assert [chunk["content"] for chunk in filtered] == [
        "Parent 1",
        "needle evidence",
    ]
