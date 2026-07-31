import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from apps.gateway.api.v1.endpoints import knowledge as knowledge_endpoint
from apps.gateway.auth.dependencies import get_current_user
from apps.gateway.main import app
from apps.shared.db.models.audit_log import AuditLog
from apps.shared.db.models.knowledge import KnowledgeCollection
from apps.shared.db.models.organization import Organization
from apps.shared.db.models.organization_membership import OrganizationMembership
from apps.shared.db.models.team import (
    Team,
    TeamKnowledgeCollectionPermission,
    TeamKnowledgeDomainPermission,
    TeamMembership,
    UserKnowledgeCollectionPermission,
    UserKnowledgeDomainPermission,
)
from apps.shared.db.models.user import User
from apps.shared.tests.helpers.disposable_postgres import (
    DisposablePostgresConfig,
    DisposablePostgresConfigurationError,
    quote_disposable_database_name,
)


ROOT_DIR = Path(__file__).resolve().parents[4]
RUN_ENV = "NODEASE_RUN_DISPOSABLE_DB_TEST"
DB_PREFIX = "mba268_create"

pytestmark = pytest.mark.skipif(
    os.getenv(RUN_ENV) != "1",
    reason=f"set {RUN_ENV}=1 to run disposable PostgreSQL create integration",
)


def _run_migrations(database: str, config: DisposablePostgresConfig) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "apps/shared/alembic.ini",
            "upgrade",
            "heads",
        ],
        cwd=ROOT_DIR,
        env=config.subprocess_environment(database=database, root_dir=ROOT_DIR),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
    )
    if completed.returncode != 0:
        raise pytest.fail.Exception(
            "disposable PostgreSQL migration failed; output omitted",
            pytrace=False,
        )


def _enable_vector_extension(database: str, config: DisposablePostgresConfig) -> None:
    extension_engine = create_engine(
        config.database_url(database),
        isolation_level="AUTOCOMMIT",
    )
    try:
        with extension_engine.connect() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    finally:
        extension_engine.dispose()


@pytest.fixture(scope="module")
def postgres_engine():
    try:
        config = DisposablePostgresConfig.from_environment()
    except DisposablePostgresConfigurationError:
        raise pytest.fail.Exception(
            "disposable PostgreSQL connection settings are not safely configured",
            pytrace=False,
        ) from None

    database = f"{DB_PREFIX}_{uuid.uuid4().hex[:12]}"
    quoted_database = quote_disposable_database_name(database, prefix=DB_PREFIX)
    admin_engine = create_engine(
        config.database_url(config.maintenance_database),
        isolation_level="AUTOCOMMIT",
    )
    database_created = False
    test_engine = None

    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f"CREATE DATABASE {quoted_database}"))
        database_created = True
        _enable_vector_extension(database, config)
        _run_migrations(database, config)
        test_engine = create_engine(config.database_url(database), pool_pre_ping=True)
        yield test_engine
    except OperationalError:
        raise pytest.fail.Exception(
            "disposable PostgreSQL is unavailable or rejected the connection; "
            "connection details omitted",
            pytrace=False,
        ) from None
    finally:
        if test_engine is not None:
            test_engine.dispose()
        if database_created:
            try:
                with admin_engine.connect() as connection:
                    connection.execute(
                        text(
                            """
                            SELECT pg_terminate_backend(pid)
                            FROM pg_stat_activity
                            WHERE datname = :database
                              AND pid <> pg_backend_pid()
                            """
                        ),
                        {"database": database},
                    )
                    connection.execute(
                        text(f"DROP DATABASE IF EXISTS {quoted_database}")
                    )
            except OperationalError:
                raise pytest.fail.Exception(
                    "disposable PostgreSQL cleanup could not connect; "
                    "connection details omitted",
                    pytrace=False,
                ) from None
        admin_engine.dispose()


@pytest.fixture
def db_session(postgres_engine):
    connection = postgres_engine.connect()
    transaction = connection.begin()
    db = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield db
    finally:
        db.close()
        transaction.rollback()
        connection.close()


def _user(label: str) -> User:
    suffix = uuid.uuid4().hex
    return User(
        email=f"mba268-{label}-{suffix}@example.invalid",
        name=f"MBA-268 {label}",
        social_provider="local",
    )


