import asyncio
import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

from apps.gateway.api.v1.endpoints.webhook import CAPTURE_SESSIONS
from apps.gateway.main import app, audit_permission_denied
from apps.shared.audit.actions import AuditAction
from apps.shared.db.session import get_db


REQUEST_ID = "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b"


class TestPermissionDeniedAudit(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        CAPTURE_SESSIONS.clear()

    def tearDown(self):
        app.dependency_overrides = {}

    def test_unauthorized_request_records_audit(self):
        app.dependency_overrides[get_db] = lambda: MagicMock()

        with patch("apps.gateway.main.record_audit") as record_audit:
            response = self.client.get(
                "/api/v1/auth/me",
                headers={"X-Request-ID": REQUEST_ID},
            )

        self.assertEqual(response.status_code, 401)
        record_audit.assert_called_once()
        event = record_audit.call_args.kwargs
        self.assertEqual(event["action"], AuditAction.AUTH_PERMISSION_DENIED)
        self.assertEqual(event["status"], "failure")
        self.assertEqual(event["metadata"]["method"], "GET")
        self.assertEqual(event["metadata"]["path"], "/api/v1/auth/me")
        self.assertEqual(event["metadata"]["status_code"], 401)
        self.assertEqual(event["metadata"]["request_id"], REQUEST_ID)

    def test_forbidden_request_records_audit(self):
        mock_db_session = MagicMock()
        mock_app = MagicMock()
        mock_app.auth_secret = "secret123"
        mock_db_session.query.return_value.filter.return_value.first.return_value = (
            mock_app
        )
        app.dependency_overrides[get_db] = lambda: mock_db_session

        with patch("apps.gateway.main.record_audit") as record_audit:
            response = self.client.post(
                "/api/v1/hooks/test-slug",
                json={},
                headers={
                    "Authorization": "Bearer wrong-token",
                    "X-Request-ID": REQUEST_ID,
                },
            )

        self.assertEqual(response.status_code, 403)
        record_audit.assert_called_once()
        event = record_audit.call_args.kwargs
        self.assertEqual(event["action"], AuditAction.AUTH_PERMISSION_DENIED)
        self.assertEqual(event["status"], "failure")
        self.assertEqual(event["metadata"]["method"], "POST")
        self.assertEqual(event["metadata"]["path"], "/api/v1/hooks/test-slug")
        self.assertEqual(event["metadata"]["status_code"], 403)
        self.assertEqual(event["metadata"]["request_id"], REQUEST_ID)

    def test_already_recorded_permission_denial_skips_global_auth_audit(self):
        request = Request(
            {
                "type": "http",
                "method": "PATCH",
                "path": "/api/v1/organizations/example",
                "headers": [],
                "query_string": b"",
                "server": ("testserver", 80),
                "client": ("testclient", 50000),
                "scheme": "http",
            }
        )
        exc = HTTPException(status_code=403, detail="Forbidden")
        setattr(exc, "audit_recorded", True)

        with patch("apps.gateway.main.record_audit") as record_audit:
            response = asyncio.run(audit_permission_denied(request, exc))

        self.assertEqual(response.status_code, 403)
        record_audit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
