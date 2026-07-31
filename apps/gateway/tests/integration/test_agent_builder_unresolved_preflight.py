import pytest

from apps.shared.services.workflow_configuration_preflight import (
    WorkflowConfigurationPreflightError,
    enforce_workflow_configuration_preflight,
    workflow_configuration_issues,
)


def _graph(data):
    return {
        "nodes": [
            {
                "id": "slack",
                "type": "slackPostNode",
                "position": {"x": 0, "y": 0},
                "data": data,
            }
        ],
        "edges": [],
    }


def test_preflight_ignores_client_configuration_state_and_reports_missing_fields():
    issues = workflow_configuration_issues(
        _graph({"configuration_state": "resolved", "slackMode": "api"})
    )

    assert len(issues) == 1
    assert issues[0].node_id == "slack"
    assert set(issues[0].missing_parameters) == {"payload", "bot_token", "channel"}


def test_preflight_reports_deferred_slack_channel_despite_retained_value():
    issues = workflow_configuration_issues(
        _graph(
            {
                "slackMode": "api",
                "authConfig": {"token": "configured-value"},
                "channel": "C123",
                "message": "hello",
                "_deferred_parameters": ["channel"],
            }
        )
    )

    assert len(issues) == 1
    assert issues[0].missing_parameters == ("channel",)


def test_preflight_reports_only_payload_when_slack_delivery_is_configured():
    issues = workflow_configuration_issues(
        _graph(
            {
                "slackMode": "api",
                "authConfig": {"token": "configured-value"},
                "channel": "C123",
            }
        )
    )

    assert len(issues) == 1
    assert issues[0].missing_parameters == ("payload",)


def test_preflight_does_not_treat_legacy_slack_body_text_as_payload():
    issues = workflow_configuration_issues(
        _graph(
            {
                "slackMode": "api",
                "authConfig": {"token": "configured-value"},
                "channel": "C123",
                "body": '{"channel":"C123","text":"legacy-only"}',
            }
        )
    )

    assert len(issues) == 1
    assert issues[0].missing_parameters == ("payload",)

    body_only_issues = workflow_configuration_issues(
        _graph(
            {
                "slackMode": "api",
                "authConfig": {"token": "configured-value"},
                "body": '{"channel":"C123","text":"legacy-only"}',
            }
        )
    )
    assert len(body_only_issues) == 1
    assert body_only_issues[0].missing_parameters == ("channel", "payload")


def test_unresolved_external_action_is_blocked_for_test_run_and_deployment():
    graph = _graph({"slackMode": "api", "channel": ""})

    for surface in ("test", "run", "deployment"):
        with pytest.raises(WorkflowConfigurationPreflightError) as exc:
            enforce_workflow_configuration_preflight(graph, surface=surface)
        assert exc.value.surface == surface
        assert exc.value.issues[0].node_type == "slackPostNode"


def test_llm_without_any_prompt_is_blocked_by_catalog_required_any_configuration():
    graph = {
        "nodes": [
            {
                "id": "llm",
                "type": "llmNode",
                "data": {"model_id": "model-reference"},
            }
        ],
        "edges": [],
    }

    issues = workflow_configuration_issues(graph)

    assert len(issues) == 1
    assert issues[0].missing_parameters == ("prompt",)


def test_local_execution_preflight_ignores_client_configuration_state():
    graph = {
        "nodes": [
            {
                "id": "workflow-call",
                "type": "workflowNode",
                "data": {"configuration_state": "resolved"},
            }
        ],
        "edges": [],
    }

    issues = workflow_configuration_issues(graph)

    assert len(issues) == 1
    assert issues[0].node_type == "workflowNode"
    assert issues[0].missing_parameters == ("appId",)


def test_local_execution_preflight_accepts_app_target_without_optional_workflow_id():
    graph = {
        "nodes": [
            {
                "id": "workflow-call",
                "type": "workflowNode",
                "data": {
                    "workflowId": "",
                    "appId": "app-reference",
                    "deployment_id": "deployment-reference",
                    "configuration_state": "resolved",
                },
            }
        ],
        "edges": [],
    }

    assert workflow_configuration_issues(graph) == []


