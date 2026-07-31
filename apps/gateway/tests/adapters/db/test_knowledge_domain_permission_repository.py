import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from apps.gateway.adapters.db.knowledge_domain_permissions import (
    SqlAlchemyKnowledgeDomainPermissionRepository,
)
from apps.gateway.application.knowledge_administration.domain_permissions import (
    DomainPermissionCommand,
)


class _ScalarResult:
    def __init__(self, row):
        self.row = row

    def scalar_one_or_none(self):
        return self.row


class _StatementDb:
    def __init__(self, row):
        self.row = row
        self.statements = []
        self.deleted = []

    def execute(self, statement):
        self.statements.append(statement)
        return _ScalarResult(self.row)

    def delete(self, row):
        self.deleted.append(row)


def _command(*, subject_type="team", organization_id=None):
    return DomainPermissionCommand(
        actor_id=uuid.uuid4(),
        organization_id=organization_id or uuid.uuid4(),
        subject_type=subject_type,
        subject_id=uuid.uuid4(),
        permission_action="catalog_manage",
    )


def _compiled(statement):
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = " ".join(str(compiled).split())
    return sql, compiled.params


@pytest.mark.parametrize(
    ("subject_type", "table_name", "subject_column"),
    [
        ("team", "team_knowledge_domain_permissions", "team_id"),
        ("user", "user_knowledge_domain_permissions", "user_id"),
    ],
)
def test_revoke_statement_scopes_and_locks_exact_permission_row(
    subject_type,
    table_name,
    subject_column,
):
    command = _command(subject_type=subject_type)

    statement = (
        SqlAlchemyKnowledgeDomainPermissionRepository._revoke_statement(command)
    )
    sql, params = _compiled(statement)

    assert f"FROM {table_name}" in sql
    assert f"{table_name}.organization_id" in sql
    assert f"{table_name}.{subject_column}" in sql
    assert f"{table_name}.permission_action" in sql
    assert sql.endswith("FOR UPDATE")
    assert len(params) == 3
    assert set(params.values()) == {
        command.organization_id,
        command.subject_id,
        command.permission_action,
    }


def test_revoke_executes_compiled_lock_statement_before_delete():
    permission = SimpleNamespace(id=uuid.uuid4())
    db = _StatementDb(permission)
    command = _command(subject_type="team")

    permission_id = SqlAlchemyKnowledgeDomainPermissionRepository(db).revoke(command)

    assert permission_id == permission.id
    assert len(db.statements) == 1
    sql, params = _compiled(db.statements[0])
    assert "team_knowledge_domain_permissions.organization_id" in sql
    assert sql.endswith("FOR UPDATE")
    assert command.organization_id in params.values()
    assert db.deleted == [permission]


def test_revoke_absent_permission_uses_lock_statement_without_delete():
    db = _StatementDb(None)
    command = _command(subject_type="user")

    permission_id = SqlAlchemyKnowledgeDomainPermissionRepository(db).revoke(command)

    assert permission_id is None
    assert len(db.statements) == 1
    sql, _params = _compiled(db.statements[0])
    assert "user_knowledge_domain_permissions.organization_id" in sql
    assert sql.endswith("FOR UPDATE")
    assert db.deleted == []


def test_revoke_statement_binds_each_organization_independently():
    organization_a = uuid.uuid4()
    organization_b = uuid.uuid4()
    command_a = _command(organization_id=organization_a)
    command_b = DomainPermissionCommand(
        actor_id=command_a.actor_id,
        organization_id=organization_b,
        subject_type=command_a.subject_type,
        subject_id=command_a.subject_id,
        permission_action=command_a.permission_action,
    )

    _sql_a, params_a = _compiled(
        SqlAlchemyKnowledgeDomainPermissionRepository._revoke_statement(command_a)
    )
    _sql_b, params_b = _compiled(
        SqlAlchemyKnowledgeDomainPermissionRepository._revoke_statement(command_b)
    )

    assert organization_a in params_a.values()
    assert organization_b not in params_a.values()
    assert organization_b in params_b.values()
    assert organization_a not in params_b.values()
