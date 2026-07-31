import asyncio
import json
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from apps.gateway.api.v1.endpoints import rag
from apps.shared import pubsub
from apps.shared.db.models.knowledge import KnowledgeBase
from apps.shared.schemas.rag import (
    RAGAgentAnswerRequest,
    RAGAgentAnswerResponse,
    RAGCitation,
    RAGResponse,
    RAGRetrievalSummary,
    RAGUsageSummary,
    SearchQuery,
)


class FakeQuery:
    def __init__(self, result):
        self.result = result

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self.result


class FakeDb:
    def __init__(self, query_result):
        self.query_result = query_result

    def query(self, model):
        return FakeQuery(self.query_result)


def _request() -> Request:
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    request.state.request_id = "test-request-id"
    return request


def test_confirm_reuses_active_resume_after_document_status_changes(monkeypatch):
    organization_id = uuid.uuid4()
    knowledge_base_id = uuid.uuid4()
    document_id = uuid.uuid4()
    job_id = uuid.uuid4()
    captured = {}

    monkeypatch.setattr(
        rag,
        "parse_organization_id",
        lambda *_args, **_kwargs: organization_id,
    )
    monkeypatch.setattr(
        rag,
        "_authorize_knowledge_document_action",
        lambda *_args, **_kwargs: (
            SimpleNamespace(
                id=knowledge_base_id,
                embedding_model="text-embedding-3-small",
            ),
            SimpleNamespace(id=document_id, status="indexing"),
        ),
    )
    monkeypatch.setattr(
        rag,
        "_ensure_document_ingestion_schema_ready",
        lambda *_args, **_kwargs: None,
    )

    def execute(command):
        captured["command"] = command
        return SimpleNamespace(
            job=SimpleNamespace(job_id=job_id),
            reused=True,
            dispatch_deferred=False,
        )

    monkeypatch.setattr(
        rag,
        "build_request_document_ingestion",
        lambda _db: SimpleNamespace(execute=execute),
    )

    response = asyncio.run(
        rag.confirm_document_parsing(
            document_id=document_id,
            request=_request(),
            strategy="general",
            x_organization_id=str(organization_id),
            db=object(),
            current_user=SimpleNamespace(id=uuid.uuid4()),
        )
    )

    assert response["job_id"] == str(job_id)
    assert response["reused"] is True
    assert captured["command"].required_document_status == "waiting_for_approval"


def test_search_requires_knowledge_base_id():
    with pytest.raises(HTTPException) as exc:
        rag._require_search_knowledge_base_id(_request(), SearchQuery(query="policy"))

    assert exc.value.status_code == 400
    assert exc.value.detail["error"]["code"] == "validation.failed"


def test_authorize_rag_use_hides_scope_mismatch(monkeypatch):
    organization_id = uuid.uuid4()
    kb = KnowledgeBase(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        name="KB",
        user_id=uuid.uuid4(),
    )
    monkeypatch.setattr(rag, "has_organization_scope_access", lambda *args: True)

    with pytest.raises(HTTPException) as exc:
        rag._authorize_rag_use(
            _request(),
            FakeDb(kb),
            SimpleNamespace(id=uuid.uuid4()),
            organization_id,
            kb.id,
        )

    assert exc.value.status_code == 404
    assert exc.value.detail["error"]["code"] == "resource.not_found"


def test_authorize_rag_use_requires_use_permission(monkeypatch):
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    kb = KnowledgeBase(
        id=uuid.uuid4(),
        organization_id=organization_id,
        name="KB",
        user_id=uuid.uuid4(),
    )
    audit_calls = []
    monkeypatch.setattr(rag, "has_organization_scope_access", lambda *args: True)

    class FakeKnowledgePermissionHelper:
        def __init__(self, *args, **kwargs):
            pass

        def evaluate_kb_use(self, kb):
            return SimpleNamespace(
                allowed=False,
                external_reason_code="permission.denied",
                effective_auth_state="viewer",
                reason_code="kb_use_denied",
            )

    monkeypatch.setattr(rag, "KnowledgePermissionHelper", FakeKnowledgePermissionHelper)
    monkeypatch.setattr(
        rag,
        "record_resource_permission_denied",
        lambda **kwargs: audit_calls.append(kwargs),
    )

    with pytest.raises(HTTPException) as exc:
        rag._authorize_rag_use(
            _request(),
            FakeDb(kb),
            SimpleNamespace(id=user_id),
            organization_id,
            kb.id,
        )

    assert exc.value.status_code == 403
    assert exc.value.detail["error"]["code"] == "permission.denied"
    assert getattr(exc.value, "audit_recorded") is True
    assert audit_calls == [
        {
            "user_id": user_id,
            "resource_type": "knowledge_base",
            "resource_id": kb.id,
            "action": "use",
            "effective_auth_state": "viewer",
            "organization_id": organization_id,
            "metadata": {
                "request_id": "test-request-id",
                "path": "/",
                "reason_code": "kb_use_denied",
            },
        }
    ]


