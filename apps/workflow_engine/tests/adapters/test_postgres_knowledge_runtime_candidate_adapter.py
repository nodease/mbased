from contextlib import AbstractContextManager
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql

from apps.shared.domain.knowledge_runtime_candidates import (
    AnonymousPublicAudience,
    AuthenticatedAudience,
    KnowledgeRuntimeCandidateRequest,
)
from apps.workflow_engine.adapters.knowledge_runtime_candidates import (
    KnowledgeRuntimeCandidateSnapshotError,
    PostgresKnowledgeRuntimeCandidateSnapshotAdapter,
)


ORG_ID = UUID(int=100)
USER_ID = UUID(int=101)
COLLECTION_A = UUID(int=200)
COLLECTION_B = UUID(int=201)
KB_1 = UUID(int=300)
KB_2 = UUID(int=301)
KB_3 = UUID(int=302)
KB_4 = UUID(int=303)
KB_5 = UUID(int=304)


def _collection(
    collection_id,
    *,
    visibility="private",
    organization_id=ORG_ID,
    sync_state="manual",
    source_managed=False,
):
    return SimpleNamespace(
        id=collection_id,
        organization_id=organization_id,
        lifecycle_state="active",
        sync_state=sync_state,
        source_identity_id=UUID(int=901) if source_managed else None,
        safe_metadata={"visibility": visibility},
    )


def _kb(
    knowledge_base_id,
    *,
    source_managed=False,
    organization_id=ORG_ID,
    lifecycle_state="active",
    sync_state="manual",
):
    return SimpleNamespace(
        id=knowledge_base_id,
        organization_id=organization_id,
        lifecycle_state=lifecycle_state,
        sync_state=sync_state,
        source_identity_id=UUID(int=900) if source_managed else None,
    )


class _NoAutoflush(AbstractContextManager):
    def __init__(self, session):
        self._session = session

    def __enter__(self):
        self._session.events.append("no_autoflush.enter")
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        del exc_type, exc_value, traceback
        self._session.events.append("no_autoflush.exit")
        return False


class _FakeSession:
    def __init__(
        self,
        *,
        dialect="postgresql",
        in_transaction=False,
        connection_error=False,
        rollback_error=False,
        close_error=False,
    ):
        self.events = []
        self._in_transaction = in_transaction
        self._dialect = dialect
        self._connection_error = connection_error
        self._rollback_error = rollback_error
        self._close_error = close_error
        self.connection_options = None

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name=self._dialect))

    def in_transaction(self):
        return self._in_transaction

    def connection(self, *, execution_options):
        self.events.append("connection")
        if self._connection_error:
            raise RuntimeError("internal-db-setup-detail")
        self.connection_options = execution_options
        self._in_transaction = True
        return object()

    @property
    def no_autoflush(self):
        return _NoAutoflush(self)

    def rollback(self):
        self.events.append("rollback")
        if self._rollback_error:
            raise RuntimeError("internal-db-rollback-detail")
        self._in_transaction = False

    def close(self):
        self.events.append("close")
        if self._close_error:
            raise RuntimeError("internal-db-close-detail")


class _CaptureResult:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def all(self):
        return self._rows

    def scalars(self):
        return self

    def scalar_one(self):
        if len(self._rows) != 1:
            raise AssertionError("expected exactly one captured scalar")
        return self._rows[0]


class _CaptureSession:
    def __init__(self, rows=()):
        self.rows = rows
        self.statement = None

    def execute(self, statement):
        self.statement = statement
        return _CaptureResult(self.rows)


class _PermissionHelper:
    def __init__(self, *, routed=(), usable=()):
        self.routed = set(routed)
        self.usable = set(usable)
        self.collection_calls = []
        self.kb_calls = []

    def bulk_evaluate_collection_action(self, collections, action):
        collection_list = list(collections)
        self.collection_calls.append(
            (tuple(collection.id for collection in collection_list), action)
        )
        return {
            collection.id: SimpleNamespace(allowed=collection.id in self.routed)
            for collection in collection_list
        }

    def bulk_evaluate_kb_use(self, kbs):
        kb_list = list(kbs)
        self.kb_calls.append(tuple(kb.id for kb in kb_list))
        return {
            kb.id: SimpleNamespace(allowed=kb.id in self.usable) for kb in kb_list
        }


