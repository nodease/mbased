from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from apps.shared.services.connection_use_resolver import (
    ConnectionUseDenied,
    ConnectionUseResolver,
    ConnectionUseUnavailable,
)
from apps.shared.utils.encryption import encryption_manager
from sqlalchemy.orm import Session


class ConnectionRuntimeSnapshotConfigurationInvalid(Exception):
    """Hide invalid encrypted/runtime Connection configuration details."""

    code = "configuration.invalid"
    retryable = False

    def __init__(self) -> None:
        super().__init__("Connection configuration is unavailable.")


@dataclass(frozen=True, slots=True)
class ConnectionRuntimeSshSnapshot:
    host: str = field(repr=False)
    port: int = field(repr=False)
    username: str = field(repr=False)
    auth_type: str = field(repr=False)
    password: str | None = field(default=None, repr=False)
    private_key: str | None = field(default=None, repr=False)

    def to_connector_config(self) -> dict[str, Any]:
        config: dict[str, Any] = {
            "enabled": True,
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "auth_type": self.auth_type,
        }
        if self.auth_type == "key":
            config["private_key"] = self.private_key
        else:
            config["password"] = self.password
        return config


@dataclass(frozen=True, slots=True)
class ConnectionRuntimeSnapshot:
    adapter_type: str
    host: str = field(repr=False)
    port: int = field(repr=False)
    database: str = field(repr=False)
    username: str = field(repr=False)
    password: str = field(repr=False)
    ssh: ConnectionRuntimeSshSnapshot | None = field(default=None, repr=False)

    def to_connector_config(self) -> dict[str, Any]:
        config: dict[str, Any] = {
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "username": self.username,
            "password": self.password,
        }
        if self.ssh is not None:
            config["ssh"] = self.ssh.to_connector_config()
        return config


SessionFactory = Callable[[], Session]


class ConnectionRuntimeSnapshotProvider:
    """Create a runtime-only Connection snapshot in an isolated short session."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def load(
        self,
        connection_id: Any,
        *,
        execution_subject_user_id: Any,
    ) -> ConnectionRuntimeSnapshot:
        session: Session | None = None
        snapshot: ConnectionRuntimeSnapshot | None = None
        pending_error: Exception | None = None

        try:
            session = self._session_factory()
            with session.begin():
                connection = ConnectionUseResolver(session).resolve(
                    connection_id,
                    execution_subject_user_id=execution_subject_user_id,
                )
                snapshot = self._project(connection)
        except (
            ConnectionUseDenied,
            ConnectionUseUnavailable,
            ConnectionRuntimeSnapshotConfigurationInvalid,
        ) as exc:
            pending_error = exc
        except Exception:
            pending_error = ConnectionUseUnavailable()
        finally:
            if session is not None:
                try:
                    session.close()
                except Exception:
                    if pending_error is None:
                        pending_error = ConnectionUseUnavailable()

        if pending_error is not None:
            raise pending_error from None
        if snapshot is None:
            raise ConnectionUseUnavailable()
        return snapshot

    @staticmethod
    def _project(connection: Any) -> ConnectionRuntimeSnapshot:
        try:
            adapter_type = ConnectionRuntimeSnapshotProvider._required_text(
                connection.type
            )
            host = ConnectionRuntimeSnapshotProvider._required_text(connection.host)
            port = ConnectionRuntimeSnapshotProvider._validated_port(connection.port)
            database = ConnectionRuntimeSnapshotProvider._required_stored_text(
                connection.database
            )
            username = ConnectionRuntimeSnapshotProvider._required_stored_text(
                connection.username
            )
            password = encryption_manager.decrypt(connection.encrypted_password)
            if not isinstance(password, str) or not password:
                raise ValueError

            ssh = None
            if bool(connection.use_ssh):
                ssh = ConnectionRuntimeSnapshotProvider._project_ssh(connection)

            return ConnectionRuntimeSnapshot(
                adapter_type=adapter_type,
                host=host,
                port=port,
                database=database,
                username=username,
                password=password,
                ssh=ssh,
            )
        except Exception:
            raise ConnectionRuntimeSnapshotConfigurationInvalid() from None

    @staticmethod
    def _project_ssh(connection: Any) -> ConnectionRuntimeSshSnapshot:
        host = ConnectionRuntimeSnapshotProvider._required_text(connection.ssh_host)
        port = ConnectionRuntimeSnapshotProvider._validated_port(connection.ssh_port)
        username = ConnectionRuntimeSnapshotProvider._required_text(
            connection.ssh_username
        )
        auth_type = ConnectionRuntimeSnapshotProvider._required_text(
            connection.ssh_auth_type
        )
        if auth_type not in {"key", "password"}:
            raise ValueError

        if auth_type == "key":
            private_key = encryption_manager.decrypt(
                connection.encrypted_ssh_private_key
            )
            if not isinstance(private_key, str) or not private_key:
                raise ValueError
            return ConnectionRuntimeSshSnapshot(
                host=host,
                port=port,
                username=username,
                auth_type=auth_type,
                private_key=private_key,
            )

        encrypted_password = connection.encrypted_ssh_password
        password = None
        if encrypted_password is not None:
            password = encryption_manager.decrypt(encrypted_password)
            if not isinstance(password, str) or not password:
                raise ValueError
        return ConnectionRuntimeSshSnapshot(
            host=host,
            port=port,
            username=username,
            auth_type=auth_type,
            password=password,
        )

    @staticmethod
    def _required_text(value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError
        normalized = value.strip()
        if not normalized:
            raise ValueError
        return normalized

    @staticmethod
    def _required_stored_text(value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError
        return value

    @staticmethod
    def _validated_port(value: Any) -> int:
        port = int(value)
        if not 1 <= port <= 65_535:
            raise ValueError
        return port
