import uuid
from types import SimpleNamespace

from fastapi.testclient import TestClient

from apps.gateway.api.v1.endpoints import knowledge as knowledge_endpoint
from apps.gateway.auth.dependencies import get_current_user
from apps.gateway.main import app
from apps.shared.schemas.knowledge import (
    KnowledgeCandidate,
    KnowledgeCandidateResolution,
    KnowledgePermissionDecision,
    KnowledgeRAGRecommendation,
    KnowledgeRAGRecommendationProvenance,
    KnowledgeRAGRecommendationResponse,
    KnowledgeRAGRecommendationSummary,
    KnowledgeRAGRecommendedOptions,
)


def test_knowledge_candidate_resolve_route_returns_safe_response(monkeypatch):
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    intended_subject_id = uuid.uuid4()
    kb_id = uuid.uuid4()
    captured = {}

    monkeypatch.setattr(
        knowledge_endpoint,
        "resolve_active_organization_id",
        lambda db, request, raw, current_user_id: organization_id,
    )

    class FakeResolver:
        def __init__(
            self,
            db,
            *,
            user_id,
            organization_id,
            runtime_permission_helper=None,
        ):
            captured["user_id"] = user_id
            captured["organization_id"] = organization_id
            captured["runtime_helper_present"] = runtime_permission_helper is not None

        def resolve_explicit_kbs(self, knowledge_base_ids):
            captured["knowledge_base_ids"] = knowledge_base_ids
            return KnowledgeCandidateResolution(
                candidates=[
                    KnowledgeCandidate(
                        candidate_id=kb_id,
                        candidate_type="knowledge_base",
                        permission=KnowledgePermissionDecision(
                            allowed=True,
                            reason_code="allowed",
                            external_reason_code="allowed",
                        ),
                        runtime_availability="unavailable",
                        safe_label=None,
                        safe_metadata={"runtime_reason_code": "permission.denied"},
                    )
                ],
                hidden_candidate_count_bucket="2-10",
                unavailable_candidate_count_bucket="1",
            )

    monkeypatch.setattr(knowledge_endpoint, "KnowledgeCandidateResolver", FakeResolver)
    app.dependency_overrides[knowledge_endpoint.get_db] = lambda: object()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)
    try:
        response = TestClient(app).post(
            "/api/v1/knowledge/candidates/resolve",
            json={
                "mode": "explicit_kb",
                "knowledge_base_ids": [str(kb_id)],
                "intended_execution_subject_id": str(intended_subject_id),
            },
            headers={"X-Organization-Id": str(organization_id)},
        )
    finally:
        app.dependency_overrides = {}

    assert response.status_code == 200
    body = response.json()
    assert captured["user_id"] == user_id
    assert captured["organization_id"] == organization_id
    assert captured["runtime_helper_present"] is True
    assert body["candidates"][0]["candidate_id"] == str(kb_id)
    assert body["candidates"][0]["runtime_availability"] == "unavailable"
    assert body["hidden_candidate_count_bucket"] == "2-10"
    assert "raw_source_url" not in str(body)
    assert "exact_denied_count" not in str(body)


def test_knowledge_candidate_resolve_rejects_over_cap_request():
    app.dependency_overrides[knowledge_endpoint.get_db] = lambda: object()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uuid.uuid4())
    try:
        response = TestClient(app).post(
            "/api/v1/knowledge/candidates/resolve",
            json={
                "mode": "auto_collection",
                "max_candidate_kbs": 5001,
            },
            headers={"X-Organization-Id": str(uuid.uuid4())},
        )
    finally:
        app.dependency_overrides = {}

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation.failed"


