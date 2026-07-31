import uuid

import pytest

from apps.gateway.application.deployment.errors import DeploymentPreflightBlocked
from apps.gateway.application.deployment.models import (
    KnowledgeBaseSnapshot,
    KnowledgeCollectionPreflightSnapshot,
    MailCredentialSnapshot,
    NodeCatalogSnapshot,
    WorkflowNodeTargetSnapshot,
)
from apps.gateway.application.deployment.preflight import DeploymentPreflightUseCase
from apps.shared.domain.workflow_node_binding import (
    WorkflowNodeBinding,
    apply_workflow_node_bindings,
    canonical_snapshot_sha256,
)
from apps.shared.domain.workflow_node_location import iter_workflow_node_locations


class _Repository:
    def __init__(self) -> None:
        self.knowledge_bases: dict[uuid.UUID, KnowledgeBaseSnapshot] = {}
        self.collections: dict[uuid.UUID, KnowledgeCollectionPreflightSnapshot] = {}
        self.public_ids: set[uuid.UUID] = set()
        self.targets: dict[uuid.UUID, WorkflowNodeTargetSnapshot] = {}
        self.deployments: dict[
            tuple[uuid.UUID, uuid.UUID], WorkflowNodeTargetSnapshot
        ] = {}
        self.mail_credentials: dict[uuid.UUID, MailCredentialSnapshot] = {}
        self.calls: list[tuple[str, uuid.UUID | None]] = []

    def get_active_knowledge_bases(self, ids, organization_id):
        self.calls.append(("knowledge", organization_id))
        return {
            item_id: self.knowledge_bases[item_id]
            for item_id in ids
            if item_id in self.knowledge_bases
        }

    def get_public_runtime_eligible_knowledge_base_ids(
        self,
        ids,
        organization_id,
    ):
        self.calls.append(("public", organization_id))
        return set(ids) & self.public_ids

    def get_active_knowledge_collections(self, ids, organization_id):
        self.calls.append(("collection", organization_id))
        return {
            item_id: self.collections[item_id]
            for item_id in ids
            if item_id in self.collections
        }

    def get_workflow_node_target(self, app_id, organization_id):
        self.calls.append(("workflow_node", organization_id))
        return self.targets.get(app_id)

    def get_workflow_node_deployment(
        self,
        app_id,
        deployment_id,
        organization_id,
    ):
        self.calls.append(("workflow_node_deployment", organization_id))
        return self.deployments.get((app_id, deployment_id))

    def get_mail_credential_snapshots(
        self,
        ids,
        organization_id,
        principal_id,
    ):
        self.calls.append(("mail", organization_id))
        return {
            item_id: self.mail_credentials[item_id]
            for item_id in ids
            if item_id in self.mail_credentials
        }


class _PermissionDenialAudit:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def record_mail_credential_use_denied(self, **kwargs) -> None:
        self.calls.append(kwargs)


def test_mail_credential_snapshot_contains_only_safe_decision_fields():
    assert set(MailCredentialSnapshot.__dataclass_fields__) == {
        "provider",
        "auth_type",
        "usable_by_principal",
        "effective_auth_state",
    }


def test_internal_chatbot_private_kb_uses_authenticated_audience():
    organization_id = uuid.uuid4()
    kb_id = uuid.uuid4()
    repository = _Repository()
    repository.knowledge_bases[kb_id] = KnowledgeBaseSnapshot(
        id=kb_id,
        source_managed=False,
    )
    use_case = DeploymentPreflightUseCase(
        repository,
        organization_id=organization_id,
    )

    result = use_case.preview(
        deployment_type="internal_chatbot",
        graph_snapshot=_llm_graph(kb_id),
    )

    assert result.audience == "authenticated_user"
    assert result.status == "passed"
    assert repository.calls == [("knowledge", organization_id)]


def test_blocking_result_is_application_error_without_http_dependency():
    organization_id = uuid.uuid4()
    kb_id = uuid.uuid4()
    repository = _Repository()
    repository.knowledge_bases[kb_id] = KnowledgeBaseSnapshot(
        id=kb_id,
        source_managed=False,
    )
    use_case = DeploymentPreflightUseCase(
        repository,
        organization_id=organization_id,
    )

    with pytest.raises(DeploymentPreflightBlocked) as exc_info:
        use_case.enforce_active_publish(
            deployment_type="chatbot",
            graph_snapshot=_llm_graph(kb_id),
        )

    assert exc_info.value.result.status == "blocked"
    assert (
        exc_info.value.result.safe_summary.blocked_reason
        == "private_kb_requires_execution_subject"
    )
    assert repository.calls == [
        ("knowledge", organization_id),
        ("public", organization_id),
    ]


def test_inactive_preview_downgrades_publish_blocker_but_not_structural_target_error():
    organization_id = uuid.uuid4()
    kb_id = uuid.uuid4()
    repository = _Repository()
    repository.knowledge_bases[kb_id] = KnowledgeBaseSnapshot(
        id=kb_id,
        source_managed=False,
    )
    use_case = DeploymentPreflightUseCase(
        repository,
        organization_id=organization_id,
    )

    private_result = use_case.preview(
        deployment_type="api",
        graph_snapshot=_llm_graph(kb_id),
        is_active=False,
    )
    unavailable_result = use_case.preview(
        deployment_type="workflow_node",
        graph_snapshot=_workflow_node_graph(uuid.uuid4()),
        is_active=False,
    )

    assert private_result.status == "warning"
    assert unavailable_result.status == "blocked"
    assert (
        unavailable_result.safe_summary.blocked_reason
        == "workflow_node_target_unavailable"
    )