class _FixtureAdapter(PostgresKnowledgeRuntimeCandidateSnapshotAdapter):
    def __init__(
        self,
        session,
        *,
        organization_active=True,
        collections=(),
        memberships=(),
        scan_limited=False,
        kbs=(),
        ready=(),
        legacy_ready=(),
        public_direct=(),
        permission_helper=None,
    ):
        super().__init__(session_factory=lambda: session)
        self.organization_active = organization_active
        self.collections = {collection.id: collection for collection in collections}
        self.memberships = tuple(memberships)
        self.fixture_scan_limited = scan_limited
        self.kbs = {kb.id: kb for kb in kbs}
        self.ready = set(ready)
        self.legacy_ready = set(legacy_ready)
        self.public_direct = set(public_direct)
        self.permission_helper = permission_helper
        self.evaluation_time = datetime(2030, 1, 2, tzinfo=timezone.utc)
        self.read_events = []

    def _organization_is_active(self, db, organization_id):
        del db
        self.read_events.append(("organization", organization_id))
        return self.organization_active

    def _load_selected_collections(self, db, organization_id, collection_ids):
        del db
        self.read_events.append(("collections", tuple(collection_ids)))
        return {
            collection_id: self.collections[collection_id]
            for collection_id in collection_ids
            if collection_id in self.collections
            and self.collections[collection_id].organization_id == organization_id
        }

    def _load_fair_collection_memberships(
        self,
        db,
        organization_id,
        collection_ids,
        scan_cap,
    ):
        del db, organization_id
        self.read_events.append(
            ("memberships", tuple(collection_ids), scan_cap)
        )
        collection_id_set = set(collection_ids)
        return (
            tuple(
                membership
                for membership in self.memberships
                if membership[0] in collection_id_set
            ),
            self.fixture_scan_limited,
        )

    def _load_candidate_kbs(self, db, organization_id, knowledge_base_ids):
        del db
        self.read_events.append(("kbs", tuple(knowledge_base_ids)))
        return {
            knowledge_base_id: self.kbs[knowledge_base_id]
            for knowledge_base_id in knowledge_base_ids
            if knowledge_base_id in self.kbs
            and self.kbs[knowledge_base_id].organization_id == organization_id
        }

    def _load_ready_kb_ids(self, db, organization_id, knowledge_base_ids):
        del db, organization_id
        self.read_events.append(("ready", tuple(knowledge_base_ids)))
        return set(knowledge_base_ids) & self.ready

    def _load_legacy_ready_kb_ids(self, db, knowledge_base_ids):
        del db
        self.read_events.append(("legacy", tuple(knowledge_base_ids)))
        return set(knowledge_base_ids) & self.legacy_ready

    def _load_public_direct_kb_ids(
        self,
        db,
        organization_id,
        knowledge_base_ids,
    ):
        del db, organization_id
        self.read_events.append(("public_direct", tuple(knowledge_base_ids)))
        return set(knowledge_base_ids) & self.public_direct

    def _load_policy_evaluation_time(self, db):
        del db
        self.read_events.append(("evaluation_time", self.evaluation_time))
        return self.evaluation_time

    def _build_permission_helper(self, db, audience, *, evaluation_time):
        del db, audience
        self.read_events.append(("permission_helper", evaluation_time))
        if self.permission_helper is None:
            raise AssertionError("anonymous resolution must not construct RBAC helper")
        return self.permission_helper


