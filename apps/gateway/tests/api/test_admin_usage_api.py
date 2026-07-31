import unittest

from fastapi.testclient import TestClient

from apps.gateway.main import app


class TestAdminUsageRoutesRegistered(unittest.TestCase):
    # route가 등록되어 있으면 미인증 요청은 404가 아니라 인증 실패 401로 끝난다.
    def setUp(self):
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides = {}

    def test_admin_usage_workflows_route_is_registered(self):
        response = self.client.get("/api/v1/admin/usage/workflows")

        self.assertEqual(response.status_code, 401)

    def test_admin_summary_route_is_registered(self):
        response = self.client.get("/api/v1/admin/summary")

        self.assertEqual(response.status_code, 401)
