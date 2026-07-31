import json
import secrets

from apps.shared.db.models.knowledge import Document
from apps.shared.utils.encryption import EncryptionManager
from cryptography.fernet import Fernet


def test_encryption_flow(monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())
    security_service = EncryptionManager()
    sensitive_value = f"Bearer {secrets.token_urlsafe(16)}"
    original_headers_str = json.dumps({"Authorization": sensitive_value})

    encrypted_headers = security_service.encrypt(original_headers_str)

    assert encrypted_headers != original_headers_str
    assert sensitive_value not in encrypted_headers

    doc = Document(meta_info={"api_config": {"headers": encrypted_headers}})
    stored_headers = doc.meta_info["api_config"]["headers"]

    assert security_service.decrypt(stored_headers) == original_headers_str
