import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from apps.gateway.services.knowledge_candidate_resolver import (
    DEFAULT_DIRECT_KB_CANDIDATE_RESERVE,
    DEFAULT_MAX_CANDIDATE_KBS,
    KnowledgeCandidateResolver,
    bucket_count,
)
from apps.shared.schemas.knowledge import KnowledgeCandidateResolveRequest
from apps.shared.db.models.organization_membership import ORGANIZATION_AUTH_MEMBER
from apps.shared.permissions import AUTH_STATE_OPERATOR
from apps.shared.services.knowledge_permission_service import KnowledgePermissionHelper


ORG_ID = uuid.UUID("10000000-0000-0000-0000-000000000001")
USER_ID = uuid.UUID("20000000-0000-0000-0000-000000000001")


def _collection(collection_id: uuid.UUID | None = None, *, actions=None, safe_metadata=None):
    return SimpleNamespace(
        id=collection_id or uuid.uuid4(),
        organization_id=ORG_ID,
        lifecycle_state="active",
        sync_state="synced",
        is_system_managed=False,
        safe_metadata=safe_metadata or {},
        _actions=set(actions or []),
    )


def _kb(
    kb_id: uuid.UUID | None = None,
    *,
    name: str = "Manual KB",
    description: str | None = None,
    safe_metadata: dict | None = None,
    source_managed=False,
    source_tier: str | None = None,
    version_status: str | None = "ready",
    sync_state: str = "synced",
):
    return SimpleNamespace(
        id=kb_id or uuid.uuid4(),
        organization_id=ORG_ID,
        name=name,
        description=description,
        safe_metadata=safe_metadata or {},
        lifecycle_state="active",
        sync_state=sync_state,
        source_identity_id=uuid.uuid4() if source_managed else None,
        source_identity=None,
        active_document_version=None
        if version_status is None
        else SimpleNamespace(
            status=version_status,
            source_tier=source_tier,
        ),
    )


def _source_identity():
    return SimpleNamespace(
        display_policy_state="pending",
        safe_display_name="Safe approved label",
        raw_source_url="https://internal.example/private",
        raw_source_path="/sensitive/path",
        raw_source_title="Sensitive title",
    )


def _source_provenance(
    *,
    source_acl_state="fresh",
    requester_source_authorization="allowed",
    source_permission_action="read",
    freshness_epoch=1,
    expires_at=None,
):
    return SimpleNamespace(
        source_acl_state=source_acl_state,
        requester_source_authorization=requester_source_authorization,
        source_permission_action=source_permission_action,
        freshness_epoch=freshness_epoch,
        freshness_expires_at=expires_at
        if expires_at is not None
        else datetime.now(timezone.utc) + timedelta(hours=1),
    )


class FakePermissionHelper(KnowledgePermissionHelper):
    def __init__(
        self,
        *,
        collection_actions=None,
        kb_auth_state=AUTH_STATE_OPERATOR,
        source_policy_auth_state="none",
        source_provenance=None,
    ):
        super().__init__(None, user_id=USER_ID, organization_id=ORG_ID)
        self.collection_actions = collection_actions or {}
        self.kb_auth_state = kb_auth_state
        self.source_policy_auth_state = source_policy_auth_state
        self.source_provenance = source_provenance
        self.collection_action_calls = []
        self.bulk_kb_calls = []

    def _organization_auth_state(self):
        return ORGANIZATION_AUTH_MEMBER

    def _collection_has_action(self, collection_id, action):
        self.collection_action_calls.append((collection_id, action))
        return action in self.collection_actions.get(collection_id, set())

    def _manual_kb_auth_state(self, kb):
        return self.kb_auth_state

    def _source_policy_kb_use_auth_state(self, kb):
        return self.source_policy_auth_state

    def _latest_source_authorization(self, kb):
        return self.source_provenance

    def _now(self):
        return datetime(2026, 7, 4, tzinfo=timezone.utc)

    def _prepare_bulk_kb_context(self, kbs):
        return None

    def bulk_evaluate_kb_use(self, kbs):
        kb_list = list(kbs)
        self.bulk_kb_calls.append([kb.id for kb in kb_list])
        return super().bulk_evaluate_kb_use(kb_list)


class FakeResolver(KnowledgeCandidateResolver):
    def __init__(
        self,
        *,
        helper,
        runtime_helper=None,
        collections=None,
        items=None,
        kbs=None,
    ):
        super().__init__(
            None,
            user_id=USER_ID,
            organization_id=ORG_ID,
            permission_helper=helper,
            runtime_permission_helper=runtime_helper,
        )
        self._fake_collections = list(collections or [])
        self._fake_items = list(items or [])
        self._fake_kbs = {kb.id: kb for kb in (kbs or [])}
        self.requested_item_collection_ids = None
        self.requested_item_limit = None

    def _collections(self, collection_ids, max_collections):
        if collection_ids is None:
            return self._fake_collections[:max_collections]
        requested = set(collection_ids)
        return [
            collection
            for collection in self._fake_collections
            if collection.id in requested
        ][:max_collections]

    def _collection_items(self, collection_ids, max_candidate_kbs):
        allowed_collection_ids = set(collection_ids)
        self.requested_item_collection_ids = allowed_collection_ids
        self.requested_item_limit = max_candidate_kbs
        items = [
            item
            for item in self._fake_items
            if item.collection_id in allowed_collection_ids
        ]
        if max_candidate_kbs is None:
            return items
        selected_kb_ids = set()
        for item in items:
            if len(selected_kb_ids) >= max_candidate_kbs:
                break
            selected_kb_ids.add(item.knowledge_base_id)
        return [
            item for item in items if item.knowledge_base_id in selected_kb_ids
        ]

    def _knowledge_bases_by_id(self, knowledge_base_ids):
        return {
            kb_id: self._fake_kbs[kb_id]
            for kb_id in knowledge_base_ids
            if kb_id in self._fake_kbs
        }

    def _direct_knowledge_bases(
        self,
        max_candidate_kbs,
        *,
        offset=0,
        excluded_kb_ids=None,
        excluded_collection_ids=None,
    ):
        excluded = excluded_kb_ids or set()
        excluded_collections = excluded_collection_ids or set()
        collection_member_ids = {
            item.knowledge_base_id
            for item in self._fake_items
            if item.collection_id in excluded_collections
        }
        values = [
            kb
            for kb in self._fake_kbs.values()
            if kb.id not in excluded and kb.id not in collection_member_ids
        ]
        values = values[offset:]
        return values if max_candidate_kbs is None else values[:max_candidate_kbs]


