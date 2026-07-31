import pytest

from apps.gateway.core.auth_login_security import LoginSecuritySettings


def _production_env(**overrides):
    values = {
        "NODE_ENV": "production",
        "AUTH_LOGIN_FINGERPRINT_KEYS": '{"v1":"0123456789abcdef0123456789abcdef"}',
        "AUTH_LOGIN_FINGERPRINT_PRIMARY_VERSION": "v1",
    }
    values.update(overrides)
    return values


def test_production_requires_dedicated_keyring():
    with pytest.raises(RuntimeError, match="fingerprint keyring is required"):
        LoginSecuritySettings.from_environment({"NODE_ENV": "production"})


def test_primary_key_is_evaluated_before_previous_rotation_key():
    settings = LoginSecuritySettings.from_environment(
        _production_env(
            AUTH_LOGIN_FINGERPRINT_KEYS=(
                '{"v1":"0123456789abcdef0123456789abcdef",'
                '"v2":"abcdef0123456789abcdef0123456789"}'
            ),
            AUTH_LOGIN_FINGERPRINT_PRIMARY_VERSION="v2",
        )
    )

    assert tuple(settings.fingerprint_keyring) == ("v2", "v1")
    assert "abcdef" not in repr(settings)


@pytest.mark.parametrize(
    "overrides",
    [
        {"AUTH_LOGIN_FINGERPRINT_PRIMARY_VERSION": "missing"},
        {"AUTH_LOGIN_FINGERPRINT_KEYS": '{"v1":"short"}'},
        {
            "AUTH_LOGIN_FINGERPRINT_KEYS": (
                '{"v1":"0123456789abcdef0123456789abcdef",'
                '"v2":"abcdef0123456789abcdef0123456789",'
                '"v3":"fedcba9876543210fedcba9876543210"}'
            )
        },
        {"AUTH_LOGIN_FINGERPRINT_KEYS": "not-json"},
        {"AUTH_LOGIN_FINGERPRINT_PRIMARY_VERSION": "unsafe version"},
    ],
)
def test_invalid_keyring_configuration_fails_fast(overrides):
    with pytest.raises(RuntimeError, match="invalid login security configuration"):
        LoginSecuritySettings.from_environment(_production_env(**overrides))


def test_development_can_derive_domain_separated_fallback_key():
    settings = LoginSecuritySettings.from_environment(
        {
            "NODE_ENV": "development",
            "SECRET_KEY": "synthetic-development-session-key-32-bytes",
        }
    )

    assert tuple(settings.fingerprint_keyring) == ("dev-v1",)
    assert settings.fingerprint_keyring["dev-v1"] != (
        b"synthetic-development-session-key-32-bytes"
    )


def test_proxy_and_redis_settings_are_validated():
    settings = LoginSecuritySettings.from_environment(
        {
            "NODE_ENV": "development",
            "SECRET_KEY": "synthetic-development-session-key-32-bytes",
            "AUTH_LOGIN_TRUSTED_PROXY_CIDRS": "10.0.0.0/8,2001:db8::/32",
            "AUTH_LOGIN_LIMITER_REDIS_DB": "3",
            "REDIS_PORT": "6380",
        }
    )

    assert settings.trusted_proxy_cidrs == ("10.0.0.0/8", "2001:db8::/32")
    assert settings.redis_db == 3
    assert settings.redis_port == 6380
