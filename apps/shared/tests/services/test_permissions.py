import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from apps.shared.permissions import (
    auth_state_at_least,
    knowledge_base_auth_state_allows,
    llm_credential_auth_state_allows,
    normalize_auth_state,
    normalize_resource_auth_state,
    workflow_auth_state_allows,
)
from apps.shared.services.permissions import (
    get_effective_knowledge_base_auth_state,
    get_effective_knowledge_domain_actions,
    get_effective_llm_credential_auth_state,
    get_effective_mail_credential_auth_state,
    get_effective_mail_credential_auth_states,
    get_effective_workflow_auth_state,
    get_workflow_permission_sources,
    has_active_organization_membership,
    has_knowledge_base_permission,
    has_knowledge_domain_permission,
    has_llm_credential_permission,
    has_workflow_permission,
)


class FakeQuery:
    def __init__(self, db):
        self.db = db

    def join(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        return self.db.first_values.pop(0)

    def all(self):
        return self.db.all_values.pop(0)


class FakeDb:
    def __init__(self, first_values=None, all_values=None):
        self.first_values = list(first_values or [])
        self.all_values = list(all_values or [])

    def query(self, *args, **kwargs):
        return FakeQuery(self)


class FakeSqlAlchemyRow:
    def __init__(self, auth_state):
        self._mapping = {"auth_state": auth_state}


class FakeMappedRow:
    def __init__(self, **values):
        self._mapping = values


def _active_user(user_id):
    return SimpleNamespace(id=user_id, deactivated_at=None)


def _active_organization(organization_id, *, created_by=None, managed_by=None):
    return SimpleNamespace(
        id=organization_id,
        created_by=created_by or uuid.uuid4(),
        managed_by=managed_by,
        is_active=True,
    )


def _inactive_organization(organization_id, *, created_by=None, managed_by=None):
    organization = _active_organization(
        organization_id,
        created_by=created_by,
        managed_by=managed_by,
    )
    organization.is_active = False
    return organization


def _organization_member(auth_state="member", membership_state="active"):
    return SimpleNamespace(
        membership_state=membership_state,
        organization_auth_state=auth_state,
    )


def test_legacy_auth_states_normalize_to_mvp_auth_states():
    assert normalize_auth_state("read") == "viewer"
    assert normalize_auth_state("execute") == "operator"
    assert normalize_auth_state("write") == "builder"
    assert normalize_auth_state("admin") == "manager"
    assert auth_state_at_least("admin", "manager") is True


def test_workflow_permission_action_matrix():
    assert workflow_auth_state_allows("viewer", "read") is True
    assert workflow_auth_state_allows("viewer", "execute") is False
    assert workflow_auth_state_allows("operator", "execute") is True
    assert workflow_auth_state_allows("operator", "write") is False
    assert workflow_auth_state_allows("builder", "write") is True
    assert workflow_auth_state_allows("builder", "deploy") is False
    assert workflow_auth_state_allows("manager", "manage") is True


def test_workflow_permission_sources_include_team_and_user_direct():
    user_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    team_id = uuid.uuid4()
    workflow = SimpleNamespace(id=workflow_id, organization_id=organization_id)
    organization = SimpleNamespace(
        id=organization_id,
        is_active=True,
        created_by=uuid.uuid4(),
        managed_by=None,
    )
    membership = SimpleNamespace(
        membership_state="active",
        organization_auth_state="member",
    )
    team_permission = SimpleNamespace(
        id=uuid.uuid4(),
        workflow_id=workflow_id,
        auth_state="builder",
    )
    team = SimpleNamespace(id=team_id, name="워크플로우 빌더팀")
    user_permission = SimpleNamespace(
        id=uuid.uuid4(),
        workflow_id=workflow_id,
        auth_state="operator",
    )
    user = SimpleNamespace(id=user_id, name=None, email="hyeyeon@moduly.local")
    db = FakeDb(
        first_values=[workflow, user, organization, membership],
        all_values=[
            [(team_permission, team)],
            [(user_permission, user)],
        ],
    )

    sources = get_workflow_permission_sources(
        db,
        user_id,
        workflow_id,
        organization_id,
    )

    assert [source.model_dump() for source in sources] == [
        {
            "type": "team",
            "auth_state": "builder",
            "team_id": team_id,
            "team_name": "워크플로우 빌더팀",
            "user_id": None,
            "user_name": None,
        },
        {
            "type": "user",
            "auth_state": "operator",
            "team_id": None,
            "team_name": None,
            "user_id": user_id,
            "user_name": "hyeyeon@moduly.local",
        },
    ]


def test_workflow_permission_sources_exclude_none_sources():
    user_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    workflow = SimpleNamespace(id=workflow_id, organization_id=organization_id)
    organization = SimpleNamespace(
        id=organization_id,
        is_active=True,
        created_by=uuid.uuid4(),
        managed_by=None,
    )
    membership = SimpleNamespace(
        membership_state="active",
        organization_auth_state="member",
    )
    team_permission = SimpleNamespace(
        id=uuid.uuid4(),
        workflow_id=workflow_id,
        auth_state="none",
    )
    team = SimpleNamespace(id=uuid.uuid4(), name="조회팀")
    db = FakeDb(
        first_values=[workflow, SimpleNamespace(id=user_id), organization, membership],
        all_values=[
            [(team_permission, team)],
            [],
        ],
    )

    assert (
        get_workflow_permission_sources(db, user_id, workflow_id, organization_id)
        == []
    )


def test_workflow_permission_sources_sort_by_strongest_auth_state_first():
    user_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    workflow = SimpleNamespace(id=workflow_id, organization_id=organization_id)
    organization = SimpleNamespace(
        id=organization_id,
        is_active=True,
        created_by=uuid.uuid4(),
        managed_by=None,
    )
    membership = SimpleNamespace(
        membership_state="active",
        organization_auth_state="member",
    )
    team_permission = SimpleNamespace(
        id=uuid.uuid4(),
        workflow_id=workflow_id,
        auth_state="viewer",
    )
    team = SimpleNamespace(id=uuid.uuid4(), name="조회팀")
    user_permission = SimpleNamespace(
        id=uuid.uuid4(),
        workflow_id=workflow_id,
        auth_state="manager",
    )
    user = SimpleNamespace(id=user_id, name="혜연", email="hyeyeon@moduly.local")
    db = FakeDb(
        first_values=[workflow, user, organization, membership],
        all_values=[
            [(team_permission, team)],
            [(user_permission, user)],
        ],
    )

    sources = get_workflow_permission_sources(
        db,
        user_id,
        workflow_id,
        organization_id,
    )

    assert [source.auth_state for source in sources] == ["manager", "viewer"]


def test_audit_only_states_fail_closed_for_resource_permissions():
    assert normalize_resource_auth_state("auditor") == "none"
    assert normalize_resource_auth_state("raw_auditor") == "none"
    assert workflow_auth_state_allows("auditor", "read") is False
    assert workflow_auth_state_allows("raw_auditor", "execute") is False
    assert llm_credential_auth_state_allows("raw_auditor", "use") is False


def test_bulk_mail_credential_auth_states_combine_team_and_direct_grants():
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    credential_a = uuid.uuid4()
    credential_b = uuid.uuid4()
    db = FakeDb(
        first_values=[
            _active_user(user_id),
            _active_organization(organization_id),
            _organization_member(),
        ],
        all_values=[
            [
                FakeMappedRow(id=credential_a),
                FakeMappedRow(id=credential_b),
            ],
            [
                FakeMappedRow(
                    mail_credential_id=credential_a,
                    auth_state="viewer",
                ),
                FakeMappedRow(
                    mail_credential_id=credential_b,
                    auth_state="operator",
                ),
            ],
            [
                FakeMappedRow(
                    mail_credential_id=credential_a,
                    auth_state="builder",
                )
            ],
        ],
    )

    result = get_effective_mail_credential_auth_states(
        db,
        user_id,
        [credential_a, credential_b, credential_a],
        organization_id,
    )

    assert result == {
        credential_a: "builder",
        credential_b: "operator",
    }
    assert db.first_values == []
    assert db.all_values == []


def test_bulk_mail_credential_auth_state_matches_scalar_decision():
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    credential_id = uuid.uuid4()
    credential = SimpleNamespace(
        id=credential_id,
        organization_id=organization_id,
    )
    scalar_db = FakeDb(
        first_values=[
            credential,
            _active_user(user_id),
            _active_organization(organization_id),
            _organization_member(),
        ],
        all_values=[
            [FakeSqlAlchemyRow("operator")],
            [FakeSqlAlchemyRow("viewer")],
        ],
    )
    bulk_db = FakeDb(
        first_values=[
            _active_user(user_id),
            _active_organization(organization_id),
            _organization_member(),
        ],
        all_values=[
            [FakeMappedRow(id=credential_id)],
            [
                FakeMappedRow(
                    mail_credential_id=credential_id,
                    auth_state="operator",
                )
            ],
            [
                FakeMappedRow(
                    mail_credential_id=credential_id,
                    auth_state="viewer",
                )
            ],
        ],
    )

    scalar = get_effective_mail_credential_auth_state(
        scalar_db,
        user_id,
        credential_id,
        organization_id,
    )
    bulk = get_effective_mail_credential_auth_states(
        bulk_db,
        user_id,
        [credential_id],
        organization_id,
    )

    assert bulk == {credential_id: scalar}


def test_bulk_mail_credential_auth_states_exclude_cross_scope_ids():
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    scoped_id = uuid.uuid4()
    hidden_id = uuid.uuid4()
    db = FakeDb(
        first_values=[
            _active_user(user_id),
            _active_organization(organization_id),
            _organization_member("manager"),
        ],
        all_values=[[FakeMappedRow(id=scoped_id)]],
    )

    result = get_effective_mail_credential_auth_states(
        db,
        user_id,
        [scoped_id, hidden_id],
        organization_id,
    )

    assert result == {scoped_id: "manager"}


def test_revoked_mail_credential_has_no_scalar_permission() -> None:
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    credential_id = uuid.uuid4()
    db = FakeDb(
        first_values=[
            SimpleNamespace(
                id=credential_id,
                organization_id=organization_id,
                status="revoked",
            )
        ]
    )

    assert (
        get_effective_mail_credential_auth_state(
            db,
            user_id,
            credential_id,
            organization_id,
        )
        == "none"
    )
    assert db.first_values == []


def test_bulk_mail_credential_auth_states_exclude_revoked_rows() -> None:
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    active_id = uuid.uuid4()
    revoked_id = uuid.uuid4()
    db = FakeDb(
        first_values=[
            _active_user(user_id),
            _active_organization(organization_id),
            _organization_member("manager"),
        ],
        all_values=[
            [
                FakeMappedRow(id=active_id, status="active"),
                FakeMappedRow(id=revoked_id, status="revoked"),
            ]
        ],
    )

    result = get_effective_mail_credential_auth_states(
        db,
        user_id,
        [active_id, revoked_id],
        organization_id,
    )

    assert result == {active_id: "manager"}


def test_active_organization_membership_requires_active_organization():
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    db = FakeDb(
        first_values=[
            _active_user(user_id),
            _inactive_organization(organization_id),
        ]
    )

    assert has_active_organization_membership(db, user_id, organization_id) is False


def test_organization_owner_gets_manager_for_workflow():
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    db = FakeDb(
        first_values=[
            SimpleNamespace(id=workflow_id, organization_id=organization_id),
            _active_user(user_id),
            _active_organization(organization_id, created_by=user_id),
            None,
        ]
    )

    assert (
        get_effective_workflow_auth_state(db, user_id, workflow_id, organization_id)
        == "manager"
    )


def test_organization_manager_membership_gets_manager_for_workflow():
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    db = FakeDb(
        first_values=[
            SimpleNamespace(id=workflow_id, organization_id=organization_id),
            _active_user(user_id),
            _active_organization(organization_id),
            _organization_member("manager"),
        ]
    )

    assert (
        get_effective_workflow_auth_state(db, user_id, workflow_id, organization_id)
        == "manager"
    )


def test_non_active_owner_membership_blocks_legacy_owner_fallback_for_workflow():
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    workflow_id = uuid.uuid4()

    for membership_state in ("invited", "suspended", "removed"):
        db = FakeDb(
            first_values=[
                SimpleNamespace(id=workflow_id, organization_id=organization_id),
                _active_user(user_id),
                _active_organization(organization_id, created_by=user_id),
                _organization_member("manager", membership_state=membership_state),
            ]
        )

        assert (
            get_effective_workflow_auth_state(db, user_id, workflow_id, organization_id)
            == "none"
        )


def test_legacy_workflow_without_organization_allows_creator_fallback():
    user_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    db = FakeDb(
        first_values=[
            SimpleNamespace(
                id=workflow_id,
                organization_id=None,
                created_by=user_id,
            ),
            _active_user(user_id),
        ]
    )

    assert get_effective_workflow_auth_state(db, user_id, workflow_id) == "manager"


def test_org_scoped_workflow_mismatch_does_not_use_creator_fallback():
    user_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    workflow_organization_id = uuid.uuid4()
    requested_organization_id = uuid.uuid4()
    db = FakeDb(
        first_values=[
            SimpleNamespace(
                id=workflow_id,
                organization_id=workflow_organization_id,
                created_by=user_id,
            ),
        ]
    )

    assert (
        get_effective_workflow_auth_state(
            db,
            user_id,
            workflow_id,
            requested_organization_id,
        )
        == "none"
    )


def test_direct_workflow_permission_is_ignored_without_organization_scope():
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    db = FakeDb(
        first_values=[
            SimpleNamespace(id=workflow_id, organization_id=organization_id),
            _active_user(user_id),
            _active_organization(organization_id),
            None,
        ],
        all_values=[[], [("manager",)]],
    )

    assert (
        get_effective_workflow_auth_state(db, user_id, workflow_id, organization_id)
        == "none"
    )


def test_workflow_permissions_fail_closed_without_rows():
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    db = FakeDb(
        first_values=[
            SimpleNamespace(id=workflow_id, organization_id=organization_id),
            _active_user(user_id),
            _active_organization(organization_id),
            _organization_member(),
        ],
        all_values=[[], []],
    )

    assert (
        get_effective_workflow_auth_state(db, user_id, workflow_id, organization_id)
        == "none"
    )


def test_direct_workflow_permission_is_additive_over_team_permission():
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    db = FakeDb(
        first_values=[
            SimpleNamespace(id=workflow_id, organization_id=organization_id),
            _active_user(user_id),
            _active_organization(organization_id),
            _organization_member(),
        ],
        all_values=[[("viewer",)], [("builder",)]],
    )

    assert (
        get_effective_workflow_auth_state(db, user_id, workflow_id, organization_id)
        == "builder"
    )


def test_workflow_permission_sqlalchemy_rows_are_unpacked_before_ranking():
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    db = FakeDb(
        first_values=[
            SimpleNamespace(id=workflow_id, organization_id=organization_id),
            _active_user(user_id),
            _active_organization(organization_id),
            _organization_member(),
        ],
        all_values=[
            [FakeSqlAlchemyRow("viewer")],
            [FakeSqlAlchemyRow("builder")],
        ],
    )

    assert (
        get_effective_workflow_auth_state(db, user_id, workflow_id, organization_id)
        == "builder"
    )


def test_weaker_direct_workflow_permission_does_not_lower_team_permission():
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    db = FakeDb(
        first_values=[
            SimpleNamespace(id=workflow_id, organization_id=organization_id),
            _active_user(user_id),
            _active_organization(organization_id),
            _organization_member(),
        ],
        all_values=[[("manager",)], [("viewer",)]],
    )

    assert (
        get_effective_workflow_auth_state(db, user_id, workflow_id, organization_id)
        == "manager"
    )


def test_audit_only_workflow_permission_does_not_override_valid_resource_permission():
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    db = FakeDb(
        first_values=[
            SimpleNamespace(id=workflow_id, organization_id=organization_id),
            _active_user(user_id),
            _active_organization(organization_id),
            _organization_member(),
        ],
        all_values=[[("viewer",), ("raw_auditor",)], []],
    )

    assert (
        get_effective_workflow_auth_state(db, user_id, workflow_id, organization_id)
        == "viewer"
    )


def test_llm_credential_use_requires_operator_or_builder():
    assert llm_credential_auth_state_allows("viewer", "use") is False
    assert llm_credential_auth_state_allows("operator", "use") is True
    assert llm_credential_auth_state_allows("builder", "use") is True


def test_knowledge_base_use_requires_operator_or_builder():
    assert knowledge_base_auth_state_allows("viewer", "use") is False
    assert knowledge_base_auth_state_allows("operator", "use") is True
    assert knowledge_base_auth_state_allows("builder", "use") is True


def test_knowledge_base_content_read_requires_builder():
    assert knowledge_base_auth_state_allows("operator", "content_read") is False
    assert knowledge_base_auth_state_allows("builder", "content_read") is True
    assert knowledge_base_auth_state_allows("manager", "content_read") is True


def test_knowledge_domain_permissions_are_additive_and_expired_rows_are_ignored():
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    db = FakeDb(
        first_values=[
            _active_user(user_id),
            _active_organization(organization_id),
            _organization_member(),
        ],
        all_values=[
            [("catalog_manage", None), ("sync_manage", datetime(2000, 1, 1, tzinfo=timezone.utc))],
            [("permission_delegate", None)],
        ],
    )

    assert get_effective_knowledge_domain_actions(
        db, user_id, organization_id
    ) == {"catalog_manage", "permission_delegate"}


def test_organization_manager_has_all_knowledge_domain_actions():
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    db = FakeDb(
        first_values=[
            _active_user(user_id),
            _active_organization(organization_id),
            _organization_member("manager"),
        ]
    )

    assert has_knowledge_domain_permission(
        db, user_id, organization_id, "sync_manage"
    ) is True


def test_unknown_knowledge_domain_action_fails_closed():
    assert has_knowledge_domain_permission(
        FakeDb(), uuid.uuid4(), uuid.uuid4(), "use"
    ) is False


def test_llm_credential_write_requires_manager():
    assert llm_credential_auth_state_allows("builder", "write") is False
    assert llm_credential_auth_state_allows("manager", "write") is True


def test_llm_credential_effective_permission_allows_direct_operator_use():
    user_id = uuid.uuid4()
    owner_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    credential_id = uuid.uuid4()
    db = FakeDb(
        first_values=[
            SimpleNamespace(
                id=credential_id,
                user_id=owner_id,
                organization_id=organization_id,
            ),
            _active_user(user_id),
            _active_organization(organization_id),
            _organization_member(),
        ],
        all_values=[[], [("operator",)]],
    )

    assert (
        get_effective_llm_credential_auth_state(
            db, user_id, credential_id, organization_id
        )
        == "operator"
    )


def test_org_scoped_llm_credential_mismatch_does_not_use_owner_fallback():
    user_id = uuid.uuid4()
    credential_id = uuid.uuid4()
    credential_organization_id = uuid.uuid4()
    requested_organization_id = uuid.uuid4()
    db = FakeDb(
        first_values=[
            SimpleNamespace(
                id=credential_id,
                user_id=user_id,
                organization_id=credential_organization_id,
            ),
        ]
    )

    assert (
        get_effective_llm_credential_auth_state(
            db,
            user_id,
            credential_id,
            requested_organization_id,
        )
        == "none"
    )


def test_non_active_owner_membership_blocks_legacy_owner_fallback_for_llm_credential():
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    credential_id = uuid.uuid4()

    for membership_state in ("invited", "suspended", "removed"):
        db = FakeDb(
            first_values=[
                SimpleNamespace(
                    id=credential_id,
                    user_id=uuid.uuid4(),
                    organization_id=organization_id,
                ),
                _active_user(user_id),
                _active_organization(organization_id, managed_by=user_id),
                _organization_member("manager", membership_state=membership_state),
            ]
        )

        assert (
            get_effective_llm_credential_auth_state(
                db, user_id, credential_id, organization_id
            )
            == "none"
        )


def test_llm_permission_sqlalchemy_rows_are_unpacked_before_ranking():
    user_id = uuid.uuid4()
    owner_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    credential_id = uuid.uuid4()
    db = FakeDb(
        first_values=[
            SimpleNamespace(
                id=credential_id,
                user_id=owner_id,
                organization_id=organization_id,
            ),
            _active_user(user_id),
            _active_organization(organization_id),
            _organization_member(),
        ],
        all_values=[
            [FakeSqlAlchemyRow("viewer")],
            [FakeSqlAlchemyRow("operator")],
        ],
    )

    assert (
        get_effective_llm_credential_auth_state(
            db, user_id, credential_id, organization_id
        )
        == "operator"
    )


def test_llm_credential_viewer_cannot_use():
    user_id = uuid.uuid4()
    owner_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    credential_id = uuid.uuid4()
    db = FakeDb(
        first_values=[
            SimpleNamespace(
                id=credential_id,
                user_id=owner_id,
                organization_id=organization_id,
            ),
            _active_user(user_id),
            _active_organization(organization_id),
            _organization_member(),
        ],
        all_values=[[], [("viewer",)]],
    )

    assert (
        has_llm_credential_permission(
            db, user_id, credential_id, "use", organization_id
        )
        is False
    )


def test_audit_only_llm_permission_does_not_grant_use():
    user_id = uuid.uuid4()
    owner_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    credential_id = uuid.uuid4()
    db = FakeDb(
        first_values=[
            SimpleNamespace(
                id=credential_id,
                user_id=owner_id,
                organization_id=organization_id,
            ),
            _active_user(user_id),
            _active_organization(organization_id),
            _organization_member(),
        ],
        all_values=[[("raw_auditor",)], []],
    )

    assert (
        has_llm_credential_permission(
            db, user_id, credential_id, "use", organization_id
        )
        is False
    )


def test_has_workflow_permission_uses_effective_auth_state():
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    db = FakeDb(
        first_values=[
            SimpleNamespace(id=workflow_id, organization_id=organization_id),
            _active_user(user_id),
            _active_organization(organization_id),
            _organization_member(),
        ],
        all_values=[[("operator",)], []],
    )

    assert (
        has_workflow_permission(db, user_id, workflow_id, "execute", organization_id)
        is True
    )


def test_organization_manager_gets_manager_for_knowledge_base():
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    knowledge_base_id = uuid.uuid4()
    db = FakeDb(
        first_values=[
            SimpleNamespace(id=knowledge_base_id, organization_id=organization_id),
            _active_user(user_id),
            _active_organization(organization_id),
            _organization_member("manager"),
        ]
    )

    assert (
        get_effective_knowledge_base_auth_state(
            db, user_id, knowledge_base_id, organization_id
        )
        == "manager"
    )


def test_team_knowledge_permission_grants_use():
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    knowledge_base_id = uuid.uuid4()
    auth_state_db = FakeDb(
        first_values=[
            SimpleNamespace(id=knowledge_base_id, organization_id=organization_id),
            _active_user(user_id),
            _active_organization(organization_id),
            _organization_member(),
        ],
        all_values=[[("operator",)], []],
    )

    assert (
        get_effective_knowledge_base_auth_state(
            auth_state_db, user_id, knowledge_base_id, organization_id
        )
        == "operator"
    )
    permission_db = FakeDb(
        first_values=[
            SimpleNamespace(id=knowledge_base_id, organization_id=organization_id),
            _active_user(user_id),
            _active_organization(organization_id),
            _organization_member(),
        ],
        all_values=[[("operator",)], []],
    )
    assert (
        has_knowledge_base_permission(
            permission_db, user_id, knowledge_base_id, "use", organization_id
        )
        is True
    )


def test_knowledge_base_owner_user_id_does_not_grant_without_permission_row():
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    knowledge_base_id = uuid.uuid4()
    db = FakeDb(
        first_values=[
            SimpleNamespace(
                id=knowledge_base_id,
                organization_id=organization_id,
                user_id=user_id,
            ),
            _active_user(user_id),
            _active_organization(organization_id),
            _organization_member(),
        ],
        all_values=[[], []],
    )

    assert (
        get_effective_knowledge_base_auth_state(
            db, user_id, knowledge_base_id, organization_id
        )
        == "none"
    )


def test_user_knowledge_permission_combines_with_team_permission():
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    knowledge_base_id = uuid.uuid4()
    db = FakeDb(
        first_values=[
            SimpleNamespace(id=knowledge_base_id, organization_id=organization_id),
            _active_user(user_id),
            _active_organization(organization_id),
            _organization_member(),
        ],
        all_values=[[("viewer",)], [("manager",)]],
    )

    assert (
        get_effective_knowledge_base_auth_state(
            db, user_id, knowledge_base_id, organization_id
        )
        == "manager"
    )


def test_non_active_knowledge_base_does_not_grant_effective_permission():
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    knowledge_base_id = uuid.uuid4()

    for lifecycle_state in ("archived", "deleted"):
        knowledge_base = SimpleNamespace(
            id=knowledge_base_id,
            organization_id=organization_id,
            lifecycle_state=lifecycle_state,
        )
        db = FakeDb(
            first_values=[knowledge_base],
            all_values=[[("manager",)], [("manager",)]],
        )

        assert (
            get_effective_knowledge_base_auth_state(
                db, user_id, knowledge_base_id, organization_id
            )
            == "none"
        )

        permission_db = FakeDb(
            first_values=[knowledge_base],
            all_values=[[("manager",)], [("manager",)]],
        )
        assert (
            has_knowledge_base_permission(
                permission_db, user_id, knowledge_base_id, "use", organization_id
            )
            is False
        )


def test_org_scoped_knowledge_base_requires_knowledge_base_organization_id():
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    knowledge_base_id = uuid.uuid4()
    db = FakeDb(
        first_values=[
            SimpleNamespace(
                id=knowledge_base_id,
                organization_id=None,
                user_id=user_id,
            ),
        ],
        all_values=[[("manager",)]],
    )

    assert (
        get_effective_knowledge_base_auth_state(
            db, user_id, knowledge_base_id, organization_id
        )
        == "none"
    )

    permission_db = FakeDb(
        first_values=[
            SimpleNamespace(
                id=knowledge_base_id,
                organization_id=None,
                user_id=user_id,
            ),
        ],
        all_values=[[("manager",)]],
    )
    assert (
        has_knowledge_base_permission(
            permission_db, user_id, knowledge_base_id, "use", organization_id
        )
        is False
    )
