import pytest
from apps.shared.schemas.connector import ConnectorTestRequest, DBConnectionTestRequest
from pydantic import ValidationError


def payload() -> dict:
    return {
        "connection_name": "public-db",
        "type": "postgres",
        "host": "db.example.com",
        "port": 5432,
        "database": "app",
        "username": "app-user",
        "password": "placeholder-secret",
        "ssh": None,
    }


def test_connector_test_schema_accepts_strict_postgres_shape() -> None:
    request_payload = payload()
    request_payload["connection_name"] = "  public-db  "

    model = ConnectorTestRequest.model_validate(request_payload)

    assert model.type == "postgres"
    assert model.port == 5432
    assert model.connection_name == "public-db"
    assert model.password.get_secret_value() == "placeholder-secret"
    assert "placeholder-secret" not in repr(model)


def test_connector_test_schema_accepts_bounded_custom_port() -> None:
    request_payload = payload()
    request_payload["port"] = 55432

    assert ConnectorTestRequest.model_validate(request_payload).port == 55432


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("type", "mysql"),
        ("port", 0),
        ("port", 65536),
        ("port", "55432"),
        ("connection_name", ""),
        ("connection_name", " \t\n "),
        ("host", "h" * 254),
        ("database", "d" * 129),
        ("username", "u" * 129),
        ("password", "p" * 1025),
    ],
)
def test_connector_test_schema_rejects_unsupported_or_oversized_fields(
    field: str,
    value: object,
) -> None:
    request_payload = payload()
    request_payload[field] = value

    with pytest.raises(ValidationError):
        ConnectorTestRequest.model_validate(request_payload)


def test_persisted_connector_schema_normalizes_connection_name() -> None:
    model = DBConnectionTestRequest.model_validate(
        {**payload(), "connection_name": "  team database  "}
    )

    assert model.connection_name == "team database"


@pytest.mark.parametrize("connection_name", ["", "   ", "x" * 101])
def test_persisted_connector_schema_rejects_invalid_connection_name(
    connection_name: str,
) -> None:
    with pytest.raises(ValidationError):
        DBConnectionTestRequest.model_validate(
            {**payload(), "connection_name": connection_name}
        )


def test_connector_test_schema_rejects_unknown_fields() -> None:
    request_payload = payload()
    request_payload["dsn"] = "postgresql://raw-value"

    with pytest.raises(ValidationError):
        ConnectorTestRequest.model_validate(request_payload)


@pytest.mark.parametrize(
    ("ssh_field", "length"),
    [("password", 1025), ("private_key", 16 * 1024 + 1)],
)
def test_connector_test_schema_bounds_unsupported_ssh_secrets(
    ssh_field: str,
    length: int,
) -> None:
    request_payload = payload()
    request_payload["ssh"] = {
        "enabled": True,
        "host": "bastion.example.com",
        "username": "ssh-user",
        ssh_field: "x" * length,
    }

    with pytest.raises(ValidationError):
        ConnectorTestRequest.model_validate(request_payload)
