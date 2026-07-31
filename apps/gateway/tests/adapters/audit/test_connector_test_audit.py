from uuid import uuid4

from apps.gateway.adapters.audit import connector_test as audit_module
from apps.gateway.adapters.audit.connector_test import ConnectorTestAuditRecorder
from apps.gateway.application.connectors.models import (
    ConnectorTestCommand,
    ConnectorTestResult,
)


def test_audit_records_only_safe_connector_test_metadata(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_record_audit(action: str, category: str, **kwargs) -> None:
        captured.update({"action": action, "category": category, **kwargs})

    monkeypatch.setattr(audit_module, "record_audit", fake_record_audit)
    command = ConnectorTestCommand(
        organization_id=uuid4(),
        actor_id=uuid4(),
        network_address="203.0.113.9",
        host="sensitive-host.example.com",
        port=5432,
        database="sensitive-db",
        username="sensitive-user",
        password="sensitive-password",
    )

    ConnectorTestAuditRecorder().record(
        command,
        ConnectorTestResult(
            success=False,
            message="safe",
            reason_code="connector.connection_failed",
        ),
        "1-5s",
    )

    assert captured["action"] == "connection.test"
    assert captured["actor_id"] == command.actor_id
    assert captured["status"] == "failure"
    assert captured["metadata"] == {
        "organization_id": str(command.organization_id),
        "result": "failure",
        "reason_code": "connector.connection_failed",
        "duration_bucket": "1-5s",
    }
    serialized = repr(captured)
    for secret in (
        command.network_address,
        command.host,
        command.database,
        command.username,
        command.password,
    ):
        assert secret not in serialized