def test_requested_collections_preserve_caller_order_before_limit():
    collection_a = _collection()
    collection_b = _collection()

    class Query:
        def filter(self, *_args):
            return self

        def all(self):
            return [collection_a, collection_b]

    resolver = KnowledgeCandidateResolver.__new__(KnowledgeCandidateResolver)
    resolver.db = SimpleNamespace(query=lambda _model: Query())
    resolver.organization_id = ORG_ID

    result = resolver._collections([collection_b.id, collection_a.id], 1)

    assert [collection.id for collection in result] == [collection_b.id]


def test_collection_read_does_not_allow_route():
    collection = _collection(actions={"read"})
    helper = FakePermissionHelper(
        collection_actions={collection.id: {"read"}},
    )

    read_decision = helper.evaluate_collection_action(collection, "read")
    route_decision = helper.evaluate_collection_action(collection, "route")

    assert read_decision.allowed is True
    assert route_decision.allowed is False
    assert route_decision.reason_code == "collection_route_denied"
    assert route_decision.external_reason_code == "permission.denied"


def test_explicit_collection_revalidation_checks_route_without_loading_children():
    allowed = _collection()
    denied = _collection()
    helper = FakePermissionHelper(
        collection_actions={
            allowed.id: {"route"},
            denied.id: {"read"},
        },
    )
    resolver = FakeResolver(
        helper=helper,
        collections=[allowed, denied],
    )

    result = resolver.resolve_explicit_collections([denied.id, allowed.id])

    assert [group.collection_id for group in result] == [allowed.id]
    assert resolver.requested_item_collection_ids is None


def test_explicit_kb_mode_does_not_require_collection_route():
    kb = _kb()
    helper = FakePermissionHelper()
    resolver = FakeResolver(helper=helper, kbs=[kb])

    result = resolver.resolve_explicit_kbs([kb.id])

    assert [candidate.candidate_id for candidate in result.candidates] == [kb.id]
    assert helper.collection_action_calls == []
    assert helper.bulk_kb_calls == [[kb.id]]
    assert result.hidden_candidate_count_bucket == "0"


def test_auto_collection_mode_uses_only_route_allowed_collections():
    allowed_collection = _collection()
    denied_collection = _collection()
    allowed_kb = _kb()
    denied_kb = _kb()
    helper = FakePermissionHelper(
        collection_actions={allowed_collection.id: {"route"}},
    )
    items = [
        SimpleNamespace(
            collection_id=allowed_collection.id,
            knowledge_base_id=allowed_kb.id,
        ),
        SimpleNamespace(
            collection_id=denied_collection.id,
            knowledge_base_id=denied_kb.id,
        ),
    ]
    resolver = FakeResolver(
        helper=helper,
        collections=[allowed_collection, denied_collection],
        items=items,
        kbs=[allowed_kb, denied_kb],
    )

    result = resolver.resolve_auto_collection_candidates()

    assert [candidate.candidate_id for candidate in result.candidates] == [
        allowed_kb.id
    ]
    assert resolver.requested_item_collection_ids == {allowed_collection.id}
    assert result.unavailable_candidate_count_bucket == "1"


def test_builder_hierarchy_filters_collection_route_and_child_kb_use_independently():
    visible_collection = _collection(
        safe_metadata={"safe_label": "사내 문서", "topics": ["인사"]}
    )
    denied_collection = _collection(
        safe_metadata={"safe_label": "숨김 Collection"}
    )
    visible_child = _kb()
    denied_child = _kb()
    ungrouped_kb = _kb()

    class PerKbPermissionHelper(FakePermissionHelper):
        def _manual_kb_auth_state(self, kb):
            return (
                AUTH_STATE_OPERATOR
                if kb.id in {visible_child.id, ungrouped_kb.id}
                else "none"
            )

    helper = PerKbPermissionHelper(
        collection_actions={visible_collection.id: {"route"}}
    )
    resolver = FakeResolver(
        helper=helper,
        collections=[visible_collection, denied_collection],
        items=[
            SimpleNamespace(
                collection_id=visible_collection.id,
                knowledge_base_id=visible_child.id,
            ),
            SimpleNamespace(
                collection_id=visible_collection.id,
                knowledge_base_id=visible_child.id,
            ),
            SimpleNamespace(
                collection_id=visible_collection.id,
                knowledge_base_id=denied_child.id,
            ),
            SimpleNamespace(
                collection_id=denied_collection.id,
                knowledge_base_id=ungrouped_kb.id,
            ),
        ],
        kbs=[visible_child, denied_child, ungrouped_kb],
    )

    result = resolver.resolve_builder_hierarchy()

    assert [group.collection_id for group in result.collections] == [
        visible_collection.id
    ]
    assert [
        candidate.candidate_id for candidate in result.collections[0].candidates
    ] == [visible_child.id]
    assert [candidate.candidate_id for candidate in result.ungrouped_candidates] == [
        ungrouped_kb.id
    ]
    assert denied_child.id not in {
        candidate.candidate_id
        for group in result.collections
        for candidate in group.candidates
    }
    assert result.hidden_candidate_count_bucket == "0"
    assert resolver.requested_item_collection_ids == {visible_collection.id}


