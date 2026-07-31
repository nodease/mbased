import unittest
from uuid import UUID

from fastapi.testclient import TestClient

from apps.gateway.main import app
from apps.gateway.utils.audit import _request_metadata
from apps.shared.audit.context import clear_current_metadata, set_current_metadata


REQUEST_ID = "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b"


class TestRequestIdMiddleware(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_request_id_header_is_preserved(self):
        response = self.client.get("/", headers={"X-Request-ID": REQUEST_ID})

        self.assertEqual(response.headers["X-Request-ID"], REQUEST_ID)

    def test_request_id_header_is_generated(self):
        response = self.client.get("/")

        self.assertEqual(
            str(UUID(response.headers["X-Request-ID"])),
            response.headers["X-Request-ID"],
        )

    def test_noncanonical_request_id_is_replaced_instead_of_echoed(self):
        csrf_token_sentinel = (
            "v1.1780000000.bm9uY2U.Y3NyZi10b2tlbi1tdXN0LW5vdC1yZWFjaC1hdWRpdA"
        )

        response = self.client.get(
            "/",
            headers={"X-Request-ID": csrf_token_sentinel},
        )

        safe_request_id = response.headers["X-Request-ID"]
        self.assertNotEqual(safe_request_id, csrf_token_sentinel)
        self.assertEqual(str(UUID(safe_request_id)), safe_request_id)


class TestAuditContext(unittest.TestCase):
    def test_audit_metadata_reads_request_context_without_request_arg(self):
        token = set_current_metadata(
            {
                "ip": "127.0.0.1",
                "user_agent": "test-agent",
                "request_id": REQUEST_ID,
            }
        )
        try:
            self.assertEqual(
                _request_metadata(None),
                {
                    "ip": "127.0.0.1",
                    "user_agent": "test-agent",
                    "request_id": REQUEST_ID,
                },
            )
        finally:
            clear_current_metadata(token)


if __name__ == "__main__":
    unittest.main()
