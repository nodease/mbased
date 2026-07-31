"""Gateway compatibility facade for shared schedule schema readiness."""

from apps.shared.services.schedule_dispatch_schema_readiness import (
    ScheduleDispatchMigrationNotReadyError,
    require_schedule_dispatch_migration_ready,
    schedule_dispatch_alembic_readiness,
)

GatewayMigrationNotReadyError = ScheduleDispatchMigrationNotReadyError
gateway_alembic_readiness = schedule_dispatch_alembic_readiness

__all__ = [
    "GatewayMigrationNotReadyError",
    "gateway_alembic_readiness",
    "require_schedule_dispatch_migration_ready",
]