def test_auto_collection_candidate_carries_safe_collection_summary_metadata():
    collection = _collection(
        safe_metadata={
            "safe_label": "HR 정책",
            "topics": ["사내 문서", "휴가 정책"],
            "raw_source_url": "https://internal.example/private",
        }
    )
    kb = _kb()
    helper = FakePermissionHelper(collection_actions={collection.id: {"route"}})
    resolver = FakeResolver(
        helper=helper,
        collections=[collection],
        items=[
            SimpleNamespace(
                collection_id=collection.id,
                knowledge_base_id=kb.id,
            )
        ],
        kbs=[kb],
    )

    result = resolver.resolve_auto_collection_candidates()

    candidate_metadata = result.candidates[0].safe_metadata
    assert candidate_metadata["collection_id"] == str(collection.id)
    assert candidate_metadata["collection_safe_label"] == "HR 정책"
    assert candidate_metadata["collection_safe_topics"] == ["사내 문서", "휴가 정책"]
    assert candidate_metadata["route_scope_type"] == "auto_collection"
    assert candidate_metadata["linked_kb_count_bucket"] == "1"
    assert "raw_source_url" not in candidate_metadata


def test_collection_safe_label_is_sanitized_before_candidate_projection():
    collection = _collection(
        safe_metadata={
            "safe_label": "HR policy token=secret-value https://private.example/path",
        }
    )
    kb = _kb()
    helper = FakePermissionHelper(collection_actions={collection.id: {"route"}})
    resolver = FakeResolver(
        helper=helper,
        collections=[collection],
        items=[
            SimpleNamespace(
                collection_id=collection.id,
                knowledge_base_id=kb.id,
            )
        ],
        kbs=[kb],
    )

    result = resolver.resolve_builder_hierarchy()

    assert result.collections[0].safe_label == "HR policy"


def test_auto_collection_without_collection_candidate_falls_back_to_direct_authorized_kb():
    collection = _collection()
    direct_kb = _kb()
    helper = FakePermissionHelper(collection_actions={collection.id: {"route"}})
    resolver = FakeResolver(
        helper=helper,
        collections=[collection],
        items=[],
        kbs=[direct_kb],
    )

    result = resolver.resolve_auto_collection_candidates()

    assert [candidate.candidate_id for candidate in result.candidates] == [
        direct_kb.id
    ]
    assert resolver.requested_item_collection_ids == {collection.id}


def test_explicit_empty_collection_scope_does_not_fall_back_to_direct_kb():
    direct_kb = _kb()
    helper = FakePermissionHelper()
    resolver = FakeResolver(helper=helper, kbs=[direct_kb])

    result = resolver.resolve_auto_collection_candidates(collection_ids=[])

    assert result.candidates == []


def test_candidate_excludes_kb_without_active_ready_version_and_carries_ready_source_tier():
    collection = _collection()
    ready_kb = _kb(source_tier="company_policy")
    staging_kb = _kb(source_tier="conversation_or_thread", version_status="indexing")
    helper = FakePermissionHelper(collection_actions={collection.id: {"route"}})
    resolver = FakeResolver(
        helper=helper,
        collections=[collection],
        items=[
            SimpleNamespace(
                collection_id=collection.id,
                knowledge_base_id=ready_kb.id,
            ),
            SimpleNamespace(
                collection_id=collection.id,
                knowledge_base_id=staging_kb.id,
            ),
        ],
        kbs=[ready_kb, staging_kb],
    )

    result = resolver.resolve_auto_collection_candidates()

    metadata_by_kb_id = {
        candidate.candidate_id: candidate.safe_metadata
        for candidate in result.candidates
    }
    assert metadata_by_kb_id[ready_kb.id]["source_tier"] == "company_policy"
    assert staging_kb.id not in metadata_by_kb_id
    assert result.unavailable_candidate_count_bucket == "1"


def test_candidate_excludes_source_deleted_kb():
    collection = _collection()
    active_kb = _kb()
    deleted_kb = _kb(sync_state="source_deleted")
    helper = FakePermissionHelper(collection_actions={collection.id: {"route"}})
    resolver = FakeResolver(
        helper=helper,
        collections=[collection],
        items=[
            SimpleNamespace(
                collection_id=collection.id,
                knowledge_base_id=active_kb.id,
            ),
            SimpleNamespace(
                collection_id=collection.id,
                knowledge_base_id=deleted_kb.id,
            ),
        ],
        kbs=[active_kb, deleted_kb],
    )

    result = resolver.resolve_auto_collection_candidates()

    assert [candidate.candidate_id for candidate in result.candidates] == [active_kb.id]
    assert result.unavailable_candidate_count_bucket == "1"