def test_snapshot_applies_postgres_repeatable_read_only_before_first_read():
    session = _FakeSession()
    adapter = _FixtureAdapter(session, organization_active=False)
    request = KnowledgeRuntimeCandidateRequest(
        audience=AnonymousPublicAudience(organization_id=ORG_ID),
        direct_kb_ids=(KB_1,),
        collection_ids=(COLLECTION_A,),
    )

    snapshot = adapter.load_snapshot(request)

    assert snapshot.eligible_direct_kb_ids == ()
    assert snapshot.policy_excluded_count == 2
    assert session.connection_options == {
        "isolation_level": "REPEATABLE READ",
        "postgresql_readonly": True,
    }
    assert session.events == [
        "connection",
        "no_autoflush.enter",
        "no_autoflush.exit",
        "rollback",
        "close",
    ]
    assert adapter.read_events == [("organization", ORG_ID)]


def test_snapshot_rejects_non_postgres_before_any_read_and_closes_session():
    session = _FakeSession(dialect="sqlite")
    adapter = _FixtureAdapter(session)
    request = KnowledgeRuntimeCandidateRequest(
        audience=AnonymousPublicAudience(organization_id=ORG_ID),
    )

    with pytest.raises(RuntimeError, match="snapshot_database_unsupported"):
        adapter.load_snapshot(request)

    assert adapter.read_events == []
    assert session.events == ["rollback", "close"]


def test_snapshot_rejects_reused_transaction_before_any_read():
    session = _FakeSession(in_transaction=True)
    adapter = _FixtureAdapter(session)
    request = KnowledgeRuntimeCandidateRequest(
        audience=AnonymousPublicAudience(organization_id=ORG_ID),
    )

    with pytest.raises(RuntimeError, match="snapshot_session_not_fresh"):
        adapter.load_snapshot(request)

    assert adapter.read_events == []
    assert "connection" not in session.events
    assert session.events[-2:] == ["rollback", "close"]


def test_snapshot_transaction_setup_failure_is_fixed_and_raw_free():
    session = _FakeSession(connection_error=True)
    adapter = _FixtureAdapter(session)
    request = KnowledgeRuntimeCandidateRequest(
        audience=AnonymousPublicAudience(organization_id=ORG_ID),
    )

    with pytest.raises(RuntimeError, match="snapshot_transaction_setup_failed") as exc_info:
        adapter.load_snapshot(request)

    assert "internal-db-setup-detail" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None
    assert adapter.read_events == []
    assert session.events == ["connection", "rollback", "close"]


class _FailingReadAdapter(_FixtureAdapter):
    def _organization_is_active(self, db, organization_id):
        del db, organization_id
        raise RuntimeError("internal-db-read-detail")


def test_snapshot_read_failure_discards_partial_state_and_is_raw_free():
    session = _FakeSession()
    adapter = _FailingReadAdapter(session)
    request = KnowledgeRuntimeCandidateRequest(
        audience=AnonymousPublicAudience(organization_id=ORG_ID),
    )

    with pytest.raises(RuntimeError, match="snapshot_read_failed") as exc_info:
        adapter.load_snapshot(request)

    assert "internal-db-read-detail" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None
    assert session.events[-2:] == ["rollback", "close"]


@pytest.mark.parametrize("failure", ["rollback", "close"])
def test_snapshot_cleanup_failure_cannot_return_precleanup_result(failure):
    session = _FakeSession(
        rollback_error=failure == "rollback",
        close_error=failure == "close",
    )
    adapter = _FixtureAdapter(session, organization_active=False)
    request = KnowledgeRuntimeCandidateRequest(
        audience=AnonymousPublicAudience(organization_id=ORG_ID),
    )

    with pytest.raises(RuntimeError, match="snapshot_cleanup_failed") as exc_info:
        adapter.load_snapshot(request)

    assert "internal-db" not in str(exc_info.value)
    assert session.events[-2:] == ["rollback", "close"]