def test_knowledge_rag_recommendation_route_uses_server_context(monkeypatch):
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    kb_id = uuid.uuid4()
    captured = {}

    monkeypatch.setattr(
        knowledge_endpoint,
        "resolve_active_organization_id",
        lambda db, request, raw, current_user_id: organization_id,
    )

    class FakeRecommendationService:
        def __init__(self, db, *, user_id, organization_id):
            captured["user_id"] = user_id
            captured["organization_id"] = organization_id

        def recommend_for_builder(self, recommendation_request):
            captured["workflow_intent"] = recommendation_request.workflow_intent
            captured["has_body_actor"] = hasattr(recommendation_request, "actor_user_id")
            captured["model_fields_set"] = set(recommendation_request.model_fields_set)
            captured["knowledge_base_ids"] = recommendation_request.knowledge_base_ids
            captured["collection_ids"] = recommendation_request.collection_ids
            captured["intended_execution_subject_id"] = (
                recommendation_request.intended_execution_subject_id
            )
            return KnowledgeRAGRecommendationResponse(
                recommendations=[
                    KnowledgeRAGRecommendation(
                        recommendation_id="rec-safe-handle-1",
                        recommendation_mode="auto_collection",
                        candidate_id="rec-safe-handle-1",
                        candidate_handle="rec-safe-handle-1",
                        safe_label=None,
                        confidence="medium",
                        score=0.4,
                        safe_reason_code="safe_candidate_available",
                        recommended_options=KnowledgeRAGRecommendedOptions(),
                        materialized_knowledge_bases=[
                            {"id": kb_id, "name": "Knowledge Base"}
                        ],
                        provenance=KnowledgeRAGRecommendationProvenance(
                            safe_reason_code="safe_candidate_available",
                        ),
                        runtime_availability="unknown",
                        warnings=["runtime_availability_unknown"],
                    )
                ],
                summary=KnowledgeRAGRecommendationSummary(
                    candidate_count_bucket="1",
                    recommendation_count_bucket="1",
                ),
            )

    monkeypatch.setattr(
        knowledge_endpoint,
        "KnowledgeRAGRecommendationService",
        FakeRecommendationService,
    )
    app.dependency_overrides[knowledge_endpoint.get_db] = lambda: object()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)
    try:
        response = TestClient(app).post(
            "/api/v1/knowledge/rag-recommendations",
            json={
                "workflow_intent": "휴가\x00 규정 답변",
                "node_purpose": "HR policy",
                "mode": "auto",
                "knowledge_base_ids": [str(uuid.uuid4())],
                "collection_ids": [str(uuid.uuid4())],
                "actor_user_id": str(uuid.uuid4()),
                "organization_id": str(uuid.uuid4()),
            },
            headers={"X-Organization-Id": str(organization_id)},
        )
    finally:
        app.dependency_overrides = {}

    assert response.status_code == 200
    body = response.json()
    assert captured["user_id"] == user_id
    assert captured["organization_id"] == organization_id
    assert captured["workflow_intent"] == "휴가 규정 답변"
    assert captured["has_body_actor"] is False
    assert captured["intended_execution_subject_id"] == user_id
    assert "knowledge_base_ids" not in captured["model_fields_set"]
    assert "collection_ids" not in captured["model_fields_set"]
    assert captured["knowledge_base_ids"] == []
    assert captured["collection_ids"] == []
    assert body["recommendations"][0]["candidate_type"] == "knowledge_base"
    assert body["recommendations"][0]["candidate_id"] == "rec-safe-handle-1"
    assert str(kb_id) not in body["recommendations"][0]["recommendation_id"]
    assert body["recommendations"][0]["materialized_knowledge_bases"] == []
    assert body["recommendations"][0]["safe_label"] is None
    assert "raw_source_url" not in str(body)


def test_knowledge_rag_recommendation_route_drops_unsafe_public_refs(monkeypatch):
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    captured = {}

    monkeypatch.setattr(
        knowledge_endpoint,
        "resolve_active_organization_id",
        lambda db, request, raw, current_user_id: organization_id,
    )

    class FakeRecommendationService:
        def __init__(self, db, *, user_id, organization_id):
            pass

        def recommend_for_builder(self, recommendation_request):
            captured["pending_resolution_ref"] = (
                recommendation_request.pending_resolution_ref
            )
            captured["knowledge_requirement"] = (
                recommendation_request.knowledge_requirement
            )
            return KnowledgeRAGRecommendationResponse(
                status="unavailable",
                recommendations=[],
                resolution_id=recommendation_request.pending_resolution_ref,
                requirement_id=(
                    recommendation_request.knowledge_requirement or {}
                ).get("requirement_id"),
                fallback_reason="adapter_unavailable",
            )

    monkeypatch.setattr(
        knowledge_endpoint,
        "KnowledgeRAGRecommendationService",
        FakeRecommendationService,
    )
    app.dependency_overrides[knowledge_endpoint.get_db] = lambda: object()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)
    try:
        response = TestClient(app).post(
            "/api/v1/knowledge/rag-recommendations",
            json={
                "workflow_intent": "휴가 정책",
                "pending_resolution_ref": "https://internal.example/raw/path",
                "knowledge_requirement": {
                    "requirement_id": "secret-token-123",
                    "topic": "휴가",
                },
            },
            headers={"X-Organization-Id": str(organization_id)},
        )
    finally:
        app.dependency_overrides = {}

    assert response.status_code == 200
    body_text = response.text
    assert captured["pending_resolution_ref"] is None
    assert captured["knowledge_requirement"]["requirement_id"] is None
    assert "internal.example" not in body_text
    assert "secret-token-123" not in body_text


def test_knowledge_rag_recommendation_route_rejects_public_explicit_kb_mode(monkeypatch):
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    kb_id = uuid.uuid4()

    monkeypatch.setattr(
        knowledge_endpoint,
        "resolve_active_organization_id",
        lambda db, request, raw, current_user_id: organization_id,
    )
    app.dependency_overrides[knowledge_endpoint.get_db] = lambda: object()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)
    try:
        response = TestClient(app).post(
            "/api/v1/knowledge/rag-recommendations",
            json={
                "workflow_intent": "휴가 정책",
                "mode": "explicit_kb",
                "knowledge_base_ids": [str(kb_id)],
            },
            headers={"X-Organization-Id": str(organization_id)},
        )
    finally:
        app.dependency_overrides = {}

    assert response.status_code == 422
    assert response.json()["error"]["code"] == (
        "knowledge.rag_recommendations.explicit_ids_not_allowed"
    )


def test_knowledge_rag_recommendation_validation_does_not_echo_raw_input():
    secret_marker = "SECRET_INTENT_SHOULD_NOT_ECHO"
    app.dependency_overrides[knowledge_endpoint.get_db] = lambda: object()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uuid.uuid4())
    try:
        response = TestClient(app).post(
            "/api/v1/knowledge/rag-recommendations",
            json={
                "workflow_intent": secret_marker + ("x" * 4000),
            },
            headers={"X-Organization-Id": str(uuid.uuid4())},
        )
    finally:
        app.dependency_overrides = {}

    assert response.status_code == 422
    body_text = response.text
    assert response.json()["error"]["code"] == "validation.failed"
    assert secret_marker not in body_text
