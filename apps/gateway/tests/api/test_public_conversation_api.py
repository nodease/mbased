from __future__ import annotations

import secrets
import threading
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
import pytest

from apps.gateway.adapters.authentication.client_network import ClientNetworkResolver
from apps.gateway.api.deps import get_db
from apps.gateway.api.v1.endpoints import public_conversation, run
from apps.gateway.middleware.public_conversation_cors import (
    PublicConversationCorsBoundaryMiddleware,
)
from apps.memory.application.public_lifecycle import (
    ClosePublicConversationResult,
    PublicConversationResult,
    PublicTranscriptResult,
)
from apps.memory.application.public_runtime import StartPublicConversationTurnResult
from apps.memory.domain.conversation import SessionLifecycle, TurnStatus
from apps.memory.domain.errors import (
    AccessGrantNotUsableError,
    PublicConversationTurnLimitExceededError,
)


def _key() -> str:
    return secrets.token_urlsafe(24)


class _Create:
    def __init__(self) -> None:
        self.commands = []

    def execute(self, command):
        self.commands.append(command)
        return PublicConversationResult(
            lifecycle=SessionLifecycle.ACTIVE,
            lifecycle_revision=1,
            memory_contract_version="conversation-memory-v1",
            expires_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
            access_token=f"cag_v1_{secrets.token_urlsafe(32)}",
            replayed=False,
        )


class _Close:
    def __init__(self) -> None:
        self.commands = []

    def execute(self, command):
        self.commands.append(command)
        return ClosePublicConversationResult(
            lifecycle=SessionLifecycle.CLOSED,
            lifecycle_revision=2,
            memory_contract_version="conversation-memory-v1",
            expires_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
            replayed=False,
        )


class _Reset:
    def __init__(self) -> None:
        self.commands = []

    def execute(self, command):
        self.commands.append(command)
        return SimpleNamespace(
            lifecycle=SessionLifecycle.ACTIVE,
            lifecycle_revision=1,
            memory_contract_version="conversation-memory-v1",
            expires_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
            access_token=f"cag_v1_{secrets.token_urlsafe(32)}",
            replayed=False,
            previous_lifecycle=SessionLifecycle.CLOSED,
            previous_lifecycle_revision=2,
        )


class _Transcript:
    def __init__(self) -> None:
        self.queries = []

    def execute(self, **query):
        self.queries.append(query)
        return PublicTranscriptResult(
            lifecycle=SessionLifecycle.CLOSED,
            lifecycle_revision=2,
            content_revision=3,
            expires_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
            turns=(),
        )


class _TurnStatus:
    def __init__(self) -> None:
        self.queries = []

    def execute(self, **query):
        self.queries.append(query)
        if query["access_token"] != "valid-capability":
            raise AccessGrantNotUsableError()
        return SimpleNamespace(
            turn_id=query["turn_id"],
            turn_sequence=2,
            turn_state=TurnStatus.COMPLETED,
            display="Approved redacted answer",
            safe_failure_reason=None,
            lifecycle_revision=7,
        )


class _Application:
    def __init__(self) -> None:
        self.create = _Create()
        self.close = _Close()
        self.reset = _Reset()
        self.transcript = _Transcript()
        self.turn_status = _TurnStatus()


def _client(
    monkeypatch,
    application: _Application,
    *,
    client_address: str = "198.51.100.17",
    trusted_proxy_cidrs: tuple[str, ...] = (),
) -> TestClient:
    app = FastAPI()
    app.include_router(public_conversation.router)
    app.dependency_overrides[get_db] = lambda: object()
    monkeypatch.setattr(public_conversation, "_application", lambda _db: application)
    monkeypatch.setattr(
        public_conversation,
        "_runtime_application",
        lambda _db: application,
    )
    resolver = ClientNetworkResolver(trusted_proxy_cidrs)
    monkeypatch.setattr(
        public_conversation,
        "login_network_resolver",
        lambda: resolver,
        raising=False,
    )
    return TestClient(app, client=(client_address, 50000))


def test_create_returns_only_public_safe_fields_and_no_store_headers(monkeypatch):
    application = _Application()
    client = _client(monkeypatch, application)
    client.cookies.set("session", "authenticated-cookie-is-not-a-principal")
    response = client.post(
        "/run-public/public-chatbot/conversations",
        json={},
        headers={"Idempotency-Key": _key()},
    )

    assert response.status_code == 201
    conversation = response.json()["conversation"]
    assert set(conversation) == {
        "access_token",
        "lifecycle_revision",
        "memory_contract_version",
        "expires_at",
    }
    assert "session_id" not in conversation
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["etag"] == '"lifecycle-revision-1"'
    assert application.create.commands[0].network_address == "198.51.100.0/24"


