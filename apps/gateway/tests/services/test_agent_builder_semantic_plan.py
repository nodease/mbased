from apps.gateway.application.agent_builder.semantic_plan import (
    catalog_parameter_guide,
    normalize_parameter_guidance_hints,
    planned_step_ids,
)
from apps.shared.schemas.agent_builder import AgentBuilderParameterGuidanceHint


def test_catalog_parameter_guide_contains_only_catalog_parameters():
    guide = catalog_parameter_guide(["slack_send"])

    assert guide == {
        "step_slack": [
            {
                "parameter_key": "slackMode",
                "label": "Slack 전송 방식",
                "input_type": "select",
            },
            {
                "parameter_key": "bot_token",
                "label": "Bot Token",
                "input_type": "secret",
            },
            {
                "parameter_key": "url",
                "label": "Webhook URL",
                "input_type": "secret",
            },
            {
                "parameter_key": "channel",
                "label": "Slack channel",
                "input_type": "text",
            },
            {
                "parameter_key": "message",
                "label": "메시지",
                "input_type": "textarea",
            },
            {
                "parameter_key": "blocks",
                "label": "Blocks",
                "input_type": "json",
            },
            {
                "parameter_key": "attachments",
                "label": "Attachments",
                "input_type": "json",
            },
            {
                "parameter_key": "thread_ts",
                "label": "Thread timestamp",
                "input_type": "text",
            },
            {
                "parameter_key": "username",
                "label": "표시 이름",
                "input_type": "text",
            },
            {
                "parameter_key": "icon_emoji",
                "label": "아이콘 이모지",
                "input_type": "text",
            },
        ]
    }


def test_planned_step_ids_are_stable_for_duplicate_capabilities():
    assert planned_step_ids(["llm", "llm", "answer"]) == [
        ("step_llm", "llm"),
        ("step_llm_2", "llm"),
        ("step_answer", "answer"),
    ]


def test_parameter_guidance_discards_unknown_mismatched_and_secret_like_hints():
    hints = [
        AgentBuilderParameterGuidanceHint(
            step_id="step_slack",
            parameter_key="channel",
            reason="메시지를 보낼 위치가 필요합니다.",
            input_guidance="Slack channel ID를 선택하세요.",
        ),
        AgentBuilderParameterGuidanceHint(
            step_id="step_slack",
            parameter_key="unknown_parameter",
            reason="다른 node의 parameter입니다.",
            input_guidance="URL을 입력하세요.",
        ),
        AgentBuilderParameterGuidanceHint(
            step_id="step_unknown",
            parameter_key="channel",
            reason="알 수 없는 step입니다.",
            input_guidance="값을 입력하세요.",
        ),
        AgentBuilderParameterGuidanceHint(
            step_id="step_slack",
            parameter_key="credential",
            reason="token=secret-value를 사용합니다.",
            input_guidance="credential을 입력하세요.",
        ),
    ]

    normalized = normalize_parameter_guidance_hints(
        hints,
        [("step_slack", "slack_send")],
    )

    assert normalized == [hints[0]]
