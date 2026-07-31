import pytest

from apps.workflow_engine.domain.external_effect import ExternalEffectError
from apps.workflow_engine.workflow.nodes.loop.loop_node import LoopNode, LoopNodeData


def test_loop_continue_does_not_turn_external_effect_error_into_output(monkeypatch):
    node = LoopNode(
        id="loop-1",
        data=LoopNodeData(
            title="Loop",
            loop_key="items",
            error_strategy="continue",
            subGraph={
                "nodes": [
                    {
                        "id": "body",
                        "type": "templateNode",
                        "position": {"x": 0, "y": 0},
                        "data": {},
                    }
                ],
                "edges": [],
            },
        ),
    )

    def fail(*args, **kwargs):
        raise ExternalEffectError(
            "external_effect.outcome_unknown",
            retryable=False,
            node_id="http-1",
        )

    monkeypatch.setattr(node, "_execute_subgraph_scoped", fail)

    with pytest.raises(ExternalEffectError) as captured:
        node.execute({"items": [1]})

    assert captured.value.code == "external_effect.outcome_unknown"
