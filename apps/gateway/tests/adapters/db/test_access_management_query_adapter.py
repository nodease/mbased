import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import literal_column

from apps.gateway.adapters.db.access_management_query_adapter import (
    SqlAlchemyAccessManagementQueryAdapter,
)


NOW = datetime.now(timezone.utc)


class _Query:
    def __init__(self, *, rows=(), count=None) -> None:
        self.rows = list(rows)
        self.count_value = len(self.rows) if count is None else count
        self.order_expressions = ()
        self.filter_expressions = []

    def join(self, *args):
        return self

    def filter(self, *args):
        self.filter_expressions.extend(args)
        return self

    def order_by(self, *args):
        self.order_expressions = args
        return self

    def offset(self, value):
        return self

    def limit(self, value):
        return self

    def distinct(self):
        return self

    def union(self, other):
        return self

    def subquery(self):
        return SimpleNamespace(
            c=SimpleNamespace(resource_id=literal_column("resource_id"))
        )

    def count(self):
        return self.count_value

    def all(self):
        return self.rows


class _Session:
    def __init__(self, queries):
        self.queries = list(queries)
        self.query_calls = []

    def query(self, *entities):
        self.query_calls.append(entities)
        if not self.queries:
            raise AssertionError(f"unexpected query: {entities}")
        return self.queries.pop(0)


def test_resource_pagination_batches_direct_and_team_sources_without_n_plus_one():
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    resource_id = uuid.uuid4()
    team_id = uuid.uuid4()
    team_membership_id = uuid.uuid4()
    direct_id = uuid.uuid4()
    direct_row = SimpleNamespace(
        id=direct_id,
        workflow_id=resource_id,
        auth_state="viewer",
        assigned_at=NOW,
    )
    team_permission = SimpleNamespace(
        workflow_id=resource_id,
        team_id=team_id,
        auth_state="builder",
    )
    membership = SimpleNamespace(id=team_membership_id)
    team = SimpleNamespace(id=team_id, name="Builders")
    session = _Session(
        [
            _Query(),
            _Query(),
            _Query(
                count=1,
                rows=[
                    SimpleNamespace(
                        resource_id=resource_id,
                        resource_name="Workflow",
                    )
                ],
            ),
            _Query(rows=[direct_row]),
            _Query(rows=[(team_permission, membership, team)]),
        ]
    )

    result = SqlAlchemyAccessManagementQueryAdapter(session).list_resource_access(
        organization_id,
        user_id,
        resource_type="workflow",
        resource_id=None,
        source="all",
        page=1,
        limit=20,
    )

    assert result.total == 1
    assert len(result.items) == 1
    item = result.items[0]
    assert item.direct_permission.permission_id == direct_id
    assert item.team_sources[0].team_membership_id == team_membership_id
    assert item.team_sources[0].auth_state == "builder"
    assert len(session.query_calls) == 5
    assert session.queries == []


def test_team_page_uses_grouped_resource_queries_for_selected_teams():
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    active_team_id = uuid.uuid4()
    inactive_team_id = uuid.uuid4()
    active_membership = SimpleNamespace(id=uuid.uuid4(), assigned_at=NOW)
    inactive_membership = SimpleNamespace(id=uuid.uuid4(), assigned_at=NOW)
    active_team = SimpleNamespace(
        id=active_team_id,
        name="Active",
        is_active=True,
    )
    inactive_team = SimpleNamespace(
        id=inactive_team_id,
        name="Inactive",
        is_active=False,
    )
    mail_query = _Query(rows=[])
    session = _Session(
        [
            _Query(
                count=2,
                rows=[
                    (active_membership, active_team),
                    (inactive_membership, inactive_team),
                ],
            ),
            _Query(
                rows=[SimpleNamespace(team_id=active_team_id, workflow_id=uuid.uuid4())]
            ),
            _Query(
                rows=[
                    SimpleNamespace(
                        team_id=active_team_id,
                        knowledge_base_id=uuid.uuid4(),
                    )
                ]
            ),
            _Query(rows=[]),
            mail_query,
        ]
    )

    result = SqlAlchemyAccessManagementQueryAdapter(session).list_team_memberships(
        organization_id,
        user_id,
        team_id=None,
        page=1,
        limit=20,
    )

    assert result.total == 2
    assert result.items[0].inherited_resource_counts.total == 2
    assert result.items[1].inherited_resource_counts.total == 0
    assert len(session.query_calls) == 5
    assert "mail_credentials.status" in " ".join(
        str(item) for item in mail_query.filter_expressions
    )


def test_permission_counts_exclude_revoked_mail_credentials():
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    direct_mail_query = _Query(count=0)
    team_mail_query = _Query(rows=[])
    session = _Session(
        [
            _Query(count=0),
            _Query(count=0),
            _Query(count=0),
            direct_mail_query,
            _Query(rows=[]),
            _Query(rows=[]),
            _Query(rows=[]),
            team_mail_query,
        ]
    )
    adapter = SqlAlchemyAccessManagementQueryAdapter(session)

    assert adapter.count_direct_permissions(organization_id, user_id) == 0
    assert adapter.count_team_permission_sources(organization_id, user_id) == 0

    direct_filters = " ".join(
        str(item) for item in direct_mail_query.filter_expressions
    )
    team_filters = " ".join(str(item) for item in team_mail_query.filter_expressions)
    assert "mail_credentials.status" in direct_filters
    assert "mail_credentials.status" in team_filters


def test_mail_resource_page_uses_mail_table_and_excludes_revoked_credentials():
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    credential_id = uuid.uuid4()
    resource_query = _Query(
        count=1,
        rows=[
            SimpleNamespace(
                resource_id=credential_id,
                resource_name="Operations Mail",
            )
        ],
    )
    session = _Session(
        [
            _Query(),
            _Query(),
            resource_query,
            _Query(rows=[]),
            _Query(rows=[]),
        ]
    )

    result = SqlAlchemyAccessManagementQueryAdapter(session).list_resource_access(
        organization_id,
        user_id,
        resource_type="mail_credential",
        resource_id=None,
        source="all",
        page=1,
        limit=20,
    )

    resource_entities = " ".join(str(item) for item in session.query_calls[2])
    resource_filters = " ".join(str(item) for item in resource_query.filter_expressions)
    assert "mail_credentials" in resource_entities
    assert "llm_credentials" not in resource_entities
    assert "mail_credentials.status" in resource_filters
    assert result.items[0].resource_name == "Operations Mail"
