from __future__ import annotations

import logging


_TRANSPORT_LOGGERS = ("httpx", "httpcore", "urllib3")


def configure_external_effect_transport_logging() -> None:
    for name in _TRANSPORT_LOGGERS:
        transport_logger = logging.getLogger(name)
        transport_logger.handlers.clear()
        transport_logger.propagate = False
        transport_logger.disabled = True