def test_authorize_rag_use_hides_source_acl_denial(monkeypatch):
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    kb = KnowledgeBase(
        id=uuid.uuid4(),
        organization_id=organization_id,
        name="Source managed KB",
        user_id=uuid.uuid4(),
    )
    monkeypatch.setattr(rag, "has_organization_scope_access", lambda *args: True)

    class FakeKnowledgePermissionHelper:
        def __init__(self, *args, **kwargs):
            pass

        def evaluate_kb_use(self, kb):
            return SimpleNamespace(
                allowed=False,
                external_reason_code="resource.hidden",
                effective_auth_state="operator",
                reason_code="source_authorization.denied",
            )

    monkeypatch.setattr(rag, "KnowledgePermissionHelper", FakeKnowledgePermissionHelper)
    monkeypatch.setattr(
        rag,
        "record_resource_permission_denied",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("hidden source ACL denial must not emit permission audit")
        ),
    )

    with pytest.raises(HTTPException) as exc:
        rag._authorize_rag_use(
            _request(),
            FakeDb(kb),
            SimpleNamespace(id=user_id),
            organization_id,
            kb.id,
        )

    assert exc.value.status_code == 404
    assert exc.value.detail["error"]["code"] == "resource.hidden"


def test_document_progress_authorizes_read_before_opening_stream(monkeypatch):
    document_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    user = SimpleNamespace(id=uuid.uuid4())
    db = object()
    request = _request()
    captured = {}

    def authorize(
        request_arg,
        db_arg,
        user_arg,
        organization_id_arg,
        document_id_arg,
        action,
    ):
        captured["authorization"] = (
            request_arg,
            db_arg,
            user_arg,
            organization_id_arg,
            document_id_arg,
            action,
        )

    monkeypatch.setattr(rag, "_authorize_knowledge_document_action", authorize)
    monkeypatch.setattr(
        rag,
        "_ensure_document_ingestion_schema_ready",
        lambda *_args, **_kwargs: None,
    )

    response = asyncio.run(
        rag.get_document_progress(
            document_id,
            request,
            organization_id,
            db=db,
            current_user=user,
        )
    )

    assert response.media_type == "text/event-stream"
    assert response.headers["cache-control"] == "no-cache, no-store"
    assert response.headers["x-accel-buffering"] == "no"
    assert captured["authorization"] == (
        request,
        db,
        user,
        organization_id,
        document_id,
        "read",
    )


def test_document_progress_denial_prevents_stream_creation(monkeypatch):
    denial = HTTPException(status_code=404, detail={"reason_code": "resource.hidden"})

    def deny(*_args, **_kwargs):
        raise denial

    monkeypatch.setattr(rag, "_authorize_knowledge_document_action", deny)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            rag.get_document_progress(
                uuid.uuid4(),
                _request(),
                uuid.uuid4(),
                db=object(),
                current_user=SimpleNamespace(id=uuid.uuid4()),
            )
        )

    assert exc.value is denial


def test_document_progress_maps_unready_ingestion_schema_after_authorization(
    monkeypatch,
):
    events = []

    monkeypatch.setattr(
        rag,
        "_authorize_knowledge_document_action",
        lambda *_args, **_kwargs: events.append("authorized"),
    )

    def reject_schema(_db, _request):
        events.append("schema")
        rag.raise_api_error(
            _request,
            503,
            "knowledge.ingestion_schema_not_ready",
            "Knowledge document ingestion is temporarily unavailable.",
            {"reason": "knowledge.ingestion_table_missing"},
        )

    monkeypatch.setattr(rag, "_ensure_document_ingestion_schema_ready", reject_schema)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            rag.get_document_progress(
                uuid.uuid4(),
                _request(),
                uuid.uuid4(),
                db=object(),
                current_user=SimpleNamespace(id=uuid.uuid4()),
            )
        )

    assert events == ["authorized", "schema"]
    assert exc_info.value.status_code == 503
    assert (
        exc_info.value.detail["error"]["code"]
        == "knowledge.ingestion_schema_not_ready"
    )