def test_candidate_excludes_kb_without_active_document_version():
    collection = _collection()
    active_kb = _kb()
    missing_version_kb = _kb(version_status=None)
    helper = FakePermissionHelper(collection_actions={collection.id: {"route"}})
    resolver = FakeResolver(
        helper=helper,
        collections=[collection],
        items=[
            SimpleNamespace(
                collection_id=collection.id,
                knowledge_base_id=active_kb.id,
            ),
            SimpleNamespace(
                collection_id=collection.id,
                knowledge_base_id=missing_version_kb.id,
            ),
        ],
        kbs=[active_kb, missing_version_kb],
    )

    result = resolver.resolve_auto_collection_candidates()

    assert [candidate.candidate_id for candidate in result.candidates] == [active_kb.id]
    assert result.unavailable_candidate_count_bucket == "1"


def test_builder_candidate_mode_keeps_authorized_unready_kb_selectable():
    collection = _collection()
    indexing_kb = _kb(version_status="indexing")
    helper = FakePermissionHelper(collection_actions={collection.id: {"route"}})
    resolver = FakeResolver(
        helper=helper,
        collections=[collection],
        items=[
            SimpleNamespace(
                collection_id=collection.id,
                knowledge_base_id=indexing_kb.id,
            )
        ],
        kbs=[indexing_kb],
    )

    result = resolver.resolve_auto_collection_candidates(
        allow_unready_candidates=True,
    )

    assert [candidate.candidate_id for candidate in result.candidates] == [indexing_kb.id]
    assert result.candidates[0].safe_metadata["active_document_version_status"] == "missing"


def test_auto_collection_summary_count_uses_authorized_candidate_subset():
    collection = _collection(safe_metadata={"safe_label": "HR 정책"})
    denied_kb = _kb()
    allowed_kb = _kb()

    class PerKbPermissionHelper(FakePermissionHelper):
        def _manual_kb_auth_state(self, kb):
            return AUTH_STATE_OPERATOR if kb.id == allowed_kb.id else "none"

    helper = PerKbPermissionHelper(collection_actions={collection.id: {"route"}})
    resolver = FakeResolver(
        helper=helper,
        collections=[collection],
        items=[
            SimpleNamespace(
                collection_id=collection.id,
                knowledge_base_id=denied_kb.id,
            ),
            SimpleNamespace(
                collection_id=collection.id,
                knowledge_base_id=allowed_kb.id,
            ),
        ],
        kbs=[denied_kb, allowed_kb],
    )

    result = resolver.resolve_auto_collection_candidates()

    assert [candidate.candidate_id for candidate in result.candidates] == [
        allowed_kb.id
    ]
    assert result.candidates[0].safe_metadata["linked_kb_count_bucket"] == "1"
    assert result.unavailable_candidate_count_bucket == "1"


def test_auto_collection_cap_applies_after_route_authorization():
    denied_collection = _collection()
    allowed_collection = _collection()
    allowed_kb = _kb()
    helper = FakePermissionHelper(
        collection_actions={allowed_collection.id: {"route"}},
    )
    resolver = FakeResolver(
        helper=helper,
        collections=[denied_collection, allowed_collection],
        items=[
            SimpleNamespace(
                collection_id=allowed_collection.id,
                knowledge_base_id=allowed_kb.id,
            )
        ],
        kbs=[allowed_kb],
    )

    result = resolver.resolve_auto_collection_candidates(max_collections=1)

    assert [candidate.candidate_id for candidate in result.candidates] == [
        allowed_kb.id
    ]
    assert resolver.requested_item_collection_ids == {allowed_collection.id}
    assert result.unavailable_candidate_count_bucket == "1"


def test_explicit_collection_cap_applies_after_route_authorization():
    denied_collection = _collection()
    allowed_collection = _collection()
    allowed_kb = _kb()
    helper = FakePermissionHelper(
        collection_actions={allowed_collection.id: {"route"}},
    )
    resolver = FakeResolver(
        helper=helper,
        collections=[denied_collection, allowed_collection],
        items=[
            SimpleNamespace(
                collection_id=allowed_collection.id,
                knowledge_base_id=allowed_kb.id,
            )
        ],
        kbs=[allowed_kb],
    )

    result = resolver.resolve_auto_collection_candidates(
        collection_ids=[denied_collection.id, allowed_collection.id],
        max_collections=1,
    )

    assert [candidate.candidate_id for candidate in result.candidates] == [
        allowed_kb.id
    ]
    assert resolver.requested_item_collection_ids == {allowed_collection.id}
    assert result.unavailable_candidate_count_bucket == "1"


def test_builder_hierarchy_can_defer_collection_limit_until_after_scoring():
    collection_a = _collection()
    collection_b = _collection()
    kb_a = _kb()
    kb_b = _kb()
    helper = FakePermissionHelper(
        collection_actions={
            collection_a.id: {"route"},
            collection_b.id: {"route"},
        },
    )
    resolver = FakeResolver(
        helper=helper,
        collections=[collection_a, collection_b],
        items=[
            SimpleNamespace(
                collection_id=collection_a.id,
                knowledge_base_id=kb_a.id,
            ),
            SimpleNamespace(
                collection_id=collection_b.id,
                knowledge_base_id=kb_b.id,
            ),
        ],
        kbs=[kb_a, kb_b],
    )

    result = resolver.resolve_builder_hierarchy(
        max_collections=1,
        apply_collection_limit=False,
    )

    assert {group.collection_id for group in result.collections} == {
        collection_a.id,
        collection_b.id,
    }


