from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from uuid import uuid4
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from apps.gateway.application.agent_builder.parameter_tasks import (
    ParameterTaskConflict,
    ParameterTaskSafetyError,
    ParameterTaskPlanner,
    acknowledge_parameter_binding,
    acknowledge_task_decision,
    apply_local_task_decision,
    canonical_parameter_value_fingerprint,
    cancel_parameter_group,
    recommendation_matches_canonical_graph,
    recovery_affected_node_ids,
    prepare_task_decision,
    previous_reopenable_task_id,
    refresh_parameter_group_configuration,
    refresh_parameter_group_suggestions,
    reconcile_parameter_group_catalog_tasks,
    remove_direct_edit_external_credential_tasks,
    remove_direct_edit_knowledge_parameter_tasks,
    validate_direct_set_value,
)
from apps.gateway.application.agent_builder.graph_mutation_builder import (
    canonical_graph_hash,
)
from apps.shared.schemas.agent_builder import (
    AgentBuilderParameterCandidate,
    AgentBuilderParameterGroup,
    AgentBuilderParameterGroupCancelRequest,
    AgentBuilderParameterTask,
    AgentBuilderParameterTaskDecisionRequest,
)
from apps.gateway.services.agent_builder.parameter_task_service import (
    ParameterTaskService,
    apply_parameter_value_to_node_data,
)
from apps.gateway.services.agent_builder.mutation_lifecycle import (
    _refresh_parameter_group,
)
from apps.gateway.services.agent_builder.parameter_candidates import (
    ParameterCandidateProvider,
)
from apps.gateway.services.agent_builder import parameter_candidates as candidate_module
from apps.gateway.services.agent_builder import parameter_task_service as task_service_module
from apps.gateway.services.llm_service import LLMService
from apps.gateway.services.workflow_service import WorkflowService
from apps.gateway.adapters.db.agent_builder_repository import (
    AgentBuilderRepository,
    AgentBuilderRepositoryError,
)
from apps.shared.schemas.agent_builder import AgentBuilderParameterGuidanceHint
from apps.workflow_engine.workflow.nodes.file_extraction.entities import (
    FileExtractionNodeData,
)
from apps.workflow_engine.workflow.nodes.github.entities import GithubNodeData


def _node(node_id, node_type, data=None):
    return {
        "id": node_id,
        "type": node_type,
        "position": {"x": 0, "y": 0},
        "data": {"title": node_id, **(data or {})},
    }