def test_document_progress_uses_fixed_messages_and_sleeps_after_bad_redis_value(
    monkeypatch,
):
    document_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    sleep_calls = []
    documents = iter(
        [
            SimpleNamespace(
                id=document_id,
                status="processing",
                error_message="legacy-internal-exception-marker",
                meta_info={"processing_current_step": "legacy-step-marker"},
            ),
            SimpleNamespace(
                id=document_id,
                status="failed",
                error_message="legacy-internal-exception-marker",
                meta_info={"processing_current_step": "legacy-step-marker"},
            ),
        ]
    )

    class ProgressQuery:
        def get(self, _document_id):
            return next(documents)

    class ProgressDb:
        def expire_all(self):
            pass

        def query(self, model):
            assert model is rag.Document
            return ProgressQuery()

    class ReadStatus:
        def execute(self, _document_id):
            return None

    class InvalidProgressRedis:
        def get(self, _key):
            return b"not-a-number"

    async def fake_sleep(delay):
        sleep_calls.append(delay)

    monkeypatch.setattr(
        rag,
        "_authorize_knowledge_document_action",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        rag,
        "_ensure_document_ingestion_schema_ready",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        rag,
        "build_read_document_ingestion_status",
        lambda _db: ReadStatus(),
    )
    monkeypatch.setattr(
        rag,
        "finalize_stale_processing_start",
        lambda *args, **kwargs: False,
    )
    monkeypatch.setattr(
        rag,
        "recover_timed_out_document_with_artifacts",
        lambda *args, **kwargs: False,
    )
    monkeypatch.setattr(
        pubsub,
        "get_redis_client",
        lambda: InvalidProgressRedis(),
    )
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    async def consume_events():
        response = await rag.get_document_progress(
            document_id,
            _request(),
            organization_id,
            db=ProgressDb(),
            current_user=SimpleNamespace(id=uuid.uuid4()),
        )
        iterator = response.body_iterator
        first = await anext(iterator)
        second = await anext(iterator)
        with pytest.raises(StopAsyncIteration):
            await anext(iterator)
        return first, second

    first_event, second_event = asyncio.run(consume_events())
    first_payload = json.loads(first_event.removeprefix("data: ").strip())
    second_payload = json.loads(second_event.removeprefix("data: ").strip())

    assert first_payload == {
        "progress": 0,
        "message": "Document processing is in progress.",
        "status": "processing",
        "error": None,
        "ingestion_job": None,
    }
    assert second_payload == {
        "progress": 0,
        "message": "Document processing failed. You can retry the document.",
        "status": "failed",
        "error": "Document processing failed. You can retry the document.",
        "ingestion_job": None,
    }
    serialized = first_event + second_event
    assert "legacy-internal-exception-marker" not in serialized
    assert "legacy-step-marker" not in serialized
    assert sleep_calls == [1]


def test_record_rag_retrieve_audit_marks_policy_as_not_evaluated(monkeypatch):
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    knowledge_base_id = uuid.uuid4()
    audit_calls = []
    monkeypatch.setattr(
        rag,
        "record_audit",
        lambda **kwargs: audit_calls.append(kwargs),
    )

    rag._record_rag_retrieve_audit(
        _request(),
        SimpleNamespace(id=user_id),
        organization_id,
        knowledge_base_id,
        metadata_filter=None,
        result_count=3,
        mode="auto",
    )

    assert audit_calls[0]["action"] == rag.AuditAction.RAG_RETRIEVE
    metadata = audit_calls[0]["metadata"]
    assert metadata["organization_id"] == str(organization_id)
    assert metadata["result_count"] == 3
    assert metadata["policy_evaluated"] is False
    assert "policy_result" not in metadata


