import uuid
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy.sql.operators import eq, in_op, is_, ne

from apps.gateway.services import deployment_service as deployment_module
from apps.gateway.services.deployment_service import DeploymentService
from apps.gateway.services.knowledge_deployment_preflight_service import (
    KnowledgeDeploymentPreflightService,
)
from apps.shared.db.models.app import App
from apps.shared.db.models.knowledge import (
    KnowledgeBase,
    KnowledgeCollection,
    KnowledgeCollectionItem,
)
from apps.shared.db.models.mail_credential import MailCredential
from apps.shared.db.models.schedule import Schedule
from apps.shared.db.models.workflow import Workflow
from apps.shared.db.models.workflow_deployment import DeploymentType, WorkflowDeployment
from apps.shared.db.models.workflow_node_secret import WorkflowNodeSecret
from apps.shared.domain.deployment_runtime_policy import (
    DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
)
from apps.shared.schemas.deployment import DeploymentCreate
from apps.shared.services.credential_encryption import CredentialEncryptionService
from apps.shared.services.workflow_configuration_preflight import (
    WorkflowConfigurationIssue,
    WorkflowConfigurationPreflightError,
)


def _workflow_node_secret_encryption() -> CredentialEncryptionService:
    return CredentialEncryptionService(
        {"v1": Fernet.generate_key().decode("utf-8")},
        "v1",
        subject_label="Workflow node secret",
    )


def test_preflight_blocks_private_kb_for_public_surface():
    organization_id = uuid.uuid4()
    kb_id = uuid.uuid4()
    db = _Db(
        {
            KnowledgeBase: [
                _row(
                    id=kb_id,
                    organization_id=organization_id,
                    lifecycle_state="active",
                    source_identity_id=None,
                )
            ]
        }
    )

    result = KnowledgeDeploymentPreflightService(
        db,
        organization_id=organization_id,
    ).preview(
        deployment_type=DeploymentType.CHATBOT,
        graph_snapshot=_llm_graph(kb_id),
    )

    assert result.status == "blocked"
    assert result.safe_summary.blocked_reason == "private_kb_requires_execution_subject"
    assert result.safe_summary.affected_kb_count_bucket == "1"
    assert str(kb_id) not in result.model_dump_json()


def test_unresolved_configuration_uses_workflow_409_preflight_envelope():
    blocked = DeploymentService.workflow_configuration_preflight_blocked(
        WorkflowConfigurationPreflightError(
            "test",
            [
                WorkflowConfigurationIssue(
                    node_id="node-safe-id",
                    node_type="slackPostNode",
                    missing_parameters=("credential", "channel"),
                )
            ],
        )
    )

    assert blocked.status_code == 409
    assert blocked.detail["error"]["code"] == "workflow.configuration_preflight.blocked"
    assert blocked.detail["error"]["reason_code"] == "node_configuration_unresolved"
    assert blocked.detail["error"]["required_actions"] == [
        "complete_node_configuration"
    ]


def test_deployment_snapshot_rejects_raw_workflow_node_secret() -> None:
    with pytest.raises(HTTPException) as captured:
        DeploymentService._enforce_node_secret_storage_boundary(
            object(),
            {
                "nodes": [
                    {
                        "id": "slack-1",
                        "type": "slackPostNode",
                        "data": {
                            "slackMode": "api",
                            "authConfig": {"token": "synthetic-raw-value"},
                        },
                    }
                ],
                "edges": [],
            },
            workflow_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
        )

    assert captured.value.status_code == 422
    assert captured.value.detail == "workflow.node_secret_reference_required"


def test_deployment_snapshot_rejects_secret_reference_owned_by_another_node() -> None:
    workflow_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    secret_id = uuid.uuid4()
    db = _Db(
        {
            WorkflowNodeSecret: [
                _row(
                    id=secret_id,
                    workflow_id=workflow_id,
                    organization_id=organization_id,
                    node_id="original-slack",
                    node_type="slackPostNode",
                    parameter_key="bot_token",
                    status="active",
                )
            ]
        }
    )

    with pytest.raises(HTTPException) as captured:
        DeploymentService._enforce_node_secret_storage_boundary(
            db,
            {
                "nodes": [
                    {
                        "id": "copied-slack",
                        "type": "slackPostNode",
                        "data": {
                            "slackMode": "api",
                            "authConfig": {
                                "token": f"workflow-node-secret://{secret_id}"
                            },
                        },
                    }
                ],
                "edges": [],
            },
            workflow_id=workflow_id,
            organization_id=organization_id,
        )

    assert captured.value.status_code == 422
    assert captured.value.detail == "workflow.node_secret_reference_invalid"


def test_legacy_deployment_snapshot_is_migrated_before_reuse(monkeypatch) -> None:
    app_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    deployment = _row(
        id=uuid.uuid4(),
        app_id=app_id,
        created_by=actor_id,
        graph_snapshot={
            "nodes": [
                {
                    "id": "github-1",
                    "type": "githubNode",
                    "data": {"api_token": "synthetic-raw-value"},
                }
            ],
            "edges": [],
        },
    )
    db = _Db(
        {
            App: [_row(id=app_id, workflow_id=workflow_id)],
            Workflow: [
                _row(
                    id=workflow_id,
                    organization_id=organization_id,
                    created_by=actor_id,
                    updated_by=None,
                )
            ],
            WorkflowDeployment: [deployment],
        }
    )
    monkeypatch.setattr(
        deployment_module,
        "get_workflow_node_secret_encryption_service",
        lambda: _workflow_node_secret_encryption(),
    )

    result = DeploymentService.migrate_legacy_node_secrets(
        db,
        deployment,
        actor_id=actor_id,
    )

    stored = result.graph_snapshot["nodes"][0]["data"]["api_token"]
    assert stored.startswith("workflow-node-secret://")
    assert "synthetic-raw-value" not in str(result.graph_snapshot)
    assert db.committed is True


def test_inactive_preflight_preview_downgrades_public_blockers_to_warning():
    organization_id = uuid.uuid4()
    kb_id = uuid.uuid4()
    db = _Db(
        {
            KnowledgeBase: [
                _row(
                    id=kb_id,
                    organization_id=organization_id,
                    lifecycle_state="active",
                    source_identity_id=None,
                )
            ]
        }
    )

    result = KnowledgeDeploymentPreflightService(
        db,
        organization_id=organization_id,
    ).preview(
        deployment_type=DeploymentType.CHATBOT,
        graph_snapshot=_llm_graph(kb_id),
        is_active=False,
    )

    assert result.status == "warning"
    assert result.nodes[0].status == "warning"
    assert result.warnings == ["private_kb_requires_execution_subject"]
    assert result.required_actions[0].action == (
        "remove_private_kb_or_use_authenticated_run"
    )


def test_preflight_allows_public_collection_kb_for_public_surface():
    organization_id = uuid.uuid4()
    kb_id = uuid.uuid4()
    collection_id = uuid.uuid4()
    db = _Db(
        {
            KnowledgeBase: [
                _row(
                    id=kb_id,
                    organization_id=organization_id,
                    lifecycle_state="active",
                    source_identity_id=None,
                )
            ],
            KnowledgeCollectionItem: [
                _row(
                    organization_id=organization_id,
                    collection_id=collection_id,
                    knowledge_base_id=kb_id,
                )
            ],
            KnowledgeCollection: [
                _row(
                    id=collection_id,
                    organization_id=organization_id,
                    lifecycle_state="active",
                    source_identity_id=None,
                    safe_metadata={"visibility": "public"},
                )
            ],
        }
    )

    result = KnowledgeDeploymentPreflightService(
        db,
        organization_id=organization_id,
    ).preview(
        deployment_type=DeploymentType.API,
        graph_snapshot=_llm_graph(kb_id),
    )

    assert result.status == "passed"
    assert result.nodes == []


def test_preflight_blocks_source_managed_kb_even_if_collection_is_public():
    organization_id = uuid.uuid4()
    kb_id = uuid.uuid4()
    collection_id = uuid.uuid4()
    db = _Db(
        {
            KnowledgeBase: [
                _row(
                    id=kb_id,
                    organization_id=organization_id,
                    lifecycle_state="active",
                    source_identity_id=uuid.uuid4(),
                )
            ],
            KnowledgeCollectionItem: [
                _row(
                    organization_id=organization_id,
                    collection_id=collection_id,
                    knowledge_base_id=kb_id,
                )
            ],
            KnowledgeCollection: [
                _row(
                    id=collection_id,
                    organization_id=organization_id,
                    lifecycle_state="active",
                    source_identity_id=None,
                    safe_metadata={"visibility": "public"},
                )
            ],
        }
    )

    result = KnowledgeDeploymentPreflightService(
        db,
        organization_id=organization_id,
    ).preview(
        deployment_type=DeploymentType.WEBHOOK,
        graph_snapshot=_llm_graph(kb_id),
    )

    assert result.status == "blocked"
    assert result.safe_summary.blocked_reason == "source_public_exposure_required"


