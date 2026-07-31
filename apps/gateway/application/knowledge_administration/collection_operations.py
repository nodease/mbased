from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol, Sequence


MAX_REORDER_ITEMS = 500
ORDER_REVISION_PREFIX = "ord_v1_"


@dataclass(frozen=True)
class CollectionOperationCommand:
    actor_id: uuid.UUID
    organization_id: uuid.UUID
    collection_id: uuid.UUID


@dataclass(frozen=True)
class CollectionSnapshot:
    collection_id: uuid.UUID
    lifecycle_state: str
    sync_state: str
    is_system_managed: bool
    is_source_managed: bool
    visibility: str


@dataclass(frozen=True)
class CollectionItemOrderSnapshot:
    item_id: uuid.UUID
    rank: int
    created_at: datetime


@dataclass(frozen=True)
class CollectionItemRank:
    item_id: uuid.UUID
    rank: int


@dataclass(frozen=True)
class ReorderCollectionItemsCommand(CollectionOperationCommand):
    expected_order_revision: str
    items: tuple[CollectionItemRank, ...]
    acknowledged_public_runtime_exposure: bool = False


@dataclass(frozen=True)
class CollectionMutationResult:
    status: Literal["changed", "unchanged"]


@dataclass(frozen=True)
class CollectionOrderMutationResult:
    status: Literal["changed", "unchanged"]
    order_revision: str


class CollectionHidden(Exception):
    pass


class CollectionPermissionDenied(Exception):
    pass


class CollectionPolicyDenied(Exception):
    pass


class CollectionPolicyBlocked(Exception):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class CollectionStateConflict(Exception):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class CollectionInputInvalid(Exception):
    pass


class CollectionPersistenceFailed(Exception):
    pass


class CollectionOperationAuthorizationPort(Protocol):
    def is_organization_manager(
        self, actor_id: uuid.UUID, organization_id: uuid.UUID
    ) -> bool: ...

    def has_domain_action(
        self,
        actor_id: uuid.UUID,
        organization_id: uuid.UUID,
        action: str,
    ) -> bool: ...

    def has_collection_action(
        self,
        actor_id: uuid.UUID,
        organization_id: uuid.UUID,
        collection_id: uuid.UUID,
        action: str,
    ) -> bool: ...


class CollectionOperationRepositoryPort(Protocol):
    def lock_collection(
        self, organization_id: uuid.UUID, collection_id: uuid.UUID
    ) -> CollectionSnapshot | None: ...

    def set_lifecycle_state(
        self,
        organization_id: uuid.UUID,
        collection_id: uuid.UUID,
        lifecycle_state: str,
    ) -> None: ...

    def lock_item_order(
        self, organization_id: uuid.UUID, collection_id: uuid.UUID
    ) -> list[CollectionItemOrderSnapshot]: ...

    def set_item_ranks(
        self,
        organization_id: uuid.UUID,
        collection_id: uuid.UUID,
        ranks: Sequence[CollectionItemRank],
    ) -> None: ...

    def has_source_managed_items(
        self,
        organization_id: uuid.UUID,
        collection_id: uuid.UUID,
    ) -> bool: ...


