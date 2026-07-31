import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.operators import eq

from apps.gateway.services import app_service
from apps.gateway.services.app_service import AppService
from apps.shared.db.models.app import App
from apps.shared.db.models.team import TeamWorkflowPermission, UserWorkflowPermission
from apps.shared.db.models.workflow import Workflow
from apps.shared.db.models.workflow_deployment import WorkflowDeployment


class _FilteringQuery:
    def __init__(self, rows):
        self.rows = list(rows)
        self.expressions = []

    def filter(self, *expressions):
        self.expressions.extend(expressions)
        return self

    def first(self):
        return next(
            (
                row
                for row in self.rows
                if all(self._matches(row, expression) for expression in self.expressions)
            ),
            None,
        )

    def all(self):
        return [
            row
            for row in self.rows
            if all(self._matches(row, expression) for expression in self.expressions)
        ]

    @staticmethod
    def _matches(row, expression):
        left = getattr(expression, "left", None)
        if left is None or expression.operator is not eq:
            return True
        column = getattr(left, "key", None)
        if not column or not hasattr(row, column):
            return False
        right = expression.right
        expected = right.value if hasattr(right, "value") else right
        return getattr(row, column) == expected


class _ModelDb:
    def __init__(self, rows_by_model):
        self.rows_by_model = rows_by_model
        self.added = []
        self.executed = []

    def query(self, model):
        return _FilteringQuery(self.rows_by_model.get(model, []))

    def add(self, row):
        self.added.append(row)

    def execute(self, statement):
        self.executed.append(statement)


def test_app_read_allows_primary_workflow_reader(monkeypatch):
    app = SimpleNamespace(
        organization_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        created_by=uuid.uuid4(),
        is_market=False,
    )
    user_id = uuid.uuid4()

    monkeypatch.setattr(app_service, "has_organization_manager_permission", lambda *a: False)
    monkeypatch.setattr(app_service, "has_workflow_permission", lambda *a, **k: True)

    assert AppService.can_read_app(SimpleNamespace(), app, user_id) is True


