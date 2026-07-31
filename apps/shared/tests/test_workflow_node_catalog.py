from apps.shared.services.workflow_node_catalog import (
    LLM_ROUTING_GRAPH_PARAMETER_KEYS,
    agent_builder_supported_node_types,
    apply_node_parameter_value,
    capability_contract,
    capability_output_contract,
    capability_output_keys,
    classify_catalog_version,
    derive_node_configuration_state,
    implemented_node_types,
    load_workflow_node_catalog,
    missing_required_configuration,
    node_parameter_definitions,
    node_parameter_is_configured,
    node_parameter_value,
    remove_node_parameter_value,
    validate_node_parameter_update,
    validate_node_parameter_value,
    validate_workflow_graph_connections,
    validate_workflow_node_catalog,
)

EXPECTED_IMPLEMENTED_NODE_TYPES = {
    "answerNode",
    "codeNode",
    "conditionNode",
    "fileExtractionNode",
    "gmailDraftNode",
    "githubNode",
    "httpRequestNode",
    "llmNode",
    "loopNode",
    "mailNode",
    "mailAcknowledgeNode",
    "scheduleTrigger",
    "slackPostNode",
    "startNode",
    "templateNode",
    "variableExtractionNode",
    "webhookTrigger",
    "workflowNode",
}
EXPECTED_AGENT_BUILDER_NODE_TYPES = EXPECTED_IMPLEMENTED_NODE_TYPES - {"loopNode"}
COMMON_EXTERNAL_EFFECT_NODES = {"httpRequestNode", "slackPostNode", "githubNode"}
MAIL_LEDGER_EXTERNAL_EFFECT_NODES = {"gmailDraftNode", "mailAcknowledgeNode"}
FAIL_CLOSED_EXTERNAL_EFFECT_NODES = {"pluginNode"}


def test_workflow_node_catalog_excludes_product_unavailable_nodes_from_builder_allowlist():
    catalog = load_workflow_node_catalog()

    assert catalog["version"] == 3
    assert implemented_node_types() == EXPECTED_IMPLEMENTED_NODE_TYPES
    assert agent_builder_supported_node_types() == EXPECTED_AGENT_BUILDER_NODE_TYPES


def test_workflow_node_catalog_declares_safe_generation_contract_for_every_node():
    catalog = load_workflow_node_catalog()

    for node in catalog["nodes"]:
        assert isinstance(node["capabilities"], list)
        assert node["side_effect"] in {
            "none",
            "local_execution",
            "external_read",
            "external_write",
        }
        assert isinstance(node["required_configuration"], list)
        if node["agent_builder_supported"]:
            assert node["implemented"] is True
            assert node["capabilities"]
            assert set(node["required_configuration"]) <= set(
                node.get("configuration_labels") or {}
            )


def test_external_write_nodes_have_an_explicit_idempotency_owner() -> None:
    catalog = load_workflow_node_catalog()
    external_write_nodes = {
        node["node_type"]
        for node in catalog["nodes"]
        if node["side_effect"] == "external_write"
    }

    assert external_write_nodes == (
        COMMON_EXTERNAL_EFFECT_NODES
        | MAIL_LEDGER_EXTERNAL_EFFECT_NODES
        | FAIL_CLOSED_EXTERNAL_EFFECT_NODES
    )


def test_workflow_node_catalog_v3_declares_typed_parameters_for_required_configuration():
    catalog = load_workflow_node_catalog()

    for node in catalog["nodes"]:
        parameters = node["parameters"]
        assert isinstance(parameters, list)
        parameter_keys = [parameter["key"] for parameter in parameters]
        assert len(parameter_keys) == len(set(parameter_keys))
        assert set(node["required_configuration"]) <= set(parameter_keys)
        for parameter in parameters:
            assert parameter["input_type"] in {
                "boolean",
                "code",
                "credential_ref",
                "json",
                "number",
                "resource_ref",
                "secret",
                "select",
                "text",
                "textarea",
                "variable_selector",
                "variable_selector_list",
            }
            assert parameter["defer_policy"] in {
                "forbidden",
                "allow_unresolved",
            }
            assert isinstance(parameter["required"], bool)
            assert isinstance(parameter.get("agent_builder_task", True), bool)
        assert {output["capability"] for output in node["outputs"]} == set(
            node["capabilities"]
        )


