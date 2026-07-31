from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from apps.shared.db.models.knowledge import KnowledgeCollection
from apps.shared.db.models.organization_membership import ORGANIZATION_AUTH_MEMBER
from apps.shared.permissions import (
    AUTH_STATE_MANAGER,
    AUTH_STATE_NONE,
    AUTH_STATE_OPERATOR,
)
from apps.shared.services.knowledge_permission_service import KnowledgePermissionHelper
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

ORG_ID = UUID(int=100)
USER_ID = UUID(int=101)


def _collection(value: int):
    return SimpleNamespace(
        id=UUID(int=value),
        organization_id=ORG_ID,
        lifecycle_state="active",
        sync_state="manual",
        is_system_managed=False,
        safe_metadata={},
    )


def _source_kb():
    return SimpleNamespace(
        id=UUID(int=300),
        organization_id=ORG_ID,
        lifecycle_state="active",
        sync_state="synced",
        source_identity_id=UUID(int=301),
    )


def _provenance(*, action):
    return SimpleNamespace(
        source_acl_state="fresh",
        requester_source_authorization="allowed",
        freshness_epoch=7,
        freshness_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        source_permission_action=action,
    )


class _BulkCollectionHelper(KnowledgePermissionHelper):
    def __init__(self, *, auth_state=ORGANIZATION_AUTH_MEMBER):
        super().__init__(object(), user_id=USER_ID, organization_id=ORG_ID)
        self.auth_state = auth_state
        self.bulk_calls = []
        self.single_calls = []

    def _organization_auth_state(self):
        return self.auth_state

    def _bulk_collection_action_ids(self, collection_ids, action):
        self.bulk_calls.append((tuple(collection_ids), action))
        return {collection_ids[0]}

    def _collection_has_action(self, collection_id, action):
        self.single_calls.append((collection_id, action))
        raise AssertionError("bulk evaluation must not call the single-row path")


class _CollectionScopeHelper(KnowledgePermissionHelper):
    def __init__(self, *, auth_state):
        super().__init__(Session(), user_id=USER_ID, organization_id=ORG_ID)
        self.auth_state = auth_state

    def _organization_auth_state(self):
        return self.auth_state


