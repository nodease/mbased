from __future__ import annotations

import json
import base64
import secrets
import uuid

import pytest
from cryptography.fernet import Fernet

from apps.memory.adapters.security import (
    FernetMemoryContentCipher,
    FernetSecretReplayCipher,
    HmacMemoryRuntimeFingerprinter,
    HmacPublicSecretIssuer,
)


def test_access_and_purge_capabilities_have_separate_hmac_purposes():
    issuer = HmacPublicSecretIssuer(secrets.token_bytes(32))

    access = issuer.issue_access_grant()
    receipt = issuer.issue_purge_receipt()

    assert issuer.access_grant_verifier(access.raw_value) == (
        access.verifier_key_version,
        access.verifier_hash,
    )
    assert issuer.purge_receipt_verifier(receipt.raw_value) == (
        receipt.verifier_key_version,
        receipt.verifier_hash,
    )
    assert issuer.access_grant_verifier(receipt.raw_value) is None
    assert issuer.purge_receipt_verifier(access.raw_value) is None


def test_malformed_public_capability_never_has_a_verifier():
    issuer = HmacPublicSecretIssuer(secrets.token_bytes(32))

    assert issuer.access_grant_verifier("bad") is None
    assert issuer.access_grant_verifier("cag_v1_unsafe value") is None
    assert issuer.purge_receipt_verifier("cpr_v1_short") is None


def test_capability_keyring_issues_with_primary_and_verifies_previous_key():
    previous_key = secrets.token_urlsafe(32)
    primary_key = secrets.token_urlsafe(32)
    previous_issuer = HmacPublicSecretIssuer(
        previous_key.encode("utf-8"), key_version="cap-v1"
    )
    previous = previous_issuer.issue_access_grant()
    previous_receipt = previous_issuer.issue_purge_receipt()
    issuer = HmacPublicSecretIssuer.from_environment(
        {
            "MEMORY_PUBLIC_CAPABILITY_HMAC_KEYS": json.dumps(
                {"cap-v2": primary_key, "cap-v1": previous_key}
            ),
            "MEMORY_PUBLIC_CAPABILITY_HMAC_PRIMARY_VERSION": "cap-v2",
        }
    )

    issued = issuer.issue_access_grant()
    candidates = issuer.access_grant_verifiers(previous.raw_value)
    receipt_candidates = issuer.purge_receipt_verifiers(previous_receipt.raw_value)

    assert issued.verifier_key_version == "cap-v2"
    assert candidates[0][0] == "cap-v2"
    assert ("cap-v1", previous.verifier_hash) in candidates
    assert ("cap-v1", previous_receipt.verifier_hash) in receipt_candidates


def test_capability_keyring_rejects_more_than_one_previous_key():
    with pytest.raises(RuntimeError, match="invalid public capability keyring"):
        HmacPublicSecretIssuer.from_environment(
            {
                "MEMORY_PUBLIC_CAPABILITY_HMAC_KEYS": json.dumps(
                    {
                        "cap-v3": secrets.token_urlsafe(32),
                        "cap-v2": secrets.token_urlsafe(32),
                        "cap-v1": secrets.token_urlsafe(32),
                    }
                ),
                "MEMORY_PUBLIC_CAPABILITY_HMAC_PRIMARY_VERSION": "cap-v3",
            }
        )


def test_replay_ciphertext_requires_matching_idempotency_associated_data():
    cipher = FernetSecretReplayCipher(Fernet.generate_key())
    issuer = HmacPublicSecretIssuer(secrets.token_bytes(32))
    issued = issuer.issue_access_grant()
    digest = "a" * 64

    envelope = cipher.encrypt(issued.raw_value, associated_data_digest=digest)

    assert (
        cipher.decrypt(
            envelope.ciphertext,
            key_version=envelope.key_version,
            associated_data_digest=digest,
        )
        == issued.raw_value
    )
    assert (
        cipher.decrypt(
            envelope.ciphertext,
            key_version=envelope.key_version,
            associated_data_digest="b" * 64,
        )
        is None
    )
    assert (
        cipher.decrypt(
            b"invalid-ciphertext",
            key_version=envelope.key_version,
            associated_data_digest=digest,
        )
        is None
    )