def test_primary_workflow_permission_inheritance_preserves_collaborators():
    organization_id = uuid.uuid4()
    other_organization_id = uuid.uuid4()
    source_workflow_id = uuid.uuid4()
    target_workflow = Workflow(
        id=uuid.uuid4(),
        organization_id=organization_id,
        app_id=uuid.uuid4(),
        created_by=uuid.uuid4(),
    )
    actor_id = uuid.uuid4()
    collaborator_id = uuid.uuid4()
    team_id = uuid.uuid4()
    actor_source = UserWorkflowPermission(
        grantee_organization_id=organization_id,
        workflow_id=source_workflow_id,
        user_id=actor_id,
        auth_state="builder",
        assigned_by=actor_id,
        options={"scope": "editor"},
        flags=1,
    )
    collaborator_source = UserWorkflowPermission(
        grantee_organization_id=organization_id,
        workflow_id=source_workflow_id,
        user_id=collaborator_id,
        auth_state="viewer",
        assigned_by=actor_id,
        options={"scope": "read"},
        flags=2,
    )
    foreign_source = UserWorkflowPermission(
        grantee_organization_id=other_organization_id,
        workflow_id=source_workflow_id,
        user_id=uuid.uuid4(),
        auth_state="manager",
        assigned_by=actor_id,
    )
    team_source = TeamWorkflowPermission(
        grantee_organization_id=organization_id,
        workflow_id=source_workflow_id,
        team_id=team_id,
        auth_state="operator",
        assigned_by=actor_id,
        options={"channel": "operations"},
        flags=4,
    )
    foreign_team_source = TeamWorkflowPermission(
        grantee_organization_id=other_organization_id,
        workflow_id=source_workflow_id,
        team_id=uuid.uuid4(),
        auth_state="manager",
        assigned_by=actor_id,
    )
    db = _ModelDb(
        {
            UserWorkflowPermission: [
                actor_source,
                collaborator_source,
                foreign_source,
            ],
            TeamWorkflowPermission: [team_source, foreign_team_source],
        }
    )

    AppService._inherit_primary_workflow_permissions(
        db,
        source_workflow_id=source_workflow_id,
        target_workflow=target_workflow,
        actor_user_id=actor_id,
        organization_id=organization_id,
    )

    inherited_users = {
        row.user_id: row for row in db.added if isinstance(row, UserWorkflowPermission)
    }
    inherited_teams = [
        row for row in db.added if isinstance(row, TeamWorkflowPermission)
    ]
    assert set(inherited_users) == {actor_id, collaborator_id}
    assert inherited_users[actor_id].auth_state == "manager"
    assert inherited_users[actor_id].assigned_by == actor_id
    assert inherited_users[actor_id].options == {"scope": "editor"}
    assert inherited_users[actor_id].flags == 1
    assert inherited_users[collaborator_id].auth_state == "viewer"
    assert inherited_users[collaborator_id].options == {"scope": "read"}
    assert len(inherited_teams) == 1
    assert inherited_teams[0].team_id == team_id
    assert inherited_teams[0].auth_state == "operator"
    assert inherited_teams[0].options == {"channel": "operations"}
    assert all(row.workflow_id == target_workflow.id for row in db.added)
    assert len(db.executed) == 1
    compiled_lock = str(
        db.executed[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "pg_advisory_xact_lock" in compiled_lock
    assert "workflow_permission_scope" in compiled_lock


def test_primary_workflow_permission_inheritance_grants_actor_without_source():
    organization_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    target_workflow = Workflow(
        id=uuid.uuid4(),
        organization_id=organization_id,
        app_id=uuid.uuid4(),
        created_by=actor_id,
    )
    db = _ModelDb({})

    AppService._inherit_primary_workflow_permissions(
        db,
        source_workflow_id=None,
        target_workflow=target_workflow,
        actor_user_id=actor_id,
        organization_id=organization_id,
    )

    assert len(db.added) == 1
    assert isinstance(db.added[0], UserWorkflowPermission)
    assert db.added[0].user_id == actor_id
    assert db.added[0].auth_state == "manager"


def test_primary_workflow_permission_inheritance_requires_flushed_target():
    with pytest.raises(ValueError, match="must be flushed"):
        AppService._inherit_primary_workflow_permissions(
            _ModelDb({}),
            source_workflow_id=uuid.uuid4(),
            target_workflow=Workflow(
                organization_id=uuid.uuid4(),
                app_id=uuid.uuid4(),
                created_by=uuid.uuid4(),
            ),
            actor_user_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
        )


def test_primary_workflow_permission_inheritance_rejects_cross_org_target():
    organization_id = uuid.uuid4()

    with pytest.raises(ValueError, match="organization must match"):
        AppService._inherit_primary_workflow_permissions(
            _ModelDb({}),
            source_workflow_id=uuid.uuid4(),
            target_workflow=Workflow(
                id=uuid.uuid4(),
                organization_id=uuid.uuid4(),
                app_id=uuid.uuid4(),
                created_by=uuid.uuid4(),
            ),
            actor_user_id=uuid.uuid4(),
            organization_id=organization_id,
        )


def test_app_read_denies_non_reader(monkeypatch):
    app = SimpleNamespace(
        organization_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        created_by=uuid.uuid4(),
        is_market=False,
    )
    user_id = uuid.uuid4()

    monkeypatch.setattr(app_service, "has_organization_manager_permission", lambda *a: False)
    monkeypatch.setattr(app_service, "has_workflow_permission", lambda *a, **k: False)

    assert AppService.can_read_app(SimpleNamespace(), app, user_id) is False


def test_app_read_denial_is_403_inside_organization_scope(monkeypatch):
    app = SimpleNamespace(
        organization_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        created_by=uuid.uuid4(),
        is_market=False,
    )
    user_id = uuid.uuid4()

    monkeypatch.setattr(
        app_service, "has_organization_manager_permission", lambda *a: False
    )
    monkeypatch.setattr(app_service, "has_workflow_permission", lambda *a, **k: False)
    monkeypatch.setattr(app_service, "has_organization_scope_access", lambda *a: True)

    assert (
        AppService.access_denial_status(SimpleNamespace(), app, user_id, "read")
        == 403
    )


def test_app_read_denial_is_404_outside_organization_scope(monkeypatch):
    app = SimpleNamespace(
        organization_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        created_by=uuid.uuid4(),
        is_market=False,
    )
    user_id = uuid.uuid4()

    monkeypatch.setattr(
        app_service, "has_organization_manager_permission", lambda *a: False
    )
    monkeypatch.setattr(app_service, "has_workflow_permission", lambda *a, **k: False)
    monkeypatch.setattr(app_service, "has_organization_scope_access", lambda *a: False)

    assert (
        AppService.access_denial_status(SimpleNamespace(), app, user_id, "read")
        == 404
    )


def test_app_manage_denial_is_403_when_readable(monkeypatch):
    app = SimpleNamespace(
        organization_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        created_by=uuid.uuid4(),
        is_market=False,
    )
    user_id = uuid.uuid4()

    monkeypatch.setattr(app_service, "has_organization_manager_permission", lambda *a: False)

    def has_workflow_permission(*args, **kwargs):
        return args[3] == "read"

    monkeypatch.setattr(app_service, "has_workflow_permission", has_workflow_permission)
    monkeypatch.setattr(app_service, "has_organization_scope_access", lambda *a: True)

    assert (
        AppService.access_denial_status(SimpleNamespace(), app, user_id, "manage")
        == 403
    )


def test_app_read_allows_marketplace_app_without_permissions(monkeypatch):
    app = SimpleNamespace(
        organization_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        created_by=uuid.uuid4(),
        is_market=True,
    )
    user_id = uuid.uuid4()

    monkeypatch.setattr(
        app_service, "has_organization_manager_permission", lambda *a: False
    )
    monkeypatch.setattr(app_service, "has_workflow_permission", lambda *a, **k: False)

    assert AppService.can_read_app(SimpleNamespace(), app, user_id) is True


def test_app_operations_read_denies_marketplace_app_without_workflow_read(
    monkeypatch,
):
    app = SimpleNamespace(
        organization_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        created_by=uuid.uuid4(),
        is_market=True,
    )
    user_id = uuid.uuid4()

    monkeypatch.setattr(
        app_service, "has_organization_manager_permission", lambda *a: False
    )
    monkeypatch.setattr(app_service, "has_workflow_permission", lambda *a, **k: False)

    assert AppService.can_read_app(SimpleNamespace(), app, user_id) is True
    assert AppService.can_read_app_operations(SimpleNamespace(), app, user_id) is False


def test_app_operations_read_allows_primary_workflow_builder(monkeypatch):
    app = SimpleNamespace(
        organization_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        created_by=uuid.uuid4(),
        is_market=True,
    )
    user_id = uuid.uuid4()

    monkeypatch.setattr(
        app_service, "has_organization_manager_permission", lambda *a: False
    )
    monkeypatch.setattr(app_service, "has_workflow_permission", lambda *a, **k: True)

    assert AppService.can_read_app_operations(SimpleNamespace(), app, user_id) is True


def test_app_operations_read_allows_organization_manager(monkeypatch):
    app = SimpleNamespace(
        organization_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        created_by=uuid.uuid4(),
        is_market=False,
    )
    user_id = uuid.uuid4()

    monkeypatch.setattr(
        app_service, "has_organization_manager_permission", lambda *a: True
    )
    monkeypatch.setattr(app_service, "has_workflow_permission", lambda *a, **k: False)

    assert AppService.can_read_app_operations(SimpleNamespace(), app, user_id) is True


def test_app_operations_read_denies_execute_only_workflow_user(monkeypatch):
    app = SimpleNamespace(
        organization_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        created_by=uuid.uuid4(),
        is_market=False,
    )
    user_id = uuid.uuid4()

    monkeypatch.setattr(
        app_service, "has_organization_manager_permission", lambda *a: False
    )

    def has_workflow_permission(*args, **kwargs):
        return args[3] in {"read", "execute"}

    monkeypatch.setattr(app_service, "has_workflow_permission", has_workflow_permission)

    assert AppService.can_read_app(SimpleNamespace(), app, user_id) is True
    assert AppService.can_read_app_operations(SimpleNamespace(), app, user_id) is False


def test_deployment_status_ignores_cross_app_active_pointer():
    app = SimpleNamespace(id=uuid.uuid4(), active_deployment_id=uuid.uuid4())
    deployment = SimpleNamespace(
        id=app.active_deployment_id,
        app_id=uuid.uuid4(),
        is_active=True,
    )
    db = _ModelDb({WorkflowDeployment: [deployment]})

    AppService._populate_deployment_status(db, app)

    assert app.active_deployment_is_active is None


def test_deployment_status_accepts_matching_app_active_pointer():
    app = SimpleNamespace(id=uuid.uuid4(), active_deployment_id=uuid.uuid4())
    deployment = SimpleNamespace(
        id=app.active_deployment_id,
        app_id=app.id,
        is_active=True,
    )
    db = _ModelDb({WorkflowDeployment: [deployment]})

    AppService._populate_deployment_status(db, app)

    assert app.active_deployment_is_active is True


def test_clone_app_rejects_cross_app_active_deployment_pointer(monkeypatch):
    source_app = SimpleNamespace(
        id=uuid.uuid4(),
        active_deployment_id=uuid.uuid4(),
    )
    deployment = SimpleNamespace(
        id=source_app.active_deployment_id,
        app_id=uuid.uuid4(),
    )
    db = _ModelDb(
        {
            App: [source_app],
            WorkflowDeployment: [deployment],
        }
    )
    monkeypatch.setattr(AppService, "can_read_app", lambda *args, **kwargs: True)

    with pytest.raises(ValueError, match="Active deployment data not found"):
        AppService.clone_app(
            db,
            source_app.id,
            uuid.uuid4(),
            organization_id=uuid.uuid4(),
        )


def test_clone_graph_cleanup_removes_workflow_node_binding_metadata():
    cleaned = AppService._clean_graph_data(
        {
            "nodes": [],
            "edges": [],
            "_nodease_runtime": {
                "workflow_node_bindings": {
                    "version": "workflow-node-bindings.v1",
                    "entries": [],
                }
            },
        }
    )

    assert "_nodease_runtime" not in cleaned


def test_public_graph_cleanup_removes_kb_and_collection_refs_recursively():
    direct_kb_id = str(uuid.uuid4())
    collection_id = str(uuid.uuid4())
    graph = {
        "nodes": [
            {
                "id": "llm-root",
                "type": "llmNode",
                "data": {
                    "knowledgeBases": [{"id": direct_kb_id, "name": "Private"}],
                    "knowledgeCollections": [
                        {"id": collection_id, "safeLabel": "Private group"}
                    ],
                    "user_prompt": "safe prompt",
                },
            },
            {
                "id": "loop-1",
                "type": "loopNode",
                "data": {
                    "subGraph": {
                        "nodes": [
                            {
                                "id": "llm-child",
                                "type": "llmNode",
                                "data": {
                                    "knowledgeBases": [
                                        {"id": direct_kb_id, "name": "Private"}
                                    ],
                                    "knowledgeCollections": [
                                        {
                                            "id": collection_id,
                                            "safeLabel": "Private group",
                                        }
                                    ],
                                },
                            }
                        ],
                        "edges": [],
                    }
                },
            },
        ],
        "edges": [],
    }

    cleaned = AppService._clean_graph_data(graph)
    serialized = str(cleaned)

    assert direct_kb_id not in serialized
    assert collection_id not in serialized
    assert "Private group" not in serialized
    assert cleaned["nodes"][0]["data"]["user_prompt"] == "safe prompt"