def test_local_execution_preflight_allows_loop_key_fallback_and_checks_subgraph():
    graph = {
        "nodes": [
            {
                "id": "loop",
                "type": "loopNode",
                "data": {
                    "subGraph": {
                        "nodes": [
                            {
                                "id": "nested-workflow-call",
                                "type": "workflowNode",
                                "data": {"configuration_state": "resolved"},
                            }
                        ],
                        "edges": [],
                    },
                },
            },
        ],
        "edges": [],
    }

    issues = workflow_configuration_issues(graph)

    assert [(issue.node_id, issue.missing_parameters) for issue in issues] == [
        ("nested-workflow-call", ("appId",))
    ]


def test_loop_subgraph_preflight_accepts_iteration_and_mapped_input_selectors():
    graph = {
        "nodes": [
            {
                "id": "source",
                "type": "startNode",
                "data": {
                    "variables": [{"name": "items"}, {"name": "mail"}]
                },
            },
            {
                "id": "loop",
                "type": "loopNode",
                "data": {
                    "inputs": [
                        {
                            "name": "items",
                            "value_selector": ["source", "items"],
                        },
                        {
                            "name": "mapped_mail",
                            "value_selector": ["source", "mail"],
                        }
                    ],
                    "subGraph": {
                        "nodes": [
                            {
                                "id": "draft",
                                "type": "gmailDraftNode",
                                "data": {
                                    "credential_id": "credential-reference",
                                    "processing_ref_selector": [
                                        "loop",
                                        "item",
                                        "processing_ref",
                                    ],
                                    "reply_body_selector": [
                                        "mapped_mail",
                                        "reply_body",
                                    ],
                                },
                            }
                        ],
                        "edges": [],
                    },
                },
            },
        ],
        "edges": [{"id": "source-loop", "source": "source", "target": "loop"}],
    }

    assert workflow_configuration_issues(graph) == []


def test_loop_subgraph_preflight_rejects_inherited_selectors_after_entry():
    graph = {
        "nodes": [
            {
                "id": "source",
                "type": "startNode",
                "data": {
                    "variables": [{"name": "items"}, {"name": "mail"}]
                },
            },
            {
                "id": "loop",
                "type": "loopNode",
                "data": {
                    "inputs": [
                        {
                            "name": "items",
                            "value_selector": ["source", "items"],
                        },
                        {
                            "name": "mapped_mail",
                            "value_selector": ["source", "mail"],
                        }
                    ],
                    "subGraph": {
                        "nodes": [
                            {
                                "id": "entry",
                                "type": "templateNode",
                                "data": {"template": "continue"},
                            },
                            {
                                "id": "draft",
                                "type": "gmailDraftNode",
                                "data": {
                                    "credential_id": "credential-reference",
                                    "processing_ref_selector": ["loop", "item"],
                                    "reply_body_selector": [
                                        "mapped_mail",
                                        "reply_body",
                                    ],
                                },
                            },
                        ],
                        "edges": [
                            {
                                "id": "entry-draft",
                                "source": "entry",
                                "target": "draft",
                            }
                        ],
                    },
                },
            },
        ],
        "edges": [{"id": "source-loop", "source": "source", "target": "loop"}],
    }

    issues = workflow_configuration_issues(graph)

    assert [
        (issue.node_id, issue.missing_parameters) for issue in issues
    ] == [
        ("draft", ("processing_ref_selector", "reply_body_selector")),
    ]