def test_catalog_exposes_start_and_answer_schema_as_configurable_tasks():
    start = node_parameter_definitions("startNode")
    answer = node_parameter_definitions("answerNode")

    assert [(item["key"], item["agent_builder_task"]) for item in start] == [
        ("variables", True)
    ]
    assert [(item["key"], item["agent_builder_task"]) for item in answer] == [
        ("outputs", True)
    ]


def test_llm_catalog_exposes_basic_agent_builder_settings_and_keeps_advanced_routing_runtime_shape():
    parameters = node_parameter_definitions("llmNode")
    assert [parameter["key"] for parameter in parameters] == [
        "model_id",
        "output_format_type",
        "output_json_schema",
        "system_prompt",
        "user_prompt",
        "assistant_prompt",
        "referenced_variables",
        "citationDisplayMode",
        "auto_model_routing",
        "fallback_model_id",
        "model_routing_refresh_every_runs",
        "model_routing_validation_budget_usd",
        "model_routing_max_cohorts",
        "knowledgeBases",
    ]
    routing_parameters = [
        parameter
        for parameter in parameters
        if parameter.get("task_group") == "model_routing"
    ]
    assert tuple(parameter["key"] for parameter in routing_parameters) == (
        "auto_model_routing",
    )
    assert next(
        parameter for parameter in parameters if parameter["key"] == "model_id"
    )["agent_builder_task"] is True
    citation_parameter = next(
        parameter
        for parameter in parameters
        if parameter["key"] == "citationDisplayMode"
    )
    assert citation_parameter["default"] == "detailed"
    assert citation_parameter["apply_default_to_existing"] is False
    assert {
        parameter["key"]
        for parameter in parameters
        if parameter["agent_builder_task"] is False
    } == {
        "fallback_model_id",
        "model_routing_refresh_every_runs",
        "model_routing_validation_budget_usd",
        "model_routing_max_cohorts",
    }
    assert set(LLM_ROUTING_GRAPH_PARAMETER_KEYS) == {
        "model_id",
        "auto_model_routing",
        "fallback_model_id",
        "model_routing_refresh_every_runs",
        "model_routing_validation_budget_usd",
        "model_routing_max_cohorts",
    }

    data = {"model_id": "default-model"}
    for key, value in [
        ("auto_model_routing", True),
        ("fallback_model_id", "fallback-model"),
        ("model_routing_refresh_every_runs", 25),
        ("model_routing_validation_budget_usd", 4.5),
        ("model_routing_max_cohorts", 8),
    ]:
        data = apply_node_parameter_value("llmNode", key, data, value)

    assert data == {
        "model_id": "default-model",
        "auto_model_routing": True,
        "fallback_model_id": "fallback-model",
        "model_routing_policy": {
            "refresh": {"refresh_every_runs": 25},
            "validation_budget_usd": 4.5,
            "max_cohorts": 8,
        },
    }
    assert node_parameter_is_configured(
        "llmNode", "model_routing_refresh_every_runs", data
    )
    assert validate_node_parameter_update(
        "llmNode", "fallback_model_id", data, "default-model"
    ) == ["fallback_must_differ"]

    for citation_mode in ("hidden", "basic", "detailed"):
        data = apply_node_parameter_value(
            "llmNode", "citationDisplayMode", data, citation_mode
        )
        assert data["citationDisplayMode"] == citation_mode
        assert validate_node_parameter_update(
            "llmNode", "citationDisplayMode", data, citation_mode
        ) == []

    assert validate_node_parameter_update(
        "llmNode", "citationDisplayMode", data, "unsupported"
    ) == ["option_not_allowed"]


def test_llm_catalog_maps_basic_output_and_selector_values_to_runtime_shape():
    data = {
        "model_id": "default-model",
        "output_format": {"type": "text"},
        "system_prompt": "Answer safely.",
        "referenced_variables": [
            {
                "name": "customer_query",
                "value_selector": ["start", "query"],
            }
        ],
    }

    data = apply_node_parameter_value(
        "llmNode", "output_format_type", data, "json"
    )
    data = apply_node_parameter_value(
        "llmNode", "output_json_schema", data, {"type": "object"}
    )
    data = apply_node_parameter_value(
        "llmNode", "referenced_variables", data, [["start", "query"]]
    )

    assert data["output_format"] == {
        "type": "json",
        "schema": {"type": "object"},
    }
    assert data["referenced_variables"] == [
        {"name": "customer_query", "value_selector": ["start", "query"]}
    ]
    assert node_parameter_is_configured(
        "llmNode", "referenced_variables", data
    )


