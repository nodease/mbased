import asyncio
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi import BackgroundTasks, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from apps.gateway.api.v1.endpoints import webhook as webhook_endpoint
from apps.gateway.api.v1.endpoints.webhook import CAPTURE_SESSIONS
from apps.gateway.application.webhook_ingress import (
    DEFAULT_WEBHOOK_INGRESS_POLICY,
    WebhookIngressError,
    WebhookIngressLimits,
    WebhookIngressPolicy,
)
from apps.gateway.main import app
from apps.shared.db.models.workflow_deployment import DeploymentType
from apps.shared.db.session import get_db
from apps.shared.domain.app_auth_secret import (
    APP_AUTH_SECRET_VERIFIER_VERSION,
    app_auth_secret_verifier,
)
from apps.shared.domain.deployment_runtime_policy import (
    DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
)


class TestWebhookIngressApi(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app, raise_server_exceptions=False)
        self.url_slug = "webhook-ingress"
        self.auth_secret = "header-secret"
        CAPTURE_SESSIONS.clear()
        app.dependency_overrides = {}

    def tearDown(self) -> None:
        CAPTURE_SESSIONS.clear()
        app.dependency_overrides = {}

    def _mock_db_with_app(self, *, active_deployment: bool = False) -> MagicMock:
        mock_db = MagicMock()
        mock_app = MagicMock()
        mock_app.id = uuid4()
        mock_app.url_slug = self.url_slug
        mock_app.auth_secret_verifier = app_auth_secret_verifier(self.auth_secret)
        mock_app.auth_secret_verifier_version = APP_AUTH_SECRET_VERIFIER_VERSION
        mock_app.auth_secret_previous_verifier = None
        mock_app.auth_secret_previous_verifier_version = None
        mock_app.auth_secret_previous_valid_until = None
        mock_app.workflow_id = uuid4()
        mock_app.active_deployment_id = uuid4() if active_deployment else None
        mock_app.created_by = uuid4()
        mock_app.organization_id = uuid4()

        mock_deployment = MagicMock()
        mock_deployment.id = mock_app.active_deployment_id
        mock_deployment.app_id = mock_app.id
        mock_deployment.type = DeploymentType.WEBHOOK
        mock_deployment.is_active = True

        def query(model):
            result = MagicMock()
            if model is webhook_endpoint.App:
                result.filter.return_value.first.return_value = mock_app
            elif model is webhook_endpoint.WorkflowDeployment:
                result.filter.return_value.first.return_value = mock_deployment
            else:
                result.filter.return_value.first.return_value = None
            return result

        mock_db.query.side_effect = query
        app.dependency_overrides[get_db] = lambda: mock_db
        return mock_db

    def _post(
        self,
        *,
        query: str = "",
        headers: dict[str, str] | None = None,
        content: bytes = b"{}",
    ):
        request_headers = {"Content-Type": "application/json"}
        if headers:
            request_headers.update(headers)
        return self.client.post(
            f"/api/v1/hooks/{self.url_slug}{query}",
            headers=request_headers,
            content=content,
        )

    def _request(
        self,
        *,
        headers: list[tuple[bytes, bytes]],
        receive,
        query_string: bytes = b"",
    ) -> Request:
        return Request(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": f"/api/v1/hooks/{self.url_slug}",
                "raw_path": f"/api/v1/hooks/{self.url_slug}".encode("ascii"),
                "query_string": query_string,
                "headers": headers,
                "client": ("testclient", 50000),
                "server": ("testserver", 80),
            },
            receive,
        )

    def test_query_token_forms_are_hard_rejected_even_with_valid_header(self) -> None:
        for query in (
            "?token",
            "?token=",
            "?token=legacy",
            "?token=one&token=two",
            "?t%6fken=encoded",
        ):
            with self.subTest(query=query):
                mock_db = self._mock_db_with_app(active_deployment=True)
                with patch.object(
                    webhook_endpoint.WorkflowBudgetService,
                    "ensure_workflow_budget_allows_execution",
                ) as budget_check, patch.object(
                    webhook_endpoint,
                    "run_webhook_workflow",
                ) as background_run:
                    response = self._post(
                        query=query,
                        headers={"Authorization": f"Bearer {self.auth_secret}"},
                    )

                self.assertEqual(response.status_code, 400)
                self.assertEqual(
                    response.json(),
                    {"detail": "webhook.query_secret_not_supported"},
                )
                self.assertEqual(mock_db.query.call_count, 1)
                budget_check.assert_not_called()
                background_run.assert_not_called()

    def test_multiple_credential_sources_are_rejected_before_body_processing(self) -> None:
        self._mock_db_with_app(active_deployment=True)
        with patch.object(
            webhook_endpoint.WorkflowBudgetService,
            "ensure_workflow_budget_allows_execution",
        ) as budget_check, patch.object(
            webhook_endpoint,
            "run_webhook_workflow",
        ) as background_run:
            response = self._post(
                headers={
                    "Authorization": f"Bearer {self.auth_secret}",
                    "X-Webhook-Secret": self.auth_secret,
                },
                content=b"not-json",
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            {"detail": "webhook.credential_ambiguous"},
        )
        budget_check.assert_not_called()
        background_run.assert_not_called()

    def test_missing_and_invalid_credentials_share_one_safe_response(self) -> None:
        for headers in ({}, {"Authorization": "Bearer wrong"}):
            with self.subTest(headers=bool(headers)):
                self._mock_db_with_app(active_deployment=True)
                response = self._post(headers=headers)

                self.assertEqual(response.status_code, 403)
                self.assertEqual(
                    response.json(),
                    {"detail": "webhook.authentication_failed"},
                )

    def test_missing_authentication_does_not_read_request_body(self) -> None:
        mock_db = self._mock_db_with_app(active_deployment=True)
        receive_count = 0

        async def receive():
            nonlocal receive_count
            receive_count += 1
            raise AssertionError("unauthenticated body must not be read")

        request = self._request(
            headers=[(b"content-type", b"application/json")],
            receive=receive,
        )

        with self.assertRaises(HTTPException) as exc_info:
            asyncio.run(
                webhook_endpoint.receive_webhook(
                    self.url_slug,
                    request,
                    BackgroundTasks(),
                    runtime_policy=DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
                    ingress_policy=DEFAULT_WEBHOOK_INGRESS_POLICY,
                    db=mock_db,
                )
            )

        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(receive_count, 0)

    def test_duplicate_raw_authorization_headers_do_not_read_body(self) -> None:
        mock_db = self._mock_db_with_app(active_deployment=True)
        receive_count = 0

        async def receive():
            nonlocal receive_count
            receive_count += 1
            raise AssertionError("ambiguous credential body must not be read")

        request = self._request(
            headers=[
                (b"authorization", f"Bearer {self.auth_secret}".encode("ascii")),
                (b"authorization", f"Bearer {self.auth_secret}".encode("ascii")),
                (b"content-type", b"application/json"),
            ],
            receive=receive,
        )

        with self.assertRaises(HTTPException) as exc_info:
            asyncio.run(
                webhook_endpoint.receive_webhook(
                    self.url_slug,
                    request,
                    BackgroundTasks(),
                    runtime_policy=DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
                    ingress_policy=DEFAULT_WEBHOOK_INGRESS_POLICY,
                    db=mock_db,
                )
            )

        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "webhook.credential_ambiguous")
        self.assertEqual(receive_count, 0)

    def test_bearer_and_custom_header_each_accept_valid_payload(self) -> None:
        credential_headers = (
            {"Authorization": f"Bearer {self.auth_secret}"},
            {"X-Webhook-Secret": self.auth_secret},
        )
        for headers in credential_headers:
            with self.subTest(header=next(iter(headers))):
                self._mock_db_with_app(active_deployment=True)
                with patch.object(
                    webhook_endpoint.WorkflowBudgetService,
                    "ensure_workflow_budget_allows_execution",
                ) as budget_check, patch.object(
                    webhook_endpoint,
                    "run_webhook_workflow",
                ) as background_run:
                    response = self._post(
                        headers=headers,
                        content=b'{"event":"created"}',
                    )

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["status"], "accepted")
                budget_check.assert_called_once()
                background_run.assert_called_once()
                self.assertEqual(
                    background_run.call_args.args[1],
                    {"event": "created"},
                )

    def test_unsupported_media_is_rejected_before_deployment_and_budget(self) -> None:
        mock_db = self._mock_db_with_app(active_deployment=True)
        with patch.object(
            webhook_endpoint.WorkflowBudgetService,
            "ensure_workflow_budget_allows_execution",
        ) as budget_check, patch.object(
            webhook_endpoint,
            "run_webhook_workflow",
        ) as background_run:
            response = self._post(
                headers={
                    "Authorization": f"Bearer {self.auth_secret}",
                    "Content-Type": "text/plain",
                }
            )

        self.assertEqual(response.status_code, 415)
        self.assertEqual(
            response.json(),
            {"detail": "webhook.payload.unsupported_media_type"},
        )
        self.assertEqual(mock_db.query.call_count, 1)
        budget_check.assert_not_called()
        background_run.assert_not_called()

    def test_declared_oversize_is_rejected_before_body_and_downstream(self) -> None:
        mock_db = self._mock_db_with_app(active_deployment=True)
        with patch.object(
            webhook_endpoint.WorkflowBudgetService,
            "ensure_workflow_budget_allows_execution",
        ) as budget_check, patch.object(
            webhook_endpoint,
            "run_webhook_workflow",
        ) as background_run:
            response = self._post(
                headers={
                    "Authorization": f"Bearer {self.auth_secret}",
                    "Content-Length": "1048577",
                }
            )

        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json(), {"detail": "webhook.payload.too_large"})
        self.assertEqual(mock_db.query.call_count, 1)
        budget_check.assert_not_called()
        background_run.assert_not_called()

    def test_actual_oversize_is_rejected_even_when_declared_length_is_understated(
        self,
    ) -> None:
        self._mock_db_with_app(active_deployment=True)
        oversized_json = b'{"value":"' + (b"a" * 1_048_575) + b'"}'
        with patch.object(
            webhook_endpoint.WorkflowBudgetService,
            "ensure_workflow_budget_allows_execution",
        ) as budget_check, patch.object(
            webhook_endpoint,
            "run_webhook_workflow",
        ) as background_run:
            response = self._post(
                headers={
                    "Authorization": f"Bearer {self.auth_secret}",
                    "Content-Length": "2",
                },
                content=oversized_json,
            )

        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json(), {"detail": "webhook.payload.too_large"})
        budget_check.assert_not_called()
        background_run.assert_not_called()

    def test_invalid_json_does_not_mutate_capture_or_query_deployment(self) -> None:
        mock_db = self._mock_db_with_app(active_deployment=True)
        CAPTURE_SESSIONS[self.url_slug] = {
            "capture_id": "capture-id",
            "created_at": datetime.now(timezone.utc),
            "expires_at": datetime.now(timezone.utc) + timedelta(seconds=60),
            "payload": None,
            "requested_by": str(uuid4()),
            "status": "waiting",
        }
        with patch.object(
            webhook_endpoint.WorkflowBudgetService,
            "ensure_workflow_budget_allows_execution",
        ) as budget_check, patch.object(
            webhook_endpoint,
            "run_webhook_workflow",
        ) as background_run:
            response = self._post(
                headers={"Authorization": f"Bearer {self.auth_secret}"},
                content=b"{",
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"detail": "webhook.payload.invalid"})
        self.assertEqual(CAPTURE_SESSIONS[self.url_slug]["status"], "waiting")
        self.assertIsNone(CAPTURE_SESSIONS[self.url_slug]["payload"])
        self.assertEqual(mock_db.query.call_count, 1)
        budget_check.assert_not_called()
        background_run.assert_not_called()

    def test_non_object_root_is_rejected_before_workflow_admission(self) -> None:
        self._mock_db_with_app(active_deployment=True)
        with patch.object(
            webhook_endpoint.WorkflowBudgetService,
            "ensure_workflow_budget_allows_execution",
        ) as budget_check, patch.object(
            webhook_endpoint,
            "run_webhook_workflow",
        ) as background_run:
            response = self._post(
                headers={"Authorization": f"Bearer {self.auth_secret}"},
                content=b'"scalar"',
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"detail": "webhook.payload.invalid"})
        budget_check.assert_not_called()
        background_run.assert_not_called()

    def test_internal_looking_payload_keys_do_not_replace_server_arguments(self) -> None:
        self._mock_db_with_app(active_deployment=True)
        payload = {
            "app_id": "payload-app",
            "organization_id": "payload-org",
            "workflow_id": "payload-workflow",
            "deployment_id": "payload-deployment",
            "user_id": "payload-user",
            "trigger_mode": "interactive",
            "execution_context": {"admin": True},
        }
        with patch.object(
            webhook_endpoint.WorkflowBudgetService,
            "ensure_workflow_budget_allows_execution",
        ), patch.object(
            webhook_endpoint,
            "run_webhook_workflow",
        ) as background_run:
            response = self._post(
                headers={"Authorization": f"Bearer {self.auth_secret}"},
                content=json.dumps(payload).encode("ascii"),
            )

        self.assertEqual(response.status_code, 200)
        args = background_run.call_args.args
        self.assertEqual(args[1], payload)
        self.assertNotEqual(args[2], payload["user_id"])
        self.assertNotEqual(args[3], payload["workflow_id"])
        self.assertNotEqual(args[4], payload["app_id"])
        self.assertNotEqual(args[5], payload["organization_id"])

    def test_duplicate_valid_deliveries_are_independently_admitted(self) -> None:
        self._mock_db_with_app(active_deployment=True)
        with patch.object(
            webhook_endpoint.WorkflowBudgetService,
            "ensure_workflow_budget_allows_execution",
        ) as budget_check, patch.object(
            webhook_endpoint,
            "run_webhook_workflow",
        ) as background_run:
            first = self._post(
                headers={"Authorization": f"Bearer {self.auth_secret}"},
                content=b'{"event":"same"}',
            )
            second = self._post(
                headers={"Authorization": f"Bearer {self.auth_secret}"},
                content=b'{"event":"same"}',
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(budget_check.call_count, 2)
        self.assertEqual(background_run.call_count, 2)

    def test_bounded_stream_accepts_exact_size_object(self) -> None:
        messages = [
            {"type": "http.request", "body": b"{}", "more_body": False},
        ]

        async def receive():
            return messages.pop(0)

        request = self._request(headers=[], receive=receive)
        policy = WebhookIngressPolicy(limits=WebhookIngressLimits(max_body_bytes=2))

        payload = asyncio.run(webhook_endpoint._read_webhook_payload(request, policy))

        self.assertEqual(payload, {})

    def test_bounded_stream_rejects_multi_chunk_crossing_before_buffering(self) -> None:
        messages = [
            {"type": "http.request", "body": b'{"a"', "more_body": True},
            {"type": "http.request", "body": b":1}", "more_body": False},
        ]

        async def receive():
            return messages.pop(0)

        request = self._request(headers=[], receive=receive)
        policy = WebhookIngressPolicy(limits=WebhookIngressLimits(max_body_bytes=5))

        with self.assertRaises(WebhookIngressError) as exc_info:
            asyncio.run(webhook_endpoint._read_webhook_payload(request, policy))

        self.assertEqual(exc_info.exception.status_code, 413)
        self.assertEqual(exc_info.exception.code, "webhook.payload.too_large")

    def test_bounded_stream_maps_disconnect_to_static_invalid_error(self) -> None:
        async def receive():
            return {"type": "http.disconnect"}

        request = self._request(headers=[], receive=receive)

        with self.assertRaises(WebhookIngressError) as exc_info:
            asyncio.run(
                webhook_endpoint._read_webhook_payload(
                    request,
                    DEFAULT_WEBHOOK_INGRESS_POLICY,
                )
            )

        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.code, "webhook.payload.invalid")

    def test_bounded_stream_maps_stall_to_static_timeout_error(self) -> None:
        async def receive():
            await asyncio.sleep(0.05)
            return {"type": "http.request", "body": b"{}", "more_body": False}

        request = self._request(headers=[], receive=receive)
        policy = WebhookIngressPolicy(
            limits=WebhookIngressLimits(processing_timeout_seconds=0.001)
        )

        with self.assertRaises(WebhookIngressError) as exc_info:
            asyncio.run(webhook_endpoint._read_webhook_payload(request, policy))

        self.assertEqual(exc_info.exception.status_code, 408)
        self.assertEqual(exc_info.exception.code, "webhook.payload.timeout")


if __name__ == "__main__":
    unittest.main()
