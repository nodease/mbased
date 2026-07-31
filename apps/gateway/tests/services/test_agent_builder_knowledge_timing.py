from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from apps.gateway.application.agent_builder.knowledge_timing import (
    KnowledgeTimingResolver,
    materialize_before_graph_plan,
)
from apps.gateway.application.agent_builder.graph_mutation_builder import (
    apply_graph_operations,
    validate_candidate_graph,
)
from apps.shared.schemas.agent_builder import (
    AgentBuilderKnowledgePlacement,
    AgentBuilderStructuredRequest,
)


def _node(node_id, node_type, data=None):
    return {
        "id": node_id,
        "type": node_type,
        "position": {"x": 0, "y": 0},
        "data": {"title": node_id, **(data or {})},
    }


def test_knowledge_placement_requires_typed_references_for_timing():
    before = AgentBuilderKnowledgePlacement(
        requirement_id="kr-1",
        timing="before_graph",
        effect_kind="insert_step",
        target_step_id="step-llm",
        knowledge_step_id="step-knowledge",
        upstream_step_id="step-input",
        downstream_step_id="step-answer",
        empty_selection_bridge="connect_upstream_to_downstream",
    )
    after = AgentBuilderKnowledgePlacement(
        requirement_id="kr-1",
        timing="after_graph",
        effect_kind="binding_only",
        target_step_id="step-llm",
    )
    assert before.timing == "before_graph"
    assert after.timing == "after_graph"

    with pytest.raises(ValidationError):
        AgentBuilderKnowledgePlacement(
            requirement_id="kr-1",
            timing="before_graph",
            effect_kind="binding_only",
            target_step_id="step-llm",
        )


@pytest.mark.parametrize(
    "selected_knowledge_bases",
    [
        [],
        [{"id": "kb-1", "name": "Human Resources"}],
        [
            {"id": "kb-1", "name": "Human Resources"},
            {"id": "kb-2", "name": "Finance"},
        ],
    ],
)
def test_after_graph_binding_uses_complete_knowledge_graph_references(
    selected_knowledge_bases,
):
    workflow_id = uuid4()
    graph = {
        "nodes": [_node("llm", "llmNode", {"model_id": "model"})],
        "edges": [],
    }
    mutation = KnowledgeTimingResolver().build_after_graph_mutation(
        operation_id=uuid4(),
        workflow_id=workflow_id,
        graph=graph,
        workflow_updated_at=datetime.now(timezone.utc),
        target_node_id="llm",
        selected_knowledge_bases=selected_knowledge_bases,
        resolution_id=uuid4(),
    )

    operation = mutation.operations[0]
    assert operation.op == "replace_node_data"
    assert operation.data["knowledgeBases"] == selected_knowledge_bases
    assert mutation.kind == "knowledge_binding"


def test_after_graph_binding_materializes_a_graph_valid_knowledge_reference():
    workflow_id = uuid4()
    knowledge_base_id = str(uuid4())
    graph = {
        "nodes": [_node("llm", "llmNode", {"model_id": "model"})],
        "edges": [],
    }
    mutation = KnowledgeTimingResolver().build_after_graph_mutation(
        operation_id=uuid4(),
        workflow_id=workflow_id,
        graph=graph,
        workflow_updated_at=datetime.now(timezone.utc),
        target_node_id="llm",
        selected_knowledge_bases=[
            {"id": knowledge_base_id, "name": "Human Resources"}
        ],
        resolution_id=uuid4(),
    )

    candidate_graph = apply_graph_operations(graph, mutation.operations)

    validate_candidate_graph(candidate_graph)


def test_after_graph_binding_stores_collections_and_direct_kbs_separately():
    workflow_id = uuid4()
    knowledge_base_id = str(uuid4())
    collection_id = str(uuid4())
    graph = {
        "nodes": [_node("llm", "llmNode", {"model_id": "model"})],
        "edges": [],
    }

    mutation = KnowledgeTimingResolver().build_after_graph_mutation(
        operation_id=uuid4(),
        workflow_id=workflow_id,
        graph=graph,
        workflow_updated_at=datetime.now(timezone.utc),
        target_node_id="llm",
        selected_knowledge_bases=[
            {"id": knowledge_base_id, "name": "Human Resources"}
        ],
        selected_knowledge_collections=[
            {"id": collection_id, "name": "Company Policies"}
        ],
        resolution_id=uuid4(),
    )

    candidate_graph = apply_graph_operations(graph, mutation.operations)
    llm_data = candidate_graph["nodes"][0]["data"]

    assert llm_data["knowledgeBases"] == [
        {"id": knowledge_base_id, "name": "Human Resources"}
    ]
    assert llm_data["knowledgeCollections"] == [
        {"id": collection_id, "safeLabel": "Company Policies"}
    ]
    validate_candidate_graph(candidate_graph)


