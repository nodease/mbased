from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from apps.gateway.adapters.audit.sqlalchemy_deployment_browser_access_audit import (
    SqlAlchemyDeploymentBrowserAccessAuditRecorder,
)
from apps.gateway.application.deployment.browser_access_models import (
    BrowserAccessRevision,
    BrowserAccessRevisionCommand,
    BrowserAccessSourceSnapshot,
)
from apps.shared.audit.actions import AuditAction
from apps.shared.db.models.audit_log import AuditLog


class _Db:
    def __init__(self) -> None:
        self.added = []

    def add(self, value) -> None:
        self.added.append(value)


def test_revision_audit_is_transaction_bound_and_contains_only_bounded_policy_facts() -> None:
    actor_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    source_id = uuid.uuid4()
    app_id = uuid.uuid4()
    source = BrowserAccessSourceSnapshot(
        id=source_id,
        app_id=app_id,
        workflow_id=uuid.uuid4(),
        organization_id=organization_id,
        version=7,
        deployment_type="chatbot",
        graph_snapshot={"nodes": [{"data": {"prompt": "private"}}]},
        config={"internal": "private"},
        input_schema=None,
        output_schema=None,
        description=None,
        url_slug="audit-chatbot",
    )
    policy = {
        "contract_version": "deployment_browser_access.v1",
        "embedding": {
            "enabled": True,
            "parent_origins": ["https://internal.example.com"],
        },
    }
    command = BrowserAccessRevisionCommand(
        source_deployment_id=source_id,
        actor_id=actor_id,
        browser_access_policy=policy,
        is_active=False,
        environment="production",
    )
    revision = BrowserAccessRevision(
        id=uuid.uuid4(),
        app_id=app_id,
        version=8,
        deployment_type="chatbot",
        graph_snapshot=source.graph_snapshot,
        config=source.config,
        input_schema=None,
        output_schema=None,
        description=None,
        created_by=actor_id,
        created_at=datetime.now(timezone.utc),
        is_active=False,
        browser_access_policy=policy,
        url_slug=source.url_slug,
    )
    db = _Db()
    recorder = SqlAlchemyDeploymentBrowserAccessAuditRecorder(
        db,
        actor=SimpleNamespace(
            id=actor_id,
            email="reviewer@example.test",
            name="Reviewer",
        ),
    )

    recorder.record_revision(
        command,
        source,
        revision,
        policy_digest="a" * 64,
    )

    assert len(db.added) == 1
    audit = db.added[0]
    assert isinstance(audit, AuditLog)
    assert audit.action == AuditAction.WORKFLOW_DEPLOY
    assert audit.target_type == "deployment"
    assert audit.target_id == str(revision.id)
    assert audit.actor_id == actor_id
    assert audit.audit_metadata["organization_id"] == str(organization_id)
    assert audit.audit_metadata["change_kind"] == "browser_access_revision"
    assert audit.audit_metadata["source_version"] == 7
    assert audit.audit_metadata["new_version"] == 8
    assert audit.audit_metadata["contract_version"] == (
        "deployment_browser_access.v1"
    )
    assert audit.audit_metadata["embedding_enabled"] is True
    assert audit.audit_metadata["origin_count"] == 1
    assert audit.audit_metadata["policy_digest"] == "a" * 64
    assert audit.audit_metadata["activated"] is False
    serialized = json.dumps(audit.audit_metadata, sort_keys=True)
    assert "internal.example.com" not in serialized
    assert "prompt" not in serialized
    assert "private" not in serialized
