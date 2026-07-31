from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

import pytest

from apps.gateway.application.agent_builder.graph_mutation_builder import (
    materialize_candidate_graph,
)
from apps.shared.services.workflow_node_catalog import (
    apply_node_parameter_value,
    node_parameter_definitions,
)
from apps.workflow_engine.workflow.nodes.answer.entities import AnswerNodeData
from apps.workflow_engine.workflow.nodes.code.entities import CodeNodeData
from apps.workflow_engine.workflow.nodes.condition.entities import ConditionNodeData
from apps.workflow_engine.workflow.nodes.file_extraction.entities import (
    FileExtractionNodeData,
)
from apps.workflow_engine.workflow.nodes.github.entities import GithubNodeData
from apps.workflow_engine.workflow.nodes.http.entities import HttpRequestNodeData
from apps.workflow_engine.workflow.nodes.llm.entities import LLMNodeData
from apps.workflow_engine.workflow.nodes.mail.entities import (
    GmailDraftNodeData,
    MailAcknowledgeNodeData,
    MailNodeData,
)
from apps.workflow_engine.workflow.nodes.schedule.entities import ScheduleTriggerNodeData
from apps.workflow_engine.workflow.nodes.slack.entities import SlackPostNodeData
from apps.workflow_engine.workflow.nodes.start.start_node import StartNodeData
from apps.workflow_engine.workflow.nodes.template.entities import TemplateNodeData
from apps.workflow_engine.workflow.nodes.variable_extraction.entities import (
    VariableExtractionNodeData,
)
from apps.workflow_engine.workflow.nodes.webhook.entities import WebhookTriggerNodeData
from apps.workflow_engine.workflow.nodes.workflow.entities import WorkflowNodeData


RUNTIME_MODELS = {
    "startNode": StartNodeData,
    "webhookTrigger": WebhookTriggerNodeData,
    "scheduleTrigger": ScheduleTriggerNodeData,
    "llmNode": LLMNodeData,
    "workflowNode": WorkflowNodeData,
    "codeNode": CodeNodeData,
    "conditionNode": ConditionNodeData,
    "fileExtractionNode": FileExtractionNodeData,
    "variableExtractionNode": VariableExtractionNodeData,
    "answerNode": AnswerNodeData,
    "httpRequestNode": HttpRequestNodeData,
    "slackPostNode": SlackPostNodeData,
    "templateNode": TemplateNodeData,
    "githubNode": GithubNodeData,
    "mailNode": MailNodeData,
    "gmailDraftNode": GmailDraftNodeData,
    "mailAcknowledgeNode": MailAcknowledgeNodeData,
}


BASE_NODE_DATA = {
    "startNode": {"title": "Input", "variables": []},
    "webhookTrigger": {"title": "Webhook", "variable_mappings": []},
    "scheduleTrigger": {
        "title": "Schedule",
        "cron_expression": "0 * * * *",
        "timezone": "UTC",
    },
    "llmNode": {"title": "LLM", "model_id": "model", "knowledgeBases": []},
    "workflowNode": {"title": "Workflow", "workflowId": "workflow", "appId": "app"},
    "codeNode": {"title": "Code", "code": "def main(inputs):\n    return inputs"},
    "conditionNode": {"title": "Condition", "cases": []},
    "fileExtractionNode": {"title": "File extraction", "referenced_variables": []},
    "variableExtractionNode": {
        "title": "Variable extraction",
        "source_selector": ["source", "result"],
        "mappings": [{"name": "value", "json_path": "$.value"}],
    },
    "answerNode": {
        "title": "Answer",
        "outputs": [{"variable": "result", "value_selector": ["source", "result"]}],
    },
    "httpRequestNode": {"title": "HTTP", "url": "https://example.com"},
    "slackPostNode": {
        "title": "Slack",
        "slackMode": "api",
        "authConfig": {"token": "token"},
        "channel": "C123",
        "referenced_variables": [],
    },
    "templateNode": {"title": "Template", "template": "{{value}}"},
    "githubNode": {
        "title": "GitHub",
        "action": "get_pr",
        "api_token": "token",
        "repo_owner": "owner",
        "repo_name": "repo",
        "pr_number": "1",
        "referenced_variables": [],
    },
    "mailNode": {"title": "Mail", "credential_id": str(uuid4())},
    "gmailDraftNode": {
        "title": "Gmail draft",
        "credential_id": str(uuid4()),
        "processing_ref_selector": ["mail", "processing_ref"],
        "reply_body_selector": ["llm", "text"],
    },
    "mailAcknowledgeNode": {
        "title": "Mail acknowledgement",
        "processing_ref_selector": ["mail", "processing_ref"],
        "required_effect_ref_selectors": [["draft", "draft_ref"]],
    },
}


