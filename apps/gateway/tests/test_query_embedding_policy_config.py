import pytest
from pydantic import ValidationError

from apps.gateway.core.config import Settings


def test_query_embedding_policy_writes_are_disabled_by_default(monkeypatch):
    monkeypatch.delenv("QUERY_EMBEDDING_POLICY_WRITE_MODE", raising=False)

    settings = Settings(_env_file=None)

    assert settings.QUERY_EMBEDDING_POLICY_WRITE_MODE == "disabled"


def test_query_embedding_policy_write_mode_accepts_only_server_owned_states():
    assert (
        Settings(
            _env_file=None,
            QUERY_EMBEDDING_POLICY_WRITE_MODE="active",
        ).QUERY_EMBEDDING_POLICY_WRITE_MODE
        == "active"
    )

    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            QUERY_EMBEDDING_POLICY_WRITE_MODE="enabled",
        )
