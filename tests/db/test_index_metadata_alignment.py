from apps.shared.db.models.audit_log import AuditLog
from apps.shared.db.models.cost_optimizer import CostOptimizerExperiment
from apps.shared.db.models.knowledge import DocumentChunk
from apps.shared.db.models.team import (
    TeamKnowledgeCollectionPermission,
    TeamKnowledgeDomainPermission,
    UserKnowledgeCollectionPermission,
    UserKnowledgeDomainPermission,
    UserWorkflowPermission,
)
from apps.shared.db.models.workflow import Workflow
from apps.shared.db.models.workflow_run import (
    TracePayload,
    TraceRedactionPolicy,
    TraceRetentionPolicy,
    TraceVisibilityPolicy,
)


def _index_columns(model) -> dict[str, tuple[str, ...]]:
    return {
        index.name: tuple(column.name for column in index.columns)
        for index in model.__table__.indexes
    }


def test_runtime_query_indexes_are_declared_in_orm_metadata():
    expected = {
        AuditLog: {
            "ix_audit_logs_occurred_at_id": ("occurred_at", "id"),
        },
        DocumentChunk: {
            "ix_document_chunks_kb_doc_version": (
                "knowledge_base_id",
                "document_version_id",
            ),
        },
        TraceRedactionPolicy: {
            "ix_trace_redaction_policies_scope": (
                "scope_type",
                "scope_id",
                "is_active",
            ),
        },
        TraceRetentionPolicy: {
            "ix_trace_retention_policies_scope": (
                "scope_type",
                "scope_id",
                "is_active",
            ),
        },
        TraceVisibilityPolicy: {
            "ix_trace_visibility_policies_scope": (
                "scope_type",
                "scope_id",
                "is_active",
            ),
        },
        Workflow: {
            "ix_workflows_organization_id": ("organization_id",),
        },
    }

    for model, required_indexes in expected.items():
        indexes = _index_columns(model)
        for index_name, columns in required_indexes.items():
            assert indexes[index_name] == columns


def test_collection_permission_indexes_keep_only_nonredundant_fk_paths():
    assert _index_columns(TeamKnowledgeCollectionPermission) == {
        "ix_team_knowledge_collection_permissions_collection": (
            "knowledge_collection_id",
        ),
        "ix_team_knowledge_collection_permissions_team": ("team_id",),
    }
    assert _index_columns(UserKnowledgeCollectionPermission) == {
        "ix_user_knowledge_collection_permissions_collection": (
            "knowledge_collection_id",
        ),
        "ix_user_knowledge_collection_permissions_user": ("user_id",),
    }


def test_unique_prefix_and_explicit_retention_indexes_are_not_duplicated():
    workflow_permission_indexes = _index_columns(UserWorkflowPermission)
    assert (
        "ix_user_workflow_permissions_grantee_organization_id"
        not in workflow_permission_indexes
    )

    cost_optimizer_indexes = _index_columns(CostOptimizerExperiment)
    assert "ix_cost_optimizer_experiments_retention" in cost_optimizer_indexes
    assert (
        "ix_cost_optimizer_experiments_retention_expires_at"
        not in cost_optimizer_indexes
    )

    assert (
        "ix_team_knowledge_domain_permissions_effective"
        not in _index_columns(TeamKnowledgeDomainPermission)
    )
    assert (
        "ix_user_knowledge_domain_permissions_effective"
        not in _index_columns(UserKnowledgeDomainPermission)
    )


def test_trace_raw_retention_index_preserves_partial_predicate():
    indexes = {
        index.name: index
        for index in TracePayload.__table__.indexes
    }
    raw_retention = indexes["ix_trace_payloads_raw_retention"]

    assert tuple(column.name for column in raw_retention.columns) == ("created_at",)
    assert str(raw_retention.dialect_options["postgresql"]["where"]) == (
        "raw_payload_encrypted IS NOT NULL"
    )
