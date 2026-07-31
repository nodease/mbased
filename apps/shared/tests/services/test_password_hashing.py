from apps.shared.services.password_hashing import hash_password, verify_password


def test_hash_password_verifies_original_password():
    hashed = hash_password("demo-password")

    assert "$" in hashed
    assert verify_password("demo-password", hashed) is True
    assert verify_password("wrong-password", hashed) is False


def test_hash_password_uses_unique_salt():
    first = hash_password("same-password")
    second = hash_password("same-password")

    assert first != second
    assert verify_password("same-password", first) is True
    assert verify_password("same-password", second) is True


def test_verify_password_rejects_malformed_hash():
    assert verify_password("demo-password", "not-a-valid-hash") is False