def test_builder_hierarchy_defers_display_limit_but_keeps_internal_candidate_cap():
    kbs = [_kb() for _ in range(DEFAULT_MAX_CANDIDATE_KBS + 1)]
    resolver = FakeResolver(
        helper=FakePermissionHelper(),
        kbs=kbs,
    )

    result = resolver.resolve_builder_hierarchy(
        max_candidate_kbs=DEFAULT_MAX_CANDIDATE_KBS,
        apply_candidate_limit=False,
    )

    assert len(result.ungrouped_candidates) == DEFAULT_MAX_CANDIDATE_KBS


def test_builder_hierarchy_keeps_internal_cap_for_collection_items():
    collection = _collection()
    kb = _kb()
    resolver = FakeResolver(
        helper=FakePermissionHelper(
            collection_actions={collection.id: {"route"}},
        ),
        collections=[collection],
        items=[
            SimpleNamespace(
                collection_id=collection.id,
                knowledge_base_id=kb.id,
            )
        ],
        kbs=[kb],
    )

    resolver.resolve_builder_hierarchy(
        max_candidate_kbs=DEFAULT_MAX_CANDIDATE_KBS,
        apply_candidate_limit=False,
    )

    assert resolver.requested_item_limit == DEFAULT_MAX_CANDIDATE_KBS


def test_builder_hierarchy_internal_cap_counts_unique_kbs_not_membership_rows():
    collection_a = _collection()
    collection_b = _collection()
    shared_kb = _kb()
    second_kb = _kb()
    resolver = FakeResolver(
        helper=FakePermissionHelper(
            collection_actions={
                collection_a.id: {"route"},
                collection_b.id: {"route"},
            },
        ),
        collections=[collection_a, collection_b],
        items=[
            SimpleNamespace(
                collection_id=collection_a.id,
                knowledge_base_id=shared_kb.id,
            ),
            SimpleNamespace(
                collection_id=collection_b.id,
                knowledge_base_id=shared_kb.id,
            ),
            SimpleNamespace(
                collection_id=collection_b.id,
                knowledge_base_id=second_kb.id,
            ),
        ],
        kbs=[shared_kb, second_kb],
    )

    result = resolver.resolve_builder_hierarchy(
        max_candidate_kbs=2,
        apply_candidate_limit=False,
    )

    children_by_collection = {
        group.collection_id: {
            candidate.candidate_id for candidate in group.candidates
        }
        for group in result.collections
    }
    assert children_by_collection == {
        collection_a.id: {shared_kb.id},
        collection_b.id: {shared_kb.id, second_kb.id},
    }


def test_builder_hierarchy_internal_cap_applies_to_linked_and_direct_kb_union():
    collection = _collection()
    direct_a = _kb()
    direct_b = _kb()
    linked_kb = _kb()
    helper = FakePermissionHelper(
        collection_actions={collection.id: {"route"}},
    )
    resolver = FakeResolver(
        helper=helper,
        collections=[collection],
        items=[
            SimpleNamespace(
                collection_id=collection.id,
                knowledge_base_id=linked_kb.id,
            )
        ],
        kbs=[direct_a, direct_b, linked_kb],
    )

    result = resolver.resolve_builder_hierarchy(
        max_candidate_kbs=2,
        apply_candidate_limit=False,
    )

    evaluated_kb_ids = {
        kb_id for call in helper.bulk_kb_calls for kb_id in call
    }
    assert len(evaluated_kb_ids) <= 2
    assert result.collections[0].candidates[0].candidate_id == linked_kb.id


def test_builder_hierarchy_reserves_bounded_space_for_direct_kbs():
    collection = _collection()
    denied_linked_a = _kb()
    denied_linked_b = _kb()
    allowed_direct = _kb()

    class PerKbPermissionHelper(FakePermissionHelper):
        def _manual_kb_auth_state(self, kb):
            return AUTH_STATE_OPERATOR if kb.id == allowed_direct.id else "none"

    helper = PerKbPermissionHelper(
        collection_actions={collection.id: {"route"}},
    )
    resolver = FakeResolver(
        helper=helper,
        collections=[collection],
        items=[
            SimpleNamespace(
                collection_id=collection.id,
                knowledge_base_id=denied_linked_a.id,
            ),
            SimpleNamespace(
                collection_id=collection.id,
                knowledge_base_id=denied_linked_b.id,
            ),
        ],
        kbs=[denied_linked_a, denied_linked_b, allowed_direct],
    )

    result = resolver.resolve_builder_hierarchy(
        max_candidate_kbs=2,
        apply_candidate_limit=False,
    )

    evaluated_kb_ids = {
        kb_id for call in helper.bulk_kb_calls for kb_id in call
    }
    assert len(evaluated_kb_ids) <= 2
    assert [
        candidate.candidate_id for candidate in result.ungrouped_candidates
    ] == [allowed_direct.id]


def test_builder_hierarchy_pages_past_denied_direct_kbs_within_shared_budget():
    collection = _collection()
    denied_direct = [
        _kb(name=f"Denied direct {index}")
        for index in range(DEFAULT_DIRECT_KB_CANDIDATE_RESERVE)
    ]
    allowed_direct = _kb(name="Allowed direct")

    class PerKbPermissionHelper(FakePermissionHelper):
        def _manual_kb_auth_state(self, kb):
            return AUTH_STATE_OPERATOR if kb.id == allowed_direct.id else "none"

    helper = PerKbPermissionHelper(
        collection_actions={collection.id: {"route"}},
    )
    resolver = FakeResolver(
        helper=helper,
        collections=[collection],
        kbs=[*denied_direct, allowed_direct],
    )

    result = resolver.resolve_builder_hierarchy(
        max_candidate_kbs=50,
        apply_candidate_limit=False,
    )

    evaluated_kb_ids = [
        kb_id for call in helper.bulk_kb_calls for kb_id in call
    ]
    assert len(evaluated_kb_ids) == DEFAULT_DIRECT_KB_CANDIDATE_RESERVE + 1
    assert len(evaluated_kb_ids) <= 50
    assert [
        candidate.candidate_id for candidate in result.ungrouped_candidates
    ] == [allowed_direct.id]


