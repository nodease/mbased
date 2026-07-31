"""AppCreationPermissionService(FR-014 회수 확장, ADR-0016) 계약 테스트.

TDD red phase: 서비스가 아직 없으므로 전부 실패해야 한다.
admin-dashboard test_cases.md의 AppCreationPermissionService 단위 계약(AC-6)을 검증한다.
class/method 이름은 test_cases.md의 권장 이름을 따른다.

회수는 row 삭제 + `user_app_creation_permission.deleted` audit으로 표현하고,
missing/cross-org row는 404로 숨긴다 (ADR-0010 패턴). 과거 approved 신청
상태는 회수로 변하지 않는다.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.sql.operators import eq

from apps.shared.audit.actions import AuditAction
from apps.shared.db.models.audit_log import AuditLog


def _service():
    from apps.gateway.services.app_creation_permission_service import (
        AppCreationPermissionService,
    )

    return AppCreationPermissionService


def _permission_model():
    from apps.shared.db.models.user_app_creation_permission import (
        UserAppCreationPermission,
    )

    return UserAppCreationPermission


def _request_model():
    from apps.shared.db.models.permission_request import PermissionRequest

    return PermissionRequest


def _permission_row(organization_id, user_id=None, assigned_at=None):
    UserAppCreationPermission = _permission_model()
    return UserAppCreationPermission(
        id=uuid4(),
        grantee_organization_id=organization_id,
        user_id=user_id or uuid4(),
        assigned_by=uuid4(),
        assigned_at=assigned_at or datetime.now(timezone.utc),
    )


def _approved_request(organization_id, user_id):
    PermissionRequest = _request_model()
    return PermissionRequest(
        id=uuid4(),
        organization_id=organization_id,
        user_id=user_id,
        requested_permission="app.create",
        reason="workflow를 만들고 싶습니다",
        status="approved",
        created_at=datetime.now(timezone.utc),
        decided_by=uuid4(),
        decided_at=datetime.now(timezone.utc),
    )


class _Query:
    def __init__(self, items):
        self.items = list(items)
        self.filters = []
        self._order = None
        self._offset = 0
        self._limit = None

    def join(self, *args, **kwargs):
        return self

    def options(self, *args, **kwargs):
        return self

    def with_for_update(self, **kwargs):
        return self

    def filter(self, *expressions):
        self.filters.extend(expressions)
        return self

    def order_by(self, *expressions):
        if expressions:
            self._order = expressions[0]
        return self

    def offset(self, value):
        self._offset = value or 0
        return self

    def limit(self, value):
        self._limit = value
        return self

    def all(self):
        matched = [item for item in self.items if self._matches(item)]
        matched = self._apply_order(matched)
        end = None if self._limit is None else self._offset + self._limit
        return matched[self._offset : end]

    def first(self):
        return next(iter(self.all()), None)

    def count(self):
        return len([item for item in self.items if self._matches(item)])

    def _matches(self, item):
        return all(
            _matches_expression(item, expression) for expression in self.filters
        )

    def _apply_order(self, items):
        if self._order is None:
            return items
        text = str(self._order)
        column = text.split()[0].split(".")[-1]
        reverse = "DESC" in text.upper()
        if items and not hasattr(items[0], column):
            return items
        return sorted(items, key=lambda item: getattr(item, column), reverse=reverse)


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
    """모델 단위 dispatch + eq filter/정렬/slice 평가만 지원하는 최소 세션 fake."""

    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.added = []
        self.deleted = []
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

    def delete(self, obj):
        self.deleted.append(obj)
        if obj in self.rows:
            self.rows.remove(obj)

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


# --- list_permissions --------------------------------------------------------


def test_list_permissions_scopes_to_organization():
    organization_id = uuid4()
    mine = _permission_row(organization_id)
    other = _permission_row(uuid4())
    db = _Db(rows=[mine, other])

    total, items = _service().list_permissions(
        db, organization_id=organization_id, page=1, limit=20
    )

    assert total == 1
    assert [row.id for row in items] == [mine.id]


def test_list_permissions_orders_by_assigned_at_desc():
    organization_id = uuid4()
    now = datetime.now(timezone.utc)
    older = _permission_row(organization_id, assigned_at=now - timedelta(days=1))
    newer = _permission_row(organization_id, assigned_at=now)
    db = _Db(rows=[older, newer])

    _, items = _service().list_permissions(
        db, organization_id=organization_id, page=1, limit=20
    )

    assert [row.id for row in items] == [newer.id, older.id]


def test_list_permissions_paginates_with_total():
    organization_id = uuid4()
    now = datetime.now(timezone.utc)
    rows = [
        _permission_row(organization_id, assigned_at=now - timedelta(days=offset))
        for offset in range(3)
    ]
    db = _Db(rows=rows)

    total, items = _service().list_permissions(
        db, organization_id=organization_id, page=2, limit=2
    )

    assert total == 3
    assert len(items) == 1


def test_list_permissions_returns_empty_for_no_rows():
    db = _Db(rows=[])

    total, items = _service().list_permissions(
        db, organization_id=uuid4(), page=1, limit=20
    )

    assert total == 0
    assert items == []


# --- revoke_permission -------------------------------------------------------


def test_revoke_permission_deletes_row_and_records_audit():
    organization_id = uuid4()
    row = _permission_row(organization_id)
    db = _Db(rows=[row])

    revoked = _service().revoke_permission(
        db,
        permission_id=row.id,
        organization_id=organization_id,
        revoked_by=uuid4(),
    )

    assert revoked.id == row.id
    assert revoked.user_id == row.user_id
    assert row in db.deleted

    audits = db.added_of(AuditLog)
    assert [audit.action for audit in audits] == [
        AuditAction.USER_APP_CREATION_PERMISSION_DELETED
    ]
    assert audits[0].target_type == "user_app_creation_permission"
    assert audits[0].target_id == str(row.id)
    assert audits[0].before == {
        "grantee_organization_id": str(organization_id),
        "user_id": str(row.user_id),
    }
    assert audits[0].after is None
    assert audits[0].audit_metadata == {"organization_id": str(organization_id)}
    assert db.commits >= 1


def test_revoke_permission_rolls_back_when_audit_add_fails():
    organization_id = uuid4()
    row = _permission_row(organization_id)
    db = _Db(rows=[row])
    db.audit_add_error = RuntimeError("audit unavailable")

    with pytest.raises(RuntimeError, match="audit unavailable"):
        _service().revoke_permission(
            db,
            permission_id=row.id,
            organization_id=organization_id,
            revoked_by=uuid4(),
        )

    assert db.commits == 0
    assert db.rollbacks == 1


def test_revoke_permission_keeps_past_request_status():
    organization_id = uuid4()
    user_id = uuid4()
    row = _permission_row(organization_id, user_id=user_id)
    approved = _approved_request(organization_id, user_id)
    db = _Db(rows=[row, approved])

    _service().revoke_permission(
        db,
        permission_id=row.id,
        organization_id=organization_id,
        revoked_by=uuid4(),
    )

    assert approved.status == "approved"


def test_revoke_permission_hides_missing_row_as_404():
    db = _Db(rows=[])

    with pytest.raises(HTTPException) as exc_info:
        _service().revoke_permission(
            db,
            permission_id=uuid4(),
            organization_id=uuid4(),
            revoked_by=uuid4(),
        )

    assert exc_info.value.status_code == 404
    assert db.added_of(AuditLog) == []


def test_revoke_permission_hides_cross_org_row_as_404():
    row = _permission_row(uuid4())
    db = _Db(rows=[row])

    with pytest.raises(HTTPException) as exc_info:
        _service().revoke_permission(
            db,
            permission_id=row.id,
            organization_id=uuid4(),
            revoked_by=uuid4(),
        )

    assert exc_info.value.status_code == 404
    assert db.deleted == []
    assert db.added_of(AuditLog) == []