def test_before_graph_empty_selection_omits_knowledge_step_and_bridges_dependencies():
    structured = AgentBuilderStructuredRequest.model_validate(
        {
            "request_type": "new_workflow",
            "draft_mode": "new_workflow",
            "intent_summary": "사내 문서 답변",
            "planned_steps": [
                {
                    "step_id": "step_input",
                    "capability": "webhook_trigger",
                    "purpose": "요청 수신",
                },
                {
                    "step_id": "step_knowledge_llm",
                    "capability": "knowledge_backed_llm",
                    "purpose": "사내 문서 답변",
                    "depends_on": ["step_input"],
                },
                {
                    "step_id": "step_slack",
                    "capability": "slack_send",
                    "purpose": "결과 전송",
                    "depends_on": ["step_knowledge_llm"],
                },
            ],
            "required_capabilities": [
                "webhook_trigger",
                "knowledge_backed_llm",
                "knowledge_base",
                "slack_send",
            ],
        }
    )
    placement = AgentBuilderKnowledgePlacement(
        requirement_id="kr-1",
        timing="before_graph",
        effect_kind="insert_step",
        target_step_id="step_knowledge_llm",
        knowledge_step_id="step_knowledge_llm",
        upstream_step_id="step_input",
        downstream_step_id="step_slack",
        empty_selection_bridge="connect_upstream_to_downstream",
    )

    empty = materialize_before_graph_plan(
        structured,
        placement=placement,
        has_selection=False,
    )
    selected = materialize_before_graph_plan(
        structured,
        placement=placement,
        has_selection=True,
    )

    assert [step.step_id for step in empty.planned_steps] == [
        "step_input",
        "step_slack",
    ]
    assert empty.planned_steps[-1].depends_on == ["step_input"]
    assert "knowledge_backed_llm" not in empty.required_capabilities
    assert [step.step_id for step in selected.planned_steps] == [
        "step_input",
        "step_knowledge_llm",
        "step_slack",
    ]


@pytest.mark.parametrize(
    "updates",
    [
        {"upstream_step_id": "step_slack"},
        {"downstream_step_id": "step_input"},
        {"upstream_step_id": "step_knowledge_llm"},
    ],
)
def test_before_graph_placement_rejects_references_that_do_not_match_dependencies(updates):
    structured = AgentBuilderStructuredRequest.model_validate(
        {
            "request_type": "new_workflow",
            "draft_mode": "new_workflow",
            "intent_summary": "사내 문서 답변",
            "planned_steps": [
                {"step_id": "step_input", "capability": "webhook_trigger", "purpose": "입력"},
                {
                    "step_id": "step_knowledge_llm",
                    "capability": "knowledge_backed_llm",
                    "purpose": "답변",
                    "depends_on": ["step_input"],
                },
                {
                    "step_id": "step_slack",
                    "capability": "slack_send",
                    "purpose": "전송",
                    "depends_on": ["step_knowledge_llm"],
                },
            ],
            "required_capabilities": ["webhook_trigger", "knowledge_backed_llm", "slack_send"],
        }
    )
    values = {
        "requirement_id": "kr-1",
        "timing": "before_graph",
        "effect_kind": "insert_step",
        "target_step_id": "step_knowledge_llm",
        "knowledge_step_id": "step_knowledge_llm",
        "upstream_step_id": "step_input",
        "downstream_step_id": "step_slack",
        "empty_selection_bridge": "connect_upstream_to_downstream",
        **updates,
    }
    placement = AgentBuilderKnowledgePlacement(**values)

    with pytest.raises(ValueError, match="topology"):
        materialize_before_graph_plan(structured, placement=placement, has_selection=True)