def test_downstream_nested_loop_does_not_tunnel_outer_inherited_sources():
    graph = {
        "nodes": [
            {
                "id": "source",
                "type": "startNode",
                "data": {
                    "variables": [{"name": "items"}, {"name": "mail"}]
                },
            },
            {
                "id": "outer-loop",
                "type": "loopNode",
                "data": {
                    "inputs": [
                        {
                            "name": "items",
                            "value_selector": ["source", "items"],
                        },
                        {
                            "name": "mapped_mail",
                            "value_selector": ["source", "mail"],
                        }
                    ],
                    "subGraph": {
                        "nodes": [
                            {
                                "id": "entry",
                                "type": "templateNode",
                                "data": {"template": "continue"},
                            },
                            {
                                "id": "inner-loop",
                                "type": "loopNode",
                                "data": {
                                    "subGraph": {
                                        "nodes": [
                                            {
                                                "id": "inner-draft",
                                                "type": "gmailDraftNode",
                                                "data": {
                                                    "credential_id": (
                                                        "credential-reference"
                                                    ),
                                                    "processing_ref_selector": [
                                                        "loop",
                                                        "item",
                                                    ],
                                                    "reply_body_selector": [
                                                        "mapped_mail",
                                                        "reply_body",
                                                    ],
                                                },
                                            }
                                        ],
                                        "edges": [],
                                    }
                                },
                            },
                        ],
                        "edges": [
                            {
                                "id": "entry-inner-loop",
                                "source": "entry",
                                "target": "inner-loop",
                            }
                        ],
                    },
                },
            },
        ],
        "edges": [
            {
                "id": "source-outer-loop",
                "source": "source",
                "target": "outer-loop",
            }
        ],
    }

    issues = workflow_configuration_issues(graph)

    assert [
        (issue.node_id, issue.missing_parameters) for issue in issues
    ] == [
        ("inner-draft", ("reply_body_selector",)),
    ]


def test_loop_subgraph_preflight_rejects_unknown_iteration_context_key():
    graph = {
        "nodes": [
            {
                "id": "loop",
                "type": "loopNode",
                "data": {
                    "subGraph": {
                        "nodes": [
                            {
                                "id": "draft",
                                "type": "gmailDraftNode",
                                "data": {
                                    "credential_id": "credential-reference",
                                    "processing_ref_selector": ["loop", "missing"],
                                    "reply_body_selector": ["loop", "item"],
                                },
                            }
                        ],
                        "edges": [],
                    }
                },
            }
        ],
        "edges": [],
    }

    issues = workflow_configuration_issues(graph)

    assert len(issues) == 1
    assert issues[0].missing_parameters == ("processing_ref_selector",)


def test_loop_subgraph_preflight_rejects_stale_input_mapping_selector():
    graph = {
        "nodes": [
            {
                "id": "source",
                "type": "variableExtractionNode",
                "data": {"mappings": [{"name": "new_name"}]},
            },
            {
                "id": "loop",
                "type": "loopNode",
                "data": {
                    "inputs": [
                        {
                            "name": "mapped_mail",
                            "value_selector": ["source", "old_name"],
                        }
                    ],
                    "subGraph": {
                        "nodes": [
                            {
                                "id": "draft",
                                "type": "gmailDraftNode",
                                "data": {
                                    "credential_id": "credential-reference",
                                    "processing_ref_selector": [
                                        "mapped_mail",
                                        "processing_ref",
                                    ],
                                    "reply_body_selector": ["source", "new_name"],
                                },
                            }
                        ],
                        "edges": [],
                    },
                },
            },
        ],
        "edges": [{"id": "source-loop", "source": "source", "target": "loop"}],
    }

    issues = workflow_configuration_issues(graph)

    assert [
        (issue.node_id, issue.missing_parameters) for issue in issues
    ] == [
        ("loop", ("inputs",)),
        ("draft", ("processing_ref_selector",)),
    ]


@pytest.mark.parametrize(
    "parent_edges",
    [
        [{"id": "loop-later", "source": "loop", "target": "later"}],
        [],
    ],
    ids=["downstream", "sibling"],
)
def test_loop_subgraph_preflight_rejects_non_predecessor_parent_sources(
    parent_edges,
):
    graph = {
        "nodes": [
            {
                "id": "loop",
                "type": "loopNode",
                "data": {
                    "inputs": [
                        {
                            "name": "mapped_mail",
                            "value_selector": ["later", "processing_ref"],
                        }
                    ],
                    "subGraph": {
                        "nodes": [
                            {
                                "id": "draft",
                                "type": "gmailDraftNode",
                                "data": {
                                    "credential_id": "credential-reference",
                                    "processing_ref_selector": [
                                        "mapped_mail",
                                        "processing_ref",
                                    ],
                                    "reply_body_selector": ["later", "reply_body"],
                                },
                            }
                        ],
                        "edges": [],
                    },
                },
            },
            {
                "id": "later",
                "type": "variableExtractionNode",
                "data": {
                    "mappings": [
                        {"name": "processing_ref"},
                        {"name": "reply_body"},
                    ]
                },
            },
        ],
        "edges": parent_edges,
    }

    issues = workflow_configuration_issues(graph)

    assert [
        (issue.node_id, issue.missing_parameters) for issue in issues
    ] == [
        ("loop", ("inputs",)),
        ("draft", ("processing_ref_selector", "reply_body_selector")),
    ]