def test_candidate_workflow_node_graph_requires_existing_scoped_target_and_type():
    organization_id = uuid.uuid4()
    target_app_id = uuid.uuid4()
    repository = _Repository()
    use_case = DeploymentPreflightUseCase(
        repository,
        organization_id=organization_id,
        candidate_graphs_by_app_id={target_app_id: {"nodes": [], "edges": []}},
        candidate_deployment_types_by_app_id={target_app_id: "workflow_node"},
    )

    missing_result = use_case.preview(
        deployment_type="workflow_node",
        graph_snapshot=_workflow_node_graph(target_app_id),
    )
    repository.targets[target_app_id] = WorkflowNodeTargetSnapshot(
        app_id=target_app_id,
        active_graph_snapshot=None,
    )
    present_result = use_case.preview(
        deployment_type="workflow_node",
        graph_snapshot=_workflow_node_graph(target_app_id),
    )

    assert missing_result.status == "blocked"
    assert present_result.status == "blocked"
    assert present_result.safe_summary.blocked_reason == "workflow_node_cycle_detected"


def test_transitive_workflow_node_graph_uses_common_structural_result():
    organization_id = uuid.uuid4()
    target_app_id = uuid.uuid4()
    repository = _Repository()
    repository.targets[target_app_id] = WorkflowNodeTargetSnapshot(
        app_id=target_app_id,
        organization_id=organization_id,
        active_graph_snapshot={"nodes": "invalid", "edges": []},
    )
    use_case = DeploymentPreflightUseCase(
        repository,
        organization_id=organization_id,
    )

    result = use_case.preview(
        deployment_type="api",
        graph_snapshot=_workflow_node_graph(target_app_id),
    )

    assert result.status == "blocked"
    assert result.safe_summary.blocked_reason == "workflow_graph_invalid"


def test_source_managed_public_kb_remains_blocked():
    organization_id = uuid.uuid4()
    kb_id = uuid.uuid4()
    repository = _Repository()
    repository.knowledge_bases[kb_id] = KnowledgeBaseSnapshot(
        id=kb_id,
        source_managed=True,
    )
    repository.public_ids.add(kb_id)
    use_case = DeploymentPreflightUseCase(
        repository,
        organization_id=organization_id,
    )

    result = use_case.preview(
        deployment_type="webhook",
        graph_snapshot=_llm_graph(kb_id),
    )

    assert result.status == "blocked"
    assert result.safe_summary.blocked_reason == "source_public_exposure_required"


def test_authenticated_run_blocks_unresolved_mail_before_publish():
    repository = _Repository()
    use_case = DeploymentPreflightUseCase(
        repository,
        organization_id=uuid.uuid4(),
        principal_id=uuid.uuid4(),
        node_catalog_by_type=_catalog(mailNode=("external_read", True)),
    )

    with pytest.raises(DeploymentPreflightBlocked) as exc_info:
        use_case.enforce_authenticated_run(
            graph_snapshot=_mail_graph(None),
        )

    result = exc_info.value.result
    assert result.safe_summary.blocked_reason == "node_configuration_unresolved"
    assert result.required_actions[0].action == "complete_node_configuration"


def test_mail_resource_failures_share_safe_unavailable_reason():
    organization_id = uuid.uuid4()
    credential_id = uuid.uuid4()
    repository = _Repository()
    repository.mail_credentials[credential_id] = MailCredentialSnapshot(
        provider="imap",
        auth_type="password",
        usable_by_principal=False,
        effective_auth_state="viewer",
    )
    use_case = DeploymentPreflightUseCase(
        repository,
        organization_id=organization_id,
        principal_id=uuid.uuid4(),
        node_catalog_by_type=_catalog(mailNode=("external_read", True)),
    )

    result = use_case.preview(
        deployment_type="workflow_node",
        graph_snapshot=_mail_graph(credential_id),
    )

    assert result.status == "blocked"
    assert result.safe_summary.blocked_reason == "mail_credential_unavailable"
    assert "mail_execution_subject_inherited" in result.warnings


def test_authenticated_run_accepts_complete_gmail_processing_chain():
    organization_id = uuid.uuid4()
    credential_id = uuid.uuid4()
    repository = _Repository()
    repository.mail_credentials[credential_id] = MailCredentialSnapshot(
        provider="gmail",
        auth_type="oauth2",
        usable_by_principal=True,
        effective_auth_state="operator",
    )
    use_case = DeploymentPreflightUseCase(
        repository,
        organization_id=organization_id,
        principal_id=uuid.uuid4(),
        node_catalog_by_type=_mail_processing_catalog(),
    )

    result = use_case.enforce_authenticated_run(
        graph_snapshot=_mail_processing_graph(credential_id),
    )

    assert result.status == "passed"


def test_authenticated_run_rejects_non_gmail_oauth_draft_credential():
    organization_id = uuid.uuid4()
    credential_id = uuid.uuid4()
    repository = _Repository()
    repository.mail_credentials[credential_id] = MailCredentialSnapshot(
        provider="imap",
        auth_type="password",
        usable_by_principal=True,
        effective_auth_state="operator",
    )
    use_case = DeploymentPreflightUseCase(
        repository,
        organization_id=organization_id,
        principal_id=uuid.uuid4(),
        node_catalog_by_type=_mail_processing_catalog(),
    )

    with pytest.raises(DeploymentPreflightBlocked) as exc_info:
        use_case.enforce_authenticated_run(
            graph_snapshot=_mail_processing_graph(credential_id),
        )

    assert exc_info.value.result.safe_summary.blocked_reason == (
        "node_configuration_invalid"
    )