@pytest.mark.parametrize("visibility", [None, "private"])
def test_preflight_blocks_private_selected_collection(visibility):
    organization_id = uuid.uuid4()
    collection_id = uuid.uuid4()
    safe_metadata = {} if visibility is None else {"visibility": visibility}
    db = _Db(
        {
            KnowledgeCollection: [
                _row(
                    id=collection_id,
                    organization_id=organization_id,
                    lifecycle_state="active",
                    source_identity_id=None,
                    safe_metadata=safe_metadata,
                )
            ],
        }
    )

    result = KnowledgeDeploymentPreflightService(
        db,
        organization_id=organization_id,
    ).preview(
        deployment_type=DeploymentType.API,
        graph_snapshot=_collection_graph(collection_id),
    )

    assert result.status == "blocked"
    assert result.safe_summary.blocked_reason == (
        "private_collection_requires_execution_subject"
    )
    assert result.safe_summary.affected_collection_count_bucket == "1"


def test_preflight_allows_public_manual_selected_collection():
    organization_id = uuid.uuid4()
    collection_id = uuid.uuid4()
    kb_id = uuid.uuid4()
    db = _Db(
        {
            KnowledgeCollection: [
                _row(
                    id=collection_id,
                    organization_id=organization_id,
                    lifecycle_state="active",
                    source_identity_id=None,
                    safe_metadata={"visibility": "public"},
                )
            ],
            KnowledgeCollectionItem: [
                _row(
                    organization_id=organization_id,
                    collection_id=collection_id,
                    knowledge_base_id=kb_id,
                )
            ],
            KnowledgeBase: [
                _row(
                    id=kb_id,
                    organization_id=organization_id,
                    lifecycle_state="active",
                    source_identity_id=None,
                )
            ],
        }
    )

    result = KnowledgeDeploymentPreflightService(
        db,
        organization_id=organization_id,
    ).preview(
        deployment_type=DeploymentType.CHATBOT,
        graph_snapshot=_collection_graph(collection_id),
    )

    assert result.status == "passed"
    assert result.nodes == []


def test_preflight_blocks_public_collection_with_source_managed_member():
    organization_id = uuid.uuid4()
    collection_id = uuid.uuid4()
    kb_id = uuid.uuid4()
    db = _Db(
        {
            KnowledgeCollection: [
                _row(
                    id=collection_id,
                    organization_id=organization_id,
                    lifecycle_state="active",
                    source_identity_id=None,
                    safe_metadata={"visibility": "public"},
                )
            ],
            KnowledgeCollectionItem: [
                _row(
                    organization_id=organization_id,
                    collection_id=collection_id,
                    knowledge_base_id=kb_id,
                )
            ],
            KnowledgeBase: [
                _row(
                    id=kb_id,
                    organization_id=organization_id,
                    lifecycle_state="active",
                    source_identity_id=uuid.uuid4(),
                )
            ],
        }
    )

    result = KnowledgeDeploymentPreflightService(
        db,
        organization_id=organization_id,
    ).preview(
        deployment_type=DeploymentType.WEBHOOK,
        graph_snapshot=_collection_graph(collection_id),
    )

    assert result.status == "blocked"
    assert result.safe_summary.blocked_reason == "source_public_exposure_required"
    assert str(collection_id) not in result.model_dump_json()
    assert str(kb_id) not in result.model_dump_json()


def test_collection_preflight_aggregate_excludes_cross_org_member_facts():
    organization_id = uuid.uuid4()
    collection_id = uuid.uuid4()
    kb_id = uuid.uuid4()
    db = _Db(
        {
            KnowledgeCollection: [
                _row(
                    id=collection_id,
                    organization_id=organization_id,
                    lifecycle_state="active",
                    source_identity_id=None,
                    safe_metadata={"visibility": "public"},
                )
            ],
            KnowledgeCollectionItem: [
                _row(
                    organization_id=organization_id,
                    collection_id=collection_id,
                    knowledge_base_id=kb_id,
                )
            ],
            KnowledgeBase: [
                _row(
                    id=kb_id,
                    organization_id=uuid.uuid4(),
                    lifecycle_state="active",
                    source_identity_id=uuid.uuid4(),
                )
            ],
        }
    )

    result = KnowledgeDeploymentPreflightService(
        db,
        organization_id=organization_id,
    ).preview(
        deployment_type=DeploymentType.API,
        graph_snapshot=_collection_graph(collection_id),
    )

    assert result.status == "passed"


def test_collection_preflight_returns_bucketed_candidate_budget_warning():
    organization_id = uuid.uuid4()
    collection_id = uuid.uuid4()
    kb_ids = [uuid.uuid4() for _index in range(21)]
    db = _Db(
        {
            KnowledgeCollection: [
                _row(
                    id=collection_id,
                    organization_id=organization_id,
                    lifecycle_state="active",
                    source_identity_id=None,
                    safe_metadata={"visibility": "public"},
                )
            ],
            KnowledgeCollectionItem: [
                _row(
                    organization_id=organization_id,
                    collection_id=collection_id,
                    knowledge_base_id=kb_id,
                )
                for kb_id in kb_ids
            ],
            KnowledgeBase: [
                _row(
                    id=kb_id,
                    organization_id=organization_id,
                    lifecycle_state="active",
                    source_identity_id=None,
                )
                for kb_id in kb_ids
            ],
        }
    )

    result = KnowledgeDeploymentPreflightService(
        db,
        organization_id=organization_id,
    ).preview(
        deployment_type=DeploymentType.API,
        graph_snapshot=_collection_graph(collection_id),
    )

    assert result.status == "warning"
    assert result.safe_summary.candidate_budget_limited is True
    assert result.safe_summary.affected_collection_count_bucket == "1"
    assert result.nodes[0].candidate_budget_limited is True
    assert all(str(kb_id) not in result.model_dump_json() for kb_id in kb_ids)


@pytest.mark.parametrize("collection_scope", ["cross_org", "archived"])
def test_preflight_hides_unavailable_selected_collection(collection_scope):
    organization_id = uuid.uuid4()
    collection_id = uuid.uuid4()
    db = _Db(
        {
            KnowledgeCollection: [
                _row(
                    id=collection_id,
                    organization_id=(
                        uuid.uuid4()
                        if collection_scope == "cross_org"
                        else organization_id
                    ),
                    lifecycle_state=(
                        "archived" if collection_scope == "archived" else "active"
                    ),
                    source_identity_id=None,
                    safe_metadata={"visibility": "public"},
                )
            ],
        }
    )

    result = KnowledgeDeploymentPreflightService(
        db,
        organization_id=organization_id,
    ).preview(
        deployment_type=DeploymentType.API,
        graph_snapshot=_collection_graph(collection_id),
    )

    assert result.status == "blocked"
    assert result.safe_summary.blocked_reason == "knowledge_collection_unavailable"
    assert str(collection_id) not in result.model_dump_json()


@pytest.mark.parametrize("kb_scope", ["cross_org", "archived"])
def test_preflight_hides_kb_outside_active_organization_scope(kb_scope):
    organization_id = uuid.uuid4()
    kb_id = uuid.uuid4()
    kb_organization_id = uuid.uuid4() if kb_scope == "cross_org" else organization_id
    lifecycle_state = "archived" if kb_scope == "archived" else "active"
    db = _Db(
        {
            KnowledgeBase: [
                _row(
                    id=kb_id,
                    organization_id=kb_organization_id,
                    lifecycle_state=lifecycle_state,
                    source_identity_id=None,
                )
            ]
        }
    )

    result = KnowledgeDeploymentPreflightService(
        db,
        organization_id=organization_id,
    ).preview(
        deployment_type=DeploymentType.API,
        graph_snapshot=_llm_graph(kb_id),
    )

    assert result.status == "blocked"
    assert result.safe_summary.blocked_reason == "knowledge_base_unavailable"
    assert str(kb_id) not in result.model_dump_json()


