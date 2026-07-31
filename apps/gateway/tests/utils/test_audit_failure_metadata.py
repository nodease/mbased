from fastapi import HTTPException

from apps.gateway.utils import audit as audit_module
from apps.gateway.utils.audit import _safe_failure_metadata


class _CodedError(Exception):
    code = "mail.credential_persistence_failed"


def test_audit_failure_metadata_uses_safe_domain_code():
    assert _safe_failure_metadata(_CodedError("sensitive detail")) == {
        "error_code": "mail.credential_persistence_failed"
    }


def test_audit_failure_metadata_does_not_serialize_exception_detail():
    metadata = _safe_failure_metadata(
        RuntimeError("mailbox@example.test synthetic-ciphertext")
    )

    assert metadata == {"error_code": "internal.request_failed"}


def test_audit_failure_metadata_keeps_only_http_status():
    metadata = _safe_failure_metadata(
        HTTPException(status_code=409, detail="sensitive detail")
    )

    assert metadata == {
        "error_code": "http.request_failed",
        "status_code": 409,
    }


def test_audit_decorator_merges_only_the_domain_safe_metadata(monkeypatch):
    events: list[dict] = []
    monkeypatch.setattr(
        audit_module,
        "record_audit",
        lambda **event: events.append(event),
    )

    @audit_module.audit(
        "deployment.llm_credential_policy.upsert",
        metadata_factory=lambda kwargs: {
            "node_location_ref": kwargs["safe_reference"]
        },
    )
    def handler(*, safe_reference: str) -> str:
        return "ok"

    assert handler(safe_reference="workflow-node-location:v1:opaque") == "ok"
    assert events[0]["metadata"] == {
        "node_location_ref": "workflow-node-location:v1:opaque"
    }
