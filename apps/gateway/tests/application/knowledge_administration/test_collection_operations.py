import uuid
from datetime import datetime, timezone

import pytest

from apps.gateway.application.knowledge_administration.collection_operations import (
    CollectionHidden,
    CollectionInputInvalid,
    CollectionItemOrderSnapshot,
    CollectionItemRank,
    CollectionOperationCommand,
    CollectionPermissionDenied,
    CollectionPolicyBlocked,
    CollectionPolicyDenied,
    CollectionSnapshot,
    CollectionStateConflict,
    CollectionLifecycleAndOrderUseCase,
    ReorderCollectionItemsCommand,
    compute_order_revision,
    validate_exact_reorder,
)


class _Authorization:
    def __init__(self, *, manager=False, domain=(), collection_actions=()):
        self.manager = manager
        self.domain = set(domain)
        self.collection_actions = set(collection_actions)

    def is_organization_manager(self, actor_id, organization_id):
        return self.manager

    def has_domain_action(self, actor_id, organization_id, action):
        return action in self.domain

    def has_collection_action(
        self, actor_id, organization_id, collection_id, action
    ):
        return action in self.collection_actions


class _Repository:
    def __init__(self, collection, items=(), *, has_source_managed_items=False):
        self.collection = collection
        self.items = list(items)
        self.source_managed_items = has_source_managed_items
        self.lifecycle_changes = []
        self.rank_changes = []
        self.locked_collection_ids = []
        self.locked_item_order_ids = []

    def lock_collection(self, organization_id, collection_id):
        self.locked_collection_ids.append(collection_id)
        if self.collection is None or self.collection.collection_id != collection_id:
            return None
        return self.collection

    def set_lifecycle_state(self, organization_id, collection_id, lifecycle_state):
        self.lifecycle_changes.append(lifecycle_state)

    def lock_item_order(self, organization_id, collection_id):
        self.locked_item_order_ids.append(collection_id)
        return list(self.items)

    def set_item_ranks(self, organization_id, collection_id, ranks):
        self.rank_changes.append(tuple(ranks))

    def has_source_managed_items(self, organization_id, collection_id):
        return self.source_managed_items


class _Audit:
    def __init__(self):
        self.records = []

    def record(self, **kwargs):
        self.records.append(kwargs)


class _UnitOfWork:
    def __init__(self):
        self.flush_count = 0
        self.commit_count = 0
        self.rollback_count = 0

    def flush(self):
        self.flush_count += 1

    def commit(self):
        self.commit_count += 1

    def rollback(self):
        self.rollback_count += 1


def _collection(**updates):
    values = {
        "collection_id": uuid.uuid4(),
        "lifecycle_state": "active",
        "sync_state": "manual",
        "is_system_managed": False,
        "is_source_managed": False,
        "visibility": "private",
    }
    values.update(updates)
    return CollectionSnapshot(**values)


def _item(rank):
    return CollectionItemOrderSnapshot(
        item_id=uuid.uuid4(),
        rank=rank,
        created_at=datetime(2026, 1, rank + 1, tzinfo=timezone.utc),
    )


def _use_case(
    collection,
    *,
    authorization=None,
    items=(),
    has_source_managed_items=False,
):
    repository = _Repository(
        collection,
        items,
        has_source_managed_items=has_source_managed_items,
    )
    audit = _Audit()
    unit_of_work = _UnitOfWork()
    use_case = CollectionLifecycleAndOrderUseCase(
        authorization or _Authorization(manager=True),
        repository,
        audit,
        unit_of_work,
    )
    return use_case, repository, audit, unit_of_work


def _command(collection):
    return CollectionOperationCommand(
        actor_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        collection_id=collection.collection_id,
    )


def test_restore_archived_manual_collection_commits_state_and_audit_once():
    collection = _collection(lifecycle_state="archived")
    use_case, repository, audit, unit_of_work = _use_case(collection)

    result = use_case.restore(_command(collection))

    assert result.status == "changed"
    assert repository.lifecycle_changes == ["active"]
    assert [record["action"] for record in audit.records] == [
        "knowledge.collection.restored"
    ]
    assert unit_of_work.flush_count == 1
    assert unit_of_work.commit_count == 1
    assert unit_of_work.rollback_count == 0


