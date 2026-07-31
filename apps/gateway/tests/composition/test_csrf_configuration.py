import pytest

from apps.gateway.composition.csrf import csrf_enforcement_enabled


def test_csrf_enforcement_defaults_to_enabled(monkeypatch):
    monkeypatch.delenv("CSRF_ENFORCEMENT_MODE", raising=False)
    monkeypatch.setenv("NODE_ENV", "production")

    assert csrf_enforcement_enabled() is True


def test_csrf_enforcement_can_only_be_disabled_in_test(monkeypatch):
    monkeypatch.setenv("CSRF_ENFORCEMENT_MODE", "disabled")
    monkeypatch.setenv("NODE_ENV", "test")

    assert csrf_enforcement_enabled() is False

    monkeypatch.setenv("NODE_ENV", "production")
    with pytest.raises(RuntimeError, match="CSRF enforcement mode is invalid"):
        csrf_enforcement_enabled()


def test_unknown_csrf_enforcement_mode_fails_closed(monkeypatch):
    monkeypatch.setenv("CSRF_ENFORCEMENT_MODE", "monitor")
    monkeypatch.setenv("NODE_ENV", "development")

    with pytest.raises(RuntimeError, match="CSRF enforcement mode is invalid"):
        csrf_enforcement_enabled()
