from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy.dialects import postgresql

from apps.gateway.adapters.db.deployment_browser_access_repository import (
    SqlAlchemyDeploymentBrowserAccessRepository,
)
from apps.gateway.application.deployment.browser_access_models import (
    BrowserAccessPolicy,
    BrowserAccessSourceSnapshot,
    BrowserEmbeddingPolicy,
)
from apps.shared.db.models.app import App
from apps.shared.db.models.workflow import Workflow
from apps.shared.db.models.workflow_deployment import DeploymentType, WorkflowDeployment


class _Query:
    def __init__(self, result=None, *, scalar=None, rows=None):
        self.result = result
        self.scalar_value = scalar
        self.rows = rows or []
        self.criteria = []
        self.joins = []
        self.locked = False

    def join(self, *args):
        self.joins.append(args)
        return self

    def filter(self, *criteria):
        self.criteria.extend(criteria)
        return self

    def with_for_update(self, *args, **kwargs):
        self.locked = True
        return self

    def first(self):
        return self.result

    def scalar(self):
        return self.scalar_value

    def all(self):
        return self.rows


class _Db:
    def __init__(self, *queries):
        self.pending_queries = list(queries)
        self.queries = []
        self.added = []
        self.info = {}

    def query(self, *args):
        query = self.pending_queries.pop(0)
        query.model_args = args
        self.queries.append(query)
        return query

    def add(self, value):
        self.added.append(value)

    def flush(self):
        for value in self.added:
            if isinstance(value, WorkflowDeployment):
                value.id = value.id or uuid.uuid4()
                value.created_at = value.created_at or datetime.now(timezone.utc)


def _compiled_criteria(query: _Query) -> str:
    return " ".join(
        str(
            criterion.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
        for criterion in query.criteria
    ).lower()


def _source() -> BrowserAccessSourceSnapshot:
    return BrowserAccessSourceSnapshot(
        id=uuid.uuid4(),
        app_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        version=3,
        deployment_type="chatbot",
        graph_snapshot={"nodes": [], "edges": []},
        config={"timeout": 10},
        input_schema={"variables": []},
        output_schema={"outputs": []},
        description="source",
        url_slug="public-chatbot",
    )


def test_public_projection_query_requires_exact_active_pointer_owner_state_and_type() -> None:
    deployment = SimpleNamespace(
        version=5,
        type=DeploymentType.CHATBOT,
        browser_access_policy=None,
    )
    db = _Db(_Query(deployment))

    result = SqlAlchemyDeploymentBrowserAccessRepository(db).get_active_by_slug(
        "public-chatbot"
    )

    assert result is not None
    assert result.deployment_version == 5
    assert result.deployment_type == "chatbot"
    query = db.queries[0]
    assert tuple(column.key for column in query.model_args) == (
        "version",
        "type",
        "browser_access_policy",
    )
    sql = _compiled_criteria(query)
    assert "apps.url_slug = 'public-chatbot'" in sql
    assert "apps.active_deployment_id = workflow_deployments.id" in sql
    assert "workflow_deployments.is_active is true" in sql
    assert "workflow_deployments.type in ('chatbot', 'widget')" in sql
    assert len(query.joins) == 1
    assert query.joins[0][0] is App
    join_sql = str(
        query.joins[0][1].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).lower()
    assert "workflow_deployments.app_id = apps.id" in join_sql


def test_lock_source_locks_app_before_reloading_source() -> None:
    deployment_id = uuid.uuid4()
    app_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    deployment = SimpleNamespace(
        id=deployment_id,
        app_id=app_id,
        version=2,
        type=DeploymentType.WIDGET,
        graph_snapshot={"nodes": [], "edges": []},
        config={},
        input_schema=None,
        output_schema=None,
        description=None,
    )
    app = SimpleNamespace(
        id=app_id,
        workflow_id=workflow_id,
        organization_id=organization_id,
        url_slug="widget",
    )
    workflow = SimpleNamespace(id=workflow_id, organization_id=organization_id)
    db = _Db(
        _Query(deployment),
        _Query(app),
        _Query(deployment),
        _Query(workflow),
    )

    result = SqlAlchemyDeploymentBrowserAccessRepository(db).lock_source(
        deployment_id
    )

    assert result is not None
    assert result.deployment_type == "widget"
    assert db.queries[1].model_args == (App,)
    assert db.queries[1].locked is True
    assert db.queries[2].model_args == (WorkflowDeployment,)
    assert db.queries[2].locked is True
    assert db.queries[3].model_args == (Workflow,)


def test_create_inactive_revision_clones_locked_source_and_marks_manual_audit() -> None:
    source = _source()
    app = SimpleNamespace(
        id=source.app_id,
        active_deployment_id=uuid.uuid4(),
        url_slug=source.url_slug,
    )
    db = _Db(_Query(scalar=8))
    repository = SqlAlchemyDeploymentBrowserAccessRepository(db)
    repository._locked_app = app
    repository._locked_source_id = source.id
    policy = BrowserAccessPolicy(
        contract_version="deployment_browser_access.v1",
        embedding=BrowserEmbeddingPolicy(
            enabled=True,
            parent_origins=("https://portal.example.com",),
        ),
    )
    actor_id = uuid.uuid4()

    result = repository.create_revision(
        source,
        actor_id=actor_id,
        policy=policy,
        is_active=False,
    )

    row = db.added[0]
    assert isinstance(row, WorkflowDeployment)
    assert row.version == 9
    assert row.type == DeploymentType.CHATBOT
    assert row.graph_snapshot == source.graph_snapshot
    assert row.graph_snapshot is not source.graph_snapshot
    assert row.browser_access_policy == policy.to_dict()
    assert row.created_by == actor_id
    assert row.is_active is False
    assert app.active_deployment_id != row.id
    assert result.id == row.id
    ownership = db.info["_manual_audit_ownership"]
    assert (WorkflowDeployment, id(row), "created") in ownership