def test_replay_keyring_encrypts_with_primary_and_decrypts_previous_ciphertext():
    previous_key = Fernet.generate_key()
    primary_key = Fernet.generate_key()
    previous_cipher = FernetSecretReplayCipher(previous_key, key_version="replay-v1")
    previous_envelope = previous_cipher.encrypt(
        "bounded-secret",
        associated_data_digest="a" * 64,
    )
    rotated = FernetSecretReplayCipher.from_environment(
        {
            "MEMORY_PUBLIC_REPLAY_ENCRYPTION_KEYS": json.dumps(
                {
                    "replay-v2": primary_key.decode("ascii"),
                    "replay-v1": previous_key.decode("ascii"),
                }
            ),
            "MEMORY_PUBLIC_REPLAY_ENCRYPTION_PRIMARY_VERSION": "replay-v2",
        }
    )

    new_envelope = rotated.encrypt(
        "new-bounded-secret",
        associated_data_digest="b" * 64,
    )

    assert new_envelope.key_version == "replay-v2"
    assert (
        rotated.decrypt(
            previous_envelope.ciphertext,
            key_version=previous_envelope.key_version,
            associated_data_digest="a" * 64,
        )
        == "bounded-secret"
    )


def test_memory_content_cipher_binds_projection_to_associated_data() -> None:
    cipher = FernetMemoryContentCipher(
        {"content-v1": Fernet.generate_key()},
        primary_key_version="content-v1",
        digest_hmac_key=secrets.token_bytes(32),
    )

    protected = cipher.protect("private message", associated_data="entry-1:model")

    assert b"private message" not in protected.ciphertext
    assert protected.plaintext_byte_length == len("private message".encode("utf-8"))
    assert (
        cipher.reveal(protected, associated_data="entry-1:model") == "private message"
    )
    assert cipher.reveal(protected, associated_data="entry-2:model") is None


def test_memory_content_cipher_rejects_empty_and_oversized_utf8_payloads() -> None:
    cipher = FernetMemoryContentCipher(
        Fernet.generate_key(),
        digest_hmac_key=secrets.token_bytes(32),
    )

    with pytest.raises(ValueError, match="content is invalid"):
        cipher.protect("", associated_data="entry:model")
    with pytest.raises(ValueError, match="content is invalid"):
        cipher.protect("가" * 6_000, associated_data="entry:model")


def test_memory_content_cipher_fails_closed_for_tampering_or_missing_key_version() -> (
    None
):
    cipher = FernetMemoryContentCipher(
        Fernet.generate_key(),
        digest_hmac_key=secrets.token_bytes(32),
    )
    protected = cipher.protect("answer", associated_data="entry:display")

    tampered = type(protected)(
        ciphertext=protected.ciphertext,
        key_version=protected.key_version,
        format_version=protected.format_version,
        content_digest="0" * 64,
        plaintext_byte_length=protected.plaintext_byte_length,
    )
    missing_key = type(protected)(
        ciphertext=protected.ciphertext,
        key_version="content-missing",
        format_version=protected.format_version,
        content_digest=protected.content_digest,
        plaintext_byte_length=protected.plaintext_byte_length,
    )

    assert cipher.reveal(tampered, associated_data="entry:display") is None
    assert cipher.reveal(missing_key, associated_data="entry:display") is None


def test_runtime_fingerprint_is_keyed_versioned_and_scope_bound() -> None:
    key = secrets.token_bytes(32)
    fingerprinter = HmacMemoryRuntimeFingerprinter(
        {"admission-v1": key},
        primary_key_version="admission-v1",
    )
    values = {
        "organization_id": "org",
        "deployment_id": "deployment",
        "deployment_version": 1,
        "grant_id": "grant",
        "session_id": "session",
        "expected_lifecycle_revision": 1,
        "mapping_version": "conversation-mapping-v1",
        "memory_policy_version": "memory-policy-v1",
        "memory_contract_version": "conversation-memory-v1",
        "storage_generation": 1,
        "input_variable": "question",
        "input_text": "private input",
    }

    version, first = fingerprinter.fingerprint(**values)
    _version, second = fingerprinter.fingerprint(
        **{**values, "grant_id": "another-grant"}
    )

    assert version == "admission-v1"
    assert first != second
    assert "private input" not in first