def test_llm_referenced_variables_preserve_output_names_and_reject_duplicates():
    data = apply_node_parameter_value(
        "llmNode",
        "referenced_variables",
        {"referenced_variables": []},
        [["start", "result"], ["extract", "result"]],
    )

    assert [item["name"] for item in data["referenced_variables"]] == [
        "result",
        "result",
    ]
    assert (
        validate_node_parameter_update(
            "llmNode",
            "referenced_variables",
            {"referenced_variables": []},
            [["start", "result"], ["extract", "result"]],
        )
        == ["duplicate_variable_name"]
    )


def test_llm_output_json_schema_accepts_only_json_objects():
    assert validate_node_parameter_update(
        "llmNode", "output_json_schema", {}, {"type": "object"}
    ) == []
    for invalid in (["not", "an", "object"], "text", 1):
        assert validate_node_parameter_update(
            "llmNode", "output_json_schema", {}, invalid
        ) == ["json_object_required"]
    assert validate_node_parameter_update(
        "llmNode", "output_json_schema", {}, None
    ) == []


def test_llm_configuration_requires_model_and_any_one_prompt():
    assert derive_node_configuration_state(
        "llmNode", {"model_id": "default-model"}
    ) == "unresolved"
    assert derive_node_configuration_state(
        "llmNode",
        {"model_id": "default-model", "assistant_prompt": "Answer."},
    ) == "resolved"


def test_mail_catalog_declares_every_user_configurable_search_parameter():
    parameters = node_parameter_definitions("mailNode")

    assert [parameter["key"] for parameter in parameters] == [
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
    ]
    assert all(parameter["agent_builder_task"] for parameter in parameters)
    assert {
        parameter["key"]: parameter["input_type"] for parameter in parameters
    } == {
        "credential_id": "credential_ref",
        "keyword": "text",
        "sender": "text",
        "subject": "text",
        "start_date": "text",
        "end_date": "text",
        "folder": "select",
        "max_results": "number",
        "unread_only": "boolean",
        "mark_as_read": "boolean",
        "processing_mode": "select",
    }


def test_catalog_uses_typed_selector_list_for_mail_acknowledgement():
    parameters = {
        parameter["key"]: parameter
        for parameter in node_parameter_definitions("mailAcknowledgeNode")
    }

    assert parameters["processing_ref_selector"]["input_type"] == (
        "variable_selector"
    )
    assert parameters["required_effect_ref_selectors"]["input_type"] == (
        "variable_selector_list"
    )


def test_catalog_does_not_model_slack_or_github_auth_as_credentials():
    slack = node_parameter_definitions("slackPostNode")
    github = node_parameter_definitions("githubNode")
    catalog = load_workflow_node_catalog()
    definitions = {node["node_type"]: node for node in catalog["nodes"]}

    assert "credential" not in {parameter["key"] for parameter in slack}
    assert "credential" not in {parameter["key"] for parameter in github}
    assert "credential" not in definitions["slackPostNode"]["required_configuration"]
    assert "credential" not in definitions["githubNode"]["required_configuration"]


def test_catalog_declares_planner_aliases_and_standalone_creation_policy():
    slack = capability_contract("slack_send")
    mail_ack = capability_contract("mail_terminal_acknowledgement")
    loop = capability_contract("loop")

    assert slack is not None
    assert slack["standalone_creation"] == "allowed"
    assert {"slack", "슬랙"} <= set(slack["planner_aliases"])
    assert mail_ack is not None
    assert mail_ack["standalone_creation"] == "requires_context"
    assert loop is not None
    assert loop["standalone_creation"] == "forbidden"


def test_catalog_output_contract_preserves_runtime_dynamic_output_names():
    assert capability_output_contract("file_extraction") == {
        "capability": "file_extraction",
        "mode": "parameter_names",
        "parameter_key": "referenced_variables",
        "name_key": "name",
        "value_type": "text",
    }
    assert capability_output_contract("variable_extraction") == {
        "capability": "variable_extraction",
        "mode": "parameter_names",
        "parameter_key": "mappings",
        "name_key": "name",
        "value_type": "unknown",
    }


