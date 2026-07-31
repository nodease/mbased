import hashlib
import hmac
import uuid
from types import SimpleNamespace

import pytest
from apps.shared.services.tracing.access import TraceAccessService
from apps.shared.services.tracing.observability import TraceObservabilityService
from apps.shared.services.tracing.policy import ResolvedVisibilityPolicy
from apps.shared.services.tracing.query import TraceQueryService
from apps.shared.services.tracing.rbac import TraceRbacService


def setup_function():
    TraceRbacService.reset_provider()
    TraceObservabilityService.reset_local_counters()


def teardown_function():
    TraceRbacService.reset_provider()
    TraceObservabilityService.reset_local_counters()


def test_run_user_id_does_not_grant_app_owner_access(monkeypatch):
    user_id = uuid.uuid4()
    app_id = uuid.uuid4()
    run = SimpleNamespace(id=uuid.uuid4(), user_id=user_id, app_id=app_id)
    user = SimpleNamespace(id=user_id)

    monkeypatch.setattr(
        TraceAccessService, "resolve_trace_app_id", lambda db, trace: app_id
    )
    monkeypatch.setattr(TraceAccessService, "is_app_owner", lambda db, app, actor: False)
    monkeypatch.setattr(
        "apps.shared.services.tracing.access.TracePolicyService.resolve_visibility_policy",
        lambda db, app_id=None, organization_id=None: ResolvedVisibilityPolicy(),
    )

    decision = TraceAccessService.check_trace_access(None, run, user)

    assert decision.allowed is False
    assert decision.reason_code == "regular_user_trace_access_denied"


def test_app_owner_metadata_access_uses_app_relationship(monkeypatch):
    user_id = uuid.uuid4()
    app_id = uuid.uuid4()
    run = SimpleNamespace(id=uuid.uuid4(), user_id=uuid.uuid4(), app_id=app_id)
    user = SimpleNamespace(id=user_id)

    monkeypatch.setattr(
        TraceAccessService, "resolve_trace_app_id", lambda db, trace: app_id
    )
    monkeypatch.setattr(TraceAccessService, "is_app_owner", lambda db, app, actor: True)
    monkeypatch.setattr(
        "apps.shared.services.tracing.access.TracePolicyService.resolve_visibility_policy",
        lambda db, app_id=None, organization_id=None: ResolvedVisibilityPolicy(),
    )

    decision = TraceAccessService.check_trace_access(None, run, user)

    assert decision.allowed is True
    assert decision.reason_code == "app_owner"


def test_raw_access_denied_by_default_for_system_admin(monkeypatch):
    app_id = uuid.uuid4()
    run = SimpleNamespace(id=uuid.uuid4(), app_id=app_id)
    user = SimpleNamespace(id=uuid.uuid4())

    class TestAdminProvider:
        def is_system_admin(self, db, actor):
            return actor is user

    TraceRbacService.configure_provider(TestAdminProvider())

    monkeypatch.setattr(
        TraceAccessService, "resolve_trace_app_id", lambda db, trace: app_id
    )
    monkeypatch.setattr(TraceAccessService, "is_app_owner", lambda db, app, actor: False)
    monkeypatch.setattr(
        "apps.shared.services.tracing.access.TracePolicyService.resolve_visibility_policy",
        lambda db, app_id=None, organization_id=None: ResolvedVisibilityPolicy(),
    )

    decision = TraceAccessService.check_trace_access(None, run, user, view_level="raw")

    assert decision.allowed is False
    assert decision.reason_code == "admin_raw_payload_access_disabled"


