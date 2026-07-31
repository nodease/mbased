import uuid

import pytest
from apps.shared.domain.mail_processing import (
    MailProcessingBoundaryError,
    MailSourceReference,
    build_draft_operation_key_hash,
    build_message_identity_hash,
    build_required_effect_contract_hash,
    digest_reply_body,
    normalize_rfc_message_id,
    parse_opaque_reference,
    require_draft_effect_transition,
    require_processing_transition,
)


def _identity(**overrides):
    values = {
        "organization_id": uuid.UUID("00000000-0000-0000-0000-000000000001"),
        "workflow_id": uuid.UUID("00000000-0000-0000-0000-000000000002"),
        "source_node_id": "mail-source",
        "credential_id": uuid.UUID("00000000-0000-0000-0000-000000000003"),
        "provider": "gmail",
        "source": MailSourceReference(
            uid_validity=11, uid=22, message_id="<message@example.com>"
        ),
    }
    values.update(overrides)
    return build_message_identity_hash(**values)


def test_message_identity_is_deterministic_and_scoped_to_logical_consumer():
    assert _identity() == _identity()
    assert _identity(workflow_id=uuid.uuid4()) != _identity()
    assert _identity(source_node_id="other-source") != _identity()
    assert _identity(credential_id=uuid.uuid4()) != _identity()


def test_source_reference_requires_stable_uid_identity():
    with pytest.raises(MailProcessingBoundaryError) as exc_info:
        MailSourceReference.from_mapping({"uid": 1})
    assert exc_info.value.reason_code == "mail.message_identity_invalid"


def test_source_reference_accepts_gmail_provider_identity_without_imap_uid():
    source = MailSourceReference.from_mapping(
        {
            "provider_message_id": "18d_example-safe-id",
            "message_id": "<message@example.com>",
            "folder": "INBOX",
        }
    )
    assert source.provider_message_id == "18d_example-safe-id"
    assert source.uid is None


@pytest.mark.parametrize(
    "value",
    [
        {},
        {"provider_message_id": "gmail-id", "uid_validity": 1, "uid": 2},
        {"provider_message_id": "bad/id"},
        {"provider_message_id": "bad id"},
    ],
)
def test_source_reference_rejects_missing_ambiguous_or_unsafe_identity(value):
    with pytest.raises(MailProcessingBoundaryError):
        MailSourceReference.from_mapping(value)


@pytest.mark.parametrize(
    "value",
    [
        "message@example.com",
        "<bad\n@example.com>",
        "<bad query@example.com>",
        '<bad"query@example.com>',
        "",
    ],
)
def test_rfc_message_id_rejects_malformed_values(value):
    if value == "":
        assert normalize_rfc_message_id(value) is None
        return
    with pytest.raises(MailProcessingBoundaryError):
        normalize_rfc_message_id(value)


def test_source_reference_json_never_contains_mail_content():
    source = MailSourceReference(1, 2, "<id@example.com>")
    assert source.to_json() == (
        '{"folder":"INBOX","message_id":"<id@example.com>",'
        '"provider_message_id":null,"uid":2,"uid_validity":1}'
    )
    assert "body" not in source.to_json()


def test_message_identity_is_scoped_to_mailbox_folder():
    assert _identity(source=MailSourceReference(1, 2, folder="INBOX")) != _identity(
        source=MailSourceReference(1, 2, folder="SENT")
    )


def test_message_identity_changes_with_gmail_provider_message_id():
    first = MailSourceReference(provider_message_id="gmail-one")
    second = MailSourceReference(provider_message_id="gmail-two")
    assert _identity(source=first) != _identity(source=second)


def test_gmail_provider_identity_is_stable_across_folder_move():
    inbox = MailSourceReference(provider_message_id="gmail-one", folder="INBOX")
    trash = MailSourceReference(provider_message_id="gmail-one", folder="TRASH")
    assert _identity(source=inbox) == _identity(source=trash)


def test_operation_key_is_stable_but_body_digest_detects_changed_input():
    processing_id = uuid.uuid4()
    assert build_draft_operation_key_hash(
        processing_id=processing_id, node_id="draft"
    ) == build_draft_operation_key_hash(processing_id=processing_id, node_id="draft")
    assert digest_reply_body("first") != digest_reply_body("second")


def test_required_effect_contract_is_stable_and_deployment_scoped():
    deployment_id = uuid.uuid4()
    values = {
        "processing_selector": ["mail", "processing_ref"],
        "effect_selectors": [["draft", "draft_ref"]],
        "deployment_id": deployment_id,
    }
    assert build_required_effect_contract_hash(
        **values
    ) == build_required_effect_contract_hash(**values)
    assert build_required_effect_contract_hash(
        **{**values, "deployment_id": uuid.uuid4()}
    ) != build_required_effect_contract_hash(**values)


def test_opaque_reference_accepts_uuid_only():
    expected = uuid.uuid4()
    assert parse_opaque_reference(str(expected), reason_code="safe.code") == expected
    with pytest.raises(MailProcessingBoundaryError) as exc_info:
        parse_opaque_reference("provider-message-id", reason_code="safe.code")
    assert exc_info.value.reason_code == "safe.code"


def test_processing_terminal_state_cannot_return_to_pending():
    require_processing_transition("pending", "processing")
    require_processing_transition("processing", "ack_pending")
    require_processing_transition("ack_pending", "succeeded")
    with pytest.raises(MailProcessingBoundaryError):
        require_processing_transition("succeeded", "pending")


def test_outcome_unknown_effect_cannot_be_reclaimed():
    require_draft_effect_transition("pending", "claimed")
    require_draft_effect_transition("claimed", "outcome_unknown")
    with pytest.raises(MailProcessingBoundaryError):
        require_draft_effect_transition("outcome_unknown", "claimed")