def _delegated_scope(db: Session, grant_source: str):
    owner = _user("owner")
    actor = _user(grant_source)
    db.add_all([owner, actor])
    db.flush()

    organization = Organization(
        name=f"MBA-268 {grant_source} {uuid.uuid4().hex}",
        created_by=owner.id,
        managed_by=owner.id,
    )
    db.add(organization)
    db.flush()
    db.add(
        OrganizationMembership(
            organization_id=organization.id,
            user_id=actor.id,
            membership_state="active",
            organization_auth_state="member",
            invited_by=owner.id,
        )
    )

    if grant_source == "user":
        db.add(
            UserKnowledgeDomainPermission(
                organization_id=organization.id,
                user_id=actor.id,
                permission_action="catalog_manage",
                assigned_by=owner.id,
            )
        )
    else:
        team = Team(
            organization_id=organization.id,
            name=f"MBA-268 team {uuid.uuid4().hex}",
            created_by=owner.id,
            managed_by=owner.id,
            is_active=True,
        )
        db.add(team)
        db.flush()
        db.add_all(
            [
                TeamMembership(
                    grantee_organization_id=organization.id,
                    user_id=actor.id,
                    team_id=team.id,
                    assigned_by=owner.id,
                ),
                TeamKnowledgeDomainPermission(
                    organization_id=organization.id,
                    team_id=team.id,
                    permission_action="catalog_manage",
                    assigned_by=owner.id,
                ),
            ]
        )
    db.flush()
    return organization, actor


def _client_for(db: Session, actor: User) -> TestClient:
    app.dependency_overrides[knowledge_endpoint.get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: actor
    return TestClient(app)


@pytest.mark.parametrize("grant_source", ["user", "team"])
def test_catalog_delegate_capability_creates_private_collection_with_audit(
    db_session,
    grant_source,
):
    organization, actor = _delegated_scope(db_session, grant_source)
    client = _client_for(db_session, actor)
    headers = {"X-Organization-Id": str(organization.id)}
    collection_name = f"MBA-268 {grant_source} collection {uuid.uuid4().hex}"
    try:
        domain_response = client.get(
            "/api/v1/knowledge/domain-capabilities",
            headers=headers,
        )
        list_response = client.get(
            "/api/v1/knowledge/collections",
            headers=headers,
        )
        create_response = client.post(
            "/api/v1/knowledge/collections",
            headers=headers,
            json={
                "name": collection_name,
                "description": "delegated private catalog",
                "safe_metadata": {
                    "safe_label": f"MBA-268 {grant_source} safe label",
                    "visibility": "public",
                },
            },
        )
    finally:
        app.dependency_overrides = {}

    assert domain_response.status_code == 200
    assert domain_response.json() == {
        "actions": ["catalog_manage"],
        "can_manage_domain_permissions": False,
        "can_create_collection": True,
        "can_delegate_permissions": False,
        "can_manage_lifecycle": False,
        "can_manage_sync": False,
        "can_change_public_visibility": False,
    }
    assert list_response.status_code == 200
    assert list_response.json()["can_create_collection"] is True
    assert list_response.json()["can_change_public_visibility"] is False

    assert create_response.status_code == 201
    body = create_response.json()
    assert body["organization_id"] == str(organization.id)
    assert body["visibility"] == "private"
    assert body["is_system_managed"] is False
    assert body["sync_state"] == "manual"
    assert body["lifecycle_state"] == "active"
    assert body["can_read"] is False
    assert body["can_route"] is False
    assert body["can_manage"] is False
    assert body["can_sync"] is False
    assert body["safe_metadata"] == {"safe_label": f"MBA-268 {grant_source} safe label"}

    collection_id = uuid.UUID(body["id"])
    collection = db_session.get(KnowledgeCollection, collection_id)
    assert collection is not None
    assert collection.created_by == actor.id
    assert (
        db_session.query(TeamKnowledgeCollectionPermission)
        .filter(
            TeamKnowledgeCollectionPermission.knowledge_collection_id == collection_id
        )
        .count()
        == 0
    )
    assert (
        db_session.query(UserKnowledgeCollectionPermission)
        .filter(
            UserKnowledgeCollectionPermission.knowledge_collection_id == collection_id
        )
        .count()
        == 0
    )

    audit = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.action == "knowledge.collection.created",
            AuditLog.target_type == "knowledge_collection",
            AuditLog.target_id == str(collection_id),
        )
        .one()
    )
    assert audit.actor_id == actor.id
    assert audit.audit_metadata == {
        "organization_id": str(organization.id),
        "visibility": "private",
    }
    assert "delegated private catalog" not in str(audit.audit_metadata)


def test_catalog_delegate_cannot_change_collection_to_public(db_session):
    organization, actor = _delegated_scope(db_session, "user")
    client = _client_for(db_session, actor)
    headers = {"X-Organization-Id": str(organization.id)}
    try:
        created = client.post(
            "/api/v1/knowledge/collections",
            headers=headers,
            json={"name": f"MBA-268 private {uuid.uuid4().hex}"},
        )
        assert created.status_code == 201
        response = client.post(
            f"/api/v1/knowledge/collections/{created.json()['id']}/visibility",
            headers=headers,
            json={
                "visibility": "public",
                "acknowledged_public_runtime_exposure": True,
            },
        )
    finally:
        app.dependency_overrides = {}

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "permission.denied"
