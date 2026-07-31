"""Safety helpers for opt-in disposable PostgreSQL tests."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from sqlalchemy.engine import URL

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_PREFIX_RE = re.compile(r"^[a-z0-9_]+$")
_RESERVED_SUBPROCESS_ENV_KEYS = frozenset(
    {
        "DATABASE_URL",
        "SQLALCHEMY_DATABASE_URI",
        "DB_HOST",
        "DB_PORT",
        "DB_USER",
        "DB_PASSWORD",
        "DB_NAME",
        "PGHOST",
        "PGPORT",
        "PGUSER",
        "PGPASSWORD",
        "PGDATABASE",
        "PYTHONPATH",
    }
)


class DisposablePostgresConfigurationError(ValueError):
    """Raised before connecting when a disposable DB target is not explicit."""


@dataclass(frozen=True, slots=True)
class DisposablePostgresConfig:
    host: str
    port: int
    user: str
    password: str = field(repr=False)
    maintenance_database: str = "postgres"

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "DisposablePostgresConfig":
        values = environ if environ is not None else os.environ
        required = {
            name: str(values.get(name, "")).strip()
            for name in ("DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD")
        }
        if any(not value for value in required.values()):
            raise DisposablePostgresConfigurationError(
                "explicit disposable PostgreSQL connection settings are required"
            )

        try:
            port = int(required["DB_PORT"])
        except ValueError as exc:
            raise DisposablePostgresConfigurationError(
                "disposable PostgreSQL port is invalid"
            ) from exc
        if port < 1 or port > 65535:
            raise DisposablePostgresConfigurationError(
                "disposable PostgreSQL port is invalid"
            )

        host = required["DB_HOST"]
        confirmation = str(values.get("NODEASE_DISPOSABLE_DB_HOST_CONFIRM", "")).strip()
        if host.lower() not in _LOOPBACK_HOSTS and confirmation != host:
            raise DisposablePostgresConfigurationError(
                "non-loopback disposable PostgreSQL host requires exact confirmation"
            )

        maintenance_database = str(
            values.get("NODEASE_DISPOSABLE_DB_MAINTENANCE_DB", "postgres")
        ).strip()
        if not maintenance_database:
            raise DisposablePostgresConfigurationError(
                "disposable PostgreSQL maintenance database is required"
            )

        return cls(
            host=host,
            port=port,
            user=required["DB_USER"],
            password=required["DB_PASSWORD"],
            maintenance_database=maintenance_database,
        )

    def database_url(self, database: str) -> URL:
        return URL.create(
            "postgresql",
            username=self.user,
            password=self.password,
            host=self.host,
            port=self.port,
            database=database,
            query={"connect_timeout": "5"},
        )

    def subprocess_environment(
        self,
        *,
        database: str,
        root_dir: Path,
        extra: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        if extra:
            conflicting_keys = {
                str(key)
                for key in extra
                if str(key).upper() in _RESERVED_SUBPROCESS_ENV_KEYS
            }
            if conflicting_keys:
                raise DisposablePostgresConfigurationError(
                    "extra environment cannot override disposable PostgreSQL settings"
                )

        env = os.environ.copy()
        if extra:
            env.update(extra)
        env.pop("DATABASE_URL", None)
        env.pop("SQLALCHEMY_DATABASE_URI", None)
        env.update(
            {
                "DB_HOST": self.host,
                "DB_PORT": str(self.port),
                "DB_USER": self.user,
                "DB_PASSWORD": self.password,
                "DB_NAME": database,
                "PYTHONIOENCODING": "utf-8",
                "PYTHONPATH": str(root_dir),
            }
        )
        return env


def quote_disposable_database_name(database: str, *, prefix: str) -> str:
    if not _PREFIX_RE.fullmatch(prefix):
        raise DisposablePostgresConfigurationError("unsafe disposable database prefix")
    pattern = re.compile(rf"^{re.escape(prefix)}_[a-f0-9]{{12}}$")
    if not pattern.fullmatch(database):
        raise DisposablePostgresConfigurationError("unsafe disposable database name")
    return f'"{database}"'
