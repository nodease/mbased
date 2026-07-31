import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import apps.gateway.services.knowledge_collection_service as knowledge_collection_service_module
from apps.gateway.services.knowledge_collection_service import (
    KnowledgeCollectionService,
    KnowledgeCollectionServiceError,
)
from apps.shared.db.models.audit_log import AuditLog
from apps.shared.db.models.knowledge import KnowledgeCollection
from apps.shared.schemas.knowledge import (
    KnowledgeCollectionCreateRequest,
    KnowledgeCollectionItemLinkRequest,
    KnowledgeCollectionPermissionBundleGrantRequest,
    KnowledgeCollectionPermissionBulkBundleRequest,
    KnowledgeCollectionResponse,
    KnowledgeCollectionUpdateRequest,
    KnowledgeCollectionVisibilityRequest,
)


class _FakeDb:
    def __init__(self):
        self.committed = False
        self.refreshed = []
        self.added = []
        self.operations = []

    def add(self, value):
        self.added.append(value)
        self.operations.append(("add", type(value)))

    def commit(self):
        self.committed = True
        self.operations.append(("commit", None))

    def refresh(self, value):
        self.refreshed.append(value)


class _BulkQuery:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def with_for_update(self):
        return self

    def all(self):
        return list(self.rows)


class _BulkDb(_FakeDb):
    def __init__(self, collections):
        super().__init__()
        self.collections = collections
        self.rollback_count = 0

    def query(self, model):
        return _BulkQuery(self.collections if model is KnowledgeCollection else [])

    def rollback(self):
        self.rollback_count += 1
        self.operations.append(("rollback", None))


