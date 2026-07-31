"""Gateway process lifespan and migration-first startup boundary."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import inspect, text

from apps.gateway.services.migration_readiness import (
    require_schedule_dispatch_migration_ready,
)
from apps.shared.db.seed import (
    seed_default_llm_models,
    seed_default_llm_providers,
    seed_placeholder_user,
)
from apps.shared.db.session import engine
from apps.shared.domain.schedule_dispatch import (
    schedule_dispatch_settings_from_environment,
)
from apps.shared.services.schedule_dispatch_schema_readiness import (
    required_schedule_dispatch_schema_exists,
)
from apps.shared.services.credential_encryption import (
    require_mail_credential_keyring_ready,
)
from apps.shared.services.llm_credential_config import (
    require_llm_credential_keyring_ready,
)
from apps.shared.services.outbound_proxy_policy import (
    require_outbound_proxy_security_ready,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    del app
    from apps.gateway.composition.connectors import (
        require_connector_test_security_ready,
    )
    from apps.shared.audit.listeners import register_audit_listeners

    register_audit_listeners()
    require_outbound_proxy_security_ready()
    require_mail_credential_keyring_ready()
    require_llm_credential_keyring_ready()
    require_connector_test_security_ready()

    dispatch_settings = schedule_dispatch_settings_from_environment(os.environ)
    require_schedule_dispatch_migration_ready(
        engine,
        settings=dispatch_settings,
    )
    schedule_maintenance_enabled = required_schedule_dispatch_schema_exists(
        inspect(engine)
    )

    # Extension provisioning remains explicit until infra owns it. ORM metadata
    # and migration-managed enum/table DDL are never applied at app startup.
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))

    from apps.shared.db.session import SessionLocal

    db = SessionLocal()
    try:
        from apps.gateway.composition.memory import (
            require_public_conversation_schema_ready,
        )

        require_public_conversation_schema_ready(db)
        try:
            seed_placeholder_user(db)
            seed_default_llm_providers(db)
            seed_default_llm_models(db)

            from apps.gateway.services.llm_service import LLMService

            result = LLMService.sync_system_prices(db)
            if result["updated_models"] > 0:
                logger.info(
                    "Default LLM prices synchronized: updated_count=%s",
                    result["updated_models"],
                )
        except Exception as exc:
            logger.error(
                "Seed initialization failed: error_type=%s",
                type(exc).__name__,
            )
    finally:
        db.close()

    from apps.gateway.api.deps import get_deployment_runtime_policy
    from apps.gateway.composition.deployment import (
        build_schedule_dispatch_dependencies,
        build_schedule_next_fire_calculator,
        build_schedule_task_publisher,
    )
    from apps.gateway.services.scheduler_service import init_scheduler_service

    scheduler_db = SessionLocal()
    try:
        init_scheduler_service(
            scheduler_db,
            runtime_policy=get_deployment_runtime_policy(),
            settings=dispatch_settings,
            session_factory=SessionLocal,
            publisher=build_schedule_task_publisher(),
            dependency_builder=build_schedule_dispatch_dependencies,
            next_fire=build_schedule_next_fire_calculator(),
            maintenance_enabled=schedule_maintenance_enabled,
        )
    finally:
        scheduler_db.close()

    yield

    from apps.gateway.services.scheduler_service import get_scheduler_service

    try:
        get_scheduler_service().shutdown()
    except Exception as exc:
        logger.error(
            "SchedulerService shutdown failed: error_type=%s",
            type(exc).__name__,
        )

    from apps.gateway.composition.connectors import (
        shutdown_connector_test_application,
    )

    await shutdown_connector_test_application()
