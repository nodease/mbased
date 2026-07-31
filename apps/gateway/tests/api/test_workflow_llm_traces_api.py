from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient

from apps.gateway.auth.dependencies import get_current_user
from apps.gateway.main import app
from apps.shared.db.session import get_db


class TestWorkflowLlmTracesApi:
    def setup_method(self):
        self.client = TestClient(app)

    def teardown_method(self):
        app.dependency_overrides = {}

    def test_llm_trace_endpoint_requires_workflow_read_and_returns_whitelist(self):
        workflow_id = uuid4()
        run_id = uuid4()
        trace_id = uuid4()
        model_id = uuid4()
        credential_id = uuid4()
        user_id = uuid4()
        db = MagicMock()
        created_at = datetime(2026, 6, 27, tzinfo=timezone.utc)

        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with (
            patch(
                "apps.gateway.api.v1.endpoints.workflow.ensure_workflow_permission"
            ) as ensure_read,
            patch(
                "apps.gateway.api.v1.endpoints.workflow.LLMService.list_workflow_run_llm_traces"
            ) as list_traces,
        ):
            list_traces.return_value = {
                "total": 1,
                "limit": 10,
                "offset": 5,
                "items": [
                    {
                        "id": trace_id,
                        "workflow_id": workflow_id,
                        "workflow_run_id": run_id,
                        "node_id": "node-a",
                        "model_id": model_id,
                        "model_name": "GPT Test",
                        "provider": "openai",
                        "credential_id": credential_id,
                        "prompt_tokens": 10,
                        "completion_tokens": 20,
                        "total_tokens": 30,
                        "total_cost": 0.001,
                        "latency_ms": 123,
                        "status": "success",
                        "created_at": created_at,
                    }
                ],
            }

            response = self.client.get(
                f"/api/v1/workflows/{workflow_id}/runs/{run_id}/llm-traces"
                "?node_id=node-a&limit=10&offset=5"
            )

        assert response.status_code == 200
        ensure_read.assert_called_once()
        guard_args = ensure_read.call_args.args
        assert guard_args[0] is db
        assert guard_args[1].id == user_id
        assert guard_args[2] == str(workflow_id)
        assert guard_args[3] == "read"
        list_traces.assert_called_once_with(
            db,
            workflow_id,
            run_id,
            node_id="node-a",
            limit=10,
            offset=5,
        )

        body = response.json()
        assert body["total"] == 1
        item = body["items"][0]
        assert item["node_id"] == "node-a"
        assert item["credential_id"] == str(credential_id)
        assert item["total_tokens"] == 30
        assert "api_key" not in item
        assert "encrypted_config" not in item
        assert "raw_prompt" not in item
        assert "raw_completion" not in item

    def test_resource_permission_denied_does_not_emit_global_audit_again(self):
        workflow_id = uuid4()
        run_id = uuid4()
        user_id = uuid4()

        app.dependency_overrides[get_db] = lambda: MagicMock()
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        exc = HTTPException(status_code=403, detail="Forbidden")
        setattr(exc, "audit_recorded", True)
        with (
            patch(
                "apps.gateway.api.v1.endpoints.workflow.ensure_workflow_permission",
                side_effect=exc,
            ),
            patch("apps.gateway.main.record_audit") as global_record_audit,
        ):
            response = self.client.get(
                f"/api/v1/workflows/{workflow_id}/runs/{run_id}/llm-traces"
            )

        assert response.status_code == 403
        global_record_audit.assert_not_called()

    def test_run_observability_endpoints_apply_workflow_read_guard(self):
        workflow_id = uuid4()
        run_id = uuid4()
        user_id = uuid4()

        app.dependency_overrides[get_db] = lambda: MagicMock()
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        paths = [
            f"/api/v1/workflows/{workflow_id}/runs",
            f"/api/v1/workflows/{workflow_id}/runs/{run_id}",
            f"/api/v1/workflows/{workflow_id}/stats",
        ]

        def deny(*args, **kwargs):
            exc = HTTPException(status_code=403, detail="Forbidden")
            setattr(exc, "audit_recorded", True)
            raise exc

        with patch(
            "apps.gateway.api.v1.endpoints.workflow.ensure_workflow_permission",
            side_effect=deny,
        ) as ensure_read:
            responses = [self.client.get(path) for path in paths]

        assert [response.status_code for response in responses] == [403, 403, 403]
        assert ensure_read.call_count == len(paths)

    def test_workflow_permission_endpoint_returns_effective_action_flags(self):
        workflow_id = uuid4()
        organization_id = uuid4()
        user_id = uuid4()
        source_team_id = uuid4()

        app.dependency_overrides[get_db] = lambda: MagicMock()
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with (
            patch(
                "apps.gateway.api.v1.endpoints.workflow.ensure_workflow_permission",
                return_value=SimpleNamespace(
                    id=workflow_id, organization_id=organization_id
                ),
            ) as ensure_read,
            patch(
                "apps.gateway.api.v1.endpoints.workflow.get_effective_workflow_auth_state",
                return_value="operator",
            ) as effective_state,
            patch(
                "apps.gateway.api.v1.endpoints.workflow.get_workflow_permission_sources",
                return_value=[
                    {
                        "type": "team",
                        "team_id": source_team_id,
                        "team_name": "운영팀",
                        "auth_state": "operator",
                    }
                ],
            ) as permission_sources,
        ):
            response = self.client.get(
                f"/api/v1/workflows/{workflow_id}/permissions/me"
            )

        assert response.status_code == 200
        ensure_read.assert_called_once()
        effective_state.assert_called_once()
        permission_sources.assert_called_once()
        payload = response.json()
        assert payload == {
            "workflow_id": str(workflow_id),
            "organization_id": str(organization_id),
            "auth_state": "operator",
            "can_read": True,
            "can_write": False,
            "can_execute": True,
            "can_deploy": False,
            "can_manage": False,
            "sources": [
                {
                    "type": "team",
                    "auth_state": "operator",
                    "team_id": str(source_team_id),
                    "team_name": "운영팀",
                }
            ],
        }
