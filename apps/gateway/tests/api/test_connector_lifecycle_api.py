import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.gateway.api.v1.endpoints import connectors as connector_endpoint
from apps.gateway.services.connection_lifecycle_service import (
    ConnectionLifecycleBusy,
    ConnectionLifecycleHidden,
    ConnectionLifecycleInUse,
    ConnectionLifecycleUnavailable,
)


@pytest.fixture
def connector_client(monkeypatch):
    app = FastAPI()
    app.include_router(connector_endpoint.router, prefix="/api/v1/connectors")
    db = SimpleNamespace()
    user = SimpleNamespace(id=uuid.uuid4())
    app.dependency_overrides[connector_endpoint.get_db] = lambda: db
    app.dependency_overrides[connector_endpoint.get_current_user] = lambda: user
    monkeypatch.setattr(
        "apps.gateway.utils.audit.record_audit",
        lambda **_kwargs: None,
    )
    with TestClient(app) as client:
        yield client, db, user


@pytest.mark.parametrize(
    ("error", "status_code", "reason_code"),
    [
        (ConnectionLifecycleHidden(), 404, "resource.hidden"),
        (ConnectionLifecycleInUse(), 409, "connection.in_use"),
        (ConnectionLifecycleBusy(), 503, "connection.reference_busy"),
        (
            ConnectionLifecycleUnavailable(),
            503,
            "connection.delete_unavailable",
        ),
    ],
)
def test_delete_connection_maps_safe_lifecycle_errors(
    connector_client,
    monkeypatch,
    error,
    status_code,
    reason_code,
):
    client, _db, _user = connector_client

    class _Service:
        def __init__(self, _db):
            pass

        def delete_unreferenced_connection(self, **_kwargs):
            raise error

    monkeypatch.setattr(connector_endpoint, "ConnectionLifecycleService", _Service)

    response = client.delete(f"/api/v1/connectors/{uuid.uuid4()}")

    assert response.status_code == status_code
    assert response.json()["detail"] == {"reason_code": reason_code}


def test_delete_connection_returns_no_content_after_owner_cleanup(
    connector_client,
    monkeypatch,
):
    client, dependency_db, user = connector_client
    captured = {}

    class _Service:
        def __init__(self, db):
            captured["db"] = db

        def delete_unreferenced_connection(self, **kwargs):
            captured["kwargs"] = kwargs

    monkeypatch.setattr(connector_endpoint, "ConnectionLifecycleService", _Service)
    connection_id = uuid.uuid4()

    response = client.delete(f"/api/v1/connectors/{connection_id}")

    assert response.status_code == 204
    assert response.content == b""
    assert captured == {
        "db": dependency_db,
        "kwargs": {"connection_id": connection_id, "owner_id": user.id},
    }


class _SchemaOwnerQuery:
    def __init__(self, owner_row):
        self.owner_row = owner_row

    def filter(self, *_args):
        return self

    def one_or_none(self):
        return self.owner_row


class _SchemaOwnerDb:
    def __init__(self, owner_row):
        self.owner_row = owner_row
        self.rollback_count = 0

    def query(self, *_args):
        return _SchemaOwnerQuery(self.owner_row)

    def rollback(self):
        self.rollback_count += 1


def test_schema_management_precheck_rolls_back_before_runtime_snapshot():
    user_id = uuid.uuid4()
    connection_id = uuid.uuid4()
    db = _SchemaOwnerDb((user_id,))

    resolved = connector_endpoint._authorize_connection_schema_management(
        db,
        connection_id=str(connection_id),
        current_user_id=user_id,
    )

    assert resolved == connection_id
    assert db.rollback_count == 1


def test_schema_management_precheck_preserves_non_owner_403_contract():
    db = _SchemaOwnerDb((uuid.uuid4(),))

    with pytest.raises(Exception) as exc_info:
        connector_endpoint._authorize_connection_schema_management(
            db,
            connection_id=str(uuid.uuid4()),
            current_user_id=uuid.uuid4(),
        )

    assert getattr(exc_info.value, "status_code", None) == 403
    assert exc_info.value.detail == "Not authorized"
    assert db.rollback_count == 1


@pytest.mark.asyncio
async def test_schema_management_caches_user_id_before_precheck_rollback(monkeypatch):
    user_id = uuid.uuid4()
    connection_id = uuid.uuid4()
    db = _SchemaOwnerDb((user_id,))
    captured = {}

    class ExpiringUser:
        def __init__(self):
            self.read_count = 0

        @property
        def id(self):
            self.read_count += 1
            if self.read_count > 1:
                raise RuntimeError("expired ORM attribute was accessed")
            return user_id

    class FakeSnapshot:
        adapter_type = "postgres"

        @staticmethod
        def to_connector_config():
            return {"safe": "config"}

    class FakeSnapshotProvider:
        def __init__(self, session_factory):
            captured["session_factory"] = session_factory

        def load(self, selected_connection_id, *, execution_subject_user_id):
            captured["connection_id"] = selected_connection_id
            captured["execution_subject_user_id"] = execution_subject_user_id
            return FakeSnapshot()

    class FakeConnector:
        @staticmethod
        def get_schema_info(config):
            captured["config"] = config
            return ["public.example"]

    user = ExpiringUser()
    monkeypatch.setattr(
        connector_endpoint,
        "ConnectionRuntimeSnapshotProvider",
        FakeSnapshotProvider,
    )
    monkeypatch.setattr(
        connector_endpoint,
        "_build_workflow_connector",
        lambda _connector_class: FakeConnector(),
    )

    result = await connector_endpoint.get_connection_schema(
        connection_id=str(connection_id),
        db=db,
        current_user=user,
    )

    assert result == {"tables": ["public.example"]}
    assert user.read_count == 1
    assert db.rollback_count == 1
    assert captured["connection_id"] == connection_id
    assert captured["execution_subject_user_id"] == user_id
    assert captured["config"] == {"safe": "config"}
