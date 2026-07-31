import asyncio
from dataclasses import dataclass, field
from uuid import uuid4

import pytest

from apps.gateway.application.connectors.errors import (
    ConnectorProbeCapacityExceeded,
    ConnectorProbeFailed,
    ConnectorTargetNotAllowed,
    ConnectorTestAdmissionUnavailable,
    ConnectorTestBusy,
    ConnectorTestRateLimited,
)
from apps.gateway.application.connectors.models import (
    AdmissionLease,
    ConnectorTestCommand,
    ConnectorTestPolicy,
    ConnectorTestResult,
)
from apps.gateway.application.connectors.test_connection import (
    TestConnectorConnection as ConnectorConnectionUseCase,
)


def command(*, ssh_enabled: bool = False, port: int = 5432) -> ConnectorTestCommand:
    return ConnectorTestCommand(
        organization_id=uuid4(),
        actor_id=uuid4(),
        network_address="203.0.113.10",
        host="db.example.com",
        port=port,
        database="app",
        username="app-user",
        password="placeholder-secret",
        ssh_enabled=ssh_enabled,
    )


@dataclass
class FakeAdmission:
    acquire_error: Exception | None = None
    release_error: Exception | None = None
    renew_error: Exception | None = None
    acquired: list[ConnectorTestCommand] = field(default_factory=list)
    released: list[AdmissionLease] = field(default_factory=list)
    renewed: list[AdmissionLease] = field(default_factory=list)
    release_event: asyncio.Event = field(default_factory=asyncio.Event)

    async def acquire(self, value: ConnectorTestCommand) -> AdmissionLease:
        self.acquired.append(value)
        if self.acquire_error:
            raise self.acquire_error
        return AdmissionLease("lease")

    async def release(self, lease: AdmissionLease) -> None:
        self.released.append(lease)
        self.release_event.set()
        if self.release_error:
            raise self.release_error

    async def renew(self, lease: AdmissionLease) -> None:
        self.renewed.append(lease)
        if self.renew_error:
            raise self.renew_error


@dataclass
class FakeProbe:
    result: bool = True
    error: Exception | None = None
    reserve_error: Exception | None = None
    blocker: asyncio.Event | None = None
    calls: list[ConnectorTestCommand] = field(default_factory=list)
    cancelled: bool = False
    reservations: int = 0
    releases: int = 0

    def reserve(self):
        if self.reserve_error:
            raise self.reserve_error
        self.reservations += 1
        return self

    def release(self) -> None:
        self.releases += 1

    async def probe(self, value: ConnectorTestCommand) -> bool:
        self.calls.append(value)
        try:
            if self.blocker is not None:
                await self.blocker.wait()
            if self.error:
                raise self.error
            return self.result
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        finally:
            self.release()


@dataclass
class FakeAudit:
    error: Exception | None = None
    calls: list[tuple[ConnectorTestCommand, ConnectorTestResult, str]] = field(
        default_factory=list
    )

    def record(
        self,
        value: ConnectorTestCommand,
        result: ConnectorTestResult,
        duration_bucket: str,
    ) -> None:
        self.calls.append((value, result, duration_bucket))
        if self.error:
            raise self.error


def use_case(
    admission: FakeAdmission,
    probe: FakeProbe,
    audit: FakeAudit,
    *,
    allowed_ports: frozenset[int] = frozenset({5432}),
    response_timeout: float = 10,
    probe_hard_timeout: float = 20,
) -> ConnectorConnectionUseCase:
    policy = ConnectorTestPolicy(
        allowed_ports=allowed_ports,
        connect_timeout_seconds=0.001,
        statement_timeout_seconds=0.001,
        response_timeout_seconds=response_timeout,
        probe_hard_timeout_seconds=probe_hard_timeout,
        redis_operation_timeout_seconds=0.001,
        lease_ttl_seconds=30,
    )
    return ConnectorConnectionUseCase(admission, probe, audit, policy)