class _SubjectQuery:
    def __init__(self, rows):
        self.rows = rows

    def join(self, *_args, **_kwargs):
        return self

    def filter(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def limit(self, value):
        self.rows = self.rows[:value]
        return self

    def all(self):
        return self.rows


class _SubjectDb:
    def __init__(self, teams, users):
        self.teams = teams
        self.users = users

    def query(self, model):
        return _SubjectQuery(self.teams if model.__name__ == "Team" else self.users)


def _service(monkeypatch, db=None):
    service = KnowledgeCollectionService(
        db or _FakeDb(),
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(service, "_record_collection_audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(service, "_has_domain_action", lambda action: False)
    monkeypatch.setattr(
        service,
        "_collection_has_source_managed_items",
        lambda collection_id: False,
    )
    monkeypatch.setattr(
        service.permission_helper,
        "bulk_evaluate_kb_action",
        lambda kbs, action: {
            kb.id: SimpleNamespace(allowed=False) for kb in kbs
        },
    )
    return service


def _collection(collection_id=None):
    return SimpleNamespace(
        id=collection_id or uuid.uuid4(),
        organization_id=uuid.uuid4(),
        name="HR",
        description=None,
        is_system_managed=False,
        source_identity_id=None,
        source_connector_ref=None,
        sync_state="manual",
        lifecycle_state="active",
        safe_metadata={},
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def _collection_response(collection):
    return KnowledgeCollectionResponse(
        id=collection.id,
        organization_id=collection.organization_id,
        name=collection.name,
        visibility="public"
        if collection.safe_metadata.get("visibility") == "public"
        else "private",
        linked_kb_count_bucket="0",
        active_kb_count_bucket="0",
        created_at=collection.created_at,
        updated_at=collection.updated_at,
    )


def test_collection_projection_separates_sync_authority_from_adapter_support(
    monkeypatch,
):
    service = _service(monkeypatch)
    collection = _collection()
    monkeypatch.setattr(service, "_linked_kb_count", lambda collection_id: 0)
    monkeypatch.setattr(service, "_active_kb_count", lambda collection_id: 0)
    monkeypatch.setattr(
        service.permission_helper,
        "evaluate_collection_action",
        lambda collection, action, **_kwargs: SimpleNamespace(allowed=True),
    )
    target_scan = SimpleNamespace(is_supported=True)
    monkeypatch.setattr(
        knowledge_collection_service_module,
        "scan_collection_sync_targets",
        lambda *_args, **_kwargs: target_scan,
    )

    supported = service._collection_response(collection)
    assert supported.can_sync is True
    assert supported.sync_supported is True

    collection.source_identity_id = uuid.uuid4()
    unsupported = service._collection_response(collection)
    assert unsupported.can_sync is True
    assert unsupported.sync_supported is False

    collection.source_identity_id = None
    target_scan.is_supported = False
    unsupported_child = service._collection_response(collection)
    assert unsupported_child.can_sync is True
    assert unsupported_child.sync_supported is False


def test_safe_metadata_rejects_raw_source_keys(monkeypatch):
    service = _service(monkeypatch)

    with pytest.raises(KnowledgeCollectionServiceError) as exc_info:
        service._sanitize_safe_metadata({"raw_source_url": "https://internal"})

    assert exc_info.value.status_code == 400
    assert exc_info.value.code == "validation.failed"


def test_safe_metadata_sanitizes_collection_label_and_preserves_other_metadata(
    monkeypatch,
):
    service = _service(monkeypatch)

    sanitized = service._sanitize_safe_metadata(
        {
            "safe_label": "  사내 규정 \x00 https://example.invalid/private  ",
            "collection_safe_topics": ["policy"],
            "visibility": "private",
        },
        preserve_visibility="public",
    )

    assert sanitized == {
        "safe_label": "사내 규정",
        "collection_safe_topics": ["policy"],
        "visibility": "public",
    }


def test_safe_metadata_caps_collection_label_length(monkeypatch):
    service = _service(monkeypatch)

    sanitized = service._sanitize_safe_metadata({"safe_label": "가" * 300})

    assert sanitized["safe_label"] == "가" * 255


@pytest.mark.parametrize("value", [None, 1, [], {}])
def test_safe_metadata_rejects_non_string_collection_label(monkeypatch, value):
    service = _service(monkeypatch)

    with pytest.raises(KnowledgeCollectionServiceError) as exc_info:
        service._sanitize_safe_metadata({"safe_label": value})

    assert exc_info.value.status_code == 400
    assert exc_info.value.code == "validation.failed"
    assert exc_info.value.details == {"field": "safe_metadata.safe_label"}


@pytest.mark.parametrize(
    "value",
    ["   \t  ", "https://example.invalid/private"],
)
def test_safe_metadata_rejects_collection_label_without_display_safe_text(
    monkeypatch,
    value,
):
    service = _service(monkeypatch)

    with pytest.raises(KnowledgeCollectionServiceError) as exc_info:
        service._sanitize_safe_metadata({"safe_label": value})

    assert exc_info.value.status_code == 400
    assert exc_info.value.code == "validation.failed"
    assert exc_info.value.details == {"field": "safe_metadata.safe_label"}


def test_create_collection_rejects_blank_name_after_normalization(monkeypatch):
    service = _service(monkeypatch)
    monkeypatch.setattr(service, "_require_org_manager_or_domain", lambda action: None)

    with pytest.raises(KnowledgeCollectionServiceError) as exc_info:
        service.create_collection(KnowledgeCollectionCreateRequest(name="   \t  "))

    assert exc_info.value.status_code == 400
    assert exc_info.value.code == "validation.failed"
    assert exc_info.value.details == {"field": "name"}


def test_delegated_collection_create_commits_collection_and_audit_together(monkeypatch):
    db = _FakeDb()
    service = KnowledgeCollectionService(
        db,
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(service, "_is_org_manager", lambda: False)
    monkeypatch.setattr(
        service,
        "_has_domain_action",
        lambda action: action == "catalog_manage",
    )
    monkeypatch.setattr(
        service,
        "_collection_response",
        lambda collection: SimpleNamespace(id=collection.id),
    )

    result = service.create_collection(KnowledgeCollectionCreateRequest(name="HR"))

    assert result.id is not None
    assert isinstance(db.added[0], KnowledgeCollection)
    assert isinstance(db.added[1], AuditLog)
    assert db.operations == [
        ("add", KnowledgeCollection),
        ("add", AuditLog),
        ("commit", None),
    ]
    assert db.added[0].safe_metadata == {}


def test_catalog_delegate_can_manage_private_membership_without_kb_content_grant(
    monkeypatch,
):
    service = _service(monkeypatch)
    collection = _collection()
    kb = SimpleNamespace(id=uuid.uuid4(), source_identity_id=None)
    monkeypatch.setattr(
        service,
        "_has_domain_action",
        lambda action: action == "catalog_manage",
    )
    monkeypatch.setattr(
        service,
        "_require_collection_action",
        lambda *_args, **_kwargs: pytest.fail("resource manage should not be required"),
    )
    monkeypatch.setattr(
        service,
        "_require_kb_manage",
        lambda *_args, **_kwargs: pytest.fail("KB content manage should not be required"),
    )

    service._require_collection_membership_mutation(collection, kb=kb)


def test_public_membership_requires_org_manager_ack_and_blocks_source_managed_link(
    monkeypatch,
):
    service = _service(monkeypatch)
    collection = _collection()
    collection.safe_metadata = {"visibility": "public"}
    source_kb = SimpleNamespace(id=uuid.uuid4(), source_identity_id=uuid.uuid4())
    monkeypatch.setattr(service, "_require_org_manager", lambda: None)

    with pytest.raises(KnowledgeCollectionServiceError) as missing_ack:
        service._require_collection_membership_mutation(
            collection,
            kb=source_kb,
            adds_public_exposure=True,
        )
    assert missing_ack.value.status_code == 400

    with pytest.raises(KnowledgeCollectionServiceError) as source_block:
        service._require_collection_membership_mutation(
            collection,
            kb=source_kb,
            acknowledged_public_runtime_exposure=True,
            adds_public_exposure=True,
        )
    assert source_block.value.status_code == 409
    assert source_block.value.details == {
        "policy_reason": "source_public_exposure_required"
    }


def test_public_membership_blocks_source_managed_collection_even_for_manual_kb(
    monkeypatch,
):
    service = _service(monkeypatch)
    collection = _collection()
    collection.safe_metadata = {"visibility": "public"}
    collection.source_connector_ref = "opaque-connector-ref"
    manual_kb = SimpleNamespace(id=uuid.uuid4(), source_identity_id=None)
    monkeypatch.setattr(service, "_require_org_manager", lambda: None)

    with pytest.raises(KnowledgeCollectionServiceError) as exc_info:
        service._require_collection_membership_mutation(
            collection,
            kb=manual_kb,
            acknowledged_public_runtime_exposure=True,
            adds_public_exposure=True,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.details == {
        "policy_reason": "source_public_exposure_required"
    }


def test_collection_role_bundle_writes_explicit_actions_in_one_transaction(monkeypatch):
    db = _FakeDb()
    service = KnowledgeCollectionService(
        db,
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )
    collection = _collection()
    actions = []

    monkeypatch.setattr(service, "_locked_collection_or_hidden", lambda _id: collection)
    monkeypatch.setattr(
        service,
        "_require_collection_permission_authority",
        lambda _collection: "resource_manager",
    )
    monkeypatch.setattr(
        service,
        "_block_collection_delegate_self_escalation",
        lambda *_args, **_kwargs: None,
    )

    def fake_grant(_collection, grant):
        actions.append(grant.permission_action)
        row = SimpleNamespace(id=uuid.uuid4())
        db.add(row)
        return row, True

    monkeypatch.setattr(service, "_grant_team_permission", fake_grant)
    monkeypatch.setattr(
        service,
        "_team_permission_response",
        lambda row: SimpleNamespace(permission_id=row.id),
    )

    result = service.grant_permission_bundle(
        collection.id,
        KnowledgeCollectionPermissionBundleGrantRequest(
            subject_type="team",
            subject_id=uuid.uuid4(),
            role_bundle="workflow_router",
        ),
    )

    assert actions == ["read", "route"]
    assert len(result) == 2
    assert sum(1 for operation in db.operations if operation == ("commit", None)) == 1
    assert isinstance(db.added[-1], AuditLog)
    assert db.operations[-1] == ("commit", None)


def test_domain_delegation_subjects_return_safe_team_and_user_labels(monkeypatch):
    db = _SubjectDb(
        teams=[SimpleNamespace(id=uuid.uuid4(), name="Knowledge Team")],
        users=[
            SimpleNamespace(
                id=uuid.uuid4(),
                name=None,
                email="raw-email-must-not-be-returned@example.com",
            )
        ],
    )
    service = KnowledgeCollectionService(
        db,
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(service, "_require_org_manager", lambda: None)

    team_result = service.list_domain_delegation_subjects(subject_type="team")
    user_result = service.list_domain_delegation_subjects(subject_type="user")

    assert team_result.subjects[0].subject_safe_label == "Knowledge Team"
    assert user_result.subjects[0].subject_safe_label == "User"
    assert "@" not in str(user_result.model_dump())


def test_domain_subject_authority_is_checked_before_page_validation(monkeypatch):
    service = _service(monkeypatch)
    monkeypatch.setattr(
        service,
        "_require_org_manager",
        lambda: (_ for _ in ()).throw(
            KnowledgeCollectionServiceError(
                403,
                "permission.denied",
                "Organization manager permission is required.",
            )
        ),
    )
    monkeypatch.setattr(
        service,
        "_delegation_subjects_response",
        lambda **kwargs: pytest.fail("page validation must follow authority"),
    )

    with pytest.raises(KnowledgeCollectionServiceError) as exc_info:
        service.list_domain_delegation_subjects(
            subject_type="invalid",
            limit="not-a-number",
        )

    assert exc_info.value.status_code == 403


def test_update_collection_rejects_blank_name_after_normalization(monkeypatch):
    service = _service(monkeypatch)
    collection = _collection()
    monkeypatch.setattr(service, "_collection_or_hidden", lambda collection_id: collection)
    monkeypatch.setattr(service, "_require_collection_action", lambda collection, action: None)

    with pytest.raises(KnowledgeCollectionServiceError) as exc_info:
        service.update_collection(
            collection.id,
            KnowledgeCollectionUpdateRequest(name=" \n "),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.code == "validation.failed"
    assert exc_info.value.details == {"field": "name"}


def test_team_manage_revoke_detects_current_users_last_management_path(monkeypatch):
    service = _service(monkeypatch)
    team_id = uuid.uuid4()
    row = SimpleNamespace(
        id=uuid.uuid4(),
        team_id=team_id,
        permission_action="manage",
    )
    monkeypatch.setattr(service, "_is_org_manager", lambda: False)
    monkeypatch.setattr(service, "_active_team_ids", lambda: {team_id})
    monkeypatch.setattr(
        service,
        "_has_alternate_collection_manage_path",
        lambda collection_id, *, exclude_permission_id: False,
    )

    assert (
        service._would_revoke_current_user_last_manage_path(
            uuid.uuid4(),
            "team",
            row,
        )
        is True
    )


def test_team_manage_revoke_allows_when_alternate_management_path_exists(monkeypatch):
    service = _service(monkeypatch)
    team_id = uuid.uuid4()
    row = SimpleNamespace(
        id=uuid.uuid4(),
        team_id=team_id,
        permission_action="manage",
    )
    monkeypatch.setattr(service, "_is_org_manager", lambda: False)
    monkeypatch.setattr(service, "_active_team_ids", lambda: {team_id})
    monkeypatch.setattr(
        service,
        "_has_alternate_collection_manage_path",
        lambda collection_id, *, exclude_permission_id: True,
    )

    assert (
        service._would_revoke_current_user_last_manage_path(
            uuid.uuid4(),
            "team",
            row,
        )
        is False
    )


def test_public_visibility_requires_acknowledgement(monkeypatch):
    service = _service(monkeypatch)
    collection = _collection()
    monkeypatch.setattr(service, "_locked_collection_or_hidden", lambda collection_id: collection)
    monkeypatch.setattr(service, "_require_org_manager", lambda: None)

    with pytest.raises(KnowledgeCollectionServiceError) as exc_info:
        service.update_visibility(
            collection.id,
            KnowledgeCollectionVisibilityRequest(
                visibility="public",
                acknowledged_public_runtime_exposure=False,
            ),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.details == {"field": "acknowledged_public_runtime_exposure"}


def test_public_visibility_blocks_source_managed_items_without_approval_primitive(
    monkeypatch,
):
    service = _service(monkeypatch)
    collection = _collection()
    monkeypatch.setattr(service, "_locked_collection_or_hidden", lambda collection_id: collection)
    monkeypatch.setattr(service, "_require_org_manager", lambda: None)
    monkeypatch.setattr(
        service,
        "_collection_has_source_managed_items",
        lambda collection_id: True,
    )

    with pytest.raises(KnowledgeCollectionServiceError) as exc_info:
        service.update_visibility(
            collection.id,
            KnowledgeCollectionVisibilityRequest(
                visibility="public",
                acknowledged_public_runtime_exposure=True,
            ),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "policy.blocked"
    assert exc_info.value.details == {
        "policy_reason": "source_public_exposure_required"
    }


@pytest.mark.parametrize("source_field", ["source_identity_id", "source_connector_ref"])
def test_public_visibility_blocks_source_managed_collection_without_approval_primitive(
    monkeypatch,
    source_field,
):
    service = _service(monkeypatch)
    collection = _collection()
    setattr(collection, source_field, uuid.uuid4())
    monkeypatch.setattr(service, "_locked_collection_or_hidden", lambda collection_id: collection)
    monkeypatch.setattr(service, "_require_org_manager", lambda: None)

    with pytest.raises(KnowledgeCollectionServiceError) as exc_info:
        service.update_visibility(
            collection.id,
            KnowledgeCollectionVisibilityRequest(
                visibility="public",
                acknowledged_public_runtime_exposure=True,
            ),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "policy.blocked"
    assert exc_info.value.details == {
        "policy_reason": "source_public_exposure_required"
    }


def test_bulk_authority_prefers_resource_manage_over_domain_delegate(monkeypatch):
    service = _service(monkeypatch)
    collections = [_collection(), _collection()]
    monkeypatch.setattr(service, "_is_org_manager", lambda: False)
    monkeypatch.setattr(
        service,
        "_has_domain_action",
        lambda action: action == "permission_delegate",
    )
    monkeypatch.setattr(
        service.permission_helper,
        "bulk_evaluate_collection_action",
        lambda candidates, action, include_archived=False: {
            candidate.id: SimpleNamespace(
                allowed=True,
                external_reason_code="permission.allowed",
            )
            for candidate in candidates
        },
    )

    authority = service._require_bulk_collection_permission_authority(collections)

    assert authority == "resource_manager"


@pytest.mark.parametrize("target_count", [11, 50])
def test_bulk_permission_response_uses_request_bounded_count_bucket(
    monkeypatch,
    target_count,
):
    collections = [_collection() for _ in range(target_count)]
    db = _BulkDb(collections)
    service = _service(monkeypatch, db)
    for collection in collections:
        collection.organization_id = service.organization_id
    monkeypatch.setattr(
        service,
        "_require_bulk_collection_permission_authority",
        lambda candidates: "organization_manager",
    )
    monkeypatch.setattr(service, "_lock_bundle_subject", lambda *args, **kwargs: None)

    response = service.mutate_permission_bundle_bulk(
        KnowledgeCollectionPermissionBulkBundleRequest(
            collection_ids=[collection.id for collection in collections],
            operation="grant",
            subject_type="user",
            subject_id=uuid.uuid4(),
            role_bundle="viewer",
        )
    )

    assert response.target_count_bucket == "11-50"
    assert response.changed_count_bucket == "11-50"
    assert response.unchanged_count_bucket == "0"
    assert db.committed is True
    assert db.rollback_count == 0


def test_bulk_permission_response_validation_failure_rolls_back_before_commit(
    monkeypatch,
):
    collection = _collection()
    db = _BulkDb([collection])
    service = _service(monkeypatch, db)
    collection.organization_id = service.organization_id
    monkeypatch.setattr(
        service,
        "_require_bulk_collection_permission_authority",
        lambda candidates: "organization_manager",
    )
    monkeypatch.setattr(service, "_lock_bundle_subject", lambda *args, **kwargs: None)

    def reject_response(**_kwargs):
        raise ValueError("simulated response validation failure")

    monkeypatch.setattr(
        knowledge_collection_service_module,
        "KnowledgeCollectionPermissionBulkBundleResponse",
        reject_response,
    )

    with pytest.raises(ValueError, match="^simulated response validation failure$"):
        service.mutate_permission_bundle_bulk(
            KnowledgeCollectionPermissionBulkBundleRequest(
                collection_ids=[collection.id],
                operation="grant",
                subject_type="user",
                subject_id=uuid.uuid4(),
                role_bundle="viewer",
            )
        )

    assert db.committed is False
    assert db.rollback_count == 1


def test_management_item_projection_rechecks_mutation_not_read_authority(monkeypatch):
    service = _service(monkeypatch)
    collection = _collection()
    calls = []
    expected = SimpleNamespace(order_revision="safe-revision")
    monkeypatch.setattr(
        service,
        "_collection_or_hidden",
        lambda collection_id: collection,
    )
    monkeypatch.setattr(
        service,
        "_require_collection_membership_candidate_access",
        lambda candidate: calls.append(candidate.id),
    )
    monkeypatch.setattr(service, "_ordered_collection_items", lambda collection_id: [])
    monkeypatch.setattr(
        service,
        "_items_response",
        lambda collection_id, items: expected,
    )

    result = service.list_items_management_response(collection.id)

    assert result is expected
    assert calls == [collection.id]


def test_public_visibility_sets_only_candidate_flag(monkeypatch):
    db = _FakeDb()
    service = _service(monkeypatch, db=db)
    collection = _collection()
    monkeypatch.setattr(service, "_locked_collection_or_hidden", lambda collection_id: collection)
    monkeypatch.setattr(service, "_require_org_manager", lambda: None)
    monkeypatch.setattr(service, "_collection_response", _collection_response)

    result = service.update_visibility(
        collection.id,
        KnowledgeCollectionVisibilityRequest(
            visibility="public",
            acknowledged_public_runtime_exposure=True,
        ),
    )

    assert db.committed is True
    assert collection.safe_metadata["visibility"] == "public"
    assert result.collection.visibility == "public"
    assert result.public_runtime_effect == "anonymous_public_only_candidate"


def test_link_item_requires_collection_manage_and_kb_manage(monkeypatch):
    service = _service(monkeypatch)
    collection = _collection()
    kb = SimpleNamespace(id=uuid.uuid4(), lifecycle_state="active")
    calls = []
    monkeypatch.setattr(service, "_locked_collection_or_hidden", lambda collection_id: collection)
    monkeypatch.setattr(
        service,
        "_require_collection_action",
        lambda collection, action: calls.append((collection.id, action)),
    )
    monkeypatch.setattr(service, "_knowledge_base_or_hidden", lambda kb_id: kb)
    monkeypatch.setattr(service, "_kb_manage_allowed", lambda kb: False)

    with pytest.raises(KnowledgeCollectionServiceError) as exc_info:
        service.link_item(
            collection.id,
            KnowledgeCollectionItemLinkRequest(knowledge_base_id=kb.id),
        )

    assert calls == [(collection.id, "manage")]
    assert exc_info.value.status_code == 403
    assert exc_info.value.code == "permission.denied"


def test_link_candidates_apply_limit_after_manage_filter(monkeypatch):
    service = _service(monkeypatch)
    collection = _collection()
    denied_kbs = [
        SimpleNamespace(id=uuid.uuid4(), name=f"Hidden {index}", lifecycle_state="active")
        for index in range(100)
    ]
    allowed_kb = SimpleNamespace(id=uuid.uuid4(), name="Allowed", lifecycle_state="active")
    pages = [denied_kbs, [allowed_kb]]
    calls = []

    monkeypatch.setattr(service, "_collection_or_hidden", lambda collection_id: collection)
    monkeypatch.setattr(service, "_require_collection_action", lambda collection, action: None)
    monkeypatch.setattr(service, "_linked_kb_ids", lambda collection_id: set())
    monkeypatch.setattr(
        service,
        "_link_candidate_kb_page",
        lambda *, limit, offset: calls.append((limit, offset)) or pages.pop(0)
        if pages
        else [],
    )
    monkeypatch.setattr(service, "_kb_manage_allowed", lambda kb: kb.id == allowed_kb.id)

    candidates = service.list_link_candidates(collection.id, limit=1)

    assert [candidate.knowledge_base_id for candidate in candidates] == [allowed_kb.id]
    assert len(calls) == 2


def test_link_candidates_redacts_source_managed_kb_name_without_approved_display_policy(
    monkeypatch,
):
    service = _service(monkeypatch)
    collection = _collection()
    kb = SimpleNamespace(
        id=uuid.uuid4(),
        name="Internal HR Source Name",
        lifecycle_state="active",
        source_identity=SimpleNamespace(
            display_policy_state="unreviewed",
            safe_display_name="Reviewed HR Label",
        ),
    )

    monkeypatch.setattr(service, "_collection_or_hidden", lambda collection_id: collection)
    monkeypatch.setattr(service, "_require_collection_action", lambda collection, action: None)
    monkeypatch.setattr(service, "_linked_kb_ids", lambda collection_id: set())
    monkeypatch.setattr(service, "_link_candidate_kb_page", lambda *, limit, offset: [kb])
    monkeypatch.setattr(service, "_kb_manage_allowed", lambda kb: True)

    candidates = service.list_link_candidates(collection.id, limit=1)

    assert candidates[0].safe_label == "Knowledge Base"


def test_link_candidates_use_approved_source_safe_display_name(monkeypatch):
    service = _service(monkeypatch)
    collection = _collection()
    kb = SimpleNamespace(
        id=uuid.uuid4(),
        name="Internal HR Source Name",
        lifecycle_state="active",
        source_identity=SimpleNamespace(
            display_policy_state="approved",
            safe_display_name="Reviewed HR Label",
        ),
    )

    monkeypatch.setattr(service, "_collection_or_hidden", lambda collection_id: collection)
    monkeypatch.setattr(service, "_require_collection_action", lambda collection, action: None)
    monkeypatch.setattr(service, "_linked_kb_ids", lambda collection_id: set())
    monkeypatch.setattr(service, "_link_candidate_kb_page", lambda *, limit, offset: [kb])
    monkeypatch.setattr(service, "_kb_manage_allowed", lambda kb: True)

    candidates = service.list_link_candidates(collection.id, limit=1)

    assert candidates[0].safe_label == "Reviewed HR Label"


def test_catalog_only_link_candidate_does_not_fallback_to_manual_kb_name(
    monkeypatch,
):
    service = _service(monkeypatch)
    collection = _collection()
    kb = SimpleNamespace(
        id=uuid.uuid4(),
        name="Restricted Manual Label",
        safe_metadata={},
        lifecycle_state="active",
        source_identity=None,
        source_identity_id=None,
    )
    monkeypatch.setattr(
        service,
        "_has_domain_action",
        lambda action: action == "catalog_manage",
    )
    monkeypatch.setattr(service, "_collection_or_hidden", lambda collection_id: collection)
    monkeypatch.setattr(service, "_linked_kb_ids", lambda collection_id: set())
    monkeypatch.setattr(service, "_link_candidate_kb_page", lambda *, limit, offset: [kb])
    read_calls = []
    monkeypatch.setattr(
        service.permission_helper,
        "bulk_evaluate_kb_action",
        lambda kbs, action: read_calls.append(
            (action, [candidate.id for candidate in kbs])
        )
        or {candidate.id: SimpleNamespace(allowed=False) for candidate in kbs},
    )

    candidates = service.list_link_candidates(collection.id, limit=1)

    assert candidates[0].safe_label == "Knowledge Base"
    assert "Restricted Manual Label" not in candidates[0].model_dump_json()
    assert read_calls == [("read", [kb.id])]


def test_catalog_only_link_candidate_uses_sanitized_safe_metadata_label(
    monkeypatch,
):
    service = _service(monkeypatch)
    collection = _collection()
    kb = SimpleNamespace(
        id=uuid.uuid4(),
        name="Restricted Manual Label",
        safe_metadata={"safe_label": "  Approved Catalog Label  "},
        lifecycle_state="active",
        source_identity=None,
        source_identity_id=None,
    )
    monkeypatch.setattr(
        service,
        "_has_domain_action",
        lambda action: action == "catalog_manage",
    )
    monkeypatch.setattr(service, "_collection_or_hidden", lambda collection_id: collection)
    monkeypatch.setattr(service, "_linked_kb_ids", lambda collection_id: set())
    monkeypatch.setattr(service, "_link_candidate_kb_page", lambda *, limit, offset: [kb])

    candidates = service.list_link_candidates(collection.id, limit=1)

    assert candidates[0].safe_label == "Approved Catalog Label"
    assert "Restricted Manual Label" not in candidates[0].model_dump_json()


def test_link_candidate_uses_manual_kb_name_after_independent_read_allows(
    monkeypatch,
):
    service = _service(monkeypatch)
    collection = _collection()
    kb = SimpleNamespace(
        id=uuid.uuid4(),
        name="Readable Manual Label",
        safe_metadata={},
        lifecycle_state="active",
        source_identity=None,
        source_identity_id=None,
    )
    monkeypatch.setattr(
        service,
        "_has_domain_action",
        lambda action: action == "catalog_manage",
    )
    monkeypatch.setattr(service, "_collection_or_hidden", lambda collection_id: collection)
    monkeypatch.setattr(service, "_linked_kb_ids", lambda collection_id: set())
    monkeypatch.setattr(service, "_link_candidate_kb_page", lambda *, limit, offset: [kb])
    monkeypatch.setattr(
        service.permission_helper,
        "bulk_evaluate_kb_action",
        lambda kbs, action: {
            candidate.id: SimpleNamespace(allowed=True) for candidate in kbs
        },
    )

    candidates = service.list_link_candidates(collection.id, limit=1)

    assert candidates[0].safe_label == "Readable Manual Label"


@pytest.mark.parametrize(
    ("auth_state", "safe_metadata", "expected_label"),
    [
        ("none", {}, "Knowledge Base"),
        ("none", {"safe_label": "Approved Catalog Label"}, "Approved Catalog Label"),
        ("none", {"safe_label": 123}, "Knowledge Base"),
        ("viewer", {}, "Readable Item Label"),
    ],
)
def test_collection_item_label_requires_safe_metadata_or_independent_read(
    monkeypatch,
    auth_state,
    safe_metadata,
    expected_label,
):
    service = _service(monkeypatch)
    kb = SimpleNamespace(
        id=uuid.uuid4(),
        name="Readable Item Label",
        safe_metadata=safe_metadata,
        lifecycle_state="active",
        sync_state="manual",
        source_identity=None,
        source_identity_id=None,
    )
    item = SimpleNamespace(
        id=uuid.uuid4(),
        knowledge_base_id=kb.id,
        knowledge_base=kb,
        rank=0,
    )
    monkeypatch.setattr(
        "apps.gateway.services.knowledge_collection_service."
        "get_effective_knowledge_base_auth_state",
        lambda *args, **kwargs: auth_state,
    )
    monkeypatch.setattr(
        service.permission_helper,
        "evaluate_kb_use",
        lambda candidate: SimpleNamespace(allowed=False),
    )

    response = service._item_response(item)

    assert response.safe_label == expected_label
    if auth_state == "none" and not isinstance(safe_metadata.get("safe_label"), str):
        assert "Readable Item Label" not in response.model_dump_json()


def test_source_managed_item_without_loaded_identity_never_uses_raw_kb_name(
    monkeypatch,
):
    service = _service(monkeypatch)
    kb = SimpleNamespace(
        id=uuid.uuid4(),
        name="Source Internal Label",
        safe_metadata={"safe_label": "Manual Override Label"},
        lifecycle_state="active",
        sync_state="synced",
        source_identity=None,
        source_identity_id=uuid.uuid4(),
    )
    item = SimpleNamespace(
        id=uuid.uuid4(),
        knowledge_base_id=kb.id,
        knowledge_base=kb,
        rank=0,
    )
    monkeypatch.setattr(
        "apps.gateway.services.knowledge_collection_service."
        "get_effective_knowledge_base_auth_state",
        lambda *args, **kwargs: "viewer",
    )
    monkeypatch.setattr(
        service.permission_helper,
        "evaluate_kb_use",
        lambda candidate: SimpleNamespace(allowed=False),
    )

    response = service._item_response(item)

    assert response.safe_label == "Knowledge Base"
    assert "Source Internal Label" not in response.model_dump_json()
    assert "Manual Override Label" not in response.model_dump_json()
