from __future__ import annotations

from .browser_access_errors import BrowserAccessResourceHidden
from .browser_access_models import (
    BrowserAccessRevision,
    BrowserAccessRevisionCommand,
    PublicBrowserAccessProjection,
)
from .browser_access_policy import (
    CONTRACT_VERSION,
    EMBED_CAPABLE_DEPLOYMENT_TYPES,
    browser_access_policy_digest,
    normalize_browser_access_policy,
    resolve_persisted_browser_access_policy,
)
from .browser_access_ports import (
    BrowserAccessActivationGuard,
    BrowserAccessAuditRecorder,
    BrowserAccessUnitOfWork,
    DeploymentBrowserAccessRepository,
)


class CreateBrowserAccessRevision:
    def __init__(
        self,
        repository: DeploymentBrowserAccessRepository,
        activation_guard: BrowserAccessActivationGuard,
        audit_recorder: BrowserAccessAuditRecorder,
        unit_of_work: BrowserAccessUnitOfWork,
    ) -> None:
        self.repository = repository
        self.activation_guard = activation_guard
        self.audit_recorder = audit_recorder
        self.unit_of_work = unit_of_work

    def execute(
        self,
        command: BrowserAccessRevisionCommand,
    ) -> BrowserAccessRevision:
        try:
            source = self.repository.lock_source(command.source_deployment_id)
            if source is None:
                raise BrowserAccessResourceHidden()
            policy = normalize_browser_access_policy(
                source.deployment_type,
                command.browser_access_policy,
                environment=command.environment,
            )
            if policy is None:
                raise BrowserAccessResourceHidden()
            if command.is_active:
                self.activation_guard.enforce(
                    source,
                    actor_id=command.actor_id,
                )
            revision = self.repository.create_revision(
                source,
                actor_id=command.actor_id,
                policy=policy,
                is_active=command.is_active,
            )
            self.audit_recorder.record_revision(
                command,
                source,
                revision,
                policy_digest=browser_access_policy_digest(policy),
            )
            self.unit_of_work.flush()
            self.unit_of_work.commit()
            return revision
        except Exception:
            self.unit_of_work.rollback()
            raise


class GetPublicBrowserAccessPolicy:
    def __init__(self, repository: DeploymentBrowserAccessRepository) -> None:
        self.repository = repository

    def execute(
        self,
        url_slug: str,
        *,
        environment: str | None,
    ) -> PublicBrowserAccessProjection:
        snapshot = self.repository.get_active_by_slug(url_slug)
        if (
            snapshot is None
            or snapshot.deployment_type not in EMBED_CAPABLE_DEPLOYMENT_TYPES
        ):
            raise BrowserAccessResourceHidden()
        policy = resolve_persisted_browser_access_policy(
            snapshot.deployment_type,
            snapshot.browser_access_policy,
            environment=environment,
        )
        return PublicBrowserAccessProjection(
            contract_version=CONTRACT_VERSION,
            deployment_version=snapshot.deployment_version,
            enabled=policy.embedding.enabled,
            frame_ancestors=policy.embedding.parent_origins,
        )
