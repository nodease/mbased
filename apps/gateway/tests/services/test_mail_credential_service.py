import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError

from apps.gateway.services.mail_credential_service import (
    MailCredentialEgressDenied,
    MailCredentialNotFound,
    MailCredentialOAuthManaged,
    MailCredentialPermissionDenied,
    MailCredentialPersistenceFailed,
    MailCredentialRevoked,
    MailCredentialService,
    MailCredentialTargetNotFound,
)
from apps.shared.db.models.audit_log import AuditLog
from apps.shared.domain.mail_oauth import GMAIL_MODIFY_SCOPE, GmailOAuthSecret
from apps.shared.schemas.mail_credential import (
    MailCredentialCreate,
    MailCredentialPermissionGrant,
    MailCredentialUpdate,
)
from apps.shared.services.credential_encryption import EncryptedSecretEnvelope


NOW = datetime(2026, 7, 11, tzinfo=timezone.utc)


def _payload(secret: str = "synthetic-mail-secret") -> MailCredentialCreate:
    return MailCredentialCreate(
        credential_name="업무 메일",
        provider="gmail",
        email_address="mailbox@example.test",
        auth_type="app_password",
        secret=secret,
        imap_host="imap.example.test",
        imap_port=993,
        use_ssl=True,
    )


def _credential(**overrides):
    values = {
        "id": uuid.uuid4(),
        "organization_id": uuid.uuid4(),
        "credential_name": "업무 메일",
        "provider": "gmail",
        "email_address": "mailbox@example.test",
        "auth_type": "app_password",
        "imap_host": "imap.example.test",
        "imap_port": 993,
        "use_ssl": True,
        "status": "active",
        "created_at": NOW,
        "updated_at": NOW,
        "revoked_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_safe_response_masks_mailbox_and_never_serializes_secret_fields():
    response = MailCredentialService.to_response(_credential())

    payload = response.model_dump()
    assert payload["email_preview"] == "m***@example.test"
    assert "email_address" not in payload
    assert "secret" not in payload
    assert "encrypted_secret" not in payload


def test_picker_option_uses_minimum_safe_metadata_allowlist():
    payload = MailCredentialService.to_option_response(_credential()).model_dump()

    assert set(payload) == {
        "id",
        "credential_name",
        "provider",
        "auth_type",
        "email_preview",
        "status",
    }


@patch(
    "apps.gateway.services.mail_credential_service.ensure_network_target_allowed",
    return_value=("imap.example.test", 993, "203.0.113.10"),
)
@patch(
    "apps.gateway.services.mail_credential_service.has_organization_manager_permission",
    return_value=True,
)
def test_create_encrypts_secret_before_persistence(_manager, _egress):
    db = MagicMock()
    encryption = MagicMock()
    encryption.encrypt.return_value = EncryptedSecretEnvelope(
        ciphertext="synthetic-ciphertext",
        key_version="v2",
    )

    def flush():
        row = db.add.call_args_list[0].args[0]
        row.id = uuid.uuid4()

    def refresh(row):
        row.created_at = NOW
        row.updated_at = NOW
        row.revoked_at = None

    db.flush.side_effect = flush
    db.refresh.side_effect = refresh
    service = MailCredentialService(db, encryption=encryption)

    response = service.create(uuid.uuid4(), uuid.uuid4(), _payload())

    stored = db.add.call_args_list[0].args[0]
    audit = db.add.call_args_list[1].args[0]
    assert stored.encrypted_secret == "synthetic-ciphertext"
    assert stored.encryption_key_version == "v2"
    assert not hasattr(stored, "secret")
    assert response.email_preview == "m***@example.test"
    assert isinstance(audit, AuditLog)
    assert audit.target_id == str(stored.id)
    assert "email_address" not in audit.audit_metadata
    assert "encrypted_secret" not in audit.audit_metadata
    db.commit.assert_called_once()


@patch(
    "apps.gateway.services.mail_credential_service.has_organization_manager_permission",
    return_value=True,
)
def test_create_gmail_oauth_encrypts_refresh_payload_and_returns_safe_metadata(_manager):
    db = MagicMock()
    encryption = MagicMock()
    encryption.encrypt.return_value = EncryptedSecretEnvelope(
        ciphertext="synthetic-oauth-ciphertext",
        key_version="v2",
    )

    def flush():
        row = db.add.call_args_list[0].args[0]
        row.id = uuid.uuid4()

    def refresh(row):
        row.created_at = NOW
        row.updated_at = NOW
        row.revoked_at = None

    db.flush.side_effect = flush
    db.refresh.side_effect = refresh
    response = MailCredentialService(db, encryption=encryption).create_gmail_oauth(
        actor_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        credential_name="Gmail OAuth",
        email_address="Mailbox@Example.test",
        refresh_token="synthetic-refresh-token",
        scopes=(GMAIL_MODIFY_SCOPE,),
    )

    protected_input = encryption.encrypt.call_args.args[0]
    parsed = GmailOAuthSecret.parse(protected_input)
    stored = db.add.call_args_list[0].args[0]
    audit = db.add.call_args_list[1].args[0]
    assert parsed.refresh_token == "synthetic-refresh-token"
    assert stored.auth_type == "oauth2"
    assert stored.encrypted_secret == "synthetic-oauth-ciphertext"
    assert response.email_preview == "m***@example.test"
    assert "refresh_token" not in response.model_dump()
    assert isinstance(audit, AuditLog)
    assert audit.target_id == str(stored.id)
    assert "email_address" not in audit.audit_metadata
    assert "encrypted_secret" not in audit.audit_metadata
    db.commit.assert_called_once()


@patch(
    "apps.gateway.services.mail_credential_service.ensure_network_target_allowed",
    return_value=("imap.example.test", 993, "203.0.113.10"),
)
@patch(
    "apps.gateway.services.mail_credential_service.has_organization_manager_permission",
    return_value=True,
)
def test_create_rolls_back_when_credential_or_audit_commit_fails(_manager, _egress):
    db = MagicMock()
    db.flush.side_effect = IntegrityError(
        "INSERT mail_credentials",
        {
            "email_address": "mailbox@example.test",
            "encrypted_secret": "synthetic-ciphertext",
        },
        Exception("failed"),
    )
    encryption = MagicMock()
    encryption.encrypt.return_value = EncryptedSecretEnvelope(
        ciphertext="synthetic-ciphertext",
        key_version="v1",
    )

    with pytest.raises(
        MailCredentialPersistenceFailed,
        match="^$",
    ) as exc_info:
        MailCredentialService(db, encryption=encryption).create(
            uuid.uuid4(), uuid.uuid4(), _payload()
        )

    assert exc_info.value.code == "mail.credential_persistence_failed"
    assert "mailbox@example.test" not in str(exc_info.value)
    assert "synthetic-ciphertext" not in str(exc_info.value)
    db.rollback.assert_called_once()


@patch(
    "apps.gateway.services.mail_credential_service.record_resource_permission_denied"
)
@patch(
    "apps.gateway.services.mail_credential_service."
    "get_effective_mail_credential_auth_state",
    return_value="none",
)
@patch(
    "apps.gateway.services.mail_credential_service.has_organization_manager_permission",
    return_value=False,
)
def test_create_denial_happens_before_encryption(_manager, _auth_state, record_denial):
    encryption = MagicMock()
    service = MailCredentialService(MagicMock(), encryption=encryption)

    with pytest.raises(MailCredentialPermissionDenied):
        service.create(uuid.uuid4(), uuid.uuid4(), _payload())

    encryption.encrypt.assert_not_called()
    record_denial.assert_called_once()


def test_cross_organization_lookup_is_hidden_as_not_found():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    service = MailCredentialService(db, encryption=MagicMock())

    with pytest.raises(MailCredentialNotFound):
        service.get(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())


@patch(
    "apps.gateway.services.mail_credential_service.get_organization_auth_state",
    return_value="member",
)
def test_list_available_batches_direct_and_team_permission_sources(_org_state):
    organization_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    direct_credential = _credential(organization_id=organization_id)
    team_credential = _credential(organization_id=organization_id)
    denied_credential = _credential(organization_id=organization_id)
    credentials_query = MagicMock()
    direct_query = MagicMock()
    team_query = MagicMock()
    for query in (credentials_query, direct_query, team_query):
        query.filter.return_value = query
        query.order_by.return_value = query
        query.join.return_value = query
    credentials_query.all.return_value = [
        direct_credential,
        team_credential,
        denied_credential,
    ]
    direct_query.all.return_value = [(direct_credential.id, "operator")]
    team_query.all.return_value = [(team_credential.id, "builder")]
    db = MagicMock()
    db.query.side_effect = [credentials_query, direct_query, team_query]

    result = MailCredentialService(db, encryption=MagicMock()).list_available(
        actor_id, organization_id
    )

    assert [item.id for item in result] == [direct_credential.id, team_credential.id]
    assert db.query.call_count == 3


def test_empty_and_null_patch_are_rejected_by_schema():
    with pytest.raises(ValueError):
        MailCredentialUpdate()
    with pytest.raises(ValueError):
        MailCredentialUpdate(use_ssl=None)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("email_address", "other@example.test"),
        ("provider", "custom"),
        ("auth_type", "password"),
        ("imap_host", "attacker.example.test"),
        ("imap_port", 143),
        ("use_ssl", False),
    ],
)
def test_patch_rejects_mailbox_identity_and_endpoint_changes(field_name, value):
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        MailCredentialUpdate.model_validate({field_name: value})


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("email_address", "mailbox@example.test\r\nA1 NOOP"),
        ("secret", "synthetic\nA1 NOOP"),
    ],
)
def test_create_rejects_imap_command_control_characters(field_name, value):
    payload = _payload().model_dump()
    payload[field_name] = value

    with pytest.raises(ValueError, match="forbidden control character"):
        MailCredentialCreate.model_validate(payload)