def test_inactive_preview_keeps_invalid_mail_configuration_blocked():
    organization_id = uuid.uuid4()
    credential_id = uuid.uuid4()
    repository = _Repository()
    repository.mail_credentials[credential_id] = MailCredentialSnapshot(
        provider="imap",
        auth_type="password",
        usable_by_principal=True,
        effective_auth_state="operator",
    )
    use_case = DeploymentPreflightUseCase(
        repository,
        organization_id=organization_id,
        principal_id=uuid.uuid4(),
        node_catalog_by_type=_mail_processing_catalog(),
    )

    result = use_case.preview(
        deployment_type="workflow_node",
        graph_snapshot=_mail_processing_graph(credential_id),
        is_active=False,
    )

    assert result.status == "blocked"
    assert result.safe_summary.blocked_reason == "node_configuration_invalid"


def test_public_mail_blocks_on_missing_execution_subject_and_checks_reference():
    credential_id = uuid.uuid4()
    repository = _Repository()
    use_case = DeploymentPreflightUseCase(
        repository,
        organization_id=uuid.uuid4(),
        principal_id=uuid.uuid4(),
        node_catalog_by_type=_catalog(mailNode=("external_read", True)),
    )

    result = use_case.preview(
        deployment_type="schedule",
        graph_snapshot=_mail_graph(credential_id),
    )

    assert result.status == "blocked"
    assert result.safe_summary.blocked_reason == "mail_execution_subject_required"
    assert any(call[0] == "mail" for call in repository.calls)


def test_public_mail_does_not_report_permission_denial_without_a_principal():
    credential_id = uuid.uuid4()
    repository = _Repository()
    repository.mail_credentials[credential_id] = MailCredentialSnapshot(
        provider="imap",
        auth_type="password",
        usable_by_principal=False,
        effective_auth_state="none",
    )
    use_case = DeploymentPreflightUseCase(
        repository,
        organization_id=uuid.uuid4(),
        principal_id=None,
        node_catalog_by_type=_catalog(mailNode=("external_read", True)),
    )

    result = use_case.preview(
        deployment_type="schedule",
        graph_snapshot=_mail_graph(credential_id),
    )

    reason_codes = {
        reason for node_result in result.nodes for reason in node_result.reason_codes
    }
    assert result.status == "blocked"
    assert reason_codes == {"mail_execution_subject_required"}


def test_inactive_preview_downgrades_fixable_mail_configuration():
    use_case = DeploymentPreflightUseCase(
        _Repository(),
        organization_id=uuid.uuid4(),
        principal_id=uuid.uuid4(),
        node_catalog_by_type=_catalog(mailNode=("external_read", True)),
    )

    result = use_case.preview(
        deployment_type="workflow_node",
        graph_snapshot=_mail_graph(None),
        is_active=False,
    )

    assert result.status == "warning"
    assert result.safe_summary.blocked_reason == "node_configuration_unresolved"


@pytest.mark.parametrize(
    ("credential_id", "configuration_state"),
    [
        (None, "resolved"),
        (None, None),
        ("", "unresolved"),
    ],
)
def test_inactive_preview_rejects_explicit_invalid_null_or_empty_reference_state(
    credential_id,
    configuration_state,
):
    use_case = DeploymentPreflightUseCase(
        _Repository(),
        organization_id=uuid.uuid4(),
        principal_id=uuid.uuid4(),
        node_catalog_by_type=_catalog(mailNode=("external_read", True)),
    )
    graph = _mail_graph(None)
    mail_data = graph["nodes"][1]["data"]
    mail_data["credential_id"] = credential_id
    mail_data["configuration_state"] = configuration_state

    result = use_case.preview(
        deployment_type="workflow_node",
        graph_snapshot=graph,
        is_active=False,
    )

    assert result.status == "blocked"
    assert result.safe_summary.blocked_reason == "node_configuration_invalid"


def test_inactive_preview_treats_legacy_missing_mail_state_as_unresolved():
    use_case = DeploymentPreflightUseCase(
        _Repository(),
        organization_id=uuid.uuid4(),
        principal_id=uuid.uuid4(),
        node_catalog_by_type=_catalog(mailNode=("external_read", True)),
    )
    graph = _mail_graph(None)
    graph["nodes"][1]["data"].pop("configuration_state")

    result = use_case.preview(
        deployment_type="workflow_node",
        graph_snapshot=graph,
        is_active=False,
    )

    assert result.status == "warning"
    assert result.safe_summary.blocked_reason == "node_configuration_unresolved"


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("folder", "ARCHIVE"),
        ("parameters", {"unexpected": True}),
        ("title", None),
    ],
)
def test_inactive_preview_does_not_hide_invalid_mail_fields_behind_unresolved_reference(
    field_name,
    invalid_value,
):
    repository = _Repository()
    use_case = DeploymentPreflightUseCase(
        repository,
        organization_id=uuid.uuid4(),
        principal_id=uuid.uuid4(),
        node_catalog_by_type=_catalog(mailNode=("external_read", True)),
    )
    graph = _mail_graph(None)
    graph["nodes"][1]["data"][field_name] = invalid_value

    result = use_case.preview(
        deployment_type="workflow_node",
        graph_snapshot=graph,
        is_active=False,
    )

    assert result.status == "blocked"
    assert result.safe_summary.blocked_reason == "node_configuration_invalid"
    assert not any(call[0] == "mail" for call in repository.calls)


