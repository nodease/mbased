from __future__ import annotations

from typing import Protocol

from .models import AdmissionLease, ConnectorTestCommand, ConnectorTestResult


class ConnectorTestAdmissionPort(Protocol):
    async def acquire(self, command: ConnectorTestCommand) -> AdmissionLease: ...

    async def renew(self, lease: AdmissionLease) -> None: ...

    async def release(self, lease: AdmissionLease) -> None: ...


class ConnectorProbeReservationPort(Protocol):
    async def probe(self, command: ConnectorTestCommand) -> bool: ...

    def release(self) -> None: ...


class ConnectorProbePort(Protocol):
    def reserve(self) -> ConnectorProbeReservationPort: ...


class ConnectorTestAuditPort(Protocol):
    def record(
        self,
        command: ConnectorTestCommand,
        result: ConnectorTestResult,
        duration_bucket: str,
    ) -> None: ...


__all__ = [
    "ConnectorProbePort",
    "ConnectorProbeReservationPort",
    "ConnectorTestAdmissionPort",
    "ConnectorTestAuditPort",
]
