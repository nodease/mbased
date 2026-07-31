from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
WATCH_PATHS = (
    ROOT_DIR / "apps" / "gateway",
    ROOT_DIR / "apps" / "shared",
    ROOT_DIR / "apps" / "workflow_engine",
)
IGNORED_PATHS = tuple(path / "tests" for path in WATCH_PATHS)


@dataclass
class ReloadDebouncer:
    quiet_seconds: float
    last_change_at: float | None = None

    def __post_init__(self) -> None:
        if self.quiet_seconds <= 0:
            raise ValueError("quiet_seconds must be positive")

    def observe(self, *, now: float, changed: bool) -> bool:
        if changed:
            self.last_change_at = now
            return False
        if self.last_change_at is None:
            return False
        if now - self.last_change_at < self.quiet_seconds:
            return False
        self.last_change_at = None
        return True


def _gateway_command() -> list[str]:
    return [
        sys.executable,
        "-m",
        "uvicorn",
        "apps.gateway.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
    ]


def _start_gateway() -> subprocess.Popen[bytes]:
    return subprocess.Popen(_gateway_command(), cwd=ROOT_DIR, env=os.environ.copy())


def _stop_gateway(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _raise_keyboard_interrupt(_signum: int, _frame: object) -> None:
    raise KeyboardInterrupt


def main() -> int:
    from watchfiles import PythonFilter, watch

    quiet_seconds = float(os.getenv("DEV_GATEWAY_RELOAD_QUIET_SECONDS", "3"))
    debouncer = ReloadDebouncer(quiet_seconds=quiet_seconds)
    process = _start_gateway()

    signal.signal(signal.SIGINT, _raise_keyboard_interrupt)
    signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)

    try:
        changes_iter = watch(
            *WATCH_PATHS,
            watch_filter=PythonFilter(ignore_paths=IGNORED_PATHS),
            force_polling=os.getenv("WATCHFILES_FORCE_POLLING", "true").lower()
            in {"1", "true", "yes"},
            poll_delay_ms=300,
            rust_timeout=250,
            yield_on_timeout=True,
        )
        for changes in changes_iter:
            return_code = process.poll()
            if return_code is not None:
                return return_code or 1
            if debouncer.observe(now=time.monotonic(), changed=bool(changes)):
                print(
                    f"Backend changes were quiet for {quiet_seconds:g}s; restarting Gateway...",
                    flush=True,
                )
                _stop_gateway(process)
                process = _start_gateway()
    except KeyboardInterrupt:
        return 0
    finally:
        _stop_gateway(process)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
