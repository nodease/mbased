import hashlib
import secrets


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    hashed = hashlib.sha256((password + salt).encode()).hexdigest()
    return f"{salt}${hashed}"


def verify_password(password: str, hashed_password: str) -> bool:
    try:
        salt, stored_hash = hashed_password.split("$")
    except ValueError:
        return False
    new_hash = hashlib.sha256((password + salt).encode()).hexdigest()
    return new_hash == stored_hash
