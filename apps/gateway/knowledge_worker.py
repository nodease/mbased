from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from celery import bootsteps
from celery.signals import worker_process_init
from dotenv import load_dotenv


logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s[%(asctime)s: %(levelname)s/%(processName)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
ENV_PATH = ROOT_DIR / ".env"
if ENV_PATH.exists():
    load_dotenv(dotenv_path=ENV_PATH, override=False)

os.environ.setdefault("CELERY_WORKER_ROLE", "knowledge")

from apps.gateway import knowledge_ingestion_tasks  # noqa: E402, F401
from apps.shared.celery_app import celery_app  # noqa: E402
from apps.shared.db.session import engine  # noqa: E402
from apps.shared.domain.knowledge_document_ingestion import (  # noqa: E402
    DEFAULT_HEARTBEAT_SECONDS,
    DEFAULT_LEASE_SECONDS,
)
from apps.shared.services.llm_credential_config import (  # noqa: E402
    require_llm_credential_keyring_ready,
)
from apps.shared.services.knowledge_document_ingestion_schema_readiness import (  # noqa: E402
    require_knowledge_document_ingestion_ready,
)
from apps.shared.services.outbound_proxy_policy import (  # noqa: E402
    require_outbound_proxy_security_ready,
)
from apps.shared.services.connector_tcp_transport import (  # noqa: E402
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


class KnowledgeSchemaReadinessStep(bootsteps.StartStopStep):
    label = "knowledge-ingestion-schema-readiness"

    def start(self, worker) -> None:
        _require_readiness()


@worker_process_init.connect
def initialize_knowledge_worker_process(**kwargs) -> None:
    engine.dispose()
    require_outbound_proxy_security_ready()
    require_connector_tcp_proxy_security_ready()
    require_llm_credential_keyring_ready()


app = celery_app
app.steps["worker"].add(KnowledgeSchemaReadinessStep)