def _collection_scope_sql(helper, action="route"):
    query = helper.db.query(KnowledgeCollection)
    scoped = helper.scope_collection_query_for_action(query, action)
    return str(
        scoped.statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def test_bulk_collection_route_uses_one_bulk_permission_projection():
    collections = [_collection(1), _collection(2)]
    helper = _BulkCollectionHelper()

    decisions = helper.bulk_evaluate_collection_action(collections, "route")

    assert decisions[collections[0].id].allowed is True
    assert decisions[collections[1].id].allowed is False
    assert decisions[collections[1].id].external_reason_code == "permission.denied"
    assert helper.bulk_calls == [((collections[0].id, collections[1].id), "route")]
    assert helper.single_calls == []


def test_bulk_collection_manager_override_skips_permission_rows():
    collections = [_collection(1), _collection(2)]
    helper = _BulkCollectionHelper(auth_state=AUTH_STATE_MANAGER)

    decisions = helper.bulk_evaluate_collection_action(collections, "route")

    assert all(decision.allowed for decision in decisions.values())
    assert helper.bulk_calls == []
    assert helper.single_calls == []


def test_archived_collection_is_hidden_by_default_but_available_to_admin_path():
    collection = _collection(1)
    collection.lifecycle_state = "archived"
    helper = _BulkCollectionHelper(auth_state=AUTH_STATE_MANAGER)

    runtime_decision = helper.evaluate_collection_action(collection, "read")
    administration_decision = helper.evaluate_collection_action(
        collection,
        "read",
        include_archived=True,
    )

    assert runtime_decision.allowed is False
    assert runtime_decision.external_reason_code == "resource.hidden"
    assert administration_decision.allowed is True


def test_bulk_archived_collection_requires_explicit_administration_scope():
    collection = _collection(1)
    collection.lifecycle_state = "archived"
    helper = _BulkCollectionHelper()

    runtime_decision = helper.bulk_evaluate_collection_action(
        [collection],
        "manage",
    )[collection.id]
    administration_decision = helper.bulk_evaluate_collection_action(
        [collection],
        "manage",
        include_archived=True,
    )[collection.id]

    assert runtime_decision.allowed is False
    assert administration_decision.allowed is True
    assert helper.bulk_calls == [((collection.id,), "manage")]


def test_bulk_collection_invalid_action_is_fixed_safe_denial():
    collection = _collection(1)
    helper = _BulkCollectionHelper()

    decision = helper.bulk_evaluate_collection_action([collection], "content")[
        collection.id
    ]

    assert decision.allowed is False
    assert decision.reason_code == "permission.invalid_action"
    assert decision.external_reason_code == "resource.hidden"
    assert helper.bulk_calls == []


def test_collection_query_scope_manager_does_not_require_permission_rows():
    sql = _collection_scope_sql(
        _CollectionScopeHelper(auth_state=AUTH_STATE_MANAGER)
    ).lower()

    assert "user_knowledge_collection_permissions" not in sql
    assert "team_knowledge_collection_permissions" not in sql


def test_collection_query_scope_member_accepts_direct_or_active_team_route():
    sql = _collection_scope_sql(
        _CollectionScopeHelper(auth_state=ORGANIZATION_AUTH_MEMBER)
    ).lower()

    assert "user_knowledge_collection_permissions" in sql
    assert "team_knowledge_collection_permissions" in sql
    assert "team_memberships" in sql
    assert "teams" in sql
    assert "permission_action = 'route'" in sql
    assert str(USER_ID) in sql
    assert str(ORG_ID) in sql
    assert "teams.is_active is true" in sql


def test_collection_query_scope_non_member_fails_closed():
    sql = _collection_scope_sql(
        _CollectionScopeHelper(auth_state=AUTH_STATE_NONE)
    ).lower()

    assert "where false" in sql
    assert "user_knowledge_collection_permissions" not in sql


def test_collection_query_scope_invalid_action_fails_closed_before_auth_lookup():
    helper = _CollectionScopeHelper(auth_state=AUTH_STATE_MANAGER)
    helper._organization_auth_state = lambda: (_ for _ in ()).throw(
        AssertionError("invalid action must not evaluate organization authorization")
    )

    sql = _collection_scope_sql(helper, action="content").lower()

    assert "where false" in sql


class _SourceActionHelper(KnowledgePermissionHelper):
    def __init__(self, provenance, *, evaluation_time=None):
        super().__init__(
            None,
            user_id=USER_ID,
            organization_id=ORG_ID,
            evaluation_time=evaluation_time,
        )
        self.provenance = provenance

    def _effective_kb_use_auth_state(self, kb):
        del kb
        return AUTH_STATE_OPERATOR

    def _latest_source_authorization(self, kb):
        del kb
        return self.provenance


@pytest.mark.parametrize(
    "action",
    ["read", "view", "use", "retrieve", "search", " READ ", "Search"],
)
def test_materialized_source_retrieval_action_is_compatible(action):
    decision = _SourceActionHelper(_provenance(action=action)).evaluate_kb_use(
        _source_kb()
    )

    assert decision.allowed is True
    assert decision.freshness_epoch == 7


@pytest.mark.parametrize(
    "action",
    [None, "", "write", "admin", "delete", "unknown", 42],
)
def test_materialized_source_non_retrieval_action_fails_closed(action):
    decision = _SourceActionHelper(_provenance(action=action)).evaluate_kb_use(
        _source_kb()
    )

    assert decision.allowed is False
    assert decision.reason_code == "source_authorization.operation_unverified"
    assert decision.external_reason_code == "resource.hidden"


def test_source_expiry_uses_one_injected_invocation_time():
    evaluation_time = datetime(2030, 1, 1, tzinfo=timezone.utc)
    provenance = _provenance(action="read")
    provenance.freshness_expires_at = evaluation_time + timedelta(seconds=1)
    helper = _SourceActionHelper(
        provenance,
        evaluation_time=evaluation_time,
    )

    decision = helper.evaluate_kb_use(_source_kb())

    assert decision.allowed is True
    assert helper._now() == evaluation_time


def test_permission_evaluation_time_requires_timezone():
    with pytest.raises(
        ValueError,
        match="knowledge_permission_evaluation_time_invalid",
    ):
        KnowledgePermissionHelper(
            None,
            user_id=USER_ID,
            organization_id=ORG_ID,
            evaluation_time=datetime(2030, 1, 1),
        )
