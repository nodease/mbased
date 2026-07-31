"""Sandbox network access policy regression tests."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from apps.sandbox.api.v1.endpoints import execute as execute_endpoint
from apps.sandbox.core.executor import SandboxExecutor
from apps.sandbox.core.network_policy import (
    NETWORK_ACCESS_UNSUPPORTED_REASON,
    SandboxNetworkAccessUnsupported,
)
from apps.sandbox.core.scheduler import FairScheduler
from apps.sandbox.models.result import ExecutionResult
from apps.sandbox.nsjail.wrapper import NSJailWrapper


@pytest.mark.asyncio
async def test_execute_api_rejects_network_access_before_scheduler_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_scheduler_is_resolved() -> None:
        raise AssertionError("unsupported network access reached the scheduler")

    monkeypatch.setattr(
        execute_endpoint.SandboxScheduler,
        "get_instance",
        fail_if_scheduler_is_resolved,
    )

    with pytest.raises(HTTPException) as exc_info:
        await execute_endpoint.execute_code(
            execute_endpoint.ExecuteRequest(
                code="def main(inputs): return inputs",
                enable_network=True,
            )
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == {
        "reason_code": NETWORK_ACCESS_UNSUPPORTED_REASON,
    }


@pytest.mark.asyncio
async def test_scheduler_rejects_network_access_before_state_or_queue_mutation() -> (
    None
):
    scheduler = object.__new__(FairScheduler)

    with pytest.raises(SandboxNetworkAccessUnsupported):
        await scheduler.submit(
            code="def main(inputs): return inputs",
            inputs={},
            enable_network=True,
        )


def test_executor_ignores_legacy_ambient_network_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class CapturingWrapper:
        def execute(self, **kwargs: object) -> ExecutionResult:
            captured.update(kwargs)
            return ExecutionResult(success=True, result={})

    executor = object.__new__(SandboxExecutor)
    executor.wrapper = CapturingWrapper()
    monkeypatch.setattr(
        "apps.sandbox.core.executor.settings.ENABLE_NETWORK",
        True,
        raising=False,
    )

    executor.execute(code="def main(inputs): return inputs", inputs={})

    assert captured["enable_network"] is False


def test_executor_rejects_explicit_network_access_before_wrapper_call() -> None:
    executor = object.__new__(SandboxExecutor)
    executor.wrapper = SimpleNamespace(
        execute=lambda **_kwargs: pytest.fail("wrapper must not be called")
    )

    with pytest.raises(SandboxNetworkAccessUnsupported):
        executor.execute(
            code="def main(inputs): return inputs",
            inputs={},
            enable_network=True,
        )


def test_nsjail_command_builder_never_disables_network_namespace() -> None:
    wrapper = object.__new__(NSJailWrapper)
    wrapper.nsjail_path = "/usr/bin/nsjail"
    wrapper.config_path = "/app/nsjail/sandbox.cfg"

    command = wrapper._build_command(
        script_path="/tmp/run.py",
        timeout=10,
        enable_network=False,
    )

    assert "--disable_clone_newnet" not in command

    with pytest.raises(SandboxNetworkAccessUnsupported):
        wrapper._build_command(
            script_path="/tmp/run.py",
            timeout=10,
            enable_network=True,
        )