def test_inactive_preview_keeps_non_null_unavailable_mail_blocked():
    use_case = DeploymentPreflightUseCase(
        _Repository(),
        organization_id=uuid.uuid4(),
        principal_id=uuid.uuid4(),
        node_catalog_by_type=_catalog(mailNode=("external_read", True)),
    )

    result = use_case.preview(
        deployment_type="workflow_node",
        graph_snapshot=_mail_graph(uuid.uuid4()),
        is_active=False,
    )

    assert result.status == "blocked"
    assert result.safe_summary.blocked_reason == "mail_credential_unavailable"


def test_inactive_preview_downgrades_only_fixable_slack_configuration():
    use_case = DeploymentPreflightUseCase(
        _Repository(),
        organization_id=uuid.uuid4(),
        principal_id=uuid.uuid4(),
        node_catalog_by_type=_catalog(slackPostNode=("external_write", True)),
    )

    result = use_case.preview(
        deployment_type="workflow_node",
        graph_snapshot=_slack_graph(
            {
                "title": "Slack",
                "slackMode": "api",
                "authConfig": {},
                "configuration_state": "unresolved",
                "channel_resolution_state": "unresolved",
            }
        ),
        is_active=False,
    )

    assert result.status == "warning"
    assert result.safe_summary.blocked_reason == "node_configuration_unresolved"


@pytest.mark.parametrize(
    "payload",
    (
        {"message": "   ", "blocks": None, "attachments": None},
        {"message": "", "blocks": "[]", "attachments": "[]"},
    ),
)
def test_inactive_preview_treats_empty_slack_payload_as_unresolved(payload):
    use_case = DeploymentPreflightUseCase(
        _Repository(),
        organization_id=uuid.uuid4(),
        principal_id=uuid.uuid4(),
        node_catalog_by_type=_catalog(slackPostNode=("external_write", True)),
    )

    result = use_case.preview(
        deployment_type="workflow_node",
        graph_snapshot=_slack_graph(
            {
                "title": "Slack",
                "slackMode": "api",
                "authConfig": {"token": "configuration-ready"},
                "channel": "C123",
                "configuration_state": "unresolved",
                **payload,
            }
        ),
        is_active=False,
    )

    assert result.status == "warning"
    assert result.safe_summary.blocked_reason == "node_configuration_unresolved"


def test_inactive_preview_does_not_hide_invalid_slack_fields_behind_missing_values():
    use_case = DeploymentPreflightUseCase(
        _Repository(),
        organization_id=uuid.uuid4(),
        principal_id=uuid.uuid4(),
        node_catalog_by_type=_catalog(slackPostNode=("external_write", True)),
    )

    result = use_case.preview(
        deployment_type="workflow_node",
        graph_snapshot=_slack_graph(
            {
                "title": "Slack",
                "slackMode": "api",
                "authConfig": {},
                "parameters": {"unexpected": True},
                "configuration_state": "unresolved",
            }
        ),
        is_active=False,
    )

    assert result.status == "blocked"
    assert result.safe_summary.blocked_reason == "node_configuration_invalid"


def test_inactive_preview_preserves_slack_invalid_issue_with_unresolved_node():
    use_case = DeploymentPreflightUseCase(
        _Repository(),
        organization_id=uuid.uuid4(),
        principal_id=uuid.uuid4(),
        node_catalog_by_type=_catalog(slackPostNode=("external_write", True)),
    )
    graph = _slack_graph(
        {
            "title": "Slack",
            "slackMode": "api",
            "authConfig": {},
            "configuration_state": "unresolved",
        }
    )
    graph["nodes"].append(
        _node(
            "slack-legacy",
            "slackPostNode",
            {
                "title": "Slack legacy selector",
                "slackMode": "api",
                "authConfig": {"token": "configuration-ready"},
                "channel": "C123",
                "message": "{{legacy}}",
                "referenced_variables": [
                    {
                        "name": "legacy",
                        "value_selector": ["slack-legacy", "data"],
                    }
                ],
            },
        )
    )
    graph["edges"].append(_edge("start", "slack-legacy", "start-slack-legacy"))

    result = use_case.preview(
        deployment_type="workflow_node",
        graph_snapshot=graph,
        is_active=False,
    )

    reason_codes = {
        reason for node_result in result.nodes for reason in node_result.reason_codes
    }
    assert result.status == "blocked"
    assert reason_codes == {
        "node_configuration_invalid",
        "node_configuration_unresolved",
    }


def test_preview_does_not_audit_mail_permission_denial():
    credential_id = uuid.uuid4()
    repository = _Repository()
    repository.mail_credentials[credential_id] = MailCredentialSnapshot(
        provider="imap",
        auth_type="password",
        usable_by_principal=False,
        effective_auth_state="viewer",
    )
    audit = _PermissionDenialAudit()
    use_case = DeploymentPreflightUseCase(
        repository,
        organization_id=uuid.uuid4(),
        principal_id=uuid.uuid4(),
        node_catalog_by_type=_catalog(mailNode=("external_read", True)),
        permission_denial_audit=audit,
    )

    use_case.preview(
        deployment_type="workflow_node",
        graph_snapshot=_mail_graph(credential_id),
    )

    assert audit.calls == []