def test_authenticated_snapshot_combines_route_use_and_bulk_readiness():
    session = _FakeSession()
    helper = _PermissionHelper(
        routed={COLLECTION_A},
        usable={KB_1, KB_3},
    )
    adapter = _FixtureAdapter(
        session,
        collections=[
            _collection(COLLECTION_A),
            _collection(COLLECTION_B),
        ],
        memberships=[
            (COLLECTION_A, KB_3),
            (COLLECTION_A, KB_4),
            (COLLECTION_B, KB_5),
        ],
        kbs=[_kb(KB_1), _kb(KB_2), _kb(KB_3), _kb(KB_4), _kb(KB_5)],
        ready={KB_1, KB_3},
        legacy_ready={KB_4},
        permission_helper=helper,
    )
    request = KnowledgeRuntimeCandidateRequest(
        audience=AuthenticatedAudience(
            organization_id=ORG_ID,
            user_id=USER_ID,
        ),
        direct_kb_ids=(KB_1, KB_2),
        collection_ids=(COLLECTION_A, COLLECTION_B),
    )

    snapshot = adapter.load_snapshot(request)

    assert snapshot.eligible_direct_kb_ids == (KB_1,)
    assert [stream.collection_id for stream in snapshot.collection_streams] == [
        COLLECTION_A
    ]
    assert snapshot.collection_streams[0].eligible_kb_ids == (KB_3,)
    assert snapshot.policy_excluded_count == 3
    assert helper.collection_calls == [
        ((COLLECTION_A, COLLECTION_B), "route")
    ]
    assert helper.kb_calls == [(KB_1, KB_3, KB_4)]
    assert ("evaluation_time", adapter.evaluation_time) in adapter.read_events
    assert ("permission_helper", adapter.evaluation_time) in adapter.read_events
    assert ("memberships", (COLLECTION_A,), 5000) in adapter.read_events
    assert ("legacy", (KB_2, KB_4)) in adapter.read_events


def test_authenticated_direct_kb_never_requires_collection_route():
    session = _FakeSession()
    helper = _PermissionHelper(usable={KB_1})
    adapter = _FixtureAdapter(
        session,
        kbs=[_kb(KB_1)],
        ready={KB_1},
        permission_helper=helper,
    )
    request = KnowledgeRuntimeCandidateRequest(
        audience=AuthenticatedAudience(
            organization_id=ORG_ID,
            user_id=USER_ID,
        ),
        direct_kb_ids=(KB_1,),
    )

    snapshot = adapter.load_snapshot(request)

    assert snapshot.eligible_direct_kb_ids == (KB_1,)
    assert helper.collection_calls == []
    assert not any(event[0] == "memberships" for event in adapter.read_events)


def test_anonymous_snapshot_uses_public_membership_without_rbac_helper():
    session = _FakeSession()
    adapter = _FixtureAdapter(
        session,
        collections=[
            _collection(COLLECTION_A, visibility="public"),
            _collection(COLLECTION_B, visibility="private"),
        ],
        memberships=[
            (COLLECTION_A, KB_3),
            (COLLECTION_A, KB_4),
            (COLLECTION_B, KB_5),
        ],
        kbs=[
            _kb(KB_1),
            _kb(KB_2),
            _kb(KB_3),
            _kb(KB_4, source_managed=True),
            _kb(KB_5, source_managed=True),
        ],
        ready={KB_1, KB_2, KB_3, KB_4, KB_5},
        public_direct={KB_1},
    )
    request = KnowledgeRuntimeCandidateRequest(
        audience=AnonymousPublicAudience(organization_id=ORG_ID),
        direct_kb_ids=(KB_1, KB_2, KB_5),
        collection_ids=(COLLECTION_A, COLLECTION_B),
    )

    snapshot = adapter.load_snapshot(request)

    assert snapshot.eligible_direct_kb_ids == (KB_1,)
    assert len(snapshot.collection_streams) == 1
    assert snapshot.collection_streams[0].collection_id == COLLECTION_A
    assert snapshot.collection_streams[0].eligible_kb_ids == (KB_3,)
    assert snapshot.policy_excluded_count == 4
    assert ("public_direct", (KB_1, KB_2)) in adapter.read_events