def test_visibility_policy_failure_returns_fail_closed_decision(monkeypatch):
    app_id = uuid.uuid4()
    run = SimpleNamespace(id=uuid.uuid4(), app_id=app_id)
    user = SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(
        TraceAccessService, "resolve_trace_app_id", lambda db, trace: app_id
    )
    monkeypatch.setattr(TraceAccessService, "is_app_owner", lambda db, app, actor: True)

    def raise_policy_error(db, app_id=None):
        raise RuntimeError("policy unavailable")

    monkeypatch.setattr(
        "apps.shared.services.tracing.access.TracePolicyService.resolve_visibility_policy",
        raise_policy_error,
    )

    decision = TraceAccessService.check_trace_access(None, run, user, view_level="raw")

    assert decision.allowed is False
    assert decision.reason_code == "visibility_policy_unavailable"
    assert decision.app_id == app_id
    assert (
        TraceObservabilityService.get_local_counter(
            "trace_access_context_failed",
            "visibility_policy_unavailable",
            "all",
        )
        == 1
    )


def test_system_admin_policy_takes_priority_over_owner_policy(monkeypatch):
    app_id = uuid.uuid4()
    run = SimpleNamespace(id=uuid.uuid4(), app_id=app_id)
    user = SimpleNamespace(id=uuid.uuid4())

    class TestAdminProvider:
        def is_system_admin(self, db, actor):
            return actor is user

    TraceRbacService.configure_provider(TestAdminProvider())
    monkeypatch.setattr(
        TraceAccessService, "resolve_trace_app_id", lambda db, trace: app_id
    )
    monkeypatch.setattr(TraceAccessService, "is_app_owner", lambda db, app, actor: True)
    monkeypatch.setattr(
        "apps.shared.services.tracing.access.TracePolicyService.resolve_visibility_policy",
        lambda db, app_id=None, organization_id=None: ResolvedVisibilityPolicy(
            owner_raw_payload_access_enabled=True,
            admin_raw_payload_access_enabled=False,
        ),
    )

    decision = TraceAccessService.check_trace_access(None, run, user, view_level="raw")

    assert decision.allowed is False
    assert decision.reason_code == "admin_raw_payload_access_disabled"
    assert decision.is_system_admin is True
    assert decision.is_app_owner is True


def test_system_admin_requires_rbac_provider():
    user = SimpleNamespace(id=uuid.uuid4(), role="admin", is_system_admin=True)

    assert TraceAccessService.is_system_admin(None, user) is False


def test_trace_rbac_service_selects_highest_workflow_auth_state():
    class FakeQuery:
        def __init__(self, db):
            self.db = db

        def join(self, *args, **kwargs):
            return self

        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return self.db.first_values.pop(0)

        def all(self):
            return self.db.all_values.pop(0)

    class FakeDb:
        def __init__(self):
            user_id = uuid.uuid4()
            organization_id = uuid.uuid4()
            workflow_id = uuid.uuid4()
            self.user = SimpleNamespace(id=user_id)
            self.workflow_id = workflow_id
            self.organization_id = organization_id
            self.first_values = [
                SimpleNamespace(id=workflow_id, organization_id=organization_id),
                SimpleNamespace(id=user_id, deactivated_at=None),
                SimpleNamespace(
                    id=organization_id,
                    created_by=uuid.uuid4(),
                    managed_by=None,
                    is_active=True,
                ),
                SimpleNamespace(
                    user_id=user_id,
                    organization_id=organization_id,
                    membership_state="active",
                    organization_auth_state="member",
                ),
            ]
            self.all_values = [
                [("read",), ("execute",), ("unknown",)],
                [],
            ]

        def query(self, *args, **kwargs):
            return FakeQuery(self)

    db = FakeDb()

    auth_state = TraceRbacService.get_workflow_auth_state(
        db,
        db.user,
        workflow_id=db.workflow_id,
        organization_id=db.organization_id,
    )

    assert auth_state == "operator"


