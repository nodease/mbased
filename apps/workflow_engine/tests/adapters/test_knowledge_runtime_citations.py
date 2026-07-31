import uuid
from types import SimpleNamespace

from apps.shared.db.models.knowledge import (
    KnowledgeBase,
    KnowledgeCollection,
    KnowledgeSourceIdentity,
)
from apps.shared.domain.knowledge_runtime_candidates import (
    KnowledgeRuntimeCandidate,
    KnowledgeRuntimeCandidateProvenance,
    KnowledgeRuntimeCandidateResolution,
)
from apps.shared.schemas.rag import ChunkPreview
from apps.workflow_engine.adapters.knowledge_runtime_citations import (
    GENERIC_COLLECTION_CITATION_LABEL,
    GENERIC_DIRECT_CITATION_LABEL,
    PromptEvidence,
    WorkflowCitationProjector,
)


class _Query:
    def __init__(self, rows):
        self._rows = rows
        self.criteria = []

    def filter(self, *_criteria):
        self.criteria.extend(_criteria)
        return self

    def all(self):
        return self._rows


class _Session:
    def __init__(self, rows_by_model):
        self._rows_by_model = rows_by_model
        self.queries = []

    def query(self, model):
        self.last_query = _Query(self._rows_by_model.get(model, []))
        self.queries.append(self.last_query)
        return self.last_query


def _resolution(*candidates):
    return KnowledgeRuntimeCandidateResolution(
        status="resolved",
        candidates=tuple(candidates),
        routing_mode="mixed",
        configured_direct_count_bucket="1",
        configured_collection_count_bucket="1",
        eligible_candidate_count_bucket="2-10",
        selected_candidate_count_bucket="2-10",
        policy_excluded_count_bucket="0",
        budget_limited=False,
        scan_limited=False,
        warning_codes=(),
    )


def _chunk(*, content="본문", page_number=2, metadata_summary=None):
    return ChunkPreview(
        content=content,
        document_id=uuid.uuid4(),
        filename="private/path/raw.pdf",
        page_number=page_number,
        similarity_score=0.9,
        metadata_summary=metadata_summary,
    )


def test_projector_uses_safe_labels_and_hides_collection_child_identity():
    organization_id = uuid.uuid4()
    direct_id = uuid.uuid4()
    collection_child_id = uuid.uuid4()
    collection_id = uuid.uuid4()
    direct = KnowledgeBase(
        id=direct_id,
        organization_id=organization_id,
        user_id=uuid.uuid4(),
        name="원문 파일명",
        safe_metadata={"safe_label": "휴가 정책"},
    )
    collection = KnowledgeCollection(
        id=collection_id,
        organization_id=organization_id,
        name="원문 Collection",
        safe_metadata={"safe_label": "온보딩 문서"},
    )
    resolution = _resolution(
        KnowledgeRuntimeCandidate(
            knowledge_base_id=direct_id,
            provenance=KnowledgeRuntimeCandidateProvenance(kind="direct"),
        ),
        KnowledgeRuntimeCandidate(
            knowledge_base_id=collection_child_id,
            provenance=KnowledgeRuntimeCandidateProvenance(
                kind="collection",
                collection_id=collection_id,
            ),
        ),
    )
    projector = WorkflowCitationProjector(
        db_session=_Session(
            {
                KnowledgeBase: [direct],
                KnowledgeCollection: [collection],
            }
        ),
        organization_id=organization_id,
        resolution=resolution,
    )

    envelope = projector.project(
        [
            PromptEvidence(
                knowledge_base_id=str(direct_id),
                chunk=_chunk(metadata_summary={"heading": "연차 신청"}),
                prompt_content="연차는 사전에 신청합니다.",
            ),
            PromptEvidence(
                knowledge_base_id=str(collection_child_id),
                chunk=_chunk(metadata_summary={"heading": "숨은 하위 문서"}),
                prompt_content="온보딩 절차를 따릅니다.",
            ),
        ],
        mode="detailed",
    )

    assert [item.label for item in envelope.items] == ["휴가 정책", "온보딩 문서"]
    assert envelope.items[0].section == "연차 신청"
    assert envelope.items[1].section is None
    assert "raw.pdf" not in str(envelope.model_dump(mode="json"))
    assert str(collection_child_id) not in str(envelope.model_dump(mode="json"))


