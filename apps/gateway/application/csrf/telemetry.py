from __future__ import annotations

import inspect
from collections.abc import Callable
from functools import partial
from typing import Any

from anyio import CapacityLimiter, to_thread


CSRF_TELEMETRY_CONCURRENCY_LIMIT = 4
_csrf_telemetry_limiter = CapacityLimiter(CSRF_TELEMETRY_CONCURRENCY_LIMIT)


async def run_bounded_csrf_telemetry(
    callback: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> None:
    callback_result = await to_thread.run_sync(
        partial(callback, *args, **kwargs),
        limiter=_csrf_telemetry_limiter,
    )
    if inspect.isawaitable(callback_result):
        await callback_result
