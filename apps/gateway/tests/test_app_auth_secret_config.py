import pytest
from pydantic import ValidationError

from apps.gateway.core.config import Settings


def test_app_auth_secret_lifecycle_mode_defaults_to_disabled(monkeypatch):
    monkeypatch.delenv("APP_AUTH_SECRET_LIFECYCLE_MODE", raising=False)

    settings = Settings(_env_file=None)

    assert settings.APP_AUTH_SECRET_LIFECYCLE_MODE == "disabled"


@pytest.mark.parametrize("mode", ["disabled", "active"])
def test_app_auth_secret_lifecycle_mode_accepts_only_declared_modes(mode):
    settings = Settings(
        _env_file=None,
        APP_AUTH_SECRET_LIFECYCLE_MODE=mode,
    )

    assert settings.APP_AUTH_SECRET_LIFECYCLE_MODE == mode


def test_app_auth_secret_lifecycle_mode_rejects_unknown_value():
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            APP_AUTH_SECRET_LIFECYCLE_MODE="enabled",
        )
