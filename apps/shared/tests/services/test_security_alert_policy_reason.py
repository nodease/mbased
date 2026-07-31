import importlib

import pytest


def _normalizer():
    module = importlib.import_module(
        "apps.shared.services.security_alert_policy_reason"
    )
    return module.normalize_security_alert_policy_reason


def test_legacy_pii_policy_reason_maps_without_mutating_metadata():
    metadata = {
        "policy_result": {
            "result": "block",
            "reason_code": "pii_policy_blocked",
        }
    }
    original = {
        "policy_result": {
            "result": "block",
            "reason_code": "pii_policy_blocked",
        }
    }

    normalized = _normalizer()(metadata)

    assert normalized == "rag.pii_evidence_detected"
    assert metadata == original


def test_canonical_policy_reason_is_returned_without_mutating_metadata():
    metadata = {"policy_reason": "rag.pii_evidence_detected"}

    normalized = _normalizer()(metadata)

    assert normalized == "rag.pii_evidence_detected"
    assert metadata == {"policy_reason": "rag.pii_evidence_detected"}


@pytest.mark.parametrize(
    "policy_reason",
    [
        "access_management.self_control_forbidden",
        "access_management.last_active_manager",
        "access_management.manager_override_active",
        "access_management.member_state_not_manageable",
        "access_management.target_user_inactive",
        "access_management.stale_state",
    ],
)
def test_canonical_access_management_policy_reason_is_returned(policy_reason):
    metadata = {"policy_reason": policy_reason}

    normalized = _normalizer()(metadata)

    assert normalized == policy_reason
    assert metadata == {"policy_reason": policy_reason}


def test_unknown_legacy_policy_reason_is_not_normalized():
    metadata = {
        "policy_result": {
            "result": "block",
            "reason_code": "unknown_policy_blocked",
        }
    }

    normalized = _normalizer()(metadata)

    assert normalized is None
