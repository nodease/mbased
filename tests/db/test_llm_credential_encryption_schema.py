from unittest.mock import MagicMock

import pytest

from apps.shared.alembic.versions import (
    c2e8f4a91d67_add_llm_credential_encryption_envelope as revision,
)
from apps.shared.db.models.llm import LLMCredential


def test_llm_credential_model_declares_nullable_encryption_envelope_metadata():
    table = LLMCredential.__table__

    assert table.c.encryption_key_version.nullable is True
    assert table.c.encryption_algorithm.nullable is True
    assert "ck_llm_credentials_encryption_metadata_pair" in {
        constraint.name for constraint in table.constraints
    }
    assert "ix_llm_credentials_encryption_key_version" in {
        index.name for index in table.indexes
    }


def test_llm_credential_encryption_revision_extends_current_head():
    assert revision.revision == "c2e8f4a91d67"
    assert revision.down_revision == "b0c1d2e3f4a5"


def test_llm_credential_encryption_downgrade_rejects_encrypted_rows(monkeypatch):
    connection = MagicMock()
    connection.execute.return_value.first.return_value = object()
    monkeypatch.setattr(revision.op, "get_bind", lambda: connection)
    drop_index = MagicMock()
    monkeypatch.setattr(revision.op, "drop_index", drop_index)

    with pytest.raises(RuntimeError, match="encrypted rows exist"):
        revision.downgrade()

    drop_index.assert_not_called()