@pytest.mark.parametrize(
    "metadata",
    [None, {}, {"visibility": "private"}, {"visibility": "PUBLIC"}, "public"],
)
def test_anonymous_collection_visibility_is_exact_and_fail_closed(metadata):
    session = _FakeSession()
    collection = _collection(COLLECTION_A)
    collection.safe_metadata = metadata
    adapter = _FixtureAdapter(
        session,
        collections=[collection],
        memberships=[(COLLECTION_A, KB_1)],
        kbs=[_kb(KB_1)],
        ready={KB_1},
    )
    request = KnowledgeRuntimeCandidateRequest(
        audience=AnonymousPublicAudience(organization_id=ORG_ID),
        collection_ids=(COLLECTION_A,),
    )

    snapshot = adapter.load_snapshot(request)

    assert snapshot.collection_streams == ()
    assert snapshot.policy_excluded_count == 1
    assert not any(event[0] == "memberships" for event in adapter.read_events)


@pytest.mark.parametrize("source_identity_state", ["managed", "missing"])
def test_anonymous_source_managed_collection_is_excluded_before_membership_scan(
    source_identity_state,
):
    session = _FakeSession()
    collection = _collection(
        COLLECTION_A,
        visibility="public",
        source_managed=True,
    )
    if source_identity_state == "missing":
        del collection.source_identity_id
    adapter = _FixtureAdapter(
        session,
        collections=[collection],
        memberships=[(COLLECTION_A, KB_1)],
        kbs=[_kb(KB_1)],
        ready={KB_1},
    )
    request = KnowledgeRuntimeCandidateRequest(
        audience=AnonymousPublicAudience(organization_id=ORG_ID),
        collection_ids=(COLLECTION_A,),
    )

    snapshot = adapter.load_snapshot(request)

    assert snapshot.collection_streams == ()
    assert snapshot.policy_excluded_count == 1
    assert not any(event[0] == "memberships" for event in adapter.read_events)


def test_authenticated_source_managed_collection_keeps_route_and_kb_use_policy():
    session = _FakeSession()
    helper = _PermissionHelper(routed={COLLECTION_A}, usable={KB_1})
    adapter = _FixtureAdapter(
        session,
        collections=[
            _collection(
                COLLECTION_A,
                visibility="public",
                source_managed=True,
            )
        ],
        memberships=[(COLLECTION_A, KB_1)],
        kbs=[_kb(KB_1)],
        ready={KB_1},
        permission_helper=helper,
    )
    request = KnowledgeRuntimeCandidateRequest(
        audience=AuthenticatedAudience(
            organization_id=ORG_ID,
            user_id=USER_ID,
        ),
        collection_ids=(COLLECTION_A,),
    )

    snapshot = adapter.load_snapshot(request)

    assert snapshot.collection_streams[0].collection_id == COLLECTION_A
    assert snapshot.collection_streams[0].eligible_kb_ids == (KB_1,)
    assert helper.collection_calls == [((COLLECTION_A,), "route")]
    assert helper.kb_calls == [(KB_1,)]


def test_snapshot_excludes_cross_org_archived_and_source_deleted_facts():
    session = _FakeSession()
    helper = _PermissionHelper(
        routed={COLLECTION_A, COLLECTION_B},
        usable={KB_1, KB_2, KB_3},
    )
    archived = _collection(COLLECTION_A)
    archived.lifecycle_state = "archived"
    adapter = _FixtureAdapter(
        session,
        collections=[archived, _collection(COLLECTION_B, organization_id=UUID(int=999))],
        kbs=[
            _kb(KB_1, lifecycle_state="archived"),
            _kb(KB_2, sync_state="source_deleted"),
            _kb(KB_3, organization_id=UUID(int=999)),
        ],
        ready={KB_1, KB_2, KB_3},
        permission_helper=helper,
    )
    request = KnowledgeRuntimeCandidateRequest(
        audience=AuthenticatedAudience(ORG_ID, USER_ID),
        direct_kb_ids=(KB_1, KB_2, KB_3),
        collection_ids=(COLLECTION_A, COLLECTION_B),
    )

    snapshot = adapter.load_snapshot(request)

    assert snapshot.eligible_direct_kb_ids == ()
    assert snapshot.collection_streams == ()
    assert snapshot.policy_excluded_count == 5
    assert helper.collection_calls == []
    assert helper.kb_calls == []


