from __future__ import annotations

from apps.gateway.application.connectors.models import (
    ConnectorTestCommand,
    ConnectorTestResult,
)
from apps.shared.audit.actions import AuditAction
from apps.shared.audit.logger import record_audit


class ConnectorTestAuditRecorder:
    def record(
        self,
        command: ConnectorTestCommand,
        result: ConnectorTestResult,
        duration_bucket: str,
    ) -> None:
        record_audit(
            AuditAction.CONNECTION_TEST,
            "action",
            actor_id=command.actor_id,
            target_type="connection_test",
            status="success" if result.success else "failure",
            metadata={
                "organization_id": str(command.organization_id),
                "result": "success" if result.success else "failure",
                "reason_code": result.reason_code,
                "duration_bucket": duration_bucket,
            },
        )


__all__ = ["ConnectorTestAuditRecorder"]
