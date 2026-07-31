"""PermissionRequestService(FR-014, ADR-0016) 계약 테스트.

TDD red phase: 서비스와 모델이 아직 없으므로 전부 실패해야 한다.
test_cases.md의 PermissionRequestService 단위 계약을 검증한다.
class/method 이름은 test_cases.md의 권장 이름을 따른다.

신청 상태 전이, 권한 row 생성, 같은 트랜잭션에 남기는 organization-scoped audit
metadata를 검증한다.
"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.sql.operators import eq

from apps.shared.audit.actions import AuditAction
from apps.shared.db.models.audit_log import AuditLog
from apps.shared.db.models.organization_membership import (
    ORGANIZATION_MEMBERSHIP_ACTIVE,
    ORGANIZATION_MEMBERSHIP_INVITED,
    ORGANIZATION_MEMBERSHIP_REMOVED,
    ORGANIZATION_MEMBERSHIP_SUSPENDED,
    OrganizationMembership,
)
from apps.shared.db.models.user import User


def _service():
    from apps.gateway.services.permission_request_service import (
        PermissionRequestService,
    )

    return PermissionRequestService


def _request_model():
    from apps.shared.db.models.permission_request import PermissionRequest

    return PermissionRequest


def _permission_model():
    from apps.shared.db.models.user_app_creation_permission import (
        UserAppCreationPermission,
    )

    return UserAppCreationPermission


def _pending_request(organization_id, user_id, **overrides):
    PermissionRequest = _request_model()
    values = {
        "id": uuid4(),
        "organization_id": organization_id,
        "user_id": user_id,
        "requested_permission": "app.create",
        "reason": "workflow를 만들고 싶습니다",
        "status": "pending",
        "created_at": datetime.now(timezone.utc),
    }
    values.update(overrides)
    return PermissionRequest(**values)


def _user(user_id=None, deactivated_at=None):
    return User(
        id=user_id or uuid4(),
        email=f"{uuid4()}@example.com",
        name="requester",
        deactivated_at=deactivated_at,
    )


def _membership(user_id, organization_id, state=ORGANIZATION_MEMBERSHIP_ACTIVE):
    return OrganizationMembership(
        id=uuid4(),
        organization_id=organization_id,
        user_id=user_id,
        membership_state=state,
        organization_auth_state="member",
    )


class _Query:
    def __init__(self, items):
        self.items = list(items)
        self.filters = []

    def join(self, *args, **kwargs):
        return self

    def options(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def with_for_update(self, **kwargs):
        return self

    def filter(self, *expressions):
        self.filters.extend(expressions)
        return self

    def all(self):
        return [item for item in self.items if self._matches(item)]

    def first(self):
        return next(iter(self.all()), None)

    def count(self):
        return len(self.all())

    def _matches(self, item):
        return all(
            _matches_expression(item, expression) for expression in self.filters
        )


def _matches_expression(item, expression):
    if not hasattr(expression, "left"):
        return True
    column = str(expression.left).split(".")[-1]
    if not hasattr(item, column):
        return True
    if expression.operator is not eq:
        return True
    right = expression.right
    right_value = right.value if hasattr(right, "value") else right
    return getattr(item, column) == right_value


class _Db:
    """모델 단위 dispatch + eq filter 평가만 지원하는 최소 세션 fake."""

    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.added = []
        self.commits = 0
        self.rollbacks = 0
        self.audit_add_error = None

    def query(self, model, *rest):
        return _Query([row for row in self.rows if isinstance(row, model)])

    def add(self, obj):
        if isinstance(obj, AuditLog) and self.audit_add_error is not None:
            raise self.audit_add_error
        self.added.append(obj)
        self.rows.append(obj)

    def flush(self):
        pass

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def refresh(self, obj):
        pass

    def added_of(self, model):
        return [obj for obj in self.added if isinstance(obj, model)]


# --- submit_request ---------------------------------------------------------


def test_submit_request_creates_pending_request_and_records_org_scoped_audit():
    organization_id = uuid4()
    requester = _user()
    db = _Db(rows=[requester])

    request = _service().submit_request(
        db,
        user=requester,
        organization_id=organization_id,
        requested_permission="app.create",
        reason="workflow를 만들고 싶습니다",
    )

    assert request.organization_id == organization_id
    assert request.user_id == requester.id
    assert request.status == "pending"
    audits = db.added_of(AuditLog)
    assert [audit.action for audit in audits] == [
        AuditAction.PERMISSION_REQUEST_CREATED
    ]
    assert audits[0].target_type == "permission_request"
    assert audits[0].target_id == str(request.id)
    assert audits[0].audit_metadata == {"organization_id": str(organization_id)}
    assert db.commits >= 1


# --- ensure_request_processable -------------------------------------------


def test_ensure_request_processable_passes_pending_same_org():
    organization_id = uuid4()
    request = _pending_request(organization_id, uuid4())

    _service().ensure_request_processable(request, organization_id)


@pytest.mark.parametrize("status", ["approved", "rejected"])
def test_ensure_request_processable_conflicts_on_processed_request(status):
    organization_id = uuid4()
    request = _pending_request(organization_id, uuid4(), status=status)

    with pytest.raises(HTTPException) as exc_info:
        _service().ensure_request_processable(request, organization_id)

    assert exc_info.value.status_code == 409


def test_ensure_request_processable_hides_cross_org_request_as_404():
    request = _pending_request(uuid4(), uuid4())

    with pytest.raises(HTTPException) as exc_info:
        _service().ensure_request_processable(request, uuid4())

    assert exc_info.value.status_code == 404


# --- ensure_requester_is_active_member -------------------------------------


def test_ensure_requester_is_active_member_passes_active_member():
    organization_id = uuid4()
    requester = _user()
    request = _pending_request(organization_id, requester.id)
    db = _Db(rows=[requester, _membership(requester.id, organization_id)])

    _service().ensure_requester_is_active_member(db, request)


@pytest.mark.parametrize(
    "membership_state",
    [
        ORGANIZATION_MEMBERSHIP_INVITED,
        ORGANIZATION_MEMBERSHIP_SUSPENDED,
        ORGANIZATION_MEMBERSHIP_REMOVED,
    ],
)
def test_ensure_requester_is_active_member_rejects_non_active_membership(
    membership_state,
):
    organization_id = uuid4()
    requester = _user()
    request = _pending_request(organization_id, requester.id)
    db = _Db(
        rows=[
            requester,
            _membership(requester.id, organization_id, state=membership_state),
        ]
    )

    with pytest.raises(HTTPException) as exc_info:
        _service().ensure_requester_is_active_member(db, request)

    assert exc_info.value.status_code == 409


def test_ensure_requester_is_active_member_rejects_deactivated_user():
    organization_id = uuid4()
    requester = _user(deactivated_at=datetime.now(timezone.utc))
    request = _pending_request(organization_id, requester.id)
    db = _Db(rows=[requester, _membership(requester.id, organization_id)])

    with pytest.raises(HTTPException) as exc_info:
        _service().ensure_requester_is_active_member(db, request)

    assert exc_info.value.status_code == 409


# --- approve_request --------------------------------------------------------


def test_approve_request_marks_request_and_grants_permission():
    organization_id = uuid4()
    decided_by = uuid4()
    requester = _user()
    request = _pending_request(organization_id, requester.id)
    db = _Db(rows=[requester, _membership(requester.id, organization_id), request])

    _service().approve_request(
        db,
        request_id=request.id,
        organization_id=organization_id,
        decided_by=decided_by,
    )

    assert request.status == "approved"
    assert request.decided_by == decided_by
    assert request.decided_at is not None

    granted = db.added_of(_permission_model())
    assert len(granted) == 1
    assert granted[0].grantee_organization_id == organization_id
    assert granted[0].user_id == requester.id
    assert granted[0].assigned_by == decided_by
    audits = db.added_of(AuditLog)
    assert [audit.action for audit in audits] == [
        AuditAction.PERMISSION_REQUEST_APPROVED,
        AuditAction.USER_APP_CREATION_PERMISSION_CREATED,
    ]
    assert [audit.audit_metadata for audit in audits] == [
        {"organization_id": str(organization_id)},
        {"organization_id": str(organization_id)},
    ]
    assert audits[1].before is None
    assert audits[1].after == {
        "grantee_organization_id": str(organization_id),
        "user_id": str(requester.id),
    }
    assert db.commits >= 1


def test_approve_request_rolls_back_permission_when_audit_add_fails():
    organization_id = uuid4()
    requester = _user()
    pending = _pending_request(organization_id, requester.id)
    db = _Db(
        rows=[
            pending,
            requester,
            _membership(requester.id, organization_id),
        ]
    )
    db.audit_add_error = RuntimeError("audit unavailable")

    with pytest.raises(RuntimeError, match="audit unavailable"):
        _service().approve_request(
            db,
            request_id=pending.id,
            organization_id=organization_id,
            decided_by=uuid4(),
        )

    assert db.commits == 0
    assert db.rollbacks == 1


def test_approve_request_conflicts_when_permission_row_already_exists():
    organization_id = uuid4()
    requester = _user()
    request = _pending_request(organization_id, requester.id)
    UserAppCreationPermission = _permission_model()
    existing_row = UserAppCreationPermission(
        id=uuid4(),
        grantee_organization_id=organization_id,
        user_id=requester.id,
        assigned_by=uuid4(),
        assigned_at=datetime.now(timezone.utc),
    )
    db = _Db(
        rows=[
            requester,
            _membership(requester.id, organization_id),
            request,
            existing_row,
        ]
    )

    with pytest.raises(HTTPException) as exc_info:
        _service().approve_request(
            db,
            request_id=request.id,
            organization_id=organization_id,
            decided_by=uuid4(),
        )

    assert exc_info.value.status_code == 409
    assert request.status == "pending"
    assert db.added_of(UserAppCreationPermission) == []


def test_approve_request_hides_cross_org_request_as_404():
    organization_id = uuid4()
    requester = _user()
    request = _pending_request(organization_id, requester.id)
    db = _Db(rows=[requester, _membership(requester.id, organization_id), request])

    with pytest.raises(HTTPException) as exc_info:
        _service().approve_request(
            db,
            request_id=request.id,
            organization_id=uuid4(),
            decided_by=uuid4(),
        )

    assert exc_info.value.status_code == 404
    assert request.status == "pending"


# --- reject_request ---------------------------------------------------------


def test_reject_request_marks_rejected_without_permission_row():
    organization_id = uuid4()
    decided_by = uuid4()
    requester = _user()
    request = _pending_request(organization_id, requester.id)
    db = _Db(rows=[requester, _membership(requester.id, organization_id), request])

    _service().reject_request(
        db,
        request_id=request.id,
        organization_id=organization_id,
        decided_by=decided_by,
    )

    assert request.status == "rejected"
    assert request.decided_by == decided_by
    assert request.decided_at is not None
    assert db.added_of(_permission_model()) == []
    audits = db.added_of(AuditLog)
    assert [audit.action for audit in audits] == [
        AuditAction.PERMISSION_REQUEST_REJECTED
    ]
    assert audits[0].audit_metadata == {"organization_id": str(organization_id)}
    assert db.commits >= 1


def test_reject_request_conflicts_on_processed_request():
    organization_id = uuid4()
    requester = _user()
    request = _pending_request(organization_id, requester.id, status="approved")
    db = _Db(rows=[requester, _membership(requester.id, organization_id), request])

    with pytest.raises(HTTPException) as exc_info:
        _service().reject_request(
            db,
            request_id=request.id,
            organization_id=organization_id,
            decided_by=uuid4(),
        )

    assert exc_info.value.status_code == 409
    assert request.status == "approved"


# --- grant_app_creation_permission ------------------------------------------


def test_grant_app_creation_permission_rejects_unsupported_permission():
    organization_id = uuid4()
    requester = _user()
    request = _pending_request(
        organization_id, requester.id, requested_permission="workflow.deploy"
    )
    db = _Db(rows=[requester, _membership(requester.id, organization_id)])

    with pytest.raises((HTTPException, ValueError)):
        _service().grant_app_creation_permission(db, request, decided_by=uuid4())

    assert db.added_of(_permission_model()) == []