def test_restore_active_is_idempotent_without_audit():
    collection = _collection()
    use_case, repository, audit, unit_of_work = _use_case(collection)

    result = use_case.restore(_command(collection))

    assert result.status == "unchanged"
    assert repository.lifecycle_changes == []
    assert audit.records == []
    assert unit_of_work.commit_count == 0
    assert unit_of_work.rollback_count == 1


@pytest.mark.parametrize(
    ("updates", "error_type"),
    [
        ({"lifecycle_state": "deleted"}, CollectionHidden),
        ({"is_system_managed": True}, CollectionPolicyDenied),
        (
            {"lifecycle_state": "archived", "sync_state": "source_deleted"},
            CollectionStateConflict,
        ),
    ],
)
def test_restore_rejects_ineligible_collection_without_mutation(updates, error_type):
    collection = _collection(**({"lifecycle_state": "archived"} | updates))
    use_case, repository, audit, unit_of_work = _use_case(collection)

    with pytest.raises(error_type):
        use_case.restore(_command(collection))

    assert repository.lifecycle_changes == []
    assert audit.records == []
    assert unit_of_work.rollback_count == 1


def test_restore_requires_lifecycle_or_collection_authority():
    collection = _collection(lifecycle_state="archived")
    use_case, repository, audit, unit_of_work = _use_case(
        collection, authorization=_Authorization()
    )

    with pytest.raises(CollectionPermissionDenied):
        use_case.restore(_command(collection))

    assert repository.lifecycle_changes == []
    assert audit.records == []
    assert unit_of_work.rollback_count == 1


def test_order_revision_changes_for_membership_or_rank():
    collection_id = uuid.uuid4()
    first = _item(0)
    second = _item(1)

    baseline = compute_order_revision(collection_id, [first, second])
    reordered = compute_order_revision(
        collection_id,
        [
            CollectionItemOrderSnapshot(first.item_id, 1, first.created_at),
            CollectionItemOrderSnapshot(second.item_id, 0, second.created_at),
        ],
    )
    removed = compute_order_revision(collection_id, [first])

    assert baseline.startswith("ord_v1_")
    assert len(baseline) == len("ord_v1_") + 64
    assert len({baseline, reordered, removed}) == 3


