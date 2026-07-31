"""App 생성 권한 보유 목록/회수 API(FR-014 회수 확장, AC-6) 계약 테스트.

TDD red phase: route가 아직 없으므로 전부 실패해야 한다.
- 보유 목록: GET /api/v1/admin/app-creation-permissions
- 회수: DELETE /api/v1/admin/app-creation-permissions/{permission_id}

route가 등록되어 있으면 미인증 요청은 404(route 없음)가 아니라 401이어야 한다.
owner/manager 권한 경계와 404 숨김의 상세 계약은 service 단위 테스트
(test_app_creation_permission_service.py)와 admin-dashboard test_cases.md의
AC-6/Permission Tests가 소유한다.
"""

import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from apps.gateway.main import app


class TestAppCreationPermissionRoutesRegistered(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides = {}

    def test_list_app_creation_permissions_route_is_registered(self):
        response = self.client.get("/api/v1/admin/app-creation-permissions")
        self.assertEqual(response.status_code, 401)

    def test_revoke_app_creation_permission_route_is_registered(self):
        response = self.client.delete(
            f"/api/v1/admin/app-creation-permissions/{uuid4()}"
        )
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
