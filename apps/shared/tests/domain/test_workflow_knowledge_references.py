from uuid import UUID

import pytest
from apps.shared.domain.workflow_knowledge_references import (
    WorkflowKnowledgeReferenceError,
    aggregate_workflow_knowledge_reference_ids,
    parse_llm_knowledge_references,
    parse_workflow_knowledge_references,
)

KB_1 = "00000000-0000-0000-0000-000000000001"
KB_2 = "00000000-0000-0000-0000-000000000002"
COLLECTION_1 = "10000000-0000-0000-0000-000000000001"
ALPHA_UUID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def _llm_data(**overrides):
    data = {
        "knowledgeBases": [{"id": KB_1, "name": "규칙"}],
        "knowledgeCollections": [
            {"id": COLLECTION_1, "safeLabel": "사내 문서"}
        ],
    }
    data.update(overrides)
    return data


def _assert_error(data, reason_code, field_path):
    with pytest.raises(WorkflowKnowledgeReferenceError) as error:
        parse_llm_knowledge_references(data)
    assert error.value.reason_code == reason_code
    assert error.value.field_path == field_path
    assert KB_1 not in str(error.value)


def test_legacy_direct_only_and_missing_collection_field_are_valid():
    parsed = parse_llm_knowledge_references(
        {"knowledgeBases": [{"id": KB_1, "name": ""}]}
    )

    assert parsed.direct_kb_ids == (UUID(KB_1),)
    assert parsed.collection_ids == ()
    assert parsed.direct_references[0].name == ""


def test_collection_only_and_mixed_references_are_valid_without_mode():
    collection_only = parse_llm_knowledge_references(
        {"knowledgeCollections": [{"id": COLLECTION_1}]}
    )
    mixed = parse_llm_knowledge_references(_llm_data())

    assert collection_only.has_references is True
    assert collection_only.collection_references[0].safe_label is None
    assert mixed.direct_kb_ids == (UUID(KB_1),)
    assert mixed.collection_ids == (UUID(COLLECTION_1),)


@pytest.mark.parametrize("key", ["knowledgeBases", "knowledgeCollections"])
@pytest.mark.parametrize("value", [None, "bad", {}, 1, True])
def test_reference_lists_must_be_explicit_lists(key, value):
    _assert_error(
        {key: value},
        "knowledge_reference_list_invalid",
        f"data.{key}",
    )


@pytest.mark.parametrize("key", ["knowledgeBases", "knowledgeCollections"])
@pytest.mark.parametrize("value", [None, "bad", 1, True])
def test_reference_items_must_be_objects(key, value):
    _assert_error(
        {key: [value]},
        "knowledge_reference_item_invalid",
        f"data.{key}[0]",
    )


def test_direct_and_collection_item_shapes_are_strict():
    _assert_error(
        {"knowledgeBases": [{"id": KB_1, "name": "ok", "extra": True}]},
        "knowledge_reference_item_invalid",
        "data.knowledgeBases[0]",
    )
    _assert_error(
        {"knowledgeCollections": [{"safeLabel": "missing id"}]},
        "knowledge_reference_item_invalid",
        "data.knowledgeCollections[0]",
    )
    _assert_error(
        {"knowledgeCollections": [{"id": COLLECTION_1, "name": "raw"}]},
        "knowledge_reference_item_invalid",
        "data.knowledgeCollections[0]",
    )


@pytest.mark.parametrize(
    "value",
    [
        "",
        "not-a-uuid",
        "00000000000000000000000000000001",
        ALPHA_UUID.upper(),
        "{00000000-0000-0000-0000-000000000001}",
        1,
        True,
    ],
)
def test_reference_ids_must_be_canonical_uuid_strings(value):
    _assert_error(
        {"knowledgeBases": [{"id": value, "name": "safe"}]},
        "knowledge_reference_id_invalid",
        "data.knowledgeBases[0].id",
    )


@pytest.mark.parametrize("field,value", [("name", None), ("name", 1)])
def test_direct_display_name_is_required_string(field, value):
    _assert_error(
        {"knowledgeBases": [{"id": KB_1, field: value}]},
        "knowledge_reference_display_invalid",
        "data.knowledgeBases[0].name",
    )


@pytest.mark.parametrize("display", ["line\nbreak", "tab\tvalue", "x\x00y"])
def test_display_snapshots_reject_control_characters(display):
    _assert_error(
        {"knowledgeCollections": [{"id": COLLECTION_1, "safeLabel": display}]},
        "knowledge_reference_display_invalid",
        "data.knowledgeCollections[0].safeLabel",
    )


def test_display_length_boundary_is_strict():
    parse_llm_knowledge_references(
        {"knowledgeCollections": [{"id": COLLECTION_1, "safeLabel": "가" * 255}]}
    )
    _assert_error(
        {"knowledgeCollections": [{"id": COLLECTION_1, "safeLabel": "가" * 256}]},
        "knowledge_reference_display_invalid",
        "data.knowledgeCollections[0].safeLabel",
    )


@pytest.mark.parametrize("key", ["knowledgeBases", "knowledgeCollections"])
def test_each_reference_list_has_an_independent_twenty_item_limit(key):
    if key == "knowledgeBases":
        item = {"id": KB_1, "name": "safe"}
    else:
        item = {"id": COLLECTION_1}
    parse_llm_knowledge_references({key: [item] * 20})
    _assert_error(
        {key: [item] * 21},
        "knowledge_reference_limit_exceeded",
        f"data.{key}",
    )


def test_duplicates_remain_configured_but_runtime_ids_are_first_deduplicated():
    parsed = parse_llm_knowledge_references(
        {
            "knowledgeBases": [
                {"id": KB_1, "name": "first"},
                {"id": KB_2, "name": "second"},
                {"id": KB_1, "name": "duplicate"},
            ]
        }
    )

    assert len(parsed.direct_references) == 3
    assert parsed.direct_kb_ids == (UUID(KB_1), UUID(KB_2))


def test_root_and_nested_loop_graphs_are_validated_and_aggregated():
    graph = {
        "nodes": [
            {"id": "root", "type": "llmNode", "data": _llm_data()},
            {
                "id": "loop",
                "type": "loopNode",
                "data": {
                    "subGraph": {
                        "nodes": [
                            {
                                "id": "nested",
                                "type": "llmNode",
                                "data": {
                                    "knowledgeBases": [
                                        {"id": KB_2, "name": "nested"},
                                        {"id": KB_1, "name": "duplicate"},
                                    ]
                                },
                            }
                        ]
                    }
                },
            },
        ]
    }

    parsed = parse_workflow_knowledge_references(graph)
    direct, collections = aggregate_workflow_knowledge_reference_ids(parsed)

    assert len(parsed) == 2
    assert direct == (UUID(KB_1), UUID(KB_2))
    assert collections == (UUID(COLLECTION_1),)


def test_nested_error_uses_index_path_without_echoing_node_identity():
    graph = {
        "nodes": [
            {
                "id": "secret-node-id",
                "type": "loopNode",
                "data": {
                    "subGraph": {
                        "nodes": [
                            {
                                "id": "hidden-llm-id",
                                "type": "llmNode",
                                "data": {"knowledgeCollections": None},
                            }
                        ]
                    }
                },
            }
        ]
    }

    with pytest.raises(WorkflowKnowledgeReferenceError) as error:
        parse_workflow_knowledge_references(graph)

    assert error.value.field_path == (
        "graph.nodes[0].data.subGraph.nodes[0].data.knowledgeCollections"
    )
    assert "secret-node-id" not in str(error.value)
