from __future__ import annotations

import time
from contextlib import contextmanager

from sqlalchemy import text

MIGRATION_ADVISORY_LOCK_ID = 734_187_2026
DEFAULT_MIGRATION_LOCK_TIMEOUT_SECONDS = 120.0
DEFAULT_MIGRATION_LOCK_POLL_SECONDS = 1.0


@contextmanager
def migration_advisory_lock(
    connection,
    *,
    timeout_seconds: float = DEFAULT_MIGRATION_LOCK_TIMEOUT_SECONDS,
    poll_interval_seconds: float = DEFAULT_MIGRATION_LOCK_POLL_SECONDS,
):
    """Own one bounded PostgreSQL migration session at a time."""
    if timeout_seconds < 0 or poll_interval_seconds <= 0:
        raise ValueError(
            "migration lock timeout must be non-negative and poll interval positive"
        )

    deadline = time.monotonic() + timeout_seconds
    while True:
        acquired = bool(
            connection.execute(
                text("SELECT pg_try_advisory_lock(:lock_id)"),
                {"lock_id": MIGRATION_ADVISORY_LOCK_ID},
            ).scalar()
        )
        if acquired:
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("another database migration is already running")
        time.sleep(min(poll_interval_seconds, remaining))
    try:
        yield
    finally:
        connection.execute(
            text("SELECT pg_advisory_unlock(:lock_id)"),
            {"lock_id": MIGRATION_ADVISORY_LOCK_ID},
        )