def test_catalog_resolves_static_and_parameter_driven_output_keys_from_node_data():
    assert capability_output_keys("llm", {"output_format": {"type": "text"}}) == [
        "text"
    ]
    assert capability_output_keys(
        "file_extraction",
        {
            "referenced_variables": [
                {"name": "document_text", "value_selector": ["input", "file"]}
            ]
        },
    ) == ["document_text"]
    assert capability_output_keys(
        "variable_extraction",
        {"mappings": [{"name": "author"}, {"name": "title"}]},
    ) == ["author", "title"]
    assert capability_output_keys("variable_extraction", {"mappings": []}) == []


def test_catalog_version_recovery_only_accepts_v3_as_current():
    assert classify_catalog_version(None) == "legacy_stale"
    assert classify_catalog_version(2) == "legacy_stale"
    assert classify_catalog_version(3) == "current"


def test_catalog_validation_rejects_missing_required_parameter_and_invalid_defer_policy():
    invalid_missing = {
        "version": 3,
        "nodes": [
            {
                "node_type": "exampleNode",
                "implemented": True,
                "agent_builder_supported": True,
                "connection_policy": {
                    "role": "intermediate",
                    "incoming": "allowed",
                    "outgoing": "allowed",
                    "outgoing_handles": "standard",
                },
                "capabilities": ["example"],
                "side_effect": "none",
                "required_configuration": ["value"],
                "configuration_labels": {"value": "Value"},
                "parameters": [],
            }
        ],
    }
    invalid_defer = {
        **invalid_missing,
        "nodes": [
            {
                **invalid_missing["nodes"][0],
                "required_configuration": [],
                "parameters": [
                    {
                        "key": "value",
                        "label": "Value",
                        "input_type": "text",
                        "required": False,
                        "defer_policy": "always",
                    }
                ],
            }
        ],
    }

    for invalid in (invalid_missing, invalid_defer):
        try:
            validate_workflow_node_catalog(invalid)
        except RuntimeError:
            pass
        else:
            raise AssertionError("invalid catalog must be rejected")


def test_node_configuration_state_is_derived_from_all_required_parameters():
    definitions = node_parameter_definitions("slackPostNode")
    assert "credential" not in {definition["key"] for definition in definitions}
    assert "bot_token" in {definition["key"] for definition in definitions}
    assert "url" in {definition["key"] for definition in definitions}
    assert "channel" in {definition["key"] for definition in definitions}
    definitions_by_key = {
        definition["key"]: definition for definition in definitions
    }
    assert definitions_by_key["bot_token"]["validation"]["required_when"] == {
        "parameter_key": "slackMode",
        "equals": "api",
    }
    assert definitions_by_key["channel"]["validation"]["required_when"] == {
        "parameter_key": "slackMode",
        "equals": "api",
    }
    assert definitions_by_key["url"]["validation"]["required_when"] == {
        "parameter_key": "slackMode",
        "equals": "webhook",
    }

    assert derive_node_configuration_state(
        "slackPostNode",
        {
            "slackMode": "api",
            "authConfig": {"token": "secret-value"},
            "channel": "C123",
            "message": "hello",
            "configuration_state": "resolved",
        },
    ) == "resolved"


def test_slack_missing_configuration_deduplicates_mode_required_parameters():
    assert missing_required_configuration(
        "slackPostNode",
        {"slackMode": "api"},
    ) == ["bot_token", "channel", "payload"]


def test_github_api_token_parameter_is_required_for_every_action():
    definitions = {
        definition["key"]: definition
        for definition in node_parameter_definitions("githubNode")
    }

    assert definitions["api_token"]["required"] is True
    assert definitions["api_token"]["defer_policy"] == "allow_unresolved"
    assert derive_node_configuration_state(
        "slackPostNode",
        {
            "slackMode": "api",
            "authConfig": {"token": "secret-value"},
            "channel": "",
            "message": "hello",
            "configuration_state": "resolved",
        },
    ) == "unresolved"
    assert derive_node_configuration_state(
        "slackPostNode",
        {
            "slackMode": "webhook",
            "url": "https://hooks.slack.test/1",
            "message": "hello",
        },
    ) == "resolved"


