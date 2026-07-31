from __future__ import annotations

import asyncio
import logging
import time

from .errors import (
    ConnectorProbeCapacityExceeded,
    ConnectorProbeFailed,
    ConnectorTargetNotAllowed,
    ConnectorTestAdmissionUnavailable,
    ConnectorTestBusy,
)
from .models import (
    SUCCESS_RESULT,
    AdmissionLease,
    ConnectorTestCommand,
    ConnectorTestPolicy,
    ConnectorTestResult,
    failure_result,
)
from .ports import (
    ConnectorProbePort,
    ConnectorTestAdmissionPort,
    ConnectorTestAuditPort,
)

logger = logging.getLogger(__name__)


class TestConnectorConnection:
    def __init__(
        self,
        admission: ConnectorTestAdmissionPort,
        probe: ConnectorProbePort,
        audit: ConnectorTestAuditPort,
        policy: ConnectorTestPolicy,
    ) -> None:
        self._admission = admission
        self._probe = probe
        self._audit = audit
        self._policy = policy
        self._deferred_cleanup_tasks: set[asyncio.Task[None]] = set()

    async def execute(self, command: ConnectorTestCommand) -> ConnectorTestResult:
        if command.ssh_enabled:
            return failure_result("connector.ssh_probe_not_supported")
        if command.port not in self._policy.allowed_ports:
            return failure_result("connector.target_not_allowed")

        try:
            probe_reservation = self._probe.reserve()
        except ConnectorProbeCapacityExceeded:
            raise ConnectorTestBusy() from None

        try:
            lease = await self._admission.acquire(command)
        except asyncio.CancelledError:
            probe_reservation.release()
            raise
        except Exception:
            probe_reservation.release()
            raise

        started_at = time.monotonic()
        probe_task = asyncio.create_task(probe_reservation.probe(command))
        heartbeat_task = asyncio.create_task(self._maintain_lease(lease))
        release_deferred = False

        try:
            try:
                done, _ = await asyncio.wait(
                    {probe_task, heartbeat_task},
                    timeout=self._policy.response_timeout_seconds,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if probe_task in done:
                    connected = probe_task.result()
                    result = (
                        SUCCESS_RESULT
                        if connected
                        else failure_result("connector.connection_failed")
                    )
                elif heartbeat_task in done:
                    await heartbeat_task
                    raise ConnectorTestAdmissionUnavailable()
                else:
                    result = failure_result("connector.connection_timeout")
                    self._release_after_completion(
                        probe_task,
                        heartbeat_task,
                        lease,
                        started_at,
                    )
                    release_deferred = True
            except ConnectorTestAdmissionUnavailable:
                self._record_audit(
                    command,
                    failure_result("connector.admission_unavailable"),
                    _duration_bucket(time.monotonic() - started_at, None),
                )
                self._release_after_completion(
                    probe_task,
                    heartbeat_task,
                    lease,
                    started_at,
                )
                release_deferred = True
                raise
            except ConnectorTargetNotAllowed:
                result = failure_result("connector.target_not_allowed")
            except ConnectorProbeFailed:
                result = failure_result("connector.connection_failed")
            except asyncio.CancelledError:
                self._release_after_completion(
                    probe_task,
                    heartbeat_task,
                    lease,
                    started_at,
                )
                release_deferred = True
                raise
            except Exception:
                result = failure_result("connector.connection_failed")

            self._record_audit(
                command,
                result,
                _duration_bucket(time.monotonic() - started_at, result.reason_code),
            )
            return result
        finally:
            if not release_deferred:
                await self._finish_lease(heartbeat_task, lease)

    def _release_after_completion(
        self,
        task: asyncio.Task[bool],
        heartbeat_task: asyncio.Task[None],
        lease: AdmissionLease,
        started_at: float,
    ) -> None:
        cleanup_task = asyncio.create_task(
            self._finish_deferred_probe(
                task,
                heartbeat_task,
                lease,
                started_at,
            )
        )
        self._deferred_cleanup_tasks.add(cleanup_task)
        cleanup_task.add_done_callback(self._deferred_cleanup_tasks.discard)

    async def _finish_deferred_probe(
        self,
        probe_task: asyncio.Task[bool],
        heartbeat_task: asyncio.Task[None],
        lease: AdmissionLease,
        started_at: float,
    ) -> None:
        remaining = max(
            0.0,
            self._policy.probe_hard_timeout_seconds
            - (time.monotonic() - started_at),
        )
        try:
            done, _ = await asyncio.wait(
                {probe_task, heartbeat_task},
                timeout=remaining,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if probe_task in done:
                self._consume_task_result(probe_task)
            else:
                probe_task.cancel()
                try:
                    await probe_task
                except (asyncio.CancelledError, Exception):
                    pass
        finally:
            await self._finish_lease(heartbeat_task, lease)

    @staticmethod
    def _consume_task_result(task: asyncio.Task[bool]) -> None:
        try:
            task.exception()
        except (asyncio.CancelledError, Exception):
            pass

    async def _maintain_lease(self, lease: AdmissionLease) -> None:
        interval = max(0.01, self._policy.lease_ttl_seconds / 3)
        try:
            while True:
                await asyncio.sleep(interval)
                await self._admission.renew(lease)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "Connector test lease renewal failed: error_type=%s",
                type(exc).__name__,
            )
            raise ConnectorTestAdmissionUnavailable() from None

    async def _finish_lease(
        self,
        heartbeat_task: asyncio.Task[None],
        lease: AdmissionLease,
    ) -> None:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        await self._release_safely(lease)

    async def _release_safely(self, lease: AdmissionLease) -> None:
        try:
            await self._admission.release(lease)
        except Exception as exc:
            logger.error(
                "Connector test lease release failed: error_type=%s",
                type(exc).__name__,
            )

    def _record_audit(
        self,
        command: ConnectorTestCommand,
        result: ConnectorTestResult,
        duration_bucket: str,
    ) -> None:
        try:
            self._audit.record(command, result, duration_bucket)
        except Exception as exc:
            logger.error(
                "Connector test audit failed: error_type=%s",
                type(exc).__name__,
            )


def _duration_bucket(duration_seconds: float, reason_code: str | None) -> str:
    if reason_code == "connector.connection_timeout":
        return "timeout"
    if duration_seconds < 1:
        return "<1s"
    if duration_seconds < 5:
        return "1-5s"
    return "5-10s"


__all__ = ["TestConnectorConnection"]
