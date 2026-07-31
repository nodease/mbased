import pytest

from apps.gateway.core.http_security import (
    parse_credentialed_cors_origins,
    resolve_session_signing_secret,
)


def test_production_requires_non_default_session_signing_secret():
    with pytest.raises(RuntimeError, match="not configured"):
        resolve_session_signing_secret(None, node_env="production")

    with pytest.raises(RuntimeError, match="not configured"):
        resolve_session_signing_secret(
            "your-secret-key-change-in-production",
            node_env="production",
        )

    with pytest.raises(RuntimeError, match="not configured"):
        resolve_session_signing_secret("   ", node_env="production")

    with pytest.raises(RuntimeError, match="not configured"):
        resolve_session_signing_secret(
            " your-secret-key-change-in-production ",
            node_env="production",
        )


def test_non_production_can_use_development_session_signing_fallback():
    assert resolve_session_signing_secret(None, node_env="development")


def test_production_environment_check_is_case_and_whitespace_insensitive():
    with pytest.raises(RuntimeError, match="not configured"):
        resolve_session_signing_secret(None, node_env=" Production ")


def test_explicit_session_signing_secret_is_returned_without_logging():
    configured = "synthetic-test-session-signing-value"

    assert (
        resolve_session_signing_secret(configured, node_env="production")
        == configured
    )


def test_credentialed_cors_origins_are_trimmed_and_deduplicated():
    assert parse_credentialed_cors_origins(
        " https://CLIENT.example:443,https://client.example/ ",
        node_env="production",
    ) == ["https://client.example"]


@pytest.mark.parametrize(
    "raw_value",
    [
        "http://client.example",
        "http://localhost:3000",
        "https://client.example,http://127.0.0.1:3000",
    ],
)
def test_production_credentialed_cors_requires_https(raw_value):
    with pytest.raises(RuntimeError, match="HTTPS"):
        parse_credentialed_cors_origins(raw_value, node_env="production")


@pytest.mark.parametrize(
    "origin",
    [
        "https://10.0.0.10",
        "https://169.254.169.254",
        "https://api-service",
        "https://api.internal",
        "https://api.local",
        "https://api.home.arpa",
        "https://gateway.default.svc",
        "https://gateway.default.svc.",
        "https://gateway.default.svc.cluster.local",
        "https://gateway.default.svc.cluster.local.",
    ],
)
def test_production_credentialed_cors_rejects_non_public_origins(origin):
    with pytest.raises(RuntimeError):
        parse_credentialed_cors_origins(origin, node_env="production")


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://[::1]:3000",
    ],
)
def test_development_credentialed_cors_allows_loopback_http(origin):
    assert parse_credentialed_cors_origins(origin, node_env="development") == [origin]


def test_development_credentialed_cors_rejects_non_loopback_http():
    with pytest.raises(RuntimeError, match="loopback"):
        parse_credentialed_cors_origins(
            "http://client.example",
            node_env="development",
        )


def test_development_credentialed_cors_rejects_private_https_origin():
    with pytest.raises(RuntimeError, match="public HTTPS"):
        parse_credentialed_cors_origins(
            "https://gateway.default.svc.cluster.local",
            node_env="development",
        )


def test_missing_environment_uses_production_cors_policy():
    with pytest.raises(RuntimeError, match="HTTPS"):
        parse_credentialed_cors_origins("http://localhost:3000", node_env=None)


@pytest.mark.parametrize(
    "raw_value",
    [
        "*",
        "",
        "null",
        "https://user@client.example",
        "https://client.example/path",
        "https://client.example?query=1",
        "https://client.example.",
        "https://*.client.example",
        "https://client.example:not-a-port",
        "https://client.example:70000",
        "javascript:alert(1)",
    ],
)
def test_credentialed_cors_origins_fail_closed(raw_value):
    with pytest.raises(RuntimeError):
        parse_credentialed_cors_origins(raw_value, node_env="production")
