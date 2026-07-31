"""Workflow Worker startup checks for the outbound proxy boundary."""

from celery.signals import worker_init, worker_process_init

from apps.shared.services.connector_tcp_transport import (
    require_connector_tcp_proxy_security_ready,
)
from apps.shared.services.outbound_proxy_policy import (
    require_outbound_proxy_security_ready,
)


@worker_init.connect
def validate_outbound_proxy_worker_readiness(**kwargs) -> None:
    """Reject task consumption when the outbound transport is unsafe."""

    require_outbound_proxy_security_ready()
    require_connector_tcp_proxy_security_ready()


@worker_process_init.connect
def validate_outbound_proxy_worker_process(**kwargs) -> None:
    """Recheck the immutable transport policy in every Worker child."""

    require_outbound_proxy_security_ready()
    require_connector_tcp_proxy_security_ready()
