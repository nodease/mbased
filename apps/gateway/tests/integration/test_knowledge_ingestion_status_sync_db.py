import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from apps.gateway.api.v1.endpoints import knowledge as knowledge_endpoint
from apps.gateway.auth.dependencies import get_current_user
from apps.gateway.main import app
from apps.gateway.services.ingestion.service import PROCESSING_START_TIMEOUT_MESSAGE
from apps.shared.db.models.knowledge import Document, KnowledgeBase
from apps.shared.db.models.organization import Organization
from apps.shared.db.models.organization_membership import OrganizationMembership
from apps.shared.db.models.user import User
from apps.shared.db.session import engine


PUBLIC_PROCESSING_FAILURE_MESSAGE = (
    "Document processing failed. You can retry the document."
)


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


def test_detail_api_synchronizes_stale_indexing_status_with_document_row(db_session):
    user = User(
        email=f"ingestion-sync-{uuid.uuid4().hex}@example.invalid",
        name="Knowledge Ingestion Status Sync",
        social_provider="local",
    )
    db_session.add(user)
    db_session.flush()
    organization = Organization(
        name=f"Knowledge Ingestion Status Sync {uuid.uuid4().hex}",
        created_by=user.id,
        managed_by=user.id,
    )
    db_session.add(organization)
    db_session.flush()
    db_session.add(
        OrganizationMembership(
            organization_id=organization.id,
            user_id=user.id,
            membership_state="active",
            organization_auth_state="manager",
        )
    )
    knowledge_base = KnowledgeBase(
        organization_id=organization.id,
        user_id=user.id,
        name="Stale indexing KB",
        safe_metadata={},
    )
    db_session.add(knowledge_base)
    db_session.flush()
    stale_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    document = Document(
        knowledge_base_id=knowledge_base.id,
        filename="stale.md",
        status="indexing",
        meta_info={
            "processing_enqueued_at": stale_at.isoformat(),
            "progress": 0,
        },
        created_at=stale_at,
        updated_at=stale_at,
    )
    db_session.add(document)
    db_session.flush()

    app.dependency_overrides[knowledge_endpoint.get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        response = TestClient(app).get(
            f"/api/v1/knowledge/{knowledge_base.id}",
            headers={"X-Organization-Id": str(organization.id)},
        )
    finally:
        app.dependency_overrides = {}

    assert response.status_code == 200
    response_document = response.json()["documents"][0]
    assert response_document["id"] == str(document.id)
    assert response_document["status"] == "failed"
    assert response_document["error_message"] == PUBLIC_PROCESSING_FAILURE_MESSAGE
    assert PROCESSING_START_TIMEOUT_MESSAGE not in response_document["error_message"]
    db_session.refresh(document)
    assert document.status == "failed"
    assert document.error_message == PROCESSING_START_TIMEOUT_MESSAGE