def test_slack_deferred_mode_requirement_overrides_retained_graph_value():
    assert derive_node_configuration_state(
        "slackPostNode",
        {
            "slackMode": "api",
            "authConfig": {"token": "secret-value"},
            "channel": "C123",
            "message": "hello",
            "_deferred_parameters": ["channel"],
        },
    ) == "unresolved"


def test_slack_requires_one_non_empty_payload_configuration():
    base = {
        "slackMode": "api",
        "authConfig": {"token": "secret-value"},
        "channel": "C123",
    }

    assert derive_node_configuration_state("slackPostNode", base) == "unresolved"
    assert derive_node_configuration_state(
        "slackPostNode",
        {**base, "message": "   ", "blocks": "[]", "attachments": "[]"},
    ) == "unresolved"
    assert derive_node_configuration_state(
        "slackPostNode", {**base, "blocks": "{invalid"}
    ) == "unresolved"
    assert derive_node_configuration_state(
        "slackPostNode", {**base, "message": "hello"}
    ) == "resolved"
    assert derive_node_configuration_state(
        "slackPostNode", {**base, "blocks": '[{"type":"section"}]'}
    ) == "resolved"
    assert derive_node_configuration_state(
        "slackPostNode", {**base, "attachments": '[{"text":"alert"}]'}
    ) == "resolved"
    assert derive_node_configuration_state(
        "slackPostNode",
        {
            **base,
            "message": "hello",
            "_deferred_parameters": ["message"],
        },
    ) == "unresolved"


def test_slack_legacy_body_text_is_not_a_runtime_payload():
    legacy_data = {
        "slackMode": "api",
        "authConfig": {"token": "secret-value"},
        "channel": "C123",
        "body": '{"channel":"C123","text":"legacy-only"}',
    }

    assert node_parameter_value(
        "slackPostNode", "message", legacy_data
    ) == (False, None)
    assert missing_required_configuration(
        "slackPostNode", legacy_data
    ) == ["payload"]
    assert derive_node_configuration_state(
        "slackPostNode", legacy_data
    ) == "unresolved"

    legacy_body_only = {
        "slackMode": "api",
        "authConfig": {"token": "secret-value"},
        "body": '{"channel":"C123","text":"legacy-only"}',
    }
    assert node_parameter_value(
        "slackPostNode", "channel", legacy_body_only
    ) == (False, None)
    assert missing_required_configuration(
        "slackPostNode", legacy_body_only
    ) == ["channel", "payload"]


def test_github_missing_legacy_action_uses_runtime_default_for_reads():
    legacy_data = {
        "api_token": "secret-value",
        "repo_owner": "octo",
        "repo_name": "repo",
        "pr_number": "1",
    }

    assert node_parameter_value(
        "githubNode", "action", legacy_data
    ) == (True, "get_pr")
    assert missing_required_configuration("githubNode", legacy_data) == []
    assert derive_node_configuration_state(
        "githubNode", legacy_data
    ) == "resolved"


def test_slack_and_github_secret_parameters_map_to_existing_node_fields():
    slack = apply_node_parameter_value(
        "slackPostNode", "bot_token", {"authConfig": {}}, "secret-value"
    )
    github = apply_node_parameter_value(
        "githubNode", "api_token", {}, "secret-value"
    )

    assert slack == {"authConfig": {"token": "secret-value"}}
    assert node_parameter_is_configured("slackPostNode", "bot_token", slack)
    assert github == {"api_token": "secret-value"}
    assert node_parameter_is_configured("githubNode", "api_token", github)


def test_slack_json_parameters_use_the_editor_string_shape():
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "hello"}}]
    attachments = [{"color": "#4A154B", "text": "alert"}]

    data = apply_node_parameter_value("slackPostNode", "blocks", {}, blocks)
    data = apply_node_parameter_value(
        "slackPostNode", "attachments", data, attachments
    )

    assert data["blocks"] == (
        '[{"type": "section", "text": {"type": "mrkdwn", "text": "hello"}}]'
    )
    assert data["attachments"] == '[{"color": "#4A154B", "text": "alert"}]'

    assert node_parameter_value("slackPostNode", "blocks", data) == (
        True,
        blocks,
    )
    assert node_parameter_value("slackPostNode", "attachments", data) == (
        True,
        attachments,
    )


def test_invalid_stored_slack_json_is_not_hydrated_as_a_parameter_value():
    assert node_parameter_value(
        "slackPostNode", "blocks", {"blocks": "{invalid"}
    ) == (False, None)
    assert node_parameter_value(
        "slackPostNode", "blocks", {"blocks": '{"type":"section"}'}
    ) == (False, None)


