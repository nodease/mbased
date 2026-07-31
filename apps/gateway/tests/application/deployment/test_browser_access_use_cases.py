from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from apps.gateway.application.deployment.browser_access_errors import (
    BrowserAccessPolicyError,
    BrowserAccessResourceHidden,
)
from apps.gateway.application.deployment.browser_access_models import (
    ActiveBrowserAccessSnapshot,
    BrowserAccessRevision,
    BrowserAccessRevisionCommand,
    BrowserAccessSourceSnapshot,
)
from apps.gateway.application.deployment.browser_access_use_cases import (
    CreateBrowserAccessRevision,
    GetPublicBrowserAccessPolicy,
)


def _raw_policy(*origins: str, enabled: bool = True) -> dict:
    return {
        "contract_version": "deployment_browser_access.v1",
        "embedding": {
            "enabled": enabled,
            "parent_origins": list(origins),
        },
    }


def _source(deployment_type: str = "chatbot") -> BrowserAccessSourceSnapshot:
    return BrowserAccessSourceSnapshot(
        id=uuid.uuid4(),
        app_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        version=2,
        deployment_type=deployment_type,
        graph_snapshot={"nodes": [], "edges": []},
        config={"timeout": 30},
        input_schema={"variables": []},
        output_schema={"outputs": []},
        description="source",
        url_slug="source-chatbot",
    )


class _Repository:
    def __init__(self, source: BrowserAccessSourceSnapshot | None) -> None:
        self.source = source
        self.active: ActiveBrowserAccessSnapshot | None = None
        self.events: list[str] = []
        self.created_policy: dict | None = None

    def lock_source(self, source_deployment_id):
        self.events.append("lock")
        if self.source is None or self.source.id != source_deployment_id:
            return None
        return self.source

    def create_revision(self, source, *, actor_id, policy, is_active):
        self.events.append("create")
        self.created_policy = policy.to_dict()
        return BrowserAccessRevision(
            id=uuid.uuid4(),
            app_id=source.app_id,
            version=source.version + 1,
            deployment_type=source.deployment_type,
            graph_snapshot=source.graph_snapshot,
            config=source.config,
            input_schema=source.input_schema,
            output_schema=source.output_schema,
            description=source.description,
            created_by=actor_id,
            created_at=datetime.now(timezone.utc),
            is_active=is_active,
            browser_access_policy=policy.to_dict(),
            url_slug=source.url_slug,
        )

    def get_active_by_slug(self, url_slug):
        self.events.append(f"get:{url_slug}")
        return self.active


class _ActivationGuard:
    def __init__(self, events: list[str], error: Exception | None = None) -> None:
        self.events = events
        self.error = error

    def enforce(self, source, *, actor_id):
        self.events.append("activate")
        if self.error:
            raise self.error


class _Audit:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.digest: str | None = None

    def record_revision(self, command, source, revision, *, policy_digest):
        self.events.append("audit")
        self.digest = policy_digest


class _UnitOfWork:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def flush(self):
        self.events.append("flush")

    def commit(self):
        self.events.append("commit")

    def rollback(self):
        self.events.append("rollback")


def _revision_use_case(repository, *, activation_error=None):
    activation = _ActivationGuard(repository.events, activation_error)
    audit = _Audit(repository.events)
    uow = _UnitOfWork(repository.events)
    return (
        CreateBrowserAccessRevision(repository, activation, audit, uow),
        audit,
    )


def test_inactive_revision_clones_source_with_canonical_policy_and_atomic_audit() -> None:
    source = _source()
    repository = _Repository(source)
    use_case, audit = _revision_use_case(repository)
    actor_id = uuid.uuid4()

    result = use_case.execute(
        BrowserAccessRevisionCommand(
            source_deployment_id=source.id,
            actor_id=actor_id,
            browser_access_policy=_raw_policy(
                "https://B.example.com:443/",
                "https://a.example.com",
            ),
            is_active=False,
            environment="production",
        )
    )

    assert repository.events == ["lock", "create", "audit", "flush", "commit"]
    assert repository.created_policy == _raw_policy(
        "https://a.example.com",
        "https://b.example.com",
    )
    assert result.graph_snapshot is source.graph_snapshot
    assert result.config is source.config
    assert result.created_by == actor_id
    assert result.is_active is False
    assert audit.digest is not None
    assert len(audit.digest) == 64
    assert "example.com" not in audit.digest


