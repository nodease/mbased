from apps.shared.services.workflow_node_catalog import capability_output_contract
from apps.workflow_engine.workflow.nodes.file_extraction import (
    FileExtractionNode,
    FileExtractionNodeData,
)
from apps.workflow_engine.workflow.nodes.variable_extraction import (
    VariableExtractionNode,
    VariableExtractionNodeData,
)
from apps.workflow_engine.workflow.nodes.webhook import (
    WebhookTriggerNode,
    WebhookTriggerNodeData,
)


def test_webhook_runtime_outputs_catalog_declared_mapping_names():
    node = WebhookTriggerNode(
        "webhook",
        WebhookTriggerNodeData(
            title="Webhook",
            variable_mappings=[
                {"variable_name": "pull_request_number", "json_path": "pull_request.number"}
            ],
        ),
    )

    result = node.execute({"pull_request": {"number": 42}})

    contract = capability_output_contract("webhook_trigger")
    assert contract is not None
    assert contract["parameter_key"] == "variable_mappings"
    assert contract["name_key"] == "variable_name"
    assert result == {"pull_request_number": 42}


def test_file_extraction_runtime_outputs_catalog_declared_variable_names(
    tmp_path, monkeypatch
):
    source = tmp_path / "source.md"
    source.write_text("source", encoding="utf-8")
    node = FileExtractionNode(
        "file",
        FileExtractionNodeData(
            title="File",
            referenced_variables=[
                {"name": "document_text", "value_selector": ["input", "path"]}
            ],
        ),
    )
    monkeypatch.setattr(node, "_extract_text_sync", lambda *_: "extracted")

    result = node.execute({"input": {"path": str(source)}})

    contract = capability_output_contract("file_extraction")
    assert contract is not None
    assert contract["parameter_key"] == "referenced_variables"
    assert result == {"document_text": "extracted"}


def test_variable_extraction_runtime_outputs_catalog_declared_mapping_names():
    node = VariableExtractionNode(
        "extract",
        VariableExtractionNodeData(
            title="Extract",
            source_selector=["input", "payload"],
            mappings=[{"name": "author", "json_path": "user.name"}],
        ),
    )

    result = node.execute({"input": {"payload": {"user": {"name": "Ada"}}}})

    contract = capability_output_contract("variable_extraction")
    assert contract is not None
    assert contract["parameter_key"] == "mappings"
    assert result == {"author": "Ada"}