def test_direct_candidates_bulk_check_legacy_chunks_only_after_permission():
    denied_kb = _kb(name="Denied legacy KB", version_status=None)
    allowed_kb = _kb(name="Allowed legacy KB", version_status=None)

    class PerKbPermissionHelper(FakePermissionHelper):
        def _manual_kb_auth_state(self, kb):
            return AUTH_STATE_OPERATOR if kb.id == allowed_kb.id else "none"

    class LegacyLookupTrackingResolver(FakeResolver):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.bulk_legacy_lookups = []
            self.per_item_legacy_lookups = []

        def _legacy_retrieval_visible_kb_ids(self, kb_ids):
            requested_ids = set(kb_ids)
            self.bulk_legacy_lookups.append(requested_ids)
            return {allowed_kb.id} & requested_ids

        def _has_legacy_retrieval_visible_chunks(self, kb):
            self.per_item_legacy_lookups.append(kb.id)
            return kb.id == allowed_kb.id

    resolver = LegacyLookupTrackingResolver(
        helper=PerKbPermissionHelper(),
        kbs=[denied_kb, allowed_kb],
    )

    result = resolver.resolve_builder_hierarchy(
        max_candidate_kbs=2,
        apply_candidate_limit=False,
    )

    assert [
        candidate.candidate_id for candidate in result.ungrouped_candidates
    ] == [allowed_kb.id]
    assert resolver.bulk_legacy_lookups == [{allowed_kb.id}]
    assert resolver.per_item_legacy_lookups == []


def test_auto_collection_mode_buckets_missing_requested_collection():
    existing_collection = _collection()
    kb = _kb()
    helper = FakePermissionHelper(
        collection_actions={existing_collection.id: {"route"}},
    )
    resolver = FakeResolver(
        helper=helper,
        collections=[existing_collection],
        items=[
            SimpleNamespace(
                collection_id=existing_collection.id,
                knowledge_base_id=kb.id,
            )
        ],
        kbs=[kb],
    )

    result = resolver.resolve_auto_collection_candidates(
        collection_ids=[existing_collection.id, uuid.uuid4()]
    )

    assert [candidate.candidate_id for candidate in result.candidates] == [kb.id]
    assert result.hidden_candidate_count_bucket == "1"


def test_auto_collection_candidate_cap_is_deterministic():
    collection = _collection()
    kbs = [_kb() for _ in range(3)]
    helper = FakePermissionHelper(collection_actions={collection.id: {"route"}})
    resolver = FakeResolver(
        helper=helper,
        collections=[collection],
        items=[
            SimpleNamespace(
                collection_id=collection.id,
                knowledge_base_id=kb.id,
            )
            for kb in kbs
        ],
        kbs=kbs,
    )

    result = resolver.resolve_auto_collection_candidates(max_candidate_kbs=2)

    assert [candidate.candidate_id for candidate in result.candidates] == [
        kbs[0].id,
        kbs[1].id,
    ]
    assert result.hidden_candidate_count_bucket == "0"
    assert result.unavailable_candidate_count_bucket == "0"


def test_auto_collection_candidate_cap_applies_after_kb_authorization():
    collection = _collection()
    denied_kb = _kb()
    allowed_kb = _kb()

    class PerKbPermissionHelper(FakePermissionHelper):
        def _manual_kb_auth_state(self, kb):
            return AUTH_STATE_OPERATOR if kb.id == allowed_kb.id else "none"

    helper = PerKbPermissionHelper(collection_actions={collection.id: {"route"}})
    resolver = FakeResolver(
        helper=helper,
        collections=[collection],
        items=[
            SimpleNamespace(
                collection_id=collection.id,
                knowledge_base_id=denied_kb.id,
            ),
            SimpleNamespace(
                collection_id=collection.id,
                knowledge_base_id=allowed_kb.id,
            ),
        ],
        kbs=[denied_kb, allowed_kb],
    )

    result = resolver.resolve_auto_collection_candidates(max_candidate_kbs=1)

    assert [candidate.candidate_id for candidate in result.candidates] == [
        allowed_kb.id
    ]
    assert result.unavailable_candidate_count_bucket == "1"


def test_kb_use_source_acl_stale_fails_closed_after_kb_use_grant():
    kb = _kb(source_managed=True)
    helper = FakePermissionHelper(
        kb_auth_state=AUTH_STATE_OPERATOR,
        source_provenance=_source_provenance(
            source_acl_state="fresh",
            requester_source_authorization="allowed",
            expires_at=datetime(2026, 7, 3, tzinfo=timezone.utc),
        ),
    )

    decision = helper.evaluate_kb_use(kb)

    assert decision.allowed is False
    assert decision.source_acl_state == "stale"
    assert decision.requester_source_authorization == "allowed"
    assert decision.reason_code == "source_acl.stale"
    assert decision.external_reason_code == "resource.hidden"
    assert "knowledge_base_id" not in decision.safe_metadata


