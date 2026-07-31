import inspect
import importlib

import pytest

from apps.shared.services.credential_encryption import CredentialEncryptionError
from apps.workflow_engine import mail_credential_startup

shared_celery_module = importlib.import_module("apps.shared.celery_app")


def test_workflow_worker_startup_rejects_invalid_mail_keyring(monkeypatch):
    monkeypatch.setattr(
        mail_credential_startup,
        "require_mail_credential_keyring_ready",
        lambda: (_ for _ in ()).throw(
            CredentialEncryptionError("Mail credential encryption keyring is invalid.")
        ),
    )

    with pytest.raises(CredentialEncryptionError, match="keyring is invalid"):
        mail_credential_startup.validate_mail_credential_worker_readiness()


def test_workflow_worker_child_rechecks_mail_keyring(monkeypatch):
    calls = []
    monkeypatch.setattr(
        mail_credential_startup,
        "require_mail_credential_keyring_ready",
        lambda: calls.append("checked"),
    )

    mail_credential_startup.validate_mail_credential_worker_process()

    assert calls == ["checked"]


def test_shared_celery_app_does_not_require_mail_keyring_for_log_workers():
    source = inspect.getsource(shared_celery_module)

    assert "require_mail_credential_keyring_ready" not in source
