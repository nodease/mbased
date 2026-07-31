"""권한 신청 API(FR-041/FR-014, ADR-0016) 계약 테스트.

TDD red phase: route와 App 생성 권한 검사가 아직 없으므로 전부 실패해야 한다.
- 신청 제출: POST /api/v1/permission-requests (organization feature)
- 관리자 목록/승인/거절: /api/v1/admin/permission-requests (admin-dashboard feature)
- App 생성 차단: POST /api/v1/apps 는 생성 권한 없는 member에게 403
"""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from apps.gateway.auth.dependencies import get_current_user
from apps.gateway.main import app
from apps.shared.db.session import get_db


VALID_APP_PAYLOAD = {
    "name": "test-app",
    "description": "permission request red test",
    "icon": {"type": "emoji", "content": "🤖", "background_color": "#FFFFFF"},
}


class TestPermissionRequestRoutesRegistered(unittest.TestCase):
    """route가 등록되어 있으면 미인증 요청은 404가 아니라 401이어야 한다."""

    def setUp(self):
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides = {}

    def test_submit_permission_request_route_is_registered(self):
        response = self.client.post(
            "/api/v1/permission-requests",
            json={"reason": "workflow를 만들고 싶습니다"},
        )
        self.assertEqual(response.status_code, 401)

    def test_admin_permission_requests_list_route_is_registered(self):
        response = self.client.get("/api/v1/admin/permission-requests")
        self.assertEqual(response.status_code, 401)

    def test_admin_approve_route_is_registered(self):
        response = self.client.post(
            f"/api/v1/admin/permission-requests/{uuid4()}/approve"
        )
        self.assertEqual(response.status_code, 401)

    def test_admin_reject_route_is_registered(self):
        response = self.client.post(
            f"/api/v1/admin/permission-requests/{uuid4()}/reject"
        )
        self.assertEqual(response.status_code, 401)


class TestAppCreationPermissionEnforcement(unittest.TestCase):
    """ADR-0016: App 생성은 owner/manager 또는 user_app_creation_permissions
    row 보유자만 허용하고, 그 외에는 403 permission.denied로 차단한다."""

    def setUp(self):
        # 구현 전 응답 검증 오류가 테스트를 중단시키지 않도록 500으로 받는다.
        self.client = TestClient(app, raise_server_exceptions=False)

    def tearDown(self):
        app.dependency_overrides = {}

    def test_app_creation_blocked_for_member_without_permission(self):
        member_id = uuid4()
        app.dependency_overrides[get_db] = lambda: MagicMock()
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
            id=member_id
        )

        with (
            patch(
                "apps.gateway.api.v1.endpoints.app.resolve_active_organization_id",
                return_value=str(uuid4()),
            ),
            patch(
                "apps.gateway.api.v1.endpoints.app.AppService.create_app"
            ) as create_app,
            patch(
                "apps.shared.services.permissions.has_app_creation_permission",
                return_value=False,
                create=True,
            ),
        ):
            response = self.client.post(
                "/api/v1/apps",
                json=VALID_APP_PAYLOAD,
                headers={"X-Organization-Id": str(uuid4())},
            )

        self.assertEqual(response.status_code, 403)
        create_app.assert_not_called()


if __name__ == "__main__":
    unittest.main()