def test_removing_optional_parameter_preserves_unrelated_nested_node_data():
    slack = remove_node_parameter_value(
        "slackPostNode",
        "channel",
        {
            "channel": "C123",
            "body": '{"channel": "C123", "text": "hello"}',
            "message": "hello",
        },
    )
    llm = remove_node_parameter_value(
        "llmNode",
        "output_json_schema",
        {
            "output_format": {
                "type": "json",
                "schema": {"type": "object"},
            },
            "model_id": "model-1",
        },
    )

    assert slack == {
        "body": '{"text": "hello"}',
        "message": "hello",
    }
    assert llm == {
        "output_format": {"type": "json"},
        "model_id": "model-1",
    }


def test_every_agent_builder_parameter_round_trips_through_catalog_application():
    catalog = load_workflow_node_catalog()
    explicit_samples = {
        ("httpRequestNode", "url"): "https://example.com/api",
        ("scheduleTrigger", "cron_expression"): "0 * * * *",
        ("scheduleTrigger", "timezone"): "UTC",
        ("slackPostNode", "url"): "https://hooks.slack.com/services/T/B/S",
        ("mailNode", "start_date"): "2026-07-01",
        ("mailNode", "end_date"): "2026-07-16",
        ("llmNode", "output_json_schema"): {"type": "object"},
    }

    for node in catalog["nodes"]:
        node_type = str(node["node_type"])
        if not node.get("agent_builder_supported"):
            continue
        for parameter in node_parameter_definitions(node_type):
            if not parameter["agent_builder_task"]:
                continue
            input_type = str(parameter["input_type"])
            options = parameter["validation"].get("options")
            sample = explicit_samples.get((node_type, str(parameter["key"])))
            if sample is None:
                sample = {
                    "boolean": True,
                    "code": "return input",
                    "credential_ref": "11111111-1111-4111-8111-111111111111",
                    "json": [{"name": "value"}],
                    "number": parameter["validation"].get("min", 1),
                    "resource_ref": "22222222-2222-4222-8222-222222222222",
                    "secret": "secret-value",
                    "select": options[0] if options else "value",
                    "text": "value",
                    "textarea": "value",
                    "variable_selector": ["upstream", "result"],
                    "variable_selector_list": [["upstream", "result"]],
                }[input_type]

            assert validate_node_parameter_value(
                node_type, str(parameter["key"]), sample
            ) == [], (node_type, parameter["key"])
            updated = apply_node_parameter_value(
                node_type, str(parameter["key"]), {}, sample
            )
            assert node_parameter_is_configured(
                node_type, str(parameter["key"]), updated
            ), (node_type, parameter["key"])


def test_external_node_configuration_uses_catalog_safe_parameter_fields():
    assert derive_node_configuration_state(
        "slackPostNode",
        {
            "slackMode": "api",
            "authConfig": {"token": "secret-value"},
            "channel": "C123",
            "message": "hello",
        },
    ) == "resolved"
    assert derive_node_configuration_state(
        "githubNode",
        {
            "action": "get_pr",
            "repo_owner": "octo",
            "repo_name": "repo",
            "pr_number": "1",
            "api_token": "secret-value",
        },
    ) == "resolved"
    assert derive_node_configuration_state(
        "githubNode",
        {
            "action": "get_pr",
            "repo_owner": "octo",
            "repo_name": "repo",
            "pr_number": 0,
            "api_token": "secret-value",
        },
    ) == "unresolved"
    assert validate_node_parameter_value(
        "githubNode", "pr_number", 1.5
    ) == ["integer_required"]
    assert derive_node_configuration_state(
        "githubNode",
        {
            "action": "comment_pr",
            "repo_owner": "octo",
            "repo_name": "repo",
            "pr_number": "1",
            "api_token": "configured",
        },
    ) == "unresolved"
    assert derive_node_configuration_state(
        "githubNode",
        {
            "action": "comment_pr",
            "repo_owner": "octo",
            "repo_name": "repo",
            "pr_number": "1",
            "api_token": "configured",
            "comment_body": "Review result: {{review}}",
        },
    ) == "resolved"


