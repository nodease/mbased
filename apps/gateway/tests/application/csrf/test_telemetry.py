import asyncio
import importlib
import threading
import time

import pytest


@pytest.mark.asyncio
async def test_shared_csrf_telemetry_runner_bounds_sync_callback_concurrency():
    try:
        telemetry = importlib.import_module(
            "apps.gateway.application.csrf.telemetry"
        )
    except ModuleNotFoundError:
        pytest.fail("shared CSRF telemetry runner is not implemented")

    active = 0
    maximum_active = 0
    lock = threading.Lock()

    def callback():
        nonlocal active, maximum_active
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        time.sleep(0.03)
        with lock:
            active -= 1

    await asyncio.gather(
        *(telemetry.run_bounded_csrf_telemetry(callback) for _ in range(12))
    )

    assert 1 <= maximum_active <= 4