def test_rbac_read_grants_metadata_access(monkeypatch):
    app_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    run = SimpleNamespace(id=uuid.uuid4(), app_id=app_id, workflow_id=workflow_id)
    user = SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(
        "apps.shared.services.tracing.access.TracePolicyService.resolve_visibility_policy",
        lambda db, app_id=None, organization_id=None: ResolvedVisibilityPolicy(),
    )
    monkeypatch.setattr(TraceAccessService, "is_app_owner", lambda db, app, actor: False)
    monkeypatch.setattr(
        TraceAccessService,
        "resolve_trace_organization_id",
        lambda db, trace, app, workflow: organization_id,
    )
    monkeypatch.setattr(
        TraceRbacService,
        "get_workflow_auth_state",
        lambda db, actor, workflow, organization_id=None: "viewer",
    )

    decision = TraceAccessService.check_trace_access(None, run, user)

    assert decision.allowed is True
    assert decision.reason_code == "rbac_metadata"
    assert decision.rbac_auth_state == "viewer"


def test_rbac_read_does_not_grant_redacted_payload(monkeypatch):
    app_id = uuid.uuid4()
    run = SimpleNamespace(id=uuid.uuid4(), app_id=app_id, workflow_id=uuid.uuid4())
    user = SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(TraceAccessService, "is_app_owner", lambda db, app, actor: False)
    monkeypatch.setattr(
        "apps.shared.services.tracing.access.TracePolicyService.resolve_visibility_policy",
        lambda db, app_id=None, organization_id=None: ResolvedVisibilityPolicy(
            owner_redacted_payload_access_enabled=True,
        ),
    )
    monkeypatch.setattr(
        TraceRbacService,
        "get_workflow_auth_state",
        lambda db, actor, workflow, organization_id=None: "viewer",
    )

    decision = TraceAccessService.check_trace_access(
        None, run, user, view_level="redacted"
    )

    assert decision.allowed is False
    assert decision.reason_code == "rbac_redacted_payload_access_disabled"


def test_rbac_builder_grants_redacted_payload_when_policy_allows(monkeypatch):
    app_id = uuid.uuid4()
    run = SimpleNamespace(id=uuid.uuid4(), app_id=app_id, workflow_id=uuid.uuid4())
    user = SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(TraceAccessService, "is_app_owner", lambda db, app, actor: False)
    monkeypatch.setattr(
        "apps.shared.services.tracing.access.TracePolicyService.resolve_visibility_policy",
        lambda db, app_id=None, organization_id=None: ResolvedVisibilityPolicy(
            owner_redacted_payload_access_enabled=True,
        ),
    )
    monkeypatch.setattr(
        TraceRbacService,
        "get_workflow_auth_state",
        lambda db, actor, workflow, organization_id=None: "builder",
    )

    decision = TraceAccessService.check_trace_access(
        None, run, user, view_level="redacted"
    )

    assert decision.allowed is True
    assert decision.reason_code == "rbac_redacted"


def test_rbac_manager_raw_access_still_requires_visibility_policy(monkeypatch):
    app_id = uuid.uuid4()
    run = SimpleNamespace(id=uuid.uuid4(), app_id=app_id, workflow_id=uuid.uuid4())
    user = SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(TraceAccessService, "is_app_owner", lambda db, app, actor: False)
    monkeypatch.setattr(
        "apps.shared.services.tracing.access.TracePolicyService.resolve_visibility_policy",
        lambda db, app_id=None, organization_id=None: ResolvedVisibilityPolicy(
            owner_raw_payload_access_enabled=False,
        ),
    )
    monkeypatch.setattr(
        TraceRbacService,
        "get_workflow_auth_state",
        lambda db, actor, workflow, organization_id=None: "manager",
    )

    decision = TraceAccessService.check_trace_access(None, run, user, view_level="raw")

    assert decision.allowed is False
    assert decision.reason_code == "rbac_raw_payload_access_disabled"


def test_rbac_manager_raw_access_allowed_when_policy_allows(monkeypatch):
    app_id = uuid.uuid4()
    run = SimpleNamespace(id=uuid.uuid4(), app_id=app_id, workflow_id=uuid.uuid4())
    user = SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(TraceAccessService, "is_app_owner", lambda db, app, actor: False)
    monkeypatch.setattr(
        "apps.shared.services.tracing.access.TracePolicyService.resolve_visibility_policy",
        lambda db, app_id=None, organization_id=None: ResolvedVisibilityPolicy(
            owner_raw_payload_access_enabled=True,
        ),
    )
    monkeypatch.setattr(
        TraceRbacService,
        "get_workflow_auth_state",
        lambda db, actor, workflow, organization_id=None: "manager",
    )

    decision = TraceAccessService.check_trace_access(None, run, user, view_level="raw")

    assert decision.allowed is True
    assert decision.reason_code == "rbac_raw"