def test_planner_includes_mode_relevant_slack_secret_field_in_agent_builder_tasks():
    graph = {
        "nodes": [
            _node(
                "slack",
                "slackPostNode",
                {"credential": "cred-existing", "slackMode": "api"},
            )
        ],
        "edges": [],
    }
    planner = ParameterTaskPlanner()

    result = planner.plan(
        graph=graph,
        step_node_ids={"step_slack": "slack"},
        explicit_values={("step_slack", "channel"): "C123"},
        upstream_candidates={},
        step_purposes={"step_slack": "Slack으로 메시지를 전송합니다."},
        guidance_hints=[
            AgentBuilderParameterGuidanceHint(
                step_id="step_slack",
                parameter_key="channel",
                reason="메시지 목적지가 필요합니다.",
                input_guidance="Slack channel ID를 선택하세요.",
            )
        ],
    )

    assert [task.parameter_key for task in result.tasks] == [
        "slackMode",
        "bot_token",
        "url",
        "channel",
        "message",
        "blocks",
        "attachments",
        "thread_ts",
        "username",
        "icon_emoji",
    ]
    channel_task = next(task for task in result.tasks if task.parameter_key == "channel")
    assert (channel_task.status, channel_task.resolution_source) == (
        "completed",
        "user_request",
    )
    assert all("value" not in task.model_dump() for task in result.tasks)
    assert channel_task.recommendation_fingerprint == hashlib.sha256(
        json.dumps(
            "C123",
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert "C123" not in json.dumps(
        channel_task.model_dump(mode="json"),
        ensure_ascii=False,
    )
    assert result.graph["nodes"][0]["data"]["channel"] == "C123"
    assert '"channel": "C123"' in result.graph["nodes"][0]["data"]["body"]
    assert channel_task.reason == "Slack API 방식으로 메시지를 보낼 대상 채널을 설정합니다."
    assert channel_task.node_label == "slack"
    assert channel_task.node_purpose == "Slack으로 메시지를 전송합니다."
    assert channel_task.configuration_state == "unresolved"
    assert channel_task.sensitivity == "safe"


def test_slack_parameter_tasks_only_activate_fields_for_the_selected_delivery_mode():
    planner = ParameterTaskPlanner()

    api_plan = planner.plan(
        graph={
            "nodes": [
                _node(
                    "slack",
                    "slackPostNode",
                    {"slackMode": "api", "message": "hello"},
                )
            ],
            "edges": [],
        },
        step_node_ids={"step_slack": "slack"},
        explicit_values={},
        upstream_candidates={},
        guidance_hints=[],
        base_node_ids=set(),
    )
    api_tasks = {task.parameter_key: task for task in api_plan.tasks}

    assert api_tasks["bot_token"].status == "active"
    assert api_tasks["bot_token"].required is True
    assert api_tasks["url"].status == "skipped"
    assert api_tasks["url"].required is False
    assert api_tasks["channel"].status == "pending"
    assert api_tasks["channel"].required is True

    webhook_plan = planner.plan(
        graph={
            "nodes": [
                _node(
                    "slack",
                    "slackPostNode",
                    {"slackMode": "webhook", "message": "hello"},
                )
            ],
            "edges": [],
        },
        step_node_ids={"step_slack": "slack"},
        explicit_values={},
        upstream_candidates={},
        guidance_hints=[],
        base_node_ids=set(),
    )
    webhook_tasks = {task.parameter_key: task for task in webhook_plan.tasks}

    assert webhook_tasks["bot_token"].status == "skipped"
    assert webhook_tasks["bot_token"].required is False
    assert webhook_tasks["channel"].status == "skipped"
    assert webhook_tasks["url"].status == "active"
    assert webhook_tasks["url"].required is True
    assert webhook_tasks["channel"].required is False


def test_switching_slack_delivery_mode_removes_incompatible_secret_fields():
    webhook_data = apply_parameter_value_to_node_data(
        "slackPostNode",
        "slackMode",
        {
            "slackMode": "api",
            "channel": "C123",
            "body": '{"text":"hello","channel":"C123"}',
            "authConfig": {"token": "synthetic-token"},
        },
        "webhook",
    )

    assert webhook_data["slackMode"] == "webhook"
    assert "authConfig" not in webhook_data
    assert "channel" not in webhook_data
    assert "channel" not in json.loads(webhook_data["body"])

    api_data = apply_parameter_value_to_node_data(
        "slackPostNode",
        "slackMode",
        {
            "slackMode": "webhook",
            "url": "https://hooks.slack.com/services/a/b/c",
        },
        "api",
    )

    assert api_data["slackMode"] == "api"
    assert "url" not in api_data


def test_reconcile_skips_webhook_task_when_slack_api_mode_is_selected():
    plan = ParameterTaskPlanner().plan(
        graph={
            "nodes": [
                _node(
                    "slack",
                    "slackPostNode",
                    {
                        "slackMode": "api",
                        "authConfig": {"token": "<redacted>"},
                        "message": "hello",
                    },
                )
            ],
            "edges": [],
        },
        step_node_ids={"step_slack": "slack"},
        explicit_values={},
        upstream_candidates={},
        guidance_hints=[],
        base_node_ids=set(),
    )
    channel_task = next(task for task in plan.tasks if task.parameter_key == "channel")
    legacy_url_task = channel_task.model_copy(
        update={
            "task_id": str(uuid4()),
            "parameter_key": "url",
            "label": "Webhook URL",
            "input_type": "secret",
            "required": True,
            "status": "active",
            "sensitivity": "secret_forbidden",
        }
    )
    legacy_tasks = [
        legacy_url_task,
        *[
            task.model_copy(update={"status": "pending"})
            if task.parameter_key == "channel"
            else task
            for task in plan.tasks
        ],
    ]

    recovered = reconcile_parameter_group_catalog_tasks(
        AgentBuilderParameterGroup(
            group_id=plan.group_id,
            status="active",
            tasks=legacy_tasks,
        ),
        plan.tasks,
    )
    recovered_by_key = {task.parameter_key: task for task in recovered.tasks}

    assert recovered_by_key["url"].status == "skipped"
    assert recovered_by_key["url"].required is False
    assert recovered_by_key["channel"].status == "active"


def test_legacy_direct_edit_knowledge_task_is_removed_and_the_group_completes():
    plan = ParameterTaskPlanner().plan(
        graph={
            "nodes": [
                _node(
                    "llm",
                    "llmNode",
                    {"model_id": "gpt-4.1-mini"},
                )
            ],
            "edges": [],
        },
        step_node_ids={"step_llm": "llm"},
        explicit_values={},
        upstream_candidates={},
        guidance_hints=[],
    )
    model_task = next(task for task in plan.tasks if task.parameter_key == "model_id")
    knowledge_task = next(
        task for task in plan.tasks if task.parameter_key == "knowledgeBases"
    )
    group = AgentBuilderParameterGroup(
        group_id=plan.group_id,
        status="active",
        tasks=[
            model_task.model_copy(update={"status": "completed"}),
            knowledge_task.model_copy(update={"status": "active"}),
        ],
    )

    normalized = remove_direct_edit_knowledge_parameter_tasks(group)

    assert [task.parameter_key for task in normalized.tasks] == ["model_id"]
    assert normalized.status == "completed"


def test_legacy_direct_edit_knowledge_task_is_not_changed_during_acknowledgement():
    plan = ParameterTaskPlanner().plan(
        graph={"nodes": [_node("llm", "llmNode")], "edges": []},
        step_node_ids={"step_llm": "llm"},
        explicit_values={},
        upstream_candidates={},
        guidance_hints=[],
    )
    group = AgentBuilderParameterGroup(
        group_id=plan.group_id,
        status="pending_ack",
        tasks=plan.tasks,
    )

    normalized = remove_direct_edit_knowledge_parameter_tasks(group)

    assert normalized is group


def test_legacy_external_credential_task_is_removed_without_blocking_safe_tasks():
    plan = ParameterTaskPlanner().plan(
        graph={"nodes": [_node("slack", "slackPostNode")], "edges": []},
        step_node_ids={"step_slack": "slack"},
        explicit_values={},
        upstream_candidates={},
        guidance_hints=[],
    )
    legacy_credential = AgentBuilderParameterTask(
        task_id=uuid4(),
        group_id=plan.group_id,
        step_id="step_slack",
        node_id="slack",
        node_type="slackPostNode",
        parameter_key="credential",
        label="Slack credential",
        input_type="credential_ref",
        required=True,
        status="active",
        task_version=1,
        stable_order=0,
        reason="legacy",
        input_guidance="legacy",
    )
    channel = next(
        task for task in plan.tasks if task.parameter_key == "channel"
    ).model_copy(update={"status": "pending"})
    group = AgentBuilderParameterGroup(
        group_id=plan.group_id,
        status="active",
        tasks=[legacy_credential, channel],
    )

    normalized = remove_direct_edit_external_credential_tasks(group)

    assert [task.parameter_key for task in normalized.tasks] == ["channel"]
    assert normalized.tasks[0].status == "active"


def test_external_secret_tasks_are_not_removed_as_legacy_credentials():
    plan = ParameterTaskPlanner().plan(
        graph={
            "nodes": [
                _node("slack", "slackPostNode"),
                _node("github", "githubNode"),
            ],
            "edges": [{"id": "e1", "source": "slack", "target": "github"}],
        },
        step_node_ids={"step_slack": "slack", "step_github": "github"},
        explicit_values={},
        upstream_candidates={},
        guidance_hints=[],
    )
    group = AgentBuilderParameterGroup(
        group_id=plan.group_id,
        status="active",
        tasks=plan.tasks,
    )

    normalized = remove_direct_edit_external_credential_tasks(group)

    secret_tasks = {
        (task.node_type, task.parameter_key, task.input_type)
        for task in normalized.tasks
        if task.input_type == "secret"
    }
    assert secret_tasks == {
        ("slackPostNode", "bot_token", "secret"),
        ("slackPostNode", "url", "secret"),
        ("githubNode", "api_token", "secret"),
    }


def test_legacy_partial_group_recovers_all_current_catalog_tasks():
    plan = ParameterTaskPlanner().plan(
        graph={"nodes": [_node("slack", "slackPostNode")], "edges": []},
        step_node_ids={"step_slack": "slack"},
        explicit_values={},
        upstream_candidates={},
        guidance_hints=[],
    )
    channel = next(
        task for task in plan.tasks if task.parameter_key == "channel"
    ).model_copy(update={"status": "active", "task_version": 4})
    legacy_group = AgentBuilderParameterGroup(
        group_id=plan.group_id,
        status="active",
        tasks=[channel],
    )

    recovered = reconcile_parameter_group_catalog_tasks(
        legacy_group,
        plan.tasks,
    )

    assert [task.parameter_key for task in recovered.tasks] == [
        "channel",
        "slackMode",
        "bot_token",
        "url",
        "message",
        "blocks",
        "attachments",
        "thread_ts",
        "username",
        "icon_emoji",
    ]
    recovered_channel = next(
        task for task in recovered.tasks if task.parameter_key == "channel"
    )
    assert recovered_channel.task_id == channel.task_id
    assert recovered_channel.stable_order == channel.stable_order
    assert all(
        task.stable_order > channel.stable_order
        for task in recovered.tasks
        if task.task_id != channel.task_id
    )
    recovered_channel = next(
        task for task in recovered.tasks if task.parameter_key == "channel"
    )
    recovered_mode = next(
        task for task in recovered.tasks if task.parameter_key == "slackMode"
    )
    assert recovered_channel.task_id == channel.task_id
    assert recovered_channel.task_version == 4
    assert recovered_channel.status == "active"
    assert recovered_mode.resolution_source == "catalog_default"
    assert recovered_mode.status == "completed"
    assert sum(task.status == "active" for task in recovered.tasks) == 1


def test_reconcile_completes_a_matching_legacy_catalog_recommendation():
    plan = ParameterTaskPlanner().plan(
        graph={"nodes": [_node("slack", "slackPostNode")], "edges": []},
        step_node_ids={"step_slack": "slack"},
        explicit_values={},
        upstream_candidates={},
        guidance_hints=[],
    )
    planned_mode = next(
        task for task in plan.tasks if task.parameter_key == "slackMode"
    )
    legacy_mode = planned_mode.model_copy(update={"status": "pending"})
    legacy_group = AgentBuilderParameterGroup(
        group_id=plan.group_id,
        status="active",
        tasks=[legacy_mode],
    )

    recovered = reconcile_parameter_group_catalog_tasks(
        legacy_group,
        plan.tasks,
    )

    recovered_mode = next(
        task for task in recovered.tasks if task.parameter_key == "slackMode"
    )
    assert recovered_mode.task_id == legacy_mode.task_id
    assert recovered_mode.resolution_source == "catalog_default"
    assert recovered_mode.recommendation_fingerprint == (
        planned_mode.recommendation_fingerprint
    )
    assert recovered_mode.status == "completed"


def test_planner_distinguishes_generated_defaults_from_base_graph_values():
    result = ParameterTaskPlanner().plan(
        graph={
            "nodes": [
                _node(
                    "generated-schedule",
                    "scheduleTrigger",
                    {"timezone": "UTC"},
                ),
                _node(
                    "existing-schedule",
                    "scheduleTrigger",
                    {"timezone": "Asia/Seoul"},
                ),
            ],
            "edges": [],
        },
        step_node_ids={
            "step_generated": "generated-schedule",
            "step_existing": "existing-schedule",
        },
        explicit_values={},
        upstream_candidates={},
        guidance_hints=[],
        base_node_ids={"existing-schedule"},
    )

    timezone_sources = {
        task.node_id: task.resolution_source
        for task in result.tasks
        if task.parameter_key == "timezone"
    }
    assert timezone_sources == {
        "generated-schedule": "catalog_default",
        "existing-schedule": "existing_graph",
    }


def test_gmail_flow_requires_confirmation_for_every_mail_configuration():
    graph = {
        "nodes": [
            _node(
                "mail",
                "mailNode",
                {
                    "credential_id": None,
                    "keyword": None,
                    "sender": None,
                    "subject": None,
                    "start_date": None,
                    "end_date": None,
                    "folder": "INBOX",
                    "max_results": 1,
                    "unread_only": False,
                    "mark_as_read": False,
                    "processing_mode": "durable",
                },
            ),
            _node(
                "draft",
                "gmailDraftNode",
                {
                    "credential_id": None,
                    "processing_ref_selector": ["mail", "processing_ref"],
                    "reply_body_selector": ["llm", "text"],
                },
            ),
            _node(
                "ack",
                "mailAcknowledgeNode",
                {
                    "processing_ref_selector": ["mail", "processing_ref"],
                    "required_effect_ref_selectors": [["draft", "draft_ref"]],
                },
            ),
        ],
        "edges": [
            {"id": "mail-draft", "source": "mail", "target": "draft"},
            {"id": "draft-ack", "source": "draft", "target": "ack"},
        ],
    }

    result = ParameterTaskPlanner().plan(
        graph=graph,
        step_node_ids={
            "step_mail": "mail",
            "step_draft": "draft",
            "step_ack": "ack",
        },
        explicit_values={},
        upstream_candidates={
            ("step_draft", "processing_ref_selector"): [
                ["mail", "processing_ref"]
            ],
            ("step_draft", "reply_body_selector"): [["llm", "text"]],
            ("step_ack", "processing_ref_selector"): [
                ["mail", "processing_ref"]
            ],
            ("step_ack", "required_effect_ref_selectors"): [
                ["draft", "draft_ref"]
            ],
        },
        guidance_hints=[],
        base_node_ids=set(),
    )

    assert [task.parameter_key for task in result.tasks] == [
        "credential_id",
        "keyword",
        "sender",
        "subject",
        "start_date",
        "end_date",
        "folder",
        "max_results",
        "unread_only",
        "mark_as_read",
        "processing_mode",
        "credential_id",
        "processing_ref_selector",
        "reply_body_selector",
        "processing_ref_selector",
        "required_effect_ref_selectors",
    ]
    by_identity = {
        (task.node_id, task.parameter_key): task for task in result.tasks
    }
    for parameter_key in (
        "folder",
        "max_results",
        "unread_only",
        "mark_as_read",
        "processing_mode",
    ):
        task = by_identity[("mail", parameter_key)]
        assert task.resolution_source == "catalog_default"
        assert task.recommendation_fingerprint is not None
        assert task.status == "completed"
    assert by_identity[("draft", "processing_ref_selector")].resolution_source == (
        "upstream_selector"
    )
    assert by_identity[("draft", "reply_body_selector")].resolution_source == (
        "upstream_selector"
    )
    assert by_identity[("ack", "processing_ref_selector")].resolution_source == (
        "upstream_selector"
    )
    assert by_identity[("ack", "required_effect_ref_selectors")].resolution_source == (
        "upstream_selector"
    )
    assert all(
        by_identity[identity].status == "completed"
        for identity in (
            ("draft", "processing_ref_selector"),
            ("draft", "reply_body_selector"),
            ("ack", "processing_ref_selector"),
            ("ack", "required_effect_ref_selectors"),
        )
    )
    assert by_identity[("mail", "credential_id")].status == "active"


def test_planner_exposes_empty_start_and_answer_schema_fields_as_user_tasks():
    result = ParameterTaskPlanner().plan(
        graph={
            "nodes": [
                _node("start", "startNode", {"variables": []}),
                _node("answer", "answerNode", {"outputs": []}),
            ],
            "edges": [{"id": "e1", "source": "start", "target": "answer"}],
        },
        step_node_ids={"step_input": "start", "step_answer": "answer"},
        explicit_values={},
        upstream_candidates={},
        guidance_hints=[],
    )

    assert [(task.parameter_key, task.status) for task in result.tasks] == [
        ("variables", "completed"),
        ("outputs", "active"),
    ]


def test_planner_rejects_secret_explicit_values_from_planner_output():
    graph = {
        "nodes": [_node("slack", "slackPostNode")],
        "edges": [],
    }

    result = ParameterTaskPlanner().plan(
        graph=graph,
        step_node_ids={"step_slack": "slack"},
        explicit_values={
            ("step_slack", "bot_token"): "credential-must-not-be-stored",
            ("step_slack", "channel"): "",
        },
        upstream_candidates={},
        guidance_hints=[],
    )

    assert [task.parameter_key for task in result.tasks] == [
        "slackMode",
        "bot_token",
        "url",
        "channel",
        "message",
        "blocks",
        "attachments",
        "thread_ts",
        "username",
        "icon_emoji",
    ]
    node_data = result.graph["nodes"][0]["data"]
    assert "bot_token" not in node_data
    assert "token" not in node_data.get("authConfig", {})
    assert "credential-must-not-be-stored" not in str(result.graph)


def test_planner_uses_single_upstream_and_safe_default_but_not_multiple_candidates():
    planner = ParameterTaskPlanner()
    schedule = planner.plan(
        graph={"nodes": [_node("schedule", "scheduleTrigger")], "edges": []},
        step_node_ids={"step_input": "schedule"},
        explicit_values={},
        upstream_candidates={},
        guidance_hints=[],
    )
    assert [(task.parameter_key, task.status, task.resolution_source) for task in schedule.tasks] == [
        ("cron_expression", "active", None),
        ("timezone", "completed", "catalog_default"),
    ]

    slack = planner.plan(
        graph={"nodes": [_node("slack", "slackPostNode")], "edges": []},
        step_node_ids={"step_slack": "slack"},
        explicit_values={},
        upstream_candidates={
            ("step_slack", "channel"): [["a", "channel"], ["b", "channel"]]
        },
        guidance_hints=[],
    )
    assert slack.tasks[0].status == "completed"
    assert slack.tasks[1].status == "active"
    assert [task.parameter_key for task in slack.tasks] == [
        "slackMode",
        "bot_token",
        "url",
        "channel",
        "message",
        "blocks",
        "attachments",
        "thread_ts",
        "username",
        "icon_emoji",
    ]


def test_auto_resolved_values_are_completed_and_remain_editable():
    plan = ParameterTaskPlanner().plan(
        graph={"nodes": [_node("schedule", "scheduleTrigger")], "edges": []},
        step_node_ids={"step_schedule": "schedule"},
        explicit_values={
            ("step_schedule", "cron_expression"): "0 9 * * *",
        },
        upstream_candidates={},
        guidance_hints=[],
    )

    cron_task, timezone_task = plan.tasks

    assert cron_task.status == "completed"
    assert cron_task.resolution_source == "user_request"
    assert timezone_task.status == "completed"
    assert timezone_task.resolution_source == "catalog_default"
    assert cron_task.recommendation_fingerprint == (
        canonical_parameter_value_fingerprint("0 9 * * *")
    )
    assert timezone_task.recommendation_fingerprint == (
        canonical_parameter_value_fingerprint("UTC")
    )

    decision = prepare_task_decision(
        tasks=plan.tasks,
        task_id=cron_task.task_id,
        operation_id=uuid4(),
        expected_task_version=cron_task.task_version,
        action="set",
        value="0 10 * * *",
    )

    assert decision.awaiting_persistence_ack is True
    assert decision.graph_data_patch == {"cron_expression": "0 10 * * *"}
    assert plan.graph["nodes"][0]["data"]["cron_expression"] == "0 9 * * *"


def test_slack_and_github_use_secret_tasks_instead_of_managed_credential_tasks():
    plan = ParameterTaskPlanner().plan(
        graph={
            "nodes": [
                _node("slack", "slackPostNode"),
                _node("github", "githubNode"),
                _node("mail", "mailNode"),
            ],
            "edges": [],
        },
        step_node_ids={
            "step_slack": "slack",
            "step_github": "github",
            "step_mail": "mail",
        },
        explicit_values={},
        upstream_candidates={},
        guidance_hints=[],
    )

    by_identity = {
        (task.node_id, task.parameter_key): task for task in plan.tasks
    }
    assert ("slack", "credential") not in by_identity
    assert ("github", "credential") not in by_identity
    assert by_identity[("slack", "bot_token")].input_type == "secret"
    assert by_identity[("slack", "bot_token")].required is True
    assert by_identity[("slack", "bot_token")].defer_policy == "allow_unresolved"
    assert by_identity[("slack", "url")].status == "skipped"
    assert by_identity[("slack", "url")].defer_policy == "allow_unresolved"
    assert by_identity[("github", "api_token")].input_type == "secret"
    assert by_identity[("github", "api_token")].required is True
    assert by_identity[("github", "api_token")].defer_policy == "allow_unresolved"
    assert by_identity[("mail", "credential_id")].status == "pending"
    assert by_identity[("slack", "slackMode")].status == "completed"
    assert by_identity[("slack", "channel")].status == "pending"
    assert by_identity[("slack", "channel")].configuration_state == "unresolved"
    assert "credential" not in plan.graph["nodes"][0]["data"]
    assert all(task.label not in {"Slack credential", "GitHub credential"} for task in plan.tasks)


def test_llm_parameter_plan_exposes_basic_settings_and_only_the_routing_toggle():
    plan = ParameterTaskPlanner().plan(
        graph={
            "nodes": [
                _node(
                    "llm",
                    "llmNode",
                    {
                        "model_id": "default-model",
                        "system_prompt": "Answer safely.",
                        "citationDisplayMode": "detailed",
                        "knowledgeBases": [],
                    },
                )
            ],
            "edges": [],
        },
        step_node_ids={"step_llm": "llm"},
        explicit_values={},
        upstream_candidates={},
        guidance_hints=[],
        base_node_ids=set(),
    )

    tasks = {task.parameter_key: task for task in plan.tasks}
    assert {
        "model_id",
        "output_format_type",
        "output_json_schema",
        "system_prompt",
        "user_prompt",
        "assistant_prompt",
        "referenced_variables",
        "citationDisplayMode",
        "auto_model_routing",
        "knowledgeBases",
    }.issubset(tasks)
    assert {
        "fallback_model_id",
        "model_routing_refresh_every_runs",
        "model_routing_validation_budget_usd",
        "model_routing_max_cohorts",
    }.isdisjoint(tasks)
    assert tasks["auto_model_routing"].resolution_source == "catalog_default"
    assert tasks["auto_model_routing"].status == "active"
    assert tasks["auto_model_routing"].confirmation_required is True
    assert tasks["auto_model_routing"].task_group == "model_routing"
    assert tasks["model_id"].status == "completed"
    assert tasks["model_id"].task_group is None
    assert tasks["citationDisplayMode"].resolution_source == "catalog_default"
    assert tasks["citationDisplayMode"].status == "completed"
    assert tasks["citationDisplayMode"].validation["options"] == [
        "hidden",
        "basic",
        "detailed",
    ]
    assert plan.graph["nodes"][0]["data"]["citationDisplayMode"] == "detailed"
    assert tasks["knowledgeBases"].status == "pending"
    assert plan.graph["nodes"][0]["data"]["auto_model_routing"] is False
    assert "model_routing_policy" not in plan.graph["nodes"][0]["data"]

    with pytest.raises(ParameterTaskConflict, match="confirmation is required"):
        prepare_task_decision(
            tasks=plan.tasks,
            task_id=tasks["auto_model_routing"].task_id,
            operation_id=uuid4(),
            expected_task_version=tasks["auto_model_routing"].task_version,
            action="skip",
            value=None,
        )

    decision = prepare_task_decision(
        tasks=plan.tasks,
        task_id=tasks["auto_model_routing"].task_id,
        operation_id=uuid4(),
        expected_task_version=tasks["auto_model_routing"].task_version,
        action="confirm",
        value=None,
    )
    confirmed = apply_local_task_decision(plan.tasks, decision)
    confirmed_by_key = {task.parameter_key: task for task in confirmed}
    assert decision.awaiting_persistence_ack is False
    assert confirmed_by_key["auto_model_routing"].status == "completed"
    assert confirmed_by_key["auto_model_routing"].task_version == 2
    assert confirmed_by_key["knowledgeBases"].status == "active"


def test_llm_parameter_plan_does_not_enable_citations_for_legacy_existing_node():
    plan = ParameterTaskPlanner().plan(
        graph={
            "nodes": [
                _node(
                    "llm",
                    "llmNode",
                    {
                        "model_id": "default-model",
                        "system_prompt": "Answer safely.",
                    },
                )
            ],
            "edges": [],
        },
        step_node_ids={"step_llm": "llm"},
        explicit_values={},
        upstream_candidates={},
        guidance_hints=[],
        base_node_ids={"llm"},
    )

    citation_task = next(
        task for task in plan.tasks if task.parameter_key == "citationDisplayMode"
    )
    assert citation_task.status == "active"
    assert "citationDisplayMode" not in plan.graph["nodes"][0]["data"]


def test_llm_parameter_plan_requires_one_prompt_when_all_prompts_are_empty():
    plan = ParameterTaskPlanner().plan(
        graph={
            "nodes": [
                _node(
                    "llm",
                    "llmNode",
                    {"model_id": "default-model", "knowledgeBases": []},
                )
            ],
            "edges": [],
        },
        step_node_ids={"step_llm": "llm"},
        explicit_values={},
        upstream_candidates={},
        guidance_hints=[],
        base_node_ids=set(),
    )

    tasks = {task.parameter_key: task for task in plan.tasks}
    assert tasks["system_prompt"].status == "active"
    assert tasks["system_prompt"].resolution_source is None
    assert tasks["system_prompt"].recommendation_fingerprint is None
    assert tasks["user_prompt"].status == "pending"
    assert tasks["assistant_prompt"].status == "pending"
    assert tasks["auto_model_routing"].status == "pending"
    assert tasks["system_prompt"].configuration_state == "unresolved"

    current_tasks = plan.tasks
    for parameter_key in ("system_prompt", "user_prompt"):
        task = next(
            item for item in current_tasks if item.parameter_key == parameter_key
        )
        decision = prepare_task_decision(
            tasks=current_tasks,
            task_id=task.task_id,
            operation_id=uuid4(),
            expected_task_version=task.task_version,
            action="skip",
            value=None,
        )
        current_tasks = apply_local_task_decision(current_tasks, decision)

    final_prompt = next(
        item for item in current_tasks if item.parameter_key == "assistant_prompt"
    )
    with pytest.raises(ParameterTaskConflict, match="one prompt is required"):
        prepare_task_decision(
            tasks=current_tasks,
            task_id=final_prompt.task_id,
            operation_id=uuid4(),
            expected_task_version=final_prompt.task_version,
            action="skip",
            value=None,
        )


def test_recovery_affected_nodes_union_all_valid_envelopes_and_existing_tasks():
    graph = {
        "nodes": [
            _node("llm-a", "llmNode"),
            _node("llm-b", "llmNode"),
            _node("llm-c", "llmNode"),
        ],
        "edges": [],
    }

    assert recovery_affected_node_ids(
        graph=graph,
        envelopes=[
            {
                "catalog_version": 3,
                "status": "applied",
                "affected_node_ids": ["llm-a", "missing"],
            },
            {
                "catalog_version": 3,
                "status": "pending_ack",
                "affected_node_ids": ["llm-b"],
            },
            {
                "catalog_version": 3,
                "status": "reverted",
                "affected_node_ids": ["llm-c"],
            },
            {
                "catalog_version": 2,
                "status": "applied",
                "affected_node_ids": ["llm-c"],
            },
        ],
        parameter_task_node_ids=["llm-c", "removed-task-node"],
    ) == {"llm-a", "llm-b", "llm-c"}


def test_parameter_planner_excludes_unaffected_existing_llm_nodes():
    plan = ParameterTaskPlanner().plan(
        graph={
            "nodes": [
                _node(
                    "affected",
                    "llmNode",
                    {"model_id": "model-a", "system_prompt": "A"},
                ),
                _node(
                    "unrelated",
                    "llmNode",
                    {"model_id": "model-b", "system_prompt": "B"},
                ),
            ],
            "edges": [],
        },
        step_node_ids={"step_a": "affected", "step_b": "unrelated"},
        explicit_values={},
        upstream_candidates={},
        guidance_hints=[],
        base_node_ids={"affected", "unrelated"},
        affected_node_ids={"affected"},
    )

    assert {task.node_id for task in plan.tasks} == {"affected"}


def test_reconcile_reopens_only_unconfirmed_legacy_disabled_routing_recommendation():
    plan = ParameterTaskPlanner().plan(
        graph={
            "nodes": [
                _node(
                    "llm",
                    "llmNode",
                    {"model_id": "default-model", "knowledgeBases": []},
                )
            ],
            "edges": [],
        },
        step_node_ids={"step_llm": "llm"},
        explicit_values={},
        upstream_candidates={},
        guidance_hints=[],
        base_node_ids=set(),
    )
    routing_task = next(
        task for task in plan.tasks if task.parameter_key == "auto_model_routing"
    )
    legacy_group = AgentBuilderParameterGroup(
        group_id=plan.group_id,
        status="completed",
        tasks=[routing_task.model_copy(update={"status": "completed"})],
    )

    reopened = reconcile_parameter_group_catalog_tasks(legacy_group, plan.tasks)

    assert reopened.status == "active"
    reopened_routing = next(
        task
        for task in reopened.tasks
        if task.parameter_key == "auto_model_routing"
    )
    assert reopened_routing.status == "active"
    confirmed_group = reopened.model_copy(
        update={
            "status": "active",
            "tasks": [
                task.model_copy(update={"status": "completed", "task_version": 2})
                if task.parameter_key == "auto_model_routing"
                else task
                for task in reopened.tasks
            ],
        }
    )
    recovered_again = reconcile_parameter_group_catalog_tasks(
        confirmed_group, plan.tasks
    )
    recovered_routing = next(
        task
        for task in recovered_again.tasks
        if task.parameter_key == "auto_model_routing"
    )
    assert recovered_routing.status == "completed"
    assert recovered_routing.task_version == 2


def test_reconcile_adds_new_catalog_tasks_to_completed_group_once():
    plan = ParameterTaskPlanner().plan(
        graph={
            "nodes": [
                _node(
                    "llm",
                    "llmNode",
                    {
                        "model_id": "default-model",
                        "system_prompt": "Answer safely.",
                        "auto_model_routing": True,
                    },
                )
            ],
            "edges": [],
        },
        step_node_ids={"step_llm": "llm"},
        explicit_values={},
        upstream_candidates={},
        guidance_hints=[],
        base_node_ids={"llm"},
    )
    model_task = next(task for task in plan.tasks if task.parameter_key == "model_id")
    existing_model_task = model_task.model_copy(
        update={"status": "completed", "task_version": 4}
    )
    completed_group = AgentBuilderParameterGroup(
        group_id=plan.group_id,
        status="completed",
        tasks=[existing_model_task],
    )

    recovered = reconcile_parameter_group_catalog_tasks(completed_group, plan.tasks)

    assert recovered.status == "active"
    assert {
        (task.node_id, task.parameter_key) for task in recovered.tasks
    } == {(task.node_id, task.parameter_key) for task in plan.tasks}
    recovered_model = next(
        task for task in recovered.tasks if task.parameter_key == "model_id"
    )
    assert recovered_model.task_id == existing_model_task.task_id
    assert recovered_model.task_version == 4
    assert recovered_model.status == "completed"
    assert sum(task.status == "active" for task in recovered.tasks) == 1
    assert reconcile_parameter_group_catalog_tasks(recovered, plan.tasks) == recovered


def test_reconcile_preserves_existing_stable_order_and_appends_new_catalog_tasks():
    plan = ParameterTaskPlanner().plan(
        graph={
            "nodes": [
                _node(
                    "llm",
                    "llmNode",
                    {
                        "model_id": "default-model",
                        "system_prompt": "Answer safely.",
                        "auto_model_routing": True,
                    },
                )
            ],
            "edges": [],
        },
        step_node_ids={"step_llm": "llm"},
        explicit_values={},
        upstream_candidates={},
        guidance_hints=[],
        base_node_ids={"llm"},
    )
    model_task = next(task for task in plan.tasks if task.parameter_key == "model_id")
    prompt_task = next(
        task for task in plan.tasks if task.parameter_key == "system_prompt"
    )
    existing_prompt = prompt_task.model_copy(
        update={"status": "completed", "stable_order": 4}
    )
    existing_model = model_task.model_copy(
        update={"status": "completed", "stable_order": 9}
    )
    completed_group = AgentBuilderParameterGroup(
        group_id=plan.group_id,
        status="completed",
        tasks=[existing_prompt, existing_model],
    )

    recovered = reconcile_parameter_group_catalog_tasks(completed_group, plan.tasks)
    recovered_by_key = {task.parameter_key: task for task in recovered.tasks}

    assert recovered_by_key["system_prompt"].task_id == existing_prompt.task_id
    assert recovered_by_key["system_prompt"].stable_order == 4
    assert recovered_by_key["model_id"].task_id == existing_model.task_id
    assert recovered_by_key["model_id"].stable_order == 9
    assert all(
        task.stable_order > 9
        for task in recovered.tasks
        if task.task_id not in {existing_prompt.task_id, existing_model.task_id}
    )
    assert reconcile_parameter_group_catalog_tasks(recovered, plan.tasks) == recovered


def test_reconcile_keeps_completed_group_closed_for_configured_additions():
    plan = ParameterTaskPlanner().plan(
        graph={
            "nodes": [
                _node(
                    "llm",
                    "llmNode",
                    {
                        "model_id": "default-model",
                        "system_prompt": "Answer safely.",
                        "auto_model_routing": True,
                    },
                )
            ],
            "edges": [],
        },
        step_node_ids={"step_llm": "llm"},
        explicit_values={},
        upstream_candidates={},
        guidance_hints=[],
        base_node_ids={"llm"},
    )
    model_task = next(task for task in plan.tasks if task.parameter_key == "model_id")
    prompt_task = next(
        task for task in plan.tasks if task.parameter_key == "system_prompt"
    )
    completed_group = AgentBuilderParameterGroup(
        group_id=plan.group_id,
        status="completed",
        tasks=[model_task],
    )

    recovered = reconcile_parameter_group_catalog_tasks(
        completed_group,
        [model_task, prompt_task],
    )

    assert recovered.status == "completed"
    assert [task.status for task in recovered.tasks] == ["completed", "completed"]
    assert recovered.tasks[0].task_id == model_task.task_id
    assert recovered.tasks[1].stable_order > recovered.tasks[0].stable_order


def test_reconcile_never_reopens_canceled_group():
    plan = ParameterTaskPlanner().plan(
        graph={"nodes": [_node("slack", "slackPostNode")], "edges": []},
        step_node_ids={"step_slack": "slack"},
        explicit_values={},
        upstream_candidates={},
        guidance_hints=[],
    )
    canceled_group = AgentBuilderParameterGroup(
        group_id=plan.group_id,
        status="canceled",
        tasks=[plan.tasks[0].model_copy(update={"status": "canceled"})],
    )

    assert reconcile_parameter_group_catalog_tasks(
        canceled_group,
        plan.tasks,
    ) == canceled_group


def test_reconcile_preserves_github_secret_task_in_completed_group():
    plan = ParameterTaskPlanner().plan(
        graph={
            "nodes": [
                _node(
                    "github",
                    "githubNode",
                    {
                        "action": "get_pr",
                        "repo_owner": "octo",
                        "repo_name": "repo",
                        "pr_number": "15",
                    },
                )
            ],
            "edges": [],
        },
        step_node_ids={"step_github": "github"},
        explicit_values={},
        upstream_candidates={},
        guidance_hints=[],
        base_node_ids={"github"},
    )
    action_task = next(task for task in plan.tasks if task.parameter_key == "action")
    legacy_token_task = action_task.model_copy(
        update={
            "task_id": str(uuid4()),
            "parameter_key": "api_token",
            "label": "GitHub API Token",
            "input_type": "secret",
            "required": True,
            "status": "skipped",
            "sensitivity": "secret_forbidden",
        }
    )
    legacy_group = AgentBuilderParameterGroup(
        group_id=plan.group_id,
        status="completed",
        tasks=[legacy_token_task, *plan.tasks],
    )

    recovered = reconcile_parameter_group_catalog_tasks(legacy_group, plan.tasks)

    token_tasks = [
        task for task in recovered.tasks if task.parameter_key == "api_token"
    ]
    assert len(token_tasks) == 1
    assert token_tasks[0].required is True
    assert token_tasks[0].status == "active"

def test_slack_parameter_flow_cannot_skip_every_payload_format():
    plan = ParameterTaskPlanner().plan(
        graph={
            "nodes": [
                _node(
                    "slack",
                    "slackPostNode",
                    {
                        "slackMode": "api",
                        "authConfig": {"token": "<redacted>"},
                        "channel": "C123",
                    },
                )
            ],
            "edges": [],
        },
        step_node_ids={"step_slack": "slack"},
        explicit_values={},
        upstream_candidates={},
        guidance_hints=[],
        base_node_ids=set(),
    )

    current_tasks = plan.tasks
    for parameter_key in ("message", "blocks"):
        task = next(
            item for item in current_tasks if item.parameter_key == parameter_key
        )
        assert task.status == "active"
        decision = prepare_task_decision(
            tasks=current_tasks,
            task_id=task.task_id,
            operation_id=uuid4(),
            expected_task_version=task.task_version,
            action="skip",
            value=None,
        )
        current_tasks = apply_local_task_decision(current_tasks, decision)

    final_payload = next(
        item for item in current_tasks if item.parameter_key == "attachments"
    )
    assert final_payload.status == "active"
    with pytest.raises(ParameterTaskConflict, match="one payload is required"):
        prepare_task_decision(
            tasks=current_tasks,
            task_id=final_payload.task_id,
            operation_id=uuid4(),
            expected_task_version=final_payload.task_version,
            action="skip",
            value=None,
        )


def test_reconcile_preserves_mode_relevant_slack_secret_tasks_without_hydrating_values():
    plan = ParameterTaskPlanner().plan(
        graph={
            "nodes": [
                _node(
                    "slack",
                    "slackPostNode",
                    {
                        "slackMode": "api",
                        "authConfig": {"token": "<redacted>"},
                    },
                )
            ],
            "edges": [],
        },
        step_node_ids={"step_slack": "slack"},
        explicit_values={},
        upstream_candidates={},
        guidance_hints=[],
        base_node_ids={"slack"},
    )
    channel_task = next(task for task in plan.tasks if task.parameter_key == "channel")
    legacy_secret_tasks = [
        channel_task.model_copy(
            update={
                "task_id": str(uuid4()),
                "parameter_key": parameter_key,
                "label": label,
                "input_type": "secret",
                "required": True,
                "status": status,
                "sensitivity": "secret_forbidden",
            }
        )
        for parameter_key, label, status in (
            ("bot_token", "Bot Token", "completed"),
            ("url", "Webhook URL", "skipped"),
        )
    ]
    legacy_group = AgentBuilderParameterGroup(
        group_id=plan.group_id,
        status="completed",
        tasks=[*legacy_secret_tasks, *plan.tasks],
    )

    recovered = reconcile_parameter_group_catalog_tasks(legacy_group, plan.tasks)

    secret_tasks = {
        task.parameter_key: task
        for task in recovered.tasks
        if task.parameter_key in {"bot_token", "url"}
    }
    assert set(secret_tasks) == {"bot_token", "url"}
    assert secret_tasks["bot_token"].required is True
    assert secret_tasks["bot_token"].status == "completed"
    assert secret_tasks["url"].required is False
    assert secret_tasks["url"].status == "skipped"

def test_reconcile_removes_agent_builder_hidden_routing_tasks_without_mutating_graph():
    plan = ParameterTaskPlanner().plan(
        graph={
            "nodes": [
                _node(
                    "llm",
                    "llmNode",
                    {
                        "model_id": "default-model",
                        "fallback_model_id": "fallback-model",
                        "model_routing_policy": {
                            "refresh": {"refresh_every_runs": 30},
                            "validation_budget_usd": 4.0,
                            "max_cohorts": 8,
                        },
                    },
                )
            ],
            "edges": [],
        },
        step_node_ids={"step_llm": "llm"},
        explicit_values={},
        upstream_candidates={},
        guidance_hints=[],
    )
    hidden_keys = {
        "fallback_model_id",
        "model_routing_refresh_every_runs",
        "model_routing_validation_budget_usd",
        "model_routing_max_cohorts",
    }
    visible_tasks = [
        task for task in plan.tasks if task.parameter_key not in hidden_keys
    ]
    routing_toggle = next(
        task for task in visible_tasks if task.parameter_key == "auto_model_routing"
    )
    legacy_hidden = routing_toggle.model_copy(
        update={
            "task_id": uuid4(),
            "parameter_key": "fallback_model_id",
            "label": "Fallback model",
            "input_type": "resource_ref",
            "status": "active",
            "stable_order": len(visible_tasks),
        }
    )
    legacy_group = AgentBuilderParameterGroup(
        group_id=plan.group_id,
        status="active",
        tasks=[*visible_tasks, legacy_hidden],
    )

    recovered = reconcile_parameter_group_catalog_tasks(
        legacy_group,
        visible_tasks,
    )

    assert hidden_keys.isdisjoint(task.parameter_key for task in recovered.tasks)
    assert plan.graph["nodes"][0]["data"]["fallback_model_id"] == "fallback-model"
    assert plan.graph["nodes"][0]["data"]["model_routing_policy"] == {
        "refresh": {"refresh_every_runs": 30},
        "validation_budget_usd": 4.0,
        "max_cohorts": 8,
    }


def test_parameter_tasks_follow_graph_topology_instead_of_step_mapping_order():
    plan = ParameterTaskPlanner().plan(
        graph={
            "nodes": [
                _node("slack", "slackPostNode"),
                _node("llm", "llmNode", {"model_id": "default-model"}),
                _node("github", "githubNode"),
            ],
            "edges": [
                {"id": "e1", "source": "slack", "target": "llm"},
                {"id": "e2", "source": "llm", "target": "github"},
            ],
        },
        step_node_ids={
            "step_github": "github",
            "step_llm": "llm",
            "step_slack": "slack",
        },
        explicit_values={},
        upstream_candidates={},
        guidance_hints=[],
    )

    node_order = list(dict.fromkeys(task.node_id for task in plan.tasks))
    assert node_order == ["slack", "llm", "github"]


def test_slack_catalog_guidance_is_korean_and_explains_upstream_message_template():
    plan = ParameterTaskPlanner().plan(
        graph={
            "nodes": [
                _node(
                    "slack",
                    "slackPostNode",
                    {
                        "slackMode": "api",
                        "message": "{{result}}",
                        "referenced_variables": [
                            {"name": "result", "value_selector": ["llm", "text"]}
                        ],
                    },
                )
            ],
            "edges": [],
        },
        step_node_ids={"step_slack": "slack"},
        explicit_values={},
        upstream_candidates={},
        guidance_hints=[],
        base_node_ids=set(),
    )
    tasks = {task.parameter_key: task for task in plan.tasks}

    assert tasks["slackMode"].reason == "Slack 메시지를 보낼 방식을 선택합니다."
    assert "{{result}}" in tasks["message"].input_guidance
    assert "이전 노드 출력" in tasks["message"].input_guidance


def test_reference_value_candidate_keeps_runtime_graph_reference_available():
    task = AgentBuilderParameterTask(
        task_id=uuid4(),
        group_id=uuid4(),
        step_id="step-workflow",
        node_id="workflow",
        node_type="workflowNode",
        parameter_key="workflowId",
        label="Workflow",
        input_type="resource_ref",
        required=True,
        status="active",
        task_version=1,
        stable_order=0,
        reason="safe reason",
        input_guidance="safe guidance",
    )
    candidate = AgentBuilderParameterCandidate(
        candidate_id=uuid4(),
        kind="resource_ref",
        label="Available workflow",
        reference_value="runtime-workflow-id",
    )
    provider = ParameterCandidateProvider(
        SimpleNamespace(),
        user_id=uuid4(),
        organization_id=uuid4(),
    )

    assert provider._reference_is_unavailable(
        task,
        {"workflow": _node("workflow", "workflowNode", {"workflowId": "runtime-workflow-id"})},
        [candidate],
    ) is False


def test_optional_skip_is_local_and_reopenable_without_graph_mutation():
    result = ParameterTaskPlanner().plan(
        graph={
            "nodes": [
                _node(
                    "llm",
                    "llmNode",
                    {
                        "model_id": "model",
                        "system_prompt": "Answer safely.",
                        "citationDisplayMode": "basic",
                    },
                )
            ],
            "edges": [],
        },
        step_node_ids={"step_llm": "llm"},
        explicit_values={},
        upstream_candidates={},
        guidance_hints=[],
    )
    routing_task = next(
        task
        for task in result.tasks
        if task.parameter_key == "auto_model_routing"
    )
    routing_confirmation = prepare_task_decision(
        tasks=result.tasks,
        task_id=routing_task.task_id,
        operation_id=uuid4(),
        expected_task_version=routing_task.task_version,
        action="confirm",
        value=None,
    )
    tasks_after_routing = apply_local_task_decision(
        result.tasks, routing_confirmation
    )
    task = next(
        task
        for task in tasks_after_routing
        if task.parameter_key == "knowledgeBases"
    )
    assert task.status == "active"

    decision = prepare_task_decision(
        tasks=tasks_after_routing,
        task_id=task.task_id,
        operation_id=uuid4(),
        expected_task_version=task.task_version,
        action="skip",
        value=None,
    )
    updated = apply_local_task_decision(tasks_after_routing, decision)

    skipped = next(item for item in updated if item.task_id == task.task_id)
    assert decision.awaiting_persistence_ack is False
    assert decision.graph_data_patch is None
    assert skipped.status == "skipped"
    assert not any(item.status == "active" for item in updated)
    previous_task_id = previous_reopenable_task_id(updated, task.task_id)
    previous_task = next(item for item in updated if item.task_id == previous_task_id)
    assert previous_task.stable_order < task.stable_order
    assert previous_task.status in {"completed", "skipped", "deferred"}


def test_previous_selects_global_stable_order_without_reopening_completed_state():
    plan = ParameterTaskPlanner().plan(
        graph={
            "nodes": [
                _node("slack", "slackPostNode"),
                _node("github", "githubNode"),
            ],
            "edges": [],
        },
        step_node_ids={"step_slack": "slack", "step_github": "github"},
        explicit_values={},
        upstream_candidates={},
        guidance_hints=[],
    )
    by_key = {(task.node_id, task.parameter_key): task for task in plan.tasks}
    slack_channel = by_key[("slack", "channel")].model_copy(
        update={"status": "completed"}
    )
    github_owner = by_key[("github", "repo_owner")].model_copy(
        update={"status": "completed"}
    )
    github_repo = by_key[("github", "repo_name")].model_copy(
        update={"status": "active"}
    )
    selected_ids = {
        slack_channel.task_id,
        github_owner.task_id,
        github_repo.task_id,
    }
    remaining = [
        task.model_copy(update={"status": "pending"})
        for task in plan.tasks
        if task.task_id not in selected_ids
    ]
    persisted_order = [
        github_repo,
        *remaining,
        github_owner,
        slack_channel,
    ]
    decision = prepare_task_decision(
        tasks=persisted_order,
        task_id=github_repo.task_id,
        operation_id=uuid4(),
        expected_task_version=github_repo.task_version,
        action="previous",
        value=None,
    )

    updated = apply_local_task_decision(persisted_order, decision)

    assert previous_reopenable_task_id(
        persisted_order,
        github_repo.task_id,
    ) == github_owner.task_id
    assert next(
        task for task in updated if task.task_id == github_owner.task_id
    ).status == "completed"
    assert next(
        task for task in updated if task.task_id == github_repo.task_id
    ).status == "active"
    assert updated == persisted_order


@pytest.mark.parametrize("current_status", ["completed", "skipped", "deferred"])
def test_previous_accepts_client_reopened_terminal_current_task(current_status):
    group_id = uuid4()
    previous_task = AgentBuilderParameterTask(
        task_id=uuid4(),
        group_id=group_id,
        step_id="step-previous",
        node_id="previous",
        node_type="answerNode",
        parameter_key="previous",
        label="Previous",
        input_type="text",
        required=True,
        status="completed",
        task_version=4,
        stable_order=0,
        reason="safe reason",
        input_guidance="safe guidance",
    )
    current_task = previous_task.model_copy(
        update={
            "task_id": uuid4(),
            "step_id": "step-current",
            "node_id": "current",
            "parameter_key": "current",
            "label": "Current",
            "status": current_status,
            "task_version": 7,
            "stable_order": 1,
        }
    )
    tasks = [previous_task, current_task]

    decision = prepare_task_decision(
        tasks=tasks,
        task_id=current_task.task_id,
        operation_id=uuid4(),
        expected_task_version=current_task.task_version,
        action="previous",
        value=None,
    )
    unchanged = apply_local_task_decision(tasks, decision)

    assert previous_reopenable_task_id(tasks, current_task.task_id) == (
        previous_task.task_id
    )
    assert unchanged == tasks


@pytest.mark.parametrize(
    "status",
    ["active", "completed", "skipped", "deferred", "invalid"],
)
def test_set_is_allowed_for_every_reeditable_status_and_acknowledges_completed(status):
    group_id = uuid4()
    task = AgentBuilderParameterTask(
        task_id=uuid4(),
        group_id=group_id,
        step_id="step-answer",
        node_id="answer",
        node_type="answerNode",
        parameter_key="answer",
        label="Answer",
        input_type="text",
        required=True,
        status=status,
        task_version=3,
        stable_order=0,
        resolution_source="catalog_default",
        recommendation_fingerprint="a" * 64,
        reason="safe reason",
        input_guidance="safe guidance",
    )

    decision = prepare_task_decision(
        tasks=[task],
        task_id=task.task_id,
        operation_id=uuid4(),
        expected_task_version=task.task_version,
        action="set",
        value="changed",
    )
    acknowledged = acknowledge_task_decision([task], decision)

    assert decision.awaiting_persistence_ack is True
    assert acknowledged[0].status == "completed"
    assert acknowledged[0].task_version == task.task_version + 1
    assert acknowledged[0].resolution_source == "user_request"
    assert acknowledged[0].recommendation_fingerprint is None


def test_confirm_requires_the_exact_current_canonical_graph_value_fingerprint():
    group_id = uuid4()
    task = AgentBuilderParameterTask(
        task_id=uuid4(),
        group_id=group_id,
        step_id="step-schedule",
        node_id="schedule",
        node_type="scheduleTrigger",
        parameter_key="timezone",
        label="Timezone",
        input_type="select",
        required=True,
        status="active",
        task_version=1,
        stable_order=0,
        resolution_source="catalog_default",
        recommendation_fingerprint=canonical_parameter_value_fingerprint("UTC"),
        reason="safe reason",
        input_guidance="safe guidance",
    )
    matching = {
        "nodes": [_node("schedule", "scheduleTrigger", {"timezone": "UTC"})],
        "edges": [],
    }
    changed = deepcopy(matching)
    changed["nodes"][0]["data"]["timezone"] = "Asia/Seoul"
    deleted = deepcopy(matching)
    del deleted["nodes"][0]["data"]["timezone"]

    assert recommendation_matches_canonical_graph(task, matching) is True
    assert recommendation_matches_canonical_graph(task, changed) is False
    assert recommendation_matches_canonical_graph(task, deleted) is False
    assert (
        recommendation_matches_canonical_graph(
            task.model_copy(update={"recommendation_fingerprint": None}),
            matching,
        )
        is False
    )


@pytest.mark.parametrize("status", ["pending", "canceled"])
def test_set_remains_blocked_for_non_reeditable_statuses(status):
    group_id = uuid4()
    task = AgentBuilderParameterTask(
        task_id=uuid4(),
        group_id=group_id,
        step_id="step-answer",
        node_id="answer",
        node_type="answerNode",
        parameter_key="answer",
        label="Answer",
        input_type="text",
        required=True,
        status=status,
        task_version=1,
        stable_order=0,
        reason="safe reason",
        input_guidance="safe guidance",
    )

    with pytest.raises(ParameterTaskConflict, match="not settable"):
        prepare_task_decision(
            tasks=[task],
            task_id=task.task_id,
            operation_id=uuid4(),
            expected_task_version=task.task_version,
            action="set",
            value="changed",
        )


def test_clear_optional_completed_task_requires_ack_and_finishes_as_skipped():
    group_id = uuid4()
    task = AgentBuilderParameterTask(
        task_id=uuid4(),
        group_id=group_id,
        step_id="step-slack",
        node_id="slack",
        node_type="slackPostNode",
        parameter_key="message",
        label="Message",
        input_type="textarea",
        required=False,
        status="completed",
        task_version=3,
        stable_order=0,
        resolution_source="user_request",
        reason="safe reason",
        input_guidance="safe guidance",
    )
    remaining_payload = task.model_copy(
        update={
            "task_id": uuid4(),
            "parameter_key": "blocks",
            "label": "Blocks",
            "input_type": "json",
            "status": "pending",
            "task_version": 1,
            "stable_order": 1,
            "resolution_source": None,
        }
    )

    decision = prepare_task_decision(
        tasks=[task, remaining_payload],
        task_id=task.task_id,
        operation_id=uuid4(),
        expected_task_version=task.task_version,
        action="clear",
        value=None,
    )

    assert decision.awaiting_persistence_ack is True
    assert decision.graph_data_patch == {"_clear_parameter": "message"}
    assert task.status == "completed"
    acknowledged = acknowledge_task_decision([task, remaining_payload], decision)
    acknowledged_message = next(
        item for item in acknowledged if item.parameter_key == "message"
    )
    assert acknowledged_message.status == "skipped"
    assert acknowledged_message.task_version == 4


def test_clear_last_slack_payload_is_rejected():
    group_id = uuid4()
    task = AgentBuilderParameterTask(
        task_id=uuid4(),
        group_id=group_id,
        step_id="step-slack",
        node_id="slack",
        node_type="slackPostNode",
        parameter_key="message",
        label="Message",
        input_type="textarea",
        required=False,
        status="completed",
        task_version=1,
        stable_order=0,
        reason="safe reason",
        input_guidance="safe guidance",
    )

    with pytest.raises(ParameterTaskConflict, match="one payload is required"):
        prepare_task_decision(
            tasks=[task],
            task_id=task.task_id,
            operation_id=uuid4(),
            expected_task_version=task.task_version,
            action="clear",
            value=None,
        )


def test_defer_requires_catalog_policy_and_only_advances_after_acknowledgement():
    result = ParameterTaskPlanner().plan(
        graph={
            "nodes": [
                _node(
                    "llm",
                    "llmNode",
                    {
                        "system_prompt": "Answer safely.",
                        "citationDisplayMode": "basic",
                    },
                )
            ],
            "edges": [],
        },
        step_node_ids={"step_llm": "llm"},
        explicit_values={},
        upstream_candidates={},
        guidance_hints=[],
    )
    model_task = result.tasks[0]
    decision = prepare_task_decision(
        tasks=result.tasks,
        task_id=model_task.task_id,
        operation_id=uuid4(),
        expected_task_version=model_task.task_version,
        action="defer",
        value=None,
    )

    assert decision.awaiting_persistence_ack is True
    assert model_task.status == "active"
    acknowledged = acknowledge_task_decision(result.tasks, decision)
    acknowledged_by_key = {task.parameter_key: task for task in acknowledged}
    assert acknowledged_by_key["model_id"].status == "deferred"
    assert acknowledged_by_key["auto_model_routing"].status == "active"
    assert acknowledged_by_key["knowledgeBases"].status == "pending"

    schedule = ParameterTaskPlanner().plan(
        graph={"nodes": [_node("schedule", "scheduleTrigger")], "edges": []},
        step_node_ids={"step_input": "schedule"},
        explicit_values={},
        upstream_candidates={},
        guidance_hints=[],
    )
    with pytest.raises(ParameterTaskConflict, match="defer"):
        prepare_task_decision(
            tasks=schedule.tasks,
            task_id=schedule.tasks[0].task_id,
            operation_id=uuid4(),
            expected_task_version=schedule.tasks[0].task_version,
            action="defer",
            value=None,
        )


def test_required_secret_defer_keeps_slack_unresolved_until_token_is_set():
    result = ParameterTaskPlanner().plan(
        graph={"nodes": [_node("slack", "slackPostNode")], "edges": []},
        step_node_ids={"step_slack": "slack"},
        explicit_values={},
        upstream_candidates={},
        guidance_hints=[],
    )
    token_task = next(
        task for task in result.tasks if task.parameter_key == "bot_token"
    )

    decision = prepare_task_decision(
        tasks=result.tasks,
        task_id=token_task.task_id,
        operation_id=uuid4(),
        expected_task_version=token_task.task_version,
        action="defer",
        value=None,
    )

    assert token_task.required is True
    assert decision.awaiting_persistence_ack is True
    assert decision.graph_data_patch == {"_deferred_parameter": "bot_token"}
    acknowledged = acknowledge_task_decision(result.tasks, decision)
    acknowledged_token = next(
        task for task in acknowledged if task.parameter_key == "bot_token"
    )
    assert acknowledged_token.status == "deferred"
    assert acknowledged_token.configuration_state == "unresolved"


def test_cancel_parameter_group_preserves_completed_and_closes_remaining_tasks():
    result = ParameterTaskPlanner().plan(
        graph={"nodes": [_node("slack", "slackPostNode")], "edges": []},
        step_node_ids={"step_slack": "slack"},
        explicit_values={},
        upstream_candidates={},
        guidance_hints=[],
    )
    group = AgentBuilderParameterGroup(
        group_id=result.group_id,
        status="active",
        tasks=result.tasks,
    )
    active = next(task for task in group.tasks if task.status == "active")

    canceled = cancel_parameter_group(
        group,
        expected_task_id=active.task_id,
        expected_task_version=active.task_version,
    )

    assert canceled.status == "canceled"
    assert all(
        task.status in {"completed", "skipped", "deferred", "canceled"}
        for task in canceled.tasks
    )
    assert next(
        task for task in canceled.tasks if task.task_id == active.task_id
    ).status == "canceled"

    with pytest.raises(ParameterTaskConflict, match="version"):
        cancel_parameter_group(
            group,
            expected_task_id=active.task_id,
            expected_task_version=active.task_version + 1,
        )


def test_parameter_group_configuration_is_refreshed_from_persisted_graph():
    result = ParameterTaskPlanner().plan(
        graph={"nodes": [_node("llm", "llmNode")], "edges": []},
        step_node_ids={"step_llm": "llm"},
        explicit_values={},
        upstream_candidates={},
        guidance_hints=[],
    )
    group = AgentBuilderParameterGroup(
        group_id=result.group_id,
        status="active",
        tasks=result.tasks,
    )

    refreshed = refresh_parameter_group_configuration(
        group,
        {
            "nodes": [
                _node(
                        "llm",
                        "llmNode",
                        {
                            "model_id": "gpt-safe",
                            "system_prompt": "Answer safely.",
                            "knowledgeBases": [],
                        },
                )
            ],
            "edges": [],
        },
    )

    assert refreshed is not None
    assert all(task.configuration_state == "resolved" for task in refreshed.tasks)


def test_acknowledge_parameter_binding_completes_matching_task_and_is_idempotent():
    group_id = uuid4()
    knowledge_task = AgentBuilderParameterTask(
        task_id=uuid4(),
        group_id=group_id,
        step_id="step_llm",
        node_id="llm",
        node_type="llmNode",
        parameter_key="knowledgeBases",
        label="Knowledge Base",
        input_type="knowledge_multi_select",
        required=False,
        status="active",
        task_version=1,
        stable_order=0,
        reason="Knowledge Base binding is optional.",
        input_guidance="Select zero or more Knowledge Bases.",
    )
    next_task = AgentBuilderParameterTask(
        task_id=uuid4(),
        group_id=group_id,
        step_id="step_llm",
        node_id="llm",
        node_type="llmNode",
        parameter_key="prompt",
        label="Prompt",
        input_type="text",
        required=True,
        status="pending",
        task_version=1,
        stable_order=1,
        reason="A prompt is required.",
        input_guidance="Enter a prompt.",
    )

    completed = acknowledge_parameter_binding(
        [knowledge_task, next_task],
        affected_node_ids={"llm"},
        parameter_key="knowledgeBases",
    )
    retried = acknowledge_parameter_binding(
        completed,
        affected_node_ids={"llm"},
        parameter_key="knowledgeBases",
    )

    assert completed[0].status == "completed"
    assert completed[0].task_version == 2
    assert completed[0].resolution_source == "user_request"
    assert completed[1].status == "active"
    assert retried == completed


@pytest.mark.parametrize("parameter_key", ["model_id", "fallback_model_id"])
def test_resource_reference_is_permission_checked_and_translated_for_runtime(
    monkeypatch,
    parameter_key,
):
    model_id = uuid4()
    option = SimpleNamespace(
        model=SimpleNamespace(
            id=model_id,
            model_id_for_api_call="gpt-safe-runtime-id",
        )
    )
    monkeypatch.setattr(
        LLMService,
        "get_agent_builder_model_option_groups",
        lambda *_args: [SimpleNamespace(options=[option])],
    )
    service = ParameterTaskService(
        SimpleNamespace(),
        user_id=uuid4(),
        organization_id=uuid4(),
    )
    task = SimpleNamespace(
        input_type="resource_ref",
        node_type="llmNode",
        parameter_key=parameter_key,
    )

    assert service._resolve_reference_value(task, model_id) == "gpt-safe-runtime-id"

    with pytest.raises(HTTPException) as exc:
        service._resolve_reference_value(task, uuid4())
    assert exc.value.status_code == 403


def test_unregistered_external_credential_reference_is_rejected():
    service = ParameterTaskService(
        SimpleNamespace(),
        user_id=uuid4(),
        organization_id=uuid4(),
    )
    task = SimpleNamespace(
        input_type="credential_ref",
        node_type="githubNode",
        parameter_key="credential",
    )

    with pytest.raises(HTTPException) as exc:
        service._resolve_reference_value(task, uuid4())
    assert exc.value.status_code == 400
    assert exc.value.detail == "invalid_decision"


def test_direct_set_value_reuses_fail_closed_secret_detector(monkeypatch):
    monkeypatch.setattr(
        "apps.gateway.application.agent_builder.parameter_tasks.TraceRedactionService.redact_payload",
        lambda *_args, **_kwargs: SimpleNamespace(
            failed=True,
            secret_detected=True,
        ),
    )

    with pytest.raises(ParameterTaskSafetyError, match="unsafe parameter value"):
        validate_direct_set_value(
            node_type="httpRequestNode",
            parameter_key="url",
            task_input_type="text",
            value="https://example.com/hook",
        )


def test_direct_set_value_rejects_secret_like_text_with_real_detector():
    secret_like_url = "https://example.invalid/hook?api_" + "key=fixture-value"

    with pytest.raises(ParameterTaskSafetyError, match="unsafe parameter value"):
        validate_direct_set_value(
            node_type="httpRequestNode",
            parameter_key="url",
            task_input_type="text",
            value=secret_like_url,
        )


@pytest.mark.parametrize(
    ("node_type", "parameter_key", "value"),
    [
        ("slackPostNode", "bot_token", "xoxb-user-supplied-value"),
        (
            "slackPostNode",
            "url",
            "https://hooks.slack.com/services/T000/B000/user-supplied",
        ),
        ("githubNode", "api_token", "github_pat_user_supplied_value"),
    ],
)
def test_direct_set_value_rejects_catalog_secret_from_parameter_decision(
    node_type,
    parameter_key,
    value,
):
    with pytest.raises(ParameterTaskSafetyError, match="secret_forbidden"):
        validate_direct_set_value(
            node_type=node_type,
            parameter_key=parameter_key,
            task_input_type="secret",
            value=value,
        )


def test_direct_set_value_rejects_null_llm_json_schema():
    assert validate_direct_set_value(
        node_type="llmNode",
        parameter_key="output_json_schema",
        task_input_type="json",
        value=None,
    ) == ["json_object_required"]


@pytest.mark.parametrize(
    "node_data",
    [
        {"workflowId": "", "appId": str(uuid4())},
        {"workflowId": str(uuid4()), "appId": "   "},
    ],
)
def test_workflow_node_pair_waits_until_both_references_are_configured(node_data):
    db = SimpleNamespace(
        query=lambda *_args: (_ for _ in ()).throw(
            AssertionError("incomplete pair must not query resources")
        )
    )
    service = ParameterTaskService(
        db,
        user_id=uuid4(),
        organization_id=uuid4(),
    )

    service._validate_workflow_node_pair(node_data)


@pytest.mark.parametrize("parameter_key", ["workflowId", "appId"])
def test_workflow_node_reference_replacement_preserves_pair_invariant(
    monkeypatch,
    parameter_key,
):
    group_id = uuid4()
    task = AgentBuilderParameterTask(
        task_id=uuid4(),
        group_id=group_id,
        step_id="step-workflow",
        node_id="workflow-node",
        node_type="workflowNode",
        parameter_key=parameter_key,
        label="Target",
        input_type="resource_ref",
        required=True,
        status="active",
        task_version=1,
        stable_order=0,
        reason="safe reason",
        input_guidance="safe guidance",
    )
    group = AgentBuilderParameterGroup(
        group_id=group_id,
        status="active",
        tasks=[task],
    )
    repository = AgentBuilderRepository()
    request_row = SimpleNamespace(response_payload={})
    repository.store_parameter_group(request_row, group)
    user_id = uuid4()
    organization_id = uuid4()
    parent_workflow_id = uuid4()
    selected_workflow_id = uuid4()
    canonical_workflow_id = uuid4()
    target_app_id = uuid4()
    session = SimpleNamespace(
        id=uuid4(),
        user_id=user_id,
        organization_id=organization_id,
        workflow_id=parent_workflow_id,
    )
    target_workflow = SimpleNamespace(
        id=(
            selected_workflow_id
            if parameter_key == "workflowId"
            else canonical_workflow_id
        ),
        organization_id=organization_id,
    )
    target_app = SimpleNamespace(
        id=target_app_id,
        organization_id=organization_id,
        workflow_id=canonical_workflow_id,
    )
    other_key = "appId" if parameter_key == "workflowId" else "workflowId"
    existing_value = (
        target_app_id if other_key == "appId" else selected_workflow_id
    )
    workflow = SimpleNamespace(
        id=parent_workflow_id,
        organization_id=organization_id,
        updated_at=datetime.now(timezone.utc),
        graph={
            "nodes": [
                _node("workflow-node", "workflowNode", {other_key: str(existing_value)})
            ],
            "edges": [],
        },
    )

    class _Query:
        def __init__(self, first=None, all_rows=None):
            self._first = first
            self._all = list(all_rows or [])

        def filter(self, *_args):
            return self

        def order_by(self, *_args):
            return self

        def with_for_update(self):
            return self

        def first(self):
            return self._first

        def all(self):
            return list(self._all)

    class _Db:
        def __init__(self):
            self.commits = 0
            self.workflow_queries = 0

        def query(self, model):
            if model is task_service_module.AgentBuilderSession:
                return _Query(first=session)
            if model is task_service_module.Workflow:
                self.workflow_queries += 1
                return _Query(
                    first=workflow if self.workflow_queries == 1 else target_workflow
                )
            if model is task_service_module.App:
                return _Query(first=target_app)
            if model is task_service_module.AgentBuilderRequest:
                return _Query(all_rows=[request_row])
            raise AssertionError(f"unexpected model: {model}")

        def commit(self):
            self.commits += 1

    db = _Db()
    audit_calls = []
    monkeypatch.setattr(
        task_service_module,
        "has_workflow_permission",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        task_service_module.AppService,
        "access_denial_status",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        task_service_module,
        "add_action_audit",
        lambda *_args, **_kwargs: audit_calls.append((_args, _kwargs)),
    )
    service = ParameterTaskService(
        db,
        user_id=user_id,
        organization_id=organization_id,
        repository=repository,
    )
    monkeypatch.setattr(
        service,
        "_validated_decision_value",
        lambda _task, _payload: str(
            selected_workflow_id
            if parameter_key == "workflowId"
            else target_app_id
        ),
    )
    payload = AgentBuilderParameterTaskDecisionRequest.model_validate(
        {
            "operation_id": str(uuid4()),
            "expected_task_version": task.task_version,
            "action": "set",
            "value": {"kind": "resource_ref", "resource_id": str(uuid4())},
        }
    )

    if parameter_key == "workflowId":
        with pytest.raises(HTTPException) as exc:
            service.decide(session.id, task.task_id, payload)

        assert exc.value.status_code == 400
        assert exc.value.detail == "invalid_decision"
        assert repository.load_parameter_group(request_row, group_id) == group
        assert request_row.response_payload.get("operation_envelopes") is None
        assert audit_calls == []
        assert db.commits == 0
        return

    response = service.decide(session.id, task.task_id, payload)

    assert response.awaiting_persistence_ack is True
    assert response.graph_mutation is not None
    operation = response.graph_mutation.operations[0]
    assert operation.op == "replace_node_data"
    assert operation.data["appId"] == str(target_app_id)
    assert operation.data["workflowId"] == str(canonical_workflow_id)
    assert len(request_row.response_payload["operation_envelopes"]) == 1
    assert len(audit_calls) == 1
    assert db.commits == 1


@pytest.mark.parametrize("denied_resource", ["app", "workflow"])
def test_workflow_node_app_normalization_requires_pair_permissions(
    monkeypatch,
    denied_resource,
):
    organization_id = uuid4()
    app_id = uuid4()
    workflow_id = uuid4()
    app = SimpleNamespace(
        id=app_id,
        organization_id=organization_id,
        workflow_id=workflow_id,
    )
    workflow = SimpleNamespace(
        id=workflow_id,
        organization_id=organization_id,
    )

    class _Query:
        def __init__(self, result):
            self.result = result

        def filter(self, *_args):
            return self

        def first(self):
            return self.result

    db = SimpleNamespace(
        query=lambda model: _Query(
            app if model is task_service_module.App else workflow
        )
    )
    monkeypatch.setattr(
        task_service_module.AppService,
        "access_denial_status",
        lambda *_args, **_kwargs: 403 if denied_resource == "app" else None,
    )
    monkeypatch.setattr(
        task_service_module,
        "has_workflow_permission",
        lambda *_args, **_kwargs: denied_resource != "workflow",
    )
    service = ParameterTaskService(
        db,
        user_id=uuid4(),
        organization_id=organization_id,
    )
    node_data = {"appId": str(app_id), "workflowId": str(uuid4())}

    with pytest.raises(HTTPException) as exc:
        service._normalize_workflow_node_pair(
            node_data,
            changed_parameter_key="appId",
        )

    assert exc.value.status_code == 403
    assert exc.value.detail == "permission_denied"
    assert node_data["workflowId"] != str(workflow_id)


def test_gmail_draft_rejects_non_gmail_oauth_credential_reference(monkeypatch):
    credential_id = uuid4()
    credential = SimpleNamespace(
        id=credential_id,
        provider="gmail",
        auth_type="app_password",
    )

    class _Query:
        def filter(self, *_args):
            return self

        def first(self):
            return credential

    db = SimpleNamespace(query=lambda *_args: _Query())
    monkeypatch.setattr(
        task_service_module,
        "has_mail_credential_permission",
        lambda *_args, **_kwargs: True,
    )
    service = ParameterTaskService(
        db,
        user_id=uuid4(),
        organization_id=uuid4(),
    )
    task = SimpleNamespace(
        input_type="credential_ref",
        node_type="gmailDraftNode",
        parameter_key="credential_id",
    )

    with pytest.raises(HTTPException) as exc:
        service._resolve_reference_value(task, credential_id)

    assert exc.value.status_code == 400
    assert exc.value.detail == "invalid_decision"


def test_credential_decision_uses_credential_id_not_resource_id():
    credential_id = uuid4()

    parsed = AgentBuilderParameterTaskDecisionRequest.model_validate(
        {
            "operation_id": str(uuid4()),
            "expected_task_version": 1,
            "action": "set",
            "value": {
                "kind": "credential_ref",
                "credential_id": str(credential_id),
            },
        }
    )

    assert parsed.value.credential_id == credential_id
    with pytest.raises(ValueError):
        AgentBuilderParameterTaskDecisionRequest.model_validate(
            {
                "operation_id": str(uuid4()),
                "expected_task_version": 1,
                "action": "set",
                "value": {
                    "kind": "credential_ref",
                    "resource_id": str(credential_id),
                },
            }
        )


def test_select_decision_uses_public_discriminator():
    parsed = AgentBuilderParameterTaskDecisionRequest.model_validate(
        {
            "operation_id": str(uuid4()),
            "expected_task_version": 1,
            "action": "set",
            "value": {"kind": "select", "value": "mini"},
        }
    )

    assert parsed.value.kind == "select"
    assert parsed.value.value == "mini"


def test_unregistered_credential_decision_cannot_bypass_reference_resolver():
    service = ParameterTaskService(
        SimpleNamespace(),
        user_id=uuid4(),
        organization_id=uuid4(),
    )
    task = SimpleNamespace(
        input_type="credential_ref",
        node_type="githubNode",
        parameter_key="credential",
    )
    payload = AgentBuilderParameterTaskDecisionRequest.model_validate(
        {
            "operation_id": str(uuid4()),
            "expected_task_version": 1,
            "action": "set",
            "value": {
                "kind": "credential_ref",
                "credential_id": str(uuid4()),
            },
        }
    )

    with pytest.raises(HTTPException) as exc:
        service._validated_decision_value(task, payload)
    assert exc.value.status_code == 400
    assert exc.value.detail == "invalid_decision"


def test_slack_channel_parameter_updates_runtime_request_body():
    data = {
        "body": '{"text":"{{result}}"}',
        "channel": "",
        "configuration_state": "unresolved",
    }

    updated = apply_parameter_value_to_node_data(
        "slackPostNode",
        "channel",
        data,
        "C123",
    )

    assert updated["channel"] == "C123"
    assert '"channel": "C123"' in updated["body"]


def test_slack_channel_parameter_candidate_passes_workflow_storage_boundary():
    data = apply_parameter_value_to_node_data(
        "slackPostNode",
        "channel",
        {
            "body": '{"text":"{{result}}"}',
            "channel": "",
            "configuration_state": "unresolved",
        },
        "C123",
    )
    data["_deferred_parameters"] = []

    WorkflowService.validate_external_node_storage_boundaries(
        {
            "nodes": [_node("slack", "slackPostNode", data)],
            "edges": [],
        },
        require_resolved=False,
    )


def test_github_pr_number_parameter_matches_runtime_schema_across_json_round_trip():
    decision = AgentBuilderParameterTaskDecisionRequest.model_validate(
        {
            "operation_id": str(uuid4()),
            "expected_task_version": 1,
            "action": "set",
            "value": {"kind": "number", "value": 15},
        }
    )
    assert decision.value is not None
    data = apply_parameter_value_to_node_data(
        "githubNode",
        "pr_number",
        {
            "title": "GitHub PR",
            "action": "get_pr",
            "api_token": "test-token",
            "repo_owner": "octo",
            "repo_name": "repo",
            "pr_number": "",
            "referenced_variables": [],
            "configuration_state": "unresolved",
        },
        decision.value.value,
    )
    graph = {
        "nodes": [_node("github", "githubNode", data)],
        "edges": [],
    }
    browser_round_trip = json.loads(json.dumps(graph))

    assert data["pr_number"] == "15"
    GithubNodeData.model_validate(data)
    assert canonical_graph_hash(graph) == canonical_graph_hash(browser_round_trip)


def test_github_comment_body_is_required_only_for_comment_action():
    planner = ParameterTaskPlanner()
    comment_plan = planner.plan(
        graph={
            "nodes": [_node("github", "githubNode")],
            "edges": [],
        },
        step_node_ids={"step_github": "github"},
        explicit_values={("step_github", "action"): "comment_pr"},
        upstream_candidates={},
        guidance_hints=[],
    )
    read_plan = planner.plan(
        graph={
            "nodes": [_node("github", "githubNode")],
            "edges": [],
        },
        step_node_ids={"step_github": "github"},
        explicit_values={("step_github", "action"): "get_pr"},
        upstream_candidates={},
        guidance_hints=[],
    )

    comment_task = next(
        task for task in comment_plan.tasks if task.parameter_key == "comment_body"
    )
    read_task = next(
        task for task in read_plan.tasks if task.parameter_key == "comment_body"
    )
    assert (comment_task.required, comment_task.status) == (True, "pending")
    assert (read_task.required, read_task.status) == (False, "skipped")


def test_graph_ack_refresh_reopens_comment_body_after_github_action_changes():
    planner = ParameterTaskPlanner()
    read_plan = planner.plan(
        graph={
            "nodes": [
                _node(
                    "github",
                    "githubNode",
                    {
                        "action": "get_pr",
                        "api_token": "configured",
                        "repo_owner": "octo",
                        "repo_name": "repo",
                        "pr_number": "15",
                    },
                )
            ],
            "edges": [],
        },
        step_node_ids={"step_github": "github"},
        explicit_values={},
        upstream_candidates={},
        guidance_hints=[],
        base_node_ids={"github"},
    )
    completed_read_group = AgentBuilderParameterGroup(
        group_id=read_plan.group_id,
        status="completed",
        tasks=[
            task.model_copy(
                update={
                    "status": (
                        "skipped"
                        if task.parameter_key == "comment_body"
                        else "completed"
                    )
                }
            )
            for task in read_plan.tasks
        ],
    )
    comment_graph = {
        "nodes": [
            _node(
                "github",
                "githubNode",
                {
                    "action": "comment_pr",
                    "api_token": "configured",
                    "repo_owner": "octo",
                    "repo_name": "repo",
                    "pr_number": "15",
                },
            )
        ],
        "edges": [],
    }

    refreshed = _refresh_parameter_group(completed_read_group, comment_graph)

    comment_task = next(
        task for task in refreshed.tasks if task.parameter_key == "comment_body"
    )
    assert comment_task.required is True
    assert comment_task.status == "active"
    assert refreshed.status == "active"


def test_file_extraction_selector_parameter_matches_runtime_schema():
    data = apply_parameter_value_to_node_data(
        "fileExtractionNode",
        "referenced_variables",
        {
            "title": "Document extraction",
            "referenced_variables": [],
            "configuration_state": "unresolved",
        },
        ["webhook", "file"],
    )

    assert data["referenced_variables"] == [
        {"name": "file", "value_selector": ["webhook", "file"]}
    ]
    FileExtractionNodeData.model_validate(data)


def test_deferred_mail_credential_passes_workflow_storage_boundary():
    WorkflowService.validate_external_node_storage_boundaries(
        {
            "nodes": [
                _node(
                    "mail",
                    "mailNode",
                    {
                        "credential_id": None,
                        "configuration_state": "unresolved",
                        "_deferred_parameters": ["credential_id"],
                    },
                )
            ],
            "edges": [],
        },
        require_resolved=False,
    )


def test_downstream_selector_is_recomputed_and_reactivated_when_source_output_changes():
    graph = {
        "nodes": [
            _node(
                "extract",
                "variableExtractionNode",
                {"mappings": [{"name": "new_name", "json_path": "$.name"}]},
            ),
            _node(
                "llm",
                "llmNode",
                {"knowledgeBases": ["extract", "old_name"], "model_id": "model"},
            ),
        ],
        "edges": [{"id": "e1", "source": "extract", "target": "llm"}],
    }
    planned = ParameterTaskPlanner().plan(
        graph=graph,
        step_node_ids={"step_extract": "extract", "step_llm": "llm"},
        explicit_values={},
        upstream_candidates={},
        guidance_hints=[],
    )
    selector_task = next(
        task
        for task in planned.tasks
        if task.node_id == "llm" and task.parameter_key == "knowledgeBases"
    ).model_copy(
        update={
            "input_type": "variable_selector",
            "status": "completed",
            "resolution_source": "upstream_selector",
        }
    )
    group = AgentBuilderParameterGroup(
        group_id=planned.group_id,
        status="active",
        tasks=[selector_task],
    )

    refreshed = refresh_parameter_group_suggestions(group, graph)

    assert refreshed.tasks[0].status == "active"
    assert refreshed.tasks[0].task_version == selector_task.task_version + 1
    assert [item.output_key for item in refreshed.tasks[0].suggestions] == ["new_name"]
    assert graph["nodes"][1]["data"]["knowledgeBases"] == [
        "extract",
        "old_name",
    ]


def test_parameter_candidate_provider_returns_only_permission_filtered_safe_refs(
    monkeypatch,
):
    allowed_id = uuid4()
    denied_id = uuid4()
    credentials = [
        SimpleNamespace(
            id=allowed_id,
            credential_name="업무 메일",
            provider="gmail",
            encrypted_secret="must-not-leak",
        ),
        SimpleNamespace(
            id=denied_id,
            credential_name="제한 메일",
            provider="imap",
            encrypted_secret="must-not-leak",
        ),
    ]

    class _CandidateQuery:
        def filter(self, *_args):
            return self

        def all(self):
            return credentials

    db = SimpleNamespace(query=lambda *_args: _CandidateQuery())
    monkeypatch.setattr(
        candidate_module,
        "has_mail_credential_permission",
        lambda _db, _user_id, credential_id, *_args, **_kwargs: (
            credential_id == allowed_id
        ),
    )
    task = SimpleNamespace(
        input_type="credential_ref",
        node_type="mailNode",
        parameter_key="credential_id",
    )

    candidates = ParameterCandidateProvider(
        db,
        user_id=uuid4(),
        organization_id=uuid4(),
    ).for_task(task)

    assert [candidate.candidate_id for candidate in candidates] == [allowed_id]
    assert candidates[0].kind == "credential_ref"
    assert candidates[0].reference_value is None
    assert "secret" not in candidates[0].model_dump_json()


def test_gmail_draft_candidates_only_include_gmail_oauth2_credentials(monkeypatch):
    gmail_oauth_id = uuid4()
    credentials = [
        SimpleNamespace(
            id=gmail_oauth_id,
            credential_name="Gmail OAuth",
            provider="gmail",
            auth_type="oauth2",
        ),
        SimpleNamespace(
            id=uuid4(),
            credential_name="Gmail app password",
            provider="gmail",
            auth_type="app_password",
        ),
        SimpleNamespace(
            id=uuid4(),
            credential_name="Outlook OAuth",
            provider="outlook",
            auth_type="oauth2",
        ),
    ]

    class _CandidateQuery:
        def filter(self, *_args):
            return self

        def all(self):
            return credentials

    db = SimpleNamespace(query=lambda *_args: _CandidateQuery())
    monkeypatch.setattr(
        candidate_module,
        "has_mail_credential_permission",
        lambda *_args, **_kwargs: True,
    )
    task = SimpleNamespace(
        input_type="credential_ref",
        node_type="gmailDraftNode",
        parameter_key="credential_id",
    )

    candidates = ParameterCandidateProvider(
        db,
        user_id=uuid4(),
        organization_id=uuid4(),
    ).for_task(task)

    assert [candidate.candidate_id for candidate in candidates] == [gmail_oauth_id]


@pytest.mark.parametrize("parameter_key", ["model_id", "fallback_model_id"])
def test_model_candidate_exposes_safe_runtime_reference_value_only(
    monkeypatch, parameter_key
):
    model_id = uuid4()
    option = SimpleNamespace(
        model=SimpleNamespace(
            id=model_id,
            name="Safe model",
            model_id_for_api_call="provider-safe-model-id",
        )
    )
    monkeypatch.setattr(
        LLMService,
        "get_agent_builder_model_option_groups",
        lambda *_args: [
            SimpleNamespace(provider_name="Safe provider", options=[option])
        ],
    )
    provider = ParameterCandidateProvider(
        SimpleNamespace(),
        user_id=uuid4(),
        organization_id=uuid4(),
    )
    task = SimpleNamespace(
        input_type="resource_ref",
        node_type="llmNode",
        parameter_key=parameter_key,
    )

    candidates = provider.for_task(task)

    assert len(candidates) == 1
    assert candidates[0].candidate_id == model_id
    assert candidates[0].reference_value == "provider-safe-model-id"
    assert "provider-safe-model-id" in candidates[0].model_dump_json()


def test_hidden_fallback_model_is_not_exposed_as_an_agent_builder_candidate_task(
    monkeypatch,
):
    primary_id = uuid4()
    fallback_id = uuid4()
    options = [
        SimpleNamespace(
            model=SimpleNamespace(
                id=primary_id,
                name="Primary model",
                model_id_for_api_call="gpt-5.4",
            )
        ),
        SimpleNamespace(
            model=SimpleNamespace(
                id=fallback_id,
                name="Fallback model",
                model_id_for_api_call="gpt-5-mini",
            )
        ),
    ]
    monkeypatch.setattr(
        LLMService,
        "get_agent_builder_model_option_groups",
        lambda *_args: [
            SimpleNamespace(provider_name="Safe provider", options=options)
        ],
    )
    plan = ParameterTaskPlanner().plan(
        graph={
            "nodes": [_node("llm", "llmNode", {"model_id": "gpt-5.4"})],
            "edges": [],
        },
        step_node_ids={"step_llm": "llm"},
        explicit_values={},
        upstream_candidates={},
        guidance_hints=[],
    )
    group = AgentBuilderParameterGroup(
        group_id=plan.group_id,
        status="active",
        tasks=plan.tasks,
    )

    enriched = ParameterCandidateProvider(
        SimpleNamespace(),
        user_id=uuid4(),
        organization_id=uuid4(),
    ).enrich_group(group, graph=plan.graph)

    assert enriched is not None
    model_task = next(
        task for task in enriched.tasks if task.parameter_key == "model_id"
    )
    assert [candidate.candidate_id for candidate in model_task.candidates] == [
        primary_id,
        fallback_id,
    ]
    assert all(
        task.parameter_key != "fallback_model_id" for task in enriched.tasks
    )


def test_provider_scoped_credential_candidates_keep_slack_and_github_empty(
    monkeypatch,
):
    mail_id = uuid4()
    credential = SimpleNamespace(
        id=mail_id,
        credential_name="mail-safe",
        provider="gmail",
        status="active",
    )

    class _CandidateQuery:
        def filter(self, *_args):
            return self

        def all(self):
            return [credential]

    db = SimpleNamespace(query=lambda *_args: _CandidateQuery())
    monkeypatch.setattr(
        candidate_module,
        "has_mail_credential_permission",
        lambda *_args, **_kwargs: True,
    )
    provider = ParameterCandidateProvider(
        db,
        user_id=uuid4(),
        organization_id=uuid4(),
    )
    base = {
        "task_id": uuid4(),
        "group_id": uuid4(),
        "step_id": "step",
        "node_id": "node",
        "parameter_key": "credential",
        "label": "Credential",
        "input_type": "credential_ref",
        "required": True,
        "status": "active",
        "task_version": 1,
        "stable_order": 0,
        "reason": "safe reason",
        "input_guidance": "safe guidance",
    }
    slack_task = AgentBuilderParameterTask(
        **base,
        node_type="slackPostNode",
        defer_policy="allow_unresolved",
    )
    github_task = slack_task.model_copy(update={"node_type": "githubNode"})
    mail_task = slack_task.model_copy(
        update={
            "node_type": "mailNode",
            "parameter_key": "credential_id",
        }
    )

    assert provider.for_task(slack_task) == []
    assert provider.for_task(github_task) == []
    assert [candidate.candidate_id for candidate in provider.for_task(mail_task)] == [
        mail_id
    ]
    assert slack_task.defer_policy == "allow_unresolved"
    assert github_task.defer_policy == "allow_unresolved"


def test_parameter_recovery_marks_permission_lost_reference_unavailable_without_leak(
    monkeypatch,
):
    denied_id = uuid4()
    denied_label = "접근 해제된 메일"
    credential = SimpleNamespace(
        id=denied_id,
        credential_name=denied_label,
        provider="gmail",
        status="active",
        encrypted_secret="must-not-leak",
    )

    class _CandidateQuery:
        def filter(self, *_args):
            return self

        def all(self):
            return [credential]

    db = SimpleNamespace(query=lambda *_args: _CandidateQuery())
    monkeypatch.setattr(
        candidate_module,
        "has_mail_credential_permission",
        lambda *_args, **_kwargs: False,
    )
    group_id = uuid4()
    task = AgentBuilderParameterTask(
        task_id=uuid4(),
        group_id=group_id,
        step_id="step-mail",
        node_id="mail",
        node_type="mailNode",
        parameter_key="credential_id",
        label="Credential",
        input_type="credential_ref",
        required=True,
        status="completed",
        task_version=2,
        stable_order=0,
        reason="safe reason",
        input_guidance="safe guidance",
        configuration_state="resolved",
    )
    canonical_group = AgentBuilderParameterGroup(
        group_id=group_id,
        status="completed",
        tasks=[task],
    )
    graph = {
        "nodes": [
            _node("mail", "mailNode", {"credential_id": str(denied_id)})
        ],
        "edges": [],
    }

    recovered = ParameterCandidateProvider(
        db,
        user_id=uuid4(),
        organization_id=uuid4(),
    ).enrich_group(canonical_group, graph=graph)

    assert canonical_group.tasks[0].status == "completed"
    assert graph["nodes"][0]["data"]["credential_id"] == str(denied_id)
    assert recovered.tasks[0].status == "invalid"
    assert recovered.tasks[0].configuration_state == "unresolved"
    assert recovered.tasks[0].candidates == []
    serialized = recovered.model_dump_json()
    assert str(denied_id) not in serialized
    assert denied_label not in serialized
    assert "must-not-leak" not in serialized


def test_parameter_recovery_marks_permission_lost_resource_unavailable_without_id(
    monkeypatch,
):
    denied_id = uuid4()
    workflow = SimpleNamespace(id=denied_id)

    class _CandidateQuery:
        def filter(self, *_args):
            return self

        def all(self):
            return [workflow]

    db = SimpleNamespace(query=lambda *_args: _CandidateQuery())
    monkeypatch.setattr(
        candidate_module,
        "has_workflow_permission",
        lambda *_args, **_kwargs: False,
    )
    group_id = uuid4()
    task = AgentBuilderParameterTask(
        task_id=uuid4(),
        group_id=group_id,
        step_id="step-workflow",
        node_id="workflow",
        node_type="workflowNode",
        parameter_key="workflowId",
        label="Workflow",
        input_type="resource_ref",
        required=True,
        status="completed",
        task_version=2,
        stable_order=0,
        reason="safe reason",
        input_guidance="safe guidance",
        configuration_state="resolved",
    )
    canonical_group = AgentBuilderParameterGroup(
        group_id=group_id,
        status="completed",
        tasks=[task],
    )
    graph = {
        "nodes": [
            _node("workflow", "workflowNode", {"workflowId": str(denied_id)})
        ],
        "edges": [],
    }

    recovered = ParameterCandidateProvider(
        db,
        user_id=uuid4(),
        organization_id=uuid4(),
    ).enrich_group(canonical_group, graph=graph)

    assert recovered.tasks[0].status == "invalid"
    assert recovered.tasks[0].configuration_state == "unresolved"
    assert recovered.tasks[0].candidates == []
    assert str(denied_id) not in recovered.model_dump_json()
    assert canonical_group.tasks[0].status == "completed"


def test_parameter_recovery_preserves_completed_authorized_reference(monkeypatch):
    allowed_id = uuid4()
    credential = SimpleNamespace(
        id=allowed_id,
        credential_name="업무 메일",
        provider="gmail",
        status="active",
    )

    class _CandidateQuery:
        def filter(self, *_args):
            return self

        def all(self):
            return [credential]

    db = SimpleNamespace(query=lambda *_args: _CandidateQuery())
    monkeypatch.setattr(
        candidate_module,
        "has_mail_credential_permission",
        lambda *_args, **_kwargs: True,
    )
    group_id = uuid4()
    task = AgentBuilderParameterTask(
        task_id=uuid4(),
        group_id=group_id,
        step_id="step-mail",
        node_id="mail",
        node_type="mailNode",
        parameter_key="credential_id",
        label="Credential",
        input_type="credential_ref",
        required=True,
        status="completed",
        task_version=2,
        stable_order=0,
        reason="safe reason",
        input_guidance="safe guidance",
        configuration_state="resolved",
    )
    canonical_group = AgentBuilderParameterGroup(
        group_id=group_id,
        status="completed",
        tasks=[task],
    )
    graph = {
        "nodes": [
            _node("mail", "mailNode", {"credential_id": str(allowed_id)})
        ],
        "edges": [],
    }

    recovered = ParameterCandidateProvider(
        db,
        user_id=uuid4(),
        organization_id=uuid4(),
    ).enrich_group(canonical_group, graph=graph)

    assert recovered.tasks[0].status == "completed"
    assert recovered.tasks[0].configuration_state == "resolved"
    assert [item.candidate_id for item in recovered.tasks[0].candidates] == [
        allowed_id
    ]
    assert canonical_group.tasks[0].status == "completed"


def test_local_task_decision_operation_is_idempotent_and_payload_bound():
    repository = AgentBuilderRepository()
    request_row = SimpleNamespace(response_payload={})
    operation_id = uuid4()
    task_id = uuid4()

    repository.store_local_task_decision(
        request_row,
        operation_id=operation_id,
        task_id=task_id,
        action="skip",
    )
    repository.store_local_task_decision(
        request_row,
        operation_id=operation_id,
        task_id=task_id,
        action="skip",
    )
    assert repository.find_local_task_decision(request_row, operation_id) == {
        "operation_id": str(operation_id),
        "task_id": str(task_id),
        "action": "skip",
    }

    with pytest.raises(AgentBuilderRepositoryError):
        repository.store_local_task_decision(
            request_row,
            operation_id=operation_id,
            task_id=task_id,
            action="previous",
        )


def test_confirm_auto_resolved_task_is_idempotent_and_never_issues_graph_mutation(
    monkeypatch,
):
    group_id = uuid4()
    auto_task = AgentBuilderParameterTask(
        task_id=uuid4(),
        group_id=group_id,
        step_id="step-schedule",
        node_id="schedule",
        node_type="scheduleTrigger",
        parameter_key="timezone",
        label="Timezone",
        input_type="select",
        required=True,
        status="active",
        task_version=2,
        stable_order=0,
        resolution_source="catalog_default",
        recommendation_fingerprint=canonical_parameter_value_fingerprint(
            "Asia/Seoul"
        ),
        reason="safe reason",
        input_guidance="safe guidance",
    )
    next_task = auto_task.model_copy(
        update={
            "task_id": uuid4(),
            "parameter_key": "cron_expression",
            "label": "Cron",
            "input_type": "text",
            "status": "pending",
            "task_version": 1,
            "stable_order": 1,
            "resolution_source": None,
        }
    )
    group = AgentBuilderParameterGroup(
        group_id=group_id,
        status="active",
        tasks=[auto_task, next_task],
    )
    repository = AgentBuilderRepository()
    request_row = SimpleNamespace(response_payload={})
    repository.store_parameter_group(request_row, group)
    user_id = uuid4()
    organization_id = uuid4()
    workflow_id = uuid4()
    session = SimpleNamespace(
        id=uuid4(),
        user_id=user_id,
        organization_id=organization_id,
        workflow_id=workflow_id,
    )
    workflow = SimpleNamespace(
        id=workflow_id,
        organization_id=organization_id,
        graph={
            "nodes": [
                _node(
                    "schedule",
                    "scheduleTrigger",
                    {"timezone": "Asia/Seoul"},
                )
            ],
            "edges": [],
        },
    )

    locked_models = []

    class _Query:
        def __init__(self, model, *, first=None, all_rows=None):
            self._model = model
            self._first = first
            self._all = list(all_rows or [])

        def filter(self, *_args):
            return self

        def order_by(self, *_args):
            return self

        def with_for_update(self):
            locked_models.append(self._model)
            return self

        def first(self):
            return self._first

        def all(self):
            return list(self._all)

    class _Db:
        def __init__(self):
            self.commits = 0

        def query(self, model):
            if model is task_service_module.AgentBuilderSession:
                return _Query(model, first=session)
            if model is task_service_module.Workflow:
                return _Query(model, first=workflow)
            if model is task_service_module.AgentBuilderRequest:
                return _Query(model, all_rows=[request_row])
            raise AssertionError(f"unexpected model: {model}")

        def commit(self):
            self.commits += 1

    db = _Db()
    audit_calls = []
    monkeypatch.setattr(
        task_service_module,
        "has_workflow_permission",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        task_service_module,
        "add_action_audit",
        lambda *_args, **_kwargs: audit_calls.append((_args, _kwargs)),
    )
    service = ParameterTaskService(
        db,
        user_id=user_id,
        organization_id=organization_id,
        repository=repository,
    )
    operation_id = uuid4()
    payload = AgentBuilderParameterTaskDecisionRequest.model_validate(
        {
            "operation_id": operation_id,
            "expected_task_version": auto_task.task_version,
            "action": "confirm",
        }
    )

    first = service.decide(session.id, auto_task.task_id, payload)
    second = service.decide(session.id, auto_task.task_id, payload)
    conflicting_payload = AgentBuilderParameterTaskDecisionRequest.model_validate(
        {
            "operation_id": operation_id,
            "expected_task_version": auto_task.task_version + 1,
            "action": "confirm",
        }
    )

    with pytest.raises(HTTPException) as conflict:
        service.decide(session.id, auto_task.task_id, conflicting_payload)

    assert second == first
    assert first.task.status == "completed"
    assert first.task.task_version == auto_task.task_version + 1
    assert first.graph_mutation is None
    assert first.awaiting_persistence_ack is False
    assert first.next_task_id == next_task.task_id
    assert locked_models[:2] == [
        task_service_module.Workflow,
        task_service_module.AgentBuilderRequest,
    ]
    assert request_row.response_payload.get("operation_envelopes") is None
    assert len(audit_calls) == 1
    assert db.commits == 1
    assert conflict.value.status_code == 409
    assert conflict.value.detail == "task_conflict"


def test_pending_structural_group_rejects_decision_until_acknowledgement(monkeypatch):
    group_id = uuid4()
    task = AgentBuilderParameterTask(
        task_id=uuid4(),
        group_id=group_id,
        step_id="step-schedule",
        node_id="schedule",
        node_type="scheduleTrigger",
        parameter_key="cron_expression",
        label="Cron",
        input_type="text",
        required=True,
        status="pending",
        task_version=1,
        stable_order=0,
        reason="safe reason",
        input_guidance="safe guidance",
    )
    group = AgentBuilderParameterGroup(
        group_id=group_id,
        status="pending_save",
        tasks=[task],
    )
    repository = AgentBuilderRepository()
    request_row = SimpleNamespace(response_payload={})
    repository.store_parameter_group(request_row, group)
    user_id = uuid4()
    organization_id = uuid4()
    workflow_id = uuid4()
    session = SimpleNamespace(
        id=uuid4(),
        user_id=user_id,
        organization_id=organization_id,
        workflow_id=workflow_id,
    )
    workflow = SimpleNamespace(
        id=workflow_id,
        organization_id=organization_id,
        graph={"nodes": [_node("schedule", "scheduleTrigger", {})], "edges": []},
        updated_at=datetime.now(timezone.utc),
    )

    class _Query:
        def __init__(self, *, first=None, all_rows=None):
            self._first = first
            self._all = list(all_rows or [])

        def filter(self, *_args):
            return self

        def order_by(self, *_args):
            return self

        def with_for_update(self):
            return self

        def first(self):
            return self._first

        def all(self):
            return list(self._all)

    class _Db:
        def __init__(self):
            self.commits = 0

        def query(self, model):
            if model is task_service_module.AgentBuilderSession:
                return _Query(first=session)
            if model is task_service_module.Workflow:
                return _Query(first=workflow)
            if model is task_service_module.AgentBuilderRequest:
                return _Query(all_rows=[request_row])
            raise AssertionError(f"unexpected model: {model}")

        def commit(self):
            self.commits += 1

    db = _Db()
    audit_calls = []
    monkeypatch.setattr(
        task_service_module,
        "has_workflow_permission",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        task_service_module,
        "add_action_audit",
        lambda *_args, **_kwargs: audit_calls.append((_args, _kwargs)),
    )
    service = ParameterTaskService(
        db,
        user_id=user_id,
        organization_id=organization_id,
        repository=repository,
    )
    payload = AgentBuilderParameterTaskDecisionRequest.model_validate(
        {
            "operation_id": str(uuid4()),
            "expected_task_version": task.task_version,
            "action": "set",
            "value": {"kind": "text", "value": "0 9 * * *"},
        }
    )

    with pytest.raises(HTTPException) as conflict:
        service.decide(session.id, task.task_id, payload)

    assert conflict.value.status_code == 409
    assert conflict.value.detail == "task_conflict"
    assert repository.load_parameter_group(request_row, group_id) == group
    assert audit_calls == []
    assert db.commits == 0


def test_invalid_set_decision_is_idempotent_and_payload_bound(monkeypatch):
    group_id = uuid4()
    task = AgentBuilderParameterTask(
        task_id=uuid4(),
        group_id=group_id,
        step_id="step-schedule",
        node_id="schedule",
        node_type="scheduleTrigger",
        parameter_key="cron_expression",
        label="Cron",
        input_type="text",
        required=True,
        status="active",
        task_version=1,
        stable_order=0,
        reason="safe reason",
        input_guidance="safe guidance",
    )
    group = AgentBuilderParameterGroup(
        group_id=group_id,
        status="active",
        tasks=[task],
    )
    repository = AgentBuilderRepository()
    request_row = SimpleNamespace(response_payload={})
    repository.store_parameter_group(request_row, group)
    user_id = uuid4()
    organization_id = uuid4()
    workflow_id = uuid4()
    session = SimpleNamespace(
        id=uuid4(),
        user_id=user_id,
        organization_id=organization_id,
        workflow_id=workflow_id,
    )
    workflow = SimpleNamespace(
        id=workflow_id,
        organization_id=organization_id,
        graph={"nodes": [_node("schedule", "scheduleTrigger", {})], "edges": []},
        updated_at=datetime.now(timezone.utc),
    )

    class _Query:
        def __init__(self, *, first=None, all_rows=None):
            self._first = first
            self._all = list(all_rows or [])

        def filter(self, *_args):
            return self

        def order_by(self, *_args):
            return self

        def with_for_update(self):
            return self

        def first(self):
            return self._first

        def all(self):
            return list(self._all)

    class _Db:
        def __init__(self):
            self.commits = 0

        def query(self, model):
            if model is task_service_module.AgentBuilderSession:
                return _Query(first=session)
            if model is task_service_module.Workflow:
                return _Query(first=workflow)
            if model is task_service_module.AgentBuilderRequest:
                return _Query(all_rows=[request_row])
            raise AssertionError(f"unexpected model: {model}")

        def commit(self):
            self.commits += 1

    db = _Db()
    audit_calls = []
    monkeypatch.setattr(
        task_service_module,
        "has_workflow_permission",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        task_service_module,
        "add_action_audit",
        lambda *_args, **_kwargs: audit_calls.append((_args, _kwargs)),
    )
    service = ParameterTaskService(
        db,
        user_id=user_id,
        organization_id=organization_id,
        repository=repository,
    )
    operation_id = uuid4()
    payload = AgentBuilderParameterTaskDecisionRequest.model_validate(
        {
            "operation_id": str(operation_id),
            "expected_task_version": task.task_version,
            "action": "set",
            "value": {"kind": "text", "value": ""},
        }
    )
    conflicting_payload = AgentBuilderParameterTaskDecisionRequest.model_validate(
        {
            "operation_id": str(operation_id),
            "expected_task_version": task.task_version,
            "action": "set",
            "value": {"kind": "text", "value": "0 9 * * *"},
        }
    )

    first = service.decide(session.id, task.task_id, payload)
    second = service.decide(session.id, task.task_id, payload)
    with pytest.raises(HTTPException) as conflict:
        service.decide(session.id, task.task_id, conflicting_payload)

    assert second == first
    assert first.task.status == "invalid"
    assert first.task.task_version == task.task_version + 1
    assert first.validation_issues == [
        {"code": "too_short", "parameter_key": "cron_expression"}
    ]
    assert conflict.value.status_code == 409
    assert conflict.value.detail == "task_conflict"
    assert len(audit_calls) == 1
    assert db.commits == 1


def test_set_retry_with_same_operation_id_and_different_value_conflicts_before_audit(
    monkeypatch,
):
    group_id = uuid4()
    task = AgentBuilderParameterTask(
        task_id=uuid4(),
        group_id=group_id,
        step_id="step-answer",
        node_id="answer",
        node_type="answerNode",
        parameter_key="outputs",
        label="Outputs",
        input_type="json",
        required=True,
        status="active",
        task_version=1,
        stable_order=0,
        reason="safe reason",
        input_guidance="safe guidance",
    )
    group = AgentBuilderParameterGroup(
        group_id=group_id,
        status="active",
        tasks=[task],
    )
    repository = AgentBuilderRepository()
    request_row = SimpleNamespace(response_payload={})
    repository.store_parameter_group(request_row, group)
    user_id = uuid4()
    organization_id = uuid4()
    workflow_id = uuid4()
    session = SimpleNamespace(
        id=uuid4(),
        user_id=user_id,
        organization_id=organization_id,
        workflow_id=workflow_id,
    )
    workflow = SimpleNamespace(
        id=workflow_id,
        organization_id=organization_id,
        graph={
            "nodes": [_node("answer", "answerNode", {})],
            "edges": [],
        },
        updated_at=datetime.now(timezone.utc),
    )

    class _Query:
        def __init__(self, model, *, first=None, all_rows=None):
            self._model = model
            self._first = first
            self._all = list(all_rows or [])

        def filter(self, *_args):
            return self

        def order_by(self, *_args):
            return self

        def with_for_update(self):
            return self

        def first(self):
            return self._first

        def all(self):
            return list(self._all)

    class _Db:
        def __init__(self):
            self.commits = 0

        def query(self, model):
            if model is task_service_module.AgentBuilderSession:
                return _Query(model, first=session)
            if model is task_service_module.Workflow:
                return _Query(model, first=workflow)
            if model is task_service_module.AgentBuilderRequest:
                return _Query(model, all_rows=[request_row])
            raise AssertionError(f"unexpected model: {model}")

        def commit(self):
            self.commits += 1

    db = _Db()
    audit_calls = []
    monkeypatch.setattr(
        task_service_module,
        "has_workflow_permission",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        task_service_module,
        "add_action_audit",
        lambda *_args, **_kwargs: audit_calls.append((_args, _kwargs)),
    )
    service = ParameterTaskService(
        db,
        user_id=user_id,
        organization_id=organization_id,
        repository=repository,
    )
    operation_id = uuid4()
    first_payload = AgentBuilderParameterTaskDecisionRequest.model_validate(
        {
            "operation_id": operation_id,
            "expected_task_version": task.task_version,
            "action": "set",
            "value": {
                "kind": "json",
                "value": [{"variable": "answer", "value_selector": ["answer"]}],
            },
        }
    )
    conflicting_payload = AgentBuilderParameterTaskDecisionRequest.model_validate(
        {
            "operation_id": operation_id,
            "expected_task_version": task.task_version,
            "action": "set",
            "value": {
                "kind": "json",
                "value": [{"variable": "changed", "value_selector": ["answer"]}],
            },
        }
    )

    first = service.decide(session.id, task.task_id, first_payload)
    retry = service.decide(session.id, task.task_id, first_payload)
    with pytest.raises(HTTPException) as conflict:
        service.decide(session.id, task.task_id, conflicting_payload)

    assert first.graph_mutation is not None
    assert first.awaiting_persistence_ack is True
    assert retry.graph_mutation is None
    assert retry.awaiting_persistence_ack is True
    assert conflict.value.status_code == 409
    assert conflict.value.detail == "task_conflict"
    assert len(request_row.response_payload["operation_envelopes"]) == 1
    assert len(audit_calls) == 1
    assert db.commits == 1


def test_cancel_retry_with_same_operation_id_and_different_payload_conflicts(
    monkeypatch,
):
    group_id = uuid4()
    task = AgentBuilderParameterTask(
        task_id=uuid4(),
        group_id=group_id,
        step_id="step-answer",
        node_id="answer",
        node_type="answerNode",
        parameter_key="outputs",
        label="Outputs",
        input_type="json",
        required=True,
        status="active",
        task_version=1,
        stable_order=0,
        reason="safe reason",
        input_guidance="safe guidance",
    )
    group = AgentBuilderParameterGroup(
        group_id=group_id,
        status="active",
        tasks=[task],
    )
    repository = AgentBuilderRepository()
    request_row = SimpleNamespace(response_payload={})
    repository.store_parameter_group(request_row, group)
    user_id = uuid4()
    organization_id = uuid4()
    workflow_id = uuid4()
    session = SimpleNamespace(
        id=uuid4(),
        user_id=user_id,
        organization_id=organization_id,
        workflow_id=workflow_id,
    )
    workflow = SimpleNamespace(
        id=workflow_id,
        organization_id=organization_id,
        graph={"nodes": [_node("answer", "answerNode", {})], "edges": []},
    )

    class _Query:
        def __init__(self, model, *, first=None, all_rows=None):
            self._model = model
            self._first = first
            self._all = list(all_rows or [])

        def filter(self, *_args):
            return self

        def order_by(self, *_args):
            return self

        def with_for_update(self):
            return self

        def first(self):
            return self._first

        def all(self):
            return list(self._all)

    class _Db:
        def __init__(self):
            self.commits = 0

        def query(self, model):
            if model is task_service_module.AgentBuilderSession:
                return _Query(model, first=session)
            if model is task_service_module.Workflow:
                return _Query(model, first=workflow)
            if model is task_service_module.AgentBuilderRequest:
                return _Query(model, all_rows=[request_row])
            raise AssertionError(f"unexpected model: {model}")

        def commit(self):
            self.commits += 1

    db = _Db()
    audit_calls = []
    monkeypatch.setattr(
        task_service_module,
        "has_workflow_permission",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        task_service_module,
        "add_action_audit",
        lambda *_args, **_kwargs: audit_calls.append((_args, _kwargs)),
    )
    service = ParameterTaskService(
        db,
        user_id=user_id,
        organization_id=organization_id,
        repository=repository,
    )
    operation_id = uuid4()
    first_payload = AgentBuilderParameterGroupCancelRequest.model_validate(
        {
            "operation_id": operation_id,
            "expected_task_id": task.task_id,
            "expected_task_version": task.task_version,
        }
    )
    conflicting_payload = AgentBuilderParameterGroupCancelRequest.model_validate(
        {
            "operation_id": operation_id,
            "expected_task_id": task.task_id,
            "expected_task_version": task.task_version + 1,
        }
    )

    first = service.cancel_group(session.id, group_id, first_payload)
    with pytest.raises(HTTPException) as conflict:
        service.cancel_group(session.id, group_id, conflicting_payload)

    assert first.parameter_group.status == "canceled"
    assert conflict.value.status_code == 409
    assert conflict.value.detail == "task_conflict"
    assert len(request_row.response_payload["parameter_group_cancellations"]) == 1
    assert len(audit_calls) == 1
    assert db.commits == 1


def test_previous_decision_retry_returns_the_same_navigation_target(monkeypatch):
    group_id = uuid4()
    previous_task = AgentBuilderParameterTask(
        task_id=uuid4(),
        group_id=group_id,
        step_id="step-previous",
        node_id="previous",
        node_type="answerNode",
        parameter_key="previous",
        label="Previous",
        input_type="text",
        required=True,
        status="completed",
        task_version=2,
        stable_order=0,
        reason="safe reason",
        input_guidance="safe guidance",
    )
    current_task = previous_task.model_copy(
        update={
            "task_id": uuid4(),
            "step_id": "step-current",
            "node_id": "current",
            "parameter_key": "current",
            "label": "Current",
            "status": "active",
            "task_version": 1,
            "stable_order": 1,
        }
    )
    group = AgentBuilderParameterGroup(
        group_id=group_id,
        status="active",
        tasks=[previous_task, current_task],
    )
    repository = AgentBuilderRepository()
    request_row = SimpleNamespace(response_payload={})
    repository.store_parameter_group(request_row, group)
    operation_id = uuid4()
    repository.store_local_task_decision(
        request_row,
        operation_id=operation_id,
        task_id=current_task.task_id,
        action="previous",
    )
    user_id = uuid4()
    organization_id = uuid4()
    workflow_id = uuid4()
    session = SimpleNamespace(
        id=uuid4(),
        user_id=user_id,
        organization_id=organization_id,
        workflow_id=workflow_id,
    )
    workflow = SimpleNamespace(
        id=workflow_id,
        organization_id=organization_id,
        graph={"nodes": [], "edges": []},
    )

    class _Query:
        def __init__(self, *, first=None, all_rows=None):
            self._first = first
            self._all = list(all_rows or [])

        def filter(self, *_args):
            return self

        def order_by(self, *_args):
            return self

        def with_for_update(self):
            return self

        def first(self):
            return self._first

        def all(self):
            return list(self._all)

    class _Db:
        def query(self, model):
            if model is task_service_module.AgentBuilderSession:
                return _Query(first=session)
            if model is task_service_module.Workflow:
                return _Query(first=workflow)
            if model is task_service_module.AgentBuilderRequest:
                return _Query(all_rows=[request_row])
            raise AssertionError(f"unexpected model: {model}")

    monkeypatch.setattr(
        task_service_module,
        "has_workflow_permission",
        lambda *_args, **_kwargs: True,
    )
    service = ParameterTaskService(
        _Db(),
        user_id=user_id,
        organization_id=organization_id,
        repository=repository,
    )
    payload = AgentBuilderParameterTaskDecisionRequest.model_validate(
        {
            "operation_id": str(operation_id),
            "expected_task_version": current_task.task_version,
            "action": "previous",
        }
    )

    response = service.decide(session.id, current_task.task_id, payload)

    assert response.next_task_id == previous_task.task_id
    assert response.task == current_task
    assert repository.load_parameter_group(request_row, group_id) == group


def test_previous_first_decision_is_presentation_only_and_persists_nothing(
    monkeypatch,
):
    group_id = uuid4()
    previous_task = AgentBuilderParameterTask(
        task_id=uuid4(),
        group_id=group_id,
        step_id="step-previous",
        node_id="previous",
        node_type="answerNode",
        parameter_key="previous",
        label="Previous",
        input_type="text",
        required=True,
        status="completed",
        task_version=2,
        stable_order=0,
        reason="safe reason",
        input_guidance="safe guidance",
    )
    current_task = previous_task.model_copy(
        update={
            "task_id": uuid4(),
            "step_id": "step-current",
            "node_id": "current",
            "parameter_key": "current",
            "label": "Current",
            "status": "active",
            "task_version": 1,
            "stable_order": 1,
        }
    )
    group = AgentBuilderParameterGroup(
        group_id=group_id,
        status="active",
        tasks=[previous_task, current_task],
    )
    repository = AgentBuilderRepository()
    request_row = SimpleNamespace(response_payload={})
    repository.store_parameter_group(request_row, group)
    before = deepcopy(request_row.response_payload)
    user_id = uuid4()
    organization_id = uuid4()
    workflow_id = uuid4()
    session = SimpleNamespace(
        id=uuid4(),
        user_id=user_id,
        organization_id=organization_id,
        workflow_id=workflow_id,
    )
    workflow = SimpleNamespace(
        id=workflow_id,
        organization_id=organization_id,
        graph={"nodes": [], "edges": []},
    )

    class _Query:
        def __init__(self, *, first=None, all_rows=None):
            self._first = first
            self._all = list(all_rows or [])

        def filter(self, *_args):
            return self

        def order_by(self, *_args):
            return self

        def with_for_update(self):
            return self

        def first(self):
            return self._first

        def all(self):
            return list(self._all)

    class _Db:
        def __init__(self):
            self.commits = 0

        def query(self, model):
            if model is task_service_module.AgentBuilderSession:
                return _Query(first=session)
            if model is task_service_module.Workflow:
                return _Query(first=workflow)
            if model is task_service_module.AgentBuilderRequest:
                return _Query(all_rows=[request_row])
            raise AssertionError(f"unexpected model: {model}")

        def commit(self):
            self.commits += 1

    db = _Db()
    audit_calls = []
    monkeypatch.setattr(
        task_service_module,
        "has_workflow_permission",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        task_service_module,
        "add_action_audit",
        lambda *_args, **_kwargs: audit_calls.append((_args, _kwargs)),
    )
    service = ParameterTaskService(
        db,
        user_id=user_id,
        organization_id=organization_id,
        repository=repository,
    )
    payload = AgentBuilderParameterTaskDecisionRequest.model_validate(
        {
            "operation_id": str(uuid4()),
            "expected_task_version": current_task.task_version,
            "action": "previous",
        }
    )

    first = service.decide(session.id, current_task.task_id, payload)
    second = service.decide(session.id, current_task.task_id, payload)

    assert first.next_task_id == previous_task.task_id
    assert second == first
    assert request_row.response_payload == before
    assert repository.load_parameter_group(request_row, group_id) == group
    assert audit_calls == []
    assert db.commits == 0


def test_pending_task_decision_is_discoverable_by_task_version():
    repository = AgentBuilderRepository()
    request_row = SimpleNamespace(response_payload={})
    operation_id = uuid4()
    task_id = uuid4()

    repository.store_pending_task_decision(
        request_row,
        operation_id=operation_id,
        task_id=task_id,
        action="set",
        expected_task_version=3,
    )

    assert repository.find_pending_task_decision_for_task_version(
        request_row,
        task_id=task_id,
        expected_task_version=3,
    ) == {
        "operation_id": str(operation_id),
        "task_id": str(task_id),
        "action": "set",
        "expected_task_version": 3,
    }
    assert repository.find_pending_task_decision_for_task_version(
        request_row,
        task_id=task_id,
        expected_task_version=4,
    ) is None