class CollectionOperationAuditPort(Protocol):
    def record(
        self,
        *,
        command: CollectionOperationCommand,
        action: str,
        before: dict[str, object] | None = None,
        after: dict[str, object] | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None: ...


class UnitOfWorkPort(Protocol):
    def flush(self) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


def compute_order_revision(
    collection_id: uuid.UUID,
    items: Sequence[CollectionItemOrderSnapshot],
) -> str:
    digest = hashlib.sha256()
    digest.update(b"knowledge-collection-order-v1\x00")
    digest.update(collection_id.bytes)
    for item in sorted(items, key=lambda row: (row.rank, row.created_at, row.item_id)):
        digest.update(item.item_id.bytes)
        digest.update(str(item.rank).encode("ascii"))
        digest.update(b"\x00")
    return f"{ORDER_REVISION_PREFIX}{digest.hexdigest()}"


def validate_exact_reorder(
    current: Sequence[CollectionItemOrderSnapshot],
    requested: Sequence[CollectionItemRank],
) -> None:
    if len(current) > MAX_REORDER_ITEMS:
        raise CollectionStateConflict("item_reorder_limit_exceeded")
    if len(current) != len(requested):
        raise CollectionStateConflict("collection_order_stale")

    requested_ids = [row.item_id for row in requested]
    requested_ranks = [row.rank for row in requested]
    if len(set(requested_ids)) != len(requested_ids):
        raise CollectionInputInvalid()
    if len(set(requested_ranks)) != len(requested_ranks):
        raise CollectionInputInvalid()
    if set(requested_ids) != {row.item_id for row in current}:
        raise CollectionStateConflict("collection_order_stale")
    if sorted(requested_ranks) != list(range(len(requested_ranks))):
        raise CollectionInputInvalid()


class CollectionLifecycleAndOrderUseCase:
    def __init__(
        self,
        authorization: CollectionOperationAuthorizationPort,
        repository: CollectionOperationRepositoryPort,
        audit: CollectionOperationAuditPort,
        unit_of_work: UnitOfWorkPort,
    ) -> None:
        self.authorization = authorization
        self.repository = repository
        self.audit = audit
        self.unit_of_work = unit_of_work

    def archive(
        self, command: CollectionOperationCommand
    ) -> CollectionMutationResult:
        collection = self._lock_collection(command)
        if collection.lifecycle_state == "deleted":
            self.unit_of_work.rollback()
            raise CollectionHidden()
        self._require_lifecycle_authority(command)
        self._require_manual_collection(collection)
        if collection.lifecycle_state == "archived":
            self.unit_of_work.rollback()
            return CollectionMutationResult("unchanged")
        if collection.lifecycle_state != "active":
            self.unit_of_work.rollback()
            raise CollectionStateConflict("collection_lifecycle_conflict")
        return self._change_lifecycle(
            command,
            before="active",
            after="archived",
            action="knowledge.collection.archived",
        )

    def restore(
        self, command: CollectionOperationCommand
    ) -> CollectionMutationResult:
        collection = self._lock_collection(command)
        if collection.lifecycle_state == "deleted":
            self.unit_of_work.rollback()
            raise CollectionHidden()
        self._require_lifecycle_authority(command)
        self._require_manual_collection(collection)
        if collection.sync_state == "source_deleted":
            self.unit_of_work.rollback()
            raise CollectionStateConflict("source_deleted")
        if collection.lifecycle_state == "active":
            self.unit_of_work.rollback()
            return CollectionMutationResult("unchanged")
        if collection.lifecycle_state != "archived":
            self.unit_of_work.rollback()
            raise CollectionStateConflict("collection_lifecycle_conflict")
        return self._change_lifecycle(
            command,
            before="archived",
            after="active",
            action="knowledge.collection.restored",
        )

    def reorder(
        self, command: ReorderCollectionItemsCommand
    ) -> CollectionOrderMutationResult:
        collection = self._lock_collection(command)
        if collection.lifecycle_state == "deleted":
            self.unit_of_work.rollback()
            raise CollectionHidden()
        self._require_membership_authority(command, collection)
        current = self.repository.lock_item_order(
            command.organization_id, command.collection_id
        )
        current_revision = compute_order_revision(command.collection_id, current)
        if command.expected_order_revision != current_revision:
            self.unit_of_work.rollback()
            raise CollectionStateConflict("collection_order_stale")
        try:
            validate_exact_reorder(current, command.items)
        except (CollectionInputInvalid, CollectionStateConflict):
            self.unit_of_work.rollback()
            raise

        current_ranks = {item.item_id: item.rank for item in current}
        if all(current_ranks[item.item_id] == item.rank for item in command.items):
            self.unit_of_work.rollback()
            return CollectionOrderMutationResult("unchanged", current_revision)

        try:
            requested_ranks = {rank.item_id: rank.rank for rank in command.items}
            self.repository.set_item_ranks(
                command.organization_id,
                command.collection_id,
                command.items,
            )
            self.audit.record(
                command=command,
                action="knowledge.collection.items.reordered",
                metadata={"item_count_bucket": _count_bucket(len(command.items))},
            )
            self.unit_of_work.flush()
            updated = [
                CollectionItemOrderSnapshot(
                    item_id=item.item_id,
                    rank=requested_ranks[item.item_id],
                    created_at=item.created_at,
                )
                for item in current
            ]
            revision = compute_order_revision(command.collection_id, updated)
            self.unit_of_work.commit()
        except (CollectionInputInvalid, CollectionStateConflict):
            self.unit_of_work.rollback()
            raise
        except Exception as exc:
            self.unit_of_work.rollback()
            raise CollectionPersistenceFailed() from exc
        return CollectionOrderMutationResult("changed", revision)

    def _lock_collection(
        self, command: CollectionOperationCommand
    ) -> CollectionSnapshot:
        collection = self.repository.lock_collection(
            command.organization_id, command.collection_id
        )
        if collection is None:
            self.unit_of_work.rollback()
            raise CollectionHidden()
        return collection

    def _require_lifecycle_authority(
        self, command: CollectionOperationCommand
    ) -> None:
        if self.authorization.is_organization_manager(
            command.actor_id, command.organization_id
        ):
            return
        if self.authorization.has_domain_action(
            command.actor_id, command.organization_id, "lifecycle_manage"
        ):
            return
        if self.authorization.has_collection_action(
            command.actor_id,
            command.organization_id,
            command.collection_id,
            "manage",
        ):
            return
        self.unit_of_work.rollback()
        raise CollectionPermissionDenied()

    def _require_membership_authority(
        self,
        command: ReorderCollectionItemsCommand,
        collection: CollectionSnapshot,
    ) -> None:
        self._require_manual_collection(collection)
        if collection.lifecycle_state != "active":
            self.unit_of_work.rollback()
            raise CollectionStateConflict("collection_not_active")
        is_manager = self.authorization.is_organization_manager(
            command.actor_id, command.organization_id
        )
        if collection.visibility == "public":
            if not is_manager:
                self.unit_of_work.rollback()
                raise CollectionPermissionDenied()
            if not command.acknowledged_public_runtime_exposure:
                self.unit_of_work.rollback()
                raise CollectionInputInvalid()
            if collection.is_source_managed or self.repository.has_source_managed_items(
                command.organization_id,
                command.collection_id,
            ):
                self.unit_of_work.rollback()
                raise CollectionPolicyBlocked("source_public_exposure_required")
            return
        if is_manager or self.authorization.has_domain_action(
            command.actor_id, command.organization_id, "catalog_manage"
        ):
            return
        if self.authorization.has_collection_action(
            command.actor_id,
            command.organization_id,
            command.collection_id,
            "manage",
        ):
            return
        self.unit_of_work.rollback()
        raise CollectionPermissionDenied()

    def _require_manual_collection(self, collection: CollectionSnapshot) -> None:
        if collection.is_system_managed:
            self.unit_of_work.rollback()
            raise CollectionPolicyDenied()

    def _change_lifecycle(
        self,
        command: CollectionOperationCommand,
        *,
        before: str,
        after: str,
        action: str,
    ) -> CollectionMutationResult:
        try:
            self.repository.set_lifecycle_state(
                command.organization_id, command.collection_id, after
            )
            self.audit.record(
                command=command,
                action=action,
                before={"lifecycle_state": before},
                after={"lifecycle_state": after},
            )
            self.unit_of_work.flush()
            self.unit_of_work.commit()
        except Exception as exc:
            self.unit_of_work.rollback()
            raise CollectionPersistenceFailed() from exc
        return CollectionMutationResult("changed")


def _count_bucket(value: int) -> str:
    if value <= 0:
        return "0"
    if value == 1:
        return "1"
    if value <= 10:
        return "2-10"
    if value <= 50:
        return "11-50"
    if value <= 100:
        return "51-100"
    return "101+"
