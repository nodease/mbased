from apps.shared.db.models.mail_credential import MailCredential
from apps.shared.db.models.mail_processing import (
    MailDraftEffect,
    MailMessageProcessing,
)


def test_mail_processing_schema_keeps_content_out_of_durable_rows():
    processing_columns = set(MailMessageProcessing.__table__.columns.keys())
    effect_columns = set(MailDraftEffect.__table__.columns.keys())

    for forbidden in {
        "body",
        "snippet",
        "subject",
        "sender",
        "recipient",
        "mime",
        "raw_response",
        "raw_error",
        "access_token",
        "refresh_token",
    }:
        assert forbidden not in processing_columns
        assert forbidden not in effect_columns


def test_mail_processing_logical_identity_unique_constraint_is_workflow_scoped():
    unique_constraints = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in MailMessageProcessing.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert unique_constraints["uq_mail_message_processings_logical_message"] == (
        "organization_id",
        "workflow_id",
        "source_node_id",
        "credential_id",
        "provider",
        "message_identity_hash",
    )


def test_mail_draft_effect_operation_is_unique_per_processing_and_node():
    unique_constraints = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in MailDraftEffect.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert unique_constraints["uq_mail_draft_effects_operation"] == (
        "processing_id",
        "node_id",
        "operation_key_hash",
    )


def test_mail_processing_models_define_state_shape_constraints():
    processing_checks = {
        constraint.name
        for constraint in MailMessageProcessing.__table__.constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    }
    effect_checks = {
        constraint.name
        for constraint in MailDraftEffect.__table__.constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    }

    assert "ck_mail_message_processings_status_shape" in processing_checks
    assert "ck_mail_draft_effects_status_shape" in effect_checks
    assert "next_attempt_at" in MailDraftEffect.__table__.columns
    assert "required_effect_contract_hash" in MailMessageProcessing.__table__.columns


def test_mail_credential_oauth_refresh_lease_is_paired_and_bounded():
    checks = {
        constraint.name
        for constraint in MailCredential.__table__.constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    }

    assert "oauth_refresh_lease_owner_hash" in MailCredential.__table__.columns
    assert "oauth_refresh_lease_expires_at" in MailCredential.__table__.columns
    assert "ck_mail_credentials_oauth_refresh_lease_pair" in checks