def test_active_revision_runs_activation_guard_before_create() -> None:
    source = _source("widget")
    repository = _Repository(source)
    use_case, _ = _revision_use_case(repository)

    result = use_case.execute(
        BrowserAccessRevisionCommand(
            source_deployment_id=source.id,
            actor_id=uuid.uuid4(),
            browser_access_policy=_raw_policy(enabled=False),
            is_active=True,
            environment="production",
        )
    )

    assert result.is_active is True
    assert repository.events == [
        "lock",
        "activate",
        "create",
        "audit",
        "flush",
        "commit",
    ]


def test_revision_rolls_back_for_missing_source_wrong_type_and_activation_error() -> None:
    missing_repository = _Repository(None)
    missing_use_case, _ = _revision_use_case(missing_repository)
    missing_id = uuid.uuid4()
    command = BrowserAccessRevisionCommand(
        source_deployment_id=missing_id,
        actor_id=uuid.uuid4(),
        browser_access_policy=_raw_policy(enabled=False),
        is_active=False,
        environment="production",
    )

    with pytest.raises(BrowserAccessResourceHidden):
        missing_use_case.execute(command)
    assert missing_repository.events == ["lock", "rollback"]

    wrong_source = _source("internal_chatbot")
    wrong_repository = _Repository(wrong_source)
    wrong_use_case, _ = _revision_use_case(wrong_repository)
    with pytest.raises(BrowserAccessPolicyError) as exc_info:
        wrong_use_case.execute(
            BrowserAccessRevisionCommand(
                source_deployment_id=wrong_source.id,
                actor_id=uuid.uuid4(),
                browser_access_policy=_raw_policy(enabled=False),
                is_active=False,
                environment="production",
            )
        )
    assert exc_info.value.code == "deployment.browser_access.not_supported"
    assert wrong_repository.events == ["lock", "rollback"]

    active_source = _source()
    active_repository = _Repository(active_source)
    activation_error = RuntimeError("blocked")
    active_use_case, _ = _revision_use_case(
        active_repository,
        activation_error=activation_error,
    )
    with pytest.raises(RuntimeError, match="blocked"):
        active_use_case.execute(
            BrowserAccessRevisionCommand(
                source_deployment_id=active_source.id,
                actor_id=uuid.uuid4(),
                browser_access_policy=_raw_policy(enabled=False),
                is_active=True,
                environment="production",
            )
        )
    assert active_repository.events == ["lock", "activate", "rollback"]


def test_public_projection_returns_only_version_and_canonical_ancestors() -> None:
    repository = _Repository(None)
    repository.active = ActiveBrowserAccessSnapshot(
        deployment_version=4,
        deployment_type="chatbot",
        browser_access_policy=_raw_policy(
            "https://B.example.com:443/",
            "https://a.example.com",
        ),
    )

    result = GetPublicBrowserAccessPolicy(repository).execute(
        "public-chatbot",
        environment="production",
    )

    assert result.contract_version == "deployment_browser_access.v1"
    assert result.deployment_version == 4
    assert result.enabled is True
    assert result.frame_ancestors == (
        "https://a.example.com",
        "https://b.example.com",
    )


@pytest.mark.parametrize(
    "persisted",
    [
        None,
        {},
        {"contract_version": "unexpected", "embedding": {}},
        _raw_policy("https://example.com", enabled=False),
    ],
)
def test_public_projection_fails_closed_for_legacy_or_malformed_policy(
    persisted,
) -> None:
    repository = _Repository(None)
    repository.active = ActiveBrowserAccessSnapshot(
        deployment_version=1,
        deployment_type="widget",
        browser_access_policy=persisted,
    )

    result = GetPublicBrowserAccessPolicy(repository).execute(
        "legacy-widget",
        environment="production",
    )

    assert result.enabled is False
    assert result.frame_ancestors == ()


def test_public_projection_hides_missing_or_invalid_active_target() -> None:
    repository = _Repository(None)

    with pytest.raises(BrowserAccessResourceHidden):
        GetPublicBrowserAccessPolicy(repository).execute(
            "missing",
            environment="production",
        )