def test_workflow_node_catalog_declares_connection_policy_for_every_node():
    catalog = load_workflow_node_catalog()

    for node in catalog["nodes"]:
        policy = node["connection_policy"]
        assert policy["role"] in {"entry", "intermediate", "branch", "terminal"}
        assert policy["incoming"] in {"forbidden", "allowed", "required"}
        assert policy["outgoing"] in {"forbidden", "allowed", "required"}
        assert policy["outgoing_handles"] in {
            "standard",
            "condition_cases",
            "unrestricted",
        }


def test_connection_policy_rejects_entry_terminal_and_condition_violations():
    graph = {
        "nodes": [
            {"id": "start", "type": "startNode", "data": {}},
            {"id": "answer", "type": "answerNode", "data": {}},
            {
                "id": "condition",
                "type": "conditionNode",
                "data": {"cases": [{"id": "case-1"}]},
            },
            {"id": "llm", "type": "llmNode", "data": {}},
        ],
        "edges": [
            {"id": "into-start", "source": "llm", "target": "start"},
            {"id": "from-answer", "source": "answer", "target": "llm"},
            {
                "id": "bad-condition",
                "source": "condition",
                "sourceHandle": "missing-case",
                "target": "llm",
            },
        ],
    }

    issues = validate_workflow_graph_connections(graph)

    assert {issue.code for issue in issues} == {
        "START_NODE_HAS_INCOMING_EDGE",
        "TERMINAL_NODE_HAS_OUTGOING_EDGE",
        "INVALID_CONDITION_SOURCE_HANDLE",
    }


def test_connection_policy_rejects_condition_edge_without_explicit_handle():
    graph = {
        "nodes": [
            {
                "id": "condition",
                "type": "conditionNode",
                "data": {"cases": [{"id": "case-1"}]},
            },
            {"id": "answer", "type": "answerNode", "data": {}},
        ],
        "edges": [
            {
                "id": "implicit-default",
                "source": "condition",
                "target": "answer",
            }
        ],
    }

    issues = validate_workflow_graph_connections(graph)

    assert [issue.code for issue in issues] == [
        "INVALID_CONDITION_SOURCE_HANDLE"
    ]


def test_catalog_parameter_validation_drives_configuration_state():
    assert validate_node_parameter_value(
        "httpRequestNode", "url", "not-a-url"
    ) == ["pattern_mismatch"]
    assert derive_node_configuration_state(
        "httpRequestNode", {"url": "not-a-url"}
    ) == "unresolved"
    assert derive_node_configuration_state(
        "httpRequestNode", {"url": "https://example.com/api"}
    ) == "resolved"


def test_secret_reference_is_valid_after_server_side_secret_storage():
    reference = (
        "workflow-node-secret://00000000-0000-4000-8000-000000000001"
    )

    assert validate_node_parameter_value(
        "slackPostNode", "url", reference
    ) == []
    assert derive_node_configuration_state(
        "slackPostNode",
        {
            "slackMode": "webhook",
            "url": reference,
            "message": "hello",
        },
    ) == "resolved"
    assert validate_node_parameter_value(
        "slackPostNode", "url", "workflow-node-secret://invalid"
    ) == ["pattern_mismatch"]


def test_catalog_exposes_effective_validation_and_sensitivity_metadata():
    catalog_by_type = {
        node["node_type"]: node for node in load_workflow_node_catalog()["nodes"]
    }
    http_url = next(
        parameter
        for parameter in node_parameter_definitions("httpRequestNode")
        if parameter["key"] == "url"
    )
    loop_key = next(
        parameter
        for parameter in node_parameter_definitions("loopNode")
        if parameter["key"] == "loop_key"
    )
    workflow_id = next(
        parameter
        for parameter in node_parameter_definitions("workflowNode")
        if parameter["key"] == "workflowId"
    )
    workflow_app_id = next(
        parameter
        for parameter in node_parameter_definitions("workflowNode")
        if parameter["key"] == "appId"
    )

    assert http_url["validation"]["max_length"] == 2048
    assert http_url["sensitivity"] == "secret_forbidden"
    assert loop_key["input_type"] == "text"
    assert loop_key["required"] is False
    assert catalog_by_type["loopNode"]["required_configuration"] == ["subGraph"]
    assert workflow_id["required"] is False
    assert workflow_app_id["required"] is True
    assert catalog_by_type["workflowNode"]["required_configuration"] == ["appId"]