def test_requester_source_authorization_denied_is_separate_from_acl_state():
    kb = _kb(source_managed=True)
    helper = FakePermissionHelper(
        kb_auth_state=AUTH_STATE_OPERATOR,
        source_provenance=_source_provenance(
            source_acl_state="fresh",
            requester_source_authorization="denied",
            freshness_epoch=7,
        ),
    )

    decision = helper.evaluate_kb_use(kb)

    assert decision.allowed is False
    assert decision.source_acl_state == "fresh"
    assert decision.requester_source_authorization == "denied"
    assert decision.freshness_epoch == 7
    assert decision.reason_code == "source_authorization.denied"
    assert decision.external_reason_code == "resource.hidden"
    assert "knowledge_base_id" not in decision.safe_metadata


def test_source_policy_grant_does_not_bypass_source_acl_gate():
    kb = _kb(source_managed=True)
    helper = FakePermissionHelper(
        kb_auth_state="none",
        source_policy_auth_state=AUTH_STATE_OPERATOR,
        source_provenance=_source_provenance(
            source_acl_state="revoked",
            requester_source_authorization="allowed",
        ),
    )

    decision = helper.evaluate_kb_use(kb)

    assert decision.allowed is False
    assert decision.effective_auth_state == AUTH_STATE_OPERATOR
    assert decision.source_acl_state == "revoked"
    assert decision.external_reason_code == "resource.hidden"


def test_source_policy_grant_requires_active_organization_membership():
    kb = _kb()

    class NoMembershipHelper(KnowledgePermissionHelper):
        def __init__(self):
            super().__init__(None, user_id=USER_ID, organization_id=ORG_ID)

        def _organization_auth_state(self):
            return ORGANIZATION_AUTH_MEMBER

        def _manual_kb_auth_state(self, kb):
            return "none"

        def _can_consume_source_policy_grants(self):
            return False

    decision = NoMembershipHelper().evaluate_kb_use(kb)

    assert decision.allowed is False
    assert decision.reason_code == "kb_use_denied"
    assert decision.effective_auth_state == "none"


def test_source_policy_grant_applies_for_active_organization_member():
    kb = _kb()

    class ActiveMembershipHelper(KnowledgePermissionHelper):
        def __init__(self):
            super().__init__(None, user_id=USER_ID, organization_id=ORG_ID)

        def _organization_auth_state(self):
            return ORGANIZATION_AUTH_MEMBER

        def _manual_kb_auth_state(self, kb):
            return "none"

        def _can_consume_source_policy_grants(self):
            return True

        def _active_source_policy_grants(self, kb):
            return [object()]

    decision = ActiveMembershipHelper().evaluate_kb_use(kb)

    assert decision.allowed is True
    assert decision.effective_auth_state == AUTH_STATE_OPERATOR


def test_bucket_count_uses_safe_ranges():
    assert bucket_count(0) == "0"
    assert bucket_count(1) == "1"
    assert bucket_count(5) == "2-10"
    assert bucket_count(50) == "11-100"
    assert bucket_count(500) == "100+"


def test_candidate_resolution_request_caps_builder_fanout():
    request = KnowledgeCandidateResolveRequest(
        mode="auto_collection",
        max_collections=100,
        max_candidate_kbs=5000,
    )

    assert request.max_collections == 100
    assert request.max_candidate_kbs == 5000

    try:
        KnowledgeCandidateResolveRequest(
            mode="auto_collection",
            max_collections=101,
        )
    except ValueError as exc:
        assert "max_collections" in str(exc)
    else:  # pragma: no cover - pydantic must reject over-cap values
        raise AssertionError("max_collections cap was not enforced")


def test_builder_candidate_hides_unapproved_source_display_metadata():
    kb = _kb(source_managed=True)
    kb.source_identity = _source_identity()
    helper = FakePermissionHelper(
        kb_auth_state=AUTH_STATE_OPERATOR,
        source_provenance=_source_provenance(),
    )
    resolver = FakeResolver(helper=helper, kbs=[kb])

    result = resolver.resolve_explicit_kbs([kb.id])

    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.safe_label is None
    assert "raw_source_url" not in candidate.safe_metadata
    assert "raw_source_path" not in candidate.safe_metadata
    assert "raw_source_title" not in candidate.safe_metadata


def test_builder_candidate_uses_approved_safe_display_label_only():
    kb = _kb(source_managed=True)
    kb.source_identity = _source_identity()
    kb.source_identity.display_policy_state = "approved"
    helper = FakePermissionHelper(
        kb_auth_state=AUTH_STATE_OPERATOR,
        source_provenance=_source_provenance(),
    )
    resolver = FakeResolver(helper=helper, kbs=[kb])

    result = resolver.resolve_explicit_kbs([kb.id])

    assert len(result.candidates) == 1
    assert result.candidates[0].safe_label == "Safe approved label"


def test_builder_candidate_auto_generates_manual_kb_safe_label_and_topics():
    kb = _kb(
        name="사내문서1",
        description="사내 문서 온보딩 가이드",
    )
    helper = FakePermissionHelper(kb_auth_state=AUTH_STATE_OPERATOR)
    resolver = FakeResolver(helper=helper, kbs=[kb])

    result = resolver.resolve_explicit_kbs([kb.id])

    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.safe_label == "사내문서1"
    assert candidate.safe_metadata["kb_safe_topics"] == [
        "사내문서1",
        "사내",
        "문서",
        "온보딩",
        "가이드",
    ]
    assert candidate.safe_metadata["kb_safe_description"] == "사내 문서 온보딩 가이드"


