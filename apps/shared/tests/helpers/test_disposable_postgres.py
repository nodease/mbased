import pytest
from apps.shared.tests.helpers.disposable_postgres import (
    DisposablePostgresConfig,
    DisposablePostgresConfigurationError,
    quote_disposable_database_name,
)


def _environment(**overrides):
    values = {
        "DB_HOST": "localhost",
        "DB_PORT": "5432",
        "DB_USER": "test-user",
        "DB_PASSWORD": "test-password",
    }
    values.update(overrides)
    return values


@pytest.mark.parametrize("missing", ["DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD"])
def test_disposable_postgres_requires_explicit_connection_settings(missing):
    environment = _environment()
    del environment[missing]

    with pytest.raises(
        DisposablePostgresConfigurationError,
        match="explicit disposable PostgreSQL connection settings are required",
    ):
        DisposablePostgresConfig.from_environment(environment)


def test_disposable_postgres_requires_exact_confirmation_for_remote_host():
    environment = _environment(DB_HOST="database.internal")

    with pytest.raises(
        DisposablePostgresConfigurationError,
        match="requires exact confirmation",
    ):
        DisposablePostgresConfig.from_environment(environment)

    environment["NODEASE_DISPOSABLE_DB_HOST_CONFIRM"] = "database.internal"
    config = DisposablePostgresConfig.from_environment(environment)
    assert config.host == "database.internal"


def test_disposable_postgres_password_is_redacted_from_representation():
    config = DisposablePostgresConfig.from_environment(_environment())

    assert "test-password" not in repr(config)
    assert "test-password" not in str(config.database_url("postgres"))


def test_disposable_postgres_subprocess_environment_overrides_ambient_urls(tmp_path):
    environment = _environment()
    config = DisposablePostgresConfig.from_environment(environment)

    result = config.subprocess_environment(
        database="mbased_lifecycle_0123456789ab",
        root_dir=tmp_path,
    )

    assert "DATABASE_URL" not in result
    assert "SQLALCHEMY_DATABASE_URI" not in result
    assert result["DB_NAME"] == "mbased_lifecycle_0123456789ab"
    assert result["DB_HOST"] == "localhost"


@pytest.mark.parametrize(
    "reserved_key",
    [
        "DATABASE_URL",
        "sqlalchemy_database_uri",
        "DB_HOST",
        "db_password",
        "DB_NAME",
        "PGHOST",
        "PYTHONPATH",
    ],
)
def test_disposable_postgres_extra_rejects_reserved_connection_keys(
    tmp_path,
    reserved_key,
):
    config = DisposablePostgresConfig.from_environment(_environment())

    with pytest.raises(
        DisposablePostgresConfigurationError,
        match="cannot override disposable PostgreSQL settings",
    ):
        config.subprocess_environment(
            database="mbased_lifecycle_0123456789ab",
            root_dir=tmp_path,
            extra={reserved_key: "untrusted-value"},
        )


def test_disposable_postgres_extra_preserves_non_connection_settings(tmp_path):
    config = DisposablePostgresConfig.from_environment(_environment())

    result = config.subprocess_environment(
        database="mbased_lifecycle_0123456789ab",
        root_dir=tmp_path,
        extra={"NODEASE_DEMO_REGENERATE_KNOWLEDGE_FIXTURE": "0"},
    )

    assert result["NODEASE_DEMO_REGENERATE_KNOWLEDGE_FIXTURE"] == "0"
    assert result["DB_HOST"] == config.host


@pytest.mark.parametrize(
    ("database", "prefix"),
    [
        ("production", "mbased_lifecycle"),
        ("mbased_lifecycle_0123456789ab_extra", "mbased_lifecycle"),
        ("mbased_lifecycle_0123456789az", "mbased_lifecycle"),
        ("mbased_lifecycle_0123456789ab", "unsafe-prefix"),
    ],
)
def test_disposable_database_name_is_strictly_allowlisted(database, prefix):
    with pytest.raises(DisposablePostgresConfigurationError):
        quote_disposable_database_name(database, prefix=prefix)


def test_disposable_database_name_quotes_allowlisted_random_name():
    assert (
        quote_disposable_database_name(
            "mbased_lifecycle_0123456789ab",
            prefix="mbased_lifecycle",
        )
        == '"mbased_lifecycle_0123456789ab"'
    )