def test_secret_rotation_rejects_imap_command_control_characters():
    with pytest.raises(ValueError, match="forbidden control character"):
        MailCredentialUpdate(secret="synthetic\r\nA1 NOOP")


def test_endpoint_mode_rejects_plaintext_or_mismatched_imap_port():
    with pytest.raises(MailCredentialEgressDenied):
        MailCredentialService._validated_endpoint(
            "imap.example.test", 143, use_ssl=True
        )

    with pytest.raises(MailCredentialEgressDenied):
        MailCredentialService._validated_endpoint(
            "imap.example.test", 993, use_ssl=False
        )


@pytest.mark.parametrize("operation", ["update", "grant_user_permission"])
def test_revoked_credential_rejects_mutation(operation):
    db = MagicMock()
    service = MailCredentialService(db, encryption=MagicMock())
    credential = _credential(status="revoked", revoked_at=NOW)
    service._get_scoped = MagicMock(return_value=credential)
    service._require = MagicMock()

    with pytest.raises(MailCredentialRevoked):
        if operation == "update":
            service.update(
                uuid.uuid4(),
                credential.organization_id,
                credential.id,
                MailCredentialUpdate(credential_name="changed"),
            )
        else:
            from apps.shared.schemas.mail_credential import (
                MailCredentialPermissionGrant,
            )

            service.grant_user_permission(
                uuid.uuid4(),
                credential.organization_id,
                credential.id,
                uuid.uuid4(),
                MailCredentialPermissionGrant(auth_state="operator"),
            )

    db.commit.assert_not_called()