def test_snapshot_excludes_source_deleted_collection_before_permission_or_membership():
    session = _FakeSession()
    helper = _PermissionHelper(routed={COLLECTION_A}, usable={KB_1})
    adapter = _FixtureAdapter(
        session,
        collections=[_collection(COLLECTION_A, sync_state="source_deleted")],
        memberships=[(COLLECTION_A, KB_1)],
        kbs=[_kb(KB_1)],
        ready={KB_1},
        permission_helper=helper,
    )
    request = KnowledgeRuntimeCandidateRequest(
        audience=AuthenticatedAudience(ORG_ID, USER_ID),
        collection_ids=(COLLECTION_A,),
    )

    snapshot = adapter.load_snapshot(request)

    assert snapshot.collection_streams == ()
    assert snapshot.policy_excluded_count == 1
    assert helper.collection_calls == []
    assert not any(event[0] == "memberships" for event in adapter.read_events)


def test_scan_limit_is_preserved_as_safe_snapshot_signal():
    session = _FakeSession()
    helper = _PermissionHelper(routed={COLLECTION_A}, usable={KB_1})
    adapter = _FixtureAdapter(
        session,
        collections=[_collection(COLLECTION_A)],
        memberships=[(COLLECTION_A, KB_1)],
        scan_limited=True,
        kbs=[_kb(KB_1)],
        ready={KB_1},
        permission_helper=helper,
    )
    request = KnowledgeRuntimeCandidateRequest(
        audience=AuthenticatedAudience(ORG_ID, USER_ID),
        collection_ids=(COLLECTION_A,),
        candidate_scan_cap=20,
    )

    snapshot = adapter.load_snapshot(request)

    assert snapshot.scan_limited is True
    assert ("memberships", (COLLECTION_A,), 20) in adapter.read_events


def test_malformed_membership_is_fail_closed_without_identifier_projection():
    session = _FakeSession()
    helper = _PermissionHelper(routed={COLLECTION_A}, usable={KB_1})
    adapter = _FixtureAdapter(
        session,
        collections=[_collection(COLLECTION_A)],
        memberships=[
            (COLLECTION_A, KB_1),
            (COLLECTION_A, "not-a-uuid"),
            (COLLECTION_B, KB_2),
        ],
        kbs=[_kb(KB_1), _kb(KB_2)],
        ready={KB_1, KB_2},
        permission_helper=helper,
    )
    request = KnowledgeRuntimeCandidateRequest(
        audience=AuthenticatedAudience(ORG_ID, USER_ID),
        collection_ids=(COLLECTION_A,),
    )

    snapshot = adapter.load_snapshot(request)

    assert snapshot.collection_streams[0].eligible_kb_ids == (KB_1,)
    assert snapshot.policy_excluded_count == 1


def test_fair_membership_query_is_windowed_ordered_and_bounded():
    rows = [
        SimpleNamespace(collection_id=COLLECTION_A, knowledge_base_id=KB_1),
        SimpleNamespace(collection_id=COLLECTION_B, knowledge_base_id=KB_2),
        SimpleNamespace(collection_id=COLLECTION_A, knowledge_base_id=KB_3),
        SimpleNamespace(collection_id=COLLECTION_B, knowledge_base_id=KB_4),
    ]
    db = _CaptureSession(rows)
    adapter = PostgresKnowledgeRuntimeCandidateSnapshotAdapter(
        session_factory=lambda: None
    )

    facts, scan_limited = adapter._load_fair_collection_memberships(
        db,
        ORG_ID,
        (COLLECTION_A, COLLECTION_B),
        3,
    )

    assert facts == (
        (COLLECTION_A, KB_1),
        (COLLECTION_B, KB_2),
        (COLLECTION_A, KB_3),
    )
    assert scan_limited is True
    sql = str(db.statement.compile(dialect=postgresql.dialect())).lower()
    assert "join lateral" in sql
    assert "values" in sql
    assert "bounded_memberships" in sql
    assert "row_number() over (partition by bounded_memberships.collection_id" in sql
    assert sql.count("limit") >= 2
    assert sql.index("limit") < sql.index("row_number() over")