def test_rbac_lookup_failure_does_not_open_regular_user_access(monkeypatch):
    app_id = uuid.uuid4()
    run = SimpleNamespace(id=uuid.uuid4(), app_id=app_id, workflow_id=uuid.uuid4())
    user = SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(TraceAccessService, "is_app_owner", lambda db, app, actor: False)
    monkeypatch.setattr(
        "apps.shared.services.tracing.access.TracePolicyService.resolve_visibility_policy",
        lambda db, app_id=None, organization_id=None: ResolvedVisibilityPolicy(),
    )

    def raise_rbac_error(db, actor, workflow, organization_id=None):
        raise RuntimeError("rbac unavailable")

    monkeypatch.setattr(
        TraceRbacService,
        "get_workflow_auth_state",
        raise_rbac_error,
    )

    decision = TraceAccessService.check_trace_access(None, run, user)

    assert decision.allowed is False
    assert decision.reason_code == "regular_user_trace_access_denied"
    assert (
        TraceObservabilityService.get_local_counter(
            "trace_access_context_failed",
            "rbac_context_unavailable",
            "all",
        )
        == 1
    )


def test_visibility_policy_receives_resolved_organization_id(monkeypatch):
    app_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    run = SimpleNamespace(id=uuid.uuid4(), app_id=app_id, workflow_id=workflow_id)
    user = SimpleNamespace(id=uuid.uuid4())
    captured = {}

    def resolve_visibility_policy(db, app_id=None, organization_id=None):
        captured["app_id"] = app_id
        captured["organization_id"] = organization_id
        return ResolvedVisibilityPolicy()

    monkeypatch.setattr(
        "apps.shared.services.tracing.access.TracePolicyService.resolve_visibility_policy",
        resolve_visibility_policy,
    )
    monkeypatch.setattr(TraceAccessService, "is_app_owner", lambda db, app, actor: False)
    monkeypatch.setattr(
        TraceAccessService,
        "resolve_trace_organization_id",
        lambda db, trace, app, workflow: organization_id,
    )

    TraceAccessService.check_trace_access(None, run, user)

    assert captured["app_id"] == app_id
    assert captured["organization_id"] == organization_id


def test_llm_span_hides_io_without_prompt_completion_policy(monkeypatch):
    span = SimpleNamespace(
        id=uuid.uuid4(),
        workflow_run_id=uuid.uuid4(),
        node_id="llm-1",
        node_type="llmNode",
        status="success",
        started_at=None,
        finished_at=None,
        duration=None,
        inputs={"prompt": "hello"},
        outputs={"text": "world"},
        process_data={},
        trace_metadata={},
        redaction_applied=True,
        pii_detected=False,
        sequence=1,
        retry_count=0,
    )
    run = SimpleNamespace(id=span.workflow_run_id)
    user = SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(
        TraceAccessService,
        "check_trace_access",
        lambda db, trace, actor, view_level="metadata", payload_kind=None: SimpleNamespace(
            allowed=payload_kind not in {"prompt", "completion"}
        ),
    )

    detail = TraceQueryService.span_detail(
        span, view_level="redacted", db=object(), run=run, user=user
    )

    assert detail["inputs"] is None
    assert detail["outputs"] is None