def test_oauth_credential_secret_can_only_change_through_oauth_flow():
    db = MagicMock()
    encryption = MagicMock()
    service = MailCredentialService(db, encryption=encryption)
    credential = _credential(auth_type="oauth2")
    service._get_scoped = MagicMock(return_value=credential)
    service._require = MagicMock()

    with pytest.raises(MailCredentialOAuthManaged):
        service.update(
            uuid.uuid4(),
            credential.organization_id,
            credential.id,
            MailCredentialUpdate(secret="synthetic-replacement"),
        )

    encryption.encrypt.assert_not_called()
    db.commit.assert_not_called()


def test_grant_user_permission_rejects_deactivated_active_member():
    db = MagicMock()
    membership_query = MagicMock()
    db.query.return_value = membership_query
    membership_query.join.return_value = membership_query
    membership_query.filter.return_value = membership_query
    membership_query.with_for_update.return_value = membership_query
    membership_query.first.return_value = None

    service = MailCredentialService(db, encryption=MagicMock())
    credential = _credential()
    service._get_scoped = MagicMock(return_value=credential)
    service._require = MagicMock()

    with pytest.raises(MailCredentialTargetNotFound):
        service.grant_user_permission(
            uuid.uuid4(),
            credential.organization_id,
            credential.id,
            uuid.uuid4(),
            MailCredentialPermissionGrant(auth_state="operator"),
        )

    predicates = {str(predicate) for predicate in membership_query.filter.call_args.args}
    assert "users.deactivated_at IS NULL" in predicates
    membership_query.join.assert_called_once()
    membership_query.with_for_update.assert_called_once_with()
    db.commit.assert_not_called()