def test_legacy_readiness_query_matches_retrieval_visible_null_pointer_rule():
    db = _CaptureSession()
    adapter = PostgresKnowledgeRuntimeCandidateSnapshotAdapter(
        session_factory=lambda: None
    )

    assert adapter._load_legacy_ready_kb_ids(db, (KB_1,)) == set()

    sql = str(db.statement.compile(dialect=postgresql.dialect())).lower()
    assert "knowledge_bases.active_document_version_id is null" in sql
    assert "document_chunks.document_version_id is null" in sql
    assert "documents.status" in sql


def test_policy_evaluation_time_uses_one_timezone_aware_transaction_timestamp():
    evaluation_time = datetime(2030, 1, 2, tzinfo=timezone.utc)
    db = _CaptureSession([evaluation_time])
    adapter = PostgresKnowledgeRuntimeCandidateSnapshotAdapter(
        session_factory=lambda: None
    )

    loaded = adapter._load_policy_evaluation_time(db)

    assert loaded == evaluation_time
    sql = str(db.statement.compile(dialect=postgresql.dialect())).lower()
    assert "transaction_timestamp()" in sql


def test_policy_evaluation_time_rejects_naive_database_value():
    db = _CaptureSession([datetime(2030, 1, 2)])
    adapter = PostgresKnowledgeRuntimeCandidateSnapshotAdapter(
        session_factory=lambda: None
    )

    with pytest.raises(
        KnowledgeRuntimeCandidateSnapshotError,
        match="snapshot_evaluation_time_invalid",
    ):
        adapter._load_policy_evaluation_time(db)


def test_snapshot_queries_do_not_select_raw_collection_or_kb_text_fields():
    db = _CaptureSession()
    adapter = PostgresKnowledgeRuntimeCandidateSnapshotAdapter(
        session_factory=lambda: None
    )

    adapter._load_selected_collections(db, ORG_ID, (COLLECTION_A,))
    collection_sql = str(
        db.statement.compile(dialect=postgresql.dialect())
    ).lower()
    assert "knowledge_collections.name" not in collection_sql
    assert "knowledge_collections.description" not in collection_sql
    assert "source_connector_ref" not in collection_sql
    assert "knowledge_collections.source_identity_id" in collection_sql
    assert "knowledge_collections.lifecycle_state =" in collection_sql
    assert "knowledge_collections.sync_state !=" in collection_sql

    adapter._load_candidate_kbs(db, ORG_ID, (KB_1,))
    kb_sql = str(db.statement.compile(dialect=postgresql.dialect())).lower()
    assert "knowledge_bases.name" not in kb_sql
    assert "knowledge_bases.description" not in kb_sql
    assert "knowledge_bases.safe_metadata" not in kb_sql

    adapter._load_public_direct_kb_ids(db, ORG_ID, (KB_1,))
    public_direct_sql = str(
        db.statement.compile(dialect=postgresql.dialect())
    ).lower()
    assert "knowledge_collections.lifecycle_state =" in public_direct_sql
    assert "knowledge_collections.sync_state !=" in public_direct_sql
    assert "knowledge_collections.source_identity_id is null" in public_direct_sql


def test_session_factory_failure_does_not_expose_partial_snapshot():
    def fail_session_factory():
        raise RuntimeError("synthetic-sensitive-connection-detail")

    adapter = PostgresKnowledgeRuntimeCandidateSnapshotAdapter(
        session_factory=fail_session_factory
    )
    request = KnowledgeRuntimeCandidateRequest(
        audience=AnonymousPublicAudience(organization_id=ORG_ID),
    )

    with pytest.raises(RuntimeError, match="snapshot_session_unavailable") as exc_info:
        adapter.load_snapshot(request)

    assert "synthetic-sensitive-connection-detail" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None
