from types import SimpleNamespace

from apps.workflow_engine.services.model_routing_effect_profile import (
    build_model_routing_effect_profiles,
)
from apps.workflow_engine.workflow.core.workflow_engine import WorkflowEngine


def _node(node_id: str, node_type: str, **data):
    return SimpleNamespace(id=node_id, type=node_type, data=data)


def test_effect_profile_captures_reachable_external_write_and_customer_output():
    nodes = {
        "llm": _node("llm", "llmNode"),
        "branch": _node("branch", "conditionNode", cases=[]),
        "request": _node(
            "request",
            "httpRequestNode",
            method="POST",
            url="https://example.test/refunds",
        ),
        "answer": _node("answer", "answerNode", outputs=[]),
    }
    profiles = build_model_routing_effect_profiles(
        nodes,
        {
            "llm": ["branch"],
            "branch": ["request", "answer"],
        },
    )

    assert profiles["llm"] == {
        "control_gate_present": True,
        "customer_output_reachable": True,
        "downstream_contract_required": True,
        "external_read_reachable": False,
        "external_write_reachable": True,
        "human_approval_required": False,
        "irreversible_effect_possible": True,
        "local_execution_reachable": False,
        "reachable_effect_count": 1,
    }


def test_effect_profile_treats_read_only_http_and_github_operations_as_reads():
    nodes = {
        "llm": _node("llm", "llmNode"),
        "http": _node(
            "http",
            "httpRequestNode",
            method="GET",
            url="https://example.test/policy",
        ),
        "github": _node("github", "githubNode", action="get_pr"),
    }
    profiles = build_model_routing_effect_profiles(
        nodes,
        {"llm": ["http"], "http": ["github"]},
    )

    assert profiles["llm"]["external_read_reachable"] is True
    assert profiles["llm"]["external_write_reachable"] is False
    assert profiles["llm"]["irreversible_effect_possible"] is False
    assert profiles["llm"]["reachable_effect_count"] == 0


def test_workflow_engine_injects_each_llm_nodes_effect_profile():
    engine = WorkflowEngine(
        graph={
            "nodes": [
                {
                    "id": "start",
                    "type": "startNode",
                    "position": {"x": 0, "y": 0},
                    "data": {"title": "입력"},
                },
                {
                    "id": "llm",
                    "type": "llmNode",
                    "position": {"x": 200, "y": 0},
                    "data": {
                        "title": "판단",
                        "model_id": "gpt-4.1-mini",
                        "user_prompt": "{{ request }}",
                    },
                },
                {
                    "id": "answer",
                    "type": "answerNode",
                    "position": {"x": 400, "y": 0},
                    "data": {"title": "응답", "outputs": []},
                },
            ],
            "edges": [
                {"id": "start-llm", "source": "start", "target": "llm"},
                {"id": "llm-answer", "source": "llm", "target": "answer"},
            ],
        }
    )

    profile = engine.node_instances["llm"].execution_context[
        "model_routing_effect_profile"
    ]

    assert profile["customer_output_reachable"] is True
    assert profile["downstream_contract_required"] is True
    assert "model_routing_effect_profile" not in engine.execution_context