def test_projector_fails_closed_for_unapproved_source_identity_and_hidden_mode():
    organization_id = uuid.uuid4()
    kb_id = uuid.uuid4()
    identity_id = uuid.uuid4()
    identity = KnowledgeSourceIdentity(
        id=identity_id,
        organization_id=organization_id,
        source_system="drive",
        source_item_ref="hashed-ref",
        hmac_key_version="v1",
        safe_display_name="노출 금지 라벨",
        display_policy_state="unreviewed",
        is_active=True,
    )
    kb = KnowledgeBase(
        id=kb_id,
        organization_id=organization_id,
        user_id=uuid.uuid4(),
        name="원문 이름",
        source_identity_id=identity_id,
        source_identity=identity,
        safe_metadata={"safe_label": "수동 우회 라벨"},
    )
    resolution = _resolution(
        KnowledgeRuntimeCandidate(
            knowledge_base_id=kb_id,
            provenance=KnowledgeRuntimeCandidateProvenance(kind="direct"),
        )
    )
    projector = WorkflowCitationProjector(
        db_session=_Session({KnowledgeBase: [kb]}),
        organization_id=organization_id,
        resolution=resolution,
    )
    evidence = [
        PromptEvidence(
            knowledge_base_id=str(kb_id),
            chunk=_chunk(),
            prompt_content="본문",
        )
    ]

    assert projector.label_for(str(kb_id)) == GENERIC_DIRECT_CITATION_LABEL
    assert projector.project(evidence, mode="hidden").items == []
    assert projector.project(evidence, mode="basic").items[0].content_preview is None


def test_projector_rechecks_lifecycle_and_source_deletion_for_labels():
    organization_id = uuid.uuid4()
    kb_id = uuid.uuid4()
    collection_id = uuid.uuid4()
    resolution = _resolution(
        KnowledgeRuntimeCandidate(
            knowledge_base_id=kb_id,
            provenance=KnowledgeRuntimeCandidateProvenance(kind="direct"),
        ),
        KnowledgeRuntimeCandidate(
            knowledge_base_id=uuid.uuid4(),
            provenance=KnowledgeRuntimeCandidateProvenance(
                kind="collection",
                collection_id=collection_id,
            ),
        ),
    )
    session = _Session({KnowledgeBase: [], KnowledgeCollection: []})

    WorkflowCitationProjector(
        db_session=session,
        organization_id=organization_id,
        resolution=resolution,
    )

    for query in session.queries:
        criteria_keys = {
            getattr(getattr(item, "left", None), "key", None)
            for item in query.criteria
        }
        assert {"organization_id", "id", "lifecycle_state", "sync_state"} <= criteria_keys


