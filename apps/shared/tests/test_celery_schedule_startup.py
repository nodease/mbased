from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace

import pytest
from apps.shared.domain.schedule_dispatch import ScheduleDispatchDomainError

celery_module = importlib.import_module("apps.shared.celery_app")

CLAIM_FINGERPRINT = "v1|claim|5|50|10|50|100|60|120|900|120|5|5|30|180"


class _Engine:
    def __init__(self) -> None:
        self.disposed = False

    def dispose(self) -> None:
        self.disposed = True


def _install_noop_dotenv(monkeypatch) -> None:
    monkeypatch.setitem(
        sys.modules,
        "dotenv",
        SimpleNamespace(load_dotenv=lambda **kwargs: None),
    )


def test_worker_startup_rejects_invalid_schedule_settings_before_engine_dispose(
    monkeypatch,
):
    from apps.shared.db import session as session_module

    fake_engine = _Engine()
    _install_noop_dotenv(monkeypatch)
    monkeypatch.setattr(session_module, "engine", fake_engine)
    monkeypatch.setenv("SCHEDULE_DISPATCH_MODE", "unsupported")

    with pytest.raises(ScheduleDispatchDomainError):
        celery_module.init_worker_process()

    assert fake_engine.disposed is False


def test_worker_startup_validates_schedule_settings_then_resets_engine_pool(
    monkeypatch,
):
    from apps.shared.db import session as session_module

    fake_engine = _Engine()
    _install_noop_dotenv(monkeypatch)
    monkeypatch.setattr(session_module, "engine", fake_engine)
    monkeypatch.setenv("SCHEDULE_DISPATCH_MODE", "disabled")

    celery_module.init_worker_process()

    assert fake_engine.disposed is True


def test_worker_process_rechecks_claim_schema_after_pool_reset(monkeypatch):
    from apps.shared.db import session as session_module
    from apps.shared.services import schedule_dispatch_schema_readiness

    fake_engine = _Engine()
    calls = []
    monkeypatch.setattr(session_module, "engine", fake_engine)
    monkeypatch.setenv("SCHEDULE_DISPATCH_MODE", "claim")
    monkeypatch.setenv(
        "SCHEDULE_DISPATCH_MODE_FINGERPRINT",
        CLAIM_FINGERPRINT,
    )
    monkeypatch.setattr(
        schedule_dispatch_schema_readiness,
        "require_schedule_dispatch_migration_ready",
        lambda engine, *, settings: calls.append((engine, settings.mode)),
    )

    celery_module.init_worker_process()

    assert fake_engine.disposed is True
    assert calls == [(fake_engine, "claim")]


def test_worker_init_checks_claim_schema_before_consuming_tasks(monkeypatch):
    from apps.shared.db import session as session_module
    from apps.shared.services import schedule_dispatch_schema_readiness

    fake_engine = _Engine()
    calls = []
    monkeypatch.setattr(session_module, "engine", fake_engine)
    monkeypatch.setenv("SCHEDULE_DISPATCH_MODE", "claim")
    monkeypatch.setenv("SCHEDULE_DISPATCH_MODE_FINGERPRINT", CLAIM_FINGERPRINT)
    monkeypatch.setattr(
        schedule_dispatch_schema_readiness,
        "require_schedule_dispatch_migration_ready",
        lambda engine, *, settings: calls.append((engine, settings.mode)),
    )

    celery_module.validate_worker_schedule_schema()

    assert calls == [(fake_engine, "claim")]


def test_worker_init_propagates_schema_readiness_failure(monkeypatch):
    from apps.shared.db import session as session_module
    from apps.shared.services import schedule_dispatch_schema_readiness

    monkeypatch.setattr(session_module, "engine", _Engine())
    monkeypatch.setenv("SCHEDULE_DISPATCH_MODE", "claim")
    monkeypatch.setenv("SCHEDULE_DISPATCH_MODE_FINGERPRINT", CLAIM_FINGERPRINT)
    monkeypatch.setattr(
        schedule_dispatch_schema_readiness,
        "require_schedule_dispatch_migration_ready",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("safe readiness failure")
        ),
    )

    with pytest.raises(RuntimeError, match="safe readiness failure"):
        celery_module.validate_worker_schedule_schema()