def test_enforcement_audits_each_denied_mail_credential_once():
    organization_id = uuid.uuid4()
    principal_id = uuid.uuid4()
    credential_id = uuid.uuid4()
    repository = _Repository()
    repository.mail_credentials[credential_id] = MailCredentialSnapshot(
        provider="imap",
        auth_type="password",
        usable_by_principal=False,
        effective_auth_state="viewer",
    )
    audit = _PermissionDenialAudit()
    graph = _mail_graph(credential_id)
    graph["nodes"].append(_node("mail-2", "mailNode", dict(graph["nodes"][1]["data"])))
    graph["edges"].append(_edge("start", "mail-2", "start-mail-2"))
    use_case = DeploymentPreflightUseCase(
        repository,
        organization_id=organization_id,
        principal_id=principal_id,
        node_catalog_by_type=_catalog(mailNode=("external_read", True)),
        permission_denial_audit=audit,
    )

    with pytest.raises(DeploymentPreflightBlocked):
        use_case.enforce_authenticated_run(graph_snapshot=graph)

    assert audit.calls == [
        {
            "principal_id": principal_id,
            "organization_id": organization_id,
            "credential_id": credential_id,
            "effective_auth_state": "viewer",
        }
    ]


def test_enforcement_does_not_audit_missing_mail_credential():
    audit = _PermissionDenialAudit()
    use_case = DeploymentPreflightUseCase(
        _Repository(),
        organization_id=uuid.uuid4(),
        principal_id=uuid.uuid4(),
        node_catalog_by_type=_catalog(mailNode=("external_read", True)),
        permission_denial_audit=audit,
    )

    with pytest.raises(DeploymentPreflightBlocked):
        use_case.enforce_authenticated_run(
            graph_snapshot=_mail_graph(uuid.uuid4()),
        )

    assert audit.calls == []


def test_inactive_save_allows_warning_but_rejects_structural_blocker():
    mail_use_case = DeploymentPreflightUseCase(
        _Repository(),
        organization_id=uuid.uuid4(),
        principal_id=uuid.uuid4(),
        node_catalog_by_type=_catalog(mailNode=("external_read", True)),
    )

    result = mail_use_case.enforce_inactive_save(
        deployment_type="workflow_node",
        graph_snapshot=_mail_graph(None),
    )

    assert result.status == "warning"

    malformed_use_case = DeploymentPreflightUseCase(
        _Repository(),
        organization_id=uuid.uuid4(),
        node_catalog_by_type={},
    )
    with pytest.raises(DeploymentPreflightBlocked):
        malformed_use_case.enforce_inactive_save(
            deployment_type="api",
            graph_snapshot={"nodes": "invalid", "edges": []},
        )


def test_unimplemented_external_node_is_not_downgraded_for_inactive_preview():
    use_case = DeploymentPreflightUseCase(
        _Repository(),
        organization_id=uuid.uuid4(),
        node_catalog_by_type=_catalog(pluginNode=("external_write", False)),
    )
    graph = {
        "nodes": [
            _node("start", "startNode"),
            _node("plugin-1", "pluginNode"),
        ],
        "edges": [_edge("start", "plugin-1", "start-plugin")],
    }

    result = use_case.preview(
        deployment_type="api",
        graph_snapshot=graph,
        is_active=False,
    )

    assert result.status == "blocked"
    assert (
        result.safe_summary.blocked_reason == "node_configuration_validator_unavailable"
    )


def test_runtime_authoritative_external_node_remains_supported():
    use_case = DeploymentPreflightUseCase(
        _Repository(),
        organization_id=uuid.uuid4(),
        node_catalog_by_type=_catalog(llmNode=("external_read", True)),
    )
    graph = {
        "nodes": [
            _node("start", "startNode"),
            _node("llm-1", "llmNode"),
        ],
        "edges": [_edge("start", "llm-1", "start-llm")],
    }

    result = use_case.preview(deployment_type="api", graph_snapshot=graph)

    assert result.status == "passed"


def test_canvas_note_is_allowed_without_catalog_runtime_definition():
    use_case = DeploymentPreflightUseCase(
        _Repository(),
        organization_id=uuid.uuid4(),
        node_catalog_by_type=_catalog(),
    )

    result = use_case.preview(
        deployment_type="api",
        graph_snapshot={
            "nodes": [
                _node("start", "startNode"),
                _node("note-1", "note"),
            ],
            "edges": [],
        },
    )

    assert result.status == "passed"


def test_nested_loop_mail_uses_same_authenticated_principal():
    organization_id = uuid.uuid4()
    principal_id = uuid.uuid4()
    credential_id = uuid.uuid4()
    repository = _Repository()
    repository.mail_credentials[credential_id] = MailCredentialSnapshot(
        provider="imap",
        auth_type="password",
        usable_by_principal=True,
        effective_auth_state="operator",
    )
    use_case = DeploymentPreflightUseCase(
        repository,
        organization_id=organization_id,
        principal_id=principal_id,
        node_catalog_by_type=_catalog(
            loopNode=("local_execution", True),
            mailNode=("external_read", True),
        ),
    )
    graph = {
        "nodes": [
            _node("start", "startNode"),
            _node(
                "loop-1",
                "loopNode",
                {"subGraph": _mail_graph(credential_id)},
            ),
        ],
        "edges": [_edge("start", "loop-1", "start-loop")],
    }

    result = use_case.enforce_authenticated_run(graph_snapshot=graph)

    assert result.status == "passed"
    assert ("mail", organization_id) in repository.calls