@pytest.mark.asyncio
async def test_success_releases_lease_and_records_safe_result() -> None:
    admission = FakeAdmission()
    probe = FakeProbe()
    audit = FakeAudit()

    result = await use_case(admission, probe, audit).execute(command())

    assert result.success is True
    assert len(admission.acquired) == 1
    assert admission.released == [AdmissionLease("lease")]
    assert len(probe.calls) == 1
    assert audit.calls[0][1] == result


@pytest.mark.asyncio
async def test_ssh_is_rejected_before_admission_probe_and_audit() -> None:
    admission = FakeAdmission()
    probe = FakeProbe()
    audit = FakeAudit()

    result = await use_case(admission, probe, audit).execute(command(ssh_enabled=True))

    assert result.reason_code == "connector.ssh_probe_not_supported"
    assert admission.acquired == []
    assert probe.calls == []
    assert audit.calls == []


@pytest.mark.asyncio
async def test_disallowed_port_is_rejected_before_admission_probe_and_audit() -> None:
    admission = FakeAdmission()
    probe = FakeProbe()
    audit = FakeAudit()

    result = await use_case(admission, probe, audit).execute(command(port=55432))

    assert result.reason_code == "connector.target_not_allowed"
    assert admission.acquired == []
    assert probe.calls == []
    assert audit.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("probe_error", "reason_code"),
    [
        (ConnectorTargetNotAllowed(), "connector.target_not_allowed"),
        (ConnectorProbeFailed(), "connector.connection_failed"),
        (RuntimeError("raw-driver-detail"), "connector.connection_failed"),
    ],
)
async def test_probe_errors_are_normalized_and_lease_is_released(
    probe_error: Exception,
    reason_code: str,
) -> None:
    admission = FakeAdmission()
    probe = FakeProbe(error=probe_error)
    audit = FakeAudit()

    result = await use_case(admission, probe, audit).execute(command())

    assert result.reason_code == reason_code
    assert "raw-driver-detail" not in result.message
    assert len(admission.released) == 1
    assert audit.calls[0][1].reason_code == reason_code


@pytest.mark.asyncio
async def test_timeout_keeps_lease_until_actual_probe_completion() -> None:
    blocker = asyncio.Event()
    admission = FakeAdmission()
    probe = FakeProbe(blocker=blocker)
    audit = FakeAudit()

    result = await use_case(
        admission,
        probe,
        audit,
        response_timeout=0.01,
    ).execute(command())

    assert result.reason_code == "connector.connection_timeout"
    assert admission.released == []
    blocker.set()
    await asyncio.wait_for(admission.release_event.wait(), timeout=1)
    assert len(admission.released) == 1


@pytest.mark.asyncio
async def test_hard_timeout_stops_heartbeat_and_releases_lease_once() -> None:
    blocker = asyncio.Event()
    admission = FakeAdmission()
    probe = FakeProbe(blocker=blocker)
    audit = FakeAudit()
    policy = ConnectorTestPolicy(
        connect_timeout_seconds=0.001,
        statement_timeout_seconds=0.001,
        response_timeout_seconds=0.01,
        probe_hard_timeout_seconds=0.08,
        redis_operation_timeout_seconds=0.001,
        lease_ttl_seconds=0.03,
    )

    result = await ConnectorConnectionUseCase(
        admission,
        probe,
        audit,
        policy,
    ).execute(command())

    assert result.reason_code == "connector.connection_timeout"
    assert admission.released == []
    await asyncio.wait_for(admission.release_event.wait(), timeout=1)
    assert probe.cancelled is True
    assert admission.released == [AdmissionLease("lease")]
    renewals_after_release = len(admission.renewed)
    await asyncio.sleep(0.05)
    assert len(admission.renewed) == renewals_after_release


