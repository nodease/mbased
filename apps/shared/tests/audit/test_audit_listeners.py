import pytest
from apps.shared.audit import listeners
from apps.shared.audit.context import clear_current_metadata, set_current_metadata
from apps.shared.db.models.app import App
from apps.shared.db.models.connection import Connection
from apps.shared.db.models.knowledge import KnowledgeBase
from apps.shared.db.models.organization import Organization
from apps.shared.db.models.schedule import Schedule
from apps.shared.db.models.team import (
    Team,
    TeamAuditPermission,
    TeamKnowledgePermission,
    TeamLLMPermission,
    TeamMembership,
    TeamWorkflowPermission,
    UserKnowledgePermission,
    UserLLMPermission,
    UserWorkflowPermission,
)
from apps.shared.db.models.workflow import Workflow
from apps.shared.db.models.workflow_deployment import WorkflowDeployment
from apps.shared.db.models.workflow_run import (
    TraceRedactionPolicy,
    TraceRetentionPolicy,
    TraceVisibilityPolicy,
)
from sqlalchemy import Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class AuditThing(Base):
    __tablename__ = "audit_things"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String)


def test_connection_audit_masks_endpoint_and_identity_fields():
    assert listeners.SENSITIVE_FIELDS[Connection] >= {
        "host",
        "database",
        "username",
        "ssh_host",
        "ssh_username",
        "encrypted_password",
        "encrypted_ssh_password",
        "encrypted_ssh_private_key",
    }


def test_app_audit_masks_raw_and_verifier_secret_fields():
    assert listeners.SENSITIVE_FIELDS[App] >= {
        "auth_secret",
        "auth_secret_verifier",
        "auth_secret_previous_verifier",
    }
    for field in listeners.SENSITIVE_FIELDS[App]:
        assert listeners._mask(App, field, "must-not-leak") == "***changed***"


def test_layer_b_tracks_security_and_deployment_models():
    expected = {
        Workflow: "workflow",
        WorkflowDeployment: "workflow_deployment",
        Schedule: "schedule",
        Organization: "organization",
        Team: "team",
        TeamMembership: "team_membership",
        TeamWorkflowPermission: "team_workflow_permission",
        TeamKnowledgePermission: "team_knowledge_permission",
        TeamLLMPermission: "team_llm_permission",
        TeamAuditPermission: "team_audit_permission",
        UserWorkflowPermission: "user_workflow_permission",
        UserKnowledgePermission: "user_knowledge_permission",
        UserLLMPermission: "user_llm_permission",
        TraceRedactionPolicy: "trace_redaction_policy",
        TraceRetentionPolicy: "trace_retention_policy",
        TraceVisibilityPolicy: "trace_visibility_policy",
    }
    assert listeners.TRACKED_MODELS.items() >= expected.items()
    assert listeners.TRACKED_OPS[Workflow] == {"created", "deleted"}


def test_layer_b_masks_sensitive_json_columns():
    assert listeners.SENSITIVE_FIELDS[Workflow] >= {
        "graph",
        "env_variables",
        "runtime_variables",
    }
    assert listeners.SENSITIVE_FIELDS[WorkflowDeployment] >= {
        "graph_snapshot",
        "config",
        "browser_access_policy",
    }
    assert listeners.SENSITIVE_FIELDS[TraceRedactionPolicy] >= {"regex_rules"}
    assert listeners.SENSITIVE_FIELDS[KnowledgeBase] >= {"safe_metadata"}


def test_schedule_operational_cursors_are_excluded_from_generic_update_audit():
    assert listeners.IGNORED_UPDATE_FIELDS[Schedule] == {
        "last_run_at",
        "next_run_at",
    }


@pytest.fixture
def session_factory(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    monkeypatch.setattr(listeners, "TRACKED_MODELS", {AuditThing: "thing"})
    monkeypatch.setattr(listeners, "SENSITIVE_FIELDS", {AuditThing: set()})
    listeners.register_audit_listeners()

    return sessionmaker(bind=engine)


def test_data_change_audit_emits_only_after_real_commit(
    monkeypatch, session_factory
):
    calls = []
    monkeypatch.setattr(listeners, "record_audit", lambda **event: calls.append(event))

    session = session_factory()
    thing = AuditThing(name="draft")
    session.add(thing)
    session.flush()

    assert calls == []

    session.commit()

    assert calls == [
        {
            "action": "thing.created",
            "category": "data_change",
            "actor_id": None,
            "actor_type": "system",
            "target_type": "thing",
            "target_id": 1,
            "before": None,
            "after": {"id": 1, "name": "draft"},
            "metadata": {},
        }
    ]


def test_data_change_audit_discards_real_rollback(monkeypatch, session_factory):
    calls = []
    monkeypatch.setattr(listeners, "record_audit", lambda **event: calls.append(event))

    session = session_factory()
    session.add(AuditThing(name="draft"))
    session.flush()
    session.rollback()

    assert calls == []


def test_data_change_audit_includes_request_metadata(monkeypatch, session_factory):
    calls = []
    monkeypatch.setattr(listeners, "record_audit", lambda **event: calls.append(event))

    token = set_current_metadata(
        {"ip": "127.0.0.1", "user_agent": "test-agent", "request_id": "req-test"}
    )
    try:
        session = session_factory()
        session.add(AuditThing(name="draft"))
        session.commit()
    finally:
        clear_current_metadata(token)

    assert calls == [
        {
            "action": "thing.created",
            "category": "data_change",
            "actor_id": None,
            "actor_type": "system",
            "target_type": "thing",
            "target_id": 1,
            "before": None,
            "after": {"id": 1, "name": "draft"},
            "metadata": {
                "ip": "127.0.0.1",
                "user_agent": "test-agent",
                "request_id": "req-test",
            },
        }
    ]


def test_data_change_audit_coalesces_multiple_flushes_before_commit(
    monkeypatch, session_factory
):
    calls = []
    monkeypatch.setattr(listeners, "record_audit", lambda **event: calls.append(event))

    session = session_factory()
    thing = AuditThing(name="draft")
    session.add(thing)
    session.flush()

    thing.name = "final"
    session.flush()
    session.commit()

    assert calls == [
        {
            "action": "thing.created",
            "category": "data_change",
            "actor_id": None,
            "actor_type": "system",
            "target_type": "thing",
            "target_id": 1,
            "before": None,
            "after": {"id": 1, "name": "final"},
            "metadata": {},
        }
    ]


def test_data_change_audit_skips_nested_transaction(monkeypatch, session_factory):
    calls = []
    monkeypatch.setattr(listeners, "record_audit", lambda **event: calls.append(event))

    session = session_factory()
    thing = AuditThing(name="outer")
    session.add(thing)
    session.flush()

    nested = session.begin_nested()
    thing.name = "inner"
    session.flush()
    nested.rollback()

    session.commit()

    assert calls == []