def test_workflow_node_preflight_uses_exact_bound_deployment_snapshot():
    organization_id = uuid.uuid4()
    principal_id = uuid.uuid4()
    target_app_id = uuid.uuid4()
    target_workflow_id = uuid.uuid4()
    bound_deployment_id = uuid.uuid4()
    credential_id = uuid.uuid4()
    bound_graph = _mail_graph(credential_id)
    repository = _Repository()
    repository.targets[target_app_id] = WorkflowNodeTargetSnapshot(
        app_id=target_app_id,
        organization_id=organization_id,
        workflow_id=target_workflow_id,
        deployment_id=uuid.uuid4(),
        deployment_version=2,
        deployment_type="workflow_node",
        active_graph_snapshot={"nodes": [], "edges": []},
        active_pointer_valid=True,
    )
    repository.deployments[(target_app_id, bound_deployment_id)] = (
        WorkflowNodeTargetSnapshot(
            app_id=target_app_id,
            organization_id=organization_id,
            workflow_id=target_workflow_id,
            deployment_id=bound_deployment_id,
            deployment_version=1,
            deployment_type="workflow_node",
            active_graph_snapshot=bound_graph,
            active_pointer_valid=True,
        )
    )
    root_graph = apply_workflow_node_bindings(
        _workflow_node_graph(target_app_id),
        (
            WorkflowNodeBinding(
                container_path=(),
                workflow_node_id="workflow-1",
                target_app_id=target_app_id,
                deployment_id=bound_deployment_id,
                deployment_version=1,
                snapshot_sha256=canonical_snapshot_sha256(bound_graph),
            ),
        ),
    )
    use_case = DeploymentPreflightUseCase(
        repository,
        organization_id=organization_id,
        principal_id=principal_id,
        node_catalog_by_type=_catalog(
            workflowNode=("local_execution", True),
            mailNode=("external_read", True),
        ),
    )

    with pytest.raises(DeploymentPreflightBlocked) as exc_info:
        use_case.enforce_authenticated_run(graph_snapshot=root_graph)

    assert exc_info.value.result.safe_summary.blocked_reason == (
        "mail_credential_unavailable"
    )
    assert ("workflow_node_deployment", organization_id) in repository.calls


def test_malformed_graph_fails_closed():
    use_case = DeploymentPreflightUseCase(
        _Repository(),
        organization_id=uuid.uuid4(),
        node_catalog_by_type={},
    )

    result = use_case.preview(
        deployment_type="api",
        graph_snapshot={"nodes": "invalid", "edges": []},
        is_active=False,
    )

    assert result.status == "blocked"
    assert result.safe_summary.blocked_reason == "workflow_graph_invalid"


def test_unencodable_node_id_fails_closed_before_resource_lookup():
    repository = _Repository()
    use_case = DeploymentPreflightUseCase(
        repository,
        organization_id=uuid.uuid4(),
        node_catalog_by_type={},
    )

    result = use_case.preview(
        deployment_type="api",
        graph_snapshot={
            "nodes": [
                {
                    "id": "\ud800",
                    "type": "startNode",
                    "position": {"x": 0, "y": 0},
                    "data": {},
                }
            ],
            "edges": [],
        },
        is_active=False,
    )

    assert result.status == "blocked"
    assert result.safe_summary.blocked_reason == "workflow_graph_invalid"
    assert repository.calls == []


def test_malformed_runtime_authoritative_node_data_fails_closed():
    use_case = DeploymentPreflightUseCase(
        _Repository(),
        organization_id=uuid.uuid4(),
        node_catalog_by_type=_catalog(llmNode=("external_read", True)),
    )

    result = use_case.preview(
        deployment_type="api",
        graph_snapshot={
            "nodes": [{"id": "llm", "type": "llmNode", "data": None}],
            "edges": [],
        },
        is_active=False,
    )

    assert result.status == "blocked"
    assert result.safe_summary.blocked_reason == "workflow_graph_invalid"


@pytest.mark.parametrize(
    ("references", "expected_reason"),
    [
        ([{"id": "not-a-uuid"}], "knowledge_reference_invalid"),
        (
            [{"id": str(uuid.uuid4())} for _index in range(21)],
            "knowledge_reference_limit_exceeded",
        ),
    ],
)
def test_malformed_or_over_limit_collection_graph_is_non_downgradable(
    references,
    expected_reason,
):
    repository = _Repository()

    result = DeploymentPreflightUseCase(
        repository,
        organization_id=uuid.uuid4(),
    ).preview(
        deployment_type="api",
        graph_snapshot=_collection_graph(references),
        is_active=False,
    )

    assert result.status == "blocked"
    assert result.safe_summary.blocked_reason == expected_reason
    assert repository.calls == []


def test_anonymous_private_collection_is_blocked_with_collection_action():
    organization_id = uuid.uuid4()
    collection_id = uuid.uuid4()
    repository = _Repository()
    repository.collections[collection_id] = _collection_snapshot(
        collection_id,
        public=False,
    )

    result = DeploymentPreflightUseCase(
        repository,
        organization_id=organization_id,
    ).preview(
        deployment_type="api",
        graph_snapshot=_collection_graph([{"id": str(collection_id)}]),
    )

    assert result.status == "blocked"
    assert (
        result.safe_summary.blocked_reason
        == "private_collection_requires_execution_subject"
    )
    assert result.required_actions[0].action == (
        "remove_private_collection_or_use_authenticated_run"
    )
    assert result.safe_summary.affected_collection_count_bucket == "1"


