import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from apps.shared.domain.mail_oauth import GMAIL_MODIFY_SCOPE, GmailOAuthSecret
from apps.shared.services.credential_encryption import EncryptedSecretEnvelope
from apps.workflow_engine.application.mail_processing import (
    GmailDraftRejectedBeforeEffect,
)
from apps.workflow_engine.services.google_oauth_service import GoogleAccessToken
from apps.workflow_engine.services.mail_credential_service import (
    MailCredentialResolver,
    MailCredentialRuntimeError,
)


def _credential(**overrides):
    values = {
        "id": uuid.uuid4(),
        "organization_id": uuid.uuid4(),
        "status": "active",
        "provider": "custom",
        "auth_type": "app_password",
        "email_address": "mailbox@example.test",
        "imap_host": "imap.example.test",
        "imap_port": 993,
        "use_ssl": True,
        "encrypted_secret": "synthetic-ciphertext",
        "encryption_key_version": "v1",
        "encryption_algorithm": "fernet-v1",
        "oauth_refresh_lease_owner_hash": None,
        "oauth_refresh_lease_expires_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _db_returning(value):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = value
    return db


@patch(
    "apps.workflow_engine.services.mail_credential_service."
    "has_mail_credential_permission",
    return_value=True,
)
@patch(
    "apps.workflow_engine.services.mail_credential_service."
    "ensure_network_target_allowed",
    return_value=("imap.example.test", 993, "203.0.113.10"),
)
@patch(
    "apps.workflow_engine.services.mail_credential_service."
    "get_credential_encryption_service"
)
def test_resolver_returns_runtime_secret_only_after_permission(
    encryption_factory, _egress, _permission
):
    credential = _credential()
    encryption_factory.return_value.decrypt.return_value = "synthetic-mail-secret"

    resolved = MailCredentialResolver.resolve(
        _db_returning(credential),
        user_id=uuid.uuid4(),
        organization_id=credential.organization_id,
        credential_id=credential.id,
    )

    assert resolved.credential_id == credential.id
    assert resolved.secret == "synthetic-mail-secret"
    assert resolved.resolved_ip == "203.0.113.10"
    assert "synthetic-mail-secret" not in repr(resolved)
    encryption_factory.return_value.decrypt.assert_called_once_with(
        EncryptedSecretEnvelope(
            ciphertext=credential.encrypted_secret,
            key_version="v1",
            algorithm="fernet-v1",
        )
    )


def test_resolver_rejects_missing_or_cross_organization_credential():
    with pytest.raises(
        MailCredentialRuntimeError, match="^mail.credential_not_available$"
    ):
        MailCredentialResolver.resolve(
            _db_returning(None),
            user_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            credential_id=uuid.uuid4(),
        )


@patch(
    "apps.workflow_engine.services.mail_credential_service."
    "record_resource_permission_denied"
)
@patch(
    "apps.workflow_engine.services.mail_credential_service."
    "get_effective_mail_credential_auth_state",
    return_value="none",
)
@patch(
    "apps.workflow_engine.services.mail_credential_service."
    "has_mail_credential_permission",
    return_value=False,
)
def test_resolver_denies_use_before_decryption(_permission, _auth_state, record_denial):
    credential = _credential()

    with (
        patch(
            "apps.workflow_engine.services.mail_credential_service."
            "get_credential_encryption_service"
        ) as encryption_factory,
        pytest.raises(
            MailCredentialRuntimeError,
            match="^mail.credential_permission_denied$",
        ),
    ):
        MailCredentialResolver.resolve(
            _db_returning(credential),
            user_id=uuid.uuid4(),
            organization_id=credential.organization_id,
            credential_id=credential.id,
        )

    encryption_factory.assert_not_called()
    record_denial.assert_called_once()


@patch(
    "apps.workflow_engine.services.mail_credential_service."
    "has_mail_credential_permission",
    return_value=True,
)
@patch(
    "apps.workflow_engine.services.mail_credential_service."
    "get_credential_encryption_service"
)
def test_serialized_oauth_refresh_locks_and_rotates_in_one_transaction(
    encryption_factory, _permission
):
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    credential = _credential(
        organization_id=organization_id,
        provider="gmail",
        auth_type="oauth2",
    )
    query = MagicMock()
    query.filter.return_value = query
    query.with_for_update.return_value = query
    query.first.return_value = credential
    db = MagicMock()
    db.query.return_value = query
    encryption_factory.return_value.decrypt.return_value = "current-secret"
    encryption_factory.return_value.encrypt.return_value = SimpleNamespace(
        ciphertext="rotated-ciphertext",
        key_version="v2",
        algorithm="fernet-v1",
    )
    replacement = GmailOAuthSecret(
        refresh_token="replacement-refresh-token",
        scopes=(GMAIL_MODIFY_SCOPE,),
    ).serialize()
    refresh = MagicMock(
        return_value=GoogleAccessToken(
            "synthetic-access-token",
            rotated_secret_payload=replacement,
        )
    )

    token = MailCredentialResolver.refresh_oauth_serialized(
        db,
        user_id=user_id,
        organization_id=organization_id,
        credential_id=credential.id,
        refresh=refresh,
    )

    assert token.value == "synthetic-access-token"
    assert query.with_for_update.call_count == 2
    refresh.assert_called_once_with("current-secret")
    assert credential.encrypted_secret == "rotated-ciphertext"
    assert db.commit.call_count == 2
    assert credential.oauth_refresh_lease_owner_hash is None


@patch(
    "apps.workflow_engine.services.mail_credential_service."
    "has_mail_credential_permission",
    return_value=True,
)
@patch(
    "apps.workflow_engine.services.mail_credential_service."
    "get_credential_encryption_service"
)
def test_serialized_invalid_grant_revokes_locked_current_credential(
    encryption_factory, _permission
):
    organization_id = uuid.uuid4()
    credential = _credential(
        organization_id=organization_id,
        provider="gmail",
        auth_type="oauth2",
    )
    query = MagicMock()
    query.filter.return_value = query
    query.with_for_update.return_value = query
    query.first.return_value = credential
    db = MagicMock()
    db.query.return_value = query
    encryption_factory.return_value.decrypt.return_value = "current-secret"

    def refresh(_secret):
        raise GmailDraftRejectedBeforeEffect(
            "mail.oauth_reauthorization_required"
        )

    with pytest.raises(GmailDraftRejectedBeforeEffect):
        MailCredentialResolver.refresh_oauth_serialized(
            db,
            user_id=uuid.uuid4(),
            organization_id=organization_id,
            credential_id=credential.id,
            refresh=refresh,
        )

    assert credential.status == "revoked"
    assert credential.revoked_at is not None
    assert db.commit.call_count == 2
    assert credential.oauth_refresh_lease_owner_hash is None
    audit = db.add.call_args.args[0]
    assert audit.audit_metadata["reason"] == "oauth_reauthorization_required"


@patch(
    "apps.workflow_engine.services.mail_credential_service."
    "has_mail_credential_permission",
    return_value=True,
)
@patch(
    "apps.workflow_engine.services.mail_credential_service."
    "get_credential_encryption_service"
)
def test_serialized_non_invalid_grant_failure_does_not_revoke_credential(
    encryption_factory, _permission
):
    organization_id = uuid.uuid4()
    credential = _credential(
        organization_id=organization_id,
        provider="gmail",
        auth_type="oauth2",
    )
    query = MagicMock()
    query.filter.return_value = query
    query.with_for_update.return_value = query
    query.first.return_value = credential
    db = MagicMock()
    db.query.return_value = query
    encryption_factory.return_value.decrypt.return_value = "current-secret"

    def refresh(_secret):
        raise GmailDraftRejectedBeforeEffect("mail.oauth_token_exchange_failed")

    with pytest.raises(GmailDraftRejectedBeforeEffect):
        MailCredentialResolver.refresh_oauth_serialized(
            db,
            user_id=uuid.uuid4(),
            organization_id=organization_id,
            credential_id=credential.id,
            refresh=refresh,
        )

    assert credential.status == "active"
    assert db.commit.call_count == 2
    db.add.assert_not_called()
    assert credential.oauth_refresh_lease_owner_hash is None


@patch(
    "apps.workflow_engine.services.mail_credential_service."
    "has_mail_credential_permission",
    return_value=True,
)
@patch(
    "apps.workflow_engine.services.mail_credential_service."
    "get_credential_encryption_service"
)
def test_active_oauth_refresh_lease_blocks_duplicate_provider_call(
    encryption_factory, _permission
):
    organization_id = uuid.uuid4()
    credential = _credential(
        organization_id=organization_id,
        provider="gmail",
        auth_type="oauth2",
        oauth_refresh_lease_owner_hash="existing-owner",
        oauth_refresh_lease_expires_at=datetime.now(timezone.utc)
        + timedelta(seconds=10),
    )
    query = MagicMock()
    query.filter.return_value = query
    query.with_for_update.return_value = query
    query.first.return_value = credential
    db = MagicMock()
    db.query.return_value = query
    refresh = MagicMock()

    with pytest.raises(MailCredentialRuntimeError) as exc_info:
        MailCredentialResolver.refresh_oauth_serialized(
            db,
            user_id=uuid.uuid4(),
            organization_id=organization_id,
            credential_id=credential.id,
            refresh=refresh,
        )

    assert exc_info.value.reason_code == "mail.oauth_refresh_in_progress"
    refresh.assert_not_called()
    encryption_factory.return_value.decrypt.assert_not_called()