def test_loop_subgraph_preflight_uses_shared_nesting_depth_limit():
    def nested_graph(depth: int):
        current = {
            "nodes": [
                {
                    "id": "workflow-call",
                    "type": "workflowNode",
                    "data": {"appId": "app-reference"},
                }
            ],
            "edges": [],
        }
        for index in range(depth):
            current = {
                "nodes": [
                    {
                        "id": f"loop-{index}",
                        "type": "loopNode",
                        "data": {"subGraph": current},
                    }
                ],
                "edges": [],
            }
        return current

    assert workflow_configuration_issues(nested_graph(16)) == []

    issues = workflow_configuration_issues(nested_graph(17))

    assert len(issues) == 1
    assert issues[0].node_type == "loopNode"
    assert issues[0].missing_parameters == ("subGraph",)


def test_resolved_external_action_passes_without_external_calls():
    enforce_workflow_configuration_preflight(
        _graph(
            {
                "slackMode": "api",
                "authConfig": {"token": "test-only-placeholder"},
                "channel": "C123",
                "message": "hello",
            }
        ),
        surface="run",
    )


def test_preflight_blocks_selector_that_no_longer_exists_in_source_output_contract():
    graph = {
        "nodes": [
            {
                "id": "extract",
                "type": "variableExtractionNode",
                "data": {"mappings": [{"name": "new_name"}]},
            },
            {
                "id": "draft",
                "type": "gmailDraftNode",
                "data": {
                    "credential_id": "credential-reference",
                    "processing_ref_selector": ["extract", "old_name"],
                    "reply_body_selector": ["extract", "new_name"],
                },
            },
        ],
        "edges": [{"id": "e1", "source": "extract", "target": "draft"}],
    }

    issues = workflow_configuration_issues(graph)

    assert issues[0].missing_parameters == ("processing_ref_selector",)


def test_preflight_accepts_file_extraction_named_selector_storage_shape():
    graph = {
        "nodes": [
            {
                "id": "start",
                "type": "startNode",
                "data": {"variables": [{"name": "file"}]},
            },
            {
                "id": "extract",
                "type": "fileExtractionNode",
                "data": {
                    "referenced_variables": [
                        {"name": "file", "value_selector": ["start", "file"]}
                    ]
                },
            },
        ],
        "edges": [{"id": "e1", "source": "start", "target": "extract"}],
    }

    assert workflow_configuration_issues(graph) == []


def test_preflight_blocks_selector_list_with_missing_source_or_output():
    graph = {
        "nodes": [
            {
                "id": "mail",
                "type": "mailNode",
                "data": {"credential_id": "credential-reference"},
            },
            {
                "id": "draft",
                "type": "gmailDraftNode",
                "data": {
                    "credential_id": "credential-reference",
                    "processing_ref_selector": ["mail", "processing_ref"],
                    "reply_body_selector": ["mail", "emails"],
                },
            },
            {
                "id": "ack",
                "type": "mailAcknowledgeNode",
                "data": {
                    "processing_ref_selector": ["mail", "processing_ref"],
                    "required_effect_ref_selectors": [
                        ["draft", "draft_ref"],
                        ["deleted-node", "draft_ref"],
                    ],
                },
            },
        ],
        "edges": [
            {"id": "mail-draft", "source": "mail", "target": "draft"},
            {"id": "draft-ack", "source": "draft", "target": "ack"},
        ],
    }

    issues = workflow_configuration_issues(graph)

    assert [(issue.node_id, issue.missing_parameters) for issue in issues] == [
        ("ack", ("required_effect_ref_selectors",))
    ]
