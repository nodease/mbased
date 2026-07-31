"""Model-routing experiment secret input boundary."""

from __future__ import annotations

import os
from collections.abc import Mapping


DEFAULT_WEBHOOK_SECRET_ENV = "NODEASE_MODEL_ROUTING_WEBHOOK_SECRET"


def read_required_secret(
    environment_variable: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Read a required secret without including its value in errors."""

    if not environment_variable:
        raise ValueError("Webhook secret 환경변수 이름이 필요합니다.")
    source = os.environ if environ is None else environ
    secret = source.get(environment_variable)
    if not secret:
        raise ValueError(
            f"실제 배포 실험에는 {environment_variable} 환경변수가 필요합니다."
        )
    return secret
