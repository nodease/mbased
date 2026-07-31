from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from apps.gateway.application.agent_builder.service import DirectEditOrchestrator
from apps.gateway.application.agent_builder.graph_mutation_builder import (
    apply_graph_operations,
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


def _structured():
    return AgentBuilderStructuredRequest.model_validate(
        {
            "request_type": "new_workflow",
            "draft_mode": "new_workflow",
            "intent_summary": "입력을 LLM으로 처리해 응답",
            "planned_steps": [
                {
                    "step_id": "step_input",
                    "capability": "start_input",
                    "purpose": "입력",
                },
                {
                    "step_id": "step_llm",
                    "capability": "llm",
                    "purpose": "처리",
                    "depends_on": ["step_input"],
                },
                {
                    "step_id": "step_answer",
                    "capability": "answer",
                    "purpose": "응답",
                    "depends_on": ["step_llm"],
                },
            ],
            "required_capabilities": ["start_input", "llm", "answer"],
        }
    )


def _candidate():
    return {
        "nodes": [
            _node("start", "startNode"),
            _node("llm", "llmNode"),
            _node("answer", "answerNode"),
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "llm"},
            {"id": "e2", "source": "llm", "target": "answer"},
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }


def test_configure_and_generate_issues_initial_mutation_and_parameter_group():
    workflow = SimpleNamespace(
        id=uuid4(),
        graph={"nodes": [], "edges": [], "viewport": {"x": 0, "y": 0, "zoom": 1}},
        updated_at=datetime.now(timezone.utc),
    )

    result = DirectEditOrchestrator().issue(
        workflow=workflow,
        structured_request=_structured(),
        candidate_graph=_candidate(),
        generation_mode="configure_and_generate",
    )

    assert result.mutation.kind == "initial_graph"
    assert result.mutation.generation_mode == "configure_and_generate"
    assert result.parameter_group is not None
    assert result.parameter_group.status == "pending_save"
    assert result.parameter_group.tasks
    assert all(task.status != "active" for task in result.parameter_group.tasks)
    assert any(task.status == "completed" for task in result.parameter_group.tasks)
    assert any(task.status == "pending" for task in result.parameter_group.tasks)
    persisted = apply_graph_operations(workflow.graph, result.mutation.operations)
    assert persisted["nodes"]
    assert result.mutation.expected_result_graph_hash


def test_direct_edit_does_not_create_a_generic_knowledge_base_parameter_task():
    workflow = SimpleNamespace(
        id=uuid4(),
        graph={"nodes": [], "edges": [], "viewport": {"x": 0, "y": 0, "zoom": 1}},
        updated_at=datetime.now(timezone.utc),
    )

    result = DirectEditOrchestrator().issue(
        workflow=workflow,
        structured_request=_structured(),
        candidate_graph=_candidate(),
        generation_mode="configure_and_generate",
    )

    assert result.parameter_group is not None
    assert all(
        not (task.node_type == "llmNode" and task.parameter_key == "knowledgeBases")
        for task in result.parameter_group.tasks
    )


def test_reissued_plan_keeps_operations_hash_and_parameter_task_ids_deterministic():
    workflow = SimpleNamespace(
        id=uuid4(),
        graph={"nodes": [], "edges": [], "viewport": {"x": 0, "y": 0, "zoom": 1}},
        updated_at=datetime.now(timezone.utc),
    )

    first = DirectEditOrchestrator().issue(
        workflow=workflow,
        structured_request=_structured(),
        candidate_graph=_candidate(),
        generation_mode="configure_and_generate",
    )
    second = DirectEditOrchestrator().issue(
        workflow=workflow,
        structured_request=_structured(),
        candidate_graph=_candidate(),
        generation_mode="configure_and_generate",
    )

    assert first.mutation.operation_id != second.mutation.operation_id
    assert first.mutation.expected_result_graph_hash == (
        second.mutation.expected_result_graph_hash
    )
    assert [item.model_dump(mode="json") for item in first.mutation.operations] == [
        item.model_dump(mode="json") for item in second.mutation.operations
    ]
    assert first.parameter_group is not None
    assert second.parameter_group is not None
    assert first.parameter_group.group_id == second.parameter_group.group_id
    assert [task.task_id for task in first.parameter_group.tasks] == [
        task.task_id for task in second.parameter_group.tasks
    ]


def test_structure_only_issues_the_same_graph_without_parameter_group():
    workflow = SimpleNamespace(
        id=uuid4(),
        graph={"nodes": [], "edges": [], "viewport": {"x": 0, "y": 0, "zoom": 1}},
        updated_at=datetime.now(timezone.utc),
    )

    result = DirectEditOrchestrator().issue(
        workflow=workflow,
        structured_request=_structured(),
        candidate_graph=_candidate(),
        generation_mode="structure_only",
    )

    assert result.mutation.generation_mode == "structure_only"
    assert result.parameter_group is None
    assert apply_graph_operations(workflow.graph, result.mutation.operations)["nodes"]


def test_graph_edit_preserves_existing_node_data_and_only_adds_new_structure():
    existing = _node("start", "startNode", {"private_existing_value": "preserved"})
    workflow = SimpleNamespace(
        id=uuid4(),
        graph={"nodes": [existing], "edges": [], "viewport": {"x": 0, "y": 0, "zoom": 1}},
        updated_at=datetime.now(timezone.utc),
    )
    candidate = {
        "nodes": [existing, _node("answer", "answerNode")],
        "edges": [{"id": "e1", "source": "start", "target": "answer"}],
        "viewport": workflow.graph["viewport"],
    }
    structured = _structured().model_copy(
        update={"request_type": "modify_workflow", "draft_mode": "modify_workflow"}
    )

    result = DirectEditOrchestrator().issue(
        workflow=workflow,
        structured_request=structured,
        candidate_graph=candidate,
        generation_mode="structure_only",
    )
    graph = apply_graph_operations(workflow.graph, result.mutation.operations)

    assert result.mutation.kind == "graph_edit"
    assert graph["nodes"][0]["data"]["private_existing_value"] == "preserved"


def test_new_workflow_replaces_existing_graph_as_one_typed_mutation():
    existing = {
        "nodes": [
            _node("old-start", "startNode"),
            _node("old-answer", "answerNode"),
        ],
        "edges": [
            {
                "id": "old-edge",
                "source": "old-start",
                "target": "old-answer",
            }
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }
    workflow = SimpleNamespace(
        id=uuid4(),
        graph=existing,
        updated_at=datetime.now(timezone.utc),
    )

    result = DirectEditOrchestrator().issue(
        workflow=workflow,
        structured_request=_structured(),
        candidate_graph=_candidate(),
        generation_mode="configure_and_generate",
    )
    graph = apply_graph_operations(existing, result.mutation.operations)

    assert result.mutation.kind == "replace_workflow"
    assert result.parameter_group is not None
    assert result.parameter_group.status == "pending_save"
    assert {operation.op for operation in result.mutation.operations} == {
        "remove_edge",
        "remove_node",
        "add_node",
        "add_edge",
    }
    assert {node["id"] for node in graph["nodes"]} == {
        "start",
        "llm",
        "answer",
    }
    assert {edge["id"] for edge in graph["edges"]} == {"e1", "e2"}


def test_new_input_answer_workflow_replaces_a_single_existing_input_node():
    existing = {
        "nodes": [_node("old-input", "startNode")],
        "edges": [],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }
    workflow = SimpleNamespace(
        id=uuid4(),
        graph=existing,
        updated_at=datetime.now(timezone.utc),
    )
    structured = AgentBuilderStructuredRequest.model_validate(
        {
            "request_type": "new_workflow",
            "draft_mode": "new_workflow",
            "intent_summary": "입력과 응답 노드를 생성합니다.",
            "planned_steps": [
                {
                    "step_id": "step_input",
                    "capability": "start_input",
                    "purpose": "입력",
                },
                {
                    "step_id": "step_answer",
                    "capability": "answer",
                    "purpose": "응답",
                    "depends_on": ["step_input"],
                },
            ],
            "required_capabilities": ["start_input", "answer"],
        }
    )
    candidate = {
        "nodes": [
            _node("new-input", "startNode"),
            _node("new-answer", "answerNode"),
        ],
        "edges": [
            {"id": "new-edge", "source": "new-input", "target": "new-answer"}
        ],
        "viewport": existing["viewport"],
    }

    result = DirectEditOrchestrator().issue(
        workflow=workflow,
        structured_request=structured,
        candidate_graph=candidate,
        generation_mode="configure_and_generate",
    )
    graph = apply_graph_operations(existing, result.mutation.operations)

    assert result.mutation.kind == "replace_workflow"
    assert {node["id"] for node in graph["nodes"]} == {
        "new-input",
        "new-answer",
    }
    assert [
        (edge["source"], edge["target"])
        for edge in graph["edges"]
    ] == [("new-input", "new-answer")]


def test_explicit_safe_parameter_value_is_materialized_from_structured_request():
    workflow = SimpleNamespace(
        id=uuid4(),
        graph={"nodes": [], "edges": [], "viewport": {"x": 0, "y": 0, "zoom": 1}},
        updated_at=datetime.now(timezone.utc),
    )
    structured = AgentBuilderStructuredRequest.model_validate(
        {
            "request_type": "new_workflow",
            "draft_mode": "new_workflow",
            "intent_summary": "입력을 Slack 채널 C123으로 보냅니다.",
            "planned_steps": [
                {
                    "step_id": "step_input",
                    "capability": "start_input",
                    "purpose": "입력",
                },
                {
                    "step_id": "step_slack",
                    "capability": "slack_send",
                    "purpose": "Slack 전송",
                    "depends_on": ["step_input"],
                },
                {
                    "step_id": "step_answer",
                    "capability": "answer",
                    "purpose": "응답",
                    "depends_on": ["step_slack"],
                },
            ],
            "explicit_parameter_values": [
                {
                    "step_id": "step_slack",
                    "parameter_key": "channel",
                    "value": "C123",
                }
            ],
            "required_capabilities": ["start_input", "slack_send", "answer"],
        }
    )
    candidate = {
        "nodes": [
            _node("start", "startNode"),
            _node("slack", "slackPostNode"),
            _node("answer", "answerNode"),
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "slack"},
            {"id": "e2", "source": "slack", "target": "answer"},
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }

    result = DirectEditOrchestrator().issue(
        workflow=workflow,
        structured_request=structured,
        candidate_graph=candidate,
        generation_mode="configure_and_generate",
    )

    graph = apply_graph_operations(workflow.graph, result.mutation.operations)
    slack_data = next(node["data"] for node in graph["nodes"] if node["id"] == "slack")
    channel_task = next(
        task
        for task in result.parameter_group.tasks
        if task.parameter_key == "channel"
    )
    assert slack_data["channel"] == "C123"
    assert channel_task.status == "completed"
    assert channel_task.resolution_source == "user_request"


def test_knowledge_placement_owns_kb_setup_without_duplicate_parameter_task():
    workflow = SimpleNamespace(
        id=uuid4(),
        graph={"nodes": [], "edges": [], "viewport": {"x": 0, "y": 0, "zoom": 1}},
        updated_at=datetime.now(timezone.utc),
    )
    structured = _structured().model_copy(
        update={
            "knowledge_placements": [
                AgentBuilderKnowledgePlacement(
                    requirement_id="kr-1",
                    timing="after_graph",
                    effect_kind="binding_only",
                    target_step_id="step_llm",
                )
            ]
        }
    )

    result = DirectEditOrchestrator().issue(
        workflow=workflow,
        structured_request=structured,
        candidate_graph=_candidate(),
        generation_mode="configure_and_generate",
    )

    assert result.parameter_group is not None
    assert all(
        not (
            task.step_id == "step_llm"
            and task.parameter_key == "knowledgeBases"
        )
        for task in result.parameter_group.tasks
    )
