import pytest

from apps.gateway.application.agent_builder.parameter_suggestions import (
    ParameterSuggestionError,
    ParameterSuggestionResolver,
)


def _node(node_id, node_type, data=None):
    return {
        "id": node_id,
        "type": node_type,
        "position": {"x": 0, "y": 0},
        "data": {"title": node_id, **(data or {})},
    }


def test_resolver_only_suggests_reachable_catalog_runtime_outputs():
    graph = {
        "nodes": [
            _node(
                "webhook",
                "webhookTrigger",
                {
                    "variable_mappings": [
                        {"variable_name": "payload", "json_path": "$"}
                    ]
                },
            ),
            _node("answer", "answerNode"),
            _node("unrelated", "llmNode"),
        ],
        "edges": [{"id": "e1", "source": "webhook", "target": "answer"}],
    }

    suggestions = ParameterSuggestionResolver().resolve(
        graph=graph,
        target_node_id="answer",
        parameter_key="outputs",
    )

    assert [(item.source_node_id, item.output_key) for item in suggestions] == [
        ("webhook", "payload")
    ]
    assert suggestions[0].value_selector == ["webhook", "payload"]
    assert suggestions[0].json_path == "$"
    assert "unrelated" not in {item.source_node_id for item in suggestions}


def test_resolver_uses_dynamic_file_and_variable_extraction_output_names():
    graph = {
        "nodes": [
            _node(
                "file",
                "fileExtractionNode",
                {
                    "referenced_variables": [
                        {"name": "document_text", "value_selector": ["input", "file"]}
                    ]
                },
            ),
            _node(
                "extract",
                "variableExtractionNode",
                {
                    "mappings": [
                        {"name": "author", "json_path": "user.name"}
                    ]
                },
            ),
            _node("answer", "answerNode"),
        ],
        "edges": [
            {"id": "e1", "source": "file", "target": "extract"},
            {"id": "e2", "source": "extract", "target": "answer"},
        ],
    }

    suggestions = ParameterSuggestionResolver().resolve(
        graph=graph,
        target_node_id="answer",
        parameter_key="outputs",
    )

    assert {(item.source_node_id, item.output_key) for item in suggestions} == {
        ("file", "document_text"),
        ("extract", "author"),
    }


def test_server_rejects_tampered_suggestion_selector():
    graph = {
        "nodes": [
            _node("template", "templateNode"),
            _node("answer", "answerNode"),
        ],
        "edges": [{"id": "e1", "source": "template", "target": "answer"}],
    }
    resolver = ParameterSuggestionResolver()
    suggestion = resolver.resolve(
        graph=graph,
        target_node_id="answer",
        parameter_key="outputs",
    )[0]

    resolver.validate_selection(
        graph=graph,
        target_node_id="answer",
        parameter_key="outputs",
        suggestion_id=suggestion.suggestion_id,
        value_selector=suggestion.value_selector,
    )
    with pytest.raises(ParameterSuggestionError, match="mismatch"):
        resolver.validate_selection(
            graph=graph,
            target_node_id="answer",
            parameter_key="outputs",
            suggestion_id=suggestion.suggestion_id,
            value_selector=["template", "missing"],
        )