def test_anonymous_public_manual_collection_passes():
    organization_id = uuid.uuid4()
    collection_id = uuid.uuid4()
    repository = _Repository()
    repository.collections[collection_id] = _collection_snapshot(
        collection_id,
        public=True,
        candidate_member_count=3,
    )

    result = DeploymentPreflightUseCase(
        repository,
        organization_id=organization_id,
    ).preview(
        deployment_type="webhook",
        graph_snapshot=_collection_graph([{"id": str(collection_id)}]),
    )

    assert result.status == "passed"
    assert result.nodes == ()
    assert repository.calls == [("collection", organization_id)]


@pytest.mark.parametrize("source_managed_field", ["collection", "member"])
def test_public_collection_with_source_managed_content_is_blocked_without_identity(
    source_managed_field,
):
    organization_id = uuid.uuid4()
    collection_id = uuid.uuid4()
    repository = _Repository()
    repository.collections[collection_id] = _collection_snapshot(
        collection_id,
        public=True,
        source_managed=source_managed_field == "collection",
        has_source_managed_members=source_managed_field == "member",
    )

    result = DeploymentPreflightUseCase(
        repository,
        organization_id=organization_id,
    ).preview(
        deployment_type="chatbot",
        graph_snapshot=_collection_graph(
            [{"id": str(collection_id), "safeLabel": "Hidden source"}]
        ),
    )

    assert result.status == "blocked"
    assert result.safe_summary.blocked_reason == "source_public_exposure_required"
    assert str(collection_id) not in str(result)
    assert "Hidden source" not in str(result)


def test_collection_lifecycle_is_checked_for_authenticated_and_inherited_audience():
    organization_id = uuid.uuid4()
    active_collection_id = uuid.uuid4()
    missing_collection_id = uuid.uuid4()
    repository = _Repository()
    repository.collections[active_collection_id] = _collection_snapshot(
        active_collection_id,
        public=False,
    )
    use_case = DeploymentPreflightUseCase(
        repository,
        organization_id=organization_id,
    )
    graph = _collection_graph(
        [
            {"id": str(active_collection_id)},
            {"id": str(missing_collection_id)},
        ]
    )

    authenticated = use_case.preview(
        deployment_type="api",
        graph_snapshot=graph,
        trusted_audience_override="authenticated_user",
    )
    inherited = use_case.preview(
        deployment_type="workflow_node",
        graph_snapshot=graph,
    )

    assert authenticated.status == "blocked"
    assert authenticated.audience == "authenticated_user"
    assert authenticated.safe_summary.blocked_reason == (
        "knowledge_collection_unavailable"
    )
    assert inherited.status == "blocked"
    assert inherited.warnings == ("workflow_node_execution_subject_inherited",)


def test_collection_candidate_budget_warning_is_conservative_and_bucketed():
    organization_id = uuid.uuid4()
    collection_id = uuid.uuid4()
    repository = _Repository()
    repository.collections[collection_id] = _collection_snapshot(
        collection_id,
        public=True,
        candidate_member_count=21,
    )

    result = DeploymentPreflightUseCase(
        repository,
        organization_id=organization_id,
    ).preview(
        deployment_type="api",
        graph_snapshot=_collection_graph([{"id": str(collection_id)}]),
    )

    assert result.status == "warning"
    assert result.safe_summary.candidate_budget_limited is True
    assert result.nodes[0].candidate_budget_limited is True
    assert result.nodes[0].knowledge_collection_count_bucket == "1"
    assert result.warnings == ("knowledge_candidate_budget_limited",)
    assert "21" not in str(result)


def test_nested_loop_collection_is_evaluated_with_parent_audience():
    organization_id = uuid.uuid4()
    collection_id = uuid.uuid4()
    repository = _Repository()
    repository.collections[collection_id] = _collection_snapshot(
        collection_id,
        public=False,
    )
    loop_body = {
        "nodes": [
            _node(
                "llm-collection",
                "llmNode",
                {"knowledgeCollections": [{"id": str(collection_id)}]},
            )
        ],
        "edges": [],
    }
    graph = {
        "nodes": [
            _node("start", "startNode"),
            _node("loop-1", "loopNode", {"subGraph": loop_body}),
        ],
        "edges": [_edge("start", "loop-1", "start-loop")],
    }

    result = DeploymentPreflightUseCase(
        repository,
        organization_id=organization_id,
    ).preview(
        deployment_type="chatbot",
        graph_snapshot=graph,
    )

    assert result.status == "blocked"
    assert result.safe_summary.blocked_reason == (
        "private_collection_requires_execution_subject"
    )


def test_nested_collection_beyond_shared_graph_depth_limit_fails_closed():
    collection_id = uuid.uuid4()
    body = {
        "nodes": [
            _node(
                "llm-collection",
                "llmNode",
                {"knowledgeCollections": [{"id": str(collection_id)}]},
            )
        ],
        "edges": [],
    }
    for index in range(17):
        body = {
            "nodes": [_node(f"loop-{index}", "loopNode", {"subGraph": body})],
            "edges": [],
        }
    graph = {
        "nodes": [
            _node("start", "startNode"),
            _node("root-loop", "loopNode", {"subGraph": body}),
        ],
        "edges": [_edge("start", "root-loop", "start-root-loop")],
    }
    repository = _Repository()

    result = DeploymentPreflightUseCase(
        repository,
        organization_id=uuid.uuid4(),
    ).preview(
        deployment_type="chatbot",
        graph_snapshot=graph,
    )

    assert result.status == "blocked"
    assert result.safe_summary.blocked_reason == "workflow_graph_invalid"
    assert repository.calls == []