def test_runtime_fingerprint_can_replay_with_a_retained_non_primary_key() -> None:
    old_key = secrets.token_bytes(32)
    values = {
        "organization_id": "org",
        "deployment_id": "deployment",
        "deployment_version": 1,
        "grant_id": "grant",
        "session_id": "session",
        "expected_lifecycle_revision": 1,
        "mapping_version": "conversation-mapping-v1",
        "memory_policy_version": "memory-policy-v1",
        "memory_contract_version": "conversation-memory-v1",
        "storage_generation": 1,
        "input_variable": "question",
        "input_text": "private input",
    }
    original = HmacMemoryRuntimeFingerprinter(
        {"admission-v1": old_key},
        primary_key_version="admission-v1",
    )
    rotated = HmacMemoryRuntimeFingerprinter(
        {"admission-v1": old_key, "admission-v2": secrets.token_bytes(32)},
        primary_key_version="admission-v2",
    )

    old_version, old_digest = original.fingerprint(**values)
    replay_version, replay_digest = rotated.fingerprint(
        key_version=old_version,
        **values,
    )
    new_version, new_digest = rotated.fingerprint(**values)

    assert (replay_version, replay_digest) == (old_version, old_digest)
    assert new_version == "admission-v2"
    assert new_digest != old_digest
    with pytest.raises(ValueError, match="fingerprint key"):
        rotated.fingerprint(key_version="admission-retired", **values)


def test_transcript_cursor_is_authenticated_and_bound_to_session_scope() -> None:
    issuer = HmacPublicSecretIssuer(secrets.token_bytes(32))
    organization_id = uuid.uuid4()
    session_id = uuid.uuid4()
    cursor = issuer.encode_transcript_cursor(
        organization_id=organization_id,
        session_id=session_id,
        after_sequence=50,
    )

    assert (
        issuer.decode_transcript_cursor(
            cursor,
            organization_id=organization_id,
            session_id=session_id,
        )
        == 50
    )

    payload_segment, signature_segment = cursor.split(".", 1)
    padded = payload_segment + ("=" * (-len(payload_segment) % 4))
    payload = json.loads(base64.urlsafe_b64decode(padded).decode("ascii"))
    payload["a"] = 1
    tampered_payload = (
        base64.urlsafe_b64encode(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii")
        )
        .decode("ascii")
        .rstrip("=")
    )

    with pytest.raises(ValueError, match="cursor"):
        issuer.decode_transcript_cursor(
            f"{tampered_payload}.{signature_segment}",
            organization_id=organization_id,
            session_id=session_id,
        )
    with pytest.raises(ValueError, match="cursor"):
        issuer.decode_transcript_cursor(
            cursor,
            organization_id=organization_id,
            session_id=uuid.uuid4(),
        )
    with pytest.raises(ValueError, match="cursor"):
        issuer.decode_transcript_cursor(
            f"{payload_segment}=.{signature_segment}",
            organization_id=organization_id,
            session_id=session_id,
        )
    with pytest.raises(ValueError, match="cursor"):
        issuer.decode_transcript_cursor(
            f"{payload_segment}.{signature_segment}=",
            organization_id=organization_id,
            session_id=session_id,
        )


def test_transcript_cursor_accepts_a_retained_previous_capability_key() -> None:
    previous_key = secrets.token_bytes(32)
    organization_id = uuid.uuid4()
    session_id = uuid.uuid4()
    original = HmacPublicSecretIssuer(
        {"capability-v1": previous_key},
        primary_key_version="capability-v1",
    )
    rotated = HmacPublicSecretIssuer(
        {
            "capability-v2": secrets.token_bytes(32),
            "capability-v1": previous_key,
        },
        primary_key_version="capability-v2",
    )
    cursor = original.encode_transcript_cursor(
        organization_id=organization_id,
        session_id=session_id,
        after_sequence=25,
    )

    assert (
        rotated.decode_transcript_cursor(
            cursor,
            organization_id=organization_id,
            session_id=session_id,
        )
        == 25
    )