PARAMETER_SAMPLES = {
    "variables": [
        {
            "id": "question",
            "name": "question",
            "label": "Question",
            "type": "text",
            "required": True,
        }
    ],
    "variable_mappings": [{"variable_name": "payload", "json_path": "$"}],
    "cron_expression": "0 9 * * *",
    "timezone": "Asia/Seoul",
    "model_id": "model-2",
    "output_format_type": "json",
    "output_json_schema": {"type": "object"},
    "system_prompt": "Answer safely.",
    "user_prompt": "Summarize the input.",
    "assistant_prompt": "Use a concise format.",
    "citationDisplayMode": "basic",
    "auto_model_routing": True,
    "fallback_model_id": "model-fallback",
    "model_routing_refresh_every_runs": 25,
    "model_routing_validation_budget_usd": 4.5,
    "model_routing_max_cohorts": 8,
    "workflowId": "workflow-2",
    "appId": "app-2",
    "code": "def main(inputs):\n    return {'result': inputs}",
    "cases": [
        {
            "id": "case-1",
            "case_name": "Case 1",
            "conditions": [],
            "logical_operator": "and",
        }
    ],
    "referenced_variables": ["source", "file"],
    "source_selector": ["source", "result"],
    "mappings": [{"name": "value", "json_path": "$.value"}],
    "outputs": [{"variable": "result", "value_selector": ["source", "result"]}],
    "url": "https://example.com/path",
    "slackMode": "api",
    "bot_token": "token-2",
    "channel": "C456",
    "message": "{{result}}",
    "blocks": [{"type": "section", "text": {"type": "plain_text", "text": "ok"}}],
    "attachments": [{"text": "attachment"}],
    "thread_ts": "123.456",
    "username": "Nodease",
    "icon_emoji": ":robot_face:",
    "template": "Result: {{result}}",
    "action": "comment_pr",
    "api_token": "token-2",
    "repo_owner": "octo",
    "repo_name": "nodease",
    "pr_number": 15,
    "comment_body": "Review result",
    "credential_id": str(uuid4()),
    "keyword": "invoice",
    "sender": "sender@example.com",
    "subject": "Subject",
    "start_date": "2026-07-01",
    "end_date": "2026-07-16",
    "folder": "INBOX",
    "max_results": 20,
    "unread_only": True,
    "mark_as_read": True,
    "processing_mode": "durable",
    "processing_ref_selector": ["mail", "processing_ref"],
    "reply_body_selector": ["llm", "text"],
    "required_effect_ref_selectors": [["draft", "draft_ref"]],
}


@pytest.mark.parametrize("node_type", sorted(RUNTIME_MODELS))
def test_every_agent_builder_parameter_matches_runtime_node_schema(node_type):
    model = RUNTIME_MODELS[node_type]
    base_data = BASE_NODE_DATA[node_type]

    for parameter in node_parameter_definitions(node_type):
        parameter_key = str(parameter["key"])
        if not parameter["agent_builder_task"]:
            continue
        if node_type == "llmNode" and parameter_key == "knowledgeBases":
            # Direct-edit Knowledge selection owns this graph binding.
            continue
        sample = (
            [["source", "text"]]
            if node_type == "llmNode" and parameter_key == "referenced_variables"
            else PARAMETER_SAMPLES[parameter_key]
        )
        updated = apply_node_parameter_value(
            node_type,
            parameter_key,
            deepcopy(base_data),
            sample,
        )

        model.model_validate(updated)


def test_materialized_mail_acknowledgement_configuration_is_runtime_valid():
    graph = materialize_candidate_graph(
        {
            "nodes": [
                {
                    "id": "mail-ack",
                    "type": "mailAcknowledgeNode",
                    "position": {"x": 0, "y": 0},
                    "data": deepcopy(BASE_NODE_DATA["mailAcknowledgeNode"]),
                }
            ],
            "edges": [],
        }
    )

    data = graph["nodes"][0]["data"]
    parsed = MailAcknowledgeNodeData.model_validate(data)

    assert parsed.configuration_state == "resolved"
    with pytest.raises(ValueError):
        MailAcknowledgeNodeData.model_validate({**data, "unknown_field": "value"})
