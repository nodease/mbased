"""권한 신청(ADR-0016) 모델/스키마/audit action 계약 테스트.

TDD red phase: 구현 전이므로 전부 실패해야 한다.
import를 각 테스트 안에서 수행해 케이스별로 실패 이유가 드러나게 한다.
"""

import pytest
from pydantic import ValidationError


class ForgivingFakeQuery:
    def join(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        return None

    def all(self):
        return []


class ForgivingFakeDb:
    """어떤 조회든 빈 결과를 돌려주는 DB. fail-closed 검증용."""

    def query(self, *args, **kwargs):
        return ForgivingFakeQuery()


def test_audit_action_defines_permission_request_lifecycle_strings():
    from apps.shared.audit.actions import AuditAction

    assert AuditAction.PERMISSION_REQUEST_CREATED == "permission_request.created"
    assert AuditAction.PERMISSION_REQUEST_APPROVED == "permission_request.approved"
    assert AuditAction.PERMISSION_REQUEST_REJECTED == "permission_request.rejected"


def test_audit_action_defines_user_app_creation_permission_data_change_strings():
    from apps.shared.audit.actions import AuditAction

    assert (
        AuditAction.USER_APP_CREATION_PERMISSION_CREATED
        == "user_app_creation_permission.created"
    )
    assert (
        AuditAction.USER_APP_CREATION_PERMISSION_DELETED
        == "user_app_creation_permission.deleted"
    )


def test_permission_request_model_contract():
    from apps.shared.db.models.permission_request import (
        PERMISSION_REQUEST_APPROVED,
        PERMISSION_REQUEST_PENDING,
        PERMISSION_REQUEST_REJECTED,
        REQUESTED_PERMISSION_APP_CREATE,
        PermissionRequest,
    )

    assert PermissionRequest.__tablename__ == "permission_requests"
    assert PERMISSION_REQUEST_PENDING == "pending"
    assert PERMISSION_REQUEST_APPROVED == "approved"
    assert PERMISSION_REQUEST_REJECTED == "rejected"
    assert REQUESTED_PERMISSION_APP_CREATE == "app.create"

    columns = PermissionRequest.__table__.columns
    for required in (
        "organization_id",
        "user_id",
        "requested_permission",
        "reason",
        "status",
        "decided_by",
        "decided_at",
    ):
        assert required in columns, f"permission_requests.{required} column 누락"
    assert columns["reason"].nullable is False
    assert columns["decided_by"].nullable is True


def test_user_app_creation_permission_model_contract():
    from apps.shared.db.models.user_app_creation_permission import (
        UserAppCreationPermission,
    )

    assert (
        UserAppCreationPermission.__tablename__ == "user_app_creation_permissions"
    )

    columns = UserAppCreationPermission.__table__.columns
    for required in ("grantee_organization_id", "user_id", "assigned_by"):
        assert required in columns, (
            f"user_app_creation_permissions.{required} column 누락"
        )
    # 단일 능력 테이블이므로 auth_state를 두지 않는다 (ADR-0016).
    assert "auth_state" not in columns


def test_permission_request_status_set_matches_db_contract():
    from apps.shared.schemas.permission_request import PERMISSION_REQUEST_STATUSES

    assert PERMISSION_REQUEST_STATUSES == {"pending", "approved", "rejected"}


def test_permission_request_create_schema_requires_reason():
    from apps.shared.schemas.permission_request import PermissionRequestCreateRequest

    with pytest.raises(ValidationError):
        PermissionRequestCreateRequest()

    with pytest.raises(ValidationError):
        PermissionRequestCreateRequest(reason="   ")


def test_permission_request_create_schema_only_allows_app_create():
    from apps.shared.schemas.permission_request import PermissionRequestCreateRequest

    request = PermissionRequestCreateRequest(reason="workflow를 만들고 싶습니다")
    assert request.requested_permission == "app.create"

    with pytest.raises(ValidationError):
        PermissionRequestCreateRequest(
            reason="사유", requested_permission="workflow.deploy"
        )


def test_has_app_creation_permission_fails_closed():
    from apps.shared.services.permissions import has_app_creation_permission

    # membership도 permission row도 없는 사용자는 거부된다 (fail-closed).
    assert (
        has_app_creation_permission(
            ForgivingFakeDb(),
            user_id="0d3f5a52-0000-0000-0000-000000000001",
            organization_id="0d3f5a52-0000-0000-0000-000000000002",
        )
        is False
    )