def test_workflow_node_preflight_uses_data_app_id_for_target_lookup():
    organization_id = uuid.uuid4()
    target_app_id = uuid.uuid4()
    target_deployment_id = uuid.uuid4()
    kb_id = uuid.uuid4()
    db = _Db(
        {
            App: [
                _row(
                    id=target_app_id,
                    organization_id=organization_id,
                    active_deployment_id=target_deployment_id,
                )
            ],
            WorkflowDeployment: [
                _row(
                    id=target_deployment_id,
                    app_id=target_app_id,
                    is_active=True,
                    type=DeploymentType.WORKFLOW_NODE,
                    graph_snapshot=_llm_graph(kb_id),
                )
            ],
            KnowledgeBase: [
                _row(
                    id=kb_id,
                    organization_id=organization_id,
                    lifecycle_state="active",
                    source_identity_id=None,
                )
            ],
        }
    )
    graph = {
        "nodes": [
            _node("start", "startNode"),
            _node(
                "workflow-1",
                "workflowNode",
                {
                    "appId": str(target_app_id),
                    "workflowId": str(uuid.uuid4()),
                },
            ),
        ],
        "edges": [_edge("start", "workflow-1", "start-workflow")],
    }

    result = KnowledgeDeploymentPreflightService(
        db,
        organization_id=organization_id,
    ).preview(
        deployment_type=DeploymentType.CHATBOT,
        graph_snapshot=graph,
    )

    assert result.status == "blocked"
    assert result.safe_summary.blocked_reason == "private_kb_requires_execution_subject"


def test_workflow_node_preflight_rejects_non_workflow_node_active_deployment():
    organization_id = uuid.uuid4()
    target_app_id = uuid.uuid4()
    target_deployment_id = uuid.uuid4()
    db = _Db(
        {
            App: [
                _row(
                    id=target_app_id,
                    organization_id=organization_id,
                    active_deployment_id=target_deployment_id,
                )
            ],
            WorkflowDeployment: [
                _row(
                    id=target_deployment_id,
                    app_id=target_app_id,
                    is_active=True,
                    type=DeploymentType.API,
                    graph_snapshot={"nodes": [], "edges": []},
                )
            ],
        }
    )

    result = KnowledgeDeploymentPreflightService(
        db,
        organization_id=organization_id,
    ).preview(
        deployment_type=DeploymentType.CHATBOT,
        graph_snapshot=_workflow_node_graph(target_app_id),
    )

    assert result.status == "blocked"
    assert result.safe_summary.blocked_reason == "workflow_node_target_unavailable"


@pytest.mark.parametrize("target_violation", ["wrong_owner", "inactive"])
def test_workflow_node_preflight_rejects_unowned_or_inactive_target_deployment(
    target_violation,
):
    organization_id = uuid.uuid4()
    target_app_id = uuid.uuid4()
    target_deployment_id = uuid.uuid4()
    db = _Db(
        {
            App: [
                _row(
                    id=target_app_id,
                    organization_id=organization_id,
                    active_deployment_id=target_deployment_id,
                )
            ],
            WorkflowDeployment: [
                _row(
                    id=target_deployment_id,
                    app_id=(
                        uuid.uuid4()
                        if target_violation == "wrong_owner"
                        else target_app_id
                    ),
                    is_active=target_violation != "inactive",
                    type=DeploymentType.WORKFLOW_NODE,
                    graph_snapshot={"nodes": [], "edges": []},
                )
            ],
        }
    )

    result = KnowledgeDeploymentPreflightService(
        db,
        organization_id=organization_id,
    ).preview(
        deployment_type=DeploymentType.CHATBOT,
        graph_snapshot=_workflow_node_graph(target_app_id),
    )

    assert result.status == "blocked"
    assert result.safe_summary.blocked_reason == "workflow_node_target_unavailable"


def test_workflow_node_preflight_hides_cross_organization_target_app():
    organization_id = uuid.uuid4()
    target_app_id = uuid.uuid4()
    db = _Db(
        {
            App: [
                _row(
                    id=target_app_id,
                    organization_id=uuid.uuid4(),
                    active_deployment_id=uuid.uuid4(),
                )
            ]
        }
    )

    result = KnowledgeDeploymentPreflightService(
        db,
        organization_id=organization_id,
    ).preview(
        deployment_type=DeploymentType.CHATBOT,
        graph_snapshot=_workflow_node_graph(target_app_id),
    )

    assert result.status == "blocked"
    assert result.safe_summary.blocked_reason == "workflow_node_target_unavailable"


def test_workflow_node_preflight_rejects_non_workflow_node_candidate_target():
    organization_id = uuid.uuid4()
    app_a_id = uuid.uuid4()
    app_b_id = uuid.uuid4()
    deployment_b_id = uuid.uuid4()
    db = _Db(
        {
            App: [
                _row(
                    id=app_a_id,
                    organization_id=organization_id,
                    active_deployment_id=uuid.uuid4(),
                ),
                _row(
                    id=app_b_id,
                    organization_id=organization_id,
                    active_deployment_id=deployment_b_id,
                ),
            ],
            WorkflowDeployment: [
                _row(
                    id=deployment_b_id,
                    app_id=app_b_id,
                    is_active=True,
                    type=DeploymentType.WORKFLOW_NODE,
                    graph_snapshot=_workflow_node_graph(app_a_id),
                )
            ],
        }
    )
    candidate_a_graph = _workflow_node_graph(app_b_id)

    result = KnowledgeDeploymentPreflightService(
        db,
        organization_id=organization_id,
        candidate_graphs_by_app_id={app_a_id: candidate_a_graph},
        candidate_deployment_types_by_app_id={app_a_id: DeploymentType.CHATBOT},
    ).preview(
        deployment_type=DeploymentType.CHATBOT,
        graph_snapshot=candidate_a_graph,
    )

    assert result.status == "blocked"
    assert result.safe_summary.blocked_reason == "workflow_node_target_unavailable"


def test_workflow_node_preflight_uses_candidate_graph_for_pending_workflow_node():
    organization_id = uuid.uuid4()
    app_a_id = uuid.uuid4()
    app_b_id = uuid.uuid4()
    deployment_b_id = uuid.uuid4()
    db = _Db(
        {
            App: [
                _row(
                    id=app_a_id,
                    organization_id=organization_id,
                    active_deployment_id=uuid.uuid4(),
                ),
                _row(
                    id=app_b_id,
                    organization_id=organization_id,
                    active_deployment_id=deployment_b_id,
                ),
            ],
            WorkflowDeployment: [
                _row(
                    id=deployment_b_id,
                    app_id=app_b_id,
                    is_active=True,
                    type=DeploymentType.WORKFLOW_NODE,
                    graph_snapshot=_workflow_node_graph(app_a_id),
                )
            ],
        }
    )
    candidate_a_graph = _workflow_node_graph(app_b_id)

    result = KnowledgeDeploymentPreflightService(
        db,
        organization_id=organization_id,
        candidate_graphs_by_app_id={app_a_id: candidate_a_graph},
        candidate_deployment_types_by_app_id={app_a_id: DeploymentType.WORKFLOW_NODE},
    ).preview(
        deployment_type=DeploymentType.WORKFLOW_NODE,
        graph_snapshot=candidate_a_graph,
    )

    assert result.status == "blocked"
    assert result.safe_summary.blocked_reason == "workflow_node_cycle_detected"


def test_workflow_node_deployment_blocks_unavailable_target_even_with_inherited_subject():
    organization_id = uuid.uuid4()
    missing_app_id = uuid.uuid4()

    result = KnowledgeDeploymentPreflightService(
        _Db(),
        organization_id=organization_id,
    ).preview(
        deployment_type=DeploymentType.WORKFLOW_NODE,
        graph_snapshot=_workflow_node_graph(missing_app_id),
    )

    assert result.status == "blocked"
    assert result.safe_summary.blocked_reason == "workflow_node_target_unavailable"
    assert result.warnings == []


def test_inactive_preview_keeps_workflow_node_structural_blockers_blocked():
    organization_id = uuid.uuid4()
    missing_app_id = uuid.uuid4()

    result = KnowledgeDeploymentPreflightService(
        _Db(),
        organization_id=organization_id,
    ).preview(
        deployment_type=DeploymentType.WORKFLOW_NODE,
        graph_snapshot=_workflow_node_graph(missing_app_id),
        is_active=False,
    )

    assert result.status == "blocked"
    assert result.safe_summary.blocked_reason == "workflow_node_target_unavailable"
    assert result.warnings == []