def test_empty_order_has_stable_revision_and_reorder_cap_is_fail_closed():
    collection_id = uuid.uuid4()

    assert compute_order_revision(collection_id, []) == compute_order_revision(
        collection_id, []
    )
    oversized = [
        CollectionItemOrderSnapshot(
            item_id=uuid.uuid4(),
            rank=rank,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        for rank in range(501)
    ]
    with pytest.raises(CollectionStateConflict) as exc_info:
        validate_exact_reorder(
            oversized,
            [CollectionItemRank(item.item_id, item.rank) for item in oversized],
        )

    assert exc_info.value.reason_code == "item_reorder_limit_exceeded"


def test_reorder_requires_current_revision_and_exact_contiguous_item_set():
    collection = _collection()
    items = [_item(0), _item(1), _item(2)]
    use_case, repository, audit, unit_of_work = _use_case(collection, items=items)
    current_revision = compute_order_revision(collection.collection_id, items)
    command = ReorderCollectionItemsCommand(
        **_command(collection).__dict__,
        expected_order_revision=current_revision,
        items=(
            CollectionItemRank(items[2].item_id, 0),
            CollectionItemRank(items[0].item_id, 1),
            CollectionItemRank(items[1].item_id, 2),
        ),
    )

    result = use_case.reorder(command)

    assert result.status == "changed"
    assert result.order_revision != current_revision
    assert len(repository.rank_changes) == 1
    assert [record["action"] for record in audit.records] == [
        "knowledge.collection.items.reordered"
    ]
    assert unit_of_work.commit_count == 1


def test_reorder_rejects_stale_revision_without_rank_change():
    collection = _collection()
    items = [_item(0), _item(1)]
    use_case, repository, audit, unit_of_work = _use_case(collection, items=items)
    command = ReorderCollectionItemsCommand(
        **_command(collection).__dict__,
        expected_order_revision="ord_v1_" + "0" * 64,
        items=tuple(CollectionItemRank(item.item_id, item.rank) for item in items),
    )

    with pytest.raises(CollectionStateConflict) as exc_info:
        use_case.reorder(command)

    assert exc_info.value.reason_code == "collection_order_stale"
    assert repository.rank_changes == []
    assert audit.records == []
    assert unit_of_work.rollback_count == 1


def test_reorder_hides_deleted_collection_before_item_order_access():
    collection = _collection(lifecycle_state="deleted")
    use_case, repository, audit, unit_of_work = _use_case(collection)
    command = ReorderCollectionItemsCommand(
        **_command(collection).__dict__,
        expected_order_revision="unreachable",
        items=(),
    )

    with pytest.raises(CollectionHidden):
        use_case.reorder(command)

    assert repository.locked_item_order_ids == []
    assert repository.rank_changes == []
    assert audit.records == []
    assert unit_of_work.commit_count == 0
    assert unit_of_work.rollback_count == 1


@pytest.mark.parametrize(
    "ranks",
    [
        (0, 0),
        (0, 2),
        (1, 2),
        (-1, 0),
    ],
)
def test_reorder_rejects_duplicate_or_gapped_ranks(ranks):
    collection = _collection()
    items = [_item(0), _item(1)]
    use_case, repository, audit, unit_of_work = _use_case(collection, items=items)
    command = ReorderCollectionItemsCommand(
        **_command(collection).__dict__,
        expected_order_revision=compute_order_revision(collection.collection_id, items),
        items=tuple(
            CollectionItemRank(item.item_id, rank)
            for item, rank in zip(items, ranks, strict=True)
        ),
    )

    with pytest.raises(CollectionInputInvalid):
        use_case.reorder(command)

    assert repository.rank_changes == []
    assert audit.records == []
    assert unit_of_work.rollback_count == 1


def test_public_reorder_requires_organization_manager_acknowledgement():
    collection = _collection(visibility="public")
    items = [_item(0)]
    use_case, _, _, unit_of_work = _use_case(collection, items=items)
    command = ReorderCollectionItemsCommand(
        **_command(collection).__dict__,
        expected_order_revision=compute_order_revision(collection.collection_id, items),
        items=(CollectionItemRank(items[0].item_id, 0),),
        acknowledged_public_runtime_exposure=False,
    )

    with pytest.raises(CollectionInputInvalid):
        use_case.reorder(command)

    assert unit_of_work.rollback_count == 1


@pytest.mark.parametrize(
    "collection_updates,has_source_managed_items",
    [
        ({"is_source_managed": True}, False),
        ({}, True),
    ],
)
def test_public_reorder_blocks_source_managed_exposure_without_public_primitive(
    collection_updates,
    has_source_managed_items,
):
    collection = _collection(visibility="public", **collection_updates)
    items = [_item(0)]
    use_case, repository, audit, unit_of_work = _use_case(
        collection,
        items=items,
        has_source_managed_items=has_source_managed_items,
    )
    command = ReorderCollectionItemsCommand(
        **_command(collection).__dict__,
        expected_order_revision=compute_order_revision(collection.collection_id, items),
        items=(CollectionItemRank(items[0].item_id, 0),),
        acknowledged_public_runtime_exposure=True,
    )

    with pytest.raises(CollectionPolicyBlocked) as exc_info:
        use_case.reorder(command)

    assert exc_info.value.reason_code == "source_public_exposure_required"
    assert repository.rank_changes == []
    assert audit.records == []
    assert unit_of_work.rollback_count == 1


def test_noop_reorder_rolls_back_lock_without_audit():
    collection = _collection()
    items = [_item(0), _item(1)]
    use_case, repository, audit, unit_of_work = _use_case(collection, items=items)
    revision = compute_order_revision(collection.collection_id, items)
    command = ReorderCollectionItemsCommand(
        **_command(collection).__dict__,
        expected_order_revision=revision,
        items=tuple(CollectionItemRank(item.item_id, item.rank) for item in items),
    )

    result = use_case.reorder(command)

    assert result.status == "unchanged"
    assert result.order_revision == revision
    assert repository.rank_changes == []
    assert audit.records == []
    assert unit_of_work.rollback_count == 1
