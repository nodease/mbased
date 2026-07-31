from __future__ import annotations

import logging
import uuid
from typing import Protocol


logger = logging.getLogger(__name__)


class DocumentIngestionProgressPort(Protocol):
    def clear(self, document_id: uuid.UUID) -> None: ...


def clear_progress_projection(
    progress: DocumentIngestionProgressPort | None,
    document_id: uuid.UUID | None,
) -> None:
    if progress is None or document_id is None:
        return
    try:
        progress.clear(document_id)
    except Exception as exc:
        logger.warning(
            "Failed to clear Knowledge progress projection: error_type=%s",
            type(exc).__name__,
        )
