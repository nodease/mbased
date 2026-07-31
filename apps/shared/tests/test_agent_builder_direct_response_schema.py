from uuid import uuid4

import pytest
from apps.shared.schemas.agent_builder import (
    AgentBuilderDirectMessageResponse,
    AgentBuilderMessageResponse,
    AgentBuilderPendingResolution,
    AgentBuilderStructuredRequest,
)
from pydantic import ValidationError


def test_direct_response_moves_kb_candidates_out_of_clarification_options() -> None:
    response = AgentBuilderMessageResponse(
        request_id=uuid4(),
        status="clarification_required",
        structured_request=AgentBuilderStructuredRequest(
            request_type="new_workflow",
            draft_mode="new_workflow",
            intent_summary="Knowledge workflow",
            knowledge_requirements=[
                {
                    "requirement_id": "knowledge-1",
                    "purpose": "internal documents",
                }
            ],
        ),
        clarification_options=[
            {
                "candidate_id": "kb-1",
                "resolution_id": "resolution-1",
                "safe_label": "Internal docs",
            },
            {
                "type": "target_node",
                "node_id": "node-1",
                "label": "Existing node",
            },
        ],
    )

    direct = AgentBuilderDirectMessageResponse.from_internal(response)

    assert direct.knowledge_resolution is not None
    assert [
        item.candidate_id for item in direct.knowledge_resolution.candidates
    ] == ["kb-1"]
    assert direct.clarification_options == [
        {
            "type": "target_node",
            "node_id": "node-1",
            "label": "Existing node",
        }
    ]


def test_direct_response_keeps_resolution_id_when_no_kb_candidates_exist() -> None:
    response = AgentBuilderMessageResponse(
        request_id=uuid4(),
        status="clarification_required",
        structured_request=AgentBuilderStructuredRequest(
            request_type="new_workflow",
            draft_mode="new_workflow",
            intent_summary="Knowledge workflow without eligible candidates",
            knowledge_requirements=[
                {
                    "requirement_id": "knowledge-1",
                    "purpose": "internal documents",
                    "target_step_ref": "step-llm",
                }
            ],
            pending_resolution=[
                AgentBuilderPendingResolution(
                    resolution_id="resolution-empty",
                    slot_type="knowledge_base",
                    slot_key="knowledge-1",
                    target_step_ref="step-llm",
                )
            ],
        ),
        clarification_options=[],
    )

    direct = AgentBuilderDirectMessageResponse.from_internal(response)

    assert direct.knowledge_resolution is not None
    assert direct.knowledge_resolution.model_dump(mode="json") == {
        "resolution_id": "resolution-empty",
        "requirement_id": None,
        "target_node_id": None,
        "timing": "after_graph",
        "required": True,
        "candidates": [],
        "collections": [],
        "ungrouped_kbs": [],
        "selected": [],
        "selected_collection_handles": [],
        "selected_kb_handles": [],
        "selection_status": None,
    }


def test_direct_response_exposes_only_the_safe_knowledge_target_node_id() -> None:
    response = AgentBuilderMessageResponse(
        request_id=uuid4(),
        status="clarification_required",
        structured_request=AgentBuilderStructuredRequest.model_validate(
            {
                "request_type": "new_workflow",
                "draft_mode": "new_workflow",
                "intent_summary": "Knowledge workflow",
                "knowledge_requirements": [
                    {
                        "requirement_id": "knowledge-1",
                        "purpose": "internal documents",
                        "target_step_ref": "step-llm",
                    }
                ],
                "knowledge_placements": [
                    {
                        "requirement_id": "knowledge-1",
                        "timing": "after_graph",
                        "effect_kind": "binding_only",
                        "target_step_id": "step-llm",
                    }
                ],
            }
        ),
        safe_step_node_ids={"step-llm": "llm-node-safe"},
    )

    direct = AgentBuilderDirectMessageResponse.from_internal(response)

    assert direct.knowledge_resolution is not None
    assert direct.knowledge_resolution.target_node_id == "llm-node-safe"


def test_direct_response_rejects_unknown_knowledge_resolution_fields() -> None:
    with pytest.raises(ValidationError):
        AgentBuilderDirectMessageResponse(
            request_id=uuid4(),
            status="clarification_required",
            knowledge_resolution={
                "resolution_id": "resolution-typed",
                "timing": "after_graph",
                "required": True,
                "candidates": [],
                "collections": [],
                "ungrouped_kbs": [],
                "selected": [],
                "raw_internal_payload": {"must": "not leak"},
            },
        )


def test_direct_response_accepts_collection_and_kb_selection_references() -> None:
    response = AgentBuilderDirectMessageResponse(
        request_id=uuid4(),
        status="parameter_configuration",
        knowledge_resolution={
            "resolution_id": "resolution-hierarchy",
            "timing": "after_graph",
            "required": True,
            "candidates": [],
            "collections": [],
            "ungrouped_kbs": [],
            "selected": [
                {
                    "selection_type": "collection",
                    "collection_handle": "collection-safe-1",
                    "safe_label": "Internal documents",
                },
                {
                    "selection_type": "knowledge_base",
                    "candidate_id": "kb-safe-1",
                    "kb_handle": "kb-safe-1",
                    "safe_label": "Policies",
                },
            ],
        },
    )

    assert response.knowledge_resolution is not None
    assert response.knowledge_resolution.selected[0].collection_handle == (
        "collection-safe-1"
    )
    assert response.knowledge_resolution.selected[1].kb_handle == "kb-safe-1"
