import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from apps.gateway.main import app


class TestAdminAuditLogRoutesRegistered(unittest.TestCase):
    # route가 등록되어 있으면 미인증 요청은 404가 아니라 인증 실패 401로 끝난다.
    # 기존 권한 신청 route red 테스트와 같은 방식으로 admin audit endpoint 등록을 고정한다.
    def setUp(self):
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides = {}

    def test_admin_audit_logs_list_route_is_registered(self):
        response = self.client.get("/api/v1/admin/audit-logs")

        self.assertEqual(response.status_code, 401)

    def test_admin_audit_log_detail_route_is_registered(self):
        response = self.client.get(f"/api/v1/admin/audit-logs/{uuid4()}")

        self.assertEqual(response.status_code, 401)