def test_workflow_node_deployment_warns_for_inherited_subject_instead_of_blocking():
    organization_id = uuid.uuid4()
    kb_id = uuid.uuid4()
    db = _Db(
        {
            KnowledgeBase: [
                _row(
                    id=kb_id,
                    organization_id=organization_id,
                    lifecycle_state="active",
                    source_identity_id=None,
                )
            ]
        }
    )

    result = KnowledgeDeploymentPreflightService(
        db,
        organization_id=organization_id,
    ).preview(
        deployment_type=DeploymentType.WORKFLOW_NODE,
        graph_snapshot=_llm_graph(kb_id),
    )

    assert result.status == "warning"
    assert result.warnings == ["workflow_node_execution_subject_inherited"]


def test_enforced_preflight_raises_409_error_envelope():
    organization_id = uuid.uuid4()
    kb_id = uuid.uuid4()
    db = _Db(
        {
            KnowledgeBase: [
                _row(
                    id=kb_id,
                    organization_id=organization_id,
                    lifecycle_state="active",
                    source_identity_id=None,
                )
            ]
        }
    )
    service = KnowledgeDeploymentPreflightService(db, organization_id=organization_id)

    with pytest.raises(HTTPException) as exc_info:
        service.enforce_active_publish(
            deployment_type=DeploymentType.CHATBOT,
            graph_snapshot=_llm_graph(kb_id),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"]["code"] == "deployment.preflight.blocked"


def test_enforced_collection_preflight_keeps_safe_409_envelope():
    organization_id = uuid.uuid4()
    collection_id = uuid.uuid4()
    db = _Db(
        {
            KnowledgeCollection: [
                _row(
                    id=collection_id,
                    organization_id=organization_id,
                    lifecycle_state="active",
                    source_identity_id=None,
                    safe_metadata={},
                )
            ]
        }
    )

    with pytest.raises(HTTPException) as exc_info:
        KnowledgeDeploymentPreflightService(
            db,
            organization_id=organization_id,
        ).enforce_active_publish(
            deployment_type=DeploymentType.CHATBOT,
            graph_snapshot=_collection_graph(collection_id),
        )

    assert exc_info.value.status_code == 409
    detail = exc_info.value.detail["error"]
    assert detail["code"] == "deployment.preflight.blocked"
    assert detail["reason_code"] == ("private_collection_requires_execution_subject")
    assert str(collection_id) not in str(detail)


def test_authenticated_configuration_preflight_uses_workflow_409_envelope():
    service = KnowledgeDeploymentPreflightService(
        _Db({}),
        organization_id=uuid.uuid4(),
        principal_id=uuid.uuid4(),
    )

    with pytest.raises(HTTPException) as exc_info:
        service.enforce_authenticated_run(
            graph_snapshot={
                "nodes": [
                    _node("start", "startNode"),
                    _node(
                        "mail-1",
                        "mailNode",
                        {
                            "title": "Mail",
                            "credential_id": None,
                            "configuration_state": "unresolved",
                        },
                    ),
                ],
                "edges": [_edge("start", "mail-1", "start-mail")],
            }
        )

    assert exc_info.value.status_code == 409
    error = exc_info.value.detail["error"]
    assert error["code"] == "workflow.configuration_preflight.blocked"
    assert error["reason_code"] == "node_configuration_unresolved"
    assert error["required_actions"] == ["complete_node_configuration"]


def test_preflight_audience_classifies_every_deployment_type():
    assert {
        deployment_type: KnowledgeDeploymentPreflightService.server_derived_audience(
            deployment_type
        )
        for deployment_type in DeploymentType
    } == {
        DeploymentType.API: "anonymous_public",
        DeploymentType.WEBAPP: "anonymous_public",
        DeploymentType.WIDGET: "anonymous_public",
        DeploymentType.CHATBOT: "anonymous_public",
        DeploymentType.INTERNAL_CHATBOT: "authenticated_user",
        DeploymentType.MCP: "anonymous_public",
        DeploymentType.WORKFLOW_NODE: "workflow_node_inherited",
        DeploymentType.SCHEDULE: "anonymous_public",
        DeploymentType.WEBHOOK: "anonymous_public",
    }


def test_preflight_audience_hint_cannot_relax_public_surface():
    organization_id = uuid.uuid4()
    kb_id = uuid.uuid4()
    db = _Db(
        {
            KnowledgeBase: [
                _row(
                    id=kb_id,
                    organization_id=organization_id,
                    lifecycle_state="active",
                    source_identity_id=None,
                )
            ]
        }
    )

    result = KnowledgeDeploymentPreflightService(
        db,
        organization_id=organization_id,
    ).preview(
        deployment_type=DeploymentType.CHATBOT,
        graph_snapshot=_llm_graph(kb_id),
        audience_hint="authenticated_user",
    )

    assert result.audience == "anonymous_public"
    assert result.status == "blocked"


def test_create_rejects_explicit_snapshot_after_primary_change():
    app_id = uuid.uuid4()
    old_workflow_id = uuid.uuid4()
    current_workflow_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    app = App(
        id=app_id,
        workflow_id=current_workflow_id,
        organization_id=uuid.uuid4(),
        created_by=actor_id,
    )
    db = _Db({App: [app], WorkflowDeployment: []})

    with pytest.raises(HTTPException) as exc_info:
        DeploymentService.create_deployment(
            db,
            DeploymentCreate(
                app_id=app_id,
                graph_snapshot={"nodes": [], "edges": []},
            ),
            user_id=actor_id,
            observed_workflow_id=old_workflow_id,
            runtime_policy=DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == {
        "code": "deployment.graph_snapshot_stale",
        "message": (
            "The App primary Workflow changed. Refresh the deployment snapshot "
            "and try again."
        ),
    }
    assert db.rows_for(WorkflowDeployment) == []


def test_create_preserves_preflight_http_exception(monkeypatch):
    app_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    app = App(
        id=app_id,
        workflow_id=workflow_id,
        organization_id=uuid.uuid4(),
        url_slug="app-slug",
        auth_secret=None,
        created_by=uuid.uuid4(),
    )
    workflow = _row(
        id=workflow_id,
        organization_id=app.organization_id,
        app_id=app.id,
        created_by=app.created_by,
    )
    db = _Db({App: [app], Workflow: [workflow]})
    expected = HTTPException(
        status_code=409,
        detail={"error": {"code": "deployment.preflight.blocked"}},
    )

    monkeypatch.setattr(
        deployment_module, "has_workflow_permission", lambda *a, **k: True
    )
    monkeypatch.setattr(
        DeploymentService,
        "_enforce_knowledge_preflight",
        lambda *a, **k: (_ for _ in ()).throw(expected),
    )

    with pytest.raises(HTTPException) as exc_info:
        DeploymentService.create_deployment(
            db,
            DeploymentCreate(
                app_id=app_id,
                type=DeploymentType.CHATBOT,
                graph_snapshot={"nodes": [], "edges": []},
                is_active=True,
            ),
            user_id=app.created_by,
            observed_workflow_id=workflow_id,
            runtime_policy=DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"]["code"] == "deployment.preflight.blocked"


@pytest.mark.parametrize("is_active", (True, False))
def test_create_binding_error_prefers_common_preflight_envelope(
    monkeypatch,
    is_active,
):
    app_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    app = App(
        id=app_id,
        workflow_id=workflow_id,
        organization_id=uuid.uuid4(),
        url_slug="app-slug",
        auth_secret=None,
        created_by=actor_id,
    )
    workflow = _row(
        id=workflow_id,
        organization_id=app.organization_id,
        app_id=app.id,
        created_by=actor_id,
    )
    db = _Db({App: [app], Workflow: [workflow]})
    expected = HTTPException(
        status_code=409,
        detail={"error": {"code": "deployment.preflight.blocked"}},
    )
    calls = []

    monkeypatch.setattr(
        deployment_module,
        "has_workflow_permission",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        DeploymentService,
        "bind_workflow_node_targets",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            HTTPException(
                status_code=422,
                detail={"code": "workflow_graph_invalid"},
            )
        ),
    )

    def block(name):
        def _block(*args, **kwargs):
            calls.append(name)
            raise expected

        return _block

    monkeypatch.setattr(
        DeploymentService,
        "_enforce_knowledge_preflight",
        block("active"),
    )
    monkeypatch.setattr(
        DeploymentService,
        "_enforce_inactive_preflight",
        block("inactive"),
    )

    with pytest.raises(HTTPException) as exc_info:
        DeploymentService.create_deployment(
            db,
            DeploymentCreate(
                app_id=app_id,
                type=DeploymentType.API,
                graph_snapshot={
                    "nodes": [_node("start", "startNode")],
                    "edges": [],
                },
                is_active=is_active,
            ),
            user_id=actor_id,
            observed_workflow_id=workflow_id,
            runtime_policy=DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"]["code"] == "deployment.preflight.blocked"
    assert calls == ["active" if is_active else "inactive"]
    assert db.rows_for(WorkflowDeployment) == []


def test_inactive_create_rejects_mail_inline_secret_with_common_preflight_error(
    monkeypatch,
):
    app_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    app = App(
        id=app_id,
        workflow_id=workflow_id,
        organization_id=organization_id,
        url_slug="app-slug",
        auth_secret=None,
        created_by=actor_id,
    )
    workflow = _row(
        id=workflow_id,
        organization_id=organization_id,
        app_id=app_id,
        created_by=actor_id,
    )
    db = _Db({App: [app], Workflow: [workflow]})
    monkeypatch.setattr(
        deployment_module, "has_workflow_permission", lambda *a, **k: True
    )

    with pytest.raises(HTTPException) as exc_info:
        DeploymentService.create_deployment(
            db,
            DeploymentCreate(
                app_id=app_id,
                graph_snapshot={
                    "nodes": [
                        _node("start-1", "startNode"),
                        _node(
                            "mail-1",
                            "mailNode",
                            {
                                "title": "Mail",
                                "credential_id": None,
                                "app_password": "synthetic-only",
                            },
                        ),
                    ],
                    "edges": [_edge("start-1", "mail-1", "start-mail")],
                },
                is_active=False,
            ),
            user_id=actor_id,
            observed_workflow_id=workflow_id,
            runtime_policy=DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
        )

    assert exc_info.value.status_code == 409
    assert (
        exc_info.value.detail["error"]["code"]
        == "workflow.configuration_preflight.blocked"
    )
    assert any(
        "node_configuration_invalid" in node["reason_codes"]
        for node in exc_info.value.detail["error"]["preflight"]["nodes"]
    )
    assert db.rows_for(WorkflowDeployment) == []


def test_inactive_create_preserves_unresolved_mail_snapshot(monkeypatch):
    app_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    app = App(
        id=app_id,
        workflow_id=workflow_id,
        organization_id=organization_id,
        url_slug="app-slug",
        auth_secret=None,
        created_by=actor_id,
    )
    workflow = _row(
        id=workflow_id,
        organization_id=organization_id,
        app_id=app_id,
        created_by=actor_id,
    )
    db = _Db({App: [app], Workflow: [workflow]})
    monkeypatch.setattr(
        deployment_module, "has_workflow_permission", lambda *a, **k: True
    )

    deployment = DeploymentService.create_deployment(
        db,
        DeploymentCreate(
            app_id=app_id,
            graph_snapshot={
                "nodes": [
                    _node("start", "startNode"),
                    _node(
                        "mail-1",
                        "mailNode",
                        {
                            "title": "Mail",
                            "credential_id": None,
                            "configuration_state": "unresolved",
                        },
                    ),
                ],
                "edges": [_edge("start", "mail-1", "start-mail")],
            },
            is_active=False,
        ),
        user_id=actor_id,
        observed_workflow_id=workflow_id,
        runtime_policy=DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
    )

    assert deployment.is_active is False
    assert db.rows_for(WorkflowDeployment) == [deployment]


def test_inactive_create_rejects_unavailable_mail_reference(
    monkeypatch,
):
    app_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    app = App(
        id=app_id,
        workflow_id=workflow_id,
        organization_id=organization_id,
        url_slug="app-slug",
        auth_secret=None,
        created_by=actor_id,
    )
    workflow = _row(
        id=workflow_id,
        organization_id=organization_id,
        app_id=app_id,
        created_by=actor_id,
    )
    db = _Db({App: [app], Workflow: [workflow]})
    monkeypatch.setattr(
        deployment_module, "has_workflow_permission", lambda *a, **k: True
    )

    with pytest.raises(HTTPException) as exc_info:
        DeploymentService.create_deployment(
            db,
            DeploymentCreate(
                app_id=app_id,
                graph_snapshot={
                    "nodes": [
                        _node("start", "startNode"),
                        _node(
                            "mail-1",
                            "mailNode",
                            {
                                "title": "Mail",
                                "credential_id": str(uuid.uuid4()),
                                "configuration_state": "resolved",
                            },
                        ),
                    ],
                    "edges": [_edge("start", "mail-1", "start-mail")],
                },
                is_active=False,
            ),
            user_id=actor_id,
            observed_workflow_id=workflow_id,
            runtime_policy=DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
        )

    assert exc_info.value.status_code == 409
    assert (
        exc_info.value.detail["error"]["reason_code"] == "mail_credential_unavailable"
    )
    assert db.rows_for(WorkflowDeployment) == []


def test_create_preserves_mail_credential_permission_denial(monkeypatch):
    app_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    app = App(
        id=app_id,
        workflow_id=workflow_id,
        organization_id=organization_id,
        url_slug="app-slug",
        auth_secret=None,
        created_by=actor_id,
    )
    workflow = _row(
        id=workflow_id,
        organization_id=organization_id,
        app_id=app_id,
        created_by=actor_id,
    )
    db = _Db({App: [app], Workflow: [workflow]})
    monkeypatch.setattr(
        deployment_module, "has_workflow_permission", lambda *a, **k: True
    )
    monkeypatch.setattr(
        DeploymentService,
        "_enforce_graph_structure_before_binding",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        DeploymentService,
        "bind_workflow_node_targets",
        lambda _db, graph, **_kwargs: graph,
    )
    monkeypatch.setattr(
        deployment_module.WorkflowService,
        "validate_mail_credential_references",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            HTTPException(status_code=403, detail="mail.credential_permission_denied")
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        DeploymentService.create_deployment(
            db,
            DeploymentCreate(
                app_id=app_id,
                graph_snapshot={"nodes": [], "edges": []},
                is_active=True,
            ),
            user_id=actor_id,
            observed_workflow_id=workflow_id,
            runtime_policy=DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "mail.credential_permission_denied"


def test_inactive_create_rejects_malformed_graph(monkeypatch):
    app_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    app = App(
        id=app_id,
        workflow_id=workflow_id,
        organization_id=organization_id,
        url_slug="app-slug",
        auth_secret=None,
        created_by=actor_id,
    )
    workflow = _row(
        id=workflow_id,
        organization_id=organization_id,
        app_id=app_id,
        created_by=actor_id,
    )
    db = _Db({App: [app], Workflow: [workflow]})
    monkeypatch.setattr(
        deployment_module, "has_workflow_permission", lambda *a, **k: True
    )

    with pytest.raises(HTTPException) as exc_info:
        DeploymentService.create_deployment(
            db,
            DeploymentCreate(
                app_id=app_id,
                graph_snapshot={"nodes": "invalid", "edges": []},
                is_active=False,
            ),
            user_id=actor_id,
            observed_workflow_id=workflow_id,
            runtime_policy=DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"]["code"] == "deployment.preflight.blocked"
    assert db.rows_for(WorkflowDeployment) == []


def test_inactive_create_does_not_mutate_active_surface(monkeypatch):
    app_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    active_deployment_id = uuid.uuid4()
    app = App(
        id=app_id,
        workflow_id=workflow_id,
        organization_id=uuid.uuid4(),
        active_deployment_id=active_deployment_id,
        url_slug="app-slug",
        auth_secret=None,
        created_by=uuid.uuid4(),
    )
    workflow = _row(
        id=workflow_id,
        organization_id=app.organization_id,
        app_id=app.id,
        created_by=app.created_by,
    )
    db = _Db({App: [app], Workflow: [workflow]}, max_deployment_version=2)

    monkeypatch.setattr(
        deployment_module, "has_workflow_permission", lambda *a, **k: True
    )
    monkeypatch.setattr(
        DeploymentService,
        "_enforce_knowledge_preflight",
        lambda *a, **k: pytest.fail("inactive create must not enforce preflight"),
    )

    deployment = DeploymentService.create_deployment(
        db,
        DeploymentCreate(
            app_id=app_id,
            type=DeploymentType.CHATBOT,
            graph_snapshot={
                "nodes": [
                    _node(
                        "schedule-1",
                        "scheduleTrigger",
                        {"cron_expression": "* * * * *"},
                    )
                ],
                "edges": [],
            },
            is_active=False,
        ),
        user_id=app.created_by,
        observed_workflow_id=workflow_id,
        runtime_policy=DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
    )

    assert deployment.is_active is False
    assert deployment.version == 3
    assert deployment.browser_access_policy == {
        "contract_version": "deployment_browser_access.v1",
        "embedding": {"enabled": False, "parent_origins": []},
    }
    assert app.active_deployment_id == active_deployment_id
    assert not db.rows_for(Schedule)


def test_active_schedule_create_rolls_back_on_invalid_schedule_configuration(
    monkeypatch,
):
    app_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    app = App(
        id=app_id,
        workflow_id=workflow_id,
        organization_id=uuid.uuid4(),
        active_deployment_id=None,
        url_slug="schedule-slug",
        auth_secret=None,
        created_by=uuid.uuid4(),
    )
    workflow = _row(
        id=workflow_id,
        organization_id=app.organization_id,
        app_id=app.id,
        created_by=app.created_by,
    )
    db = _Db({App: [app], Workflow: [workflow], Schedule: []})

    monkeypatch.setattr(
        deployment_module, "has_workflow_permission", lambda *a, **k: True
    )
    monkeypatch.setattr(
        DeploymentService,
        "_enforce_knowledge_preflight",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "apps.gateway.services.scheduler_service.get_scheduler_service",
        lambda: _RejectingScheduler(),
    )

    with pytest.raises(HTTPException) as exc_info:
        DeploymentService.create_deployment(
            db,
            DeploymentCreate(
                app_id=app_id,
                type=DeploymentType.SCHEDULE,
                graph_snapshot={
                    "nodes": [
                        _node(
                            "schedule-1",
                            "scheduleTrigger",
                            {"cron_expression": "invalid"},
                        )
                    ],
                    "edges": [],
                },
                is_active=True,
            ),
            user_id=app.created_by,
            observed_workflow_id=workflow_id,
            runtime_policy=DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["code"] == "deployment.schedule_configuration_invalid"
    assert db.rolled_back is True


def test_active_schedule_create_hides_unexpected_scheduler_error(monkeypatch):
    app_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    app = App(
        id=app_id,
        workflow_id=workflow_id,
        organization_id=uuid.uuid4(),
        active_deployment_id=None,
        url_slug="schedule-error-slug",
        auth_secret=None,
        created_by=uuid.uuid4(),
    )
    workflow = _row(
        id=workflow_id,
        organization_id=app.organization_id,
        app_id=app.id,
        created_by=app.created_by,
    )
    db = _Db({App: [app], Workflow: [workflow], Schedule: []})

    monkeypatch.setattr(
        deployment_module, "has_workflow_permission", lambda *a, **k: True
    )
    monkeypatch.setattr(
        DeploymentService,
        "_enforce_knowledge_preflight",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "apps.gateway.services.scheduler_service.get_scheduler_service",
        lambda: _ExplodingScheduler(),
    )

    with pytest.raises(HTTPException) as exc_info:
        DeploymentService.create_deployment(
            db,
            DeploymentCreate(
                app_id=app_id,
                type=DeploymentType.SCHEDULE,
                graph_snapshot={
                    "nodes": [
                        _node(
                            "schedule-1",
                            "scheduleTrigger",
                            {"cron_expression": "* * * * *"},
                        )
                    ],
                    "edges": [],
                },
                is_active=True,
            ),
            user_id=app.created_by,
            observed_workflow_id=workflow_id,
            runtime_policy=DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == {
        "code": "deployment.creation_failed",
        "message": "Deployment could not be created.",
    }
    assert "internal database endpoint" not in str(exc_info.value.detail)
    assert db.rolled_back is True


def test_workflow_node_create_does_not_create_schedule_surface(monkeypatch):
    app_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    app = App(
        id=app_id,
        workflow_id=workflow_id,
        organization_id=uuid.uuid4(),
        active_deployment_id=None,
        url_slug="module-slug",
        auth_secret=None,
        created_by=uuid.uuid4(),
    )
    workflow = _row(
        id=workflow_id,
        organization_id=app.organization_id,
        app_id=app.id,
        created_by=app.created_by,
    )
    db = _Db({App: [app], Workflow: [workflow], Schedule: []})
    scheduler = _Scheduler()

    monkeypatch.setattr(
        deployment_module, "has_workflow_permission", lambda *a, **k: True
    )
    monkeypatch.setattr(
        DeploymentService,
        "_enforce_knowledge_preflight",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "apps.gateway.services.scheduler_service.get_scheduler_service",
        lambda: scheduler,
    )

    deployment = DeploymentService.create_deployment(
        db,
        DeploymentCreate(
            app_id=app_id,
            type=DeploymentType.WORKFLOW_NODE,
            graph_snapshot={
                "nodes": [
                    _node(
                        "schedule-1",
                        "scheduleTrigger",
                        {"cron_expression": "* * * * *"},
                    )
                ],
                "edges": [],
            },
            is_active=True,
        ),
        user_id=app.created_by,
        observed_workflow_id=workflow_id,
        runtime_policy=DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
    )

    assert deployment.is_active is True
    assert app.active_deployment_id == deployment.id
    assert db.rows_for(Schedule) == []
    assert scheduler.added == []


def test_active_redeployment_creates_fresh_model_routing_policy_without_bootstrap(
    monkeypatch,
):
    app_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    previous_deployment_id = uuid.uuid4()
    app = App(
        id=app_id,
        workflow_id=workflow_id,
        organization_id=uuid.uuid4(),
        active_deployment_id=previous_deployment_id,
        url_slug="routing-slug",
        auth_secret="existing-auth-secret",
        created_by=uuid.uuid4(),
    )
    workflow = _row(
        id=workflow_id,
        organization_id=app.organization_id,
        app_id=app.id,
        created_by=app.created_by,
    )
    previous_deployment = _row(
        id=previous_deployment_id,
        app_id=app_id,
        is_active=True,
        version=1,
    )
    graph_snapshot = {
        "nodes": [
            _node("trigger", "webhookTrigger"),
            _node(
                "llm-triage",
                "llmNode",
                    {
                        "auto_model_routing": True,
                        "model_id": "test-model",
                        "user_prompt": "{{result}}",
                    },
            ),
        ],
        "edges": [_edge("trigger", "llm-triage", "trigger-llm")],
    }
    db = _Db(
        {
            App: [app],
            Workflow: [workflow],
            WorkflowDeployment: [previous_deployment],
            Schedule: [],
        },
        max_deployment_version=1,
    )
    bootstrapped = []
    published = []
    monkeypatch.setattr(
        deployment_module, "has_workflow_permission", lambda *a, **k: True
    )
    monkeypatch.setattr(
        DeploymentService,
        "_enforce_knowledge_preflight",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "apps.gateway.services.scheduler_service.get_scheduler_service",
        lambda: _Scheduler(),
    )
    monkeypatch.setattr(
        deployment_module.ModelRoutingPolicyStore,
        "ensure_policies_for_deployment",
        lambda _db, **kwargs: (
            bootstrapped.append(kwargs) or [SimpleNamespace(id=uuid.uuid4())]
        ),
    )
    monkeypatch.setattr(
        deployment_module,
        "send_workflow_task",
        lambda _app, name, args: published.append((name, args)),
    )

    deployment = DeploymentService.create_deployment(
        db,
        DeploymentCreate(
            app_id=app_id,
            type=DeploymentType.WEBHOOK,
            graph_snapshot=graph_snapshot,
            is_active=True,
        ),
        user_id=app.created_by,
        observed_workflow_id=workflow_id,
        runtime_policy=DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
    )

    assert bootstrapped == [
        {
            "workflow_id": workflow_id,
            "deployment_id": deployment.id,
            "organization_id": workflow.organization_id,
            "execution_subject_user_id": app.created_by,
            "graph_snapshot": deployment.graph_snapshot,
        }
    ]
    assert published == []


def test_workflow_node_toggle_removes_legacy_schedule_surface(monkeypatch):
    app_id = uuid.uuid4()
    deployment_id = uuid.uuid4()
    schedule_id = uuid.uuid4()
    app = _row(id=app_id, active_deployment_id=None)
    deployment = _row(
        id=deployment_id,
        app_id=app_id,
        type=DeploymentType.WORKFLOW_NODE,
        is_active=False,
        graph_snapshot={
            "nodes": [
                _node(
                    "schedule-1",
                    "scheduleTrigger",
                    {"cron_expression": "* * * * *"},
                )
            ],
            "edges": [],
        },
    )
    schedule = _row(
        id=schedule_id,
        deployment_id=deployment_id,
        cron_expression="* * * * *",
        timezone="UTC",
    )
    db = _Db({App: [app], WorkflowDeployment: [deployment], Schedule: [schedule]})
    scheduler = _Scheduler()
    lock_calls = []
    original_lock = deployment_module.lock_app_for_lifecycle

    def tracked_app_lock(db_arg, app_id_arg, **kwargs):
        lock_calls.append(app_id_arg)
        return original_lock(db_arg, app_id_arg, **kwargs)

    monkeypatch.setattr(
        deployment_module,
        "lock_app_for_lifecycle",
        tracked_app_lock,
    )

    monkeypatch.setattr(
        DeploymentService,
        "_enforce_knowledge_preflight",
        lambda *a, **k: None,
    )

    DeploymentService.toggle_deployment(
        db,
        deployment_id,
        scheduler,
        runtime_policy=DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
    )

    assert deployment.is_active is True
    assert app.active_deployment_id == deployment_id
    assert lock_calls == [app_id]
    assert db.rows_for(Schedule) == []
    assert scheduler.removed == [schedule_id]
    assert scheduler.added == []


def test_toggle_blocks_reactivation_when_deployment_workflow_is_ambiguous(monkeypatch):
    app_id = uuid.uuid4()
    deployment_id = uuid.uuid4()
    app = _row(
        id=app_id,
        organization_id=uuid.uuid4(),
        active_deployment_id=None,
    )
    deployment = _row(
        id=deployment_id,
        app_id=app_id,
        type=DeploymentType.API,
        is_active=False,
        graph_snapshot={"nodes": [], "edges": []},
    )
    db = _Db(
        {App: [app], WorkflowDeployment: [deployment], Schedule: []},
        max_deployment_version=2,
    )
    monkeypatch.setattr(
        DeploymentService,
        "_enforce_knowledge_preflight",
        lambda *args, **kwargs: pytest.fail("provenance guard must run first"),
    )

    with pytest.raises(HTTPException) as exc_info:
        DeploymentService.toggle_deployment(
            db,
            deployment_id,
            _Scheduler(),
            runtime_policy=DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
            user_id=uuid.uuid4(),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == (
        "deployment.reactivation_provenance_unavailable"
    )
    assert deployment.is_active is False
    assert app.active_deployment_id is None
    assert db.committed is False


def test_strict_toggle_blocks_legacy_public_chatbot_without_mutating_state(
    monkeypatch,
):
    app_id = uuid.uuid4()
    deployment_id = uuid.uuid4()
    app = _row(
        id=app_id,
        organization_id=uuid.uuid4(),
        active_deployment_id=None,
    )
    deployment = _row(
        id=deployment_id,
        app_id=app_id,
        type=DeploymentType.CHATBOT,
        is_active=False,
        config=None,
        graph_snapshot={
            "nodes": [
                _node("start", "startNode"),
                _node("answer", "llmNode"),
            ],
            "edges": [_edge("start", "answer", "start-answer")],
        },
    )
    db = _Db({App: [app], WorkflowDeployment: [deployment], Schedule: []})
    scheduler = _Scheduler()
    monkeypatch.setattr(
        DeploymentService,
        "_enforce_knowledge_preflight",
        lambda *_args, **_kwargs: pytest.fail(
            "strict conversation validation must run before knowledge preflight"
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        DeploymentService.toggle_deployment(
            db,
            deployment_id,
            scheduler,
            runtime_policy=DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
            require_public_chat_conversation_contract=True,
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["code"] == "conversation.consumer_mapping_required"
    assert deployment.is_active is False
    assert app.active_deployment_id is None
    assert scheduler.added == []
    assert scheduler.removed == []
    assert db.committed is False


def test_strict_toggle_allows_valid_public_chatbot_consumer_mapping(monkeypatch):
    app_id = uuid.uuid4()
    deployment_id = uuid.uuid4()
    app = _row(
        id=app_id,
        organization_id=uuid.uuid4(),
        active_deployment_id=None,
    )
    graph_snapshot = {
        "nodes": [
            _node("start", "startNode"),
            _node(
                "loop",
                "loopNode",
                {
                    "subGraph": {
                        "nodes": [_node("answer", "llmNode")],
                        "edges": [],
                    }
                },
            ),
        ],
        "edges": [_edge("start", "loop", "start-loop")],
    }
    deployment = _row(
        id=deployment_id,
        app_id=app_id,
        type=DeploymentType.CHATBOT,
        is_active=False,
        config={
            "public_conversation": {
                "contract_version": "public_chat_conversation.v1",
                "history_consumer": {
                    "node_id": "answer",
                    "container_path": [
                        {"kind": "loop", "node_id": "loop"}
                    ],
                },
            }
        },
        graph_snapshot=graph_snapshot,
    )
    db = _Db({App: [app], WorkflowDeployment: [deployment], Schedule: []})
    monkeypatch.setattr(
        DeploymentService,
        "_enforce_knowledge_preflight",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        deployment_module,
        "enforce_workflow_configuration_preflight",
        lambda *_args, **_kwargs: None,
    )

    DeploymentService.toggle_deployment(
        db,
        deployment_id,
        _Scheduler(),
        runtime_policy=DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
        require_public_chat_conversation_contract=True,
    )

    assert deployment.is_active is True
    assert app.active_deployment_id == deployment_id
    assert db.committed is True


def test_compatibility_toggle_allows_legacy_public_chatbot(monkeypatch):
    app_id = uuid.uuid4()
    deployment_id = uuid.uuid4()
    app = _row(
        id=app_id,
        organization_id=uuid.uuid4(),
        active_deployment_id=None,
    )
    deployment = _row(
        id=deployment_id,
        app_id=app_id,
        type=DeploymentType.CHATBOT,
        is_active=False,
        config=None,
        graph_snapshot={
            "nodes": [
                _node("start", "startNode"),
                _node("answer", "llmNode"),
            ],
            "edges": [_edge("start", "answer", "start-answer")],
        },
    )
    db = _Db({App: [app], WorkflowDeployment: [deployment], Schedule: []})
    monkeypatch.setattr(
        DeploymentService,
        "_enforce_knowledge_preflight",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        deployment_module,
        "enforce_workflow_configuration_preflight",
        lambda *_args, **_kwargs: None,
    )

    DeploymentService.toggle_deployment(
        db,
        deployment_id,
        _Scheduler(),
        runtime_policy=DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
        require_public_chat_conversation_contract=False,
    )

    assert deployment.is_active is True
    assert app.active_deployment_id == deployment_id
    assert db.committed is True


def test_toggle_rejects_legacy_mail_inline_secret_with_common_preflight_error():
    app_id = uuid.uuid4()
    deployment_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    app = _row(
        id=app_id,
        organization_id=organization_id,
        active_deployment_id=None,
    )
    deployment = _row(
        id=deployment_id,
        app_id=app_id,
        type=DeploymentType.API,
        is_active=False,
        graph_snapshot={
            "nodes": [
                _node("start", "startNode"),
                _node(
                    "mail-1",
                    "mailNode",
                    {
                        "title": "Mail",
                        "credential_id": None,
                        "password": "synthetic-only",
                    },
                ),
            ],
            "edges": [_edge("start", "mail-1", "start-mail")],
        },
    )
    db = _Db({App: [app], WorkflowDeployment: [deployment], Schedule: []})

    with pytest.raises(HTTPException) as exc_info:
        DeploymentService.toggle_deployment(
            db,
            deployment_id,
            _Scheduler(),
            runtime_policy=DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
            user_id=actor_id,
        )

    assert exc_info.value.status_code == 409
    assert (
        exc_info.value.detail["error"]["code"]
        == "workflow.configuration_preflight.blocked"
    )
    assert any(
        "node_configuration_invalid" in node["reason_codes"]
        for node in exc_info.value.detail["error"]["preflight"]["nodes"]
    )
    assert deployment.is_active is False


def test_toggle_rejects_unresolved_mail_with_common_preflight(monkeypatch):
    app_id = uuid.uuid4()
    deployment_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    app = _row(
        id=app_id,
        organization_id=organization_id,
        active_deployment_id=None,
    )
    deployment = _row(
        id=deployment_id,
        app_id=app_id,
        type=DeploymentType.API,
        is_active=False,
        graph_snapshot={
            "nodes": [
                _node("start", "startNode"),
                _node(
                    "mail-1",
                    "mailNode",
                    {
                        "title": "Mail",
                        "credential_id": None,
                        "configuration_state": "unresolved",
                    },
                ),
            ],
            "edges": [_edge("start", "mail-1", "start-mail")],
        },
    )
    db = _Db({App: [app], WorkflowDeployment: [deployment], Schedule: []})

    def _block_preflight(*args, **kwargs):
        raise HTTPException(
            status_code=409,
            detail={
                "error": {
                    "code": "deployment.preflight.blocked",
                    "reason_code": "mail_execution_subject_required",
                }
            },
        )

    monkeypatch.setattr(
        DeploymentService,
        "_enforce_knowledge_preflight",
        _block_preflight,
    )

    with pytest.raises(HTTPException) as exc_info:
        DeploymentService.toggle_deployment(
            db,
            deployment_id,
            _Scheduler(),
            runtime_policy=DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
            user_id=actor_id,
        )

    assert exc_info.value.status_code == 409
    assert (
        exc_info.value.detail["error"]["code"]
        == "workflow.configuration_preflight.blocked"
    )
    assert deployment.is_active is False


def test_delete_active_deployment_does_not_auto_promote_other_deployment():
    app_id = uuid.uuid4()
    deployment_id = uuid.uuid4()
    other_deployment_id = uuid.uuid4()
    app = _row(id=app_id, active_deployment_id=deployment_id)
    deployment = _row(id=deployment_id, app_id=app_id)
    other_deployment = _row(
        id=other_deployment_id,
        app_id=app_id,
        is_active=True,
        version=2,
    )
    db = _Db(
        {
            App: [app],
            WorkflowDeployment: [deployment, other_deployment],
            Schedule: [],
        }
    )

    DeploymentService.delete_deployment(db, str(deployment_id))

    assert app.active_deployment_id is None
    assert deployment not in db.rows_for(WorkflowDeployment)
    assert other_deployment in db.rows_for(WorkflowDeployment)


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


def _collection_graph(collection_id: uuid.UUID) -> dict:
    return {
        "nodes": [
            _node("start", "startNode"),
            _node(
                "llm-collection",
                "llmNode",
                {"knowledgeCollections": [{"id": str(collection_id)}]},
            ),
        ],
        "edges": [_edge("start", "llm-collection", "start-collection")],
    }


def _workflow_node_graph(app_id: uuid.UUID) -> dict:
    return {
        "nodes": [
            _node("start", "startNode"),
            _node("workflow-1", "workflowNode", {"appId": str(app_id)}),
        ],
        "edges": [_edge("start", "workflow-1", "start-workflow")],
    }


def _row(**kwargs):
    return SimpleNamespace(**kwargs)


class _Db:
    def __init__(self, rows_by_model=None, *, max_deployment_version=0):
        self.rows_by_model = {
            model: list(rows) for model, rows in (rows_by_model or {}).items()
        }
        self.max_deployment_version = max_deployment_version
        self.committed = False
        self.rolled_back = False

    def query(self, model, *rest):
        if (
            getattr(model, "class_", None) is KnowledgeCollectionItem
            and getattr(model, "key", None) == "collection_id"
            and rest
        ):
            return _CollectionAggregateQuery(
                self.rows_by_model.setdefault(KnowledgeCollectionItem, []),
                self.rows_by_model.setdefault(KnowledgeBase, []),
            )
        if model in {
            App,
            Workflow,
            WorkflowDeployment,
            Schedule,
            KnowledgeBase,
            KnowledgeCollection,
            KnowledgeCollectionItem,
            MailCredential,
            WorkflowNodeSecret,
        }:
            return _Query(self.rows_by_model.setdefault(model, []))
        return _ScalarQuery(self.max_deployment_version)

    def get(self, model, object_id):
        return next(
            (
                row
                for row in self.rows_by_model.get(model, [])
                if getattr(row, "id", None) == object_id
            ),
            None,
        )

    def add(self, obj):
        self.rows_by_model.setdefault(type(obj), []).append(obj)

    def delete(self, obj):
        for rows in self.rows_by_model.values():
            if obj in rows:
                rows.remove(obj)
                return

    def flush(self):
        for deployment in self.rows_by_model.get(WorkflowDeployment, []):
            if getattr(deployment, "id", None) is None:
                deployment.id = uuid.uuid4()

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def refresh(self, obj):
        pass

    def rows_for(self, model):
        return self.rows_by_model.setdefault(model, [])


class _ScalarQuery:
    def __init__(self, value):
        self.value = value

    def filter(self, *expressions):
        return self

    def scalar(self):
        return self.value


class _Scheduler:
    def __init__(self):
        self.added = []
        self.removed = []

    def add_schedule(self, schedule, db):
        self.added.append(schedule)

    def remove_schedule(self, schedule_id):
        self.removed.append(schedule_id)


class _RejectingScheduler(_Scheduler):
    def add_schedule(self, schedule, db):
        from apps.gateway.application.deployment.schedule_errors import (
            ScheduleConfigurationError,
        )

        raise ScheduleConfigurationError("safe configuration failure")


class _ExplodingScheduler(_Scheduler):
    def add_schedule(self, schedule, db):
        raise RuntimeError("internal database endpoint must not leak")


class _Query:
    def __init__(self, rows):
        self.rows = rows
        self.expressions = []

    def filter(self, *expressions):
        self.expressions.extend(expressions)
        return self

    def join(self, *args, **kwargs):
        return self

    def with_for_update(self, *args, **kwargs):
        return self

    def populate_existing(self):
        return self

    def all(self):
        return [row for row in self.rows if self._matches(row)]

    def first(self):
        return next((row for row in self.rows if self._matches(row)), None)

    def order_by(self, *args, **kwargs):
        return self

    def offset(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def _matches(self, row):
        return all(
            _matches_expression(row, expression) for expression in self.expressions
        )


class _CollectionAggregateQuery:
    def __init__(self, items, knowledge_bases):
        self.items = items
        self.knowledge_bases = knowledge_bases
        self.expressions = []

    def join(self, *args, **kwargs):
        return self

    def filter(self, *expressions):
        self.expressions.extend(expressions)
        return self

    def group_by(self, *args, **kwargs):
        return self

    def all(self):
        knowledge_bases_by_id = {
            knowledge_base.id: knowledge_base for knowledge_base in self.knowledge_bases
        }
        grouped: dict[uuid.UUID, list[SimpleNamespace]] = {}
        for item in self.items:
            knowledge_base = knowledge_bases_by_id.get(item.knowledge_base_id)
            if knowledge_base is None:
                continue
            if not all(
                _matches_expression(item, expression)
                and _matches_expression(knowledge_base, expression)
                for expression in self.expressions
            ):
                continue
            grouped.setdefault(item.collection_id, []).append(knowledge_base)
        return [
            (
                collection_id,
                len(knowledge_bases),
                sum(
                    getattr(knowledge_base, "source_identity_id", None) is not None
                    for knowledge_base in knowledge_bases
                ),
            )
            for collection_id, knowledge_bases in grouped.items()
        ]


def _matches_expression(row, expression):
    if not hasattr(expression, "left"):
        return True

    column_name = str(expression.left).split(".")[-1]
    if not hasattr(row, column_name):
        return True

    row_value = getattr(row, column_name)
    right_value = _right_value(expression.right)
    if expression.operator is eq:
        return row_value == right_value or str(row_value) == str(right_value)
    if expression.operator is ne:
        return row_value != right_value and str(row_value) != str(right_value)
    if expression.operator is in_op:
        return row_value in set(right_value or [])
    if expression.operator is is_:
        return row_value is right_value or bool(row_value) is bool(right_value)
    return True


def _right_value(right):
    if hasattr(right, "value"):
        return right.value
    if str(right).lower() == "null":
        return None
    if str(right).lower() == "true":
        return True
    if str(right).lower() == "false":
        return False
    return right
