from __future__ import annotations

import json
import secrets

import pytest
from cryptography.fernet import Fernet

from apps.memory.adapters.security import (
    FernetSecretReplayCipher,
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
