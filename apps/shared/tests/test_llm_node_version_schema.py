from apps.shared.db.models import LLMNodeVersion
from apps.shared.db.models.app import App
from apps.shared.db.models.workflow import Workflow
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB


def test_llm_node_version_model_contract():
    table = LLMNodeVersion.__table__

    assert LLMNodeVersion.__tablename__ == "llm_node_versions"

    assert table.c.app_id.nullable is False
    assert table.c.node_id.nullable is False
    assert table.c.version_number.nullable is False
    assert table.c.parent_version_id.nullable is True
    assert table.c.source_workflow_id.nullable is True
    assert table.c.created_by.nullable is False

    app_fk = next(
        fk for fk in table.c.app_id.foreign_keys if fk.target_fullname == "apps.id"
    )
    source_workflow_fk = next(
        fk
        for fk in table.c.source_workflow_id.foreign_keys
        if fk.target_fullname == "workflows.id"
    )
    created_by_fk = next(
        fk for fk in table.c.created_by.foreign_keys if fk.target_fullname == "users.id"
    )

    assert app_fk.ondelete == "CASCADE"
    assert source_workflow_fk.ondelete == "SET NULL"
    assert created_by_fk.ondelete is None


def test_llm_node_version_jsonb_defaults():
    table = LLMNodeVersion.__table__
    expected_defaults = {
        "referenced_variables": "'[]'::jsonb",
        "parameters": "'{}'::jsonb",
        "knowledge_bases": "'[]'::jsonb",
        "output_config": "'{}'::jsonb",
        "retrieval_config": "'{}'::jsonb",
        "tool_config": "'[]'::jsonb",
    }

    for column_name, server_default in expected_defaults.items():
        column = table.c[column_name]
        assert isinstance(column.type, JSONB)
        assert column.nullable is False
        assert str(column.server_default.arg) == server_default


def test_llm_node_version_constraints_and_indexes():
    table = LLMNodeVersion.__table__

    assert any(
        isinstance(constraint, UniqueConstraint)
        and constraint.name == "uq_llm_node_versions_app_node_version"
        and [column.name for column in constraint.columns]
        == ["app_id", "node_id", "version_number"]
        for constraint in table.constraints
    )
    assert any(
        isinstance(constraint, UniqueConstraint)
        and constraint.name == "uq_llm_node_versions_app_node_id"
        and [column.name for column in constraint.columns]
        == ["app_id", "node_id", "id"]
        for constraint in table.constraints
    )

    check_names = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert check_names >= {
        "ck_llm_node_versions_version_number_positive",
        "ck_llm_node_versions_top_k_positive",
        "ck_llm_node_versions_score_threshold_range",
    }

    indexes = {
        index.name: [column.name for column in index.columns]
        for index in table.indexes
    }
    assert indexes["ix_llm_node_versions_app_id"] == ["app_id"]
    assert indexes["ix_llm_node_versions_parent_version_id"] == ["parent_version_id"]
    assert indexes["ix_llm_node_versions_source_workflow_id"] == ["source_workflow_id"]
    assert indexes["ix_llm_node_versions_created_by"] == ["created_by"]


def test_llm_node_version_parent_constraint_scopes_to_same_app_node():
    table = LLMNodeVersion.__table__
    parent_constraint = next(
        constraint
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
        and constraint.name == "fk_llm_node_versions_parent_same_node"
    )

    assert [column.name for column in parent_constraint.columns] == [
        "app_id",
        "node_id",
        "parent_version_id",
    ]
    assert [element.target_fullname for element in parent_constraint.elements] == [
        "llm_node_versions.app_id",
        "llm_node_versions.node_id",
        "llm_node_versions.id",
    ]
    assert parent_constraint.ondelete is None


def test_app_llm_node_versions_relationship():
    relationship = App.__mapper__.relationships["llm_node_versions"]

    assert relationship.mapper.class_ is LLMNodeVersion
    assert relationship.back_populates == "app"
    assert "delete-orphan" in relationship.cascade


def test_workflow_does_not_own_llm_node_versions():
    assert "llm_node_versions" not in Workflow.__mapper__.relationships
