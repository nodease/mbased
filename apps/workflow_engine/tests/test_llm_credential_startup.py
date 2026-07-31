import importlib
import inspect
from pathlib import Path

import pytest

from apps.shared.services.llm_credential_config import LLMCredentialConfigError
from apps.workflow_engine import llm_credential_startup

shared_celery_module = importlib.import_module("apps.shared.celery_app")
WORKFLOW_TASKS = Path(__file__).resolve().parents[1] / "tasks.py"


def test_workflow_worker_startup_rejects_invalid_llm_keyring(monkeypatch):
    monkeypatch.setattr(
        llm_credential_startup,
        "require_llm_credential_keyring_ready",
        lambda: (_ for _ in ()).throw(
            LLMCredentialConfigError("LLM credential keyring is invalid.")
        ),
    )

    with pytest.raises(LLMCredentialConfigError, match="keyring is invalid"):
        llm_credential_startup.validate_llm_credential_worker_readiness()


def test_workflow_worker_child_rechecks_llm_keyring(monkeypatch):
    calls = []
    monkeypatch.setattr(
        llm_credential_startup,
        "require_llm_credential_keyring_ready",
        lambda: calls.append("checked"),
    )

    llm_credential_startup.validate_llm_credential_worker_process()

    assert calls == ["checked"]


def test_shared_celery_app_does_not_require_llm_keyring_for_log_workers():
    source = inspect.getsource(shared_celery_module)

    assert "require_llm_credential_keyring_ready" not in source


def test_workflow_task_module_registers_llm_credential_startup_hooks():
    source = WORKFLOW_TASKS.read_text(encoding="utf-8")

    assert "from apps.workflow_engine import llm_credential_startup" in source