def test_builder_candidate_sanitizes_manual_kb_safe_text():
    kb = _kb(
        name="인사 KB https://internal.example/private",
        description="api_key=sk-secret-token 사내 문서 C:\\secret\\policy.pdf",
    )
    helper = FakePermissionHelper(kb_auth_state=AUTH_STATE_OPERATOR)
    resolver = FakeResolver(helper=helper, kbs=[kb])

    result = resolver.resolve_explicit_kbs([kb.id])

    candidate = result.candidates[0]
    rendered = str(candidate.model_dump())
    assert candidate.safe_label == "인사 KB"
    assert "사내" in candidate.safe_metadata["kb_safe_topics"]
    assert "문서" in candidate.safe_metadata["kb_safe_topics"]
    assert "internal.example" not in rendered
    assert "sk-secret-token" not in rendered
    assert "C:\\secret" not in rendered


def test_builder_candidate_uses_persisted_manual_kb_safe_label_and_topics():
    kb = _kb(
        name="Fallback KB",
        description="fallback description",
        safe_metadata={
            "safe_label": "People Ops",
            "kb_safe_topics": ["onboarding", "benefits"],
            "raw_source_url": "https://internal.example/private",
        },
    )
    helper = FakePermissionHelper(kb_auth_state=AUTH_STATE_OPERATOR)
    resolver = FakeResolver(helper=helper, kbs=[kb])

    result = resolver.resolve_explicit_kbs([kb.id])

    candidate = result.candidates[0]
    assert candidate.safe_label == "People Ops"
    assert candidate.safe_metadata["kb_safe_topics"] == ["onboarding", "benefits"]
    rendered = str(candidate.model_dump())
    assert "raw_source_url" not in rendered
    assert "internal.example" not in rendered


def test_builder_candidate_runtime_availability_unknown_without_intended_subject():
    kb = _kb()
    actor_helper = FakePermissionHelper(kb_auth_state=AUTH_STATE_OPERATOR)
    resolver = FakeResolver(helper=actor_helper, kbs=[kb])

    result = resolver.resolve_explicit_kbs([kb.id])

    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.runtime_availability == "unknown"
    assert "runtime_reason_code" not in candidate.safe_metadata


def test_builder_candidate_marks_runtime_unavailable_for_intended_subject():
    kb = _kb()
    actor_helper = FakePermissionHelper(kb_auth_state=AUTH_STATE_OPERATOR)
    runtime_helper = FakePermissionHelper(kb_auth_state="none")
    resolver = FakeResolver(helper=actor_helper, runtime_helper=runtime_helper, kbs=[kb])

    result = resolver.resolve_explicit_kbs([kb.id])

    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.runtime_availability == "unavailable"
    assert candidate.safe_metadata["runtime_reason_code"] == "permission.denied"
    assert "knowledge_base_id" in candidate.permission.safe_metadata
    assert runtime_helper.bulk_kb_calls == [[kb.id]]


def test_bulk_kb_use_uses_prefetched_context_and_restores_it():
    kb = _kb(source_managed=True)
    provenance = _source_provenance(freshness_epoch=13)

    class BulkContextHelper(KnowledgePermissionHelper):
        def __init__(self):
            super().__init__(None, user_id=USER_ID, organization_id=ORG_ID)
            self.prepare_calls = 0

        def _organization_auth_state(self):
            return ORGANIZATION_AUTH_MEMBER

        def _prepare_bulk_kb_context(self, kbs):
            self.prepare_calls += 1
            self._bulk_manual_auth_state_by_kb_id = {
                item.id: AUTH_STATE_OPERATOR for item in kbs
            }
            self._bulk_source_policy_allowed_kb_ids = set()
            self._bulk_source_authorization_by_key = {
                (item.id, item.source_identity_id): provenance for item in kbs
            }

        def _now(self):
            return datetime(2026, 7, 4, tzinfo=timezone.utc)

    helper = BulkContextHelper()

    decisions = helper.bulk_evaluate_kb_use([kb])

    assert helper.prepare_calls == 1
    assert decisions[kb.id].allowed is True
    assert decisions[kb.id].source_acl_state == "fresh"
    assert decisions[kb.id].freshness_epoch == 13
    assert helper._bulk_manual_auth_state_by_kb_id is None
    assert helper._bulk_source_policy_allowed_kb_ids is None
    assert helper._bulk_source_authorization_by_key is None


def test_bulk_kb_use_honors_user_direct_permission_without_team_permission():
    kb = _kb()

    class BulkPermissionQuery:
        def __init__(self, db):
            self.db = db

        def join(self, *args, **kwargs):
            return self

        def filter(self, *args, **kwargs):
            return self

        def all(self):
            return self.db.all_values.pop(0)

    class BulkPermissionDb:
        def __init__(self):
            self.all_values = [
                [],  # team_knowledge_permissions
                [(kb.id, AUTH_STATE_OPERATOR)],  # user_knowledge_permissions
            ]

        def query(self, *args, **kwargs):
            return BulkPermissionQuery(self)

    class DirectPermissionHelper(KnowledgePermissionHelper):
        def __init__(self):
            super().__init__(
                BulkPermissionDb(),
                user_id=USER_ID,
                organization_id=ORG_ID,
            )

        def _organization_auth_state(self):
            return ORGANIZATION_AUTH_MEMBER

        def _bulk_source_policy_kb_ids(self, kbs):
            return set()

        def _bulk_latest_source_authorization_by_key(self, kbs):
            return {}

    helper = DirectPermissionHelper()

    decisions = helper.bulk_evaluate_kb_use([kb])

    assert decisions[kb.id].allowed is True
    assert decisions[kb.id].effective_auth_state == AUTH_STATE_OPERATOR
