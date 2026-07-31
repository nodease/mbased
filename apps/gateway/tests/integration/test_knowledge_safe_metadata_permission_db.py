import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from apps.gateway.api.v1.endpoints import knowledge as knowledge_endpoint
from apps.gateway.auth.dependencies import get_current_user
from apps.gateway.main import app
from apps.shared.db.models.knowledge import KnowledgeBase
from apps.shared.db.models.organization import Organization
from apps.shared.db.models.organization_membership import OrganizationMembership
from apps.shared.db.models.team import UserKnowledgePermission
from apps.shared.db.models.user import User
from apps.shared.db.session import engine


@pytest.fixture
def db_session():
    try:
        connection = engine.connect()
    except OperationalError:
        pytest.skip("PostgreSQL integration database is unavailable")
    transaction = connection.begin()
    db = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield db
    finally:
        db.close()
        transaction.rollback()
        connection.close()


def _user(prefix: str) -> User:
    suffix = uuid.uuid4().hex
    return User(
        email=f"{prefix}-{suffix}@example.invalid",
        name=f"Knowledge Safe Metadata {prefix}",
        social_provider="local",
    )


@pytest.fixture
def safe_metadata_scope(db_session):
    owner = _user("owner")
    manager = _user("manager")
    operator = _user("operator")
    db_session.add_all([owner, manager, operator])
    db_session.flush()

    organization = Organization(
        name=f"Knowledge Safe Metadata {uuid.uuid4().hex}",
        created_by=owner.id,
        managed_by=owner.id,
    )
    db_session.add(organization)
    db_session.flush()
    db_session.add_all(
        [
            OrganizationMembership(
                organization_id=organization.id,
                user_id=owner.id,
                membership_state="active",
                organization_auth_state="member",
            ),
            OrganizationMembership(
                organization_id=organization.id,
                user_id=manager.id,
                membership_state="active",
                organization_auth_state="member",
                invited_by=owner.id,
            ),
            OrganizationMembership(
                organization_id=organization.id,
                user_id=operator.id,
                membership_state="active",
                organization_auth_state="member",
                invited_by=owner.id,
            ),
        ]
    )
    knowledge_base = KnowledgeBase(
        organization_id=organization.id,
        user_id=owner.id,
        name="People Ops",
        description="Onboarding and benefits",
        safe_metadata={},
    )
    db_session.add(knowledge_base)
    db_session.flush()
    db_session.add_all(
        [
            UserKnowledgePermission(
                grantee_organization_id=organization.id,
                user_id=manager.id,
                knowledge_base_id=knowledge_base.id,
                auth_state="manager",
                assigned_by=owner.id,
            ),
            UserKnowledgePermission(
                grantee_organization_id=organization.id,
                user_id=operator.id,
                knowledge_base_id=knowledge_base.id,
                auth_state="operator",
                assigned_by=owner.id,
            ),
        ]
    )
    db_session.flush()
    return organization, knowledge_base, owner, manager, operator


def _client_for(db_session, user):
    app.dependency_overrides[knowledge_endpoint.get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def test_kb_manager_can_read_detail_with_bounded_capabilities(
    db_session, safe_metadata_scope
):
    organization, knowledge_base, _, manager, _ = safe_metadata_scope
    client = _client_for(db_session, manager)
    try:
        response = client.get(
            f"/api/v1/knowledge/{knowledge_base.id}",
            headers={"X-Organization-Id": str(organization.id)},
        )
    finally:
        app.dependency_overrides = {}

    assert response.status_code == 200
    assert response.json()["can_edit_settings"] is True
    assert response.json()["can_manage_safe_metadata"] is True


def test_kb_manager_can_update_only_sanitized_safe_metadata(
    db_session, safe_metadata_scope
):
    organization, knowledge_base, _, manager, _ = safe_metadata_scope
    client = _client_for(db_session, manager)
    try:
        response = client.patch(
            f"/api/v1/knowledge/{knowledge_base.id}/safe-metadata",
            headers={"X-Organization-Id": str(organization.id)},
            json={
                "safe_label": "People Ops",
                "kb_safe_topics": ["onboarding", "https://private.invalid/source"],
                "raw_source_url": "https://private.invalid/raw",
            },
        )
    finally:
        app.dependency_overrides = {}

    assert response.status_code == 200
    assert response.json()["safe_metadata"] == {
        "safe_label": "People Ops",
        "kb_safe_topics": ["onboarding"],
    }
    db_session.refresh(knowledge_base)
    assert knowledge_base.safe_metadata == response.json()["safe_metadata"]


def test_kb_operator_cannot_update_safe_metadata(db_session, safe_metadata_scope):
    organization, knowledge_base, _, _, operator = safe_metadata_scope
    client = _client_for(db_session, operator)
    try:
        response = client.patch(
            f"/api/v1/knowledge/{knowledge_base.id}/safe-metadata",
            headers={"X-Organization-Id": str(organization.id)},
            json={"safe_label": "Denied"},
        )
    finally:
        app.dependency_overrides = {}

    assert response.status_code == 403
    db_session.refresh(knowledge_base)
    assert knowledge_base.safe_metadata == {}


def test_kb_manager_can_use_resource_settings_patch(db_session, safe_metadata_scope):
    organization, knowledge_base, _, manager, _ = safe_metadata_scope
    client = _client_for(db_session, manager)
    try:
        response = client.patch(
            f"/api/v1/knowledge/{knowledge_base.id}",
            headers={"X-Organization-Id": str(organization.id)},
            json={"name": "Manager rename"},
        )
    finally:
        app.dependency_overrides = {}

    assert response.status_code == 204
    db_session.refresh(knowledge_base)
    assert knowledge_base.name == "Manager rename"


def test_generic_settings_patch_rejects_safe_metadata_payload(
    db_session, safe_metadata_scope
):
    organization, knowledge_base, owner, _, _ = safe_metadata_scope
    client = _client_for(db_session, owner)
    try:
        response = client.patch(
            f"/api/v1/knowledge/{knowledge_base.id}",
            headers={"X-Organization-Id": str(organization.id)},
            json={"safe_metadata": {"safe_label": "Wrong route"}},
        )
    finally:
        app.dependency_overrides = {}

    assert response.status_code == 422
    db_session.refresh(knowledge_base)
    assert knowledge_base.safe_metadata == {}
