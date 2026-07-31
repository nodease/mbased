from __future__ import annotations

import logging
import uuid

from apps.shared.pubsub import get_redis_client


logger = logging.getLogger(__name__)


class RedisDocumentIngestionProgressProjection:
    """Maintain the advisory document progress cache without owning state."""

    def clear(self, document_id: uuid.UUID) -> None:
        try:
            get_redis_client().delete(f"knowledge_progress:{document_id}")
        except Exception as exc:
            logger.warning(
                "Failed to clear Knowledge progress projection: error_type=%s",
                type(exc).__name__,
            )
