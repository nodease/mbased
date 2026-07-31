# RAG Agent Answer 3단계 구현 계획

Status: Draft
Authority: Implementation Plan
Source of Truth: Yes
Verified Against: dev @ 860ece0dee7cab3925d27f30ea650baf0cb18b4e (PR #138 docs target, 2026-07-01 KST)
Related Docs: [MVP 2 Governance/RAG/Audit](../requirements/mvp-2-governance-rag-audit.md), [Knowledge/RAG architecture](../architecture/knowledge-rag.md), [Knowledge/RAG API](../api/knowledge-rag.md), [Tracing/Audit API](../api/tracing-audit.md), [Physical data model](../data-model/physical-data-model.md), [RAG answer trace/usage ADR](../decisions/ADR-202607010220-rag-answer-trace-usage-correlation-boundary.md)

## 목적

이 문서는 `local/rag-extension-phase-3/`의 비권위 구현 메모를 공식 구현 순서로 정리한다. RAG 확장 3단계의 목표는 MBA-78/MBA-85에서 구현한 metadata-aware 및 hierarchical retrieval을 사용자-facing Agent answer 경험으로 연결하는 것이다.

이번 구현은 사용자가 UI에서 단일 knowledge base를 직접 선택하고 질문하면, 서버가 권한과 policy를 검증한 뒤 metadata-aware/hierarchical RAG로 context를 검색하고 LLM 답변, citation, redaction-safe retrieval/citation/usage summary를 반환하며 audit과 trace/usage correlation 정보를 기록하는 흐름이다.

## 기준 문서

구현 중 계약 충돌이 발생하면 아래 문서를 우선한다.

| 영역 | 기준 |
| --- | --- |
| 범위와 완료 기준 | [MVP 2 Governance/RAG/Audit](../requirements/mvp-2-governance-rag-audit.md) |
| RAG service boundary | [Knowledge/RAG architecture](../architecture/knowledge-rag.md) |
| request/response/SSE/error | [Knowledge/RAG API](../api/knowledge-rag.md) |
| trace/usage 조회 경계 | [Tracing/Audit API](../api/tracing-audit.md), [Tracing/Audit architecture](../architecture/tracing-audit.md) |
| `rag_answer_runs` | [Physical data model](../data-model/physical-data-model.md) |
| lifecycle/audit action | [Audit action ADR](../decisions/ADR-202606290131-audit-action-naming-standard.md) |
| trace/usage FK 금지 | [RAG answer trace/usage ADR](../decisions/ADR-202607010220-rag-answer-trace-usage-correlation-boundary.md) |

## 포함 범위

- `POST /api/v1/rag/agent/answer`
- `POST /api/v1/rag/agent/answer/stream`
- 단일 `knowledge_base_id` 직접 선택
- active organization header와 KB organization scope 검증
- KB `use` 권한, organization manager override, 현재 구현된 team knowledge permission 기준
- LLM credential `use` 권한과 credential-model relation 검증
- metadata filter, classification filter, `hierarchy_mode`, `top_k` 적용
- `rag_answer_runs` 실행 anchor와 status transition
- `correlation_id` 검증과 cross-domain loose correlation
- SSE streaming event 순서와 terminal/error event
- RAG answer lifecycle audit, retrieval audit, policy/permission denial audit
- redaction-safe retrieval/citation/answer/usage summary 저장
- 사용자-facing answer UI와 citation 표시

## 제외 범위

- RAG preset request field와 `rag_agent_preset_id` table
- 범용 `agent_id`, 범용 agent framework, 장기 memory, arbitrary tool routing, multi-agent orchestration
- multi-KB Agent answer
- 대화 세션 저장과 answer history/replay
- `rag_answer_runs` list/detail/delete/purge API
- standalone answer raw content 저장/조회
- DB source hierarchical RAG
- RAG 품질 평가/비교 dashboard
- full/partial re-index UI
- `user_knowledge_permissions` 전체 API/UI
- advanced chunk tuning UI

RAG preset은 공식 data model/API field, 권한, 우선순위가 확정된 뒤 조건부 후속 작업 또는 별도 이슈로 분리한다. Preset이 공식화되기 전에는 `agent_id`나 임의 preset field를 request schema에 열지 않는다.

## 구현 원칙

- Controller는 request parsing과 dependency wiring만 담당한다. 권한, retrieval, policy, prompt 구성, answer run 상태 전이, audit/usage 기록은 service/facade가 소유한다.
- RAG answer service는 기존 retrieval/filter/permission helper를 재사용하되, external LLM prompt path에 필요한 credential/model 권한과 classification policy를 추가 검증한다.
- MBA-78에서 후속 범위로 남긴 document metadata policy enforcement 중 이번 구현은 Agent answer external LLM path의 final evidence `pii` block만 포함한다. Search-test/runtime 전체 policy enforcement 확장은 별도 범위다.
- active organization membership은 organization scope 전제 조건이며, KB 사용 권한 source가 아니다.
- `trace_payloads.rag_answer_run_id`, `llm_usage_logs.rag_answer_run_id`, standalone answer 전용 `rag_retrieval_traces`는 추가하지 않는다.
- Raw query, raw final answer, raw retrieved chunk content, raw prompt/completion, credential 원문, API key, token, encrypted_config, provider raw response는 durable summary, trace metadata, audit metadata, usage metadata에 기본 저장하지 않는다.
- Stream 시작 전 검증 가능한 오류는 HTTP status/reason code로 반환하고, stream 시작 후 오류는 terminal `error` event로 반환한다.

## 구현 단계

### A. 계약 고정

이미 반영된 계약:

- Agent answer API와 SSE event 계약
- `rag_answer_runs` data model, FK nullable/ondelete 정책, retention, purge audit convention
- `rag.answer.*`, `rag.answer.purge`, `rag.retrieve`, `policy.block`, `permission.denied` 의미 분리
- `correlation_id` 검증과 RAG 전용 trace/usage FK 금지
- user-facing/durable `usage_summary` whitelist 분리

구현 중 새 보안, retention, access control, data storage 결정을 만들면 ADR 또는 상위 source-of-truth 문서를 먼저 갱신한다.

### B. Schema와 migration

`rag_answer_runs`를 additive table로 추가한다.

필수 기준:

- `organization_id`는 tenant scope 기준으로 non-null
- `user_id`, `knowledge_base_id`, `generation_model_id`, `generation_credential_id`는 hard delete 시 `SET NULL` 가능한 nullable FK
- `actor_user_ref`, `knowledge_base_ref`, `generation_model_snapshot`, `generation_credential_ref`는 safe snapshot으로만 사용
- `(organization_id, correlation_id, created_at)` index
- `status` 값: `requested`, `running`, `completed`, `failed`, `cancelled`, `blocked`
- `retention_expires_at`은 생성 시점에 설정하고 기본 보존 기간은 90일
- 상태 전이는 `requested -> blocked`와 `requested -> running -> completed|failed|cancelled|blocked`를 모두 허용한다.
- `query_hash`, `answer_hash`, `hash_version`은 nullable로 두고, 값을 저장할 때는 HMAC-SHA256, server-side secret/pepper, `hash_version`을 함께 사용한다. HMAC secret/pepper가 없으면 hash 값을 저장하지 않으며 unsalted hash fallback은 금지한다.

### C. API schema와 endpoint

- `RAGAgentAnswerRequest`, `RAGAgentAnswerResponse`, SSE event payload schema를 추가한다.
- `generation_model_id`는 3단계 request에서 필수로 둔다.
- `credential_id`는 3단계 기본 request에서 필수로 둔다. Default credential 또는 preset 기반 자동 선택은 data model/API 계약을 별도 공식화한 뒤 후속 확장으로 다룬다.
- `correlation_id`는 255자 이하의 UUID/ULID 또는 `[A-Za-z0-9._:-]` 범위 문자열만 허용한다.
- `top_k`는 3단계 초기 목표 guardrail 기준 기본값 8, 최대 8이다.
- `/answer`와 `/answer/stream`은 같은 service facade를 사용해 policy와 status 전이가 갈라지지 않게 한다.

### D. Service facade와 권한 검증

권장 service 흐름:

1. request schema와 `X-Organization-Id`를 검증한다.
2. active organization scope를 확인한다.
3. KB existence, active 상태, organization match를 확인한다.
4. 필수 `generation_model_id`는 existence와 active 상태를 확인하고, 필수 `credential_id`는 existence, active 상태, organization match, scope visibility를 확인한다.
5. KB/credential/model 중 scope 밖, organization mismatch, 숨겨야 하는 not found는 answer run 생성 없이 `404 resource.not_found`로 닫는다.
6. visible resource와 required credential/model visibility가 확인되면 `rag_answer_runs` row를 만들고 `rag.answer.requested`를 기록한다.
7. KB `use` 권한, KB `embedding_model` readiness, generation credential/model `use` 권한, verified credential-model relation을 preflight로 검증한다. Request의 `credential_id`는 generation credential이며, retrieval query embedding은 KB의 `embedding_model`을 지원하는 same-organization valid credential과 verified relation 및 `use` 권한을 별도로 확인한다.
8. 같은 scope 안 resource에 대한 use 권한, KB embedding credential use 권한, verified relation preflight 실패는 HTTP `403`, `error.code="permission.denied"`, `status=blocked`, `permission.denied` audit으로 닫는다.
9. retrieval/generation 시작 전 `running`으로 전환한다.
10. retrieval을 수행하고 final evidence classification policy를 검증한다.
11. `pii` evidence가 있으면 LLM 호출 전 `policy.block` audit을 기록하고, HTTP `403`, `error.code="policy.blocked"`, `status=blocked`로 종료한다. 반환 예외에는 `audit_recorded=True` 또는 동등 marker를 설정해 Gateway 전역 401/403 handler의 `auth.permission_denied` 중복 기록을 막는다.
12. LLM answer를 생성하고 citation/summary/usage를 저장한다.
13. 완료, 실패, 취소에 맞춰 status와 lifecycle audit을 기록한다.

### E. Streaming

SSE event 순서는 [Knowledge/RAG API](../api/knowledge-rag.md)의 계약을 따른다.

- `retrieval.started`
- `retrieval.completed`
- `answer.delta`
- `usage`
- `summary`
- `answer.completed`
- `error`

`answer.completed`, `usage`, `summary`는 SSE event 이름이며 audit action이 아니다. 브라우저 기본 `EventSource`는 POST body를 보내기 어렵기 때문에 프론트엔드는 `fetch`와 `ReadableStream` 기반 parser 또는 Next.js proxy route를 사용한다.

### F. Frontend UI

기본 UI는 고급 튜닝 도구가 아니라 실제 질문/답변 흐름을 우선한다.

필수 UI 상태:

- active organization 선택/복구
- 사용 가능한 KB 직접 선택
- 질문 입력
- streaming answer 표시
- citation card와 redaction된 300자 이하 `content_preview`
- retrieval/usage summary
- empty retrieval
- `permission.denied`
- `resource.not_found`
- `policy.blocked` 응답
- `hierarchy_unavailable`
- streaming/network error

Preset 선택 UI는 RAG preset model/API가 공식화된 뒤에만 제공한다.

### G. Audit, trace, usage, retention

- 성공 retrieval은 `audit_logs.action='rag.retrieve'`
- answer lifecycle은 `rag.answer.requested/completed/failed/cancelled`
- policy 차단은 `policy.block`
- 권한 차단은 `permission.denied`
- retention purge aggregate는 `rag.answer.purge`
- workflow runtime evidence만 `trace_payloads.payload_kind='rag.retrieval'`
- standalone answer evidence는 `rag_answer_runs.retrieval_summary`와 `citation_summary`
- canonical token/cost source는 `llm_usage_logs`, standalone answer는 `usage_summary` snapshot

`rag.answer.purge` aggregate event는 `target_type='rag_answer_runs'`, `target_id=null`, safe metadata allowlist만 사용한다.

## 검증 계획

Backend unit/service:

- invalid `correlation_id` 400
- missing/invalid organization header에서 answer run 미생성
- missing `credential_id` 또는 `generation_model_id`는 schema validation 실패와 answer run 미생성
- KB 없음, inactive, scope 밖, organization mismatch에서 404와 answer run 미생성
- 같은 scope 안 KB `use` 부족은 HTTP 403, `error.code="permission.denied"`, `rag_answer_runs.status=blocked`, `permission.denied` audit을 함께 검증
- KB embedding credential readiness는 retrieval 시작 전 검증하고, embedding credential `use` 부족은 HTTP 403, `error.code="permission.denied"`, `reason_code="embedding_credential_use_denied"`로 닫음
- credential/model scope 밖, organization mismatch, 숨겨야 하는 not found는 404와 answer run 미생성
- 같은 scope 안 credential/model `use` 또는 verified relation 부족은 HTTP 403, `error.code="permission.denied"`, `rag_answer_runs.status=blocked`, `permission.denied` audit을 함께 검증
- `pii` evidence 포함 시 LLM 호출 전 HTTP 403, `error.code="policy.blocked"`, `policy.block` audit
- retrieval 성공 뒤 `pii` policy block이 발생하면 `rag.retrieve`와 `policy.block` audit이 모두 남고 LLM 호출은 발생하지 않음
- `policy.blocked` 응답은 Gateway 전역 401/403 handler에서 `auth.permission_denied` 또는 `permission.denied`로 중복/오분류 기록되지 않음
- `confidential` evidence 포함 시 policy result 기록
- `rag_answer_runs` status transition
- SSE delivery 중 cancel이 발생해도 이미 `completed/failed/blocked`인 terminal status를 `cancelled`로 덮어쓰지 않음
- non-stream/stream retrieval timeout mapping이 문서와 일치함
- 작은 `context_window` model에서는 context budget 8000 상한과 provider `max_tokens` 1000 상한을 모두 model-aware 값으로 낮춤
- nullable FK/ondelete 목표와 safe snapshot 저장
- `query_hash`/`answer_hash`는 HMAC secret/pepper가 있을 때만 저장하고, 없으면 `null`로 유지
- purge aggregate metadata allowlist

Backend API:

- `/answer`와 `/answer/stream`의 권한/policy 결과 일치
- 같은 scope 안 blocked 403 응답은 목표 error envelope의 `error.details`에 `answer_run_id`, `correlation_id`, `status="blocked"`, `reason_code`를 safe metadata로 반환
- permission block은 `error.code="permission.denied"`, policy block은 `error.code="policy.blocked"`를 반환
- SSE event 순서와 terminal event
- SSE event 이름과 `rag.answer.*` audit action 비혼동
- `citations[].content_preview`는 user-facing JSON/SSE response에만 redaction된 300자 이하로 허용하고 durable summary/audit/trace/usage metadata에는 미저장
- raw chunk content, raw prompt/completion, credential 원문 미저장

Frontend:

- KB 직접 선택
- streaming answer rendering
- citation card와 preview 제한
- permission/not found/`policy.blocked`/empty/error 상태 표시
- preset field 미공식 상태에서 preset UI와 `agent_id` 미노출

## 커밋 분리 권장

1. 공식 문서와 implementation plan 정렬
2. `rag_answer_runs` schema/migration
3. API schema와 endpoint skeleton
4. service facade, permission, policy, credential/model 검증
5. retrieval/generation/SSE 연결
6. audit/trace/usage/retention
7. frontend UI와 API client

RAG preset을 추가하는 경우 이번 KB 직접 선택 구현 이후 공식 data-model/API 문서 갱신, migration, API field 추가를 별도 커밋 또는 별도 PR로 분리한다.

## 중단 기준

다음 상황이 확인되면 구현을 중단하고 공식 문서 또는 ADR 보강을 먼저 진행한다.

- search-test와 Agent answer API의 permission/filter semantics가 달라져야 하는 경우
- raw chunk content를 trace/audit metadata에 저장해야만 UI가 구현되는 경우
- `trace_payloads` 또는 `llm_usage_logs`에 RAG 전용 FK가 필요하다는 요구가 생기는 경우
- workflow trace raw 접근 정책을 standalone answer에 그대로 적용해야 한다는 요구가 생기는 경우
- credential/model 권한 없이 generation model을 선택해야 한다는 요구가 생기는 경우
- multi-KB, DB source hierarchical, RAG preset, 대화 저장을 이번 범위에 포함해야 한다는 요구가 생기는 경우