def test_bulk_mail_permission_grant_commits_all_pairs_once():
    db = MagicMock()
    service = MailCredentialService(db, encryption=MagicMock())
    service._get_scoped = MagicMock(
        side_effect=lambda organization_id, credential_id, **_: _credential(
            id=credential_id,
            organization_id=organization_id,
        )
    )
    service._require = MagicMock()
    service.grant_team_permission = MagicMock()
    credential_ids = [uuid.uuid4(), uuid.uuid4()]
    team_ids = [uuid.uuid4(), uuid.uuid4()]
    payload = MailCredentialPermissionGrant(auth_state="operator")

    service.grant_permissions_bulk(
        uuid.uuid4(),
        uuid.uuid4(),
        credential_ids,
        "team",
        team_ids,
        payload,
    )

    assert service.grant_team_permission.call_count == 4
    assert all(
        call.kwargs["commit"] is False
        for call in service.grant_team_permission.call_args_list
    )
    db.commit.assert_called_once_with()


def test_bulk_mail_permission_grant_rolls_back_when_one_pair_fails():
    db = MagicMock()
    service = MailCredentialService(db, encryption=MagicMock())
    service._get_scoped = MagicMock(
        side_effect=lambda organization_id, credential_id, **_: _credential(
            id=credential_id,
            organization_id=organization_id,
        )
    )
    service._require = MagicMock()
    service.grant_team_permission = MagicMock(
        side_effect=[None, MailCredentialRevoked()]
    )

    with pytest.raises(MailCredentialRevoked):
        service.grant_permissions_bulk(
            uuid.uuid4(),
            uuid.uuid4(),
            [uuid.uuid4()],
            "team",
            [uuid.uuid4(), uuid.uuid4()],
            MailCredentialPermissionGrant(auth_state="operator"),
        )

    db.commit.assert_not_called()
    db.rollback.assert_called_once_with()


def test_bulk_mail_permission_grant_rolls_back_unexpected_persistence_failure():
    db = MagicMock()
    service = MailCredentialService(db, encryption=MagicMock())
    service._get_scoped = MagicMock(
        side_effect=lambda organization_id, credential_id, **_: _credential(
            id=credential_id,
            organization_id=organization_id,
        )
    )
    service._require = MagicMock()
    service.grant_team_permission = MagicMock(
        side_effect=[None, RuntimeError("permission insert failed")]
    )

    with pytest.raises(RuntimeError, match="permission insert failed"):
        service.grant_permissions_bulk(
            uuid.uuid4(),
            uuid.uuid4(),
            [uuid.uuid4()],
            "team",
            [uuid.uuid4(), uuid.uuid4()],
            MailCredentialPermissionGrant(auth_state="operator"),
        )

    db.commit.assert_not_called()
    db.rollback.assert_called_once_with()


def test_bulk_mail_permission_grant_locks_all_resources_before_grantees():
    db = MagicMock()
    service = MailCredentialService(db, encryption=MagicMock())
    events: list[tuple[str, uuid.UUID]] = []
    credential_ids = [uuid.UUID(int=2), uuid.UUID(int=1)]
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()

    def lock_credential(
        scoped_organization_id: uuid.UUID,
        credential_id: uuid.UUID,
        **_: object,
    ):
        events.append(("lock", credential_id))
        return _credential(
            id=credential_id,
            organization_id=scoped_organization_id,
        )

    def grant_user(
        _actor_id: uuid.UUID,
        _organization_id: uuid.UUID,
        credential_id: uuid.UUID,
        _user_id: uuid.UUID,
        _payload: MailCredentialPermissionGrant,
        **_: object,
    ) -> None:
        events.append(("grant", credential_id))

    service._get_scoped = MagicMock(side_effect=lock_credential)
    service._require = MagicMock()
    service.grant_user_permission = MagicMock(side_effect=grant_user)

    service.grant_permissions_bulk(
        uuid.uuid4(),
        organization_id,
        credential_ids,
        "user",
        [user_id],
        MailCredentialPermissionGrant(auth_state="operator"),
    )

    assert events == [
        ("lock", uuid.UUID(int=1)),
        ("lock", uuid.UUID(int=2)),
        ("grant", uuid.UUID(int=1)),
        ("grant", uuid.UUID(int=2)),
    ]
