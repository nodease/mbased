from __future__ import annotations

import time
from collections.abc import Callable

from apps.shared.db.session import engine
from apps.shared.domain.knowledge_document_ingestion import (
    DEFAULT_HEARTBEAT_SECONDS,
    DEFAULT_LEASE_SECONDS,
)
from apps.shared.services.llm_credential_config import (
    require_llm_credential_keyring_ready,
)
from apps.shared.services.knowledge_document_ingestion_schema_readiness import (
    KnowledgeDocumentIngestionSchemaNotReady,
    require_knowledge_document_ingestion_ready,
)
from apps.shared.services.outbound_proxy_policy import (
    require_outbound_proxy_security_ready,
)
from apps.shared.services.connector_tcp_transport import (
    require_connector_tcp_proxy_security_ready,
)


def _require_readiness() -> None:
    require_outbound_proxy_security_ready()
    require_connector_tcp_proxy_security_ready()
    require_llm_credential_keyring_ready()
    require_knowledge_document_ingestion_ready(
        engine,
        lease_seconds=DEFAULT_LEASE_SECONDS,
        heartbeat_seconds=DEFAULT_HEARTBEAT_SECONDS,
    )


def wait_for_readiness(
    *,
    check: Callable[[], None] = _require_readiness,
    timeout_seconds: float = 300.0,
    interval_seconds: float = 2.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    if timeout_seconds <= 0 or interval_seconds <= 0:
        raise ValueError("readiness timing must be positive")
    deadline = clock() + timeout_seconds
    while True:
        try:
            check()
            return
        except KnowledgeDocumentIngestionSchemaNotReady:
            remaining = deadline - clock()
            if remaining <= 0:
                raise
            sleep(min(interval_seconds, remaining))


if __name__ == "__main__":
    wait_for_readiness()
