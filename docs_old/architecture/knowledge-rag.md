# Knowledge/RAG 아키텍처

Status: Draft
Authority: Architecture
Source of Truth: Yes
Verified Against: dev @ 860ece0dee7cab3925d27f30ea650baf0cb18b4e (PR #138 docs target, 2026-07-01 KST)
Related ADRs: [ADR-202606271559-audit-log-rag-trace-storage](../decisions/ADR-202606271559-audit-log-rag-trace-storage.md), [ADR-202606290124-mvp2-classification-metadata-storage](../decisions/ADR-202606290124-mvp2-classification-metadata-storage.md), [ADR-202606290131-audit-action-naming-standard](../decisions/ADR-202606290131-audit-action-naming-standard.md), [ADR-202606301045-metadata-aware-hierarchical-rag-boundary](../decisions/ADR-202606301045-metadata-aware-hierarchical-rag-boundary.md), [ADR-202607010220-rag-answer-trace-usage-correlation-boundary](../decisions/ADR-202607010220-rag-answer-trace-usage-correlation-boundary.md)

## 목적

Knowledge/RAG 런타임에서 metadata filter, knowledge base 권한, document metadata policy, hierarchical retrieval, trace/citation 저장 경계를 정의한다.

## 용어

| 용어 | 의미 |
| --- | --- |
| Metadata-aware RAG | document/source/chunk metadata를 retrieval filter, policy decision, ranking hint, citation evidence에 사용하는 RAG |
| Permission-aware RAG | RAG 실행 경로에서 knowledge base `use` 권한과 document metadata policy를 강제하는 접근 제어 경계 |
| Hierarchical RAG | `knowledge base -> document/source -> section/parent chunk -> child chunk` 계층을 indexing/retrieval에 사용하는 검색 구조 |

RBAC 기반 접근 제어는 Hierarchical RAG가 아니다. RBAC는 누가 KB/RAG를 사용할 수 있는지 결정하고, Hierarchical RAG는 어떤 chunk 계층을 어떻게 검색할지 결정한다.

## Component View

```text
Client Search UI / Workflow Runtime
  -> RAG API / Workflow Engine
    -> KB Permission Gate
    -> Metadata Filter Normalizer
    -> Document Metadata Policy Evaluator
    -> Retrieval Service
      -> Vector Search
      -> Keyword Search
      -> RRF/Rerank
      -> Hierarchical Parent/Child Expansion
    -> Trace/Citation Writer
  -> Trace/Run Detail API
```

## 레이어 경계

Controller는 request parsing, auth dependency, response mapping만 담당한다. Permission, metadata policy, retrieval ranking, trace redaction business logic을 controller에 넣지 않는다.

Service/helper layer는 다음 orchestration을 담당한다.

- active actor/context 해석
- knowledge base `use` 권한 확인
- metadata filter normalization
- document metadata policy warn/block
- retrieval 호출
- trace/citation payload 생성

Gateway search-test와 Workflow Engine runtime retrieval은 같은 filter/policy semantics를 사용해야 한다. 이를 위해 shared helper를 우선한다.

후보 module:

- `apps/shared/services/rag_filters.py`
- `apps/shared/services/rag_policy.py`
- `apps/shared/services/rag_trace.py`
- `apps/shared/services/rag_retrieval_contracts.py`
- `apps/shared/services/rag_hierarchy.py`

## Metadata Architecture

`documents.meta_info`가 document metadata source of truth다.

표준 key 후보:

- `classification`
- `tags`
- `source_type`
- `source_hash`
- `document_version`
- `effective_from`
- `effective_to`
- `needs_reindex`
- `metadata_version`

`classification` 허용값은 `public`, `internal`, `confidential`, `pii`다. 누락 시 `internal`로 해석한다.

`document_chunks.metadata`는 retrieval/filter/citation 성능을 위한 denormalized cache다. `documents.meta_info`와 충돌하면 `documents.meta_info`를 우선한다. 문서 metadata가 바뀌면 chunk metadata를 동기화하거나 `documents.meta_info.needs_reindex=true`를 설정한다.

Metadata는 permission source of truth가 아니다. `owner_team_id`, `owner_user_id` 같은 metadata field를 권한 판정에 사용하지 않는다. Active organization membership은 KB organization scope와 resource permission subject의 전제 조건이며, 이 membership만으로 KB `read`/`use`를 허용하지 않는다. 실제 resource 허용은 organization manager override, `team_knowledge_permissions`, 목표 `user_knowledge_permissions`의 effective permission으로 판정한다.

## Permission Architecture

RAG execution path는 knowledge base `use` 권한을 요구한다.

적용 지점:

- RAG search-test API. Retrieval과 content preview를 수행하므로 KB `use` 권한을 요구한다.
- Workflow Engine LLM node retrieval 직전
- retrieval 실행과 연결되는 DB/API source 사용 경로

Scope prerequisite와 resource permission source:

- Scope prerequisite: active `organization_memberships` row와 KB의 `organization_id`가 요청의 active organization context 안에 있는지 확인한다.
- Resource permission source: organization manager override, `team_knowledge_permissions`, 목표 `user_knowledge_permissions`.

`user_knowledge_permissions`는 MVP 2 목표 table이며, MBA-78 1차 구현에는 포함하지 않는다. 장기 effective permission은 team permission과 additive user direct permission을 합산하되, table/API가 추가되기 전까지 user direct grant는 fail-closed로 둔다.

Document별 permission table은 만들지 않는다. Document access/policy는 KB permission과 `documents.meta_info` 기반 metadata policy를 조합한다.

MVP 2 목표 계약의 Knowledge/RAG permission gate는 KB의 `organization_id`와 요청의 active organization context를 비교해야 한다. 목표 계약은 Knowledge/RAG org-scoped API도 `X-Organization-Id` header를 사용하는 것이다. Org-scoped RAG에서 KB `organization_id`는 필수이며, legacy `organization_id=null` KB는 요청 header organization으로 보정하지 않고 backfill/reassignment 전까지 scope 밖 resource로 닫는다. 현재 Knowledge/RAG API의 primary organization fallback은 과도기 구현이며, MBA-78 1차 구현은 [knowledge-rag API 문서](../api/knowledge-rag.md)의 header 기반 400/404 계약으로 수렴하는 첫 범위다.

## LLM Credential Routing Boundary

MBA-43은 LLM credential routing과 runtime `use` 권한 이슈이며, Knowledge Base permission enforcement 이슈가 아니다. RAG/retrieval/ingestion 경로가 LLM provider 호출을 수행할 때는 가능한 경우 KB `organization_id`를 LLM credential routing scope로 전달할 수 있지만, 이것은 KB `read`/`use`/`write`/`manage` permission enforcement를 대체하지 않는다.

MBA-43 범위에서 제외되는 항목:

- RAG/retrieval/ingestion runtime block audit
- RAG/retrieval/ingestion successful usage logging in `llm_usage_logs`
- Gateway ingestion embedding credential routing through `apps/gateway/services/ingestion/service.py`
- LlamaParse/parser credential selection policy
- Connector credential policy
- Shared embedding service direct credential lookup refactor
- Workflow Engine RetrievalService rewrite model candidate selection refactor
- Workflow Engine RetrievalService constructor hardening for `organization_id=None`

Workflow Engine RetrievalService는 Workflow Engine LLMService를 공유하므로, LLM-backed retrieval 호출에는 explicit organization scope가 필요하다. 이는 shared LLM runtime hardening의 부수 효과이며, 새로운 RAG permission feature가 아니다.

## Document Metadata Policy

| classification | 기본 동작 |
| --- | --- |
| `public` | KB `use` 통과 시 허용 |
| `internal` | KB `use` 통과 시 허용 |
| `confidential` | KB `use` 통과 시 허용하되 audit/trace policy result 기록 |
| `pii` | external LLM prompt path에서는 `policy.block`, internal-only search preview에서는 `policy.warn` |

MBA-78 1차 구현은 위 policy action 이름과 RAG 권한/audit 경계를 먼저 고정한다. 실제 document metadata policy enforcement는 MBA-78 기준 후속 구현 범위이며, 현재 RAG search-test와 Workflow runtime은 KB `use` 권한 통과 후 retrieval을 수행한다. RAG Agent answer 3단계는 external LLM prompt path의 final evidence `pii` block만 이번 범위에 포함하고, search-test/runtime 전체 policy enforcement 확장은 별도 범위다.

Audit action:

- RBAC 거부: `permission.denied`
- policy 경고: `policy.warn`
- policy 차단: `policy.block`
- 성공한 retrieval 감사: `rag.retrieve`

Policy result는 `audit_logs.audit_metadata.policy_result`에 저장한다.

## Metadata Filter Contract

Free-form dict filter를 받지 않는다. API는 allowlist 기반 `MetadataFilter`를 받는다.

허용 예시:

```json
{
  "classification": ["internal", "confidential"],
  "tags": {
    "mode": "contains_all",
    "values": ["policy", "hr"]
  },
  "source_type": ["FILE", "API", "DB"],
  "effective_at": "2026-06-30T00:00:00+00:00"
}
```

MBA-78 1차 filter allowlist는 `classification`, `tags`, `source_type`, `effective_at`에 한정한다. `source_hash`, `document_version`, `metadata_version` 같은 key는 citation evidence 또는 후속 filter 확장 후보이지 현재 request filter key가 아니다.

`effective_from`/`effective_to` metadata는 canonical source인 `documents.meta_info`에 UTC ISO 문자열(`YYYY-MM-DDTHH:MM:SS+00:00`)로 정규화해 저장한다. 누락/빈 값은 열린 구간이고, 값이 있지만 이 형식을 따르지 않으면 filter match에서 제외한다. `document_chunks.metadata`에 복제된 값은 denormalized cache/citation evidence이며, MBA-78 1차 filter source로 보지 않는다. Ingestion/backfill은 `Z` 또는 다른 timezone offset을 그대로 남기지 않고 `+00:00` 문자열로 정규화해야 한다.

허용 operator:

- scalar list: `in`
- tags: `contains_any`, `contains_all`
- time window: `effective_from <= effective_at < effective_to`

금지:

- arbitrary JSONPath
- raw SQL fragment
- nested free-form filter
- secret/header/cookie/auth/prompt/completion/raw response 관련 key

Vector search와 keyword search는 동일 filter semantics를 적용해야 한다. Keyword raw SQL이 필요한 경우 bind parameter만 사용한다.

## Hierarchical RAG

MBA-78 1차 구현은 `document_chunks`에 nullable hierarchy field를 추가한다. 이 schema는 flat KB 호환 기반이며, full parent-child ingestion/ranking은 후속 구현 범위다.

- `parent_chunk_id`: nullable FK to `document_chunks.id`
- `chunk_level`: `parent | child | flat`
- `section_path`: JSON array nullable
- `heading`: text nullable

`parent_chunk_id`, `chunk_level`, `section_path`, `heading`은 hierarchy canonical column이다. JSON metadata에 같은 값을 중복 저장하지 않는다.

MBA-85 2단계 구현 기준:

- Hierarchical ingestion은 opt-in이다. 기본 `chunkingMode`/`chunking_mode`는 `flat`이다.
- `chunkingMode=hierarchical`은 `FILE`, `API` source에서만 지원한다. `DB` source는 `400 unsupported_chunking_mode_for_source`로 닫고, row/table 기반 hierarchy 설계는 후속 범위로 둔다.
- Workflow Engine DB sync와 shared `VectorStoreService`는 MBA-85에서 flat-only 경로로 유지한다. 이 경로는 non-flat parent/child payload나 DB source의 `chunking_mode=hierarchical`을 조용히 `flat`으로 변환하지 않고 실패 처리한다.
- Endpoint는 raw form/JSON 값을 service/helper로 전달하고 error mapping만 담당한다. `chunkingMode` 허용값, source type 조합, `selection_mode` 조합은 shared hierarchy helper 또는 ingestion service helper에서 검증한다.
- Parent chunk는 LLM summary가 아니다. 원문에서 만든 큰 routing chunk이며 coarse retrieval에만 사용한다.
- Final citation/evidence는 child chunk 또는 legacy/flat chunk 기준으로 반환한다.
- Parent routing chunk는 preview response나 final `ChunkPreview`의 독립 content 항목으로 노출하지 않는다.
- `selection_mode=range`와 hierarchical chunking 조합은 chunk 번호 의미가 충돌하므로 `400 invalid_chunking_selection`으로 닫는다.
- Hierarchical parent/child content 암호화 실패는 저장 준비 실패로 보고, 평문 fallback 없이 기존 chunk를 보존한다.
- 기존 `content_hash` 기반 처리 skip은 chunking 설정 변경을 고려해야 한다. `chunking_mode`, `hierarchy_version`, `chunk_size`, `chunk_overlap`, `segment_identifier`, preprocess flag, selection setting이 바뀌면 같은 원문이라도 재처리한다.
- `chunk_level`이 `parent`, `child`, `flat`, `NULL` 외 값이면 retrieval evidence 후보에서 제외한다. Unknown 값을 legacy flat으로 해석하지 않는다.
- Hierarchical mode는 parent와 child를 모두 embedding하므로 flat mode보다 처리 시간과 embedding 비용이 늘 수 있다. Progress는 parent+child 저장 대상과 embedding batch 기준으로 계산한다.

Retrieval flow:

1. KB `use` 권한 확인
2. metadata filter normalization
3. parent chunk 또는 section routing chunk coarse retrieval
4. parent 후보 cap 적용
5. parent 후보의 child chunk pool 생성
6. child pool에서 vector/keyword/hybrid retrieval
7. rerank
8. child chunk를 final evidence로 반환
9. redaction-safe trace 저장

Scoring:

- 최종 ranking은 child 또는 flat/legacy evidence chunk의 직접 vector/keyword/rerank score 기준이다.
- Parent route score는 candidate pool 제한, tie-break, 제한적인 boost에만 사용한다.
- Parent route score, child evidence score, flat fallback direct score를 정규화 없이 단순 합산하지 않는다.

Metadata filter:

- Canonical source는 `documents.meta_info`다.
- Parent routing 단계는 document-level filter를 기준으로 후보를 줄인다.
- Child-only chunk metadata 때문에 parent 후보가 먼저 탈락하지 않도록 한다.
- `document_chunks.metadata`는 child/flat evidence 단계에서 canonical value가 없을 때 safe fallback으로만 사용한다.

Fallback:

- parent chunk가 없는 KB는 flat retrieval
- hierarchy field가 일부 누락된 document는 해당 document만 flat fallback
- fallback 여부는 trace metadata에 기록
- MBA-78 1차 구현에서 `hierarchy_mode=auto`와 `flat`은 기존 flat retrieval을 사용한다. 명시적 `parent_child` 요청은 hierarchy data/index가 아직 없으면 `422 hierarchy_unavailable`로 닫는다.
- MBA-85 2단계에서 `hierarchy_mode=auto`는 hierarchy data가 있으면 parent-child retrieval을 사용하고 flat/legacy document를 fallback 후보로 합류시킨다.
- `hierarchy_mode=flat`은 parent routing chunk를 제외하고 child/flat evidence chunk를 flat하게 검색한다.
- `hierarchy_mode=parent_child`는 유효한 parent-child hierarchy data가 없으면 `422 hierarchy_unavailable`로 닫는다.

## Trace and Citation

신규 `rag_retrieval_traces` table은 만들지 않는다. Workflow runtime의 per-chunk retrieval evidence는 `trace_payloads.payload_kind='rag.retrieval'`의 redacted payload convention으로 저장하고, run/node trace metadata에는 redaction-safe summary allowlist만 저장한다.

권장 trace payload record convention:

```json
{
  "payload_kind": "rag.retrieval",
  "workflow_node_run_id": "...",
  "redacted_payload": {
    "knowledge_base_ids": ["..."],
    "workflow_run_id": "...",
    "node_id": "...",
    "retrieved_chunks": [
      {
        "document_id": "...",
        "chunk_id": "...",
        "parent_chunk_id": "...",
        "rank": 1,
        "score": 0.83,
        "token_count": 210,
        "metadata_summary": {
          "classification": "internal",
          "tags": ["policy"],
          "section_path": ["Handbook", "Leave"],
          "heading": "Leave Policy"
        }
      }
    ],
    "result_count": 1,
    "policy_result": "allow",
    "raw_content_returned": false
  }
}
```

Runtime node payload body에는 `workflow_node_run_id`를 중복 저장하지 않고, 저장 시 `trace_payloads.workflow_node_run_id` 컬럼으로 연결한다.

Run/node trace metadata allowlist는 `knowledge_base_id`, `retrieved_chunk_count`, `document_ids`, `citation_ids`, score summary, hierarchy fallback flag, `raw_content_returned` 같은 요약 field로 제한한다. `retrieved_chunks` 배열과 raw chunk content는 run/node metadata에 복사하지 않는다.

현재 `TraceMetadataSanitizer`의 RAG run/node metadata allowlist는 `knowledge_base_id`, `retrieved_chunk_count`, `document_ids`, `citation_ids`, `score_summary`, `hierarchy_fallback`, `raw_content_returned`, `latency_ms`, `retrieval_payload_id`, `retrieved_context_payload_id` 같은 요약 field만 허용한다. MBA-85에서는 run/node metadata allowlist를 넓히지 않는다. `hierarchy_mode`, parent/child candidate count 같은 diagnostic 값은 필요하면 `trace_payloads.payload_kind='rag.retrieval'`의 redacted payload 안에만 둔다. Legacy `retrieval_results` 입력은 저장하지 않고 위 summary field로 변환한다. Per-chunk evidence fixture와 run/node summary allowlist fixture는 분리해 검증한다.

`audit_logs.action='rag.retrieve'`는 성공한 retrieval 감사 event 이름이고, `trace_payloads.payload_kind='rag.retrieval'`는 trace payload 분류값이다. 두 값을 같은 계약으로 합치지 않는다.

## Agent Answer Trace/Usage Boundary

Standalone RAG Agent answer는 workflow run이 없을 수 있으므로 `workflow_runs`나 `trace_payloads`를 실행 anchor로 사용하지 않는다. Agent answer 실행의 기준 record는 RAG 도메인이 소유하는 `rag_answer_runs`이며, 이 record는 organization, actor, target KB, status, redaction-safe retrieval summary, citation summary, answer summary, nullable top-level answer hash, policy result, generation model/credential identifiers, denormalized usage snapshot, `correlation_id`를 저장한다.

Trace/usage 도메인과의 연결은 RAG 전용 FK가 아니라 opaque `correlation_id`로 한다. `trace_payloads.rag_answer_run_id`, `llm_usage_logs.rag_answer_run_id` 같은 column을 추가하지 않는다. Workflow runtime RAG evidence는 계속 `trace_payloads.payload_kind='rag.retrieval'`에 저장하지만, standalone Agent answer의 evidence는 `rag_answer_runs`의 summary/citation allowlist에 저장한다. Trace 도메인이 workflow 외 실행 payload를 지원해야 하면 generic trace subject 또는 correlation-only payload 설계를 별도 ADR로 다룬다. Client가 제공한 `correlation_id`는 추적 힌트일 뿐 권한/tenant 판정 입력이 아니며, 길이/문자셋/secret 금지 검증을 통과해야 한다.

LLM token/cost/latency의 원천은 `llm_usage_logs`다. Standalone answer와 usage를 강하게 묶어야 하면 usage 도메인이 generic `correlation_id` 또는 usage metadata extension을 제공해야 한다. RAG 도메인은 usage table에 answer 전용 FK를 추가하지 않는다. `rag_answer_runs.usage_summary`는 실행 시점의 denormalized snapshot이며 canonical usage 집계와 강한 FK 정합성을 보장하지 않는다. Durable/internal snapshot field는 token/cost/latency와 model/credential/provider 식별자 allowlist로 제한하고, credential 원문이나 raw prompt/completion/provider response는 포함하지 않는다. 일반 user-facing answer response에는 `credential_id`와 internal `model_id`를 기본 노출하지 않는다.

Standalone Agent answer의 durable `retrieval_summary`, `citation_summary`, `answer_summary`, `usage_summary`는 allowlist 기반 summary만 저장한다. Citation summary에는 document/chunk id, rank, score, heading, hierarchy path, safe metadata summary를 둘 수 있지만 raw chunk content나 user-facing `content_preview`는 저장하지 않는다. Answer hash는 nullable `rag_answer_runs.answer_hash` top-level column을 canonical 위치로 두고, answer summary에는 length, cited document count, citation ids, policy result, completion status, 선택적 redacted summary만 둔다.

Agent answer lifecycle audit은 `rag.answer.requested`, `rag.answer.completed`, `rag.answer.failed`, `rag.answer.cancelled`를 사용한다. `rag.answer.requested`는 schema validation, organization header validation, active organization scope 확인, KB scope visibility 확인, required credential/model visibility 확인을 모두 통과해 answer run을 생성할 때 남긴다. Retrieval 성공 감사 `rag.retrieve`, provider 호출 감사 `llm.call`, answer 상태 `rag_answer_runs.status`와 의미를 분리한다. Scope 안 resource가 확인된 뒤 policy 또는 permission preflight로 answer delta를 생성하지 못한 경우에만 `rag_answer_runs.status="blocked"`를 사용한다. PII/classification/metadata policy 차단은 `policy.block`, KB/credential/model permission preflight 차단은 `permission.denied` audit으로 표현하고 별도 `rag.answer.blocked` action은 만들지 않는다. Resource hiding 대상인 `resource.not_found`, scope 밖, organization mismatch에는 answer run과 lifecycle audit을 만들지 않는다. Invalid organization header와 validation 실패처럼 실행 전 검증에서 닫히는 오류도 answer run과 lifecycle audit 없이 반환한다.

Raw user question, raw final answer, raw retrieved chunk content, raw prompt/completion, credential 원문, API key, token, encrypted_config, provider raw response는 Agent answer summary, trace, audit, usage metadata에 기본 저장하지 않는다. `query_hash`와 `answer_hash`는 nullable이며, 저장하려면 HMAC-SHA256, server-side secret/pepper, `hash_version` 또는 동등 metadata convention을 먼저 확정하고 구현해야 한다. HMAC secret/pepper가 없으면 값을 저장하지 않으며 unsalted hash fallback은 허용하지 않는다. Answer history/replay가 필요하면 redacted answer snapshot과 retention/access policy를 별도 설계로 확정한다. `rag_answer_runs`를 도입하는 PR은 list/detail/delete/purge API를 포함하지 않더라도 90일 기본 보존, RAG domain scheduled worker purge, 실패 시 다음 run 재시도, `rag.answer.purge` aggregate audit을 함께 문서화해야 한다.

Boundary:

- search-test response는 KB `use` 권한을 통과한 user에게 chunk content preview를 반환할 수 있다.
- workflow trace/run detail 기본 응답은 raw chunk content 없이 citation metadata만 반환한다.
- raw content가 필요하면 기존 trace payload visibility/access policy를 따른다.