def _node(node_id: str, node_type: str, data: dict | None = None) -> dict:
    return {
        "id": node_id,
        "type": node_type,
        "position": {"x": 0, "y": 0},
        "data": data or {},
    }


def _edge(source: str, target: str, edge_id: str) -> dict:
    return {"id": edge_id, "source": source, "target": target}


def _llm_graph(kb_id: uuid.UUID) -> dict:
    return {
        "nodes": [
            _node("start", "startNode"),
            _node(
                "llm-1",
                "llmNode",
                {"knowledgeBases": [{"id": str(kb_id), "name": "KB"}]},
            ),
        ],
        "edges": [_edge("start", "llm-1", "start-llm")],
    }


def _workflow_node_graph(app_id: uuid.UUID) -> dict:
    return {
        "nodes": [
            _node("start", "startNode"),
            _node("workflow-1", "workflowNode", {"appId": str(app_id)}),
        ],
        "edges": [_edge("start", "workflow-1", "start-workflow")],
    }


def _collection_graph(references: list[dict]) -> dict:
    return {
        "nodes": [
            _node("start", "startNode"),
            _node(
                "llm-collection",
                "llmNode",
                {"knowledgeCollections": references},
            ),
        ],
        "edges": [_edge("start", "llm-collection", "start-collection")],
    }


def _collection_snapshot(
    collection_id: uuid.UUID,
    *,
    public: bool,
    source_managed: bool = False,
    has_source_managed_members: bool = False,
    candidate_member_count: int = 0,
) -> KnowledgeCollectionPreflightSnapshot:
    return KnowledgeCollectionPreflightSnapshot(
        id=collection_id,
        public=public,
        source_managed=source_managed,
        has_source_managed_members=has_source_managed_members,
        candidate_member_count=candidate_member_count,
    )


def _mail_graph(credential_id: uuid.UUID | None) -> dict:
    return {
        "nodes": [
            _node("start", "startNode"),
            _node(
                "mail-1",
                "mailNode",
                {
                    "title": "Mail",
                    "parameters": {},
                    "credential_id": (
                        str(credential_id) if credential_id is not None else None
                    ),
                    "configuration_state": (
                        "resolved" if credential_id is not None else "unresolved"
                    ),
                    "processing_mode": "search_only",
                },
            ),
        ],
        "edges": [_edge("start", "mail-1", "start-mail")],
    }


def _mail_processing_graph(credential_id: uuid.UUID) -> dict:
    processing_selector = ["mail", "processing_ref"]
    return {
        "nodes": [
            _node("start", "startNode"),
            _node(
                "mail",
                "mailNode",
                {
                    "title": "Mail",
                    "credential_id": str(credential_id),
                    "configuration_state": "resolved",
                    "processing_mode": "durable",
                    "max_results": 1,
                    "mark_as_read": False,
                },
            ),
            _node("llm", "llmNode"),
            _node(
                "draft",
                "gmailDraftNode",
                {
                    "title": "Gmail Draft",
                    "credential_id": str(credential_id),
                    "configuration_state": "resolved",
                    "processing_ref_selector": processing_selector,
                    "reply_body_selector": ["llm", "result"],
                },
            ),
            _node(
                "ack",
                "mailAcknowledgeNode",
                {
                    "title": "Mail Acknowledge",
                    "processing_ref_selector": processing_selector,
                    "required_effect_ref_selectors": [["draft", "draft_ref"]],
                },
            ),
        ],
        "edges": [
            _edge("start", "mail", "start-mail"),
            _edge("mail", "llm", "mail-llm"),
            _edge("llm", "draft", "llm-draft"),
            _edge("draft", "ack", "draft-ack"),
        ],
    }


def _mail_processing_catalog() -> dict[str, NodeCatalogSnapshot]:
    return _catalog(
        mailNode=("external_read", True),
        llmNode=("external_write", True),
        gmailDraftNode=("external_write", True),
        mailAcknowledgeNode=("external_write", True),
    )


def _slack_graph(data: dict) -> dict:
    return {
        "nodes": [
            _node("start", "startNode"),
            _node("slack", "slackPostNode", data),
        ],
        "edges": [_edge("start", "slack", "start-slack")],
    }


def _catalog(**definitions: tuple[str, bool]) -> dict[str, NodeCatalogSnapshot]:
    snapshots = {
        node_type: NodeCatalogSnapshot(
            side_effect=side_effect,
            implemented=implemented,
        )
        for node_type, (side_effect, implemented) in definitions.items()
    }
    snapshots["startNode"] = NodeCatalogSnapshot(
        side_effect="none",
        implemented=True,
    )
    return snapshots


def test_preflight_uses_the_shared_canonical_nested_node_locations() -> None:
    graph = {
        "nodes": [
            _node(
                "loop-a",
                "loopNode",
                {
                    "subGraph": {
                        "nodes": [_node("llm-1", "llmNode")],
                        "edges": [],
                    }
                },
            )
        ],
        "edges": [],
    }

    shared = [
        (located.location.container_path, located.location.node_id)
        for located in iter_workflow_node_locations(graph)
    ]
    preflight = [
        (container_path, node["id"])
        for node, container_path in DeploymentPreflightUseCase._iter_graph_nodes(
            graph
        )
    ]

    assert preflight == shared