def test_create_uses_canonical_client_network_from_a_trusted_proxy(monkeypatch):
    application = _Application()
    response = _client(
        monkeypatch,
        application,
        client_address="10.0.0.5",
        trusted_proxy_cidrs=("10.0.0.0/8",),
    ).post(
        "/run-public/public-chatbot/conversations",
        json={},
        headers={
            "Idempotency-Key": _key(),
            "X-Forwarded-For": "203.0.113.9",
        },
    )

    assert response.status_code == 201
    assert application.create.commands[0].network_address == "203.0.113.0/24"


def test_create_fails_closed_when_client_network_cannot_be_resolved(monkeypatch):
    application = _Application()
    response = _client(
        monkeypatch,
        application,
        client_address="testclient",
    ).post(
        "/run-public/public-chatbot/conversations",
        json={},
        headers={"Idempotency-Key": _key()},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "memory.adapter_unavailable"
    assert application.create.commands == []


def test_lifecycle_requires_exact_if_match_before_application_mutation(monkeypatch):
    application = _Application()
    response = _client(monkeypatch, application).post(
        "/run-public/public-chatbot/conversation/close",
        json={},
        headers={
            "Authorization": f"Conversation cag_v1_{secrets.token_urlsafe(32)}",
            "Idempotency-Key": _key(),
        },
    )

    assert response.status_code == 428
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.json()["detail"]["code"] == "memory.lifecycle_precondition_required"
    assert application.close.commands == []


def test_lifecycle_fingerprint_includes_if_match_revision(monkeypatch):
    application = _Application()
    client = _client(monkeypatch, application)
    access_token = f"cag_v1_{secrets.token_urlsafe(32)}"
    idempotency_key = _key()

    for revision in (1, 2):
        response = client.post(
            "/run-public/public-chatbot/conversation/close",
            json={},
            headers={
                "Authorization": f"Conversation {access_token}",
                "Idempotency-Key": idempotency_key,
                "If-Match": f'"lifecycle-revision-{revision}"',
            },
        )
        assert response.status_code == 200

    first, second = application.close.commands
    assert first.expected_lifecycle_revision == 1
    assert second.expected_lifecycle_revision == 2
    assert first.request_fingerprint != second.request_fingerprint


def test_reset_returns_old_terminal_revision_separately_from_the_new_etag(
    monkeypatch,
):
    application = _Application()
    response = _client(monkeypatch, application).post(
        "/run-public/public-chatbot/conversation/reset",
        json={},
        headers={
            "Authorization": f"Conversation cag_v1_{secrets.token_urlsafe(32)}",
            "Idempotency-Key": _key(),
            "If-Match": '"lifecycle-revision-1"',
        },
    )

    assert response.status_code == 201
    assert response.headers["etag"] == '"lifecycle-revision-1"'
    assert response.json()["previous"] == {
        "lifecycle": "closed",
        "lifecycle_revision": 2,
    }


def test_transcript_uses_the_documented_public_response_shape(monkeypatch):
    application = _Application()
    access_token = f"cag_v1_{secrets.token_urlsafe(32)}"

    response = _client(monkeypatch, application).get(
        "/run-public/public-chatbot/conversation/transcript",
        headers={"Authorization": f"Conversation {access_token}"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "conversation": {
            "state": "closed",
            "lifecycle_revision": 2,
            "content_revision": 3,
            "expires_at": "2026-07-25T00:00:00+00:00",
        },
        "turns": [],
        "next_cursor": None,
    }
    assert response.headers["etag"] == '"lifecycle-revision-2"'
    assert application.transcript.queries[0]["access_token"] == access_token


def test_non_conversation_authorization_uses_typed_resource_hidden_contract(
    monkeypatch,
):
    application = _Application()
    client = _client(monkeypatch, application)
    client.cookies.set("session", "authenticated-cookie-is-not-a-principal")
    response = client.post(
        "/run-public/public-chatbot/conversation/close",
        json={},
        headers={
            "Authorization": "Bearer authenticated-token",
            "Idempotency-Key": _key(),
            "If-Match": '"lifecycle-revision-1"',
        },
    )

    assert response.status_code == 404
    assert response.json() == {
        "detail": {
            "code": "memory.session_hidden",
            "message": "Conversation not found",
        }
    }
    assert application.close.commands == []


class _StartPublicTurn:
    def __init__(self) -> None:
        self.commands = []

    def execute(self, command):
        self.commands.append(command)
        return StartPublicConversationTurnResult(
            session_id=uuid.uuid4(),
            turn_id=uuid.uuid4(),
            dispatch_id=uuid.uuid4(),
            turn_sequence=2,
            turn_version=1,
            lifecycle_revision=3,
            turn_state=TurnStatus.PENDING_DISPATCH,
            replayed=False,
        )


def test_public_run_accepts_the_versioned_conversation_envelope(monkeypatch):
    start_turn = _StartPublicTurn()
    app = FastAPI()
    app.include_router(run.router, prefix="/api/v1")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["https://parent.example"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(PublicConversationCorsBoundaryMiddleware)
    app.dependency_overrides[get_db] = lambda: object()
    monkeypatch.setattr(run, "_start_public_conversation_turn", start_turn.execute)
    monkeypatch.setattr(run, "_network_address", lambda _request: "198.51.100.0/24")

    response = TestClient(app).post(
        "/api/v1/run-public/public-chatbot",
        json={
            "inputs": {"question": "Where is the handbook?"},
            "conversation": {"expected_lifecycle_revision": 3},
        },
        headers={
            "Authorization": f"Conversation cag_v1_{secrets.token_urlsafe(32)}",
            "Idempotency-Key": _key(),
            "Origin": "https://parent.example",
        },
    )

    assert response.status_code == 202
    assert response.json()["status"] == "accepted"
    assert response.json()["conversation"]["turn_state"] == "pending_dispatch"
    assert response.json()["conversation"]["turn_sequence"] == 2
    assert response.headers["etag"] == '"lifecycle-revision-3"'
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "access-control-allow-origin" not in response.headers
    assert "access-control-allow-credentials" not in response.headers
    assert "origin" not in response.headers.get("vary", "").lower()
    command = start_turn.commands[0]
    assert command.inputs == {"question": "Where is the handbook?"}
    assert command.expected_lifecycle_revision == 3


def test_public_run_rejects_an_incomplete_conversation_envelope(monkeypatch):
    start_turn = _StartPublicTurn()
    app = FastAPI()
    app.include_router(run.router, prefix="/api/v1")
    app.add_middleware(PublicConversationCorsBoundaryMiddleware)
    app.dependency_overrides[get_db] = lambda: object()
    response = TestClient(app).post(
        "/api/v1/run-public/public-chatbot",
        json={"inputs": {"question": "hello"}, "conversation": {}},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "memory.input_mapping_invalid"
    assert start_turn.commands == []


def test_memory_enabled_public_run_without_conversation_fails_closed(monkeypatch):
    app = FastAPI()
    app.include_router(run.router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: object()
    monkeypatch.setattr(
        run, "_public_conversation_runtime_required", lambda _slug: True
    )

    async def legacy_run(**_kwargs):
        raise AssertionError("Memory-enabled deployment must not use legacy execution")

    monkeypatch.setattr(run.DeploymentService, "run_deployment", legacy_run)

    response = TestClient(app).post(
        "/api/v1/run-public/public-chatbot",
        json={"inputs": {"question": "hello"}},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "memory.conversation_required"


def test_memory_off_public_run_uses_legacy_execution(monkeypatch):
    app = FastAPI()
    app.include_router(run.router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: object()
    monkeypatch.setattr(
        run, "_public_conversation_runtime_required", lambda _slug: False
    )
    calls = []

    async def legacy_run(**kwargs):
        calls.append(kwargs)
        return {"status": "success"}

    monkeypatch.setattr(run.DeploymentService, "run_deployment", legacy_run)

    response = TestClient(app).post(
        "/api/v1/run-public/public-chatbot",
        json={"inputs": {"question": "hello"}},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "success"}
    assert calls[0]["trigger_mode"] == "app"


def test_public_run_offloads_memory_application_boundary(monkeypatch):
    start_turn = _StartPublicTurn()
    app = FastAPI()
    app.include_router(run.router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: object()
    caller_thread = []
    worker_thread = []

    def start_in_worker(command):
        worker_thread.append(threading.get_ident())
        return start_turn.execute(command)

    monkeypatch.setattr(run, "_start_public_conversation_turn", start_in_worker)
    monkeypatch.setattr(run, "_network_address", lambda _request: "198.51.100.0/24")

    async def record_caller(request, call_next):
        caller_thread.append(threading.get_ident())
        return await call_next(request)

    app.middleware("http")(record_caller)
    response = TestClient(app).post(
        "/api/v1/run-public/public-chatbot",
        json={
            "inputs": {"question": "hello"},
            "conversation": {"expected_lifecycle_revision": 3},
        },
        headers={
            "Authorization": f"Conversation cag_v1_{secrets.token_urlsafe(32)}",
            "Idempotency-Key": _key(),
        },
    )

    assert response.status_code == 202
    assert worker_thread[0] != caller_thread[0]


def test_public_turn_status_returns_only_approved_display_projection(monkeypatch):
    application = _Application()
    client = _client(monkeypatch, application)
    turn_id = uuid.uuid4()

    response = client.get(
        f"/run-public/public-chatbot/conversation/turns/{turn_id}",
        headers={"Authorization": "Conversation valid-capability"},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["etag"] == '"lifecycle-revision-7"'
    assert response.json() == {
        "turn": {
            "id": str(turn_id),
            "sequence": 2,
            "status": "completed",
            "display": "Approved redacted answer",
            "failure_reason": None,
        }
    }
    assert application.turn_status.queries == [
        {
            "url_slug": "public-chatbot",
            "turn_id": turn_id,
            "access_token": "valid-capability",
            "now": application.turn_status.queries[0]["now"],
        }
    ]


@pytest.mark.parametrize(
    "authorization",
    [None, "Bearer authenticated-token", "Conversation invalid-capability"],
)
def test_public_turn_status_hides_missing_or_invalid_bearer(monkeypatch, authorization):
    application = _Application()
    client = _client(monkeypatch, application)
    headers = {} if authorization is None else {"Authorization": authorization}

    response = client.get(
        f"/run-public/public-chatbot/conversation/turns/{uuid.uuid4()}",
        headers=headers,
    )

    assert response.status_code == 404
    assert response.json() == {
        "detail": {
            "code": "memory.session_hidden",
            "message": "Conversation not found",
        }
    }


def test_transcript_serializes_typed_turns_and_forwards_the_opaque_cursor(monkeypatch):
    application = _Application()
    access_token = f"cag_v1_{secrets.token_urlsafe(32)}"

    class _DetailedTranscript:
        def __init__(self):
            self.queries = []

        def execute(self, **query):
            self.queries.append(query)
            return SimpleNamespace(
                lifecycle=SessionLifecycle.CLOSED,
                lifecycle_revision=2,
                content_revision=3,
                expires_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
                turns=(
                    SimpleNamespace(
                        turn_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
                        sequence=1,
                        state=TurnStatus.COMPLETED,
                        user_content="Approved user question",
                        assistant_content="Approved redacted answer",
                        created_at=datetime(2026, 7, 24, tzinfo=timezone.utc),
                        safe_failure_reason=None,
                    ),
                ),
                next_cursor="opaque-next-page",
            )

    application.transcript = _DetailedTranscript()
    response = _client(monkeypatch, application).get(
        "/run-public/public-chatbot/conversation/transcript?cursor=opaque-current-page",
        headers={"Authorization": f"Conversation {access_token}"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "conversation": {
            "state": "closed",
            "lifecycle_revision": 2,
            "content_revision": 3,
            "expires_at": "2026-07-25T00:00:00+00:00",
        },
        "turns": [
            {
                "turn_id": "11111111-1111-1111-1111-111111111111",
                "sequence": 1,
                "state": "completed",
                "user": {"content": "Approved user question"},
                "assistant": {"content": "Approved redacted answer"},
                "created_at": "2026-07-24T00:00:00+00:00",
                "safe_failure_reason": None,
            }
        ],
        "next_cursor": "opaque-next-page",
    }
    assert application.transcript.queries == [
        {
            "url_slug": "public-chatbot",
            "access_token": access_token,
            "cursor": "opaque-current-page",
            "now": application.transcript.queries[0]["now"],
        }
    ]


@pytest.mark.parametrize(
    ("error", "status_code", "code"),
    [
        (
            PublicConversationTurnLimitExceededError(),
            409,
            "memory.turn_limit_exceeded",
        ),
    ],
)
def test_public_runtime_policy_errors_keep_typed_safe_status_codes(
    error,
    status_code,
    code,
):
    mapped = public_conversation._map_public_error(error)

    assert mapped.status_code == status_code
    assert mapped.detail["code"] == code
    assert mapped.headers == {
        "Cache-Control": "no-store",
        "Referrer-Policy": "no-referrer",
    }