@pytest.mark.asyncio
async def test_local_capacity_failure_does_not_consume_admission_rate() -> None:
    admission = FakeAdmission()
    probe = FakeProbe(reserve_error=ConnectorProbeCapacityExceeded())
    audit = FakeAudit()

    with pytest.raises(ConnectorTestBusy):
        await use_case(admission, probe, audit).execute(command())

    assert admission.acquired == []
    assert admission.released == []
    assert probe.calls == []
    assert audit.calls == []


@pytest.mark.asyncio
async def test_admission_failure_never_opens_probe() -> None:
    admission = FakeAdmission(acquire_error=ConnectorTestRateLimited(15))
    probe = FakeProbe()
    audit = FakeAudit()

    with pytest.raises(ConnectorTestRateLimited):
        await use_case(admission, probe, audit).execute(command())

    assert probe.calls == []
    assert probe.releases == 1
    assert admission.released == []
    assert audit.calls == []


@pytest.mark.asyncio
async def test_request_cancellation_keeps_lease_until_probe_completion() -> None:
    blocker = asyncio.Event()
    admission = FakeAdmission()
    probe = FakeProbe(blocker=blocker)
    audit = FakeAudit()
    task = asyncio.create_task(use_case(admission, probe, audit).execute(command()))
    while not probe.calls:
        await asyncio.sleep(0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert admission.released == []
    blocker.set()
    await asyncio.wait_for(admission.release_event.wait(), timeout=1)
    assert len(admission.released) == 1
    assert audit.calls == []


@pytest.mark.asyncio
async def test_audit_or_release_failure_does_not_change_probe_result() -> None:
    admission = FakeAdmission(release_error=RuntimeError("redis release failed"))
    probe = FakeProbe()
    audit = FakeAudit(error=RuntimeError("audit unavailable"))

    result = await use_case(admission, probe, audit).execute(command())

    assert result.success is True
    assert len(probe.calls) == 1
    assert len(audit.calls) == 1
    assert len(admission.released) == 1


@pytest.mark.asyncio
async def test_long_running_probe_renews_lease_until_completion() -> None:
    blocker = asyncio.Event()
    admission = FakeAdmission()
    probe = FakeProbe(blocker=blocker)
    audit = FakeAudit()
    policy = ConnectorTestPolicy(
        connect_timeout_seconds=0.001,
        statement_timeout_seconds=0.001,
        response_timeout_seconds=0.02,
        redis_operation_timeout_seconds=0.001,
        lease_ttl_seconds=0.03,
    )
    task = asyncio.create_task(
        ConnectorConnectionUseCase(admission, probe, audit, policy).execute(command())
    )

    while not admission.renewed:
        await asyncio.sleep(0.005)
    result = await task

    assert result.reason_code == "connector.connection_timeout"
    assert admission.released == []
    blocker.set()
    await asyncio.wait_for(admission.release_event.wait(), timeout=1)
    assert len(admission.renewed) >= 1
    assert len(admission.released) == 1


@pytest.mark.asyncio
async def test_renewal_failure_returns_admission_error_and_defers_release() -> None:
    blocker = asyncio.Event()
    admission = FakeAdmission(
        renew_error=ConnectorTestAdmissionUnavailable(),
    )
    probe = FakeProbe(blocker=blocker)
    audit = FakeAudit()
    policy = ConnectorTestPolicy(
        connect_timeout_seconds=0.001,
        statement_timeout_seconds=0.001,
        response_timeout_seconds=0.02,
        redis_operation_timeout_seconds=0.001,
        lease_ttl_seconds=0.03,
    )

    with pytest.raises(ConnectorTestAdmissionUnavailable):
        await ConnectorConnectionUseCase(admission, probe, audit, policy).execute(
            command()
        )

    assert admission.released == []
    assert audit.calls[0][1].reason_code == "connector.admission_unavailable"
    blocker.set()
    await asyncio.wait_for(admission.release_event.wait(), timeout=1)
    assert len(admission.released) == 1