def test_metadata_span_hides_process_data():
    span = SimpleNamespace(
        id=uuid.uuid4(),
        workflow_run_id=uuid.uuid4(),
        node_id="http-1",
        node_type="httpRequestNode",
        status="success",
        started_at=None,
        finished_at=None,
        duration=None,
        inputs={"url": "https://example.test"},
        outputs={"status": 200},
        process_data={"credential_id": str(uuid.uuid4())},
        trace_metadata={},
        redaction_applied=True,
        pii_detected=False,
        sequence=1,
        retry_count=0,
    )

    detail = TraceQueryService.span_detail(span, view_level="metadata")

    assert detail["inputs"] is None
    assert detail["outputs"] is None
    assert detail["process_data"] is None


def test_raw_access_event_keeps_hmac_actor_ref(monkeypatch):
    events = []

    class FakeSession:
        def add(self, event):
            events.append(event)

        def commit(self):
            return None

        def rollback(self):
            return None

        def close(self):
            return None

    actor_id = uuid.uuid4()
    secret = "test-trace-audit-pepper"
    monkeypatch.setenv("TRACE_AUDIT_ACTOR_REF_SECRET", secret)
    monkeypatch.setattr(
        "apps.shared.services.tracing.access.SessionLocal", lambda: FakeSession()
    )

    TraceAccessService.record_payload_access_event(
        None,
        workflow_run_id=uuid.uuid4(),
        actor_user_id=actor_id,
        view_level="raw",
        allowed=True,
        reason_code="system_admin_raw",
        payload_id=uuid.uuid4(),
    )

    assert events[0].actor_user_id == actor_id
    assert events[0].actor_user_ref == hmac.new(
        secret.encode("utf-8"),
        f"trace-actor:{actor_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    assert str(actor_id) not in events[0].actor_user_ref


def test_raw_access_event_omits_actor_ref_without_secret(monkeypatch):
    events = []

    class FakeSession:
        def add(self, event):
            events.append(event)

        def commit(self):
            return None

        def rollback(self):
            return None

        def close(self):
            return None

    monkeypatch.delenv("TRACE_AUDIT_ACTOR_REF_SECRET", raising=False)
    monkeypatch.setattr(
        "apps.shared.services.tracing.access.SessionLocal", lambda: FakeSession()
    )

    TraceAccessService.record_payload_access_event(
        None,
        workflow_run_id=uuid.uuid4(),
        actor_user_id=uuid.uuid4(),
        view_level="raw",
        allowed=True,
        reason_code="system_admin_raw",
        payload_id=uuid.uuid4(),
    )

    assert events[0].actor_user_ref is None


def test_denied_raw_access_audit_failure_preserves_denial(monkeypatch):
    class FailingSession:
        def add(self, event):
            return None

        def commit(self):
            raise RuntimeError("audit unavailable")

        def rollback(self):
            return None

        def close(self):
            return None

    monkeypatch.setattr(
        "apps.shared.services.tracing.access.SessionLocal", lambda: FailingSession()
    )

    recorded = TraceAccessService.record_payload_access_event(
        None,
        workflow_run_id=uuid.uuid4(),
        actor_user_id=uuid.uuid4(),
        view_level="raw",
        allowed=False,
        reason_code="admin_raw_payload_access_disabled",
        payload_id=uuid.uuid4(),
        strict=False,
    )

    assert recorded is False
    assert (
        TraceObservabilityService.get_local_counter(
            "raw_payload_audit_failed",
            "false",
            "admin_raw_payload_access_disabled",
        )
        == 1
    )


def test_allowed_raw_access_audit_failure_blocks_response(monkeypatch):
    class FailingSession:
        def add(self, event):
            return None

        def commit(self):
            raise RuntimeError("audit unavailable")

        def rollback(self):
            return None

        def close(self):
            return None

    monkeypatch.setattr(
        "apps.shared.services.tracing.access.SessionLocal", lambda: FailingSession()
    )

    with pytest.raises(RuntimeError):
        TraceAccessService.record_payload_access_event(
            None,
            workflow_run_id=uuid.uuid4(),
            actor_user_id=uuid.uuid4(),
            view_level="raw",
            allowed=True,
            reason_code="system_admin_raw",
            payload_id=uuid.uuid4(),
            strict=True,
        )
