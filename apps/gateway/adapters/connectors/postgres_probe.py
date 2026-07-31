from __future__ import annotations

import asyncio
import ssl
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.pool import NullPool

from apps.gateway.application.connectors.errors import (
    ConnectorProbeCapacityExceeded,
    ConnectorProbeFailed,
    ConnectorTargetNotAllowed,
)
from apps.gateway.application.connectors.models import (
    ConnectorTestCommand,
    ConnectorTestPolicy,
)
from apps.shared.services.egress_guard import (
    EgressGuardError,
    canonicalize_network_host,
    ensure_network_target_allowed,
)
from apps.shared.services.connector_tcp_transport import (
    HttpConnectProxyDialer,
    LocalConnectorProxyRelay,
)

_URL_HOST_MARKERS = ("://", "/", "@", "?", "#")


def _system_ca_file() -> str:
    ca_file = ssl.get_default_verify_paths().cafile
    if not ca_file or not Path(ca_file).is_file():
        raise ConnectorProbeFailed()
    return ca_file


def _configured_ca_file(ca_file: str) -> str:
    if not Path(ca_file).is_file():
        raise ConnectorProbeFailed()
    return ca_file


class StrictPostgresConnectorProbe:
    def __init__(
        self,
        policy: ConnectorTestPolicy,
        *,
        connector_proxy_dialer: HttpConnectProxyDialer | None = None,
    ) -> None:
        self._policy = policy
        self._connector_proxy_dialer = connector_proxy_dialer
        self._executor = ThreadPoolExecutor(
            max_workers=policy.global_concurrency_limit,
            thread_name_prefix="connector-test",
        )
        self._capacity = threading.BoundedSemaphore(policy.global_concurrency_limit)

    def reserve(self) -> _StrictPostgresProbeReservation:
        if not self._capacity.acquire(blocking=False):
            raise ConnectorProbeCapacityExceeded()
        return _StrictPostgresProbeReservation(self)

    async def _probe_reserved(self, command: ConnectorTestCommand) -> bool:
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(self._executor, self._probe_sync, command)
        return await asyncio.shield(future)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=False)

    def _probe_sync(self, command: ConnectorTestCommand) -> bool:
        engine = None
        relay = None
        try:
            host_input = command.host.strip()
            if not host_input or any(marker in host_input for marker in _URL_HOST_MARKERS):
                raise ConnectorTargetNotAllowed()

            canonical_host = canonicalize_network_host(host_input)
            trusted_local_targets = frozenset(
                (target.host, target.port)
                for target in self._policy.trusted_local_targets
            )
            is_trusted_local = (
                canonical_host,
                command.port,
            ) in trusted_local_targets
            ca_file = (
                _configured_ca_file(self._policy.trusted_local_ca_file)
                if is_trusted_local and self._policy.trusted_local_ca_file
                else _system_ca_file()
            )
            host, port, host_address = ensure_network_target_allowed(
                host_input,
                command.port,
                allowed_ports=self._policy.allowed_ports,
                trusted_local_targets=trusted_local_targets,
            )
            connect_port = port
            if self._connector_proxy_dialer is not None and not is_trusted_local:
                relay = LocalConnectorProxyRelay(
                    self._connector_proxy_dialer,
                    target_address=host_address,
                    target_port=port,
                    connect_timeout_seconds=self._policy.connect_timeout_seconds,
                )
                relay.start()
                host_address = "127.0.0.1"
                connect_port = relay.local_bind_port
            url = URL.create(
                drivername="postgresql+psycopg2",
                username=command.username,
                password=command.password,
                host=host,
                port=connect_port,
                database=command.database,
                query={
                    "hostaddr": host_address,
                    "sslmode": "verify-full",
                    "sslrootcert": ca_file,
                },
            )
            engine = create_engine(
                url,
                poolclass=NullPool,
                connect_args={
                    "connect_timeout": self._policy.connect_timeout_seconds,
                    "options": (
                        "-c statement_timeout="
                        f"{self._policy.statement_timeout_seconds * 1000}"
                    ),
                },
            )
            with engine.connect() as connection:
                connection.execute(text("SET TRANSACTION READ ONLY"))
                return connection.execute(text("SELECT 1")).scalar_one() == 1
        except ConnectorTargetNotAllowed:
            raise
        except EgressGuardError:
            raise ConnectorTargetNotAllowed() from None
        except Exception:
            raise ConnectorProbeFailed() from None
        finally:
            if engine is not None:
                engine.dispose()
            if relay is not None:
                relay.stop()


class _StrictPostgresProbeReservation:
    def __init__(self, probe: StrictPostgresConnectorProbe) -> None:
        self._probe = probe
        self._state_lock = threading.Lock()
        self._started = False
        self._released = False

    async def probe(self, command: ConnectorTestCommand) -> bool:
        with self._state_lock:
            if self._released or self._started:
                raise ConnectorProbeFailed()
            self._started = True

        task = asyncio.create_task(self._probe._probe_reserved(command))
        release_deferred = False
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            task.add_done_callback(lambda _: self.release())
            release_deferred = True
            raise
        finally:
            if not release_deferred:
                self.release()

    def release(self) -> None:
        with self._state_lock:
            if self._released:
                return
            self._released = True
        self._probe._capacity.release()


__all__ = ["StrictPostgresConnectorProbe"]
