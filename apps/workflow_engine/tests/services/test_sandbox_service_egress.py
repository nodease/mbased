from __future__ import annotations

from typing import Any

from apps.workflow_engine.services import sandbox_service as sandbox_module
from apps.workflow_engine.services.sandbox_service import SandboxService


class _SuccessfulResponse:
    status_code = 200

    def json(self) -> dict[str, Any]:
        return {"success": True, "result": {"value": "ok"}}


def test_sandbox_control_ignores_ambient_proxy_and_uses_internal_url(
    monkeypatch,
) -> None:
    observed: dict[str, Any] = {}
    monkeypatch.setenv("HTTP_PROXY", "http://ambient.invalid:9999")
    monkeypatch.setenv("NO_PROXY", "*")

    class _Client:
        def __init__(self, **kwargs: Any) -> None:
            observed["client_kwargs"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def post(self, url: str, **kwargs: Any) -> _SuccessfulResponse:
            observed["url"] = url
            observed["request_kwargs"] = kwargs
            return _SuccessfulResponse()

    monkeypatch.setattr(sandbox_module.httpx, "Client", _Client)

    result = SandboxService("http://sandbox:8194").execute_python_code(
        "def main(inputs): return inputs",
        {"value": "synthetic"},
    )

    assert result == {"value": "ok"}
    assert observed["url"] == "http://sandbox:8194/v1/sandbox/execute"
    assert observed["client_kwargs"]["trust_env"] is False
