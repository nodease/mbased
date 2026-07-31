"""Workflow Engine Worker startup checks for the external-effect boundary."""

from celery.signals import worker_init, worker_process_init

from apps.workflow_engine.composition.external_effect_readiness import (
    require_external_effect_worker_ready,
)


def _validate() -> None:
    from apps.shared.db.session import engine

    require_external_effect_worker_ready(engine)


@worker_init.connect
def validate_external_effect_worker_readiness(**kwargs) -> None:
    _validate()


@worker_process_init.connect
def validate_external_effect_worker_process(**kwargs) -> None:
    _validate()