def test_search_test_chat_passes_top_k_and_organization_id(monkeypatch):
    captured = {}
    organization_id = uuid.uuid4()
    knowledge_base_id = uuid.uuid4()

    class FakeRetrievalService:
        def __init__(self, db, user_id, organization_id=None):
            captured["init"] = {
                "db": db,
                "user_id": user_id,
                "organization_id": organization_id,
            }

        async def generate_answer_for_test(self, *args, **kwargs):
            captured.update(kwargs)
            return RAGResponse(answer="ok", references=[])

    monkeypatch.setattr(rag, "RetrievalService", FakeRetrievalService)
    monkeypatch.setattr(rag, "_authorize_rag_use", lambda *args, **kwargs: None)
    monkeypatch.setattr(rag, "_record_rag_retrieve_audit", lambda *args, **kwargs: None)

    asyncio.run(
        rag.search_test_chat(
            SearchQuery(
                query="policy",
                top_k=7,
                knowledge_base_id=knowledge_base_id,
            ),
            _request(),
            str(organization_id),
            db=object(),
            current_user=SimpleNamespace(id=uuid.uuid4()),
        ),
    )

    assert captured["top_k"] == 7
    assert captured["init"]["organization_id"] == organization_id


def test_get_or_create_knowledge_base_uses_active_organization(monkeypatch):
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    captured = {}
    kb_id = uuid.uuid4()

    class FakeCreateService:
        def __init__(self, db):
            captured["db"] = db

        def create(self, payload, **kwargs):
            captured["payload"] = payload
            captured["kwargs"] = kwargs
            return SimpleNamespace(
                id=kb_id,
                embedding_model="text-embedding-3-small",
            )

    monkeypatch.setattr(rag, "has_organization_scope_access", lambda *args: True)
    monkeypatch.setattr(rag, "KnowledgeBaseQueryService", FakeCreateService)
    fake_db = object()

    created_kb_id, model = rag._get_or_create_knowledge_base(
        _request(),
        fake_db,
        SimpleNamespace(id=user_id),
        organization_id,
        None,
        "KB",
        "desc",
        "text-embedding-3-small",
        5,
        0.7,
        None,
    )

    assert created_kb_id == kb_id
    assert model == "text-embedding-3-small"
    assert captured["db"] is fake_db
    assert captured["payload"].name == "KB"
    assert captured["payload"].description == "desc"
    assert captured["kwargs"] == {
        "user_id": user_id,
        "organization_id": organization_id,
        "top_k": 5,
        "similarity_threshold": 0.7,
    }


def _agent_answer_payload() -> RAGAgentAnswerRequest:
    return RAGAgentAnswerRequest(
        knowledge_base_id=uuid.uuid4(),
        query="policy",
        generation_model_id=uuid.uuid4(),
        credential_id=uuid.uuid4(),
    )


def _agent_answer_response() -> RAGAgentAnswerResponse:
    knowledge_base_id = uuid.uuid4()
    document_id = uuid.uuid4()
    chunk_id = uuid.uuid4()
    return RAGAgentAnswerResponse(
        answer_run_id=uuid.uuid4(),
        correlation_id="corr-1",
        status="completed",
        answer="answer",
        citations=[
            RAGCitation(
                citation_id="c1",
                document_id=document_id,
                chunk_id=chunk_id,
                rank=1,
                score=0.9,
                filename="policy.md",
                metadata_summary={"classification": "internal"},
                content_preview="safe preview",
            )
        ],
        retrieval_summary=RAGRetrievalSummary(
            knowledge_base_id=knowledge_base_id,
            hierarchy_mode="auto",
            retrieved_chunk_count=1,
            document_ids=[document_id],
            citation_ids=["c1"],
            raw_content_returned=False,
        ),
        usage_summary=RAGUsageSummary(
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            total_cost=0.001,
            latency_ms=123,
            model_name="GPT Test",
            provider="openai",
        ),
        policy_result={"result": "allow"},
    )


def test_rag_agent_answer_passes_active_organization_to_service(monkeypatch):
    captured = {}
    organization_id = uuid.uuid4()
    expected_response = _agent_answer_response()

    class FakeRAGAgentAnswerService:
        def __init__(self, db, current_user, request, organization_id):
            captured["init"] = {
                "db": db,
                "current_user": current_user,
                "request": request,
                "organization_id": organization_id,
            }

        async def answer(self, payload):
            captured["payload"] = payload
            return expected_response

    monkeypatch.setattr(rag, "RAGAgentAnswerService", FakeRAGAgentAnswerService)
    payload = _agent_answer_payload()
    user = SimpleNamespace(id=uuid.uuid4())
    db = object()

    response = asyncio.run(
        rag.rag_agent_answer(
            payload,
            _request(),
            str(organization_id),
            db=db,
            current_user=user,
        )
    )

    assert response is expected_response
    assert captured["payload"] is payload
    assert captured["init"] == {
        "db": db,
        "current_user": user,
        "request": captured["init"]["request"],
        "organization_id": organization_id,
    }