def test_projector_label_projection_failure_falls_back_without_failing_answer():
    class _BrokenSourceIdentityResource:
        def __init__(self, resource_id):
            self.id = resource_id
            self.source_identity_id = uuid.uuid4()

        @property
        def source_identity(self):
            raise RuntimeError("relationship unavailable")

    organization_id = uuid.uuid4()
    direct_id = uuid.uuid4()
    collection_child_id = uuid.uuid4()
    collection_id = uuid.uuid4()
    projector = WorkflowCitationProjector(
        db_session=_Session(
            {
                KnowledgeBase: [_BrokenSourceIdentityResource(direct_id)],
                KnowledgeCollection: [_BrokenSourceIdentityResource(collection_id)],
            }
        ),
        organization_id=organization_id,
        resolution=_resolution(
            KnowledgeRuntimeCandidate(
                knowledge_base_id=direct_id,
                provenance=KnowledgeRuntimeCandidateProvenance(kind="direct"),
            ),
            KnowledgeRuntimeCandidate(
                knowledge_base_id=collection_child_id,
                provenance=KnowledgeRuntimeCandidateProvenance(
                    kind="collection",
                    collection_id=collection_id,
                ),
            ),
        ),
    )

    envelope = projector.project(
        [
            PromptEvidence(
                knowledge_base_id=str(direct_id),
                chunk=_chunk(),
                prompt_content="직접 KB 근거",
            ),
            PromptEvidence(
                knowledge_base_id=str(collection_child_id),
                chunk=_chunk(),
                prompt_content="Collection KB 근거",
            ),
        ],
        mode="basic",
    )

    assert [item.label for item in envelope.items] == [
        GENERIC_DIRECT_CITATION_LABEL,
        GENERIC_COLLECTION_CITATION_LABEL,
    ]


def test_detailed_preview_uses_common_redaction_and_fails_closed(monkeypatch):
    organization_id = uuid.uuid4()
    kb_id = uuid.uuid4()
    kb = KnowledgeBase(
        id=kb_id,
        organization_id=organization_id,
        user_id=uuid.uuid4(),
        name="안전한 문서",
        safe_metadata={"safe_label": "안전한 문서"},
    )
    projector = WorkflowCitationProjector(
        db_session=_Session({KnowledgeBase: [kb]}),
        organization_id=organization_id,
        resolution=_resolution(
            KnowledgeRuntimeCandidate(
                knowledge_base_id=kb_id,
                provenance=KnowledgeRuntimeCandidateProvenance(kind="direct"),
            )
        ),
    )
    observed = {}

    def redact(value, policy, payload_kind):
        observed.update(
            value=value,
            raw_payload_storage_enabled=policy.raw_payload_storage_enabled,
            payload_kind=payload_kind,
        )
        return SimpleNamespace(
            failed=False,
            pii_detected=False,
            secret_detected=False,
            redacted_payload="정제된 미리보기",
        )

    monkeypatch.setattr(
        "apps.workflow_engine.adapters.knowledge_runtime_citations.TraceRedactionService.redact_payload",
        redact,
    )
    evidence = [
        PromptEvidence(
            knowledge_base_id=str(kb_id),
            chunk=_chunk(),
            prompt_content="원본 근거 본문",
        )
    ]

    envelope = projector.project(evidence, mode="detailed")

    assert envelope.items[0].content_preview == "정제된 미리보기"
    assert observed == {
        "value": "원본 근거 본문",
        "raw_payload_storage_enabled": False,
        "payload_kind": "workflow_citation_preview",
    }

    monkeypatch.setattr(
        "apps.workflow_engine.adapters.knowledge_runtime_citations.TraceRedactionService.redact_payload",
        lambda *_args, **_kwargs: SimpleNamespace(
            failed=True,
            pii_detected=False,
            secret_detected=True,
            redacted_payload=None,
        ),
    )
    assert projector.project(evidence, mode="detailed").items[0].content_preview is None

    monkeypatch.setattr(
        "apps.workflow_engine.adapters.knowledge_runtime_citations.TraceRedactionService.redact_payload",
        lambda *_args, **_kwargs: SimpleNamespace(
            failed=False,
            pii_detected=True,
            secret_detected=False,
            redacted_payload="마스킹된 미리보기",
        ),
    )
    assert projector.project(evidence, mode="detailed").items[0].content_preview is None

    monkeypatch.setattr(
        "apps.workflow_engine.adapters.knowledge_runtime_citations.TraceRedactionService.redact_payload",
        lambda *_args, **_kwargs: SimpleNamespace(
            failed=False,
            pii_detected=False,
            secret_detected=True,
            redacted_payload="마스킹된 미리보기",
        ),
    )
    assert projector.project(evidence, mode="detailed").items[0].content_preview is None
