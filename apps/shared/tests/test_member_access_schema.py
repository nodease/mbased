import uuid

import pytest
from apps.shared.schemas.member_access import (
    MemberAccessActionRequest,
    normalize_management_reason,
)
from pydantic import BaseModel, ValidationError


class _ActionEnvelope(BaseModel):
    action_request: MemberAccessActionRequest


def _common(action: str) -> dict:
    return {
        "action": action,
        "expected_membership_id": str(uuid.uuid4()),
        "expected_user_active": True,
        "expected_membership_state": "active",
        "expected_organization_auth_state": "member",
    }


def _parse(payload: dict):
    return _ActionEnvelope(action_request=payload).action_request


def test_reason_normalizes_newlines_whitespace_and_blank():
    assert normalize_management_reason("  검토\r\n완료\r  ") == "검토\n완료"
    assert normalize_management_reason(" \t\r\n ") is None


@pytest.mark.parametrize(
    "reason",
    [
        "a" * 501,
        "blocked\x00value",
        "blocked\x7fvalue",
        "blocked\x85value",
        "blocked\u202evalue",
        "blocked\u2066value",
    ],
)
def test_reason_rejects_length_control_and_bidi_boundaries(reason):
    with pytest.raises(ValueError):
        normalize_management_reason(reason)


def test_reason_counts_unicode_code_points_not_utf16_units():
    assert normalize_management_reason("😀" * 500) == "😀" * 500
    with pytest.raises(ValueError):
        normalize_management_reason("😀" * 501)


def test_direct_grant_requires_exactly_one_precondition_shape():
    payload = {
        **_common("direct_permission.grant"),
        "resource_type": "knowledge_base",
        "resource_id": str(uuid.uuid4()),
        "auth_state": "viewer",
    }

    with pytest.raises(ValidationError):
        _parse(payload)
    with pytest.raises(ValidationError):
        _parse(
            {
                **payload,
                "expected_absent": True,
                "expected_permission_id": str(uuid.uuid4()),
                "expected_auth_state": "operator",
            }
        )
    with pytest.raises(ValidationError):
        _parse({**payload, "expected_permission_id": str(uuid.uuid4())})

    assert _parse({**payload, "expected_absent": True}).expected_absent is True
    assert (
        _parse(
            {
                **payload,
                "expected_permission_id": str(uuid.uuid4()),
                "expected_auth_state": "none",
            }
        ).expected_auth_state
        == "none"
    )


def test_action_union_rejects_none_grant_and_variant_field_mixing():
    direct = {
        **_common("direct_permission.grant"),
        "resource_type": "workflow",
        "resource_id": str(uuid.uuid4()),
        "auth_state": "none",
        "expected_absent": True,
    }
    suspend = {
        **_common("membership.suspend"),
        "team_id": str(uuid.uuid4()),
    }

    with pytest.raises(ValidationError):
        _parse(direct)
    with pytest.raises(ValidationError):
        _parse(suspend)


def test_membership_action_requires_directional_expected_state():
    with pytest.raises(ValidationError):
        _parse(
            {
                **_common("membership.reactivate"),
                "expected_membership_state": "active",
            }
        )


def test_expected_absent_must_be_literal_true():
    payload = {
        **_common("team_membership.add"),
        "team_id": str(uuid.uuid4()),
        "expected_absent": False,
    }
    with pytest.raises(ValidationError):
        _parse(payload)
