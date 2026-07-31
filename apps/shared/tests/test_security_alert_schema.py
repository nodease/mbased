from datetime import datetime, timezone
from uuid import uuid4

import pytest
from apps.shared.schemas.security_alert import (
    SecurityAlertAcknowledgeRequest,
    SecurityAlertDetail,
    SecurityAlertResolveRequest,
    SecurityAlertSummaryResponse,
)
from pydantic import ValidationError


def _detail_payload() -> dict:
    now = datetime.now(timezone.utc)
    actor = {"id": uuid4(), "display_name": "관리자", "state": "active"}
    return {
        "id": uuid4(),
        "organization_id": uuid4(),
        "rule_id": "repeated_permission_denied",
        "rule_version": "v1",
        "severity": "medium",
        "status": "open",
        "policy_reason": None,
        "actor": actor,
        "occurrence_count": 5,
        "evidence_count": 5,
        "first_detected_at": now,
        "last_detected_at": now,
        "version": 1,
        "acknowledged": None,
        "resolution": None,
        "created_at": now,
        "updated_at": now,
    }


def test_security_alert_detail_exposes_only_safe_contract_fields():
    detail = SecurityAlertDetail.model_validate(_detail_payload())
    serialized = detail.model_dump(mode="json")

    assert "detection_key" not in serialized
    assert "audit_metadata" not in serialized
    assert "email" not in serialized["actor"]
    assert "raw_targets" not in serialized


@pytest.mark.parametrize(
    "request_type",
    [SecurityAlertAcknowledgeRequest, SecurityAlertResolveRequest],
)
def test_security_alert_mutation_requests_reject_unknown_fields(request_type):
    payload = {"expected_version": 1, "unknown": "must fail"}
    if request_type is SecurityAlertResolveRequest:
        payload.update(
            resolution_type="mitigated",
            reason="필요한 대응을 완료했습니다.",
        )

    with pytest.raises(ValidationError):
        request_type.model_validate(payload)


def test_security_alert_resolve_request_normalizes_reason():
    request = SecurityAlertResolveRequest(
        expected_version=2,
        resolution_type="false_positive",
        reason="  검토\r\n완료\r  ",
    )

    assert request.reason == "검토\n완료"


@pytest.mark.parametrize("reason", [" \t\r\n ", "x" * 501, "unsafe\x00reason"])
def test_security_alert_resolve_request_rejects_invalid_reason(reason):
    with pytest.raises(ValidationError):
        SecurityAlertResolveRequest(
            expected_version=1,
            resolution_type="accepted_risk",
            reason=reason,
        )


def test_security_alert_summary_rejects_more_than_five_recent_items():
    now = datetime.now(timezone.utc)
    item = {
        "id": uuid4(),
        "rule_id": "repeated_permission_denied",
        "severity": "medium",
        "actor": {"id": uuid4(), "display_name": None, "state": "deleted"},
        "occurrence_count": 5,
        "last_detected_at": now,
    }

    with pytest.raises(ValidationError):
        SecurityAlertSummaryResponse(
            open_count=6,
            high_open_count=0,
            recent_items=[{**item, "id": uuid4()} for _ in range(6)],
        )