def test_rag_agent_answer_stream_returns_generator_without_completing_answer(monkeypatch):
    organization_id = uuid.uuid4()
    captured = {}

    class FakeRAGAgentAnswerService:
        def __init__(self, *args, **kwargs):
            pass

        def prepare_execution(self, payload):
            captured["prepared"] = payload
            return SimpleNamespace(id="execution")

        async def stream_events(self, execution):
            captured["streamed"] = execution
            yield "retrieval.started", {
                "answer_run_id": str(uuid.uuid4()),
                "correlation_id": "corr-1",
                "status": "running",
                "knowledge_base_id": str(uuid.uuid4()),
                "hierarchy_mode": "auto",
            }

    monkeypatch.setattr(rag, "RAGAgentAnswerService", FakeRAGAgentAnswerService)

    response = asyncio.run(
        rag.rag_agent_answer_stream(
            _agent_answer_payload(),
            _request(),
            str(organization_id),
            db=object(),
            current_user=SimpleNamespace(id=uuid.uuid4()),
        )
    )

    assert response.media_type == "text/event-stream"
    assert "prepared" in captured
    assert "streamed" not in captured


def test_rag_agent_answer_sse_events_do_not_put_preview_in_summary():
    response = _agent_answer_response()

    async def source_events():
        yield "retrieval.started", {
            "answer_run_id": str(response.answer_run_id),
            "correlation_id": response.correlation_id,
            "status": "running",
            "knowledge_base_id": str(response.retrieval_summary.knowledge_base_id),
            "hierarchy_mode": response.retrieval_summary.hierarchy_mode,
        }
        yield "retrieval.completed", {
            "answer_run_id": str(response.answer_run_id),
            "correlation_id": response.correlation_id,
            "retrieval_summary": response.retrieval_summary.model_dump(mode="json"),
            "citations": [c.model_dump(mode="json") for c in response.citations],
        }
        yield "answer.delta", {
            "answer_run_id": str(response.answer_run_id),
            "correlation_id": response.correlation_id,
            "delta": response.answer,
            "index": 0,
        }
        yield "usage", {
            "answer_run_id": str(response.answer_run_id),
            "correlation_id": response.correlation_id,
            "usage_summary": response.usage_summary.model_dump(mode="json"),
        }
        yield "summary", {
            "answer_run_id": str(response.answer_run_id),
            "correlation_id": response.correlation_id,
            "status": response.status,
            "retrieval_summary": response.retrieval_summary.model_dump(mode="json"),
            "citation_summary": [
                c.model_dump(mode="json", exclude={"content_preview"})
                for c in response.citations
            ],
            "answer_summary": {
                "answer_length": len(response.answer),
                "cited_document_count": 1,
                "citation_ids": ["c1"],
                "policy_result": {"result": "allow"},
                "completion_status": "completed",
            },
            "policy_result": response.policy_result,
        }
        yield "answer.completed", {
            "answer_run_id": str(response.answer_run_id),
            "correlation_id": response.correlation_id,
            "status": response.status,
        }

    async def collect():
        return [event async for event in rag._rag_agent_sse_events(source_events())]

    events = asyncio.run(collect())

    assert [event.split("\n", 1)[0] for event in events] == [
        "event: retrieval.started",
        "event: retrieval.completed",
        "event: answer.delta",
        "event: usage",
        "event: summary",
        "event: answer.completed",
    ]
    retrieval_completed = "\n".join(events)
    assert "safe preview" in retrieval_completed

    started_data = json.loads(events[0].split("data: ", 1)[1])
    assert started_data["status"] == "running"
    assert started_data["knowledge_base_id"] == str(
        response.retrieval_summary.knowledge_base_id
    )
    assert started_data["hierarchy_mode"] == response.retrieval_summary.hierarchy_mode

    delta_data = json.loads(events[2].split("data: ", 1)[1])
    assert delta_data["index"] == 0

    summary_event = events[4]
    assert "content_preview" not in summary_event
    summary_data = json.loads(summary_event.split("data: ", 1)[1])
    assert summary_data["answer_summary"] == {
        "answer_length": len(response.answer),
        "cited_document_count": 1,
        "citation_ids": ["c1"],
        "policy_result": {"result": "allow"},
        "completion_status": "completed",
    }
