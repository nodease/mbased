from types import SimpleNamespace
from uuid import uuid4

from apps.workflow_engine.services.model_routing_bootstrap import (
    PersistedModelRoutingBootstrapStore,
    downstream_contract_from_graph,
    task_fingerprint,
)


def _node(**overrides):
    values = {
        "model_id": "gpt-4.1",
        "fallback_model_id": "gpt-4.1-mini",
        "auto_model_routing": True,
        "system_prompt": "고객 문의를 JSON으로 분류합니다.",
        "user_prompt": "{{message}}",
        "assistant_prompt": "",
        "referenced_variables": [
            {"name": "message", "value_selector": ["webhook", "message"]}
        ],
        "output_format": {
            "type": "json",
            "schema": {"type": "object", "required": ["severity"]},
        },
        "knowledgeBases": [],
        "knowledgeCollections": [],
        "topK": 3,
        "scoreThreshold": 0.5,
        "retrievedContextMaxChars": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_fingerprint_ignores_manual_model_and_routing_toggle():
    baseline = task_fingerprint(_node())

    assert task_fingerprint(
        _node(model_id="gpt-4o-mini", auto_model_routing=False)
    ) == baseline


def test_fingerprint_changes_when_prompt_rag_or_output_contract_changes():
    baseline = task_fingerprint(_node())

    assert task_fingerprint(_node(system_prompt="다른 작업")) != baseline
    assert task_fingerprint(_node(knowledgeBases=[{"id": "kb-1"}])) != baseline
    assert task_fingerprint(_node(output_format={"type": "text"})) != baseline


def test_fingerprint_changes_when_downstream_contract_changes():
    node = _node()

    assert task_fingerprint(
        node,
        downstream_contract={"consumers": [{"node_id": "answer"}]},
    ) != task_fingerprint(
        node,
        downstream_contract={"consumers": [{"node_id": "extract"}]},
    )


def test_bootstrap_policy_is_judge_first_only():
    bootstrap = SimpleNamespace(
        id=uuid4(),
        task_fingerprint="fingerprint",
        default_model_id="gpt-4.1",
        fallback_model_id="gpt-4.1-mini",
        generation_summary={
            "candidate_model_ids": ["gpt-4.1", "gpt-4.1-mini", "gpt-4o-mini"]
        },
    )

    policy = PersistedModelRoutingBootstrapStore.active_policy_for_bootstrap(bootstrap)

    assert policy["strategy_id"] == "judge_bootstrap_incremental_v1"
    assert "learning" not in policy
    assert policy["candidate_model_ids"] == [
        "gpt-4.1",
        "gpt-4.1-mini",
        "gpt-4o-mini",
    ]
    assert "difficulty_models" not in policy
    assert "decision_profiles" not in policy


def test_downstream_contract_contains_only_consumer_contract_fields():
    graph = {
        "nodes": [
            {"id": "llm", "type": "llmNode", "data": {}},
            {
                "id": "extract",
                "type": "variableExtractionNode",
                "data": {
                    "referenced_variables": [{"name": "severity"}],
                    "variableMappings": [{"source": "severity"}],
                    "output_format": {"type": "json"},
                },
            },
        ],
        "edges": [{"source": "llm", "target": "extract"}],
    }

    contract = downstream_contract_from_graph(graph, "llm")

    assert contract["consumers"][0]["node_id"] == "extract"
    assert contract["consumers"][0]["node_type"] == "variableExtractionNode"
